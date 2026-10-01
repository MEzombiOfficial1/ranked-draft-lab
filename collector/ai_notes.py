import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

ENDPOINT = "https://models.github.ai/inference/chat/completions"
FALLBACK_MODELS = ["openai/gpt-4.1-mini", "openai/gpt-4o-mini"]
CF_API = "https://api.cloudflare.com/client/v4"
CF_MODELS = ["@cf/meta/llama-3.3-70b-instruct-fp8-fast", "@cf/meta/llama-3.1-8b-instruct-fast"]
K_MODE, K_MAP = 120, 50
PROMPT = """You are a top Brawl Stars Ranked (Legendary/Masters) coach. For each map below you get the
brawlers with the best Bayesian-smoothed win rates from real ranked games (win rate, pick rate, games).
Write one practical draft note per map (max 200 characters): which picks are safest first picks,
what to ban, and one key synergy or counter idea. Base claims on the numbers; do not invent stats.
Answer ONLY with a JSON object mapping the exact map name to its note."""


def smoothed_top(m, mode_stats, n=10):
    rows = []
    for bid, (g, w, l, _d) in m["brawlers"].items():
        if g < 20:
            continue
        ms = mode_stats.get(m["mode"], {}).get(bid, [0, 0, 0, 0])
        prior = (ms[1] + K_MODE * 0.5) / (ms[1] + ms[2] + K_MODE)
        rows.append(((w + K_MAP * prior) / (w + l + K_MAP), bid, g))
    rows.sort(reverse=True)
    return rows[:n]


def _post(url, token, payload, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json",
        "User-Agent": "ranked-draft-lab/1.0", "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _extract(content):
    match = re.search(r"\{.*\}", content, re.S)
    if not match:
        raise ValueError(f"no JSON object in answer: {content[:200]!r}")
    return json.loads(match.group(0))


def ask_github(model, content):
    raw = _post(ENDPOINT, os.environ["GITHUB_TOKEN"], {
        "model": model, "temperature": 0.3, "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": PROMPT}, {"role": "user", "content": content}]})
    try:
        return _extract(json.loads(raw)["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        raise ValueError(f"unexpected response: {raw[:200]!r}") from None


_cf_account = None


def ask_cloudflare(model, content):
    global _cf_account
    token = os.environ["CLOUDFLARE_API_TOKEN"]
    if not _cf_account:
        _cf_account = os.environ.get("CLOUDFLARE_ACCOUNT_ID") or None
    if not _cf_account:
        req = urllib.request.Request(f"{CF_API}/accounts?per_page=5", headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=30) as r:
            _cf_account = json.load(r)["result"][0]["id"]
    raw = _post(f"{CF_API}/accounts/{_cf_account}/ai/run/{model}", token, {
        "max_tokens": 1500, "temperature": 0.3,
        "messages": [{"role": "system", "content": PROMPT}, {"role": "user", "content": content}]}, timeout=120)
    try:
        answer = json.loads(raw)["result"]["response"]
    except (KeyError, TypeError, json.JSONDecodeError):
        raise ValueError(f"unexpected response: {raw[:200]!r}") from None
    return answer if isinstance(answer, dict) else _extract(answer)


def main():
    ap = argparse.ArgumentParser(description="Generate short meta notes for the current ranked maps.")
    ap.add_argument("--stats", default="data/stats")
    ap.add_argument("--model", default=os.environ.get("AI_NOTES_MODEL", "openai/gpt-4.1-mini"))
    ap.add_argument("--max-age-hours", type=float, default=20)
    args = ap.parse_args()
    providers = []
    if os.environ.get("GITHUB_TOKEN"):
        providers += [("GitHub Models", m, ask_github) for m in [args.model] + [m for m in FALLBACK_MODELS if m != args.model]]
    if os.environ.get("CLOUDFLARE_API_TOKEN"):
        providers += [("Workers AI", m, ask_cloudflare) for m in CF_MODELS]
    if not providers:
        print("No AI provider configured; skipping AI notes.")
        return
    path = os.path.join(args.stats, "ai_notes.json")
    old = json.load(open(path)) if os.path.exists(path) else {"maps": {}}
    maps = json.load(open(os.path.join(args.stats, "maps.json")))
    brawlers = json.load(open(os.path.join(args.stats, "brawlers.json")))
    names = json.load(open(os.path.join(args.stats, "summary.json"))).get("brawler_names", {})
    pool = {tuple(x) for x in maps.get("current_pool", [])}
    keys = {f"{mo}|{mp}" for mo, mp in pool}
    fresh = time.time() - old.get("generated_ts", 0) < args.max_age_hours * 3600
    if fresh and keys <= set(old.get("maps", {})):
        print("AI notes are fresh; skipping.")
        return
    mode_stats = {}
    for bid, b in brawlers["brawlers"].items():
        for mode, s in b["modes"].items():
            mode_stats.setdefault(mode, {})[bid] = s
    notes, errors, used = dict(old.get("maps", {})), 0, None
    by_mode = {}
    for m in maps["maps"]:
        if (m["mode"], m["map"]) in pool:
            by_mode.setdefault(m["mode"], []).append(m)
    for mode, ms in sorted(by_mode.items()):
        lines = [f"Mode: {mode}"]
        for m in ms:
            top = smoothed_top(m, mode_stats)
            if not top:
                continue
            lines.append(f"\nMap: {m['map']} ({m['battles']} games)")
            lines += [f"- {names.get(bid, bid)}: {p * 100:.1f}% WR, {g / max(1, m['battles']) * 100:.1f}% pick, {g} games"
                      for p, bid, g in top]
        answer = None
        for provider in list(providers):
            name, model, fn = provider
            try:
                answer = fn(model, "\n".join(lines))
                used = f"{name} {model}"
                break
            except (urllib.error.URLError, OSError, KeyError, ValueError) as e:
                detail = f"HTTP {e.code} {e.read()[:200]!r}" if isinstance(e, urllib.error.HTTPError) else e
                print(f"AI notes for {mode} with {name} {model} failed: {detail}", file=sys.stderr)
                providers.remove(provider)
        if answer is None:
            errors += 1
            continue
        for m in ms:
            note = answer.get(m["map"])
            if isinstance(note, str) and note.strip():
                notes[f"{mode}|{m['map']}"] = note.strip()[:300]
    if errors == len(by_mode):
        print("AI notes: every request failed; keeping previous notes.")
        return
    with open(path, "w") as f:
        json.dump({"generated_ts": int(time.time()), "model": used, "maps": notes}, f, ensure_ascii=False,
                  separators=(",", ":"), sort_keys=True)
    print(f"AI notes written for {len(notes)} maps ({errors} mode request(s) failed).")


if __name__ == "__main__":
    main()
