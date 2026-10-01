#!/usr/bin/env bash
set -euo pipefail
branch=${GITHUB_REF_NAME:?}
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add data/stats
if git diff --cached --quiet; then echo "Stats unchanged."; exit 0; fi
git commit -qm "Update ranked stats $(date -u +%Y-%m-%dT%H:%MZ)"
for i in 1 2 3 4; do
  if git pull -q --rebase origin "$branch" && git push -q origin "HEAD:$branch"; then echo "Stats pushed."; exit 0; fi
  sleep $((2 ** i))
done
echo "Could not push stats." >&2
exit 1
