#!/usr/bin/env bash
# 種 01 静的検査 / 項 02 設定の整合。**ROS も Docker も要らない。**
#
# ここが見るのは「起動時にエラーで止まる」ものと「エラーも警告も出ないまま
# 効かない」ものの 2 種類。launch を立てる前にどちらも分かる。
#
# **読むだけ。** src/daifuku_config/ の下に書くと config_sentinel が指紋の変化で launch を
# 落とし、人が立てた navigation / mapping は終わったままになる。

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

section 0102 "設定の整合"

CONFIG="${ROOT}/src/daifuku_config"

require "src/daifuku_config/ がある" test -d "${CONFIG}"

# ── 場所 (src/daifuku_config/site) ──────────────────────────────────────────────────────
SITE="$(site_name)"

check_site_exists() {
  [[ -n "${SITE}" ]] || {
    echo "src/daifuku_config/site が空"
    return 1
  }
  echo "${SITE}"
  test -f "${CONFIG}/overrides/${SITE}.yaml"
}
require "src/daifuku_config/site の名前が overrides にある" check_site_exists

# **ファイルは名前 1 語だけ。** 2026-08-25 にコメント付きの書式をやめた。戻すと
# 「1 つめの空でない非コメント行が値」という規則を読み手 (params.read_site_file、
# checklist の site_name) と書き手 (site_manager.write_site、tools/site.sh) が
# また各々写すことになる。書式が無いから echo 1 つで切り替えられる。
check_site_bare() {
  local body
  body="$(cat "${CONFIG}/site")"
  [[ "${body}" == "${SITE}" ]] || {
    echo "src/daifuku_config/site に名前以外が入っている (コメント・空行・複数行)"
    return 1
  }
  echo "${SITE} (1 語だけ)"
}
item "src/daifuku_config/site が名前 1 語だけを持つ" check_site_bare

# 地図は「同じ名前の地図」ではなく、その overrides 自身の site: map: が決める。
# 無いと navigation は既定の地図へ落とさずに起動時で止まる (別の場所の地図で
# 自己位置を推定し始めるほうが危ないため)。2026-08-25 から **役ごとに 2 枚**
# (navigation: /map、localization: /map_loc)。片方だけだと起動時に落ちる。
check_site_map() {
  local ov="${CONFIG}/overrides/${SITE}.yaml" role map bad=0
  for role in navigation localization; do
    map="$(sed -n '/^site:/,/^[^ #]/p' "${ov}" \
           | sed -n "s/^[[:space:]]*${role}:[[:space:]]*//p" |
           sed 's/[[:space:]]*#.*$//; s/[[:space:]]*$//' | head -n 1)"
    [[ -n "${map}" ]] || {
      echo "site: map: ${role}: が無い → navigation が起動時に落ちる"
      return 1
    }
    echo "${role}: ${map}"
    test -f "${ROOT}/src/daifuku_stack/maps/${map}" || test -f "${map}" || bad=1
  done
  return "${bad}"
}
item "overrides の site: map: が指す地図 2 枚が実在する" check_site_map

# ── overrides の行き先 ──────────────────────────────────────────────────────
# 1 段目のパッケージ名、2 段目のノード名、nav2 断片の重複は起動時と同じ関数
# (overlay.audit_tree) で見る。sed/awk で YAML を拾うと段が深いときに検査だけ通る。
check_overrides_same_as_launch() {
  python3 "${ROOT}/tools/checklist/check_overrides.py" "${CONFIG}"
}
item "overrides の行き先と断片の重複が起動時と同じ規則で通る" check_overrides_same_as_launch

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
# src/daifuku_config/stack/vi_planner.yaml だけで、launch が持つのは「内蔵を使うか」
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
item "src/daifuku_config/stack と src/daifuku_config/overrides に standalone: / follow: / publish_tf: が無い" \
  check_no_launch_only_keys

# 自己位置推定側の map_server (map_server_loc) は設定ファイルの節を持たない。
# 足すと RewrittenYaml が yaml_filename を**キー名で**振り替えるので、2 つの
# map_server が同じ地図を読む。**エラーも警告も出ないまま地図が 1 枚に戻る。**
check_no_map_server_loc_section() {
  local d hit
  for d in "${CONFIG}/stack" "${CONFIG}/overrides"; do
    [[ -d "${d}" ]] || {
      echo "見る先が無い: ${d}"
      return 1
    }
  done
  hit="$(grep -rln '^[[:space:]]*map_server_loc:' \
    "${CONFIG}/stack" "${CONFIG}/overrides" 2>/dev/null | tr '\n' ' ')"
  [[ -z "${hit}" ]] || {
    echo "map_server_loc: が書かれている (2 つの map_server が同じ地図になる): ${hit}"
    return 1
  }
  echo "書かれていない"
}
item "src/daifuku_config/ に map_server_loc: の節が無い" check_no_map_server_loc_section

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
# 入口は name: daifuku-autonomous をわざと揃えてある。違えるとドライバを
# 替えた瞬間にビルドキャッシュの名前付きボリュームが別物になり、**1〜2 時間
# かけて建て直しになる** (include: された側の name: は無視されるので、揃える
# 必要があるのは入口の側)。入口を足したらこの一覧にも足すこと。
COMPOSE_ENTRIES=(compose.rt.yaml compose.original.yaml compose.none.yaml)
check_compose_name() {
  local f n names=()
  for f in "${COMPOSE_ENTRIES[@]}"; do
    n="$(sed -n 's/^name:[[:space:]]*//p' "${ROOT}/docker/raspberrypi/${f}" 2>/dev/null | head -n 1)"
    [[ -n "${n}" ]] || {
      echo "${f} に name: が無い"
      return 1
    }
    names+=("${n}")
  done
  echo "${names[*]}"
  for n in "${names[@]}"; do
    [[ "${n}" == "${names[0]}" ]] || return 1
  done
}
item "compose の入口 ${#COMPOSE_ENTRIES[@]} つで name: が揃っている" check_compose_name

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
  # 19f が true なので、引数を何も足さずに立てると踏む。
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
