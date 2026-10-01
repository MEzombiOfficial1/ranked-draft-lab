# Brawl Stars Ranked Data Collector + Draft Lab

> **Proprietary — all rights reserved.** This code is published for viewing only. Copying, reusing, modifying, re-uploading, hosting or deploying any part of it is not permitted without written permission. See [LICENSE](LICENSE).

Collects **Ranked** (`soloRanked`) battles from the official Brawl Stars API on a schedule and turns them
into stats for a draft helper: brawler pick/win rates per map, synergies, counters and builds.

## Website: Ranked Draft Lab

`site/` is a static app served by a Cloudflare Worker (`worker/`), redeployed after every collection run so the data stays fresh.

- **Draft:** pick the map (current ranked rotation first), the **rank bracket**, and who picks first, then fill bans and picks in Ranked order (1-2-2-1). The brackets are *All ranks*, *Below Legendary* and *Legendary+*, and every stat, including synergies and counters, is tracked per bracket.
  - **Pick suggestions** come from a **lookahead search**: each candidate is played out through the rest of the draft, up to ~70k continuations, with the enemy answering strongly. Candidates are ranked by projected win chance and shown with the immediate win chance, reasons (synergy, counters) and the counter-picks to fear.
  - **Ban suggestions** simulate the full draft with and without each ban. Bans that would hurt your own best first pick are never suggested.
  - **Extras:** the win-probability bar, final breakdown, 🤖 AI coach, and the "My brawlers" player-tag filter (e.g. only power 11).
- **Practice:** draft against a bot (Easy / Normal / Hard; Hard uses the full search). Every pick is graded (Best, Excellent, Good, Inaccuracy, Mistake, Blunder) by how many win-chance points it gives up versus the engine's best move, with the better options shown. You get hints, a simulated result, an accuracy score and your history.
- **Meta:** S–D tier list and full table per map and bracket. **Brawlers:** best maps, teammates, matchups and builds. **Data:** collection stats plus the model's measured prediction quality.
- **Engine** (`site/engine.js`):
  - Win chance = sigmoid(w₁·map strength + w₂·synergy + w₃·counters).
  - Rates are smoothed hierarchically: 50% → mode → map → rank bracket, and pair residuals shrink toward all-rank values, so thin samples never top the lists by luck.
  - The weights w are **calibrated on unseen games** by `collector/calibrate.py`: stats come from older days, the test set is the newest day (`data/stats/model.json`). This keeps win chances honest instead of overconfident.
  - Tests: `node site/test/engine.test.mjs`.
- **Images:** brawler portraits and map images are bundled into the site at deploy time, with a Worker proxy fallback.

## Self-training (every run)

1. **Learns from real games (every run):** `collector/train.py` fits one joint model on every collected game (L2-regularised logistic regression in numpy). It covers brawler strength per mode, per map and for Legendary+, teammate synergy, counters, and **team composition by role** (e.g. how a 2-tank team does in Brawl Ball).
   - Because everything is fitted together, a brawler that only looks strong thanks to its usual partners is not over-credited.
   - The regularisation is re-tuned once a day; the other runs reuse it, so retraining takes ~1–2 minutes.
   - It is tested on the newest day's games, which it never trained on. The site switches to it **only if it beats** the calibrated smoothed model on those games.
2. **Plays itself (every run):** `collector/selfplay.mjs` runs the site's own engine in drafts against itself, on every rotation map, from both sides and with varied bans. Tournament evidence **accumulates across runs** (older runs fade slowly), and the opening book keeps growing, refreshing its oldest entries first.
   - Search strategies compete in a tournament. A rival replaces the default only if it wins by more than 2 standard errors.
   - The winning strategy then builds an **opening book** (first picks and replies to the enemy's likely first picks), searched deeper than a browser can afford. The draft page marks book values with 📘.
3. **Roles and stats:** `collector/brawler_info.py` reads each brawler's class (Tank, Marksman, …), rarity and infobox stats (health, damage, speed, range, …) from the Brawl Stars Fandom wiki's API (CC BY-SA, 3 batched requests a day). The roles feed the composition model and the UI. The stats are shown for reference, while the model learns each brawler's real effectiveness from games, which automatically reflects every balance change.
4. **Blending:** the trained model is also tested as a blend with the smoothed model. The mix is chosen on half of the unseen day and judged on the other half, and the site uses whichever wins.

The Data page and the Discord report show the latest training and self-play results.

## AI

- **Live AI coach:** Cloudflare Workers AI (free daily allowance, ~10k "neurons" a day, enough for a few hundred coach answers). It tries Llama 3.3 70B first and falls back to Llama 3.1 8B.
- **Per-map meta notes:** GitHub Models (free with a GitHub account, rate limited), called with the workflow's own token. One request per mode, at most once every 20 h → `data/stats/ai_notes.json`. The notes are shown on the site and in Discord.
- Neither is truly unlimited. The draft suggestions themselves are computed without AI, so they are instant and unlimited.

## Discord report

After every run, the `DISCORD_WEBHOOK_URL` webhook gets:
- A summary embed: matches scanned per minute, new games per minute, API requests per minute, totals, and an image of the best brawlers overall.
- One embed per ranked map: map image, the top 5 brawlers with win rate, pick rate and game count, a portrait strip image, and the AI note.

## Secrets (Settings → Secrets and variables → Actions)

| Secret | Used for |
|---|---|
| `BRAWL_STARS_API_KEY` | Brawl Stars API. Only the Collect step can read it; it never reaches the website or logs. |
| `CLOUDFLARE_API_TOKEN` | Deploying the Worker (site + AI coach). Optional variable `CLOUDFLARE_ACCOUNT_ID` if the token can see several accounts. |
| `DISCORD_WEBHOOK_URL` | Discord run reports. |

The website and Discord steps skip themselves while their secret is missing.

## How it works

- **Workflow:** `.github/workflows/collect.yml` runs 4× per day (and can be started by hand under *Actions → Collect ranked data → Run workflow*).
- **Crawler:** `collector/crawl.py` visits Legendary+ players ~30% more often (the main audience). It starts from the trophy leaderboards (global, about 80 countries and every brawler), then keeps adding every player it sees in a ranked game. Each player is checked again roughly when ~18 new battles have piled up in their 25-battle log. The request rate adjusts itself: it speeds up while requests succeed and backs off on HTTP 429.
- **Profiles:** about 25% of requests fetch player profiles, which give each player's owned gadgets, star powers, gears and hypercharges, plus their ranked Elo and rank.
- **API access:** requests go through the RoyaleAPI proxy (`bsproxy.royaleapi.dev`). The key is read from the `BRAWL_STARS_API_KEY` secret and must whitelist `45.79.218.79`.

## Where the data lives

| What | Where |
|---|---|
| Every collected ranked game (SQLite `battles` + `appearances`, one file per UTC day, kept forever) | Releases **`raw-YYYY-MM`** → `battles-YYYY-MM-DD.db.gz` |
| Core database: crawl frontier, players (Elo/rank), owned builds, daily aggregates | Release **`ranked-database`** → newest `core-*.db.gz` |
| Aggregated stats for the website | `data/stats/*.json` (committed after every run) |

Databases are release assets rather than git files, so the repo stays small and no file ever has to hold all the data. Games older than 3 days when first seen are skipped: they are stale for the meta, and skipping them lets each run touch only the newest day files.

`data/stats` (the last 28 days of battles, so the stats follow the current meta). Win/loss arrays are `[games, wins, losses, draws]`:

- `summary.json`: totals, collector health, brawler names, ranked tier names.
- `maps.json`: per mode+map, per brawler results, at all ranks and at high ranks (`brawlers_high`, Legendary+). `current_pool` lists the maps seen in the last 3 days.
- `brawlers.json`: per brawler results, overall and per mode.
- `synergy.json` / `matchups.json`: brawler pairs on the same team / on opposite teams, per mode.
- `builds.json`: per brawler build items.

**Build caveat:** the API does not reveal which gadget, star power or gears were equipped in a match, only what the player owns. `builds.json` gives two counts for each item:
- **owned:** how often the item is owned.
- **certainly equipped:** only counted when the player owns a single gadget or star power, or ≤ 2 gears, so the equipped choice is known.

## Using the whole free budget

`collector/budget.py` records every run's minutes in `data/stats/usage.json`. Before each run it splits the remaining monthly budget (`ACTIONS_BUDGET_MINUTES`, default 1,900 of the 2,000 free minutes) over the remaining scheduled runs. So the system always uses as much as it is allowed, and never more. With a bigger plan, raise the repository variable and everything scales up.

## 24/7 collection (repo stays private)

Two collectors work together:

1. **Edge collector (24/7):** a Cloudflare Worker with a cron trigger every minute, on the free plan (`worker/src/collector.js`).
   - Each minute it fetches the battle logs of 36 ranked players from its frontier (≈52k player checks a day), choosing the active ranked players the GitHub crawler checked least recently, so it finds games GitHub misses. It keeps only ranked games and stores them as one compact batch in a free D1 database (`ranked-edge`).
   - It stays inside the free plan's limits: ≤ 50 subrequests, ~6 ms of the 10 ms CPU, and 1 row written per minute.
   - `deploy/deploy.sh` creates the database and cron automatically.
2. **GitHub Actions (3× per day, heavy work):** each run first **pulls** every edge batch into the database, deduplicated, and clears D1 (`collector/edge.py pull`). It then crawls at full speed (~45 requests/s for 14 minutes), rebuilds stats, calibrates, deploys the site, uploads the edge collector's **next frontier**, and posts to Discord.
   - The frontier is the most active ranked players, Legendary+ first, with the top 10k visited twice per cycle.

Private repos get 2,000 free Actions minutes per month (the 3 × 14-minute runs use ≈1,650). If the repo were ever made public, the workflow would detect it and switch Actions itself to continuous back-to-back runs as well.

- **Override:** set the repository variable `CRAWL_MINUTES` (Settings → Secrets and variables → Actions → Variables) to change the Actions crawl length.

To keep storage bounded, the crawler:
- stores at most `CRAWL_DAILY_CAP` new games per UTC day (default 1.5M), released evenly over the day so data stays fresh. When it is ahead of budget it spends requests on player profiles (build data) instead.
- re-aggregates only day files that changed.
- deletes raw day files after `RAW_KEEP_DAYS` (default 60). The daily aggregates behind every statistic are kept forever.

Optional tuning environment variables: `CRAWL_DAILY_CAP` (1500000), `RAW_KEEP_DAYS` (60), `CRAWL_WORKERS` (48), `CRAWL_START_RATE` (10 req/s), `CRAWL_MAX_RATE` (80 req/s), `CRAWL_PROFILE_SHARE` (0.25), `CRAWL_MAX_AGE_DAYS` (3), `STATS_WINDOW_DAYS` (28), `STATS_MIN_PAIR_GAMES` (10).

## Local use

```bash
bash collector/db_release.sh download work          # needs gh + GH_TOKEN; restores core.db and recent day files
BRAWL_STARS_API_KEY=... python3 collector/crawl.py --minutes 5
python3 collector/export_stats.py                    # folds day files into aggregates, writes data/stats
```
