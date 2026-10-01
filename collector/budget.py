import calendar
import json
import math
import os
import sys
import time
from datetime import datetime, timezone

USAGE = "data/stats/usage.json"
RUNS_PER_DAY = 3
FIXED_MINUTES = 9.5
MIN_CRAWL, MAX_CRAWL = 4, 40


def load():
    now = datetime.now(timezone.utc)
    month = now.strftime("%Y-%m")
    u = json.load(open(USAGE)) if os.path.exists(USAGE) else {}
    if u.get("month") != month:
        u = {"month": month, "minutes": 0, "runs": 0, "history": []}
    return now, u


def plan():
    now, u = load()
    budget = float(os.environ.get("ACTIONS_BUDGET_MINUTES") or 1900)
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    hours_left = (days_in_month - now.day) * 24 + (24 - now.hour - now.minute / 60)
    runs_left = max(1, math.ceil(hours_left / (24 / RUNS_PER_DAY)))
    per_run = (budget - u["minutes"]) / runs_left
    crawl = int(max(MIN_CRAWL, min(MAX_CRAWL, per_run - FIXED_MINUTES)))
    if u["minutes"] >= budget:
        crawl = 0
    print(f"budget {budget:.0f} min, used {u['minutes']:.0f} this month in {u['runs']} runs, "
          f"{runs_left} runs left -> {per_run:.1f} min per run, crawl {crawl} min", file=sys.stderr)
    print(crawl)


def record(start_epoch):
    now, u = load()
    minutes = math.ceil((time.time() - float(start_epoch)) / 60) + 1
    u["minutes"] += minutes
    u["runs"] += 1
    u["history"] = (u.get("history") or [])[-90:] + [[now.strftime("%Y-%m-%dT%H:%MZ"), minutes]]
    os.makedirs(os.path.dirname(USAGE), exist_ok=True)
    json.dump(u, open(USAGE, "w"), indent=1)
    print(f"Recorded {minutes} min; {u['minutes']} min used in {u['month']} ({u['runs']} runs)")


if __name__ == "__main__":
    if sys.argv[1:2] == ["plan"]:
        plan()
    elif sys.argv[1:2] == ["record"]:
        record(sys.argv[2])
    else:
        sys.exit("usage: budget.py plan | record <start-epoch>")
