import argparse
import glob
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbm

HIGH_RANK = 16
PAIR_KEEP_DAYS = 45
INACTIVE_DAYS = 45
FIELDS = ["games", "wins", "losses", "draws"]


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else None


def dump(out_dir, name, obj):
    path = os.path.join(out_dir, name)
    with open(path + ".tmp", "w") as f:
        json.dump(obj, f, separators=(",", ":"), sort_keys=True)
    os.replace(path + ".tmp", path)


def wl(games, wins, draws):
    return [games, wins, games - wins - draws, draws]


def aggregate_day(core, shard_path, day):
    core.execute("ATTACH DATABASE ? AS s", (shard_path,))
    for table in ("agg_battles", "agg_brawler", "agg_pair", "agg_rank", "agg_build", "agg_gear_pair", "agg_loadout_games"):
        core.execute(f"DELETE FROM {table} WHERE day = ?", (day,))
    core.execute(f"""INSERT INTO agg_rank SELECT :day, CAST(avg_rank AS INTEGER), COUNT(*) FROM s.battles
        WHERE avg_rank IS NOT NULL GROUP BY 2""", {"day": day})
    p = {"day": day, "high": HIGH_RANK}
    ok = "b.winner IS NOT NULL"
    core.execute(f"""INSERT INTO agg_battles
        SELECT :day, b.mode, b.map, COALESCE(b.avg_rank >= :high, 0), COUNT(*), MAX(b.battle_time)
        FROM s.battles b WHERE {ok} GROUP BY 2, 3, 4""", p)
    core.execute("""INSERT INTO map_info SELECT mode, map, MAX(event_id), MAX(battle_time) FROM s.battles
        WHERE map IS NOT NULL GROUP BY 1, 2
        ON CONFLICT(mode, map) DO UPDATE SET event_id = excluded.event_id, last_time = excluded.last_time
        WHERE excluded.last_time >= map_info.last_time""")
    core.execute(f"""INSERT INTO agg_brawler
        SELECT :day, b.mode, b.map, COALESCE(b.avg_rank >= :high, 0), a.brawler_id,
               COUNT(*), SUM(a.team = b.winner), SUM(b.winner = -1)
        FROM s.appearances a JOIN s.battles b ON b.id = a.battle_id WHERE {ok} GROUP BY 2, 3, 4, 5""", p)
    core.execute(f"""INSERT INTO agg_pair
        SELECT :day, b.mode, COALESCE(b.avg_rank >= :high, 0), a1.brawler_id, a2.brawler_id, a1.team = a2.team,
               COUNT(*), SUM(a1.team = b.winner), SUM(a2.team = b.winner)
        FROM s.battles b JOIN s.appearances a1 ON a1.battle_id = b.id
        JOIN s.appearances a2 ON a2.battle_id = b.id AND a2.brawler_id > a1.brawler_id
        WHERE {ok} GROUP BY 2, 3, 4, 5, 6""", p)

    build, gear_pairs, games = defaultdict(lambda: [0, 0, 0]), defaultdict(lambda: [0, 0, 0]), defaultdict(lambda: [0, 0, 0])

    def add(arr, won, draw):
        arr[0] += 1
        arr[1] += won
        arr[2] += draw
    for bid, won, draw, gadgets, sps, gears, hcs in core.execute(f"""
            SELECT a.brawler_id, a.team = b.winner, b.winner = -1, l.gadgets, l.star_powers, l.gears, l.hypercharges
            FROM s.appearances a JOIN s.battles b ON b.id = a.battle_id
            JOIN loadouts l ON l.player_tag = a.player_tag AND l.brawler_id = a.brawler_id WHERE {ok}"""):
        add(games[bid], won, draw)
        for kind, csv, limit in (("gadget", gadgets, 1), ("star_power", sps, 1), ("gear", gears, 2),
                                 ("hypercharge", hcs, 1)):
            items = csv.split(",") if csv else []
            for item in items:
                add(build[(bid, kind, int(item), 0)], won, draw)
                if len(items) <= limit:
                    add(build[(bid, kind, int(item), 1)], won, draw)
        gear_items = gears.split(",") if gears else []
        if len(gear_items) == 2:
            add(gear_pairs[(bid, "+".join(gear_items))], won, draw)
    core.executemany("INSERT INTO agg_build VALUES(?,?,?,?,?,?,?,?)",
                     [(day, *k, *v) for k, v in build.items()])
    core.executemany("INSERT INTO agg_gear_pair VALUES(?,?,?,?,?,?)", [(day, *k, *v) for k, v in gear_pairs.items()])
    core.executemany("INSERT INTO agg_loadout_games VALUES(?,?,?,?,?)", [(day, k, *v) for k, v in games.items()])
    core.commit()
    core.execute("DETACH DATABASE s")


def maintain(core, newest_day):
    cutoff = (date.fromisoformat(newest_day) - timedelta(days=PAIR_KEEP_DAYS)).isoformat()
    for table in ("agg_pair", "agg_build", "agg_gear_pair", "agg_loadout_games"):
        core.execute(f"DELETE FROM {table} WHERE day < ?", (cutoff,))
    inactive = int(time.time()) - INACTIVE_DAYS * 86400
    gone = core.execute("DELETE FROM players WHERE last_battle_time < ? AND next_due IS NOT NULL", (inactive,)).rowcount
    core.execute("DELETE FROM loadouts WHERE player_tag NOT IN (SELECT tag FROM players)")
    core.execute("DELETE FROM pending_loadouts WHERE player_tag NOT IN (SELECT tag FROM players)")
    core.commit()
    return gone


def export(core, out_dir, window_days, min_pair_games):
    os.makedirs(out_dir, exist_ok=True)
    newest = core.execute("SELECT MAX(day) FROM agg_battles").fetchone()[0] or date.today().isoformat()
    since = (date.fromisoformat(newest) - timedelta(days=window_days - 1)).isoformat()
    p = {"since": since, "min": min_pair_games}
    catalog = {bid: json.loads(data) for bid, data in core.execute("SELECT id, data FROM brawlers")}
    names = {bid: b["name"] for bid, b in catalog.items()}
    rank_names = json.loads(dbm.get_meta(core, "rank_names", "{}"))

    maps = {}
    for mode, map_, n, n_high, last in core.execute(
            "SELECT mode, map, SUM(battles), SUM(battles * high), MAX(last_time) FROM agg_battles "
            "WHERE day >= :since GROUP BY 1, 2", p):
        maps[(mode, map_)] = {"mode": mode, "map": map_, "battles": n, "battles_high": n_high,
                              "last_seen": iso(last), "brawlers": {}, "brawlers_high": {}}
    for mode, map_, bid, g, w, d, gh, wh, dh in core.execute(
            "SELECT mode, map, brawler_id, SUM(games), SUM(wins), SUM(draws), SUM(games * high), SUM(wins * high), "
            "SUM(draws * high) FROM agg_brawler WHERE day >= :since GROUP BY 1, 2, 3", p):
        m = maps[(mode, map_)]
        m["brawlers"][bid] = wl(g, w, d)
        if gh:
            m["brawlers_high"][bid] = wl(gh, wh, dh)
    for mode, map_, event_id in core.execute("SELECT mode, map, event_id FROM map_info"):
        if (mode, map_) in maps:
            maps[(mode, map_)]["event_id"] = event_id
    map_list = sorted(maps.values(), key=lambda m: (m["mode"] or "", m["map"] or ""))
    newest_ts = max((m["last_seen"] for m in map_list if m["last_seen"]), default=None)
    pool_since = iso(int(datetime.strptime(newest_ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
                     - 3 * 86400) if newest_ts else None
    dump(out_dir, "maps.json", {
        "window_days": window_days, "high_rank_min": HIGH_RANK, "fields": FIELDS,
        "current_pool": [[m["mode"], m["map"]] for m in map_list if pool_since and m["last_seen"] >= pool_since],
        "maps": map_list})

    brawlers = defaultdict(lambda: {"all": [0, 0, 0, 0], "modes": {}, "modes_high": {}})
    for mode, bid, g, w, d, gh, wh, dh in core.execute(
            "SELECT mode, brawler_id, SUM(games), SUM(wins), SUM(draws), SUM(games * high), SUM(wins * high), "
            "SUM(draws * high) FROM agg_brawler WHERE day >= :since GROUP BY 1, 2", p):
        e = brawlers[bid]
        e["name"] = names.get(bid)
        e["modes"][mode] = wl(g, w, d)
        if gh:
            e["modes_high"][mode] = wl(gh, wh, dh)
        e["all"] = [x + y for x, y in zip(e["all"], wl(g, w, d))]
    total = sum(m["battles"] for m in map_list)
    dump(out_dir, "brawlers.json", {"window_days": window_days, "battles": total, "fields": FIELDS,
                                    "brawlers": brawlers})

    synergy, matchups = defaultdict(list), defaultdict(list)
    for mode, a, b, same, g, wa, wb, gh, wah, wbh in core.execute(
            "SELECT mode, a, b, same, SUM(games), SUM(wins_a), SUM(wins_b), SUM(games * high), SUM(wins_a * high), "
            "SUM(wins_b * high) FROM agg_pair WHERE day >= :since GROUP BY 1, 2, 3, 4 HAVING SUM(games) >= :min", p):
        (synergy[mode].append([a, b, g, wa, gh, wah]) if same else matchups[mode].append([a, b, g, wa, wb, gh, wah, wbh]))
    dump(out_dir, "synergy.json", {"window_days": window_days, "min_games": min_pair_games, "high_rank_min": HIGH_RANK,
                                   "fields": ["brawler_a", "brawler_b", "games_together", "wins", "games_high", "wins_high"],
                                   "modes": synergy})
    dump(out_dir, "matchups.json", {"window_days": window_days, "min_games": min_pair_games, "high_rank_min": HIGH_RANK,
                                    "fields": ["brawler_a", "brawler_b", "games", "wins_a", "wins_b",
                                               "games_high", "wins_a_high", "wins_b_high"], "modes": matchups})

    builds = {}
    for bid, g, w, d in core.execute("SELECT brawler_id, SUM(games), SUM(wins), SUM(draws) FROM agg_loadout_games "
                                     "WHERE day >= :since GROUP BY 1", p):
        builds[bid] = {"name": names.get(bid), "games": wl(g, w, d), "gadget": {}, "star_power": {}, "gear": {},
                       "hypercharge": {}, "gear_pairs": {}}
    for bid, kind, item, certain, g, w, d in core.execute(
            "SELECT brawler_id, kind, item, certain, SUM(games), SUM(wins), SUM(draws) FROM agg_build "
            "WHERE day >= :since GROUP BY 1, 2, 3, 4", p):
        if bid in builds:
            builds[bid][kind].setdefault(item, [[0, 0, 0, 0], [0, 0, 0, 0]])[certain] = wl(g, w, d)
    for bid, pair, g, w, d in core.execute("SELECT brawler_id, pair, SUM(games), SUM(wins), SUM(draws) "
                                           "FROM agg_gear_pair WHERE day >= :since GROUP BY 1, 2", p):
        if bid in builds:
            builds[bid]["gear_pairs"][pair] = wl(g, w, d)
    item_names = {it["id"]: it["name"] for b in catalog.values()
                  for key in ("gadgets", "starPowers", "gears", "hyperCharges") for it in b.get(key) or []}
    dump(out_dir, "builds.json", {
        "window_days": window_days, "fields": FIELDS,
        "note": "The API exposes owned items only. Each item maps to [owned, certainly_equipped]; "
                "certainly_equipped counts games where the player owned only that option (<= 2 gears).",
        "item_names": item_names, "brawlers": builds})

    q = lambda sql, *a: core.execute(sql, a).fetchone()[0]
    dump(out_dir, "summary.json", {
        "generated_at": iso(int(time.time())),
        "window_days": window_days, "window_start_day": since, "newest_day": newest,
        "battles_in_window": total,
        "battles_total": q("SELECT COALESCE(SUM(battles), 0) FROM agg_battles"),
        "battles_per_day": dict(core.execute("SELECT day, SUM(battles) FROM agg_battles WHERE day >= ? GROUP BY 1",
                                             (since,)).fetchall()),
        "first_day": q("SELECT MIN(day) FROM agg_battles"),
        "players_known": q("SELECT COUNT(*) FROM players"),
        "players_crawled": q("SELECT COUNT(*) FROM players WHERE last_crawled IS NOT NULL"),
        "players_with_profile": q("SELECT COUNT(*) FROM players WHERE profile_at IS NOT NULL"),
        "loadouts": q("SELECT COUNT(*) FROM loadouts"),
        "rank_names": rank_names, "high_rank_min": HIGH_RANK,
        "rank_distribution": dict(core.execute("SELECT rank, SUM(battles) FROM agg_rank WHERE day >= ? GROUP BY 1",
                                               (since,)).fetchall()),
        "brawler_names": names,
        "lifetime": json.loads(dbm.get_meta(core, "lifetime", "{}")),
        "last_run": json.loads(dbm.get_meta(core, "last_run", "{}")),
        "edge": json.loads(dbm.get_meta(core, "edge_pull", "{}")),
    })


def main():
    ap = argparse.ArgumentParser(description="Aggregate day shards and export the site statistics.")
    ap.add_argument("--core", default="work/core.db")
    ap.add_argument("--raw-dir", default="work/raw")
    ap.add_argument("--out", default="data/stats")
    ap.add_argument("--window-days", type=int, default=int(os.environ.get("STATS_WINDOW_DAYS", 28)))
    ap.add_argument("--min-pair-games", type=int, default=int(os.environ.get("STATS_MIN_PAIR_GAMES", 10)))
    args = ap.parse_args()
    core = dbm.connect(args.core)
    days = []
    for path in sorted(glob.glob(os.path.join(args.raw_dir, "battles-*.db"))):
        day = re.search(r"battles-(\d{4}-\d{2}-\d{2})\.db$", path).group(1)
        days.append(day)
        stamp = f"{os.path.getsize(path)}:{int(os.path.getmtime(path))}"
        if dbm.get_meta(core, f"agg_stamp:{day}") == stamp:
            continue
        aggregate_day(core, path, day)
        dbm.set_meta(core, f"agg_stamp:{day}", stamp)
        core.commit()
    gone = maintain(core, max(days) if days else date.today().isoformat())
    export(core, args.out, args.window_days, args.min_pair_games)
    dbm.close(core)
    print(f"Checked {len(days)} day shard(s), dropped {gone} inactive players, stats written to {args.out}")


if __name__ == "__main__":
    main()
