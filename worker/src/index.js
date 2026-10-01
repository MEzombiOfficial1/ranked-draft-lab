import { collect, status } from "./collector.js";

const SYSTEM = `You are an expert Brawl Stars Ranked draft coach. You get the live draft state and statistics
computed from thousands of real ranked games (win rates are Bayesian-smoothed; "winIfPicked" already includes
map strength, synergy with teammates and counters against enemy picks). Give concise, concrete advice:
1) The best pick or ban right now and why (2-3 reasons grounded in the numbers).
2) One or two alternatives and when to prefer them.
3) What the enemy is likely to do next and how to prepare.
"brawlerInfo"/"role"/"stats" give each brawler's class and wiki stats (❤ health, ⚔ damage per projectile × count, ↔ range):
use them to explain WHY (range, burst, tankiness, team roles). Use brawler names exactly as given. No more than 140 words. Plain text, short bullet lines, no markdown headers.`;

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json", "cache-control": "no-store" } });

async function coach(request, env) {
  if (!env.AI) return json({ error: "Workers AI is not bound" }, 503);
  const body = await request.text();
  if (body.length > 12000) return json({ error: "Request too large" }, 413);
  let draft;
  try { draft = JSON.parse(body); } catch { return json({ error: "Invalid JSON" }, 400); }
  const messages = [{ role: "system", content: SYSTEM }, { role: "user", content: JSON.stringify(draft) }];
  const models = (env.AI_MODELS || "").split(",").map((s) => s.trim()).filter(Boolean);
  let lastError = "no model configured";
  for (const model of models) {
    try {
      const out = await env.AI.run(model, { messages, max_tokens: 380, temperature: 0.4 });
      const text = typeof out?.response === "string" ? out.response : out?.choices?.[0]?.message?.content;
      if (text) return json({ text: text.trim(), model });
      lastError = "empty response";
    } catch (e) {
      lastError = String(e?.message || e);
    }
  }
  return json({ error: `AI unavailable: ${lastError}` }, 503);
}

const TAG_RE = /^[0289PYLQGRJCUV]{3,14}$/;
const ids = (xs) => (xs || []).map((x) => x.id);

async function player(tagRaw, env, ctx) {
  const tag = decodeURIComponent(tagRaw).toUpperCase().replace(/^#/, "").replace(/O/g, "0");
  if (!TAG_RE.test(tag)) return json({ error: "That does not look like a Brawl Stars player tag." }, 400);
  if (!env.BRAWL_STARS_API_KEY) return json({ error: "Player lookup is not configured." }, 503);
  const cache = caches.default;
  const cacheKey = new Request(`https://cache.internal/player/${tag}`);
  const hit = await cache.match(cacheKey);
  if (hit) return hit;
  const base = env.BS_API_BASE || "https://bsproxy.royaleapi.dev/v1";
  const res = await fetch(`${base}/players/%23${tag}`, {
    headers: { Authorization: `Bearer ${env.BRAWL_STARS_API_KEY}`, Accept: "application/json" },
  });
  if (res.status === 404) return json({ error: `No player found with tag #${tag}.` }, 404);
  if (!res.ok) return json({ error: `Brawl Stars API answered ${res.status}. Try again in a minute.` }, 502);
  const p = await res.json();
  const out = json({
    tag: p.tag, name: p.name, trophies: p.trophies, rankedRankName: p.rankedRankName, rankedElo: p.rankedElo,
    brawlers: (p.brawlers || []).map((b) => ({ id: b.id, power: b.power, rank: b.rank, gadgets: ids(b.gadgets),
      starPowers: ids(b.starPowers), gears: ids(b.gears), hyperCharges: ids(b.hyperCharges) })),
  });
  out.headers.set("cache-control", "public, max-age=300");
  ctx.waitUntil(cache.put(cacheKey, out.clone()));
  return out;
}

const IMG_SRC = { brawler: (id) => `https://cdn.brawlify.com/brawlers/borderless/${id}.png`,
                  map: (id) => `https://cdn.brawlify.com/maps/regular/${id}.png` };

async function image(request, kind, id, ctx) {
  const cache = caches.default;
  const hit = await cache.match(request);
  if (hit) return hit;
  const res = await fetch(IMG_SRC[kind](id), { headers: { "User-Agent": "ranked-draft-lab/1.0" }, cf: { cacheTtl: 604800, cacheEverything: true } });
  if (!res.ok) return new Response("Image not found", { status: 404, headers: { "cache-control": "public, max-age=3600" } });
  const out = new Response(res.body, { headers: { "content-type": res.headers.get("content-type") || "image/png",
    "cache-control": "public, max-age=604800, immutable" } });
  ctx.waitUntil(cache.put(request, out.clone()));
  return out;
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const im = url.pathname.match(/^\/img\/(brawler|map)\/(\d{6,10})\.png$/);
    if (im) return image(request, im[1], im[2], ctx);
    if (url.pathname.startsWith("/img/")) return new Response("Not found", { status: 404 });
    const pm = url.pathname.match(/^\/api\/player\/([^/]+)$/);
    if (pm) return player(pm[1], env, ctx);
    if (url.pathname === "/api/ai") {
      if (request.method !== "POST") return json({ error: "POST only" }, 405);
      return coach(request, env);
    }
    if (url.pathname === "/api/health") return json({ ok: true, ai: Boolean(env.AI), collector: Boolean(env.DB) });
    if (url.pathname === "/api/collector/status") return json(await status(env));
    if (url.pathname.startsWith("/api/")) return json({ error: "Not found" }, 404);
    return env.ASSETS.fetch(request);
  },

  async scheduled(event, env, ctx) {
    ctx.waitUntil(collect(env).then((r) => console.log(JSON.stringify(r))).catch((e) => console.log(`collect failed: ${e}`)));
  },
};
