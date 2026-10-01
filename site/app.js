import { PAGES, FAN_DISCLAIMER } from "./legal.js";
import { buildModel, context as makeContext, suggest, suggestBans, winProbability, tierList, mapStats, counter, synergy, PICK_ORDER,
  BRACKETS } from "./engine.js";
import { renderPractice } from "./practice.js";

const IMG = "img";
const MODE_NAMES = { gemGrab: "Gem Grab", brawlBall: "Brawl Ball", heist: "Heist", bounty: "Bounty", hotZone: "Hot Zone",
  knockout: "Knockout", siege: "Siege", wipeout: "Wipeout", basketBrawl: "Basket Brawl", volleyBrawl: "Volley Brawl" };
const MODE_COLORS = { gemGrab: "#b46cff", brawlBall: "#6c8cff", heist: "#e66cff", bounty: "#32d0ff", hotZone: "#ff5b5b", knockout: "#ffa447" };
const app = document.getElementById("app");
let model;
const ctxCache = new Map();
function context(m, mapKey, bracket = "all") {
  const key = `${mapKey}|${bracket}`;
  if (!ctxCache.has(key)) ctxCache.set(key, makeContext(m, mapKey, bracket));
  return ctxCache.get(key);
}
const bracketSeg = (val, set) => h("div", { class: "seg", title: "Which ranked games the statistics come from" },
  Object.entries(BRACKETS).map(([v, label]) => h("button", { class: v === val ? "on" : "", onclick: () => set(v) }, label)));

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : String(kid));
  return el;
}
const pct = (x, d = 1) => (x == null ? "–" : `${(x * 100).toFixed(d)}%`);
const title = (s) => (s || "").toLowerCase().replace(/(^|[\s-])\S/g, (m) => m.toUpperCase());
const bname = (id) => title(model.names[id] || `#${id}`);
const modeName = (m) => MODE_NAMES[m] || title(m?.replace(/([A-Z])/g, " $1"));
const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { } },
};

const ROLE_ICON = { Tank: "🛡️", "Damage Dealer": "💥", Marksman: "🎯", Assassin: "🗡️", Support: "💚", Controller: "🌀", Artillery: "💣" };
const roleOf = (id) => model?.info?.[id]?.class || null;
const STAT_LABELS = { health: "Health", attack: "Attack damage", attackbullets: "Projectiles per attack", attackrange: "Attack range",
  reload: "Reload", movementspeed: "Movement speed", attackspread: "Attack spread", attacksupercharge: "Super charge per attack",
  super: "Super damage", superrange: "Super range", superbullets: "Super projectiles", supersupercharge: "Super charge (Super)" };
const statOf = (id, key) => model?.info?.[id]?.stats?.[key] || null;
const firstNum = (v) => (v ? (String(v).match(/[\d.]+/) || [null])[0] : null);
function statLine(id) {
  const hp = firstNum(statOf(id, "health")), atk = firstNum(statOf(id, "attack")), n = firstNum(statOf(id, "attackbullets")),
        rng = firstNum(statOf(id, "attackrange"));
  return [hp && `❤ ${hp}`, atk && `⚔ ${atk}${n && n !== "1" ? `×${n}` : ""}`, rng && `↔ ${rng}`].filter(Boolean).join(" · ") || null;
}
const roleTag = (id) => { const r = roleOf(id); return r ? h("span", { class: "role", title: r }, `${ROLE_ICON[r] || "•"} ${r}`) : null; };

function portrait(id, size = 48) {
  const wrap = h("span", { class: "pt", style: { width: `${size}px`, height: `${size}px` }, title: bname(id) });
  const img = h("img", { src: `${IMG}/brawler/${id}.png`, alt: bname(id), loading: "lazy" });
  img.onerror = () => { img.remove(); wrap.append(h("span", { class: "ini" }, bname(id).slice(0, 2))); };
  wrap.append(img);
  return wrap;
}
const MODE_ICON = { gemGrab: "💎", brawlBall: "⚽", heist: "🔒", bounty: "⭐", hotZone: "🔥", knockout: "💀" };
function mapPlaceholder(mapObj, cls) {
  return h("div", { class: `${cls} mapph`, style: { background: MODE_COLORS[mapObj.mode] || "var(--card2)" }, "aria-hidden": "true" },
    MODE_ICON[mapObj.mode] || "🗺️");
}
function mapImage(mapObj, cls = "mapimg") {
  if (!mapObj.event_id) return mapPlaceholder(mapObj, cls);
  const img = h("img", { class: cls, alt: "", loading: "lazy", src: `${IMG}/map/${mapObj.event_id}.png` });
  img.onerror = () => img.replaceWith(mapPlaceholder(mapObj, cls));
  return img;
}
function mapOptions(select, current) {
  const pool = new Set(model.maps.current_pool.map(([m, n]) => `${m}|${n}`));
  const groups = {};
  for (const m of model.maps.maps) (groups[pool.has(`${m.mode}|${m.map}`) ? `In rotation · ${modeName(m.mode)}` : `Other · ${modeName(m.mode)}`] ||= []).push(m);
  for (const label of Object.keys(groups).sort((a, b) => (a.startsWith("In") === b.startsWith("In") ? a.localeCompare(b) : a.startsWith("In") ? -1 : 1))) {
    const og = h("optgroup", { label });
    for (const m of groups[label]) og.append(h("option", { value: `${m.mode}|${m.map}`, selected: `${m.mode}|${m.map}` === current }, `${m.map} (${m.battles})`));
    select.append(og);
  }
}
function defaultMap() {
  const [mode, map] = model.maps.current_pool[0] || [model.maps.maps[0].mode, model.maps.maps[0].map];
  return `${mode}|${map}`;
}
const noteFor = (mapKey) => model.notes?.maps?.[mapKey] || null;

let profile = store.get("profile", null);
let mine = store.get("mine", { on: false, minPower: 11 });
function myAllowed() {
  if (!mine.on || !profile) return null;
  return new Set(profile.brawlers.filter((b) => b.power >= mine.minPower).map((b) => b.id));
}
const myBrawler = (id) => profile?.brawlers.find((b) => b.id === id) || null;
async function loadProfile(tag) {
  const clean = tag.trim().toUpperCase().replace(/^#/, "");
  if (!clean) return "Enter your player tag (in game: tap your profile, it starts with #).";
  try {
    const res = await fetch(`api/player/${encodeURIComponent(clean)}`);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) return data.error || (res.status === 404 ? "Player lookup runs on the deployed site only." : `Lookup failed (${res.status})`);
    profile = { ...data, fetchedAt: Date.now() };
    mine.on = true;
    store.set("profile", profile);
    store.set("mine", mine);
    return null;
  } catch (e) {
    return `Lookup failed: ${e.message}`;
  }
}
function accountBar() {
  const msg = h("span", { class: "small muted" });
  const input = h("input", { type: "search", placeholder: "Your player tag, e.g. #2PP…", value: profile?.tag || store.get("tagInput", ""),
    style: { minWidth: "170px", flex: "1" }, oninput: (e) => store.set("tagInput", e.target.value) });
  const go = async () => { msg.textContent = "Loading your brawlers…"; const err = await loadProfile(input.value); if (err) msg.textContent = err; else renderDraft(); };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  const count = profile ? profile.brawlers.filter((b) => b.power >= mine.minPower).length : 0;
  const threshold = h("select", { "aria-label": "Minimum power level", onchange: (e) => { mine.minPower = Number(e.target.value); store.set("mine", mine); renderDraft(); } },
    [[11, "Power 11"], [10, "Power 10+"], [9, "Power 9+"], [1, "Any unlocked"]].map(([v, l]) => h("option", { value: v, selected: mine.minPower === v }, l)));
  return h("div", { class: "card account" },
    h("span", { style: { fontWeight: 700 } }, "👤 My brawlers"),
    input, h("button", { class: "btn", onclick: go }, profile ? "Refresh" : "Load"),
    profile ? h("span", { class: "pill" }, `${profile.name} · ${count}/${profile.brawlers.length} at ${mine.minPower > 1 ? `power ${mine.minPower}${mine.minPower < 11 ? "+" : ""}` : "any power"}`) : null,
    profile ? threshold : null,
    profile ? h("div", { class: "seg" }, [[false, "Off"], [true, "On"]].map(([v, l]) =>
      h("button", { class: mine.on === v ? "on" : "", onclick: () => { mine.on = v; store.set("mine", mine); renderDraft(); } }, l))) : null,
    msg);
}

const SEQ_BANS = [["A", "ban", 0], ["A", "ban", 1], ["A", "ban", 2], ["B", "ban", 0], ["B", "ban", 1], ["B", "ban", 2]];
function pickSeq(weFirst) {
  const n = { A: 0, B: 0 };
  return PICK_ORDER.map((t) => { const team = weFirst ? t : (t === "A" ? "B" : "A"); return [team, "pick", n[team]++]; });
}
function freshDraft(mapKey) {
  return { mapKey, bracket: store.get("bracket", "high"), weFirst: true, A: { ban: [null, null, null], pick: [null, null, null] },
           B: { ban: [null, null, null], pick: [null, null, null] }, active: ["A", "ban", 0] };
}
let draft;

function draftSeq() { return [...SEQ_BANS, ...pickSeq(draft.weFirst)]; }
function advance() {
  const seq = draftSeq();
  const cur = seq.findIndex(([t, k, i]) => t === draft.active[0] && k === draft.active[1] && i === draft.active[2]);
  for (let j = 1; j <= seq.length; j++) {
    const [t, k, i] = seq[(cur + j) % seq.length];
    if (draft[t][k][i] == null) { draft.active = [t, k, i]; return; }
  }
}
function taken() { return new Set([...draft.A.ban, ...draft.A.pick, ...draft.B.ban, ...draft.B.pick].filter((x) => x != null)); }
function assign(id) {
  if (taken().has(id)) return;
  const [t, k, i] = draft.active;
  draft[t][k][i] = id;
  advance();
  renderDraft();
}
function clearSlot(t, k, i) { draft[t][k][i] = null; draft.active = [t, k, i]; renderDraft(); }

function renderDraft() {
  store.set("draft", draft);
  let ctx;
  if (!BRACKETS[draft.bracket]) draft.bracket = draft.high ? "high" : store.get("bracket", "high");
  try { ctx = context(model, draft.mapKey, draft.bracket); } catch { draft = freshDraft(defaultMap()); ctx = context(model, draft.mapKey, draft.bracket); }
  const mapObj = ctx.mapObj;
  const A = draft.A.pick.filter((x) => x != null), B = draft.B.pick.filter((x) => x != null);
  const bans = [...draft.A.ban, ...draft.B.ban].filter((x) => x != null);
  const seqPick = pickSeq(draft.weFirst);
  const orderOf = (t, i) => seqPick.findIndex(([tt, , ii]) => tt === t && ii === i) + 1;

  const sel = h("select", { "aria-label": "Map", onchange: (e) => { draft.mapKey = e.target.value; renderDraft(); } });
  mapOptions(sel, draft.mapKey);
  const seg = (opts, val, set) => h("div", { class: "seg" }, opts.map(([v, label]) => h("button", { class: v === val ? "on" : "", onclick: () => { set(v); renderDraft(); } }, label)));
  const top = h("div", { class: "draft-top" }, sel,
    bracketSeg(draft.bracket, (v) => { draft.bracket = v; store.set("bracket", v); renderDraft(); }),
    seg([[true, "We pick first"], [false, "They pick first"]], draft.weFirst, (v) => { draft.weFirst = v; }),
    h("button", { class: "btn", onclick: () => { const k = draft.mapKey, br = draft.bracket, wf = draft.weFirst; draft = freshDraft(k); draft.bracket = br; draft.weFirst = wf; renderDraft(); } }, "Reset draft"));

  const note = noteFor(draft.mapKey);
  const banner = h("div", { class: "card mapbanner" }, mapImage(mapObj),
    h("div", {}, h("div", { class: "title display" }, mapObj.map),
      h("div", { class: "row" }, h("span", { class: "pill", style: { color: MODE_COLORS[mapObj.mode] } }, modeName(mapObj.mode)),
        h("span", { class: "pill" }, `${ctx.battles.toLocaleString()} ranked games · ${BRACKETS[ctx.bracket]}`),
        ctx.bracket !== "all" && ctx.battles < 300 ? h("span", { class: "pill" }, "Few games in this bracket: blended with all-rank data") : null),
      note ? h("div", { class: "note" }, "🤖 ", note) : null));

  const slot = (t, k, i) => {
    const id = draft[t][k][i];
    const active = draft.active[0] === t && draft.active[1] === k && draft.active[2] === i;
    const el = h("button", { class: `slot ${k}${id != null ? " filled" : ""}${active ? " active" : ""}`,
      onclick: () => (id != null && active ? clearSlot(t, k, i) : ((draft.active = [t, k, i]), renderDraft())),
      oncontextmenu: (e) => { e.preventDefault(); clearSlot(t, k, i); }, title: id != null ? "Click again (or right-click) to clear" : "Select slot" });
    if (k === "pick") el.append(h("span", { class: "order" }, `#${orderOf(t, i)}`));
    if (id != null) {
      el.append(portrait(id, k === "ban" ? 50 : 200));
      if (k === "pick") el.append(h("span", { class: "nm" }, bname(id)));
      el.append(h("span", { class: "x" }, "✕"));
    } else el.append(k === "ban" ? "ban" : "pick");
    if (k === "pick" && id != null) { el.querySelector(".pt").style.width = "100%"; el.querySelector(".pt").style.height = "100%"; }
    return el;
  };
  const teamBox = (t, label) => h("div", { class: `team ${t}` },
    h("h3", {}, label, h("span", { class: "small muted" }, t === "A" ? (draft.weFirst ? "1st pick" : "2nd pick") : (draft.weFirst ? "2nd pick" : "1st pick"))),
    h("div", { class: "label" }, "Bans"), h("div", { class: "slots" }, [0, 1, 2].map((i) => slot(t, "ban", i))),
    h("div", { class: "label" }, "Picks"), h("div", { class: "slots" }, [0, 1, 2].map((i) => slot(t, "pick", i))),
    compositionLine(ctx, draft[t].pick.filter((x) => x != null)));
  const p = A.length || B.length ? winProbability(ctx, A, B) : 0.5;
  const [at, ak] = draft.active;
  const complete = A.length === 3 && B.length === 3;
  const phaseText = complete ? "Draft complete" : `${at === "A" ? "Your" : "Enemy"} ${ak}`;
  const mid = h("div", { class: "mid card" },
    h("div", { class: "phase" }, phaseText),
    h("div", { class: "winbox" }, h("div", { class: "winnum", style: { color: p >= 0.5 ? "var(--blue)" : "var(--red)" } }, pct(p)),
      h("div", { class: "winbar" }, h("div", { style: { width: pct(p, 2) } })), h("div", { class: "small muted" }, "Your predicted win chance")),
    h("button", { class: "btn primary", onclick: () => aiCoach(ctx, A, B, bans) }, "🤖 AI coach"));
  const board = h("div", { class: "board" }, teamBox("A", "Your team"), mid, teamBox("B", "Enemy team"));

  const ours = at === "A";
  const state = { ours: ours ? A : B, theirs: ours ? B : A, bans };
  const sideFirst = ours ? draft.weFirst : !draft.weFirst;
  let head, list;
  if (complete) {
    head = h("h3", {}, "Final draft breakdown");
    list = breakdown(ctx, A, B);
  } else if (ak === "ban") {
    head = h("h3", {}, ours ? "Best bans for you" : "What the enemy will probably ban",
      h("div", { class: "small muted" }, "Simulates the full draft with and without each ban: how much it improves your side's best achievable draft"));
    list = h("div", { class: "sugg" }, suggestBans(ctx, state, { weFirst: sideFirst }).map((s, i) => suggestionCard(ctx, s, i, "ban")));
  } else {
    const allowed = ours ? myAllowed() : null;
    const sug = applyBook(ctx, suggest(ctx, state, { limit: 40, allowed, weFirst: sideFirst }), state, sideFirst).slice(0, 14);
    head = h("h3", {}, ours ? "Best picks for you" : "Enemy's strongest options",
      h("div", { class: "small muted" }, ours ? "Ranked by projected win chance after the rest of the draft, assuming the enemy answers well (lookahead search over thousands of continuations)"
        : "Predicted from their side with the same lookahead: expect one of these"),
      allowed ? h("div", { class: "small", style: { color: "var(--accent)" } }, `Only your brawlers at ${mine.minPower > 1 ? `power ${mine.minPower}${mine.minPower < 11 ? "+" : ""}` : "any power"} (${allowed.size})`) : null);
    list = h("div", { class: "sugg" }, sug.map((s, i) => suggestionCard(ctx, s, i, "pick")));
  }
  const aiOut = h("div", { class: "aibox muted", id: "aibox" });
  const left = h("div", { class: "card" }, head, list, aiOut);
  const right = h("div", { class: "card" }, brawlerGrid(ctx));

  app.replaceChildren(top, accountBar(), banner, board, h("div", { class: "layout" }, left, right));
}

function applyBook(ctx, sug, state, sideFirst) {
  const book = model.selfplay?.book?.[`${draft.mapKey}|${ctx.bracket === "high" ? "high" : "all"}`];
  if (!book || state.bans.length > 6) return sug;
  let line = null;
  if (!state.ours.length && !state.theirs.length && sideFirst) line = book.first;
  else if (!state.ours.length && state.theirs.length === 1 && !sideFirst) line = book.replies?.[state.theirs[0]];
  if (!line) return sug;
  const val = new Map(line);
  const taken = new Set([...state.ours, ...state.theirs, ...state.bans]);
  const allowed = myAllowed();
  for (const s of sug) if (val.has(s.id)) { s.projected = val.get(s.id); s.book = true; }
  for (const [id, v] of line) {
    if (taken.has(id) || (allowed && !allowed.has(id)) || sug.some((s) => s.id === id)) continue;
    const ms = mapStats(ctx, id);
    sug.push({ id, projected: v, book: true, win: winProbability(ctx, [...state.ours, id], state.theirs), map: ms, reasons: [], threats: [],
               confidence: ms.games >= 150 ? "high" : ms.games >= 40 ? "medium" : "low" });
  }
  return sug.sort((a, b) => (b.projected ?? -1) - (a.projected ?? -1));
}

function compositionLine(ctx, team) {
  if (!team.length || !model.info) return null;
  const roles = team.map(roleOf).filter(Boolean);
  if (!roles.length) return null;
  const counts = {};
  roles.forEach((r) => (counts[r] = (counts[r] || 0) + 1));
  let note = null;
  if (ctx.compW && ctx.classNames) {
    let x = 0;
    for (const [r, c] of Object.entries(counts)) {
      const ci = ctx.classNames.indexOf(r);
      if (ci >= 0) for (let k = 0; k < c; k++) x += ctx.compW[ci * 3 + k];
    }
    note = h("span", { class: x >= 0 ? "good" : "bad" }, ` composition ${x >= 0 ? "+" : ""}${(x * 25).toFixed(1)}%`);
  }
  return h("div", { class: "small muted", style: { marginTop: "8px" } },
    Object.entries(counts).map(([r, c]) => `${ROLE_ICON[r] || "•"} ${c > 1 ? `${c}× ` : ""}${r}`).join(" · "), note);
}

function reasonChips(s) {
  const out = [];
  for (const r of s.reasons.slice(0, 4)) {
    const txt = { "syn+": "Great with", "syn-": "Awkward with", "ctr+": "Beats", "ctr-": "Weak to" }[r.kind];
    out.push(h("span", { class: `chip ${r.v > 0 ? "pos" : "neg"}` }, `${txt} ${bname(r.with)} ${r.v > 0 ? "+" : ""}${(r.v * 25).toFixed(1)}%`));
  }
  if (s.threats?.length) out.push(h("span", { class: "chip neg" }, `Counter risk: ${s.threats.map(bname).join(", ")}`));
  const mb = profile && myBrawler(s.id);
  if (profile) out.push(h("span", { class: `chip ${mb && mb.power >= 11 ? "pos" : "neg"}` }, mb ? `You: power ${mb.power}` : "You don't own it"));
  return out;
}
function suggestionCard(ctx, s, i, kind) {
  const ms = s.map;
  const stats = `Map WR ${pct(ms.p)} · pick ${pct(ms.pickRate)} · ${ms.games} games`;
  return h("div", { class: "sg", onclick: () => assign(s.id), title: "Click to put in the active slot" },
    h("div", { class: "row", style: { gap: "6px", flexWrap: "nowrap" } }, portrait(s.id, 48)),
    h("div", {}, h("div", { class: "name" }, h("span", { class: "rankno" }, i + 1), bname(s.id), " ",
      kind === "pick" ? h("span", { class: `conf ${s.confidence}` }, s.confidence) : null,
      s.book ? h("span", { class: "book", title: "Value from the self-play opening book (searched much deeper)" }, "📘 book") : null, " ", roleTag(s.id)),
      h("div", { class: "stats" }, stats, statLine(s.id) ? h("span", { class: "statline" }, ` · ${statLine(s.id)}`) : null),
      kind === "pick" ? h("div", { class: "chips" }, reasonChips(s)) : null),
    h("div", { class: "score" }, kind === "pick"
      ? [h("b", {}, pct(s.projected ?? s.win)), h("span", { class: "small muted" }, s.projected != null ? `projected · now ${pct(s.win, 0)}` : "win chance now")]
      : [h("b", { class: s.gain > 0.001 ? "good" : "" }, s.gain > 0.001 ? `+${(s.gain * 100).toFixed(1)}` : pct(ms.p, 0)),
         h("span", { class: "small muted" }, s.gain > 0.001 ? "draft points" : "map WR")]));
}
function breakdown(ctx, A, B) {
  const rows = [];
  const add = (label, v) => Math.abs(v) >= 0.002 && rows.push(h("tr", {}, h("td", {}, label), h("td", { class: v >= 0 ? "good" : "bad" }, `${v >= 0 ? "+" : ""}${(v * 25).toFixed(1)}%`)));
  const wm = model.weights.map;
  for (const a of A) add(`${bname(a)} map strength`, mapStats(ctx, a).eff * wm);
  for (const b of B) add(`Enemy ${bname(b)} map strength`, -mapStats(ctx, b).eff * wm);
  for (let i = 0; i < 3; i++) for (let j = i + 1; j < 3; j++) { add(`${bname(A[i])} + ${bname(A[j])} synergy`, synergy(ctx, A[i], A[j]).res); add(`Enemy ${bname(B[i])} + ${bname(B[j])} synergy`, -synergy(ctx, B[i], B[j]).res); }
  for (const a of A) for (const b of B) add(`${bname(a)} vs ${bname(b)}`, counter(ctx, a, b).res);
  return h("div", {}, h("p", { class: "muted small" }, "Approximate effect of each factor on your win chance"), h("table", {}, h("tbody", {}, rows)));
}
function brawlerGrid(ctx) {
  const used = taken();
  const allowedSet = myAllowed();
  const sortMode = store.get("gridSort", "strength");
  const search = h("input", { type: "search", placeholder: "Search brawler… (Enter = pick first match)", value: store.get("gridSearch", "") });
  const grid = h("div", { class: "bgrid" });
  const fill = () => {
    const q = search.value.trim().toLowerCase();
    store.set("gridSearch", search.value);
    let ids = model.ids.filter((id) => bname(id).toLowerCase().includes(q));
    if (sortMode === "strength") ids = ids.sort((a, b) => mapStats(ctx, b).p - mapStats(ctx, a).p);
    grid.replaceChildren(...ids.map((id) => {
      const ms = mapStats(ctx, id);
      const col = ms.p >= 0.53 ? "var(--good)" : ms.p <= 0.47 ? "var(--bad)" : "var(--text)";
      const notMine = allowedSet && !allowedSet.has(id);
      return h("button", { class: `bt${used.has(id) ? " used" : ""}${notMine ? " notmine" : ""}`, title: `${bname(id)}: map WR ${pct(ms.p)} (${ms.games} games)`,
        onclick: () => assign(id) }, portrait(id, 200), h("span", { class: "wr", style: { color: col } }, ms.games ? pct(ms.p, 0) : "–"));
    }));
    grid.querySelectorAll(".pt").forEach((p) => { p.style.width = "100%"; p.style.height = "100%"; });
    return ids;
  };
  search.addEventListener("input", fill);
  search.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { const first = fill().find((id) => !used.has(id)); if (first != null) { search.value = ""; store.set("gridSearch", ""); assign(first); } }
  });
  const sortSeg = h("div", { class: "seg" }, [["strength", "Map strength"], ["name", "A–Z"]].map(([v, l]) =>
    h("button", { class: v === sortMode ? "on" : "", onclick: () => { store.set("gridSort", v); renderDraft(); } }, l)));
  fill();
  setTimeout(() => { if (window.matchMedia("(pointer: fine)").matches) search.focus(); }, 0);
  return h("div", {}, h("div", { class: "gridtools" }, search, sortSeg), grid);
}

async function aiCoach(ctx, A, B, bans) {
  const box = document.getElementById("aibox");
  box.textContent = "Thinking…";
  const [at, ak] = draft.active;
  const ours = at === "A";
  const state = { ours: ours ? A : B, theirs: ours ? B : A, bans };
  const top = ak === "pick" ? suggest(ctx, state, { limit: 8, allowed: ours ? myAllowed() : null, weFirst: ours ? draft.weFirst : !draft.weFirst }) : [];
  const payload = {
    map: ctx.mapObj.map, mode: modeName(ctx.mode), games: ctx.battles, bracket: BRACKETS[ctx.bracket],
    phase: `${ours ? "our" : "enemy"} ${ak}`, weFirst: draft.weFirst,
    ourPicks: A.map(bname), enemyPicks: B.map(bname), bans: bans.map(bname),
    brawlerInfo: Object.fromEntries([...A, ...B].map((x) => [bname(x), [roleOf(x), statLine(x)].filter(Boolean).join(", ")])),
    winChance: +(winProbability(ctx, A, B) * 100).toFixed(1),
    suggestions: top.map((s) => ({ brawler: bname(s.id), projectedWin: +((s.projected ?? s.win) * 100).toFixed(1), winNow: +(s.win * 100).toFixed(1), mapWR: +(s.map.p * 100).toFixed(1),
      games: s.map.games, reasons: s.reasons.slice(0, 3).map((r) => `${r.kind} ${bname(r.with)}`), counterRisk: s.threats.map(bname),
      role: roleOf(s.id), stats: statLine(s.id) })),
    mapMeta: tierList(ctx).S.concat(tierList(ctx).A).slice(0, 10).map((r) => `${bname(r.id)} ${(r.p * 100).toFixed(1)}%`),
    note: noteFor(draft.mapKey),
    myBrawlersOnly: myAllowed() ? `our suggestions are limited to brawlers I own at power >= ${mine.minPower}` : null,
  };
  try {
    const res = await fetch("api/ai", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload) });
    if (!res.ok) throw new Error(res.status === 404 ? "AI coach runs on the deployed site only." : `AI unavailable (${res.status})`);
    const data = await res.json();
    box.textContent = data.text || "No answer.";
    box.classList.remove("muted");
  } catch (e) {
    box.textContent = e.message;
  }
}

function renderMeta(params) {
  const mapKey = params.get("map");
  if (!mapKey) {
    const pool = new Set(model.maps.current_pool.map(([m, n]) => `${m}|${n}`));
    const maps = model.maps.maps.slice().sort((a, b) => (pool.has(`${b.mode}|${b.map}`) - pool.has(`${a.mode}|${a.map}`)) || a.mode.localeCompare(b.mode) || b.battles - a.battles);
    app.replaceChildren(h("h2", {}, "Ranked meta by map"),
      h("p", { class: "muted" }, "Maps in the current ranked rotation come first. Click a map for its tier list."),
      h("div", { class: "maps" }, maps.map((m) => {
        const ctx = context(model, `${m.mode}|${m.map}`);
        const best = tierList(ctx).S.slice(0, 4);
        return h("a", { class: "card mapcard", href: `#/meta?map=${encodeURIComponent(`${m.mode}|${m.map}`)}`, style: { textDecoration: "none" } },
          mapImage(m, "mi"), h("div", {}, h("div", { style: { fontWeight: 700 } }, m.map),
            h("div", { class: "small muted" }, `${modeName(m.mode)} · ${m.battles} games${pool.has(`${m.mode}|${m.map}`) ? " · in rotation" : ""}`),
            h("div", { class: "row", style: { gap: "3px", marginTop: "4px" } }, best.map((r) => portrait(r.id, 26)))));
      })));
    return;
  }
  const bracket = BRACKETS[params.get("bracket")] ? params.get("bracket") : store.get("bracket", "high");
  const ctx = context(model, mapKey, bracket);
  const tiers = tierList(ctx);
  const note = noteFor(mapKey);
  const rows = model.ids.map((id) => ({ id, ...mapStats(ctx, id) })).filter((r) => r.games > 0).sort((a, b) => b.p - a.p);
  app.replaceChildren(
    h("div", { class: "row", style: { marginBottom: "12px" } }, h("a", { class: "btn", href: "#/meta" }, "← All maps"),
      bracketSeg(bracket, (v) => { store.set("bracket", v); location.hash = `#/meta?map=${encodeURIComponent(mapKey)}&bracket=${v}`; }),
      h("button", { class: "btn primary", onclick: () => { draft = freshDraft(mapKey); location.hash = "#/draft"; } }, "Draft on this map")),
    h("div", { class: "card mapbanner" }, mapImage(ctx.mapObj), h("div", {}, h("div", { class: "title display" }, ctx.mapObj.map),
      h("div", { class: "muted" }, `${modeName(ctx.mode)} · ${ctx.battles} ranked games · ${BRACKETS[ctx.bracket]}`),
      note ? h("div", { class: "note" }, "🤖 ", note) : null)),
    h("div", { class: "two" },
      h("div", { class: "tiers" }, Object.entries(tiers).map(([t, list]) => h("div", { class: `tier ${t}` }, h("div", { class: "t" }, t),
        h("div", { class: "list" }, list.length ? list.map((r) => h("div", { class: "tcell" }, portrait(r.id, 56), h("div", { class: "v" }, pct(r.p)))) : h("span", { class: "muted small" }, "–"))))),
      h("div", { class: "card" }, h("h3", {}, "All brawlers on this map"),
        h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Brawler"), h("th", {}, "Win rate"), h("th", {}, "Raw"), h("th", {}, "Pick rate"), h("th", {}, "Games"))),
          h("tbody", {}, rows.map((r) => h("tr", {}, h("td", {}, h("span", { class: "row", style: { gap: "8px" } }, portrait(r.id, 26), bname(r.id))),
            h("td", {}, pct(r.p)), h("td", { class: "muted" }, pct(r.raw)), h("td", {}, pct(r.pickRate)), h("td", {}, r.games))))))));
}

function renderBrawlers(params) {
  const id = Number(params.get("id"));
  if (!id) {
    const overall = model.ids.map((b) => {
      const s = model.summaryBrawlers?.[b]?.all || [0, 0, 0, 0];
      return { id: b, g: s[0], wr: s[1] + s[2] ? s[1] / (s[1] + s[2]) : null };
    });
    app.replaceChildren(h("h2", {}, "Brawlers"), h("p", { class: "muted" }, "Overall ranked win rate (last 28 days). Click for maps, partners, counters and builds."),
      h("div", { class: "bgrid", style: { maxHeight: "none", gridTemplateColumns: "repeat(auto-fill, minmax(92px, 1fr))" } },
        overall.map((r) => h("a", { class: "bt", href: `#/brawlers?id=${r.id}`, style: { aspectRatio: "auto", padding: "6px", textDecoration: "none", textAlign: "center" } },
          portrait(r.id, 64), h("div", { class: "small", style: { fontWeight: 700 } }, bname(r.id)), h("div", { class: "small muted" }, `${pct(r.wr)} · ${r.g}`)))));
    return;
  }
  const mapsFor = model.maps.maps.map((m) => { const ctx = context(model, `${m.mode}|${m.map}`); return { m, ctx, s: mapStats(ctx, id) }; })
    .filter((x) => x.s.games >= 10).sort((a, b) => b.s.p - a.s.p);
  const modes = Object.keys(model.modeStats).filter((mo) => model.modeStats[mo][id]);
  const mode = params.get("mode") || modes[0];
  const sample = model.maps.maps.find((m) => m.mode === mode);
  const pairs = [];
  if (sample) {
    const ctx = context(model, `${sample.mode}|${sample.map}`);
    for (const o of model.ids) if (o !== id) {
      const s = synergy(ctx, id, o), c = counter(ctx, id, o);
      pairs.push({ o, syn: s, ctr: c });
    }
  }
  const top = (arr, key, dir, n = 6) => arr.filter((x) => x[key].games >= 10).sort((a, b) => dir * (b[key].res - a[key].res)).slice(0, n);
  const pairList = (arr, key, fmt) => h("div", { class: "row" }, arr.length ? arr.map((x) => h("div", { class: "tcell" }, portrait(x.o, 48), h("div", { class: "small" }, bname(x.o)),
    h("div", { class: `small ${x[key].res > 0 ? "good" : "bad"}` }, fmt(x[key])))) : h("span", { class: "muted small" }, "Not enough games yet"));
  const b = model.builds?.brawlers?.[id];
  const mb = profile && myBrawler(id);
  const youOwn = (kind, item) => {
    const key = { gadget: "gadgets", star_power: "starPowers", gear: "gears", hypercharge: "hyperCharges" }[kind];
    return Boolean(mb?.[key]?.includes(item));
  };
  const itemNames = model.builds?.item_names || {};
  const buildTable = (kind, label) => {
    const items = Object.entries(b?.[kind] || {});
    if (!items.length) return null;
    return h("div", {}, h("div", { class: "label" }, label), h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Item"), h("th", {}, "Owned by"), h("th", {}, "Owner WR"), h("th", {}, "Surely equipped"), h("th", {}, "WR when equipped"), profile ? h("th", {}, "You") : null)),
      h("tbody", {}, items.sort((x, y) => y[1][1][0] - x[1][1][0] || y[1][0][0] - x[1][0][0]).map(([item, [owned, eq]]) => h("tr", {},
        h("td", { title: model.info?.[id]?.abilities?.[item] || "" }, title(itemNames[item] || item),
          model.info?.[id]?.abilities?.[item] ? h("div", { class: "small muted" }, model.info[id].abilities[item]) : null), h("td", {}, b.games[0] ? pct(owned[0] / b.games[0], 0) : "–"),
        h("td", {}, owned[1] + owned[2] ? pct(owned[1] / (owned[1] + owned[2])) : "–"), h("td", {}, eq[0]),
        h("td", {}, eq[1] + eq[2] ? pct(eq[1] / (eq[1] + eq[2])) : "–"),
        profile ? h("td", {}, youOwn(kind, Number(item)) ? "✓" : "–") : null)))));
  };
  const s = model.summaryBrawlers?.[id]?.all || [0, 0, 0, 0];
  app.replaceChildren(
    h("div", { class: "row", style: { marginBottom: "12px" } }, h("a", { class: "btn", href: "#/brawlers" }, "← All brawlers")),
    h("div", { class: "card mapbanner" }, portrait(id, 92), h("div", {}, h("div", { class: "title display" }, bname(id)),
      model.info?.[id] ? h("div", { class: "small", style: { margin: "2px 0 4px" } }, roleTag(id), model.info[id].rarity ? h("span", { class: "muted" }, ` · ${model.info[id].rarity}`) : null,
        model.info[id].description ? h("div", { class: "muted", style: { marginTop: "4px", maxWidth: "720px" } }, model.info[id].description) : null) : null,
      statsTable(id),
      h("div", { class: "muted" }, `${pct(s[1] + s[2] ? s[1] / (s[1] + s[2]) : null)} win rate · ${s[0]} ranked games`
        + (profile ? (mb ? ` · you: power ${mb.power}` : " · you don't own it") : "")),
      h("div", { class: "row", style: { marginTop: "6px" } }, modes.map((mo) => { const ms = model.modeStats[mo][id];
        return h("span", { class: "pill" }, `${modeName(mo)} ${pct(ms[1] / Math.max(1, ms[1] + ms[2]))} (${ms[0]})`); })))),
    h("div", { class: "two" },
      h("div", { class: "card" }, h("h3", {}, "Best maps"), h("table", {}, h("tbody", {}, mapsFor.slice(0, 12).map((x) => h("tr", {},
        h("td", {}, h("a", { href: `#/meta?map=${encodeURIComponent(`${x.m.mode}|${x.m.map}`)}` }, x.m.map)), h("td", { class: "muted" }, modeName(x.m.mode)),
        h("td", {}, pct(x.s.p)), h("td", { class: "muted" }, `${x.s.games} games`)))))),
      h("div", { class: "card" }, h("h3", {}, "Partners & counters"),
        h("div", { class: "seg", style: { marginBottom: "8px", flexWrap: "wrap" } }, modes.map((mo) => h("button", { class: mo === mode ? "on" : "", onclick: () => (location.hash = `#/brawlers?id=${id}&mode=${mo}`) }, modeName(mo)))),
        h("div", { class: "label" }, "Best teammates"), pairList(top(pairs, "syn", 1), "syn", (x) => `${x.res > 0 ? "+" : ""}${(x.res * 25).toFixed(1)}%`),
        h("div", { class: "label" }, "Beats"), pairList(top(pairs, "ctr", 1), "ctr", (x) => `${pct(x.raw)} (${x.games})`),
        h("div", { class: "label" }, "Countered by"), pairList(top(pairs, "ctr", -1), "ctr", (x) => `${pct(x.raw)} (${x.games})`))),
    h("div", { class: "card", style: { marginTop: "14px" } }, h("h3", {}, "Builds"),
      h("p", { class: "muted small" }, "The API only shows what players own, not what they equipped. 'Surely equipped' counts games where the player owned only that option (≤ 2 gears)."),
      b ? [buildTable("gadget", "Gadgets"), buildTable("star_power", "Star powers"), buildTable("hypercharge", "Hypercharge"), buildTable("gear", "Gears")] : h("p", { class: "muted" }, "No build data yet")));
}

function statsTable(id) {
  const st = model.info?.[id]?.stats;
  if (!st || !Object.keys(st).length) return null;
  const keys = [...Object.keys(STAT_LABELS).filter((k) => st[k]), ...Object.keys(st).filter((k) => !STAT_LABELS[k]).sort()];
  return h("details", { class: "statsbox" }, h("summary", {}, "Stats (wiki)"),
    h("table", {}, h("tbody", {}, keys.map((k) => h("tr", {}, h("td", { class: "muted" }, STAT_LABELS[k] || k.replace(/(\d+)/g, " $1")), h("td", {}, st[k]))))),
    h("div", { class: "small muted" }, "Source: ", h("a", { href: model.info[id].wiki, target: "_blank", rel: "noopener" }, "Brawl Stars Fandom wiki"), " (CC BY-SA). Values as listed on the wiki."));
}

function trainingCard() {
  const L = model.learnedReport, sp = model.selfplay;
  if (!L && !sp) return null;
  return h("div", { class: "card", style: { marginBottom: "14px" } }, h("h3", {}, "🧠 Self-training"),
    L ? h("p", {}, h("b", {}, "Trained model: "), L.chosen ? (L.blend < 1 ? `in use, blended ${Math.round(L.blend * 100)}% with the smoothed model. ` : "in use. ") : "not in use (did not beat the smoothed model yet). ",
      h("span", { class: "muted" }, `Learned jointly from ${L.train_games.toLocaleString()} real games, tested on ${L.test_games.toLocaleString()} unseen games from ${L.test_day}: `
        + `${pct(L.test.accuracy)} correct winners, log loss ${L.test.log_loss}`
        + (L.baseline_calibrated ? ` (smoothed model: ${L.baseline_calibrated.log_loss})` : "") + "."),
      L.ensemble ? h("div", { class: "small muted" }, `Blend test (${L.ensemble.judged_games.toLocaleString()} unseen games): `
        + `smoothed ${L.ensemble.smoothed.log_loss}, trained ${L.ensemble.trained.log_loss}, blend ${Math.round(L.ensemble.alpha * 100)}% trained → ${L.ensemble.blend.log_loss}`
        + `${L.chosen ? " (in use)" : ""}.`) : null) : null,
    sp ? h("div", {}, h("p", {}, h("b", {}, "Self-play: "), `${(sp.total_drafts || sp.drafts).toLocaleString()} drafts played against itself so far (${sp.drafts} in the last run, ${sp.runs || 1} runs), `
      + `${sp.book_entries || Object.keys(sp.book || {}).length} map/bracket entries in the opening book. `,
      h("span", { class: "muted" }, `Strategy in use: ${sp.search.name}${sp.significant ? " (won the tournament significantly)" : " (default; no rival was significantly better)"}.`)),
      h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Strategy"), h("th", {}, "Edge (win-% points)"), h("th", {}, "Effective drafts"))),
        h("tbody", {}, sp.tournament.map((r) => h("tr", {}, h("td", {}, r.name), h("td", { class: r.edge >= 0 ? "good" : "bad" }, `${r.edge >= 0 ? "+" : ""}${r.edge} ± ${r.se ?? "?"}`), h("td", {}, r.games))))),
      h("p", { class: "small muted" }, `Updated ${new Date(sp.generated_at).toLocaleString()}.`)) : null);
}

function renderStatus() {
  const s = model.summary;
  const days = Object.entries(s.battles_per_day || {}).sort();
  const max = Math.max(1, ...days.map(([, v]) => v));
  const lr = s.last_run || {};
  const perMin = lr.seconds ? (lr.new_battles || 0) / (lr.seconds / 60) : null;
  const kpi = (label, v) => h("div", { class: "card kpi" }, h("div", { class: "muted small" }, label), h("div", { class: "v" }, v));
  app.replaceChildren(h("h2", {}, "Data collection"),
    h("div", { class: "kpis" },
      kpi("Ranked games (28 days)", (s.battles_in_window || 0).toLocaleString()),
      kpi("Games collected all-time", (s.battles_total || 0).toLocaleString()),
      kpi("New games / minute (last run)", perMin ? Math.round(perMin).toLocaleString() : "–"),
      kpi("Players tracked", (s.players_known || 0).toLocaleString()),
      kpi("Player builds known", (s.loadouts || 0).toLocaleString()),
      kpi("Maps tracked", model.maps.maps.length)),
    trainingCard(),
    model.calibration ? h("div", { class: "card", style: { marginBottom: "14px" } }, h("h3", {}, "Prediction quality (games the model never saw)"),
      h("p", { class: "muted small" }, `Tested on ${model.calibration.test_games.toLocaleString()} ranked games from ${model.calibration.test_day}, using stats built only from earlier days.`),
      h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, ""), h("th", {}, "Correct winner"), h("th", {}, "Log loss (lower = better)"))),
        h("tbody", {}, [["Draft model (calibrated)", model.calibration.calibrated], ["Uncalibrated", model.calibration.uncalibrated], ["Coin flip", model.calibration.coin_flip]]
          .map(([l, m]) => h("tr", {}, h("td", {}, l), h("td", {}, pct(m.accuracy)), h("td", {}, m.log_loss.toFixed(4))))))) : null,
    h("div", { class: "card" }, h("h3", {}, "Games per day"), h("div", { class: "bars" }, days.map(([d, v]) => h("div", { title: `${d}: ${v}`, style: { height: `${(v / max) * 100}%` } }))),
      h("div", { class: "row small muted", style: { justifyContent: "space-between" } }, h("span", {}, days[0]?.[0] || ""), h("span", {}, days.at(-1)?.[0] || ""))),
    h("p", { class: "muted small" }, `Stats updated ${s.generated_at ? new Date(s.generated_at).toLocaleString() : "–"}. Newest game: ${s.newest_day || "–"}.`));
  fetch("api/collector/status").then((r) => (r.ok ? r.json() : null)).then((st) => {
    if (!st?.enabled || !location.hash.startsWith("#/status")) return;
    const age = st.lastBatch ? Math.round((Date.now() - st.lastBatch) / 60000) : null;
    const live = age != null && age <= 3;
    app.insertBefore(h("div", { class: "card", style: { marginBottom: "14px" } },
      h("h3", {}, `${live ? "🟢" : "🟡"} 24/7 collector (Cloudflare)`),
      h("p", { class: "muted small" }, `Checks ranked players every minute, around the clock. ${(st.pendingGames || 0).toLocaleString()} games gathered since the last processing run`
        + `${age != null ? ` · last batch ${age} min ago` : ""} · ${st.frontierChunks || 0} player groups in rotation.`)), app.children[2] || null);
  }).catch(() => {});
}

function renderLegal(key) {
  const page = PAGES[key];
  const body = h("div", { class: "card legal" });
  body.innerHTML = page.html;
  app.replaceChildren(h("h2", {}, page.title), body);
  const btn = document.getElementById("clear-local");
  if (btn) btn.onclick = () => {
    try { localStorage.clear(); } catch { }
    profile = null; mine = { on: false, minPower: 11 };
    if (model) draft = freshDraft(defaultMap());
    document.getElementById("clear-msg").textContent = "Done: everything this site saved in your browser was deleted.";
  };
}
function renderFooter(stats) {
  const foot = document.getElementById("foot");
  foot.innerHTML = `${stats ? `<div>${stats}</div>` : ""}
    <div class="footlinks"><a href="#/terms">Terms of Service</a> · <a href="#/privacy">Privacy Policy</a> · <a href="#/legal">Legal &amp; credits</a></div>
    <div class="disclaimer">${FAN_DISCLAIMER}</div>
    <div class="disclaimer">© ${new Date().getFullYear()} Ranked Draft Lab. All rights reserved. The site's code, models and statistics may not be copied or reused.</div>`;
}

function practiceEnv() {
  return { app, h, model, context, portrait, mapImage, mapOptions, bname, pct, store, modeName, defaultMap, bracketSeg,
           myAllowed, rerender: () => renderPractice(practiceEnv()) };
}

function route() {
  const [path, qs] = (location.hash.slice(2) || "draft").split("?");
  const params = new URLSearchParams(qs || "");
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("on", a.dataset.route === path));
  window.scrollTo(0, 0);
  if (PAGES[path]) return renderLegal(path);
  if (!model) return;
  if (path === "meta") renderMeta(params);
  else if (path === "practice") renderPractice(practiceEnv());
  else if (path === "brawlers") renderBrawlers(params);
  else if (path === "status") renderStatus();
  else renderDraft();
}

async function load() {
  const get = async (f, optional) => {
    try {
      const r = await fetch(`data/${f}.json`, { cache: "no-cache" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return await r.json();
    } catch (e) {
      if (optional) return null;
      throw new Error(`data/${f}.json: ${e.message}`);
    }
  };
  try {
    const [maps, brawlers, synergyD, matchups, summary, builds, notes, calibration, learned, selfplay, info] = await Promise.all(
      ["maps", "brawlers", "synergy", "matchups", "summary", "builds"].map((f) => get(f))
        .concat(["ai_notes", "model", "learned", "selfplay", "brawler_info"].map((f) => get(f, true))));
    model = buildModel({ maps, brawlers, synergy: synergyD, matchups, summary, builds, notes, calibration, learned, selfplay });
    model.info = info?.brawlers || {};
    model.learnedReport = learned;
    model.summaryBrawlers = brawlers.brawlers;
    const saved = store.get("draft", null);
    draft = saved && model.mapIndex.has(saved.mapKey) ? saved : freshDraft(defaultMap());
    renderFooter(`${(summary.battles_in_window || 0).toLocaleString()} ranked games · last ${maps.window_days} days · updated ${summary.generated_at ? new Date(summary.generated_at).toLocaleString() : "–"}`);
    route();
  } catch (e) {
    const [path] = (location.hash.slice(2) || "").split("?");
    if (PAGES[path]) renderLegal(path);
    else app.replaceChildren(h("div", { class: "empty" }, `Could not load data: ${e.message}`));
  }
}
renderFooter("");
window.addEventListener("hashchange", route);
load();
