export const SCHEMA = `
CREATE TABLE IF NOT EXISTS frontier(i INTEGER PRIMARY KEY, tags TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS batches(id INTEGER PRIMARY KEY AUTOINCREMENT, created INTEGER NOT NULL, n INTEGER NOT NULL, data TEXT NOT NULL);
`;

const RANKED = new Set(["soloRanked", "teamRanked"]);

export async function collect(env) {
  if (!env.DB || !env.BRAWL_STARS_API_KEY) return { skipped: "not configured" };
  const minute = Math.floor(Date.now() / 60000);
  const row = await env.DB.prepare("SELECT tags FROM frontier WHERE i = ?1 % (SELECT MAX(i) + 1 FROM frontier)")
    .bind(minute).first();
  if (!row) return { skipped: "empty frontier" };
  const tags = row.tags.split(",").filter(Boolean).slice(0, 44);
  const base = env.BS_API_BASE || "https://bsproxy.royaleapi.dev/v1";
  const headers = { Authorization: `Bearer ${env.BRAWL_STARS_API_KEY}`, Accept: "application/json" };
  const bodies = await Promise.all(tags.map((t) =>
    fetch(`${base}/players/%23${t}/battlelog`, { headers }).then((r) => (r.ok ? r.text() : null)).catch(() => null)));

  const seen = new Set();
  const out = [];
  let logs = 0;
  for (let k = 0; k < tags.length; k++) {
    if (!bodies[k]) continue;
    let log;
    try { log = JSON.parse(bodies[k]); } catch { continue; }
    logs++;
    const me = `#${tags[k]}`;
    for (const it of log.items || []) {
      const b = it.battle;
      if (!b || !RANKED.has(b.type) || !b.teams || b.teams.length !== 2) continue;
      let minTag = "~";
      for (const team of b.teams) for (const p of team) if (p.tag < minTag) minTag = p.tag;
      const key = it.battleTime + minTag;
      if (seen.has(key)) continue;
      seen.add(key);
      const teams = b.teams.map((team) => team.map((p) => [p.tag, p.brawler?.id ?? null, p.brawler?.power ?? null, p.brawler?.trophies ?? null]));
      out.push([it.battleTime, it.event?.id ?? null, it.event?.mode || b.mode || null, it.event?.map ?? null, b.type,
        b.duration ?? null, b.result ?? null, b.starPlayer?.tag ?? null, teams, me]);
    }
  }
  if (out.length) {
    await env.DB.prepare("INSERT INTO batches(created, n, data) VALUES(?1, ?2, ?3)")
      .bind(Date.now(), out.length, JSON.stringify(out)).run();
  }
  return { players: tags.length, logs, games: out.length };
}

export async function status(env) {
  if (!env.DB) return { enabled: false };
  const b = await env.DB.prepare("SELECT COUNT(*) AS batches, COALESCE(SUM(n), 0) AS games, MAX(created) AS last FROM batches").first();
  const f = await env.DB.prepare("SELECT COUNT(*) AS chunks FROM frontier").first();
  return { enabled: true, pendingBatches: b.batches, pendingGames: b.games, lastBatch: b.last, frontierChunks: f.chunks };
}
