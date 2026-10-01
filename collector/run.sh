#!/usr/bin/env bash
set -uo pipefail
total=$1 segment=$2
work=${WORK_DIR:-work}
start=$(date +%s)
end=$(( start + total * 60 ))
cd "$(dirname "$0")/.."
bash collector/db_release.sh download "$work" || exit 1
python3 collector/edge.py pull --core "$work/core.db" --raw-dir "$work/raw"
fatal=0
while :; do
  left=$(( end - $(date +%s) ))
  (( left >= 30 )) || break
  seconds=$(( left < segment * 60 ? left : segment * 60 ))
  minutes=$(awk "BEGIN { printf \"%.2f\", $seconds / 60 }")
  echo "::group::Crawl $minutes min"
  timeout $(( seconds + 600 )) python3 collector/crawl.py --core "$work/core.db" --raw-dir "$work/raw" --minutes "$minutes"
  code=$?
  echo "::endgroup::"
  python3 collector/export_stats.py --core "$work/core.db" --raw-dir "$work/raw" --out data/stats || exit 1
  python3 collector/calibrate.py --core "$work/core.db" --raw-dir "$work/raw" --out data/stats/model.json || echo "::warning::Calibration failed (non-fatal)"
  python3 collector/brawler_info.py data/stats/brawler_info.json || true
  { python3 -c "import numpy" 2>/dev/null || pip install --quiet numpy; } &&
    python3 collector/train.py --raw-dir "$work/raw" --stats data/stats --core "$work/core.db" || echo "::warning::Training failed (non-fatal)"
  node collector/selfplay.mjs --stats data/stats --minutes "${SELFPLAY_MINUTES:-3}" || echo "::warning::Self-play failed (non-fatal)"
  python3 collector/ai_notes.py --stats data/stats || echo "::warning::AI notes step failed (non-fatal)"
  bash collector/db_release.sh upload "$work" || exit 1
  bash collector/commit_stats.sh || exit 1
  if [ -n "${CLOUDFLARE_API_TOKEN:-}" ] && (( end - $(date +%s) > 300 )); then
    bash deploy/deploy.sh >/dev/null 2>&1 && echo "Site refreshed." || echo "::warning::Mid-run site deploy failed (non-fatal)"
  fi
  if (( code == 2 )); then echo "::error::API rejected the key (HTTP 403). Check BRAWL_STARS_API_KEY and its whitelisted IP."; fatal=1; break; fi
  if (( code == 3 )); then echo "::warning::API unavailable (maintenance?). Stopping this run early."; break; fi
  if (( code != 0 )); then echo "::error::Crawler exited with code $code"; fatal=1; break; fi
done
du -sh "$work"/core.db "$work"/raw/*.db 2>/dev/null || true
echo "ran_minutes=$(( ($(date +%s) - start) / 60 ))" >> "${GITHUB_OUTPUT:-/dev/null}"
exit $fatal
