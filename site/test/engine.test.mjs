import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
import { buildModel, context, suggest, suggestBans, winProbability, tierList, counter, synergy, remainingOrder,
  evaluatePick, botPick, botBans, gradePick } from "../engine.js";

const load = (f) => JSON.parse(readFileSync(new URL(`../../data/stats/${f}.json`, import.meta.url)));
const model = buildModel({ maps: load("maps"), brawlers: load("brawlers"), synergy: load("synergy"),
  matchups: load("matchups"), summary: load("summary"), builds: load("builds") });
const [mode, map] = model.maps.current_pool[0];
const name = (id) => model.names[id];

assert.deepEqual(remainingOrder(true, 0, 0), ["A", "B", "B", "A", "A", "B"]);
assert.deepEqual(remainingOrder(true, 1, 2), ["A", "A", "B"]);
assert.deepEqual(remainingOrder(false, 0, 1), ["A", "A", "B", "B", "A"]);

for (const bracket of ["all", "low", "high"]) {
  const t0 = performance.now();
  const ctx = context(model, `${mode}|${map}`, bracket);
  const tCtx = performance.now() - t0;
  const top = suggest(ctx, { ours: [], theirs: [], bans: [] }, { weFirst: true });
  assert.ok(top.length >= 10);
  const [a, b, c] = top.map((s) => s.id);
  assert.ok(Math.abs(winProbability(ctx, [a], [b, c]) + winProbability(ctx, [b, c], [a]) - 1) < 1e-9);
  assert.ok(Math.abs(counter(ctx, a, b).res + counter(ctx, b, a).res) < 1e-9);
  assert.equal(synergy(ctx, a, b).res, synergy(ctx, b, a).res);
  const state = { ours: [a], theirs: [b, c], bans: [top[3].id] };
  const t1 = performance.now();
  const sug = suggest(ctx, state, { weFirst: true });
  const tSearch = performance.now() - t1;
  const searched = sug.filter((s) => s.projected != null);
  for (let i = 1; i < searched.length; i++) assert.ok(searched[i - 1].projected >= searched[i].projected);
  for (const s of sug) assert.ok(![a, b, c, top[3].id].includes(s.id) && s.win > 0 && s.win < 1);
  const v = evaluatePick(ctx, state, sug[0].id, { weFirst: true });
  assert.ok(Math.abs(v - sug[0].projected) < 1e-9, `${v} vs ${sug[0].projected}`);
  assert.equal(gradePick(v, v).grade, "Best");
  const allowed = new Set(sug.slice(3, 8).map((s) => s.id));
  assert.ok(suggest(ctx, state, { allowed }).every((s) => allowed.has(s.id)));
  for (const level of ["easy", "normal", "hard"]) {
    const p = botPick(ctx, state, level);
    assert.ok(p != null && ![a, b, c, top[3].id].includes(p));
  }
  const bans = botBans(ctx, { ours: [], theirs: [], bans: [] }, 3, "normal");
  assert.equal(new Set(bans).size, 3);
  const t2 = performance.now();
  const banS = suggestBans(ctx, { ours: [], theirs: [], bans: [] });
  const tBans = performance.now() - t2;
  console.log(`[${bracket}] ${ctx.battles} games | ctx ${tCtx.toFixed(0)}ms search ${tSearch.toFixed(0)}ms (${sug.searchNodes} nodes) bans ${tBans.toFixed(0)}ms`);
  console.log(`   first pick: ${top.slice(0, 5).map((s) => `${name(s.id)} ${(s.projected * 100).toFixed(1)}%`).join(" | ")}`);
  console.log(`   bans: ${banS.slice(0, 5).map((x) => `${name(x.id)} +${(x.gain * 100).toFixed(1)}`).join(", ")}`);
  assert.ok(tSearch < 1500, "search too slow");
  assert.ok(Object.values(tierList(ctx)).flat().length > 0);
}
console.log("All engine tests passed");
