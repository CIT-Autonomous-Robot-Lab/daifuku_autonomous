#!/usr/bin/env bash
# 走らせる場所を切り替える。
#
#   tools/site.sh                  今の値と、選べる名前を出す
#   tools/site.sh map_tsudanuma    切り替える (機体は自分で上がり直す)
#   tools/site.sh map_19f --file-only   ファイルを書くだけ (ROS にも Docker にも触らない)
#
# 場所が決まれば LiDAR の帯 (仰角と高さ) も emcl2 / 価値反復の調整も地図も決まる。
# その 1 つを src/daifuku_config/site に置いてあり、機体側
# (docker compose で常駐) も人が立てる navigation も同じ値を見る。
#
# **切り替えの本体は ROS 側にある。** 機体で site_manager ノードが上がっていて、
#
#   ros2 param set /site_manager site <名前>
#
# を受けると、両方のパッケージについて「その場所で本当に立つか」を検査してから
# src/daifuku_config/site を書き、/daifuku/site へ流す。機体の launch に居る config_sentinel が
# それを見て、**機体が止まっていることを確かめてから**自分を終了し、compose の
# restart: unless-stopped が新しい設定で上げ直す。このスクリプトは
# その `ros2 param set` を叩くための薄い口で、**走行中に切り替えても
# その場では止まらない** (止まってから上がり直す)。
#
# ROS へ届かないとき (機体が上がっていない、開発ホスト) は、従来どおりファイルを
# 書いて raspicat を立て直す。**この 2 経路が同じ結果になるように、書き換えるのは
# どちらも「値の行 1 つ」だけ**にしてある (見出しのコメントは触らない)。
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SITE_FILE=$ROOT/src/daifuku_config/site
OVERRIDES_DIR=$ROOT/src/daifuku_config/overrides

current() {
    # params.read_site_file と同じ規則 (1 つめの空でない非コメント行)。
    sed -e 's/[[:space:]]*$//' "$SITE_FILE" 2>/dev/null \
        | grep -v '^[[:space:]]*#' | grep -v '^[[:space:]]*$' | head -n 1
}

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

# 値の行だけを差し替える (site_manager の write_site と同じことを sh でやる)。
write_site() {
    local name=$1 tmp
    tmp=$(mktemp "$(dirname "$SITE_FILE")/.site.XXXXXX")
    awk -v value="$name" '
        !done && !/^[[:space:]]*#/ && NF { print value; done = 1; next }
        { print }
        END { if (!done) print value }
    ' "$SITE_FILE" > "$tmp"
    # 同じディレクトリなので mv は rename(2) 1 つ = 書きかけを誰にも読ませない。
    mv "$tmp" "$SITE_FILE"
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

if [ -z "$NAME" ]; then
    echo "site: $(current)   ($SITE_FILE)"
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
# 地図は overrides の site: map: が持つ。**役ごとに 2 枚** (navigation -> /map、
# localization -> /map_loc)。無いのはエラーにしない (navigation を立てるときに
# map:= / map_loc:= を明示すれば通る) が、黙って進むと「なぜか地図が決まらない」
# で悩むので言っておく。片方だけのときは navigation が起動時に落ちる。
if ! awk '
        /^site:[[:space:]]*$/ { in_site = 1; next }
        /^[^[:space:]#]/      { in_site = 0 }
        in_site && /^[[:space:]]+navigation:/   { nav = 1 }
        in_site && /^[[:space:]]+localization:/ { loc = 1 }
        END { exit !(nav && loc) }
    ' "$OVERRIDES_DIR/$NAME.yaml"; then
    echo "!  $NAME.yaml の site: map: に navigation: / localization: が揃っていません。" >&2
    echo "   navigation は map:= / map_loc:= を明示しないと起動時に止まります。" >&2
fi

BEFORE=$(current)

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
write_site "$NAME"
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
