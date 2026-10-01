import argparse
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import quote

CDN = "https://cdn.brawlify.com"
K_MODE, K_MAP, MIN_GAMES = 120, 50, 15
MODE_EMOJI = {"gemGrab": "💎", "brawlBall": "⚽", "heist": "🔒", "bounty": "⭐", "hotZone": "🔥", "knockout": "💀"}
MODE_NAME = {"gemGrab": "Gem Grab", "brawlBall": "Brawl Ball", "heist": "Heist", "bounty": "Bounty",
             "hotZone": "Hot Zone", "knockout": "Knockout"}
COLOR = 0xFFB020
UA = {"User-Agent": "ranked-draft-lab/1.0"}


def load(stats_dir, name, optional=False):
    path = os.path.join(stats_dir, f"{name}.json")
    if optional and not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def title(s):
    return " ".join(w[:1].upper() + w[1:].lower() for w in (s or "").split(" "))


def best_on_map(m, mode_stats, n=5):
    rows = []
    for bid, (g, w, l, _d) in m["brawlers"].items():
        if g < MIN_GAMES:
            continue
        ms = mode_stats.get(m["mode"], {}).get(bid, [0, 0, 0, 0])
        prior = (ms[1] + K_MODE * 0.5) / (ms[1] + ms[2] + K_MODE)
        p = (w + K_MAP * prior) / (w + l + K_MAP)
        rows.append((p, bid, g))
    rows.sort(reverse=True)
    return [{"id": bid, "wr": p, "games": g, "pick": g / max(1, m["battles"])} for p, bid, g in rows[:n]]


class Painter:
    def __init__(self, cache_dir):
        from PIL import Image, ImageDraw, ImageFont
        self.Image, self.ImageDraw, self.ImageFont = Image, ImageDraw, ImageFont
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self.failed = 0
        self.bad_ids = set()

    def font(self, size):
        for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"):
            if os.path.exists(path):
                return self.ImageFont.truetype(path, size)
        return self.ImageFont.load_default(size=size)

    def portrait(self, bid, name, size):
        path = os.path.join(self.cache_dir, f"{bid}.png")
        if bid in self.bad_ids:
            path = None
        elif not os.path.exists(path):
            try:
                req = urllib.request.Request(f"{CDN}/brawlers/borderless/{bid}.png", headers=UA)
                with urllib.request.urlopen(req, timeout=15) as r, open(path, "wb") as f:
                    f.write(r.read())
            except (OSError, urllib.error.URLError):
                self.failed += 1
                self.bad_ids.add(bid)
                path = None
        img = None
        if path:
            try:
                img = self.Image.open(path).convert("RGBA")
                img.thumbnail((size, size))
            except OSError:
                img = None
        if img is None:
            img = self.Image.new("RGBA", (size, size), (44, 53, 80, 255))
            d = self.ImageDraw.Draw(img)
            d.text((size / 2, size / 2), title(name)[:2], font=self.font(size // 3), fill=(200, 208, 230), anchor="mm")
        tile = self.Image.new("RGBA", (size, size), (0, 0, 0, 0))
        tile.paste(img, ((size - img.width) // 2, (size - img.height) // 2), img)
        return tile

    def strip(self, rows, names, size=112):
        pad, label_h = 10, 50
        w = pad + len(rows) * (size + pad)
        canvas = self.Image.new("RGBA", (max(w, 200), size + label_h + pad * 2), (20, 25, 37, 255))
        d = self.ImageDraw.Draw(canvas)
        f_big, f_small = self.font(22), self.font(15)
        for i, r in enumerate(rows):
            x = pad + i * (size + pad)
            d.rounded_rectangle((x, pad, x + size, pad + size), 14, fill=(34, 42, 61, 255))
            canvas.alpha_composite(self.portrait(r["id"], names.get(str(r["id"]), "?"), size), (x, pad))
            wr = r["wr"] * 100
            color = (54, 211, 153) if wr >= 53 else (255, 176, 32) if wr >= 50 else (255, 107, 107)
            d.text((x + size / 2, pad + size + 14), f"{wr:.1f}%", font=f_big, fill=color, anchor="mm")
            d.text((x + size / 2, pad + size + 38), title(names.get(str(r["id"]), "?"))[:12], font=f_small,
                   fill=(233, 237, 247), anchor="mm")
        out = io.BytesIO()
        canvas.convert("RGB").save(out, "PNG", optimize=True)
        return out.getvalue()


def post(webhook, payload, files):
    boundary = uuid.uuid4().hex
    body = io.BytesIO()

    def part(headers, data):
        body.write(f"--{boundary}\r\n".encode())
        for k, v in headers.items():
            body.write(f"{k}: {v}\r\n".encode())
        body.write(b"\r\n")
        body.write(data)
        body.write(b"\r\n")
    part({"Content-Disposition": 'form-data; name="payload_json"', "Content-Type": "application/json"},
         json.dumps(payload).encode())
    for i, (name, data) in enumerate(files):
        part({"Content-Disposition": f'form-data; name="files[{i}]"; filename="{name}"', "Content-Type": "image/png"}, data)
    body.write(f"--{boundary}--\r\n".encode())
    for attempt in range(5):
        req = urllib.request.Request(webhook + ("&" if "?" in webhook else "?") + "wait=true", data=body.getvalue(), method="POST",
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}", **UA})
        try:
            with urllib.request.urlopen(req, timeout=30):
                return
        except urllib.error.HTTPError as e:
            if e.code == 429:
                retry = json.loads(e.read() or b"{}").get("retry_after", 2)
                time.sleep(float(retry) + 0.5)
                continue
            raise RuntimeError(f"Discord answered HTTP {e.code}: {e.read()[:300]!r}") from None
    raise RuntimeError("Discord kept rate limiting")


def build_messages(stats_dir, site_url, painter):
    maps, brawlers, summary = load(stats_dir, "maps"), load(stats_dir, "brawlers"), load(stats_dir, "summary")
    notes = (load(stats_dir, "ai_notes", optional=True) or {}).get("maps", {})
    names = {str(k): v for k, v in summary.get("brawler_names", {}).items()}
    mode_stats = {}
    for bid, b in brawlers["brawlers"].items():
        for mode, s in b["modes"].items():
            mode_stats.setdefault(mode, {})[bid] = s
    lr = summary.get("last_run", {})
    minutes = max(lr.get("seconds", 0) / 60, 1e-9)
    scanned = lr.get("new_battles", 0) + lr.get("dup_battles", 0) + lr.get("old_battles", 0)
    pool = {tuple(x) for x in maps.get("current_pool", [])}
    pool_maps = [m for m in maps["maps"] if (m["mode"], m["map"]) in pool] or maps["maps"]
    pool_maps.sort(key=lambda m: (m["mode"], -m["battles"]))

    overall = []
    for bid, b in brawlers["brawlers"].items():
        g, w, l, _ = b["all"]
        if g >= 200:
            overall.append({"id": bid, "wr": (w + 50 * 0.5) / (w + l + 50), "games": g})
    overall.sort(key=lambda r: -r["wr"])

    link = lambda path="": f"{site_url.rstrip('/')}/{path}" if site_url else None
    fmt = lambda n: f"{n:,.0f}"
    summary_embed = {
        "title": "📊 Ranked data collector: run report",
        "url": link(),
        "color": COLOR,
        "description": (f"Scanned **{fmt(scanned)}** ranked matches in {minutes:.1f} min "
                        f"(**{fmt(scanned / minutes)} / min**), **{fmt(lr.get('new_battles', 0))}** of them new."
                        + (f"\n[Open the draft helper]({link()})" if site_url else "")),
        "fields": [
            {"name": "Matches scanned / min", "value": fmt(scanned / minutes), "inline": True},
            {"name": "New games / min", "value": fmt(lr.get("new_battles", 0) / minutes), "inline": True},
            {"name": "API requests / min", "value": fmt(lr.get("requests", 0) / minutes), "inline": True},
            {"name": "Games (last 28 days)", "value": fmt(summary.get("battles_in_window", 0)), "inline": True},
            {"name": "Games all-time", "value": fmt(summary.get("battles_total", 0)), "inline": True},
            {"name": "Players tracked", "value": fmt(summary.get("players_known", 0)), "inline": True},
            {"name": "Player builds known", "value": fmt(summary.get("loadouts", 0)), "inline": True},
            {"name": "Ranked maps", "value": f"{len(pool_maps)} in rotation", "inline": True},
            {"name": "Profiles fetched", "value": fmt(lr.get("profiles", 0)), "inline": True},
        ],
        "footer": {"text": "Top brawlers = Bayesian-smoothed ranked win rate"},
        "timestamp": summary.get("generated_at"),
    }
    edge = summary.get("edge") or {}
    if edge.get("batches"):
        hours = max((time.time() - edge.get("at", time.time())) / 3600, 0)
        summary_embed["fields"].append({"name": "🌐 24/7 edge collector (Cloudflare)", "inline": False,
            "value": f"{fmt(edge['games'])} ranked games gathered between runs ({fmt(edge['new_battles'])} new, "
                     f"{fmt(edge['batches'])} one-minute batches)"})
    learned, sp = load(stats_dir, "learned", optional=True), load(stats_dir, "selfplay", optional=True)
    if learned or sp:
        parts = []
        if learned:
            parts.append(f"trained model {'**in use**' if learned.get('chosen') else 'not used yet'}: "
                         f"{learned['test']['accuracy'] * 100:.1f}% on {fmt(learned['test_games'])} unseen games")
        if sp:
            parts.append(f"self-play: {fmt(sp.get('total_drafts', sp['drafts']))} drafts so far (+{fmt(sp['drafts'])} this run), "
                         f"{sp.get('book_entries', len(sp.get('book', {})))} book entries, strategy *{sp['search']['name']}*")
        summary_embed["fields"].append({"name": "🧠 Self-training (every run)", "value": "\n".join(parts)[:1024], "inline": False})
    calib = load(stats_dir, "model", optional=True)
    if calib:
        summary_embed["fields"].append({"name": "🎯 Draft model accuracy (unseen games)", "inline": False,
            "value": f"{calib['calibrated']['accuracy'] * 100:.1f}% correct winner on {fmt(calib['test_games'])} games "
                     f"(log loss {calib['calibrated']['log_loss']} vs coin flip {calib['coin_flip']['log_loss']})"})
    files = []
    if overall and painter:
        files.append(("overall.png", painter.strip(overall[:8], names, size=96)))
        summary_embed["image"] = {"url": "attachment://overall.png"}
        summary_embed["fields"].append({"name": "🏆 Best brawlers overall",
                                        "value": " · ".join(f"{title(names.get(r['id'], '?'))} {r['wr'] * 100:.1f}%" for r in overall[:8])[:1024]})
    messages = [({"username": "Ranked Draft Lab", "embeds": [summary_embed]}, files)]

    embeds, files, chars = [], [], 0
    for i, m in enumerate(pool_maps):
        best = best_on_map(m, mode_stats)
        key = f"{m['mode']}|{m['map']}"
        lines = [f"**{n + 1}. {title(names.get(r['id'], '?'))}** · {r['wr'] * 100:.1f}% WR · {r['pick'] * 100:.1f}% pick · {r['games']} games"
                 for n, r in enumerate(best)] or ["Not enough games yet"]
        if notes.get(key):
            lines.append(f"\n🤖 {notes[key][:300]}")
        embed = {"title": f"{MODE_EMOJI.get(m['mode'], '🎮')} {m['map']}", "color": COLOR,
                 "url": link(f"#/meta?map={quote(key)}"),
                 "description": f"{MODE_NAME.get(m['mode'], m['mode'])} · {fmt(m['battles'])} ranked games\n" + "\n".join(lines)}
        if m.get("event_id"):
            embed["thumbnail"] = {"url": f"{CDN}/maps/regular/{m['event_id']}.png"}
        if best and painter:
            fname = f"map{i}.png"
            pending = (fname, painter.strip(best, names))
            embed["image"] = {"url": f"attachment://{fname}"}
        size = len(embed["title"]) + len(embed["description"])
        if embeds and (len(embeds) == 10 or chars + size > 5500):
            messages.append(({"username": "Ranked Draft Lab", "embeds": embeds}, files))
            embeds, files, chars = [], [], 0
        embeds.append(embed)
        chars += size
        if embed.get("image"):
            files.append(pending)
    if embeds:
        messages.append(({"username": "Ranked Draft Lab", "embeds": embeds}, files))
    return messages


def main():
    ap = argparse.ArgumentParser(description="Post the run report to Discord.")
    ap.add_argument("--stats", default="data/stats")
    ap.add_argument("--site-url", default=os.environ.get("SITE_URL", ""))
    ap.add_argument("--dry-run", metavar="DIR", help="write payloads/images here instead of posting")
    ap.add_argument("--no-images", action="store_true")
    args = ap.parse_args()
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook and not args.dry_run:
        print("DISCORD_WEBHOOK_URL not set; skipping Discord report.")
        return
    painter = None
    if not args.no_images:
        try:
            painter = Painter(os.path.join(os.environ.get("RUNNER_TEMP", "/tmp"), "brawler-portraits"))
        except ImportError:
            print("Pillow not installed; sending report without images.", file=sys.stderr)
    messages = build_messages(args.stats, args.site_url, painter)
    if painter and painter.failed:
        print(f"{painter.failed} portrait(s) could not be downloaded; used initials instead.", file=sys.stderr)
    for n, (payload, files) in enumerate(messages):
        if args.dry_run:
            os.makedirs(args.dry_run, exist_ok=True)
            with open(os.path.join(args.dry_run, f"message{n}.json"), "w") as f:
                json.dump(payload, f, indent=1, ensure_ascii=False)
            for name, data in files:
                with open(os.path.join(args.dry_run, f"message{n}-{name}"), "wb") as f:
                    f.write(data)
        else:
            post(webhook, payload, files)
            time.sleep(1.2)
    print(f"Discord report: {len(messages)} message(s), {sum(len(p['embeds']) for p, _ in messages)} embed(s)"
          + (" [dry run]" if args.dry_run else " sent."))


if __name__ == "__main__":
    main()
