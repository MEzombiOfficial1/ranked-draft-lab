import os
import sqlite3

CORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS brawlers(id INTEGER PRIMARY KEY, name TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS players(
  tag TEXT PRIMARY KEY, name TEXT, source TEXT,
  first_seen INTEGER, last_crawled INTEGER, next_due INTEGER, last_battle_time INTEGER,
  crawls INTEGER NOT NULL DEFAULT 0, ranked_found INTEGER NOT NULL DEFAULT 0,
  needs_profile INTEGER NOT NULL DEFAULT 0, profile_at INTEGER,
  trophies INTEGER, ranked_rank INTEGER, ranked_elo INTEGER, highest_ranked_elo INTEGER, last_rank INTEGER);
CREATE INDEX IF NOT EXISTS players_due ON players(next_due);
CREATE INDEX IF NOT EXISTS players_profile ON players(needs_profile) WHERE needs_profile = 1;
-- Owned build items (comma-separated ids) for brawlers a player has used in ranked.
CREATE TABLE IF NOT EXISTS loadouts(
  player_tag TEXT NOT NULL, brawler_id INTEGER NOT NULL, power INTEGER,
  gadgets TEXT, star_powers TEXT, gears TEXT, hypercharges TEXT, buffies TEXT, fetched_at INTEGER,
  PRIMARY KEY(player_tag, brawler_id)) WITHOUT ROWID;
-- Brawlers seen in ranked whose loadout we do not know yet (resolved by a profile fetch).
CREATE TABLE IF NOT EXISTS pending_loadouts(
  player_tag TEXT NOT NULL, brawler_id INTEGER NOT NULL, PRIMARY KEY(player_tag, brawler_id)) WITHOUT ROWID;
-- Daily aggregates (day = 'YYYY-MM-DD' UTC; high = average rank in the game >= Legendary I).
CREATE TABLE IF NOT EXISTS agg_battles(
  day TEXT, mode TEXT, map TEXT, high INTEGER, battles INTEGER, last_time INTEGER,
  PRIMARY KEY(day, mode, map, high)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS agg_brawler(
  day TEXT, mode TEXT, map TEXT, high INTEGER, brawler_id INTEGER, games INTEGER, wins INTEGER, draws INTEGER,
  PRIMARY KEY(day, mode, map, high, brawler_id)) WITHOUT ROWID;
-- same = 1: a and b on the same team; 0: opposite teams. a < b. high as in agg_brawler.
CREATE TABLE IF NOT EXISTS agg_pair(
  day TEXT, mode TEXT, high INTEGER, a INTEGER, b INTEGER, same INTEGER, games INTEGER, wins_a INTEGER, wins_b INTEGER,
  PRIMARY KEY(day, mode, high, a, b, same)) WITHOUT ROWID;
-- Games per day by average rank tier (rounded down).
CREATE TABLE IF NOT EXISTS agg_rank(day TEXT, rank INTEGER, battles INTEGER, PRIMARY KEY(day, rank)) WITHOUT ROWID;
-- kind: gadget / star_power / gear / hypercharge; certain = 1 when the item was surely equipped.
CREATE TABLE IF NOT EXISTS agg_build(
  day TEXT, brawler_id INTEGER, kind TEXT, item INTEGER, certain INTEGER, games INTEGER, wins INTEGER, draws INTEGER,
  PRIMARY KEY(day, brawler_id, kind, item, certain)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS agg_gear_pair(
  day TEXT, brawler_id INTEGER, pair TEXT, games INTEGER, wins INTEGER, draws INTEGER,
  PRIMARY KEY(day, brawler_id, pair)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS map_info(
  mode TEXT, map TEXT, event_id INTEGER, last_time INTEGER, PRIMARY KEY(mode, map)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS agg_loadout_games(
  day TEXT, brawler_id INTEGER, games INTEGER, wins INTEGER, draws INTEGER,
  PRIMARY KEY(day, brawler_id)) WITHOUT ROWID;
"""

SHARD_SCHEMA = """
-- One row per ranked game. winner: 0/1 = index of the winning team, -1 = draw, NULL = unknown.
CREATE TABLE IF NOT EXISTS battles(
  id INTEGER PRIMARY KEY,
  bkey INTEGER NOT NULL UNIQUE,
  battle_time INTEGER NOT NULL,
  event_id INTEGER, mode TEXT, map TEXT, type TEXT,
  duration INTEGER, winner INTEGER, avg_rank REAL,
  star_player TEXT, collected_at INTEGER);
-- One row per player per game. rank = the player's ranked tier in that game.
CREATE TABLE IF NOT EXISTS appearances(
  battle_id INTEGER NOT NULL, team INTEGER NOT NULL, slot INTEGER NOT NULL,
  player_tag TEXT, brawler_id INTEGER, power INTEGER, rank INTEGER,
  PRIMARY KEY(battle_id, team, slot)) WITHOUT ROWID;
"""


def _open(path, schema):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.executescript(schema)
    return db


MIGRATIONS = [
    ("agg_pair", "high", "DROP TABLE agg_pair"),
    ("players", "last_rank", "ALTER TABLE players ADD COLUMN last_rank INTEGER"),
]


def connect(path):
    exists = os.path.exists(path)
    db = _open(path, "PRAGMA foreign_keys = OFF;") if exists else None
    if db:
        for table, column, sql in MIGRATIONS:
            cols = [r[1] for r in db.execute(f"PRAGMA table_info({table})")]
            if cols and column not in cols:
                db.execute(sql)
        db.commit()
        db.close()
    return _open(path, CORE_SCHEMA)


def connect_shard(path):
    return _open(path, SHARD_SCHEMA)


def close(db):
    db.commit()
    db.execute("PRAGMA optimize")
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.execute("PRAGMA journal_mode=DELETE")
    db.close()


def get_meta(db, key, default=None):
    row = db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(db, key, value):
    db.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
               (key, str(value)))
