#!/bin/bash
# Download VS1..VS20 of AI Hub dataset 515 one at a time, keep only the clips
# in clips.txt, and delete each zip before the next one (each is 1-11 GB).
#
# usage: bash download_clips.sh <work folder>
#   clips end up in <work folder>/clips. Needs an AI Hub API key
#   (AIHUB_APIKEY env var, or typed at the prompt). Re-run to resume.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="$1"
[ -n "$WORK" ] || { echo "usage: bash download_clips.sh <work folder>"; exit 1; }
mkdir -p "$WORK/clips"
cd "$WORK"

# AI Hub file keys of the validation video zips (aihubshell -mode l -datasetkey 515)
KEYS=(- 53048 53059 53061 53062 53063 53064 53065 53066 53067 53049
        53050 53051 53052 53053 53054 53055 53056 53057 53058 53060)

[ -f aihubshell ] || curl -s -o aihubshell https://api.aihub.or.kr/api/aihubshell.do
if [ -z "$AIHUB_APIKEY" ]; then
    read -rsp "AI Hub API key: " AIHUB_APIKEY; echo
    export AIHUB_APIKEY
fi

# Largest zip first: aihubshell holds the tar, its parts and the merged zip at once
# (about 3x the zip), so the 11 GB ones must run before clips/ fills up.
for n in 18 17 19 3 16 20 1 4 5 7 8 2 12 6 11 10 13 15 9 14; do
    [ -f "clips/.VS$n.done" ] && continue
    # aihubshell does not resume: a tar left by a closed window would only take up space
    rm -f download*.tar
    bash aihubshell -mode d -datasetkey 515 -filekey "${KEYS[$n]}"
    zip=$(find . -name "VS$n.zip" | head -1)
    [ -n "$zip" ] || { echo "VS$n.zip was not downloaded"; exit 1; }
    python "$HERE/extract_clips.py" clips "$zip"
    rm "$zip"
    touch "clips/.VS$n.done"
done
