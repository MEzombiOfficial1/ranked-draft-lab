import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbm
from crawl import Crawler, parse_time

CF_API = "https://api.cloudflare.com/client/v4"
DB_NAME = "ranked-edge"
CHUNK = int(os.environ.get("EDGE_CHUNK", 36))
TAG_RE = re.compile(r"^[0289PYLQGRJCUV]{3,14}$")


def cf(method, path, body=None):
    token = os.environ["CLOUDFLARE_API_TOKEN"]
    req = urllib.request.Request(f"{CF_API}{path}", method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        out = json.load(r)
    if not out.get("success", False):
        raise RuntimeError(f"Cloudflare API error: {out.get('errors')}")
    return out["result"]


def locate():
    acct = os.environ.get("CLOUDFLARE_ACCOUNT_ID") or cf("GET", "/accounts?per_page=5")[0]["id"]
    dbs = [d for d in cf("GET", f"/accounts/{acct}/d1/database?name={DB_NAME}") if d.get("name") == DB_NAME]
    if not dbs:
        raise RuntimeError("edge database not found (it is created by deploy/deploy.sh)")
    return acct, dbs[0]["uuid"]


def query(acct, db, sql, params=None):
    res = cf("POST", f"/accounts/{acct}/d1/database/{db}/query", {"sql": sql, "params": params or []})
    return res[0].get("results", []) if res else []


def to_item(b):
    bt, event_id, mode, map_, btype, duration, result, star, teams, _me = b
    return {"battleTime": bt, "event": {"id": event_id, "mode": mode, "map": map_},
            "battle": {"mode": mode, "type": btype, "duration": duration, "result": result,
                       "starPlayer": {"tag": star} if star else None,
                       "teams": [[{"tag": t, "brawler": {"id": bid, "power": pw, "trophies": rk}} for t, bid, pw, rk in team]
                                 for team in teams]}}


def pull(core_path, raw_dir):
    acct, db = locate()
    core = dbm.connect(core_path)
    crawler = Crawler(core, raw_dir, api=None, workers=1, profile_share=0)
    now = int(time.time())
    last, batches, games = 0, 0, 0
    before = dict(crawler.n)
    while True:
        rows = query(acct, db, "SELECT id, data FROM batches WHERE id > ? ORDER BY id LIMIT 40", [last])
        if not rows:
            break
        for row in rows:
            last = row["id"]
            batches += 1
            for b in json.loads(row["data"]):
                games += 1
                try:
                    crawler.store_battle(b[9], to_item(b), parse_time(b[0]), now)
                except (KeyError, ValueError, TypeError, IndexError):
                    continue
        crawler.commit()
    for shard in crawler.shards.values():
        dbm.close(shard)
    if last:
        query(acct, db, "DELETE FROM batches WHERE id <= ?", [last])
    stats = {"at": now, "batches": batches, "games": games,
             **{k: crawler.n[k] - before.get(k, 0) for k in ("new_battles", "dup_battles", "old_battles", "new_players")}}
    dbm.set_meta(core, "edge_pull", json.dumps(stats))
    dbm.close(core)
    print(f"Edge pull: {batches} batches, {games} games -> {stats['new_battles']} new, {stats['dup_battles']} duplicates, "
          f"{stats['old_battles']} too old, {stats['new_players']} new players")


def push_frontier(core_path, size, top):
    acct, db = locate()
    core = dbm.connect(core_path)
    recent = int(time.time()) - 3 * 86400
    tags = [t for (t,) in core.execute(
        "SELECT tag FROM players WHERE last_rank IS NOT NULL AND last_battle_time > ? "
        "ORDER BY (last_rank >= 16) DESC, COALESCE(last_crawled, 0) ASC LIMIT ?", (recent, size))]
    if len(tags) < size:
        have = set(tags)
        tags += [t for (t,) in core.execute("SELECT tag FROM players WHERE source = 'battle' ORDER BY first_seen DESC LIMIT ?",
                                            (size,)) if t not in have][: size - len(tags)]
    dbm.close(core)
    clean = [t.lstrip("#") for t in tags if TAG_RE.match(t.lstrip("#"))]
    order = clean + clean[:top]
    chunks = [",".join(order[i:i + CHUNK]) for i in range(0, len(order), CHUNK)]
    query(acct, db, "DELETE FROM frontier")
    for start in range(0, len(chunks), 150):
        values = ",".join(f"({start + k},'{c}')" for k, c in enumerate(chunks[start:start + 150]))
        query(acct, db, f"INSERT INTO frontier(i, tags) VALUES {values}")
    print(f"Edge frontier: {len(clean)} players in {len(chunks)} chunks (one per minute, ~{len(chunks) / 60:.1f} h per cycle)")


def main():
    ap = argparse.ArgumentParser(description="Sync the Cloudflare edge collector with the local database.")
    ap.add_argument("command", choices=["pull", "push-frontier"])
    ap.add_argument("--core", default="work/core.db")
    ap.add_argument("--raw-dir", default="work/raw")
    ap.add_argument("--size", type=int, default=int(os.environ.get("EDGE_FRONTIER_SIZE", 40000)))
    ap.add_argument("--top", type=int, default=10000)
    args = ap.parse_args()
    if not os.environ.get("CLOUDFLARE_API_TOKEN"):
        print("CLOUDFLARE_API_TOKEN not set; edge collector bridge skipped.")
        return
    try:
        pull(args.core, args.raw_dir) if args.command == "pull" else push_frontier(args.core, args.size, args.top)
    except (urllib.error.URLError, OSError, RuntimeError, KeyError, IndexError) as e:
        detail = f"HTTP {e.code} {e.read()[:200]!r}" if isinstance(e, urllib.error.HTTPError) else e
        print(f"::warning::Edge {args.command} failed (non-fatal): {detail}")


if __name__ == "__main__":
    main()
