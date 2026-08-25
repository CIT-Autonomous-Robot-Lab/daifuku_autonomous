#!/usr/bin/env bash
# 走らせる場所を切り替える。**便利口であって、これでなければ切り替えられないわけではない。**
#
#   tools/site.sh                   今の値と、選べる名前を出す
#   tools/site.sh tsudanuma         切り替える (機体は自分で上がり直す)
#   tools/site.sh 19f --file-only   ファイルを書くだけ (ROS にも Docker にも触らない)
#
# src/daifuku_config/site は名前 1 語しか持たないので、同じことは
#
#   ros2 param set /site_manager site tsudanuma   機体が上がっているとき (検査して書いて流す)
#   echo tsudanuma > src/daifuku_config/site      上がっていないとき (開発ホスト)
#
# でもできる。このスクリプトがやるのは**その 2 経路の選び分けと、ROS へ届かなかった
# ときの `docker compose restart raspicat`** だけ。機体側 (LiDAR の帯) は起動時にしか
# 読まないので、ファイルを書いただけでは変わらない。
#
# ROS 経由なら site_manager が両方のパッケージについて検査してから書き、/daifuku/site
# へ流す。機体の launch に居る config_sentinel がそれを見て、**機体が止まっている
# ことを確かめてから**自分を終了し、compose の restart: unless-stopped が新しい設定で
# 上げ直す。だから**走行中に切り替えてもその場では止まらない**。
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
    echo "usage: tools/site.sh [<名前>] [--file-only] [--no-restart]" >&2
    echo "選べる名前:" >&2
    available | sed 's/^/  /' >&2
}

NAME=""
FILE_ONLY=no
RESTART=yes
for arg in "$@"; do
    case "$arg" in
        --file-only) FILE_ONLY=yes ;;
        --no-restart) RESTART=no ;;
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
        echo "機体は**止まっているのを確かめてから**自分で上がり直します"
        echo "(config_sentinel。走行中なら止まるまで待つ。ログは docker compose logs -f raspicat)。"
        echo "**そのとき機体は静止させておくこと** (Mid-360 のジャイロの電源投入時"
        echo "バイアスを起動後の静止区間から測るため)。"
        echo "navigation は立て直すだけで追随します (map も overrides も既定で $NAME)。"
        exit 0
    fi
    echo "!  site_manager へ届きませんでした。ファイルを書いて立て直します。" >&2
fi

# ── ファイル経由 (開発ホスト、機体が上がっていないとき) ──────────────────────
# 同じディレクトリへ書いてから mv = rename(2) 1 つ。書きかけを誰にも読ませない
# (読み手が truncate の隙に空を読むと、黙って既定の場所で立ち上がる)。
TMP=$(mktemp "$(dirname "$SITE_FILE")/.site.XXXXXX")
printf '%s\n' "$NAME" > "$TMP"
mv "$TMP" "$SITE_FILE"
echo "site: $BEFORE -> $NAME   ($SITE_FILE)"

if [ "$FILE_ONLY" = yes ]; then
    echo "ROS にも Docker にも触っていません (--file-only)。"
    exit 0
fi
if ! command -v docker >/dev/null 2>&1; then
    echo "docker が無いので機体は立て直しません (開発ホスト)。" >&2
    exit 0
fi
if [ -z "$(docker compose ps -q raspicat 2>/dev/null)" ]; then
    echo "raspicat が起動していないので立て直しません (次の docker compose up で反映されます)。"
    exit 0
fi
if [ "$RESTART" != yes ]; then
    echo "機体は立て直していません (--no-restart)。次に上がるまで LiDAR の帯は $BEFORE のままです。"
    exit 0
fi

echo "=== docker compose restart raspicat"
docker compose restart raspicat
echo "機体は $NAME で上がり直しました。**このとき機体は静止させておくこと**"
echo "(Mid-360 のジャイロの電源投入時バイアスを起動後の静止区間から測るため)。"
echo "navigation は立て直すだけで追随します (map も overrides も既定で $NAME)。"
