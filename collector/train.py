import argparse
import glob
import json
import math
import os
import re
import sqlite3
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import calibrate as cal

HIGH_RANK = 16
GROUPS = ("u", "v", "h", "s", "c", "k")


def load_games(shards):
    rows = []
    for path in shards:
        db = sqlite3.connect(path)
        cur = db.execute("""SELECT b.id, b.mode, b.map, b.winner, COALESCE(b.avg_rank, 0), b.battle_time, a.team, a.brawler_id
                            FROM battles b JOIN appearances a ON a.battle_id = b.id
                            WHERE b.winner IN (0, 1) AND b.map IS NOT NULL ORDER BY b.id, a.team, a.slot""")
        game, last = None, None
        for bid, mode, map_, winner, rank, t, team, brawler in cur:
            if bid != last:
                if game and len(game[6]) == 3 and len(game[7]) == 3:
                    rows.append(game)
                game, last = [mode, f"{mode}|{map_}", winner, rank >= HIGH_RANK, t, path, [], []], bid
            game[6 if team == 0 else 7].append(brawler)
        if game and len(game[6]) == 3 and len(game[7]) == 3:
            rows.append(game)
        db.close()
    return rows


class Layout:
    def __init__(self, games, classes=None):
        self.cls_of = classes or {}
        self.classes = sorted(set(self.cls_of.values()))
        self.ci = {c: i for i, c in enumerate(self.classes)}
        self.modes = sorted({g[0] for g in games})
        self.maps = sorted({g[1] for g in games})
        self.brawlers = sorted({b for g in games for b in g[6] + g[7]})
        self.mi = {m: i for i, m in enumerate(self.modes)}
        self.pi = {m: i for i, m in enumerate(self.maps)}
        self.bi = {b: i for i, b in enumerate(self.brawlers)}
        M, P, B = len(self.modes), len(self.maps), len(self.brawlers)
        sizes = {"u": M * B, "v": P * B, "h": M * B, "s": M * B * B, "c": M * B * B, "k": M * max(1, len(self.classes)) * 3}
        self.off, o = {}, 0
        for g in GROUPS:
            self.off[g] = o
            o += sizes[g]
        self.size = o
        self.B = B

    def encode(self, games):
        N, B = len(games), self.B
        mi = np.array([self.mi[g[0]] for g in games])
        pi = np.array([self.pi[g[1]] for g in games])
        high = np.array([1.0 if g[3] else 0.0 for g in games])
        A = np.array([[self.bi[b] for b in g[6]] for g in games])
        Bt = np.array([[self.bi[b] for b in g[7]] for g in games])
        y = np.array([1.0 if g[2] == 0 else 0.0 for g in games])
        idx, sgn = [], []
        for team, sign in ((A, 1.0), (Bt, -1.0)):
            for k in range(3):
                idx += [self.off["u"] + mi * B + team[:, k], self.off["v"] + pi * B + team[:, k], self.off["h"] + mi * B + team[:, k]]
                sgn += [np.full(N, sign), np.full(N, sign), sign * high]
            for i, j in ((0, 1), (0, 2), (1, 2)):
                lo, hi = np.minimum(team[:, i], team[:, j]), np.maximum(team[:, i], team[:, j])
                idx.append(self.off["s"] + mi * B * B + lo * B + hi)
                sgn.append(np.where(lo == hi, 0.0, sign))
        for i in range(3):
            for j in range(3):
                a, b = A[:, i], Bt[:, j]
                lo, hi = np.minimum(a, b), np.maximum(a, b)
                idx.append(self.off["c"] + mi * B * B + lo * B + hi)
                sgn.append(np.where(a == b, 0.0, np.where(a < b, 1.0, -1.0)))
        NC = max(1, len(self.classes))
        for team, sign in ((A, 1.0), (Bt, -1.0)):
            kidx = np.zeros((N, 3), dtype=np.int64)
            ksgn = np.zeros((N, 3))
            if self.classes:
                cls = np.vectorize(lambda b: self.ci.get(self.cls_of.get(self.brawlers[b]), -1))(team)
                for n in range(N):
                    seen = {}
                    slot = 0
                    for c in cls[n]:
                        if c < 0:
                            continue
                        k = seen.get(c, 0)
                        seen[c] = k + 1
                        kidx[n, slot] = self.off["k"] + (mi[n] * NC + c) * 3 + k
                        ksgn[n, slot] = sign
                        slot += 1
            for k in range(3):
                idx.append(kidx[:, k])
                sgn.append(ksgn[:, k])
        return np.stack(idx, 1).astype(np.int32), np.stack(sgn, 1).astype(np.float32), y


def lam_vector(layout, lam):
    v = np.empty(layout.size)
    scale = {"u": 0.3, "v": 1.0, "h": 1.5, "s": 3.0, "c": 3.0, "k": 0.5}
    for g in GROUPS:
        end = layout.off[GROUPS[GROUPS.index(g) + 1]] if g != GROUPS[-1] else layout.size
        v[layout.off[g]:end] = lam * scale[g]
    return v


def fit(layout, idx, sgn, y, lam, iters=400, lr=0.1, val=None):
    w = np.zeros(layout.size)
    m = np.zeros_like(w)
    v2 = np.zeros_like(w)
    L = lam_vector(layout, lam)
    n = len(y)
    best, best_w, best_it = math.inf, w.copy(), 0
    for t in range(1, iters + 1):
        z = (w[idx] * sgn).sum(1)
        p = 1 / (1 + np.exp(-z))
        g = np.bincount(idx.ravel(), weights=((p - y)[:, None] * sgn).ravel(), minlength=layout.size) / n + 2 * L * w
        m = 0.9 * m + 0.1 * g
        v2 = 0.999 * v2 + 0.001 * g * g
        w -= lr * (m / (1 - 0.9 ** t)) / (np.sqrt(v2 / (1 - 0.999 ** t)) + 1e-8)
        if val is not None and t % 10 == 0:
            ll = metrics(w, *val)["log_loss"]
            if ll < best - 1e-5:
                best, best_w, best_it = ll, w.copy(), t
            elif t - best_it >= 60:
                break
    return (best_w, best_it) if val is not None else (w, iters)


def metrics(w, idx, sgn, y):
    p = np.clip(1 / (1 + np.exp(-(w[idx] * sgn).sum(1))), 1e-6, 1 - 1e-6)
    return {"log_loss": round(float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))), 4),
            "accuracy": round(float(np.mean((p >= 0.5) == (y == 1))), 4),
            "brier": round(float(np.mean((p - y) ** 2)), 4)}


def export(layout, w):
    B = layout.B
    out = {"modes": {}, "maps": {}}
    for mode, mi in layout.mi.items():
        u = w[layout.off["u"] + mi * B: layout.off["u"] + (mi + 1) * B]
        h = w[layout.off["h"] + mi * B: layout.off["h"] + (mi + 1) * B]
        S = w[layout.off["s"] + mi * B * B: layout.off["s"] + (mi + 1) * B * B].reshape(B, B)
        C = w[layout.off["c"] + mi * B * B: layout.off["c"] + (mi + 1) * B * B].reshape(B, B)
        out["modes"][mode] = {
            "u": {str(layout.brawlers[i]): round(float(x), 4) for i, x in enumerate(u) if abs(x) >= 0.002},
            "h": {str(layout.brawlers[i]): round(float(x), 4) for i, x in enumerate(h) if abs(x) >= 0.002},
            "syn": [[layout.brawlers[i], layout.brawlers[j], round(float(S[i, j]), 3)]
                    for i, j in zip(*np.nonzero(np.abs(S) >= 0.01)) if i < j],
            "ctr": [[layout.brawlers[i], layout.brawlers[j], round(float(C[i, j]), 3)]
                    for i, j in zip(*np.nonzero(np.abs(C) >= 0.01)) if i < j],
        }
    if layout.classes:
        NC = len(layout.classes)
        out["comp"] = {mode: {c: [round(float(x), 4) for x in w[layout.off["k"] + (mi * NC + ci) * 3: layout.off["k"] + (mi * NC + ci) * 3 + 3]]
                              for c, ci in layout.ci.items()} for mode, mi in layout.mi.items()}
        out["classes"] = {str(b): c for b, c in layout.cls_of.items()}
    for key, pi in layout.pi.items():
        v = w[layout.off["v"] + pi * B: layout.off["v"] + (pi + 1) * B]
        out["maps"][key] = {str(layout.brawlers[i]): round(float(x), 4) for i, x in enumerate(v) if abs(x) >= 0.002}
    return out


def main():
    ap = argparse.ArgumentParser(description="Train the joint draft model on recent games.")
    ap.add_argument("--raw-dir", default="work/raw")
    ap.add_argument("--stats", default="data/stats")
    ap.add_argument("--core", default="work/core.db")
    ap.add_argument("--max-games", type=int, default=int(os.environ.get("TRAIN_MAX_GAMES", 800_000)))
    args = ap.parse_args()
    t0 = time.time()
    shards = sorted(glob.glob(os.path.join(args.raw_dir, "battles-*.db")))
    if len(shards) < 2:
        print("Training skipped: need at least two day shards.")
        return
    games = load_games(shards)[-args.max_games:]
    test_path = shards[-1]
    train = [g for g in games if g[5] != test_path]
    test = [g for g in games if g[5] == test_path]
    if len(train) < 20000 or len(test) < 2000:
        print(f"Training skipped: {len(train)} train / {len(test)} test games (need 20000 / 2000).")
        return
    train.sort(key=lambda g: g[4])
    cut = int(len(train) * 0.85)
    info_path = os.path.join(args.stats, "brawler_info.json")
    classes = {}
    if os.path.exists(info_path):
        classes = {int(b): v["class"] for b, v in json.load(open(info_path))["brawlers"].items() if v.get("class")}
    layout = Layout(games, classes)
    tune = train[max(0, cut - 400_000):cut]
    te = layout.encode(test)
    tr = va = None
    prev_path = os.path.join(args.stats, "learned.json")
    prev = json.load(open(prev_path)) if os.path.exists(prev_path) else {}
    tuned_at = prev.get("tuned_at", 0)
    if prev.get("lambda") and prev.get("iterations") and time.time() - tuned_at < 20 * 3600:
        lam, it = prev["lambda"], prev["iterations"]
        print(f"  reusing regularisation from {time.strftime('%H:%M', time.gmtime(tuned_at))} UTC: lambda {lam:g}, {it} iterations")
    else:
        tr, va = layout.encode(tune), layout.encode(train[cut:])
        best = None
        for lam in (1e-4, 3e-4, 1e-3):
            w, it = fit(layout, *tr, lam=lam, val=va)
            ll = metrics(w, *va)["log_loss"]
            print(f"  lambda {lam:g}: validation log loss {ll} (best at iteration {it})")
            if best is None or ll < best[0]:
                best = (ll, lam, it)
        _, lam, it = best
        tuned_at = time.time()
    tr_all = layout.encode(train)
    w, _ = fit(layout, *tr_all, lam=lam, iters=max(it, 20))
    test_m = metrics(w, *te)
    calib_path = os.path.join(args.stats, "model.json")
    calib = json.load(open(calib_path)) if os.path.exists(calib_path) else None
    baseline = calib["calibrated"] if calib else None
    test_day = re.search(r"battles-(\d{4}-\d{2}-\d{2})", test_path).group(1)
    blend, ens = 1.0, None
    chosen = baseline is None or test_m["log_loss"] < baseline["log_loss"] - 0.0005
    if calib and os.path.exists(args.core):
        core = sqlite3.connect(args.core)
        since = core.execute("SELECT date(?, '-28 days')", (test_day,)).fetchone()[0]
        eff, syn_of, ctr_of = cal.build_signals(core, test_day, since)
        core.close()
        cw = calib["weights"]
        zb = []
        for g in test:
            mode, map_ = g[0], g[1].split("|", 1)[1]
            a, b = g[6], g[7]
            x = cw["map"] * (sum(eff(mode, map_, i) for i in a) - sum(eff(mode, map_, i) for i in b))
            x += cw["syn"] * (sum(syn_of(mode, a[i], a[j]) for i in range(3) for j in range(i + 1, 3))
                              - sum(syn_of(mode, b[i], b[j]) for i in range(3) for j in range(i + 1, 3)))
            x += cw["ctr"] * sum(ctr_of(mode, i, j) for i in a for j in b)
            zb.append(x)
        zb = np.array(zb)
        zl = (w[te[0]] * te[1]).sum(1)
        y = te[2]
        half = len(y) // 2

        def ll(z, sl):
            p = np.clip(1 / (1 + np.exp(-z[sl])), 1e-6, 1 - 1e-6)
            return float(-np.mean(y[sl] * np.log(p) + (1 - y[sl]) * np.log(1 - p)))

        def acc(z, sl):
            return float(np.mean((z[sl] >= 0) == (y[sl] == 1)))
        A, B = slice(0, half), slice(half, None)
        blend = min((round(a, 2) for a in np.linspace(0, 1, 11)), key=lambda a: ll(a * zl + (1 - a) * zb, A))
        zbl = blend * zl + (1 - blend) * zb
        ens = {"alpha": blend, "judged_games": len(y) - half,
               "smoothed": {"log_loss": round(ll(zb, B), 4), "accuracy": round(acc(zb, B), 4)},
               "trained": {"log_loss": round(ll(zl, B), 4), "accuracy": round(acc(zl, B), 4)},
               "blend": {"log_loss": round(ll(zbl, B), 4), "accuracy": round(acc(zbl, B), 4)}}
        chosen = blend > 0 and ens["blend"]["log_loss"] < ens["smoothed"]["log_loss"] - 0.0003
        print(f"  blend {blend:.2f} trained + {1 - blend:.2f} smoothed on {len(y) - half} games: log loss {ens['blend']['log_loss']} "
              f"(smoothed {ens['smoothed']['log_loss']}, trained {ens['trained']['log_loss']})")
    w_final, _ = fit(layout, *layout.encode(games), lam=lam, iters=max(it, 20))
    out = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "chosen": chosen, "blend": blend, "ensemble": ens,
           "test_day": test_day,
           "train_games": len(train), "test_games": len(test), "lambda": lam, "iterations": it, "tuned_at": int(tuned_at),
           "test": test_m, "baseline_calibrated": baseline, **export(layout, w_final)}
    os.makedirs(args.stats, exist_ok=True)
    with open(os.path.join(args.stats, "learned.json"), "w") as f:
        json.dump(out, f, separators=(",", ":"))
    verdict = (f"USING {'trained model' if blend == 1 else f'blend ({blend:.0%} trained)'}" if chosen
               else "keeping smoothed model (trained/blended model did not beat it)")
    print(f"Trained on {len(train)} games, tested on {len(test)} unseen: log loss {test_m['log_loss']} "
          f"accuracy {test_m['accuracy']:.1%} vs smoothed {baseline and baseline['log_loss']} -> {verdict} ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    sys.exit(main())
