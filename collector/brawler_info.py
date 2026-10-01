import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

API = "https://brawlstars.fandom.com/api.php"
UA = "RankedDraftLab/1.0 (private fan project; daily batch of brawler infoboxes)"
STAT_HINTS = ("health", "damage", "speed", "range", "reload", "attack", "super", "bullets", "spread", "hypercharge", "cooldown")
NOT_STATS = ("label", "name", "title", "next", "voice")


def wiki_title(name):
    t = " ".join(w[:1].upper() + w[1:].lower() for w in name.split(" "))
    return "-".join(p[:1].upper() + p[1:] for p in t.split("-"))


def fetch_pages(titles):
    q = {"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main", "format": "json",
         "formatversion": "2", "redirects": "1", "titles": "|".join(titles)}
    req = urllib.request.Request(f"{API}?{urllib.parse.urlencode(q)}", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    redirects = {x["from"]: x["to"] for x in data.get("query", {}).get("redirects", [])}
    normalized = {x["from"]: x["to"] for x in data.get("query", {}).get("normalized", [])}
    pages = {}
    for p in data.get("query", {}).get("pages", []):
        revs = p.get("revisions") or []
        if revs:
            pages[p["title"]] = revs[0].get("slots", {}).get("main", {}).get("content", "")
    out = {}
    for t in titles:
        final = redirects.get(normalized.get(t, t), normalized.get(t, t))
        if final in pages:
            out[t] = (final, pages[final])
    return out


def clean(v):
    v = re.sub(r"<ref[^>]*>.*?</ref>|<ref[^>]*/>", "", v, flags=re.S)
    v = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", v)
    v = re.sub(r"\{\{[^{}]*\}\}", "", v)
    v = re.sub(r"<br\s*/?>", " / ", v)
    v = re.sub(r"<[^>]+>|'''?", "", v)
    return re.sub(r"\s+", " ", v).strip()


def parse_infobox(text):
    i = text.find("{{Brawler Infobox")
    if i < 0:
        return {}
    depth, j = 0, i
    while j < len(text):
        if text.startswith("{{", j):
            depth, j = depth + 1, j + 2
        elif text.startswith("}}", j):
            depth, j = depth - 1, j + 2
            if depth == 0:
                break
        else:
            j += 1
    body = text[i + 2:j - 2]
    parts, cur, d = [], [], 0
    k = 0
    while k < len(body):
        two = body[k:k + 2]
        if two in ("{{", "[["):
            d += 1; cur.append(two); k += 2; continue
        if two in ("}}", "]]"):
            d -= 1; cur.append(two); k += 2; continue
        if body[k] == "|" and d == 0:
            parts.append("".join(cur)); cur = []; k += 1; continue
        cur.append(body[k]); k += 1
    parts.append("".join(cur))
    fields = {}
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, val = part.split("=", 1)
        key, val = key.strip().lower(), clean(val)
        if key and val and key not in fields:
            fields[key] = val[:160]
    return fields


def main(out_path="data/stats/brawler_info.json", max_age_h=20):
    if os.path.exists(out_path):
        old = json.load(open(out_path))
        if old.get("classes") and time.time() - old.get("fetched_ts", 0) < max_age_h * 3600:
            print("Brawler info is fresh; skipping.")
            return
    names = json.load(open(os.path.join(os.path.dirname(out_path), "summary.json"))).get("brawler_names", {})
    titles = {wiki_title(n): bid for bid, n in names.items()}
    found = {}
    try:
        keys = list(titles)
        for i in range(0, len(keys), 50):
            found.update(fetch_pages(keys[i:i + 50]))
            time.sleep(1)
    except (OSError, ValueError) as e:
        print(f"::warning::Brawler info (wiki) fetch failed, keeping previous: {e}")
        return
    out, seen_keys, sample = {}, set(), None
    for title, (final, text) in found.items():
        f = parse_infobox(text)
        if sample is None and names[titles[title]].upper() == "SHELLY":
            sample = text[:2500]
        seen_keys.update(f)
        cls = f.get("class") or f.get("role") or f.get("type")
        stats = {k: v for k, v in f.items()
                 if any(h in k for h in STAT_HINTS) and not any(x in k for x in NOT_STATS) and re.search(r"\d", v)}
        out[titles[title]] = {"name": names[titles[title]], "class": cls, "rarity": f.get("rarity"), "stats": stats,
                              "wiki": f"https://brawlstars.fandom.com/wiki/{urllib.parse.quote(final.replace(' ', '_'))}"}
    if len(out) < len(names) * 0.5:
        print(f"::warning::Brawler info: only {len(out)}/{len(names)} wiki pages found; keeping previous. Infobox keys seen: {sorted(seen_keys)[:40]}")
        return
    classes = sorted({v["class"] for v in out.values() if v["class"]})
    if not classes:
        print(f"::warning::Brawler info: wiki pages found but no class field parsed. Keys seen: {sorted(seen_keys)[:60]}")
        print("----- sample wikitext (Shelly) -----\n" + (sample or next(iter(found.values()))[1][:2500]) + "\n-----")
        return
    with open(out_path, "w") as fh:
        json.dump({"fetched_ts": int(time.time()), "source": "Brawl Stars Fandom wiki (CC-BY-SA)", "classes": classes,
                   "brawlers": out}, fh, separators=(",", ":"), ensure_ascii=False)
    with_stats = sum(1 for v in out.values() if v["stats"])
    print(f"Brawler info: {len(out)}/{len(names)} brawlers from the wiki, {with_stats} with stats; classes: {', '.join(classes)}")
    print(f"  stat fields seen: {sorted({k for v in out.values() for k in v['stats']})[:30]}")


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:2]))
