export const PICK_ORDER = ["A", "B", "B", "A", "A", "B"];
export const BRACKETS = { all: "All ranks", low: "Below Legendary", high: "Legendary+" };

const K_MODE = 120;
const K_MAP = 50;
const K_BRACKET = 80;
const K_PAIR = 30;

export const sigmoid = (x) => 1 / (1 + Math.exp(-x));
export const logit = (p) => Math.log(p / (1 - p));
const pairKey = (a, b) => (a < b ? `${a}-${b}` : `${b}-${a}`);
const sub = (a, b) => a.map((x, i) => x - (b?.[i] || 0));
const ZERO = [0, 0, 0, 0];
const WIDTHS = { 1: [4, 3, 2, 2, 2], 2: [5, 4, 3, 3, 2], 3: [7, 6, 5, 4, 3] };

export function buildModel(data) {
  const { maps, brawlers, synergy, matchups, summary, builds, notes, calibration, learned, selfplay } = data;
  const modeStats = {};
  for (const [id, b] of Object.entries(brawlers.brawlers)) {
    for (const [mode, s] of Object.entries(b.modes)) (modeStats[mode] ||= {})[id] = { all: s, high: b.modes_high?.[mode] || ZERO };
  }
  const syn = {}, mat = {};
  for (const [mode, rows] of Object.entries(synergy.modes)) {
    const m = (syn[mode] = new Map());
    for (const [a, b, g, w, gh = 0, wh = 0] of rows) m.set(pairKey(a, b), [g, w, gh, wh]);
  }
  for (const [mode, rows] of Object.entries(matchups.modes)) {
    const m = (mat[mode] = new Map());
    for (const [a, b, g, wa, wb, gh = 0, wah = 0, wbh = 0] of rows) m.set(pairKey(a, b), [a, wa, wb, wah, wbh]);
  }
  const mapIndex = new Map(maps.maps.map((m) => [`${m.mode}|${m.map}`, m]));
  const names = summary.brawler_names || {};
  const ids = Object.keys(names).map(Number).sort((a, b) => names[a].localeCompare(names[b]));
  const w = calibration?.weights || {};
  const weights = { map: w.map ?? 1, syn: w.syn ?? 1, ctr: w.ctr ?? 1 };
  const search = { blend: selfplay?.search?.blend ?? 0.7, widths: selfplay?.search?.widths ?? WIDTHS[3] };
  return { maps, mapIndex, modeStats, syn, mat, names, ids, summary, builds, notes: notes || null, calibration: calibration || null, weights,
           learned: learned?.chosen ? learned : null, selfplay: selfplay || null, search };
}

function bracketRow(bracket, all = ZERO, high = ZERO) {
  if (bracket === "high") return high;
  if (bracket === "low") return sub(all, high);
  return all;
}

export function context(model, mapKey, bracket = "all") {
  if (bracket === true) bracket = "high";
  if (!bracket) bracket = "all";
  const mapObj = model.mapIndex.get(mapKey);
  if (!mapObj) throw new Error(`unknown map ${mapKey}`);
  const mode = mapObj.mode;
  const battles = bracket === "high" ? mapObj.battles_high : bracket === "low" ? mapObj.battles - mapObj.battles_high : mapObj.battles;
  const ctx = { model, mode, mapObj, bracket, battles, high: bracket === "high", ids: [], index: new Map() };

  const ms = model.modeStats[mode] || {};
  ctx.ids = model.ids.filter((id) => ms[id] || mapObj.brawlers[id]);
  ctx.ids.forEach((id, i) => ctx.index.set(id, i));
  const n = ctx.ids.length;
  ctx.pAll = new Float64Array(n);
  ctx.pModeB = new Float64Array(n);
  ctx.stats = new Array(n);
  ctx.eff = new Float64Array(n);
  ctx.pick = new Float64Array(n);
  ctx.prior = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    const id = ctx.ids[i];
    const mAll = ms[id]?.all || ZERO, mHigh = ms[id]?.high || ZERO;
    const pModeAll = (mAll[1] + K_MODE * 0.5) / (mAll[1] + mAll[2] + K_MODE);
    const mb = bracketRow(bracket, mAll, mHigh);
    const pModeB = bracket === "all" ? pModeAll : (mb[1] + K_BRACKET * pModeAll) / (mb[1] + mb[2] + K_BRACKET);
    const sAll = mapObj.brawlers[id] || ZERO, sHigh = mapObj.brawlers_high?.[id] || ZERO;
    const pMapAll = (sAll[1] + K_MAP * pModeAll) / (sAll[1] + sAll[2] + K_MAP);
    const sb = bracketRow(bracket, sAll, sHigh);
    let p = pMapAll;
    if (bracket !== "all") {
      const prior = sigmoid(logit(pMapAll) + logit(pModeB) - logit(pModeAll));
      p = (sb[1] + K_BRACKET * prior) / (sb[1] + sb[2] + K_BRACKET);
    }
    ctx.pAll[i] = pModeAll;
    ctx.pModeB[i] = pModeB;
    ctx.eff[i] = logit(p) * model.weights.map;
    ctx.pick[i] = battles ? sb[0] / battles : 0;
    ctx.prior[i] = 0.02 * Math.log(1e-3 + ctx.pick[i]);
    ctx.stats[i] = { games: sb[0], wins: sb[1], losses: sb[2], raw: sb[1] + sb[2] ? sb[1] / (sb[1] + sb[2]) : null,
                     p, eff: logit(p), pickRate: ctx.pick[i], gamesAll: sAll[0] };
  }
  ctx.syn = new Float64Array(n * n);
  ctx.ctr = new Float64Array(n * n);
  ctx.synGames = new Int32Array(n * n);
  ctx.ctrGames = new Int32Array(n * n);
  ctx.ctrRaw = new Float64Array(n * n).fill(NaN);
  const synM = model.syn[mode], matM = model.mat[mode];
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      const a = ctx.ids[i], b = ctx.ids[j];
      const key = pairKey(a, b);
      const s = synM?.get(key);
      if (s) {
        const [g, w, gh, wh] = s;
        const expAll = logit(ctx.pAll[i]) + logit(ctx.pAll[j]);
        const resAll = logit((w + K_PAIR * sigmoid(expAll)) / (g + K_PAIR)) - expAll;
        let res = resAll, games = g;
        if (bracket !== "all") {
          const gb = bracket === "high" ? gh : g - gh, wb = bracket === "high" ? wh : w - wh;
          const exp = logit(ctx.pModeB[i]) + logit(ctx.pModeB[j]);
          res = logit((wb + K_PAIR * sigmoid(exp + resAll)) / (gb + K_PAIR)) - exp;
          games = gb;
        }
        ctx.syn[i * n + j] = ctx.syn[j * n + i] = res * model.weights.syn;
        ctx.synGames[i * n + j] = ctx.synGames[j * n + i] = games;
      }
      const m = matM?.get(key);
      if (m) {
        const [first, wa, wb, wah, wbh] = m;
        const flip = first !== a;
        const wA = flip ? wb : wa, wB = flip ? wa : wb, wAh = flip ? wbh : wah, wBh = flip ? wah : wbh;
        const expAll = logit(ctx.pAll[i]) - logit(ctx.pAll[j]);
        const nAll = wA + wB;
        const resAll = logit((wA + K_PAIR * sigmoid(expAll)) / (nAll + K_PAIR)) - expAll;
        let res = resAll, games = nAll, raw = nAll ? wA / nAll : NaN;
        if (bracket !== "all") {
          const x = bracket === "high" ? wAh : wA - wAh, y = bracket === "high" ? wBh : wB - wBh;
          const exp = logit(ctx.pModeB[i]) - logit(ctx.pModeB[j]);
          res = logit((x + K_PAIR * sigmoid(exp + resAll)) / (x + y + K_PAIR)) - exp;
          games = x + y;
          raw = games ? x / games : NaN;
        }
        ctx.ctr[i * n + j] = res * model.weights.ctr;
        ctx.ctr[j * n + i] = -res * model.weights.ctr;
        ctx.ctrGames[i * n + j] = ctx.ctrGames[j * n + i] = games;
        ctx.ctrRaw[i * n + j] = raw;
        ctx.ctrRaw[j * n + i] = 1 - raw;
      }
    }
  }
  if (model.learned) applyLearned(ctx, model.learned, mapKey);
  return ctx;
}

function applyLearned(ctx, L, mapKey) {
  const lm = L.modes?.[ctx.mode];
  if (!lm) return;
  const lp = L.maps?.[mapKey] || {};
  const n = ctx.ids.length;
  const a = L.blend ?? 1;
  for (let i = 0; i < n; i++) {
    const id = ctx.ids[i];
    ctx.eff[i] = a * ((lm.u?.[id] || 0) + (lp[id] || 0) + (ctx.bracket === "high" ? lm.h?.[id] || 0 : 0)) + (1 - a) * ctx.eff[i];
  }
  for (let k = 0; k < n * n; k++) { ctx.syn[k] *= 1 - a; ctx.ctr[k] *= 1 - a; }
  for (const [x, y, v] of lm.syn || []) {
    const i = ctx.index.get(x), j = ctx.index.get(y);
    if (i != null && j != null) { ctx.syn[i * n + j] += a * v; ctx.syn[j * n + i] += a * v; }
  }
  for (const [x, y, v] of lm.ctr || []) {
    const i = ctx.index.get(x), j = ctx.index.get(y);
    if (i != null && j != null) { ctx.ctr[i * n + j] += a * v; ctx.ctr[j * n + i] -= a * v; }
  }
  const comp = L.comp?.[ctx.mode];
  if (comp && L.classes) {
    const names = Object.keys(comp);
    ctx.classNames = names;
    ctx.compW = new Float64Array(names.length * 3);
    names.forEach((c, ci) => comp[c].forEach((v, k) => (ctx.compW[ci * 3 + k] = a * v)));
    ctx.cls = new Int8Array(n).fill(-1);
    for (let i = 0; i < n; i++) { const c = names.indexOf(L.classes[ctx.ids[i]]); ctx.cls[i] = c; }
  }
  ctx.learned = true;
}

function compTerm(ctx, T) {
  let x = 0;
  for (let i = 0; i < T.length; i++) {
    const c = ctx.cls[T[i]];
    if (c < 0) continue;
    let k = 0;
    for (let j = 0; j < i; j++) if (ctx.cls[T[j]] === c) k++;
    x += ctx.compW[c * 3 + k];
  }
  return x;
}
function compDelta(ctx, c, mine) {
  const cc = ctx.cls[c];
  if (cc < 0) return 0;
  let k = 0;
  for (let j = 0; j < mine.length; j++) if (ctx.cls[mine[j]] === cc) k++;
  return k < 3 ? ctx.compW[cc * 3 + k] : 0;
}

const idx = (ctx, id) => ctx.index.get(id);

export function mapStats(ctx, id) {
  const i = idx(ctx, id);
  if (i == null) return { games: 0, wins: 0, losses: 0, raw: null, p: 0.5, eff: 0, pickRate: 0, gamesAll: 0 };
  return ctx.stats[i];
}
export function synergy(ctx, a, b) {
  const i = idx(ctx, a), j = idx(ctx, b);
  if (i == null || j == null) return { res: 0, games: 0 };
  const n = ctx.ids.length;
  return { res: ctx.syn[i * n + j], games: ctx.synGames[i * n + j] };
}
export function counter(ctx, c, e) {
  const i = idx(ctx, c), j = idx(ctx, e);
  if (i == null || j == null) return { res: 0, games: 0, raw: null };
  const n = ctx.ids.length;
  const raw = ctx.ctrRaw[i * n + j];
  return { res: ctx.ctr[i * n + j], games: ctx.ctrGames[i * n + j], raw: Number.isNaN(raw) ? null : raw };
}

function logitIdx(ctx, A, B) {
  const n = ctx.ids.length, eff = ctx.eff, syn = ctx.syn, ctr = ctx.ctr;
  let x = 0;
  for (let i = 0; i < A.length; i++) {
    x += eff[A[i]];
    for (let j = i + 1; j < A.length; j++) x += syn[A[i] * n + A[j]];
    for (let j = 0; j < B.length; j++) x += ctr[A[i] * n + B[j]];
  }
  for (let i = 0; i < B.length; i++) {
    x -= eff[B[i]];
    for (let j = i + 1; j < B.length; j++) x -= syn[B[i] * n + B[j]];
  }
  if (ctx.compW) x += compTerm(ctx, A) - compTerm(ctx, B);
  return x;
}
const toIdx = (ctx, list) => list.map((id) => idx(ctx, id)).filter((i) => i != null);

export function teamLogit(ctx, ours, theirs) { return logitIdx(ctx, toIdx(ctx, ours), toIdx(ctx, theirs)); }
export const winProbability = (ctx, ours, theirs) => sigmoid(teamLogit(ctx, ours, theirs));

function gain(ctx, c, mine, other) {
  const n = ctx.ids.length;
  let x = ctx.eff[c];
  for (const a of mine) x += ctx.syn[c * n + a];
  for (const e of other) x += ctx.ctr[c * n + e];
  if (ctx.compW) x += compDelta(ctx, c, mine);
  return x;
}

function topCandidates(ctx, mine, other, used, k) {
  const n = ctx.ids.length, eff = ctx.eff, syn = ctx.syn, ctr = ctx.ctr, prior = ctx.prior;
  const bestV = new Float64Array(k).fill(-Infinity), bestC = new Int32Array(k).fill(-1);
  for (let c = 0; c < n; c++) {
    if (used[c]) continue;
    let x = eff[c] + prior[c];
    const row = c * n;
    for (let i = 0; i < mine.length; i++) x += syn[row + mine[i]];
    for (let i = 0; i < other.length; i++) x += ctr[row + other[i]];
    if (ctx.compW) x += compDelta(ctx, c, mine);
    if (x <= bestV[k - 1]) continue;
    let j = k - 1;
    while (j > 0 && bestV[j - 1] < x) { bestV[j] = bestV[j - 1]; bestC[j] = bestC[j - 1]; j--; }
    bestV[j] = x; bestC[j] = c;
  }
  const out = [];
  for (let j = 0; j < k; j++) if (bestC[j] >= 0) out.push(bestC[j]);
  return out;
}

function search(ctx, A, B, order, used, depth, widths, stats) {
  stats.nodes++;
  if (!order.length || depth >= widths.length) return sigmoid(logitIdx(ctx, A, B));
  const side = order[0], rest = order.slice(1);
  const mine = side === "A" ? A : B, other = side === "A" ? B : A;
  const cands = topCandidates(ctx, mine, other, used, widths[depth]);
  const vals = [];
  for (const c of cands) {
    used[c] = 1;
    mine.push(c);
    vals.push(search(ctx, A, B, rest, used, depth + 1, widths, stats));
    mine.pop();
    used[c] = 0;
  }
  if (!vals.length) return sigmoid(logitIdx(ctx, A, B));
  if (side === "A") return Math.max(...vals);
  vals.sort((a, b) => a - b);
  const top = vals.slice(0, 3);
  const blend = ctx.model.search.blend;
  return blend * vals[0] + (1 - blend) * top.reduce((s, v) => s + v, 0) / top.length;
}

export function remainingOrder(weFirst, oursCount, theirsCount) {
  const seq = PICK_ORDER.map((t) => (weFirst ? t : t === "A" ? "B" : "A"));
  let a = oursCount, b = theirsCount;
  return seq.filter((t) => (t === "A" ? a-- <= 0 : b-- <= 0));
}

function setup(ctx, state) {
  const used = new Uint8Array(ctx.ids.length);
  for (const x of [...state.ours, ...state.theirs, ...state.bans]) { const i = idx(ctx, x); if (i != null) used[i] = 1; }
  return { used, A: toIdx(ctx, state.ours), B: toIdx(ctx, state.theirs) };
}
function orderNow(weFirst, A, B) {
  const order = remainingOrder(weFirst, A.length, B.length);
  const k = order.indexOf("A");
  return k <= 0 ? order : ["A", ...order.slice(0, k), ...order.slice(k + 1)];
}

export function suggest(ctx, state, { limit = 20, allowed = null, weFirst = true, depth = 3, candidates = 20 } = {}) {
  const n = ctx.ids.length;
  const { used, A, B } = setup(ctx, state);
  const base = logitIdx(ctx, A, B);
  const rest = orderNow(weFirst, A, B).slice(1);
  const remainingEnemy = rest.filter((t) => t === "B").length;

  const scored = [];
  for (let c = 0; c < n; c++) {
    if (used[c] || (allowed && !allowed.has(ctx.ids[c]))) continue;
    scored.push({ c, g: gain(ctx, c, A, B) });
  }
  scored.sort((x, y) => y.g - x.g);

  const widths = depth >= 3 ? ctx.model.search.widths : WIDTHS[Math.max(1, depth)];
  const stats = { nodes: 0 };
  const out = [];
  scored.forEach(({ c, g }, rank) => {
    const now = sigmoid(base + g);
    let projected = null, threats = [];
    if (!rest.length) projected = now;
    else if (depth > 0 && rank < candidates) {
      used[c] = 1; A.push(c);
      projected = search(ctx, A, B, rest, used, 0, widths, stats);
      if (remainingEnemy) threats = topCandidates(ctx, B, A, used, 3).filter((e) => ctx.ctr[c * n + e] < -0.04).map((e) => ctx.ids[e]);
      A.pop(); used[c] = 0;
    }
    const reasons = [];
    for (const a of A) { const v = ctx.syn[c * n + a]; if (Math.abs(v) > 0.04 && ctx.synGames[c * n + a] >= 10) reasons.push({ kind: v > 0 ? "syn+" : "syn-", with: ctx.ids[a], v }); }
    for (const e of B) { const v = ctx.ctr[c * n + e]; if (Math.abs(v) > 0.04 && ctx.ctrGames[c * n + e] >= 10) reasons.push({ kind: v > 0 ? "ctr+" : "ctr-", with: ctx.ids[e], v }); }
    reasons.sort((x, y) => Math.abs(y.v) - Math.abs(x.v));
    const s = ctx.stats[c];
    out.push({ id: ctx.ids[c], win: now, projected, threats, reasons, map: s,
      confidence: s.games >= 150 ? "high" : s.games >= 40 ? "medium" : "low" });
  });
  out.sort((a, b) => (b.projected != null) - (a.projected != null) || (b.projected ?? 0) - (a.projected ?? 0) || b.win - a.win);
  const res = out.slice(0, limit);
  res.searchNodes = stats.nodes;
  return res;
}

export function evaluatePick(ctx, state, id, { weFirst = true, depth = 3 } = {}) {
  const c = idx(ctx, id);
  if (c == null) return 0;
  const { used, A, B } = setup(ctx, state);
  const rest = orderNow(weFirst, A, B).slice(1);
  used[c] = 1; A.push(c);
  if (!rest.length) return sigmoid(logitIdx(ctx, A, B));
  return search(ctx, A, B, rest, used, 0, depth >= 3 ? ctx.model.search.widths : WIDTHS[Math.max(1, depth)], { nodes: 0 });
}

export function suggestBans(ctx, state, { limit = 10, weFirst = true } = {}) {
  const n = ctx.ids.length;
  const { used } = setup(ctx, state);
  const order = remainingOrder(weFirst, 0, 0);
  const widths = [6, 5, 3, 3, 2, 2];
  const baseline = search(ctx, [], [], order, used, 0, widths, { nodes: 0 });
  const threats = [];
  for (let c = 0; c < n; c++) {
    const strength = Math.max(ctx.eff[c], logit(ctx.stats[c].p));
    if (used[c] || ctx.stats[c].games < 15 || strength <= 0) continue;
    threats.push({ c, t: strength * Math.sqrt(Math.max(ctx.pick[c], 0.005)) });
  }
  threats.sort((a, b) => b.t - a.t);
  return threats.slice(0, 16).map(({ c, t }) => {
    used[c] = 1;
    const v = search(ctx, [], [], order, used, 0, widths, { nodes: 0 });
    used[c] = 0;
    return { id: ctx.ids[c], map: ctx.stats[c], gain: v - baseline, value: v, threat: t };
  }).filter((x) => x.gain > -0.005)
    .sort((a, b) => b.gain - a.gain || b.threat - a.threat).slice(0, limit);
}

export function botPick(ctx, state, level, { weFirst = true, rng = Math.random } = {}) {
  if (level === "hard") return suggest(ctx, state, { limit: 1, weFirst, depth: 3, candidates: 12 })[0]?.id;
  const { used, A, B } = setup(ctx, state);
  const k = level === "easy" ? 18 : 5, temp = level === "easy" ? 0.35 : 0.08;
  const cands = topCandidates(ctx, A, B, used, k);
  const weights = cands.map((c) => Math.exp(gain(ctx, c, A, B) / temp) * (0.2 + ctx.pick[c]));
  let r = rng() * weights.reduce((s, w) => s + w, 0);
  for (let i = 0; i < cands.length; i++) { r -= weights[i]; if (r <= 0) return ctx.ids[cands[i]]; }
  return ctx.ids[cands[0]];
}

export function botBans(ctx, state, count, level, rng = Math.random) {
  const pool = suggestBans(ctx, state, { limit: level === "hard" ? 5 : 10 });
  const out = [];
  while (out.length < count && pool.length) {
    const i = level === "hard" ? 0 : Math.floor(rng() * Math.min(pool.length, level === "easy" ? 10 : 5));
    out.push(pool.splice(i, 1)[0].id);
  }
  return out;
}

export function gradePick(bestValue, pickValue) {
  const loss = Math.max(0, (bestValue - pickValue) * 100);
  const grade = loss <= 0.3 ? "Best" : loss <= 1 ? "Excellent" : loss <= 2.5 ? "Good" : loss <= 5 ? "Inaccuracy" : loss <= 9 ? "Mistake" : "Blunder";
  return { loss, grade, accuracy: Math.round(100 * Math.exp(-loss / 5)) };
}

export function tierList(ctx, minGames = 20) {
  const rows = ctx.ids.map((id) => ({ id, ...mapStats(ctx, id) })).filter((r) => r.games >= minGames);
  rows.sort((a, b) => b.p - a.p);
  const tiers = { S: [], A: [], B: [], C: [], D: [] };
  rows.forEach((r, i) => {
    const q = i / Math.max(1, rows.length);
    const t = r.p >= 0.56 || q < 0.08 ? "S" : q < 0.25 ? "A" : q < 0.55 ? "B" : q < 0.8 ? "C" : "D";
    tiers[t].push(r);
  });
  return tiers;
}
