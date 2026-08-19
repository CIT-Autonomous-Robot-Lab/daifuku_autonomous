#!/usr/bin/env bash
# 種 03 ROS / 項 01 グラフと TF の所有者。
#
# ここで見つけたいのは**二重起動**と**二重配信**。TF を 2 つのノードが出しても
# エラーは出ず、自己位置だけが静かに壊れる。

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

section 0301 "ROS グラフ"

need_ros

check_domain() {
  local inside outside
  if [[ -n "${CHECKLIST_NATIVE:-}" ]]; then
    inside="${ROS_DOMAIN_ID:-0}"
  else
    inside="$(compose exec -T "${CHECKLIST_SERVICE}" printenv ROS_DOMAIN_ID 2>/dev/null | tr -d '\r')"
  fi
  outside="${ROS_DOMAIN_ID:-未設定}"
  echo "コンテナ ${inside:-0} / このシェル ${outside}"
}
item_warn "ROS_DOMAIN_ID を確かめる" check_domain

# ディスカバリが不安定だと、以降の「トピックが無い」が全部あてにならなくなる。
# 2 回取って同じかどうかだけ見る。
check_discovery_stable() {
  local again diff
  again="$(ros node list 2>/dev/null)"
  diff="$(comm -3 <(sort <<<"${ROS_NODES}") <(sort <<<"${again}") | tr -d '\t' | tr '\n' ' ')"
  echo "$(grep -c . <<<"${ROS_NODES}") ノード"
  [[ -z "${diff}" ]] || {
    echo "2 回で違う: ${diff}"
    return 1
  }
}
item "ノード一覧が 2 回とも同じ" check_discovery_stable

# site_manager を立てるのは robot_bringup の 1 か所だけ。2 つ立つと同じ
# src/daifuku_config/site を 2 つのノードが書きに行く。
check_one_site_manager() {
  local n
  n="$(grep -c '/site_manager$' <<<"${ROS_NODES}")"
  echo "${n} つ"
  ((n == 1))
}
item "site_manager がちょうど 1 つ" check_one_site_manager

# config_sentinel は各 top-level launch が 1 つずつ立てるので、navigation を
# 立てていれば 2 つが正常。それ以外の同名ノードの重複は事故。
check_no_dup_nodes() {
  local dup
  dup="$(sort <<<"${ROS_NODES}" | uniq -d | grep -v 'config_sentinel' | tr '\n' ' ')"
  [[ -z "${dup}" ]] || {
    echo "同名が 2 つ以上: ${dup}"
    return 1
  }
  echo "重複なし (config_sentinel は launch ごとに 1 つなので除外)"
}
item "同名ノードが二重に立っていない" check_no_dup_nodes

# /tf を出すノードの顔ぶれ。odom -> base_footprint の所有者は本体ドライバか EKF の
# **どちらか一方**で、両方が出すと自己位置だけが静かに壊れる。
# ekf_filter_node は /tf の publisher でも (tf2 listener として) subscriber でも
# あるので、topic_publishers で publisher 側だけを取ること。
check_odom_tf_owner() {
  local pubs n
  pubs="$(topic_publishers /tf)"
  n="$(grep -oE '(ekf_filter_node|raspicat_driver|raspimouse)' <<<"${pubs}" | sort -u | wc -l)"
  echo "所有者 ${n} つ / 全 publisher: ${pubs:-なし}"
  ((n <= 1))
}
item "odom -> base_footprint を出すものが 1 つだけ" check_odom_tf_owner

# compute_path_to_pose を出すものは 1 つだけ。2 つ載るとクライアントは先に見つけた
# ほうへ繋ぎ、**どちらに繋がったかはログにも ros2 action list にも出ない**。
# かつてここは vi_planner と vi_global_planner の排他を見ていたが、2026-08-08 の
# 上流の整理で後者はパッケージごと消え (広域だけ VI = 同じ vi_planner を
# follow: false で立てる)、残る同居先は planner:=vi で立たないはずの planner_server。
check_vi_exclusive() {
  local n
  n="$(grep -cE '/(vi_planner|planner_server)$' <<<"${ROS_NODES}")"
  echo "${n} つ"
  ((n <= 1))
}
item "compute_path_to_pose を出すノードが 1 つだけ" check_vi_exclusive

finish
