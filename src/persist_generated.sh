#!/usr/bin/env bash
# Commit generated state before rebasing; never suppress conflicts or discard data.
set -euo pipefail
git config user.name 'github-actions[bot]'
git config user.email 'github-actions[bot]@users.noreply.github.com'
for path in bhav_data db docs output; do
  if [ -d "$path" ]; then git add -A -- "$path"; fi
done
git diff --cached --quiet || git commit -m "Pipeline checkpoint/data: $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
for attempt in 1 2 3 4 5; do
  git fetch origin main
  git rebase origin/main || { git rebase --abort; echo 'Concurrent data conflict: preserved local commit; refusing destructive merge'; exit 1; }
  if git -c http.version=HTTP/1.1 push origin HEAD:main; then exit 0; fi
  sleep $((attempt * 5))
done
echo 'Could not persist generated state after 5 attempts'
exit 1
