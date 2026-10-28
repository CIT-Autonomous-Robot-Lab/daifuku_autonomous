#!/usr/bin/env bash
# 次回起動する場所を選び、対応する公開入口を表示する。実行中の構成は変更しない。
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SITE_FILE=$ROOT/src/daifuku_config/site
SITES_DIR=$ROOT/src/daifuku_config/sites

available() {
    local path name
    for path in "$SITES_DIR"/*/overrides.yaml; do
        [ -f "$path" ] || continue
        name=${path%/overrides.yaml}
        printf '%s\n' "${name##*/}"
    done | sort
}

usage() {
    echo "usage: tools/site.sh [<場所>]" >&2
    available >&2
}

if [[ $# -gt 1 ]]; then
    usage
    exit 2
fi
if [[ ${1:-} == -h || ${1:-} == --help ]]; then
    usage
    exit 0
fi

if [[ $# == 0 ]]; then
    printf '次回の選択: %s\n' "$(tr -d '[:space:]' < "$SITE_FILE")"
    available
    exit 0
fi

name=$1
if [[ ! $name =~ ^[a-zA-Z0-9_-]+$ || ! -f "$SITES_DIR/$name/overrides.yaml" ]]; then
    printf '!! 知らない場所: %s\n' "$name" >&2
    usage
    exit 2
fi
tmp=$(mktemp "$(dirname "$SITE_FILE")/.site.XXXXXX")
trap 'rm -f "$tmp"' EXIT
printf '%s\n' "$name" > "$tmp"
mv "$tmp" "$SITE_FILE"
printf '次回の入口: ros2 launch daifuku_bringup navigation.%s.launch.py\n' "$name"
echo "実行中の navigation と機体は変更していません。"
