#!/usr/bin/env bash
set -euo pipefail
cmd=$1 work=$2
core_tag=ranked-database
days=${CRAWL_MAX_AGE_DAYS:-3}
tmp=${RUNNER_TEMP:-/tmp}
mkdir -p "$work/raw"

assets() { gh release view "$1" --json assets --jq ".assets[].name | select(test(\"$2\"))" 2>/dev/null || true; }
ensure_release() {
  gh release view "$1" >/dev/null 2>&1 || gh release create "$1" --latest=false --title "$2" --notes "$3" >/dev/null
}

case $cmd in
  download)
    name=$(assets "$core_tag" '^core-.*[.]db[.]gz$' | sort | tail -n 1)
    if [ -n "$name" ]; then
      gh release download "$core_tag" -p "$name" -O "$tmp/$name" --clobber
      gunzip -c "$tmp/$name" > "$work/core.db" && rm -f "$tmp/$name"
      echo "Restored $name"
    else
      echo "No core database yet - starting fresh."
    fi
    for i in $(seq 0 "$days"); do
      day=$(date -u -d "-$i day" +%F)
      tag="raw-${day:0:7}"
      if assets "$tag" "^battles-${day}[.]db[.]gz$" | grep -q .; then
        gh release download "$tag" -p "battles-$day.db.gz" -O "$tmp/battles-$day.db.gz" --clobber
        gunzip -c "$tmp/battles-$day.db.gz" > "$work/raw/battles-$day.db" && rm -f "$tmp/battles-$day.db.gz"
        echo "Restored battles-$day"
      fi
    done
    for old in $(assets "$core_tag" '^(ranked|archive)-.*[.]db[.]gz$'); do gh release delete-asset "$core_tag" "$old" -y; done
    touch "$work/.downloaded"
    ;;
  upload)
    ensure_release "$core_tag" "Ranked database" \
      "core-*.db.gz: crawl frontier, player builds and daily aggregates (newest file is current). Raw games are in the raw-YYYY-MM releases."
    name="core-$(date -u +%Y%m%dT%H%M%SZ).db.gz"
    gzip -c -6 "$work/core.db" > "$tmp/$name"
    gh release upload "$core_tag" "$tmp/$name" && rm -f "$tmp/$name"
    echo "Uploaded $name"
    assets "$core_tag" '^core-.*[.]db[.]gz$' | sort | head -n -3 | while read -r old; do gh release delete-asset "$core_tag" "$old" -y; done
    for f in "$work"/raw/battles-*.db; do
      [ -e "$f" ] || continue
      [ "$f" -nt "$work/.downloaded" ] || continue
      base=$(basename "$f" .db); day=${base#battles-}; tag="raw-${day:0:7}"
      ensure_release "$tag" "Ranked games ${day:0:7}" "battles-YYYY-MM-DD.db.gz: every collected ranked game of that UTC day (SQLite: battles + appearances)."
      gzip -c -6 "$f" > "$tmp/$base.db.gz"
      gh release upload "$tag" "$tmp/$base.db.gz" --clobber && rm -f "$tmp/$base.db.gz"
      echo "Uploaded $base ($(du -h "$f" | cut -f1) raw)"
    done
    touch "$work/.downloaded"
    cutoff=$(date -u -d "-${RAW_KEEP_DAYS:-60} days" +%Y-%m)
    gh release list --limit 200 --json tagName --jq '.[].tagName' 2>/dev/null | grep -E '^raw-[0-9]{4}-[0-9]{2}$' |
      while read -r rel; do
        if [[ "${rel#raw-}" < "$cutoff" ]]; then gh release delete "$rel" --cleanup-tag -y && echo "Deleted old $rel"; fi
      done || true
    ;;
  *) echo "usage: $0 download|upload <work-dir>"; exit 1 ;;
esac
