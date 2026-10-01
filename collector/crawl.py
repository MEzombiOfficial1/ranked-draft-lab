import argparse
import hashlib
import json
import os
import random
import sys
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbm
from api import Api, FatalApiError

RANKED_TYPES = {"soloRanked", "teamRanked"}
COUNTRIES = ("global US BR MX DE FR ES IT GB RU TR PL UA KR JP CN TW HK SG MY ID PH TH VN IN SA AE EG MA DZ "
             "TN AR CL CO PE EC CA AU NZ NL BE CH AT SE NO DK FI PT GR RO HU CZ SK BG RS HR IL KZ BY LT LV EE "
             "IE UY PY BO CR GT PA DO PR KW QA JO LB IQ").split()
SEED_EVERY = 24 * 3600
LEASE = 3 * 86400
LOOKAHEAD = 1800
MAX_AGE_DAYS = int(os.environ.get("CRAWL_MAX_AGE_DAYS", 3))
DAILY_CAP = int(os.environ.get("CRAWL_DAILY_CAP", 1_500_000))

EXIT_OK, EXIT_FATAL, EXIT_API_DOWN = 0, 2, 3


def parse_time(s):
    return int(datetime.strptime(s, "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=timezone.utc).timestamp())


def day_of(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def ids(items):
    return ",".join(str(i["id"]) for i in sorted(items or [], key=lambda x: x["id"]))


HIGH_RANK = 16


def next_interval(times, ranked, now, rank=None):
    if not times or now - max(times) > 14 * 86400:
        return 14 * 86400
    rate = len(times) / max(now - min(times), 600)
    interval = 18 / rate
    if ranked == 0:
        interval = max(interval * 4, 12 * 3600)
    elif rank is not None and rank >= HIGH_RANK:
        interval *= 0.7
    return int(min(max(interval, 600), 14 * 86400) * random.uniform(0.9, 1.1))


class Crawler:
    def __init__(self, db, raw_dir, api, workers, profile_share):
        self.db, self.raw_dir, self.api, self.workers, self.profile_share = db, raw_dir, api, workers, profile_share
        self.shards = {}
        self.day_counts = {}
        self.log_q, self.prof_q, self.seed_q = deque(), deque(), deque()
        self.queued = set()
        self.n = {"logs": 0, "profiles": 0, "seeds": 0, "new_battles": 0, "dup_battles": 0, "old_battles": 0, "budget_waits": 0,
                  "new_players": 0}
        self.rank_names = json.loads(dbm.get_meta(db, "rank_names", "{}"))

    def plan_seeds(self, now):
        players = self.db.execute("SELECT COUNT(*) FROM players").fetchone()[0]
        if players >= 1000 and now - int(dbm.get_meta(self.db, "seeded_at", 0)) < SEED_EVERY:
            return
        self.seed_q.extend(f"/rankings/{c}/players?limit=200" for c in COUNTRIES)
        self.seed_q.extend(f"/rankings/global/brawlers/{b}?limit=200"
                           for (b,) in self.db.execute("SELECT id FROM brawlers"))
        dbm.set_meta(self.db, "seeded_at", now)

    def refill(self, now):
        if len(self.log_q) < self.workers * 4:
            rows = self.db.execute("SELECT tag FROM players WHERE next_due <= ? ORDER BY next_due LIMIT 2000",
                                   (now + LOOKAHEAD,)).fetchall()
            tags = [t for (t,) in rows if ("log", t) not in self.queued]
            self.db.executemany("UPDATE players SET next_due = ? WHERE tag = ?", [(now + LEASE, t) for t in tags])
            self.log_q.extend(tags)
            self.queued.update(("log", t) for t in tags)
        if len(self.prof_q) < self.workers * 2:
            rows = self.db.execute("SELECT tag FROM players WHERE needs_profile = 1 LIMIT 500").fetchall()
            tags = [t for (t,) in rows if ("profile", t) not in self.queued]
            self.db.executemany("UPDATE players SET needs_profile = 2 WHERE tag = ?", [(t,) for t in tags])
            self.prof_q.extend(tags)
            self.queued.update(("profile", t) for t in tags)

    def within_budget(self, now):
        if not DAILY_CAP:
            return True
        today = day_of(now)
        self.shard(now, now)
        allowed = DAILY_CAP * min(1.0, (now % 86400) / 86400 + 0.05)
        return self.day_counts.get(today, 0) < allowed

    def next_task(self):
        if self.seed_q:
            return "seed", self.seed_q.popleft()
        done = self.n["logs"] + self.n["profiles"]
        if self.prof_q and (not self.log_q or self.n["profiles"] < self.profile_share * (done + 1)):
            return "profile", self.prof_q.popleft()
        if self.log_q:
            if not self.within_budget(int(time.time())):
                self.n["budget_waits"] += 1
                return ("profile", self.prof_q.popleft()) if self.prof_q else None
            return "log", self.log_q.popleft()
        return None

    def fetch(self, task):
        kind, arg = task
        if kind == "seed":
            return self.api.get(arg)
        if kind == "log":
            return self.api.get(f"/players/{quote(arg)}/battlelog")
        return self.api.get(f"/players/{quote(arg)}")

    def add_player(self, tag, name, source, now, due):
        cur = self.db.execute("INSERT OR IGNORE INTO players(tag, name, source, first_seen, next_due) "
                              "VALUES(?, ?, ?, ?, ?)", (tag, name, source, now, due))
        self.n["new_players"] += cur.rowcount

    def handle_seed(self, data, now):
        for p in (data or {}).get("items", []):
            self.add_player(p["tag"], p.get("name"), "leaderboard", now, now + random.randint(0, 3600))

    def shard(self, t, now):
        day = day_of(t)
        if day < day_of(now - MAX_AGE_DAYS * 86400):
            return None
        if day not in self.shards:
            self.shards[day] = dbm.connect_shard(os.path.join(self.raw_dir, f"battles-{day}.db"))
            self.day_counts[day] = self.shards[day].execute("SELECT COUNT(*) FROM battles").fetchone()[0]
        return self.shards[day]

    def store_battle(self, me, item, t, now):
        shard = self.shard(t, now)
        if shard is None:
            self.n["old_battles"] += 1
            return False
        b = item["battle"]
        teams = b["teams"]
        tags = sorted(p["tag"] for team in teams for p in team)
        digest = hashlib.blake2b(f"{item['battleTime']}|{'|'.join(tags)}".encode(), digest_size=8).digest()
        bkey = int.from_bytes(digest, "big", signed=True)
        mine = next((i for i, team in enumerate(teams) if any(p["tag"] == me for p in team)), None)
        result = b.get("result")
        winner = None
        if mine is not None and result == "victory":
            winner = mine
        elif mine is not None and result == "defeat":
            winner = 1 - mine
        elif result == "draw":
            winner = -1
        ranks = [p.get("brawler", {}).get("trophies") for team in teams for p in team]
        ranks = [r for r in ranks if isinstance(r, int) and r > 0]
        event = item.get("event") or {}
        cur = shard.execute(
            "INSERT OR IGNORE INTO battles(bkey, battle_time, event_id, mode, map, type, duration, winner, "
            "avg_rank, star_player, collected_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (bkey, t, event.get("id"), event.get("mode") or b.get("mode"), event.get("map"), b.get("type"),
             b.get("duration"), winner, sum(ranks) / len(ranks) if ranks else None,
             (b.get("starPlayer") or {}).get("tag"), now))
        if not cur.rowcount:
            self.n["dup_battles"] += 1
            return False
        bid = cur.lastrowid
        for ti, team in enumerate(teams):
            for si, p in enumerate(team):
                br = p.get("brawler") or {}
                shard.execute("INSERT INTO appearances VALUES(?,?,?,?,?,?,?)",
                                (bid, ti, si, p["tag"], br.get("id"), br.get("power"), br.get("trophies")))
                if p["tag"] != me:
                    self.add_player(p["tag"], p.get("name"), "battle", now, now)
                    self.db.execute("UPDATE players SET next_due = ? WHERE tag = ? AND next_due > ? "
                                    "AND (last_crawled IS NULL OR last_crawled < ?)",
                                    (now + 300, p["tag"], now + 300, now - 3600))
                has = self.db.execute("SELECT 1 FROM loadouts WHERE player_tag = ? AND brawler_id = ?",
                                      (p["tag"], br.get("id"))).fetchone()
                if not has and br.get("id"):
                    self.db.execute("INSERT OR IGNORE INTO pending_loadouts VALUES(?, ?)", (p["tag"], br["id"]))
                    self.db.execute("UPDATE players SET needs_profile = 1 WHERE tag = ? AND needs_profile = 0",
                                    (p["tag"],))
        self.n["new_battles"] += 1
        self.day_counts[day_of(t)] = self.day_counts.get(day_of(t), 0) + 1
        return True

    def handle_log(self, tag, status, data, now):
        if data is None:
            retry = 30 * 86400 if status == 404 else 1800
            self.db.execute("UPDATE players SET next_due = ? WHERE tag = ?", (now + retry, tag))
            return
        times, ranked, new, my_rank = [], 0, 0, None
        for item in data.get("items", []):
            try:
                t = parse_time(item["battleTime"])
            except (KeyError, ValueError):
                continue
            times.append(t)
            b = item.get("battle") or {}
            teams = b.get("teams")
            if b.get("type") not in RANKED_TYPES or not teams or len(teams) != 2:
                continue
            ranked += 1
            new += self.store_battle(tag, item, t, now)
            for team in teams:
                for p in team:
                    r = (p.get("brawler") or {}).get("trophies")
                    if p.get("tag") == tag and isinstance(r, int) and r > 0:
                        my_rank = max(my_rank or 0, r)
        self.db.execute(
            "UPDATE players SET last_crawled = ?, crawls = crawls + 1, ranked_found = ranked_found + ?, "
            "last_battle_time = COALESCE(?, last_battle_time), last_rank = COALESCE(?, last_rank), next_due = ? WHERE tag = ?",
            (now, new, max(times) if times else None, my_rank, now + next_interval(times, ranked, now, my_rank), tag))

    def handle_profile(self, tag, data, now):
        if data is None:
            self.db.execute("UPDATE players SET needs_profile = 0, profile_at = ? WHERE tag = ?", (now, tag))
            self.db.execute("DELETE FROM pending_loadouts WHERE player_tag = ?", (tag,))
            return
        owned = {b["id"]: b for b in data.get("brawlers", [])}
        used = self.db.execute("SELECT brawler_id FROM pending_loadouts WHERE player_tag = ? UNION "
                               "SELECT brawler_id FROM loadouts WHERE player_tag = ?", (tag, tag)).fetchall()
        self.db.execute("DELETE FROM pending_loadouts WHERE player_tag = ?", (tag,))
        rows = []
        for (bid,) in used:
            b = owned.get(bid)
            if b:
                rows.append((tag, bid, b.get("power"), ids(b.get("gadgets")), ids(b.get("starPowers")),
                             ids(b.get("gears")), ids(b.get("hyperCharges")),
                             json.dumps(b.get("buffies"), separators=(",", ":")) if b.get("buffies") else None, now))
        self.db.executemany("INSERT OR REPLACE INTO loadouts VALUES(?,?,?,?,?,?,?,?,?)", rows)
        self.db.execute(
            "UPDATE players SET name = ?, trophies = ?, ranked_rank = ?, ranked_elo = ?, highest_ranked_elo = ?, "
            "needs_profile = 0, profile_at = ? WHERE tag = ?",
            (data.get("name"), data.get("trophies"), data.get("rankedRank"), data.get("rankedElo"),
             data.get("highestAllTimeRankedElo"), now, tag))
        for k in ("", "highestSeason", "highestAllTime"):
            rank, name = data.get(f"{k}RankedRank" if k else "rankedRank"), data.get(
                f"{k}RankedRankName" if k else "rankedRankName")
            if isinstance(rank, int) and name:
                self.rank_names[str(rank)] = name

    def refresh_catalog(self):
        status, data = self.api.get("/brawlers")
        if data:
            self.db.executemany("INSERT OR REPLACE INTO brawlers VALUES(?,?,?)",
                                [(b["id"], b["name"], json.dumps(b, separators=(",", ":"))) for b in data["items"]])
        return status

    def run(self, deadline):
        start = time.time()
        if self.refresh_catalog() is None and self.api.consecutive_failures:
            print("API unreachable, stopping.", flush=True)
            return EXIT_API_DOWN
        self.plan_seeds(int(start))
        exit_code, inflight, last_commit, last_log = EXIT_OK, {}, time.time(), time.time()
        with ThreadPoolExecutor(self.workers) as pool:
            while True:
                now = time.time()
                stopping = now >= deadline or exit_code != EXIT_OK
                if not stopping:
                    self.refill(int(now))
                    while len(inflight) < self.workers * 2:
                        task = self.next_task()
                        if task is None:
                            break
                        inflight[pool.submit(self.fetch, task)] = task
                if not inflight:
                    if stopping:
                        break
                    time.sleep(2)
                    continue
                done, _ = wait(inflight, timeout=1, return_when=FIRST_COMPLETED)
                now = int(time.time())
                for fut in done:
                    kind, arg = task = inflight.pop(fut)
                    self.queued.discard(task)
                    try:
                        status, data = fut.result()
                    except FatalApiError as e:
                        print(f"FATAL: {e}", flush=True)
                        exit_code = EXIT_FATAL
                        continue
                    if kind == "seed":
                        self.n["seeds"] += 1
                        self.handle_seed(data, now)
                    elif kind == "log":
                        self.n["logs"] += 1
                        self.handle_log(arg, status, data, now)
                    else:
                        self.n["profiles"] += 1
                        self.handle_profile(arg, data, now)
                if self.api.consecutive_failures > 300 and exit_code == EXIT_OK:
                    print("API keeps failing (maintenance?), stopping early.", flush=True)
                    exit_code = EXIT_API_DOWN
                if time.time() - last_commit > 3:
                    self.commit()
                    last_commit = time.time()
                if time.time() - last_log > 60:
                    self.progress(start)
                    last_log = time.time()
        self.release_queues()
        self.finish(start)
        for shard in self.shards.values():
            dbm.close(shard)
        return exit_code

    def commit(self):
        self.db.commit()
        for shard in self.shards.values():
            shard.commit()

    def release_queues(self):
        now = int(time.time())
        self.db.executemany("UPDATE players SET next_due = ? WHERE tag = ?", [(now, t) for t in self.log_q])
        self.db.execute("UPDATE players SET needs_profile = 1 WHERE needs_profile = 2")

    def progress(self, start):
        s, el = self.api.stats, time.time() - start
        print(f"[{el / 60:5.1f} min] req={s['requests']} ({s['requests'] / el:.1f}/s, limit {self.api.limiter.rate:.1f}/s) "
              f"429={s['throttled']} err={s['errors']} | logs={self.n['logs']} profiles={self.n['profiles']} "
              f"| new battles={self.n['new_battles']} dup={self.n['dup_battles']} too old={self.n['old_battles']} new players={self.n['new_players']}",
              flush=True)

    def finish(self, start):
        self.progress(start)
        dbm.set_meta(self.db, "rank_names", json.dumps(self.rank_names, sort_keys=True))
        total = json.loads(dbm.get_meta(self.db, "lifetime", "{}"))
        for k, v in list(self.n.items()) + [("requests", self.api.stats["requests"])]:
            total[k] = total.get(k, 0) + v
        total["runs"] = total.get("runs", 0) + 1
        dbm.set_meta(self.db, "lifetime", json.dumps(total, sort_keys=True))
        dbm.set_meta(self.db, "last_run", json.dumps({"finished": int(time.time()), "seconds": int(time.time() - start),
                                                      **self.n, **self.api.stats}, sort_keys=True))


def main():
    ap = argparse.ArgumentParser(description="Crawl ranked battle logs into the day shards.")
    ap.add_argument("--core", default="work/core.db")
    ap.add_argument("--raw-dir", default="work/raw")
    ap.add_argument("--minutes", type=float, default=10)
    ap.add_argument("--workers", type=int, default=int(os.environ.get("CRAWL_WORKERS", 48)))
    ap.add_argument("--rate", type=float, default=float(os.environ.get("CRAWL_START_RATE", 10)))
    ap.add_argument("--max-rate", type=float, default=float(os.environ.get("CRAWL_MAX_RATE", 80)))
    ap.add_argument("--profile-share", type=float, default=float(os.environ.get("CRAWL_PROFILE_SHARE", 0.2)))
    args = ap.parse_args()
    key = os.environ.get("BRAWL_STARS_API_KEY")
    if not key:
        sys.exit("BRAWL_STARS_API_KEY is not set")
    db = dbm.connect(args.core)
    api = Api(key, rate=args.rate, max_rate=args.max_rate)
    try:
        code = Crawler(db, args.raw_dir, api, args.workers, args.profile_share).run(time.time() + args.minutes * 60)
    finally:
        dbm.close(db)
    sys.exit(code)


if __name__ == "__main__":
    main()
