#!/usr/bin/env bash
# 種 01 静的検査 / 項 03 lint とファイルをまたぐ約束。**ROS も Docker も要らない。**
#
# 見るのは 2 種類。**colcon test が落ちる**もの (ヘッダ・lint の顔ぶれ) と、
# **1 か所だけ直すと噛み合わなくなる**もの (見張りの立て方、トピック名)。
# どちらも 1 つのファイルを開いただけでは分からないので、ここで突き合わせる。

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

section 0103 "lint とファイルをまたぐ約束"

PARAMS_PY="${ROOT}/src/daifuku_config_manager/src/daifuku_config_manager/params.py"

require "git のワークツリーである" git -C "${ROOT}" rev-parse --is-inside-work-tree

# ── ライセンスヘッダ ────────────────────────────────────────────────────────
# ament_copyright は**最初のコメント塊しか見ない**。.hpp の #ifndef や launch 冒頭の
# 説明コメントの下に置くと、ファイルの中にヘッダがあっても見つからない。だから
# 「在るか」ではなく「先頭に在るか」で見る (shebang だけは飛ばしてよい)。
check_copyright_header() {
  local f first bad=()
  while read -r f; do
    # package.xml の隣の setup.py は ament_copyright の対象外。
    [[ "${f##*/}" == "setup.py" ]] && continue
    first="$(awk 'NF == 0 { next } /^#!/ { next } { print; exit }' "${ROOT}/${f}")"
    [[ "${first}" == "# Copyright"* || "${first}" == "// Copyright"* ]] || bad+=("${f}")
  done < <(git -C "${ROOT}" ls-files -- "${OWN_PKGS[@]}" | grep -E '\.(py|cpp|hpp)$')
  ((${#bad[@]} == 0)) || {
    echo "先頭にヘッダが無い: ${bad[*]}"
    return 1
  }
  echo "$(git -C "${ROOT}" ls-files -- "${OWN_PKGS[@]}" | grep -cE '\.(py|cpp|hpp)$') 本すべて先頭にある"
}
item "自前パッケージの .py / .cpp / .hpp が先頭に Apache-2.0 ヘッダを持つ" check_copyright_header

# 詰め合わせ (ament_lint_common) は戻さない。C++ の書式 lint が
# daifuku_waypoint_manager の移植コードで落ち、pep257 は「。」で終わる日本語
# docstring を全部弾く (2026-08-07 の実測で 330 件)。
check_no_lint_common() {
  local f hit=()
  for f in "${OWN_PKGS[@]}"; do
    [[ -f "${ROOT}/${f}/package.xml" ]] || continue
    # 見るのは要素の中身だけ。「使わない」と書いたコメントに名前が出るので、
    # ただの grep だと自分の説明文で落ちる。
    grep -qE '<[a-z_]*depend>(ament_lint_common|ament_cmake_pep257|ament_pep257)</' \
      "${ROOT}/${f}/package.xml" && hit+=("${f##*/}")
  done
  ((${#hit[@]} == 0)) || {
    echo "詰め合わせが戻っている: ${hit[*]}"
    return 1
  }
  echo "名指しのまま"
}
item "package.xml に ament_lint_common / pep257 が入っていない" check_no_lint_common

# ament_python では test/test_*.py が lint の実体。test_depend に足すだけでは
# **何も走らない** (2026-08-07 まで daifuku_rqt が宣言だけで、lint が一度も
# 走っていなかった)。
check_python_lint_runs() {
  local f bad=() n=0
  for f in "${OWN_PKGS[@]}"; do
    [[ -f "${ROOT}/${f}/setup.py" ]] || continue
    grep -qE 'ament_copyright|ament_flake8' "${ROOT}/${f}/package.xml" 2>/dev/null || continue
    n=$((n + 1))
    compgen -G "${ROOT}/${f}/test/test_*.py" >/dev/null || bad+=("${f##*/}")
  done
  ((${#bad[@]} == 0)) || {
    echo "宣言だけで test/ が無い: ${bad[*]} — lint は 1 度も走らない"
    return 1
  }
  echo "${n} つとも test/test_*.py を持つ"
}
item "ament_python のパッケージが test/test_*.py を持つ" check_python_lint_runs

# ── 見張り (config_sentinel) の立て方 ───────────────────────────────────────
# 落とす合図が 0 だと、OnProcessExit → EmitEvent(Shutdown) がノードのバグでの
# 異常終了でも発火する。restart: unless-stopped と組んで止まらなくなる。
check_sentinel_code() {
  local v
  v="$(sed -n 's/^SENTINEL_RESTART_CODE = \([0-9]*\).*/\1/p' "${PARAMS_PY}" | head -n 1)"
  [[ -n "${v}" ]] || {
    echo "params.py から読めない"
    return 1
  }
  echo "${v}"
  ((v != 0))
}
item "SENTINEL_RESTART_CODE が 0 でない" check_sentinel_code

# sentinel_actions を呼ぶのは top-level launch だけ。**include される側でも呼ぶと、
# 1 つの launch 木に見張りが 3 つ立ってそれぞれが勝手に落としにかかる。**
# 「include される側」= その名前を他の launch が文字列で持っているもの。
check_sentinel_placement() {
  local f name bad=() ok=()
  while read -r f; do
    name="${f##*/}"
    if grep -rlF -- "\"${name}\"" "${ROOT}"/src/daifuku_*/launch/ 2>/dev/null |
      grep -qv -- "${f}\$"; then
      bad+=("${name}")
    else
      ok+=("${name}")
    fi
  done < <(grep -rl 'sentinel_actions' "${ROOT}"/src/daifuku_*/launch/*.py 2>/dev/null)
  ((${#bad[@]} == 0)) || {
    echo "include される側が見張りを立てている: ${bad[*]}"
    return 1
  }
  echo "${#ok[@]} つの top-level だけ: ${ok[*]}"
}
item "config_sentinel を立てるのが top-level launch だけ" check_sentinel_placement

# site_manager は逆に**リポジトリ全体で 1 か所**。2 つ立てると同じ configs/site を
# 2 つのノードが書きに行く。
check_one_site_manager_launch() {
  local hit
  hit="$(grep -rl 'executable="site_manager"' "${ROOT}"/src/*/launch/*.py 2>/dev/null |
    sed 's|.*/||' | tr '\n' ' ')"
  echo "${hit:-どの launch も立てていない}"
  [[ "$(wc -w <<<"${hit}")" == "1" ]]
}
item "site_manager を立てる launch がちょうど 1 つ" check_one_site_manager_launch

# ── 順路のトピック名 ────────────────────────────────────────────────────────
# パネル (絶対名) と joy_teleop (相対名) と vi_planner の waypoint_topic の 3 か所に
# あり、1 つだけ変えると**エラーも警告も出ないまま先読みだけが起きなくなる**。
# vi_planner は vcs import で入るので、クローンが古いと読めない。**読めなかった
# ことを黙らせない** (2 つ揃っただけで「3 か所が揃っている」に見せないため)。
check_waypoint_topic() {
  local panel joy vi note main_rs
  panel="$(sed -n 's/.*kWaypointPathTopic\[\][[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' \
    "${ROOT}/src/daifuku_waypoint_manager/src/waypoint_manager_panel.cpp" 2>/dev/null | head -n 1)"
  joy="$(sed -n 's/.*Path,[[:space:]]*"\([^"]*\)".*/\1/p' \
    "${ROOT}/src/daifuku_bringup/src/joy_teleop.py" 2>/dev/null | head -n 1)"
  [[ -n "${panel}" && -n "${joy}" ]] || {
    echo "パネルか joy_teleop からトピック名を読めない"
    return 1
  }
  # 2026-08-09 の上流の整理でパッケージが vi_ros2/ から vi_rs/ へ移り、パラメータの
  # 宣言も main.rs から node/params.rs へ分かれた。
  main_rs="${ROOT}/src/value_iteration3/vi_rs/vi_planner/src/node/params.rs"
  vi="$(sed -n 's/.*"waypoint_topic"[^"]*"\([^"]*\)".*/\1/p' "${main_rs}" 2>/dev/null | head -n 1)"
  # 比べるのは先頭の / を落とした形。絶対名なのはパネルだけ。
  if [[ -z "${vi}" ]]; then
    # **2 つ揃ったことを 3 つ揃ったことにしない。** 読めなかったなら通さない。
    echo "パネル ${panel} / joy_teleop ${joy} / vi_planner は読めない (vcs import が無いか古い)"
    return 1
  fi
  echo "パネル ${panel} / joy_teleop ${joy} / vi_planner ${vi}"
  [[ "${panel#/}" == "${joy#/}" && "${panel#/}" == "${vi#/}" ]]
}
item_warn "順路のトピック名が食い違っていない" check_waypoint_topic

# ── RViz のパネル ───────────────────────────────────────────────────────────
# nav2 の「Navigation 2」パネルは /waypoints へ MarkerArray を、自前の
# WaypointManagerPanel は同じ名前へ Path を出す。RViz は 1 プロセス = 1 参加者
# なので後から立ったほうが落ち、**そこで設定の読み込みが止まる** (自前パネルと
# /waypoints の表示だけが出ないまま上がる)。表示側も同じ衝突を起こすので、/waypoints を見るのは
# Path でなければならない。
check_rviz_waypoint_panels() {
  local cfg="${ROOT}/src/daifuku_stack/rviz/navigation.rviz"
  [[ -f "${cfg}" ]] || { echo "navigation.rviz が無い"; return 1; }
  grep -q "Class: daifuku_waypoint_manager/WaypointManagerPanel" "${cfg}" || {
    echo "WaypointManagerPanel が居ない (この検査の前提が変わった)"
    return 1
  }
  grep -q "Class: nav2_rviz_plugins/Navigation 2" "${cfg}" && {
    echo "Navigation 2 パネルが同居している (/waypoints の型が衝突する)"
    return 1
  }
  # /waypoints を見る表示の型。直前の Class 行がその表示のもの。
  local klass
  klass="$(awk '/Class: rviz_default_plugins\// { c = $0 }
                /Value: \/waypoints$/ { sub(/.*Class: /, "", c); print c; exit }' "${cfg}")"
  [[ "${klass}" == "rviz_default_plugins/Path" ]] || {
    echo "/waypoints の表示が ${klass:-不明} (Path でないと購読側で衝突する)"
    return 1
  }
  echo "パネルは WaypointManagerPanel だけ / 表示は Path"
}
item "navigation.rviz の /waypoints が 1 つの型で揃っている" check_rviz_waypoint_panels

finish
