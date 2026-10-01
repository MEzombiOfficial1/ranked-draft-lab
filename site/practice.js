import { suggest, suggestBans, evaluatePick, botPick, botBans, gradePick, winProbability, mapStats, PICK_ORDER, BRACKETS } from "./engine.js";

const LEVELS = { easy: "Easy", normal: "Normal", hard: "Hard" };
const GRADE_CLASS = { Best: "good", Excellent: "good", Good: "", Inaccuracy: "warn", Mistake: "bad", Blunder: "bad" };
let P = null;
let botTimer = null;

function newSession(env, settings) {
  const s = settings || env.store.get("practiceSettings", { mapKey: env.defaultMap(), bracket: env.store.get("bracket", "high"), side: "random", level: "normal" });
  const weFirst = s.side === "first" ? true : s.side === "second" ? false : Math.random() < 0.5;
  return { phase: "ban", ...s, weFirst, A: { ban: [], pick: [] }, B: { ban: [], pick: [] }, log: [], hints: 0, hint: null, feedback: null };
}

const seq = (weFirst) => PICK_ORDER.map((t) => (weFirst ? t : t === "A" ? "B" : "A"));
const bansOf = () => [...P.A.ban, ...P.B.ban];
const takenSet = () => new Set([...P.A.ban, ...P.B.ban, ...P.A.pick, ...P.B.pick]);
const nextSide = () => seq(P.weFirst)[P.A.pick.length + P.B.pick.length];

export function renderPractice(env) {
  clearTimeout(botTimer);
  if (!P || P.phase === "setup") return renderSetup(env);
  const { h, app } = env;
  const ctx = env.context(env.model, P.mapKey, P.bracket);
  const complete = P.A.pick.length === 3 && P.B.pick.length === 3;
  if (complete && P.phase !== "done") finish(env, ctx);

  if (P.phase === "pick" && !complete && nextSide() === "B") {
    botTimer = setTimeout(() => {
      if (!location.hash.startsWith("#/practice")) return;
      const id = botPick(ctx, { ours: P.B.pick, theirs: P.A.pick, bans: bansOf() }, P.level, { weFirst: !P.weFirst });
      if (id != null) P.B.pick.push(id);
      env.store.set("practice", P);
      env.rerender();
    }, 650);
  }

  const p = P.A.pick.length || P.B.pick.length ? winProbability(ctx, P.A.pick, P.B.pick) : 0.5;
  const status = P.phase === "ban" ? `Choose up to 3 bans, then lock them (${P.A.ban.length}/3)`
    : P.phase === "done" ? "Draft finished"
    : nextSide() === "A" ? "Your pick: click a brawler" : "Bot is picking…";

  const slotRow = (list, kind, n = 3) => h("div", { class: "slots" }, Array.from({ length: n }, (_, i) => {
    const id = list[i];
    const el = h("div", { class: `slot ${kind}${id != null ? " filled" : ""}` });
    if (id != null) {
      const pt = env.portrait(id, kind === "ban" ? 50 : 200);
      if (kind === "pick") { pt.style.width = "100%"; pt.style.height = "100%"; }
      el.append(pt);
      if (kind === "pick") el.append(h("span", { class: "nm" }, env.bname(id)));
    } else el.append(kind);
    return el;
  }));
  const teamBox = (t, label) => h("div", { class: `team ${t}` },
    h("h3", {}, label, h("span", { class: "small muted" }, (t === "A") === P.weFirst ? "1st pick" : "2nd pick")),
    h("div", { class: "label" }, "Bans"), slotRow(P.phase === "ban" && t === "B" ? [] : P[t].ban, "ban"),
    h("div", { class: "label" }, "Picks"), slotRow(P[t].pick, "pick"));

  const mid = h("div", { class: "mid card" },
    h("div", { class: "phase" }, status),
    h("div", { class: "winbox" }, h("div", { class: "winnum", style: { color: p >= 0.5 ? "var(--blue)" : "var(--red)" } }, env.pct(p)),
      h("div", { class: "winbar" }, h("div", { style: { width: env.pct(p, 2) } })), h("div", { class: "small muted" }, "Your win chance")),
    P.phase === "ban" ? h("button", { class: "btn primary", onclick: () => lockBans(env, ctx) }, "Lock bans") : null,
    P.phase === "pick" && nextSide() === "A" ? h("button", { class: "btn", onclick: () => showHint(env, ctx) }, `💡 Hint (${P.hints} used)`) : null,
    h("button", { class: "btn", onclick: () => { P = { ...P, phase: "setup" }; env.rerender(); } }, "End / new setup"));

  const panel = h("div", { class: "card" });
  if (P.phase === "done") panel.append(...review(env, ctx));
  else {
    if (P.feedback) panel.append(feedbackCard(env, P.feedback));
    if (P.hint) panel.append(h("div", { class: "label" }, "Hint: strongest options"),
      h("div", { class: "row" }, P.hint.map((s) => h("div", { class: "tcell" }, env.portrait(s.id, 48), h("div", { class: "small" }, env.bname(s.id)),
        h("div", { class: "small good" }, env.pct(s.projected ?? s.win))))));
    if (!P.feedback && !P.hint) panel.append(h("h3", {}, "Practice draft"),
      h("p", { class: "muted" }, `${LEVELS[P.level]} bot · ${BRACKETS[P.bracket]} data. Draft without suggestions; every pick is graded against the engine's best move (lookahead search).`));
  }

  app.replaceChildren(
    h("div", { class: "card mapbanner" }, env.mapImage(ctx.mapObj), h("div", {}, h("div", { class: "title display" }, ctx.mapObj.map),
      h("div", { class: "row" }, h("span", { class: "pill" }, env.modeName(ctx.mode)), h("span", { class: "pill" }, `Bot: ${LEVELS[P.level]}`),
        h("span", { class: "pill" }, BRACKETS[P.bracket]), h("span", { class: "pill" }, P.weFirst ? "You pick first" : "Bot picks first")))),
    h("div", { class: "board" }, teamBox("A", "You"), mid, teamBox("B", "Bot")),
    h("div", { class: "layout" }, panel, h("div", { class: "card" }, P.phase === "done" ? history(env) : grid(env, ctx))));
}

function grid(env, ctx) {
  const { h } = env;
  const used = takenSet();
  const search = h("input", { type: "search", placeholder: "Search brawler… (Enter = first match)" });
  const box = h("div", { class: "bgrid" });
  const clickable = P.phase === "ban" || (P.phase === "pick" && nextSide() === "A");
  const fill = () => {
    const q = search.value.trim().toLowerCase();
    const ids = ctx.ids.filter((id) => env.bname(id).toLowerCase().includes(q));
    box.replaceChildren(...ids.map((id) => {
      const mineBan = P.phase === "ban" && P.A.ban.includes(id);
      const b = h("button", { class: `bt${used.has(id) && !mineBan ? " used" : ""}${mineBan ? " sel" : ""}`, title: env.bname(id),
        onclick: () => clickable && choose(env, ctx, id) }, env.portrait(id, 200));
      b.querySelector(".pt").style.width = "100%"; b.querySelector(".pt").style.height = "100%";
      return b;
    }));
    return ids;
  };
  search.addEventListener("input", fill);
  search.addEventListener("keydown", (e) => { if (e.key === "Enter") { const id = fill().find((x) => !used.has(x)); if (id != null) choose(env, ctx, id); } });
  fill();
  return h("div", {}, h("div", { class: "gridtools" }, search), box);
}

function choose(env, ctx, id) {
  if (P.phase === "ban") {
    const i = P.A.ban.indexOf(id);
    if (i >= 0) P.A.ban.splice(i, 1);
    else if (P.A.ban.length < 3 && !takenSet().has(id)) P.A.ban.push(id);
    env.store.set("practice", P);
    return env.rerender();
  }
  if (P.phase !== "pick" || nextSide() !== "A" || takenSet().has(id)) return;
  const state = { ours: [...P.A.pick], theirs: [...P.B.pick], bans: bansOf() };
  const allowed = env.myAllowed();
  const best = suggest(ctx, state, { limit: 3, weFirst: P.weFirst, allowed });
  const val = evaluatePick(ctx, state, id, { weFirst: P.weFirst });
  const bestVal = Math.max(best[0]?.projected ?? val, val);
  const g = gradePick(bestVal, val);
  P.log.push({ kind: "pick", id, ...g, value: val, best: best.filter((s) => s.id !== id).slice(0, 2).map((s) => ({ id: s.id, v: s.projected })), bestVal });
  P.feedback = P.log.at(-1);
  P.hint = null;
  P.A.pick.push(id);
  env.store.set("practice", P);
  env.rerender();
}

function lockBans(env, ctx) {
  const rec = suggestBans(ctx, { ours: [], theirs: [], bans: [] }, { weFirst: P.weFirst, limit: 8 });
  const rank = new Map(rec.map((r, i) => [r.id, i]));
  P.log.push({ kind: "bans", ids: [...P.A.ban], rated: P.A.ban.map((id) => ({ id, verdict: rank.has(id) ? (rank.get(id) < 4 ? "Great ban" : "Good ban") : "Low impact" })),
    best: rec.slice(0, 3).map((r) => r.id) });
  P.B.ban = botBans(ctx, { ours: [], theirs: [], bans: [...P.A.ban] }, 3, P.level);
  P.phase = "pick";
  P.feedback = { kind: "bans", ...P.log.at(-1) };
  env.store.set("practice", P);
  env.rerender();
}

function showHint(env, ctx) {
  P.hints++;
  P.hint = suggest(ctx, { ours: P.A.pick, theirs: P.B.pick, bans: bansOf() }, { limit: 3, weFirst: P.weFirst, allowed: env.myAllowed() });
  env.rerender();
}

function feedbackCard(env, f) {
  const { h } = env;
  if (f.kind === "bans") {
    return h("div", {}, h("h3", {}, "Your bans"),
      h("div", { class: "row" }, f.rated.map((r) => h("div", { class: "tcell" }, env.portrait(r.id, 48), h("div", { class: "small" }, env.bname(r.id)),
        h("div", { class: `small ${r.verdict === "Low impact" ? "warn" : "good"}` }, r.verdict)))),
      h("p", { class: "small muted" }, `Engine's top bans here: ${f.best.map(env.bname).join(", ")}`));
  }
  return h("div", { class: "feedback" },
    h("div", { class: "row" }, env.portrait(f.id, 56), h("div", {},
      h("div", { class: `grade ${GRADE_CLASS[f.grade]}` }, f.grade),
      h("div", { class: "small muted" }, `${env.bname(f.id)} · projected ${env.pct(f.value)} · ${f.loss < 0.05 ? "no loss" : `−${f.loss.toFixed(1)} win points vs best`}`))),
    f.best.length && f.loss >= 0.3 ? h("div", { class: "small", style: { marginTop: "6px" } }, "Better: ",
      f.best.map((b) => `${env.bname(b.id)} (${env.pct(b.v)})`).join(" · ")) : null);
}

function finish(env, ctx) {
  const win = winProbability(ctx, P.A.pick, P.B.pick);
  const picks = P.log.filter((l) => l.kind === "pick");
  const accuracy = picks.length ? Math.round(picks.reduce((s, l) => s + l.accuracy, 0) / picks.length) : 0;
  const result = Math.random() < win ? "Victory" : "Defeat";
  P.phase = "done";
  P.summary = { win, accuracy, result };
  const hist = env.store.get("practiceHistory", []);
  hist.unshift({ date: Date.now(), map: ctx.mapObj.map, level: P.level, bracket: P.bracket, accuracy, win, result, hints: P.hints });
  env.store.set("practiceHistory", hist.slice(0, 30));
  env.store.set("practice", P);
}

function review(env) {
  const { h } = env;
  const s = P.summary;
  const picks = P.log.filter((l) => l.kind === "pick");
  return [
    h("h3", {}, `${s.result === "Victory" ? "🏆" : "💀"} Simulated result: ${s.result}`),
    h("div", { class: "kpis" },
      h("div", { class: "card kpi" }, h("div", { class: "muted small" }, "Draft win chance"), h("div", { class: "v" }, env.pct(s.win))),
      h("div", { class: "card kpi" }, h("div", { class: "muted small" }, "Pick accuracy"), h("div", { class: "v" }, `${s.accuracy}%`)),
      h("div", { class: "card kpi" }, h("div", { class: "muted small" }, "Hints used"), h("div", { class: "v" }, P.hints))),
    h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Your pick"), h("th", {}, "Grade"), h("th", {}, "Loss"), h("th", {}, "Engine preferred"))),
      h("tbody", {}, picks.map((l) => h("tr", {}, h("td", {}, env.bname(l.id)), h("td", { class: GRADE_CLASS[l.grade] }, l.grade),
        h("td", {}, l.loss < 0.05 ? "–" : `−${l.loss.toFixed(1)}`), h("td", { class: "muted" }, l.loss >= 0.3 ? l.best.map((b) => env.bname(b.id)).join(", ") : "✓"))))),
    h("div", { class: "row", style: { marginTop: "12px" } },
      h("button", { class: "btn primary", onclick: () => { P = newSession(env, settingsOf()); env.rerender(); } }, "Rematch (same settings)"),
      h("button", { class: "btn", onclick: () => { P = { ...P, phase: "setup" }; env.rerender(); } }, "New setup")),
  ];
}
const settingsOf = () => ({ mapKey: P.mapKey, bracket: P.bracket, side: P.side, level: P.level });

function history(env) {
  const { h } = env;
  const hist = env.store.get("practiceHistory", []);
  const avg = hist.length ? Math.round(hist.reduce((s, x) => s + x.accuracy, 0) / hist.length) : null;
  return h("div", {}, h("h3", {}, "Your practice history"),
    avg != null ? h("p", { class: "muted" }, `${hist.length} drafts · average accuracy ${avg}% · ${hist.filter((x) => x.result === "Victory").length} simulated wins`) : null,
    h("table", {}, h("tbody", {}, hist.slice(0, 12).map((x) => h("tr", {}, h("td", {}, new Date(x.date).toLocaleDateString()), h("td", {}, x.map),
      h("td", { class: "muted" }, LEVELS[x.level]), h("td", {}, `${x.accuracy}%`), h("td", {}, env.pct(x.win)), h("td", { class: x.result === "Victory" ? "good" : "bad" }, x.result))))));
}

function renderSetup(env) {
  const { h, app, store } = env;
  const s = store.get("practiceSettings", { mapKey: env.defaultMap(), bracket: store.get("bracket", "high"), side: "random", level: "normal" });
  if (!env.model.mapIndex.has(s.mapKey)) s.mapKey = env.defaultMap();
  const save = () => { store.set("practiceSettings", s); renderSetup(env); };
  const seg = (opts, val, set) => h("div", { class: "seg" }, Object.entries(opts).map(([v, l]) => h("button", { class: v === val ? "on" : "", onclick: () => { set(v); save(); } }, l)));
  const sel = h("select", { onchange: (e) => { s.mapKey = e.target.value; save(); } });
  env.mapOptions(sel, s.mapKey);
  const pool = env.model.maps.current_pool;
  const saved = store.get("practice", null);
  app.replaceChildren(
    h("h2", {}, "Practice drafting"),
    h("p", { class: "muted" }, "Draft against a bot built on the same engine. Every pick is graded (Best → Blunder) by how much win chance it gives up versus the engine's best move, with the better options shown. Your history is saved in this browser."),
    h("div", { class: "card", style: { display: "grid", gap: "12px", maxWidth: "720px" } },
      h("div", { class: "row" }, h("b", {}, "Map"), sel, h("button", { class: "btn", onclick: () => { const [m, n] = pool[Math.floor(Math.random() * pool.length)]; s.mapKey = `${m}|${n}`; save(); } }, "🎲 Random rotation map")),
      h("div", { class: "row" }, h("b", {}, "Rank data"), seg(BRACKETS, s.bracket, (v) => (s.bracket = v))),
      h("div", { class: "row" }, h("b", {}, "Your side"), seg({ first: "First pick", second: "Second pick", random: "Random" }, s.side, (v) => (s.side = v))),
      h("div", { class: "row" }, h("b", {}, "Bot"), seg(LEVELS, s.level, (v) => (s.level = v))),
      h("p", { class: "small muted" }, "Easy: plays popular picks with lots of randomness. Normal: picks strong options with a little randomness. Hard: uses the full lookahead search, like a top player."),
      h("div", { class: "row" },
        h("button", { class: "btn primary", onclick: () => { P = newSession(env, s); store.set("practice", P); env.rerender(); } }, "Start practice draft"),
        saved && saved.phase !== "done" && saved.phase !== "setup" ? h("button", { class: "btn", onclick: () => { P = saved; env.rerender(); } }, "Resume last draft") : null)),
    h("div", { class: "card", style: { marginTop: "14px" } }, history(env)));
}
