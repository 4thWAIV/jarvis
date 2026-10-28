#!/usr/bin/env bash
# Build the team server's deploy package: this folder's service files plus
# build_info.json naming the exact commit, from a clean Git checkout only.
#   bash team_server/package.sh /absolute/output/dir
# Then deploy with the prepared wrapper:
#   bash .agents/in-progress/team-server-deploy.sh staging /absolute/output/dir
set -euo pipefail
out="${1:?Give an empty output directory}"
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(git -C "$here" rev-parse --show-toplevel)"
if [ -n "$(git -C "$repo" status --porcelain -- team_server)" ]; then
  echo "team_server/ has uncommitted changes; commit first so the image names a real commit." >&2
  exit 2
fi
commit="$(git -C "$repo" rev-parse HEAD)"
if [ -e "$out" ] && [ -n "$(ls -A "$out")" ]; then
  echo "Output directory is not empty: $out" >&2
  exit 2
fi
mkdir -p "$out"
git -C "$repo" archive "$commit" team_server/Dockerfile team_server/requirements.txt \
    team_server/railway.json team_server/.dockerignore team_server/teamserver \
  | tar -x -C "$out" --strip-components=1
printf '{"commit": "%s"}\n' "$commit" > "$out/build_info.json"
echo "packaged $commit into $out"
