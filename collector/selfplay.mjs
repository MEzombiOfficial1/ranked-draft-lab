import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { buildModel, context, suggest, suggestBans, winProbability } from "../site/engine.js";

const args = Object.fromEntries(process.argv.slice(2).reduce((a, x, i, all) => (x.startsWith("--") ? [...a, [x.slice(2), all[i + 1]]] : a), []));
const dir = args.stats || "data/stats";
const budgetMs = Number(args.minutes || 4) * 60000;
const load = (f, optional) => (existsSync(join(dir, `${f}.json`)) ? JSON.parse(readFileSync(join(dir, `${f}.json`), "utf8")) : optional ? null : (() => { throw new Error(`${f}.json missing`); })());
const model = buildModel({ maps: load("maps"), brawlers: load("brawlers"), synergy: load("synergy"), matchups: load("matchups"),
  summary: load("summary"), builds: load("builds"), calibration: load("model", true), learned: load("learned", true) });
const pool = (model.maps.current_pool.length ? model.maps.current_pool : model.maps.maps.map((m) => [m.mode, m.map]))
  .map(([m, n]) => `${m}|${n}`).filter((k) => model.mapIndex.has(k));
const t0 = Date.now();
let seed = Date.now() % 2147483647;
const rng = () => ((seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648);

const STRATEGIES = [
  { name: "balanced (70% worst-case)", blend: 0.7, widths: [7, 6, 5, 4, 3] },
  { name: "pure minimax", blend: 1.0, widths: [7, 6, 5, 4, 3] },
  { name: "optimistic (40% worst-case)", blend: 0.4, widths: [7, 6, 5, 4, 3] },
  { name: "wide search", blend: 0.7, widths: [10, 8, 6, 4, 3] },
  { name: "deep-narrow", blend: 0.85, widths: [6, 6, 5, 5, 4] },
];

function withStrategy(s, fn) {
  const saved = model.search;
  model.search = { blend: s.blend, widths: s.widths };
  try { return fn(); } finally { model.search = saved; }
}

function draft(ctx, X, Y, xFirst) {
  const pickBans = (state) => {
    const pool8 = suggestBans(ctx, state, { limit: 8 }).map((b) => b.id);
    const out = [];
    while (out.length < 3 && pool8.length) out.push(pool8.splice(Math.floor(rng() * Math.min(pool8.length, 5)), 1)[0]);
    return out;
  };
  const bans = [...pickBans({ ours: [], theirs: [], bans: [] })];
  bans.push(...pickBans({ ours: [], theirs: [], bans }));
  const A = [], B = [];
  const order = xFirst ? ["A", "B", "B", "A", "A", "B"] : ["B", "A", "A", "B", "B", "A"];
  for (const side of order) {
    const [mine, other, strat, first] = side === "A" ? [A, B, X, xFirst] : [B, A, Y, !xFirst];
    const pick = withStrategy(strat, () => suggest(ctx, { ours: mine, theirs: other, bans }, { limit: 1, weFirst: first, depth: 3, candidates: 12 })[0]);
    if (!pick) break;
    mine.push(pick.id);
  }
  return winProbability(ctx, A, B);
}

const DECAY = 0.85;
const prev = load("selfplay", true) || {};
const score = STRATEGIES.map((st) => {
  const r = prev.raw?.[st.name];
  return r ? { sum: r.sum * DECAY, sq: r.sq * DECAY, games: r.games * DECAY } : { sum: 0, sq: 0, games: 0 };
});
let drafts = 0;
const tourBudget = budgetMs * 0.6;
outer: for (let round = 0; ; round++) {
  for (const mapKey of [...pool].sort(() => rng() - 0.5)) {
    for (const bracket of ["high", "all"]) {
      const ctx = context(model, mapKey, bracket);
      for (let i = 0; i < STRATEGIES.length; i++) for (let j = 0; j < STRATEGIES.length; j++) {
        if (i === j) continue;
        if (Date.now() - t0 > tourBudget) break outer;
        const xFirst = (round + i + j) % 2 === 0;
        const p = draft(ctx, STRATEGIES[i], STRATEGIES[j], xFirst);
        score[i].sum += p - 0.5; score[i].sq += (p - 0.5) ** 2; score[i].games++;
        score[j].sum += 0.5 - p; score[j].sq += (p - 0.5) ** 2; score[j].games++;
        drafts++;
      }
    }
  }
  if (round > 50) break;
}
const table = STRATEGIES.map((s, i) => {
  const n = score[i].games, mean = n ? score[i].sum / n : 0;
  const se = n > 1 ? Math.sqrt(Math.max(0, score[i].sq / n - mean * mean) / (n - 1)) : 1;
  return { name: s.name, blend: s.blend, widths: s.widths, games: Math.round(n), edge: +(100 * mean).toFixed(2), se: +(100 * se).toFixed(2) };
}).sort((a, b) => b.edge - a.edge);
const dflt = table.find((r) => r.name === STRATEGIES[0].name);
const top = table[0];
const significant = top !== dflt && top.edge - dflt.edge > 2 * Math.hypot(top.se, dflt.se);
const best = significant ? top : dflt;
console.log(`Self-play tournament: ${drafts} drafts on ${pool.length} maps`);
for (const r of table) console.log(`  ${r.name.padEnd(30)} edge ${r.edge >= 0 ? "+" : ""}${r.edge} ± ${r.se} win-% points over ${r.games} drafts`);
console.log(significant ? `  -> ${top.name} is significantly better: adopting it` : `  -> no strategy significantly beats the default: keeping ${dflt.name}`);

model.search = { blend: best.blend, widths: best.widths };
const deep = { blend: best.blend, widths: best.widths.map((w) => w + 3) };
const poolKeys = new Set(pool.flatMap((k) => [`${k}|high`, `${k}|all`]));
const book = Object.fromEntries(Object.entries(prev.book || {}).filter(([k]) => poolKeys.has(k)));
let positions = 0;
const queue = [...poolKeys].sort((a, b) => (book[a]?.ts || 0) - (book[b]?.ts || 0));
for (const key of queue) {
  {
    if (Date.now() - t0 > budgetMs) break;
    const bracket = key.endsWith("|high") ? "high" : "all";
    const mapKey = key.slice(0, key.lastIndexOf("|"));
    const ctx = context(model, mapKey, bracket);
    const entry = { first: [], replies: {}, ts: Date.now() };
    withStrategy(deep, () => {
      entry.first = suggest(ctx, { ours: [], theirs: [], bans: [] }, { limit: 12, weFirst: true, depth: 3, candidates: 24 })
        .filter((s) => s.projected != null).map((s) => [s.id, +s.projected.toFixed(4)]);
      positions++;
      const theirFirst = entry.first.slice(0, 6).map(([id]) => id);
      for (const x of theirFirst) {
        entry.replies[x] = suggest(ctx, { ours: [], theirs: [x], bans: [] }, { limit: 8, weFirst: false, depth: 3, candidates: 16 })
          .filter((s) => s.projected != null).map((s) => [s.id, +s.projected.toFixed(4)]);
        positions++;
      }
    });
    book[key] = entry;
  }
}
const raw = Object.fromEntries(STRATEGIES.map((st, i) => [st.name, score[i]]));
const out = { generated_at: new Date().toISOString(), seconds: Math.round((Date.now() - t0) / 1000), drafts, positions,
  total_drafts: (prev.total_drafts || prev.drafts || 0) + drafts, runs: (prev.runs || 0) + 1, raw,
  book_entries: Object.keys(book).length,
  search: { blend: best.blend, widths: best.widths, name: best.name }, significant, tournament: table, learned_model: Boolean(model.learned), book };
writeFileSync(join(dir, "selfplay.json"), JSON.stringify(out));
console.log(`Opening book: ${positions} positions refreshed this run, ${Object.keys(book).length} map/bracket entries in total · strategy: ${best.name} · `
  + `${out.total_drafts} self-play drafts all-time · ${out.seconds}s`);
