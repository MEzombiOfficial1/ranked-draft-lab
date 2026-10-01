#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${CLOUDFLARE_API_TOKEN:?CLOUDFLARE_API_TOKEN is not set}"
api=https://api.cloudflare.com/client/v4
cf() { curl -sS -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" -H "Content-Type: application/json" "$@"; }

if [ -z "${CLOUDFLARE_ACCOUNT_ID:-}" ]; then
  CLOUDFLARE_ACCOUNT_ID=$(cf "$api/accounts?per_page=5" | jq -r '.result[0].id // empty')
  [ -n "$CLOUDFLARE_ACCOUNT_ID" ] || { echo "::error::Could not find a Cloudflare account for this token."; exit 1; }
fi
export CLOUDFLARE_ACCOUNT_ID

sub=$(cf "$api/accounts/$CLOUDFLARE_ACCOUNT_ID/workers/subdomain" | jq -r '.result.subdomain // empty')
if [ -z "$sub" ]; then
  want="rdl-$(echo "${GITHUB_REPOSITORY_OWNER:-draft}" | tr 'A-Z' 'a-z' | tr -cd 'a-z0-9' | cut -c1-24)"
  sub=$(cf -X PUT "$api/accounts/$CLOUDFLARE_ACCOUNT_ID/workers/subdomain" --data "{\"subdomain\":\"$want\"}" | jq -r '.result.subdomain // empty')
  [ -n "$sub" ] || { echo "::error::Could not create a workers.dev subdomain."; exit 1; }
  echo "Created workers.dev subdomain: $sub"
fi

rm -rf dist && cp -r site dist && rm -rf dist/test && mkdir -p dist/data && cp data/stats/*.json dist/data/

cache="${RUNNER_TEMP:-/tmp}/img-cache"; mkdir -p "$cache/brawler" "$cache/map" dist/img/brawler dist/img/map
{ jq -r '.brawler_names | keys[] | "brawler \(.)"' data/stats/summary.json
  jq -r '.maps[] | select(.event_id != null) | "map \(.event_id)"' data/stats/maps.json; } | sort -u |
  xargs -P 16 -L 1 sh -c '
    kind=$0; id=$1; f="'"$cache"'/$kind/$id.png"
    [ -s "$f" ] && exit 0
    if [ "$kind" = brawler ]; then url="https://cdn.brawlify.com/brawlers/borderless/$id.png"; else url="https://cdn.brawlify.com/maps/regular/$id.png"; fi
    curl -sfL --max-time 20 -o "$f.tmp" "$url" && mv "$f.tmp" "$f" || rm -f "$f.tmp"' || true
jq -r '.brawler_names | to_entries[] | "\(.key)\t\(.value)"' data/stats/summary.json | while IFS=$'\t' read -r id name; do
  f="$cache/brawler/$id.png"; [ -s "$f" ] && continue
  file=$(echo "$name" | tr 'A-Z' 'a-z' | sed -E 's/(^|[ -])([a-z])/\1\u\2/g; s/ /_/g')1-pfp.png
  src=$(curl -sf --max-time 20 "https://brawlstars.fandom.com/api.php?action=query&titles=File:$file&prop=imageinfo&iiprop=url&format=json" |
        jq -r '.query.pages[].imageinfo[0].url // empty' 2>/dev/null) || src=""
  [ -n "$src" ] && curl -sfL --max-time 20 -A "Mozilla/5.0" -o "$f.tmp" "$src" && mv "$f.tmp" "$f" || rm -f "$f.tmp"
done || true
cp "$cache"/brawler/*.png dist/img/brawler/ 2>/dev/null || true
cp "$cache"/map/*.png dist/img/map/ 2>/dev/null || true
echo "Bundled images: $(ls dist/img/brawler | wc -l) brawlers, $(ls dist/img/map | wc -l) maps"
config=worker/wrangler.toml
db_id=$(cf "$api/accounts/$CLOUDFLARE_ACCOUNT_ID/d1/database?name=ranked-edge" | jq -r '[.result[]? | select(.name == "ranked-edge") | .uuid][0] // empty')
if [ -z "$db_id" ]; then
  db_id=$(cf -X POST "$api/accounts/$CLOUDFLARE_ACCOUNT_ID/d1/database" --data '{"name":"ranked-edge"}' | jq -r '.result.uuid // empty')
  [ -n "$db_id" ] && echo "Created D1 database ranked-edge ($db_id)"
fi
if [ -n "$db_id" ]; then
  node -e 'import("./worker/src/collector.js").then((m) => process.stdout.write(m.SCHEMA))' | tr '\n' ' ' | tr ';' '\n' |
    while read -r stmt; do
      [ -n "${stmt// }" ] || continue
      cf -X POST "$api/accounts/$CLOUDFLARE_ACCOUNT_ID/d1/database/$db_id/query" --data "$(jq -n --arg sql "$stmt" '{sql: $sql}')" |
        jq -e '.success' >/dev/null || echo "::warning::D1 schema statement failed: $stmt"
    done
  config=worker/wrangler.generated.toml
  { cat worker/wrangler.toml
    printf '\n[[d1_databases]]\nbinding = "DB"\ndatabase_name = "ranked-edge"\ndatabase_id = "%s"\n\n[triggers]\ncrons = ["* * * * *"]\n' "$db_id"
  } > "$config"
  echo "Edge collector enabled (D1 $db_id, cron every minute)"
else
  echo "::warning::Could not create or find the D1 database (token needs D1 edit permission): edge collector disabled."
fi

npx --yes wrangler@4 deploy --config "$config"
if [ -n "${BRAWL_STARS_API_KEY:-}" ]; then
  if [ "${FORCE_SECRET:-}" = "1" ] || ! npx --yes wrangler@4 secret list --config "$config" 2>/dev/null | grep -q '"BRAWL_STARS_API_KEY"'; then
    printf '%s' "$BRAWL_STARS_API_KEY" | npx --yes wrangler@4 secret put BRAWL_STARS_API_KEY --config "$config" >/dev/null
    echo "Worker secret BRAWL_STARS_API_KEY set."
  fi
fi
url="https://ranked-draft.$sub.workers.dev"
echo "Site: $url"
echo "url=$url" >> "${GITHUB_OUTPUT:-/dev/null}"

sleep 5
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
page=$(code "$url/"); data=$(code "$url/data/summary.json")
echo "Smoke test: page=$page data=$data health=$(curl -s "$url/api/health") edge=$(curl -s "$url/api/collector/status")"
map_id=$(jq -r '[.maps[].event_id | select(. != null)][0] // 15000025' data/stats/maps.json)
echo "  images: brawler=$(curl -s -o /dev/null -w '%{http_code} %{content_type}' "$url/img/brawler/16000000.png") map=$(curl -s -o /dev/null -w '%{http_code} %{content_type}' "$url/img/map/$map_id.png")"
echo "  player lookup: $(curl -s "$url/api/player/${SMOKE_TAG:-9CCU092V}" | jq -c '{name, brawlers: (.brawlers | length?), error}' 2>/dev/null)"
echo "  AI coach: $(curl -s -X POST -H 'content-type: application/json' \
  -d '{"map":"Smoke test","phase":"our pick","ourPicks":[],"enemyPicks":[],"suggestions":[{"brawler":"Spike"}]}' \
  "$url/api/ai" | jq -c '{model, error, text: ((.text // "") | .[0:120])}' 2>/dev/null)"
[ "$page" = 200 ] && [ "$data" = 200 ] || { echo "::error::Live site smoke test failed"; exit 1; }
