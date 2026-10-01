import argparse
import glob
import json
import math
import os
import re
import sqlite3
import sys
import time
from collections import defaultdict

K_MODE, K_MAP, K_PAIR = 120, 50, 30
MIN_TEST_GAMES = 1500


def logit(p):
    return math.log(p / (1 - p))


def sig(x):
    return 1 / (1 + math.exp(-x))


def build_signals(core, before_day, since_day):
    p = {"b": before_day, "s": since_day}
    mode_wl = defaultdict(lambda: [0, 0])
    map_wl = defaultdict(lambda: [0, 0])
    for mode, map_, bid, g, w, d in core.execute(
            "SELECT mode, map, brawler_id, SUM(games), SUM(wins), SUM(draws) FROM agg_brawler "
            "WHERE day < :b AND day >= :s GROUP BY 1, 2, 3", p):
        l = g - w - d
        mode_wl[(mode, bid)][0] += w
        mode_wl[(mode, bid)][1] += l
        map_wl[(mode, map_, bid)] = [w, l]
    p_mode = {k: (w + K_MODE * 0.5) / (w + l + K_MODE) for k, (w, l) in mode_wl.items()}

    def pm(mode, bid):
        return p_mode.get((mode, bid), 0.5)

    def eff(mode, map_, bid):
        w, l = map_wl.get((mode, map_, bid), (0, 0))
        prior = pm(mode, bid)
        return logit((w + K_MAP * prior) / (w + l + K_MAP))

    syn, ctr = {}, {}
    for mode, a, b, same, g, wa, wb in core.execute(
            "SELECT mode, a, b, same, SUM(games), SUM(wins_a), SUM(wins_b) FROM agg_pair "
            "WHERE day < :b AND day >= :s GROUP BY 1, 2, 3, 4", p):
        if same:
            exp = logit(pm(mode, a)) + logit(pm(mode, b))
            syn[(mode, a, b)] = logit((wa + K_PAIR * sig(exp)) / (g + K_PAIR)) - exp
        else:
            exp = logit(pm(mode, a)) - logit(pm(mode, b))
            n = wa + wb
            ctr[(mode, a, b)] = logit((wa + K_PAIR * sig(exp)) / (n + K_PAIR)) - exp

    def syn_of(mode, a, b):
        return syn.get((mode, min(a, b), max(a, b)), 0.0)

    def ctr_of(mode, a, b):
        if a < b:
            return ctr.get((mode, a, b), 0.0)
        return -ctr.get((mode, b, a), 0.0)
    return eff, syn_of, ctr_of


def features(shard_path, eff, syn_of, ctr_of):
    db = sqlite3.connect(shard_path)
    teams = defaultdict(lambda: ([], []))
    meta = {}
    for bid, mode, map_, winner in db.execute("SELECT id, mode, map, winner FROM battles WHERE winner IN (0, 1)"):
        meta[bid] = (mode, map_, winner)
    for bid, team, brawler in db.execute("SELECT battle_id, team, brawler_id FROM appearances"):
        if bid in meta:
            teams[bid][team].append(brawler)
    db.close()
    X, Y = [], []
    for bid, (mode, map_, winner) in meta.items():
        a, b = teams[bid]
        if len(a) != 3 or len(b) != 3:
            continue
        x_map = sum(eff(mode, map_, i) for i in a) - sum(eff(mode, map_, i) for i in b)
        x_syn = sum(syn_of(mode, a[i], a[j]) for i in range(3) for j in range(i + 1, 3)) \
            - sum(syn_of(mode, b[i], b[j]) for i in range(3) for j in range(i + 1, 3))
        x_ctr = sum(ctr_of(mode, i, j) for i in a for j in b)
        X.append((x_map, x_syn, x_ctr))
        Y.append(1 if winner == 0 else 0)
    return X, Y


def fit(X, Y, l2=1.0, iters=30):
    w = [1.0, 1.0, 1.0]
    for _ in range(iters):
        g = [l2 * (w[k] - 1) for k in range(3)]
        H = [[l2 if r == c else 0.0 for c in range(3)] for r in range(3)]
        for x, y in zip(X, Y):
            p = sig(sum(w[k] * x[k] for k in range(3)))
            for r in range(3):
                g[r] += (p - y) * x[r]
                for c in range(3):
                    H[r][c] += p * (1 - p) * x[r] * x[c]
        step = solve3(H, g)
        w = [w[k] - step[k] for k in range(3)]
        if max(abs(s) for s in step) < 1e-6:
            break
    return w


def solve3(A, b):
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for i in range(3):
        piv = max(range(i, 3), key=lambda r: abs(M[r][i]))
        M[i], M[piv] = M[piv], M[i]
        if abs(M[i][i]) < 1e-12:
            return [0.0, 0.0, 0.0]
        for r in range(3):
            if r != i:
                f = M[r][i] / M[i][i]
                M[r] = [M[r][c] - f * M[i][c] for c in range(4)]
    return [M[i][3] / M[i][i] for i in range(3)]


def metrics(X, Y, w):
    ll = acc = brier = 0.0
    for x, y in zip(X, Y):
        p = min(max(sig(sum(w[k] * x[k] for k in range(3))), 1e-6), 1 - 1e-6)
        ll -= y * math.log(p) + (1 - y) * math.log(1 - p)
        acc += (p >= 0.5) == (y == 1)
        brier += (p - y) ** 2
    n = len(Y)
    return {"log_loss": round(ll / n, 4), "accuracy": round(acc / n, 4), "brier": round(brier / n, 4)}


def main():
    ap = argparse.ArgumentParser(description="Fit the draft model weights on the newest day of games.")
    ap.add_argument("--core", default="work/core.db")
    ap.add_argument("--raw-dir", default="work/raw")
    ap.add_argument("--out", default="data/stats/model.json")
    ap.add_argument("--window-days", type=int, default=28)
    args = ap.parse_args()
    shards = sorted(glob.glob(os.path.join(args.raw_dir, "battles-*.db")))
    if not shards:
        print("No day shards; skipping calibration.")
        return
    test = shards[-1]
    day = re.search(r"battles-(\d{4}-\d{2}-\d{2})\.db$", test).group(1)
    core = sqlite3.connect(args.core)
    since = core.execute("SELECT date(?, ?)", (day, f"-{args.window_days} days")).fetchone()[0]
    train_games = core.execute("SELECT COALESCE(SUM(battles), 0) FROM agg_battles WHERE day < ? AND day >= ?", (day, since)).fetchone()[0]
    eff, syn_of, ctr_of = build_signals(core, day, since)
    core.close()
    X, Y = features(test, eff, syn_of, ctr_of)
    if len(Y) > 80000:
        X, Y = X[:80000], Y[:80000]
    if len(Y) < MIN_TEST_GAMES or train_games < 5000:
        print(f"Calibration skipped: {len(Y)} test games, {train_games} training games (need {MIN_TEST_GAMES} / 5000).")
        return
    w = fit(X, Y)
    w = [max(0.05, min(2.0, x)) for x in w]
    out = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "weights": {"map": round(w[0], 4), "syn": round(w[1], 4), "ctr": round(w[2], 4)},
        "test_day": day, "test_games": len(Y), "train_games": train_games,
        "calibrated": metrics(X, Y, w), "uncalibrated": metrics(X, Y, [1.0, 1.0, 1.0]),
        "coin_flip": {"log_loss": round(math.log(2), 4), "accuracy": 0.5, "brier": 0.25},
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(out, f, indent=1)
    print(f"Calibrated on {len(Y)} unseen games: weights {out['weights']}, "
          f"accuracy {out['calibrated']['accuracy']:.1%}, log loss {out['calibrated']['log_loss']} "
          f"(uncalibrated {out['uncalibrated']['log_loss']}, coin flip {out['coin_flip']['log_loss']})")


if __name__ == "__main__":
    sys.exit(main())
