#!/usr/bin/env bash
# 種 01 静的検査 / 項 02 設定の整合。**ROS も Docker も要らない。**
#
# ここが見るのは「起動時にエラーで止まる」ものと「エラーも警告も出ないまま
# 効かない」ものの 2 種類。launch を立てる前にどちらも分かる。
#
# **読むだけ。** config/ の下に書くと config_sentinel が指紋の変化で launch を
# 落とし、人が立てた navigation / mapping は終わったままになる。

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

section 0102 "設定の整合"

CONFIG="${ROOT}/config"
PARAMS_PY="${ROOT}/src/daifuku_config_manager/src/daifuku_config_manager/params.py"

require "config/ がある" test -d "${CONFIG}"

# ── 場所 (config/site) ──────────────────────────────────────────────────────
SITE="$(site_name)"

check_site_exists() {
  [[ -n "${SITE}" ]] || {
    echo "config/site が空"
    return 1
  }
  echo "${SITE}"
  test -f "${CONFIG}/overrides/${SITE}.yaml"
}
require "config/site の名前が overrides にある" check_site_exists

# 地図は「同じ名前の地図」ではなく、その overrides 自身の site: map: が決める。
# 無いと navigation は既定の地図へ落とさずに起動時で止まる (別の場所の地図で
# 自己位置を推定し始めるほうが危ないため)。
check_site_map() {
  local ov="${CONFIG}/overrides/${SITE}.yaml" map
  map="$(sed -n '/^site:/,/^[^ #]/p' "${ov}" | sed -n 's/^[[:space:]]*map:[[:space:]]*//p' | head -n 1)"
  [[ -n "${map}" ]] || {
    echo "site: map: が無い → navigation は map:= が必須になる"
    return 1
  }
  echo "${map}"
  test -f "${ROOT}/src/daifuku_stack/maps/${map}" || test -f "${map}"
}
item "overrides の site: map: が指す地図が実在する" check_site_map

# ── overrides の行き先 ──────────────────────────────────────────────────────
# 1 段目はパッケージ名か site:。知らない名前は「誰も読まない部分木」になるので
# params.py が起動時に落とす。ここで先に見つける。
known_packages() {
  sed -n '/^CONFIG_DIRS = {/,/^}/p' "${PARAMS_PY}" | grep -o '"[a-z_]*":' | tr -d '":'
}

check_override_toplevel() {
  local f key bad=() known
  known="$(known_packages) site"
  for f in "${CONFIG}"/overrides/*.yaml; do
    while read -r key; do
      grep -qw -- "${key}" <<<"${known}" || bad+=("${f##*/}:${key}")
    done < <(grep -o '^[a-z_][a-z_0-9]*:' "${f}" | tr -d ':')
  done
  ((${#bad[@]} == 0)) || {
    echo "知らない 1 段目: ${bad[*]}"
    return 1
  }
  echo "$(known_packages | tr '\n' ' ')site のみ"
}
item "overrides の 1 段目がパッケージ名か site: だけ" check_override_toplevel

# 2 段目はノード名。そのパッケージのどの設定ファイルにも無い名前は起動時に落ちる
# (綴り違いが黙って消えるのを防ぐため)。
declared_nodes() {
  # $1 = bringup | stack。設定ファイルの 1 段目 = ノード名。
  grep -ho '^[a-zA-Z_][a-zA-Z_0-9]*:' "${CONFIG}/$1"/*/*.yaml "${CONFIG}/$1"/*.yaml 2>/dev/null |
    tr -d ':' | sort -u
}

check_override_nodes() {
  local f pkg dir node bad=() nodes
  for f in "${CONFIG}"/overrides/*.yaml; do
    for pkg in $(known_packages); do
      dir="$(sed -n "s/^[[:space:]]*\"${pkg}\":[[:space:]]*\"\\([a-z]*\\)\".*/\\1/p" "${PARAMS_PY}" | head -n 1)"
      [[ -n "${dir}" ]] || continue
      nodes="$(declared_nodes "${dir}")"
      # そのパッケージの部分木 (1 段目 pkg: の次の 1 段目まで) の 2 段目を拾う。
      while read -r node; do
        [[ -n "${node}" ]] || continue
        grep -qx -- "${node}" <<<"${nodes}" || bad+=("${f##*/}:${pkg}:${node}")
      done < <(awk -v pkg="${pkg}:" '
        $0 == pkg { inpkg = 1; next }
        /^[^ \t#]/ { inpkg = 0 }
        inpkg && /^  [a-zA-Z_][a-zA-Z_0-9]*:[[:space:]]*$/ { gsub(/[ :]/, "", $0); print }
      ' "${f}")
    done
  done
  ((${#bad[@]} == 0)) || {
    echo "行き先の無いノード名: ${bad[*]}"
    return 1
  }
  echo "すべて行き先がある"
}
item "overrides の 2 段目のノード名に行き先がある" check_override_nodes

# ── nav2 の断片 ─────────────────────────────────────────────────────────────
# ファイル名順に 1 つの params_file へ束ねられる。**同じノード名が 2 つの断片に
# あると起動時にエラーで止まる** (キーが重なっていなくても止まる)。
check_nav2_dup() {
  local dup
  dup="$(grep -ho '^[a-zA-Z_][a-zA-Z_0-9]*:' "${CONFIG}"/stack/nav2/*.yaml |
    tr -d ':' | sort | uniq -d | tr '\n' ' ')"
  [[ -z "${dup}" ]] || {
    echo "2 つの断片に居る: ${dup}"
    return 1
  }
  echo "重複なし"
}
item "config/stack/nav2/*.yaml にノード名の重複が無い" check_nav2_dup

# standalone を設定に書くと、Nav2 構成で立てたとき navigate_to_pose のサーバが
# bt_navigator と 2 つになる。**どちらに繋がったかはログにも ros2 action list にも
# 出ない。** follow も同じで、nav2:=false (既定) では navigation.launch.py が
# vi_planner を直に立てて follow を渡さないので、**follow: false と書いてあると
# follow_path のサーバが立たないまま上がり、機体が黙って追従しなくなる**。
# vi_planner の publish_tf も同じ側。真になるのは localization:=vi (emcl2 を立てない
# 構成) だけで、設定に書いて emcl2 構成で真になると map->odom の出し手が 2 人になり、
# **エラーも警告も出ないまま自己位置だけが壊れる**。どれも渡すのは launch だけ。
# **見るのは自律移動側 (stack/ と overrides/) だけ** — 同じ名前のキーが機体側の
# EKF と駆動ドライバにもあり、あちらは正しく設定で持つもの (odom->base_footprint)。
#
# **localizer は逆にここに書く側**。「どの推定器を使うか」を持つのは
# config/stack/nav2/vi_planner.yaml だけで、launch が持つのは「内蔵を使うか」
# (localization:=vi) だけ。噛み合わなければ backends.validate_localization が
# 起動時に止めるので、この検査の対象には**入れない**。
check_no_launch_only_keys() {
  local d hit
  # 見る先が無いと grep は stderr へ書いて空を返す = **黙って合格**になる。
  for d in "${CONFIG}/stack" "${CONFIG}/overrides"; do
    [[ -d "${d}" ]] || {
      echo "見る先が無い: ${d}"
      return 1
    }
  done
  hit="$(grep -rlnE '^[[:space:]]*(standalone|follow|publish_tf):' \
    "${CONFIG}/stack" "${CONFIG}/overrides" 2>/dev/null | tr '\n' ' ')"
  [[ -z "${hit}" ]] || {
    echo "standalone: / follow: / publish_tf: が書かれている: ${hit}"
    return 1
  }
  echo "書かれていない"
}
item "config/stack と config/overrides に standalone: / follow: / publish_tf: が無い" \
  check_no_launch_only_keys

# ── .env ────────────────────────────────────────────────────────────────────
ROOT_ENV="${ROOT}/.env"
PI_ENV="${ROOT}/docker/raspberrypi/.env"

if [[ -f "${ROOT_ENV}" ]]; then
  item "ルートの .env に COMPOSE_FILE がある" grep -q '^COMPOSE_FILE=' "${ROOT_ENV}"

  # .env は 2 つ読まれ、同じキーは docker/raspberrypi/.env が勝つ。実機で
  # 「ルートの .env を直したのに効かない」はこれ。
  check_env_shadow() {
    local dup
    [[ -f "${PI_ENV}" ]] || {
      echo "docker/raspberrypi/.env は無い"
      return 0
    }
    dup="$(comm -12 \
      <(grep -o '^[A-Z_][A-Z_0-9]*=' "${ROOT_ENV}" | sort -u) \
      <(grep -o '^[A-Z_][A-Z_0-9]*=' "${PI_ENV}" | sort -u) | tr -d '=' | tr '\n' ' ')"
    [[ -z "${dup}" ]] || {
      echo "両方にあり docker/raspberrypi/.env が勝つ: ${dup}"
      return 1
    }
    echo "重なりなし"
  }
  item "ルートと docker/raspberrypi/ の .env でキーが重なっていない" check_env_shadow
else
  skip "ルートの .env" ".env が無い (.env.example から作る)"
fi

# ── compose の入口 ──────────────────────────────────────────────────────────
# 入口 2 つは name: daifuku-autonomous をわざと揃えてある。違えるとドライバを
# 替えた瞬間にビルドキャッシュの名前付きボリュームが別物になり、**1〜2 時間
# かけて建て直しになる** (include: された側の name: は無視されるので、揃える
# 必要があるのは入口の側)。
check_compose_name() {
  local f n names=()
  for f in compose.rt.yaml compose.original.yaml; do
    n="$(sed -n 's/^name:[[:space:]]*//p' "${ROOT}/docker/raspberrypi/${f}" 2>/dev/null | head -n 1)"
    [[ -n "${n}" ]] || {
      echo "${f} に name: が無い"
      return 1
    }
    names+=("${n}")
  done
  echo "${names[*]}"
  [[ "${names[0]}" == "${names[1]}" ]]
}
item "compose の入口 2 つで name: が揃っている" check_compose_name

# ── 機種と設定の取り違え ────────────────────────────────────────────────────
MODEL="$(pi_model)"
if [[ -z "${MODEL}" ]]; then
  skip "機種とドライバの組み合わせ" "Raspberry Pi ではない (開発ホスト)"
else
  check_driver_choice() {
    local cf
    cf="$(grep -h '^COMPOSE_FILE=' "${ROOT_ENV}" 2>/dev/null | tail -n 1)"
    echo "${MODEL} / ${cf:-COMPOSE_FILE 未設定}"
    if is_pi5 && [[ "${cf}" == *"compose.rt.yaml" ]]; then
      echo "Pi 5 に公式実装 (rtmouse) — configure で落ちる。compose.original.yaml へ"
      return 1
    fi
    return 0
  }
  item "機種とドライバの組み合わせが合っている" check_driver_choice

  # rtmouse (公式実装用) と driver:=original は排他。カーネルは衝突を検出しない
  # ので、両方が GPIO 16/6/5 を持つと車輪が逆に回り得る。
  check_rtmouse_exclusive() {
    local loaded cf
    loaded="$(lsmod 2>/dev/null | grep -c '^rtmouse')"
    cf="$(grep -h '^COMPOSE_FILE=' "${ROOT_ENV}" 2>/dev/null | tail -n 1)"
    if ((loaded > 0)) && [[ "${cf}" == *"compose.original.yaml" ]]; then
      echo "rtmouse が載ったまま自前実装を選んでいる (GPIO の二重掴み)"
      return 1
    fi
    echo "rtmouse=$((loaded > 0 ? 1 : 0)) / ${cf##*/}"
  }
  item "rtmouse と自前ドライバが同居していない" check_rtmouse_exclusive

  # Pi 4 (4GB) で waypoint_prefetch:=true は価値関数が 2 つ生きる。既定の
  # map_19f が true なので、引数を何も足さずに立てると踏む。
  if is_pi4; then
    check_prefetch() {
      local ov="${CONFIG}/overrides/${SITE}.yaml"
      grep -q '^[[:space:]]*waypoint_prefetch:[[:space:]]*true' "${ov}" || {
        echo "${SITE} は false"
        return 0
      }
      echo "${SITE} が true — 4GB 機では場が 2 本になる。overrides から外すこと"
      return 1
    }
    item "Pi 4 で waypoint_prefetch が true になっていない" check_prefetch
  fi
fi

finish
