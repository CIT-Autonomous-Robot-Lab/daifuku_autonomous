#!/usr/bin/env bash
# 走らせる場所を切り替える。
#
#   tools/site.sh                  今の値と、選べる名前を出す
#   tools/site.sh map_tsudanuma    書き換えて機体 (raspicat) を立て直す
#   tools/site.sh map_19f --no-restart   書き換えるだけ
#
# 場所が決まれば LiDAR の帯 (仰角と高さ) も emcl2 / 価値反復の調整も地図も決まる。
# その 1 つを src/daifuku_config_manager/config/site に置いてあり、機体側
# (docker compose で常駐) も人が立てる navigation も同じ値を見る。
#
# **機体側はこれを起動時にしか読まない。** だからこのスクリプトは書き換えたあと
# raspicat を立て直すところまでやる。ファイルだけ直して忘れると、LiDAR の帯だけが
# 前の場所のまま走る (エラーも警告も出ない)。navigation は人が立て直すので何もしない。
#
# restart で足りるのは、値が環境変数ではなくファイルにあるから。環境変数だと
# コンテナ生成時に焼かれるので `docker compose up -d` (作り直し) が要る。
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SITE_FILE=$ROOT/src/daifuku_config_manager/config/site
OVERRIDES_DIR=$ROOT/src/daifuku_config_manager/config/overrides
MAPS_DIR=$ROOT/src/daifuku_stack/maps

current() {
    # params.py の _read_site と同じ規則 (1 つめの空でない非コメント行)。
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
    echo "usage: tools/site.sh [<名前>] [--no-restart]" >&2
    echo "選べる名前:" >&2
    available | sed 's/^/  /' >&2
}

NAME=""
RESTART=yes
for arg in "$@"; do
    case "$arg" in
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

# 綴り違いは書く前に弾く。書いてしまうと、次に機体が上がるときまで気付けない。
if [ ! -f "$OVERRIDES_DIR/$NAME.yaml" ]; then
    echo "!! そんな場所はありません: $NAME" >&2
    echo "   ($OVERRIDES_DIR/$NAME.yaml が無い)" >&2
    usage
    exit 2
fi
# 地図が無いのはエラーにしない (navigation を立てるときに map:= を明示すれば通る)。
# ただし黙って進むと「なぜか地図が見つからない」で悩むので言っておく。
if [ ! -f "$MAPS_DIR/$NAME.yaml" ]; then
    echo "!  地図がありません ($MAPS_DIR/$NAME.yaml)。" >&2
    echo "   navigation は map:= を明示しないと起動時に止まります。" >&2
fi

BEFORE=$(current)
cat > "$SITE_FILE" <<EOF
# 今どこで走らせるか。**場所に関わる値はここ 1 つで決まる。**
#
#   * LiDAR の帯 (仰角と高さ)   … 機体側。docker compose で常駐している
#   * emcl2 と価値反復の調整      … 自律移動側。人が navigation を立てるとき
#   * 地図 (navigation の map:=) … 同名の maps/<名前>.yaml
#
# 名前は config/overrides/<名前>.yaml と daifuku_stack の maps/<名前>.yaml の
# 両方に対応する (2 つで名前を分けない)。
#
# **書き換えるのは tools/site.sh。** 機体側は起動時に読むので、ファイルを直しても
# 立て直さなければ変わらない。スクリプトはその立て直しまでやる。
#
#   tools/site.sh                 今の値と、選べる名前
#   tools/site.sh map_tsudanuma   書き換えて機体を立て直す
#
# 1 つめの空でない行 (# で始まらないもの) が値。
$NAME
EOF
echo "site: $BEFORE -> $NAME"

if [ "$RESTART" != yes ]; then
    echo "機体は立て直していません (--no-restart)。次に上がるまで LiDAR の帯は $BEFORE のままです。"
    exit 0
fi

# COMPOSE_FILE はリポジトリルートの .env にしか無いので、ここから叩く。
cd "$ROOT"
if ! command -v docker >/dev/null 2>&1; then
    echo "docker が無いので機体は立て直しません (開発ホスト)。" >&2
    exit 0
fi
if [ -z "$(docker compose ps -q raspicat 2>/dev/null)" ]; then
    echo "raspicat が起動していないので立て直しません (次の docker compose up で反映されます)。"
    exit 0
fi

echo "=== docker compose restart raspicat"
docker compose restart raspicat
echo "機体は $NAME で上がり直しました。**このとき機体は静止させておくこと**"
echo "(Mid-360 のジャイロの電源投入時バイアスを起動後の静止区間から測るため)。"
echo "navigation は立て直すだけで追随します (map も overrides も既定で $NAME)。"
