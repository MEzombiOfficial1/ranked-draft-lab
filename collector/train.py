import argparse
import glob
import json
import os
import re
import sqlite3
import sys
import time

import numpy as np
from scipy.optimize import minimize

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import calibrate as cal

HIGH_RANK = 16
CHUNK = 400_000
SCALE = {"u": 0.3, "v": 1.0, "h": 1.5, "vh": 2.0, "s": 3.0, "c": 3.0, "k": 0.5}
LAMBDAS = (3e-6, 1e-5)


def load_games(shards, max_games):
    modes, maps, parts, total = {}, {}, [], 0
    for path in sorted(shards, reverse=True):
        db = sqlite3.connect(path)
        rows = db.execute("SELECT id, mode, map, winner, COALESCE(avg_rank, 0), battle_time FROM battles "
                          "WHERE winner IN (0, 1) AND map IS NOT NULL ORDER BY id").fetchall()
        n = len(rows)
        ids = np.fromiter((r[0] for r in rows), np.int64, n)
        mo = np.fromiter((modes.setdefault(r[1], len(modes)) for r in rows), np.int16, n)
        mp = np.fromiter((maps.setdefault(f"{r[1]}|{r[2]}", len(maps)) for r in rows), np.int16, n)
        win = np.fromiter((r[3] for r in rows), np.int8, n)
        high = np.fromiter((r[4] >= HIGH_RANK for r in rows), np.bool_, n)
        tm = np.fromiter((r[5] for r in rows), np.int64, n)
        del rows
        team = np.full((n, 6), -1, np.int64)
        cur = db.execute("SELECT battle_id, team * 3 + slot, brawler_id FROM appearances WHERE slot < 3")
        while True:
            block = cur.fetchmany(1_000_000)
            if not block:
                break
            app = np.array(block, dtype=np.int64)
            pos = np.minimum(np.searchsorted(ids, app[:, 0]), max(n - 1, 0))
            ok = (ids[pos] == app[:, 0]) & (app[:, 1] < 6) if n else np.zeros(len(app), bool)
            team[pos[ok], app[ok, 1]] = app[ok, 2]
        db.close()
        full = (team >= 0).all(1)
        parts.append((mo[full], mp[full], win[full], high[full], tm[full], team[full], np.full(int(full.sum()), path)))
        total += int(full.sum())
        if total >= max_games:
            break
    cols = [np.concatenate([p[i] for p in parts]) for i in range(7)]
    order = np.argsort(cols[4], kind="stable")[-max_games:]
    return modes, maps, [c[order] for c in cols]


class Layout:
    def __init__(self, n_modes, n_maps, brawlers, classes):
        self.brawlers = brawlers
        self.B = len(brawlers)
        self.bi = np.full(int(brawlers.max()) + 1, -1, np.int64)
        self.bi[brawlers] = np.arange(self.B)
        self.classes = sorted(set(classes.values()))
        ci = {c: i for i, c in enumerate(self.classes)}
        self.cls = np.array([ci.get(classes.get(int(b)), -1) for b in brawlers], np.int64)
        self.NC = max(1, len(self.classes))
        M, P, B = n_modes, n_maps, self.B
        self.off, o = {}, 0
        for g, n in (("u", M * B), ("v", P * B), ("h", M * B), ("vh", P * B), ("s", M * B * B), ("c", M * B * B), ("k", M * self.NC * 3)):
            self.off[g] = (o, o + n)
            o += n
        self.size = o

    def lam(self, lam):
        v = np.empty(self.size)
        for g, (a, b) in self.off.items():
            v[a:b] = lam * SCALE[g]
        return v

    def encode(self, mo, mp, high, team):
        B, N = self.B, len(mo)
        mo, mp, hi = mo.astype(np.int64), mp.astype(np.int64), high.astype(np.float64)
        A, Bt = self.bi[team[:, :3]], self.bi[team[:, 3:]]
        o = {g: a for g, (a, _) in self.off.items()}
        idx, sgn = [], []
        one = np.ones(N)
        for T, s in ((A, 1.0), (Bt, -1.0)):
            for k in range(3):
                b = T[:, k]
                idx += [o["u"] + mo * B + b, o["v"] + mp * B + b, o["h"] + mo * B + b, o["vh"] + mp * B + b]
                sgn += [s * one, s * one, s * hi, s * hi]
            for i, j in ((0, 1), (0, 2), (1, 2)):
                lo, up = np.minimum(T[:, i], T[:, j]), np.maximum(T[:, i], T[:, j])
                idx.append(o["s"] + mo * B * B + lo * B + up)
                sgn.append(np.where(lo == up, 0.0, s))
            C = self.cls[T]
            for k in range(3):
                nth = sum((C[:, k] == C[:, q]).astype(np.int64) for q in range(k)) if k else np.zeros(N, np.int64)
                idx.append(o["k"] + (mo * self.NC + np.maximum(C[:, k], 0)) * 3 + np.minimum(nth, 2))
                sgn.append(np.where(C[:, k] >= 0, s, 0.0))
        for i in range(3):
            for j in range(3):
                a, b = A[:, i], Bt[:, j]
                lo, up = np.minimum(a, b), np.maximum(a, b)
                idx.append(o["c"] + mo * B * B + lo * B + up)
                sgn.append(np.where(a == b, 0.0, np.where(a < b, 1.0, -1.0)))
        return np.stack(idx, 1).astype(np.int32), np.stack(sgn, 1).astype(np.float32)


def batches(layout, cols, rows):
    mo, mp, win, high, _, team, _ = cols
    out = []
    for s in range(0, len(rows), CHUNK):
        r = rows[s:s + CHUNK]
        idx, sgn = layout.encode(mo[r], mp[r], high[r], team[r])
        out.append((idx, sgn, (win[r] == 0).astype(np.float64)))
    return out


def objective(w, data, n, L):
    loss, g = 0.0, np.zeros_like(w)
    for idx, sgn, y in data:
        z = (w[idx] * sgn).sum(1)
        loss += float(np.sum(np.logaddexp(0, z) - y * z))
        g += np.bincount(idx.ravel(), weights=((1 / (1 + np.exp(-z)) - y)[:, None] * sgn).ravel(), minlength=len(w))
    return loss / n + float(np.sum(L * w * w)), g / n + 2 * L * w


def fit(layout, data, lam, iters, w0=None):
    n = sum(len(d[2]) for d in data)
    res = minimize(objective, np.zeros(layout.size) if w0 is None else w0, args=(data, n, layout.lam(lam)), jac=True,
                   method="L-BFGS-B", options={"maxiter": iters, "maxcor": 20})
    return res.x, int(res.nit)


def scores(w, data):
    return np.concatenate([(w[idx] * sgn).sum(1) for idx, sgn, _ in data]), np.concatenate([d[2] for d in data])


def log_loss(z, y):
    return float(np.mean(np.logaddexp(0, z) - y * z))


def accuracy(z, y):
    return float(np.mean((z >= 0) == (y == 1)))


def metrics(z, y):
    p = 1 / (1 + np.exp(-z))
    return {"log_loss": round(log_loss(z, y), 4), "accuracy": round(accuracy(z, y), 4), "brier": round(float(np.mean((p - y) ** 2)), 4)}


def warm_start(layout, prev, modes, maps):
    w = np.zeros(layout.size)
    if not prev.get("modes"):
        return None
    B, bi = layout.B, {int(b): i for i, b in enumerate(layout.brawlers)}
    o = {g: a for g, (a, _) in layout.off.items()}
    for mode, mi in modes.items():
        lm = prev["modes"].get(mode, {})
        for key, g in (("u", "u"), ("h", "h")):
            for b, x in lm.get(key, {}).items():
                if int(b) in bi:
                    w[o[g] + mi * B + bi[int(b)]] = x
        for key, g in (("syn", "s"), ("ctr", "c")):
            for a, b, x in lm.get(key, []):
                if a in bi and b in bi:
                    i, j = bi[a], bi[b]
                    sign = 1.0 if g == "s" or i < j else -1.0
                    w[o[g] + mi * B * B + min(i, j) * B + max(i, j)] = sign * x if g == "c" else x
    for key, pi in maps.items():
        for src, g in (("maps", "v"), ("maps_high", "vh")):
            for b, x in prev.get(src, {}).get(key, {}).items():
                if int(b) in bi:
                    w[o[g] + pi * B + bi[int(b)]] = x
    return w


def export(layout, w, modes, maps):
    B = layout.B
    o = {g: a for g, (a, _) in layout.off.items()}
    names = [int(b) for b in layout.brawlers]
    out = {"modes": {}, "maps": {}, "maps_high": {}}
    for mode, mi in modes.items():
        u = w[o["u"] + mi * B: o["u"] + (mi + 1) * B]
        h = w[o["h"] + mi * B: o["h"] + (mi + 1) * B]
        S = w[o["s"] + mi * B * B: o["s"] + (mi + 1) * B * B].reshape(B, B)
        C = w[o["c"] + mi * B * B: o["c"] + (mi + 1) * B * B].reshape(B, B)
        out["modes"][mode] = {
            "u": {str(names[i]): round(float(x), 4) for i, x in enumerate(u) if abs(x) >= 0.002},
            "h": {str(names[i]): round(float(x), 4) for i, x in enumerate(h) if abs(x) >= 0.002},
            "syn": [[names[i], names[j], round(float(S[i, j]), 3)] for i, j in zip(*np.nonzero(np.abs(S) >= 0.01)) if i < j],
            "ctr": [[names[i], names[j], round(float(C[i, j]), 3)] for i, j in zip(*np.nonzero(np.abs(C) >= 0.01)) if i < j],
        }
    for key, pi in maps.items():
        v = w[o["v"] + pi * B: o["v"] + (pi + 1) * B]
        vh = w[o["vh"] + pi * B: o["vh"] + (pi + 1) * B]
        out["maps"][key] = {str(names[i]): round(float(x), 4) for i, x in enumerate(v) if abs(x) >= 0.002}
        out["maps_high"][key] = {str(names[i]): round(float(x), 4) for i, x in enumerate(vh) if abs(x) >= 0.002}
    if layout.classes:
        k0, NC = o["k"], layout.NC
        out["comp"] = {mode: {c: [round(float(x), 4) for x in w[k0 + (mi * NC + ci) * 3: k0 + (mi * NC + ci) * 3 + 3]]
                              for ci, c in enumerate(layout.classes)} for mode, mi in modes.items()}
        out["classes"] = {str(names[i]): layout.classes[c] for i, c in enumerate(layout.cls) if c >= 0}
    return out


def smoothed_scores(core_path, calib, test_day, cols, rows, modes, maps):
    core = sqlite3.connect(core_path)
    since = core.execute("SELECT date(?, '-28 days')", (test_day,)).fetchone()[0]
    eff, syn_of, ctr_of = cal.build_signals(core, test_day, since)
    core.close()
    cw = calib["weights"]
    inv_mode = {v: k for k, v in modes.items()}
    inv_map = {v: k.split("|", 1)[1] for k, v in maps.items()}
    mo, mp, team = cols[0], cols[1], cols[5]
    z = np.empty(len(rows))
    for k, r in enumerate(rows):
        mode, map_ = inv_mode[int(mo[r])], inv_map[int(mp[r])]
        a, b = team[r, :3].tolist(), team[r, 3:].tolist()
        x = cw["map"] * (sum(eff(mode, map_, i) for i in a) - sum(eff(mode, map_, i) for i in b))
        x += cw["syn"] * (sum(syn_of(mode, a[i], a[j]) for i in range(3) for j in range(i + 1, 3))
                          - sum(syn_of(mode, b[i], b[j]) for i in range(3) for j in range(i + 1, 3)))
        x += cw["ctr"] * sum(ctr_of(mode, i, j) for i in a for j in b)
        z[k] = x
    return z


def main():
    ap = argparse.ArgumentParser(description="Train the joint draft model on recent games.")
    ap.add_argument("--raw-dir", default="work/raw")
    ap.add_argument("--stats", default="data/stats")
    ap.add_argument("--core", default="work/core.db")
    ap.add_argument("--max-games", type=int, default=int(os.environ.get("TRAIN_MAX_GAMES", 9_000_000)))
    ap.add_argument("--iterations", type=int, default=int(os.environ.get("TRAIN_ITERATIONS", 60)))
    ap.add_argument("--retune-hours", type=float, default=float(os.environ.get("TRAIN_RETUNE_HOURS", 24)))
    args = ap.parse_args()
    t0 = time.time()
    shards = sorted(glob.glob(os.path.join(args.raw_dir, "battles-*.db")))
    if len(shards) < 2:
        print("Training skipped: need at least two day shards.")
        return
    modes, maps, cols = load_games(shards, args.max_games)
    test_path = shards[-1]
    is_test = cols[6] == test_path
    train_rows, test_rows = np.nonzero(~is_test)[0], np.nonzero(is_test)[0]
    if len(train_rows) < 20000 or len(test_rows) < 2000:
        print(f"Training skipped: {len(train_rows)} train / {len(test_rows)} test games (need 20000 / 2000).")
        return
    info_path = os.path.join(args.stats, "brawler_info.json")
    classes = {}
    if os.path.exists(info_path):
        classes = {int(b): v["class"] for b, v in json.load(open(info_path))["brawlers"].items() if v.get("class")}
    layout = Layout(len(modes), len(maps), np.unique(cols[5]), classes)
    prev_path = os.path.join(args.stats, "learned.json")
    prev = json.load(open(prev_path)) if os.path.exists(prev_path) else {}
    w0 = warm_start(layout, prev, modes, maps) if prev.get("trainer") == 2 else None
    print(f"  {len(train_rows):,} training games, {len(test_rows):,} test games, {layout.size:,} weights, loaded in {time.time() - t0:.0f}s"
          f"{' (warm start)' if w0 is not None else ''}", flush=True)
    train_data = batches(layout, cols, train_rows)
    test_data = batches(layout, cols, test_rows)
    tuned_at = prev.get("tuned_at", 0)
    if prev.get("trainer") == 2 and prev.get("lambda") and time.time() - tuned_at < args.retune_hours * 3600:
        lam = prev["lambda"]
        print(f"  reusing regularisation from {time.strftime('%H:%M', time.gmtime(tuned_at))} UTC: lambda {lam:g}")
    else:
        cut = int(len(train_data) * 0.92) or 1
        fit_data, val_data = train_data[:cut], train_data[cut:] or train_data[-1:]
        best = None
        for lam in LAMBDAS:
            w, it = fit(layout, fit_data, lam, args.iterations, w0)
            ll = log_loss(*scores(w, val_data))
            print(f"  lambda {lam:g}: validation log loss {ll:.4f} ({it} iterations)", flush=True)
            if best is None or ll < best[0]:
                best = (ll, lam, w)
        _, lam, w0 = best
        tuned_at = time.time()
    w, it = fit(layout, train_data, lam, args.iterations, w0)
    del train_data
    zt, y = scores(w, test_data)
    test_m = metrics(zt, y)
    calib_path = os.path.join(args.stats, "model.json")
    calib = json.load(open(calib_path)) if os.path.exists(calib_path) else None
    baseline = calib["calibrated"] if calib else None
    test_day = re.search(r"battles-(\d{4}-\d{2}-\d{2})", test_path).group(1)
    blend, ens = 1.0, None
    chosen = baseline is None or test_m["log_loss"] < baseline["log_loss"] - 0.0005
    if calib and os.path.exists(args.core):
        zb = smoothed_scores(args.core, calib, test_day, cols, test_rows, modes, maps)
        half = len(y) // 2
        A, B = slice(0, half), slice(half, None)
        blend = min((round(a, 2) for a in np.linspace(0, 1, 11)), key=lambda a: log_loss(a * zt[A] + (1 - a) * zb[A], y[A]))
        zbl = blend * zt + (1 - blend) * zb
        ens = {"alpha": blend, "judged_games": int(len(y) - half),
               "smoothed": {"log_loss": round(log_loss(zb[B], y[B]), 4), "accuracy": round(accuracy(zb[B], y[B]), 4)},
               "trained": {"log_loss": round(log_loss(zt[B], y[B]), 4), "accuracy": round(accuracy(zt[B], y[B]), 4)},
               "blend": {"log_loss": round(log_loss(zbl[B], y[B]), 4), "accuracy": round(accuracy(zbl[B], y[B]), 4)}}
        chosen = blend > 0 and ens["blend"]["log_loss"] < ens["smoothed"]["log_loss"] - 0.0003
        print(f"  blend {blend:.2f} trained + {1 - blend:.2f} smoothed on {len(y) - half:,} games: log loss {ens['blend']['log_loss']} "
              f"(smoothed {ens['smoothed']['log_loss']}, trained {ens['trained']['log_loss']})", flush=True)
    final = batches(layout, cols, np.arange(len(cols[0])))
    w_final, _ = fit(layout, final, lam, max(10, args.iterations // 3), w)
    del final
    out = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "trainer": 2, "chosen": chosen, "blend": blend, "ensemble": ens,
           "test_day": test_day, "train_games": int(len(train_rows)), "test_games": int(len(test_rows)), "lambda": lam, "iterations": it,
           "tuned_at": int(tuned_at), "test": test_m, "baseline_calibrated": baseline, **export(layout, w_final, modes, maps)}
    os.makedirs(args.stats, exist_ok=True)
    with open(prev_path, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    verdict = (f"USING {'trained model' if blend == 1 else f'blend ({blend:.0%} trained)'}" if chosen
               else "keeping smoothed model (trained/blended model did not beat it)")
    print(f"Trained on {len(train_rows):,} games, tested on {len(test_rows):,} unseen: log loss {test_m['log_loss']} "
          f"accuracy {test_m['accuracy']:.1%} vs smoothed {baseline and baseline['log_loss']} -> {verdict} ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    sys.exit(main())
