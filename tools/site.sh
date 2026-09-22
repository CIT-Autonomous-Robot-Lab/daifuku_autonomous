#!/usr/bin/env bash
# 走らせる場所を切り替える。**便利口であって、これでなければ切り替えられないわけではない。**
#
#   tools/site.sh                   今の値と、選べる名前を出す
#   tools/site.sh tsudanuma         切り替える
#   tools/site.sh 19f --file-only   ファイルを書くだけ (ROS に触らない)
#
# src/daifuku_config/site は名前 1 語しか持たないので、同じことは
#
#   ros2 param set /site_manager site tsudanuma   機体が上がっているとき (検査して書いて流す)
#   echo tsudanuma > src/daifuku_config/site      上がっていないとき (開発ホスト)
#
# でもできる。このスクリプトがやるのは**その 2 経路の選び分け**だけ。ROS 経由なら
# site_manager が両方のパッケージについて検査してから書き、/daifuku/site へ流す。
#
# **機体 (raspicat サービス) は立て直さない。** 2026-08-25 に /scan を作る段が
# daifuku_stack へ移ってから、機体には場所ごとに変わる設定が 1 つも無い
# (robot_bringup.launch.py の見張りも watch_site=False で場所を見ない)。
# **反映するには navigation / mapping を立て直すこと** — 帯も仰角も地図も
# emcl2 も VI も、みなそちらが起動時に読む。
#
# 場所と設定の関係は src/daifuku_config/README.md。
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SITE_FILE=$ROOT/src/daifuku_config/site
OVERRIDES_DIR=$ROOT/src/daifuku_config/overrides

available() {
    # glob で列挙する。find -printf は GNU 拡張で、無い環境では**空の一覧**になり、
    # 下の検査があらゆる名前を弾くようになる (綴りが正しくても通らない)。
    local path name
    for path in "$OVERRIDES_DIR"/*.yaml; do
        [ -e "$path" ] || continue
        name=${path##*/}
        echo "${name%.yaml}"
    done | sort
}

usage() {
    echo "usage: tools/site.sh [<名前>] [--file-only]" >&2
    echo "選べる名前:" >&2
    available | sed 's/^/  /' >&2
}

NAME=""
FILE_ONLY=no
for arg in "$@"; do
    case "$arg" in
        --file-only) FILE_ONLY=yes ;;
        -h|--help) usage; exit 0 ;;
        -*) echo "!! 知らないオプション: $arg" >&2; usage; exit 2 ;;
        *)
            if [ -n "$NAME" ]; then
                echo "!! 名前は 1 つだけです: $NAME と $arg" >&2
                exit 2
            fi
            NAME=$arg
            ;;
    esac
done

BEFORE=$(cat "$SITE_FILE" 2>/dev/null | tr -d '[:space:]')

if [ -z "$NAME" ]; then
    echo "site: $BEFORE   ($SITE_FILE)"
    echo "選べる名前:"
    available | sed 's/^/  /'
    exit 0
fi

# 綴り違いは書く前に弾く。書いてしまうと、次に機体が上がるときまで気付けない
# (ROS 経由なら site_manager がもっと深く検査するが、こちらは Docker が無くても
# 通る道なので、ここでも見ておく)。
if [ ! -f "$OVERRIDES_DIR/$NAME.yaml" ]; then
    echo "!! そんな場所はありません: $NAME" >&2
    echo "   ($OVERRIDES_DIR/$NAME.yaml が無い)" >&2
    usage
    exit 2
fi

# ── ROS 経由 (機体が上がっているとき) ────────────────────────────────────────
# COMPOSE_FILE はリポジトリルートの .env にしか無いので、ここから叩く。
cd "$ROOT"
if [ "$FILE_ONLY" = no ] && command -v docker >/dev/null 2>&1 \
        && [ -n "$(docker compose ps -q raspicat 2>/dev/null)" ]; then
    echo "=== ros2 param set /site_manager site $NAME"
    # `ros2 param set` は失敗しても 0 で返すことがあるので、出力で見る。
    OUT=$(docker compose exec -T raspicat /ros_entrypoint.sh \
            ros2 param set /site_manager site "$NAME" 2>&1 || true)
    echo "$OUT"
    if printf '%s' "$OUT" | grep -q "successful"; then
        echo "site: $BEFORE -> $NAME"
        echo "**機体はそのままです** (場所ごとに変わる設定を持たないため)。"
        echo "navigation / mapping を立て直すと反映されます (map も overrides も既定で $NAME)。"
        exit 0
    fi
    echo "!  site_manager へ届きませんでした。ファイルを直接書きます。" >&2
fi

# ── ファイル経由 (開発ホスト、機体が上がっていないとき) ──────────────────────
# 同じディレクトリへ書いてから mv = rename(2) 1 つ。書きかけを誰にも読ませない
# (読み手が truncate の隙に空を読むと、黙って既定の場所で立ち上がる)。
TMP=$(mktemp "$(dirname "$SITE_FILE")/.site.XXXXXX")
printf '%s\n' "$NAME" > "$TMP"
mv "$TMP" "$SITE_FILE"
echo "site: $BEFORE -> $NAME   ($SITE_FILE)"

if [ "$FILE_ONLY" = yes ]; then
    echo "ROS には触っていません (--file-only)。"
fi
echo "navigation / mapping を立て直すと反映されます (map も overrides も既定で $NAME)。"
