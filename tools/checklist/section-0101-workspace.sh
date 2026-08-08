#!/usr/bin/env bash
# 種 01 静的検査 / 項 01 ワークスペース。**ROS も Docker も要らない**ので一番先に走る。
#
# ここで見るのは全部「エラーも警告も出ないまま効かなくなる」たぐいのもの。
# ビルドが通ることは見ない (それは colcon の仕事)。

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

section 0101 "ワークスペース"

require "git のワークツリーである" git -C "${ROOT}" rev-parse --is-inside-work-tree

# install/ のリンク切れ。config_sentinel はこれを黙って飛ばすうえ、**見張りの
# 対象からも外れる**ので、設定を移したあとの古い symlink はここでしか見つからない。
check_broken_links() {
  local dirs=() d n list
  for d in install build; do [[ -d "${ROOT}/${d}" ]] && dirs+=("${ROOT}/${d}"); done
  list="$(find "${dirs[@]}" -xtype l 2>/dev/null)"
  n="$(grep -c . <<<"${list}")"
  [[ -z "${list}" ]] && n=0
  if ((n > 0)); then
    echo "${n} 本: $(head -n 3 <<<"${list}" | tr '\n' ' ')"
    return 1
  fi
  echo "0 本"
}
if [[ -d "${ROOT}/install" || -d "${ROOT}/build" ]]; then
  item "install/ にリンク切れが無い" check_broken_links
else
  skip "install/ にリンク切れが無い" "未ビルド (Windows の開発ホストなど)"
fi

# launch が Node(executable=) で立てる Python の実行ビット。
# **見るのは git の index**。Windows のチェックアウトは core.fileMode=false なので
# 作業ツリーの権限は当てにならず、Linux で初めて Permission denied になる。
check_exec_bits() {
  local name path mode miss=()
  while read -r name; do
    [[ -n "${name}" ]] || continue
    path="$(git -C "${ROOT}" ls-files -- "*/${name}" | head -n 1)"
    [[ -n "${path}" ]] || continue
    mode="$(git -C "${ROOT}" ls-files -s -- "${path}" | awk '{print $1}')"
    [[ "${mode}" == "100755" ]] || miss+=("${path}")
  done < <(grep -rho 'executable="[A-Za-z0-9_]*\.py"' "${ROOT}"/src/daifuku_*/launch/*.py |
    sed 's/.*"\(.*\)"/\1/' | sort -u)
  if ((${#miss[@]} > 0)); then
    echo "実行ビットが無い: ${miss[*]}  → git update-index --chmod=+x"
    return 1
  fi
  echo "全部 100755"
}
item "launch が立てる .py に実行ビットがある" check_exec_bits

# パッケージ一覧は 3 か所にあり、食い違うとビルドが通らなくなるか、機体が上がらない。
BUILD_LISTS=(
  "docker/raspberrypi/scripts/build-workspace.sh"
  "docker/dev/tools/build-workspace.sh"
  "tools/setup/setup_native_base.sh"
)
check_required_pkgs() {
  local f p miss=()
  for f in "${BUILD_LISTS[@]}"; do
    for p in daifuku_bringup daifuku_config daifuku_config_manager; do
      grep -qw -- "${p}" "${ROOT}/${f}" || miss+=("${f##*/}:${p}")
    done
  done
  ((${#miss[@]} == 0)) || {
    echo "落ちている: ${miss[*]}"
    return 1
  }
  echo "3 か所すべてに居る"
}
item "bringup / config / config_manager が 3 つの一覧すべてに居る" check_required_pkgs

# 逆に、実機イメージには rqt も RViz も無いので Pi の一覧に足すとビルドが落ちる。
check_pi_excludes() {
  local p bad=()
  for p in daifuku_rqt daifuku_waypoint_manager; do
    grep -qw -- "${p}" "${ROOT}/docker/raspberrypi/scripts/build-workspace.sh" && bad+=("${p}")
  done
  ((${#bad[@]} == 0)) || {
    echo "Pi の一覧に居る: ${bad[*]} (実機イメージに rqt / RViz が無いので落ちる)"
    return 1
  }
  echo "Pi の一覧には無い"
}
item "daifuku_rqt / daifuku_waypoint_manager が Pi の一覧に無い" check_pi_excludes

# vcs import で入るものは本リポジトリのコミットに入らない。ここを直しても消えるので
# 差分が残っていたら知らせる (untracked のまま現れるのが正常)。
check_vendored_clean() {
  local d dirty=()
  for d in raspicat_ros raspicat_description raspimouse2 value_iteration3; do
    [[ -d "${ROOT}/src/${d}" ]] || continue
    git -C "${ROOT}/src/${d}" rev-parse --is-inside-work-tree >/dev/null 2>&1 || continue
    [[ -z "$(git -C "${ROOT}/src/${d}" status --porcelain 2>/dev/null)" ]] || dirty+=("${d}")
  done
  ((${#dirty[@]} == 0)) || {
    echo "手が入っている: ${dirty[*]} (本リポジトリのコミットには入らない)"
    return 1
  }
  echo "手つかず"
}
item_warn "vcs import で入るパッケージに未コミットの差分が無い" check_vendored_clean

finish
