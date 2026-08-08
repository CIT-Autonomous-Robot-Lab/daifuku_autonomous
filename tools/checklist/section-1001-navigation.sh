#!/usr/bin/env bash
# 種 10 自律 / 項 01 ナビゲーション。**navigation を立てていないと全部 SKIP。**
#
# 「サーバが 2 つ載っている」を見つけるのが主眼。クライアントは先に見つけたほうへ
# 繋ぐが、**どちらに繋がったかはログにも ros2 action list にも出ない**ので、
# 数を数えるところまでやらないと分からない。
#
# 機体は動かさない (ゴールは投げない)。

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

section 1001 "ナビゲーション"

need_ros

if ! has_node /vi_planner && ! has_node /vi_global_planner && ! has_node /bt_navigator; then
  skip "ナビゲーション" "navigation が立っていない"
  finish 0
fi

if has_node /bt_navigator; then
  result CHECK "構成" "nav2:=true (BT がゴールを捌く)"
else
  result CHECK "構成" "nav2:=false (vi_planner が standalone で受ける)"
fi

# ── アクションサーバの数 ────────────────────────────────────────────────────
action_servers() {
  ros_run 10 action info "$1" 2>/dev/null |
    sed -n 's/^Action servers:[[:space:]]*//p' | head -n 1
}
exactly_one_server() {
  local n
  n="$(action_servers "$1")"
  [[ -n "${n}" ]] || {
    echo "$1 が見あたらない"
    return 1
  }
  echo "${n} 台"
  [[ "${n}" == "1" ]]
}
item "/navigate_to_pose のサーバがちょうど 1 台" exactly_one_server /navigate_to_pose
item "/compute_path_to_pose のサーバがちょうど 1 台" exactly_one_server /compute_path_to_pose
item "/follow_waypoints のサーバがちょうど 1 台" exactly_one_server /follow_waypoints

# planner:=vi では navigate_through_poses が使えない。nav2:=true では BT が
# AlwaysFailure の stub に差し替わっていて**即座に ABORTED になりログにも何も
# 出ない**ので、居ること自体を知らせておく。複数点は /follow_waypoints で。
if has_node /bt_navigator && has_node /vi_planner; then
  result WARN "/navigate_through_poses" \
    "planner:=vi では stub (AlwaysFailure)。複数点は /follow_waypoints を使うこと"
fi

# ── コストマップと lifecycle ────────────────────────────────────────────────
if has_node /bt_navigator; then
  item "/global_costmap/costmap が出ている" has_topic /global_costmap/costmap
  item "/local_costmap/costmap が出ている" has_topic /local_costmap/costmap

  # **居るだけでは足りない。** 止まったコストマップも latch されたままなので
  # has_topic は通る。周期はノード自身の publish_frequency と突き合わせる
  # (0.0 = 更新時のみ、という設定もありうるので、そのときは測らない)。
  check_costmap_hz() { # check_costmap_hz local | global
    local node f
    # ノード名は決め打ちにしない (planner:=vi では素の Nav2 と顔ぶれが違う)。
    node="$(grep -E "/$1_costmap/[a-z_]*costmap\$" <<<"${ROS_NODES}" | head -n 1)"
    [[ -n "${node}" ]] || {
      echo "$1_costmap のノードが居ない"
      return 0
    }
    f="$(param_num "${node}" publish_frequency)"
    [[ -n "${f}" ]] || {
      echo "${node} の publish_frequency を読めない"
      return 1
    }
    awk -v f="${f}" 'BEGIN { exit !(f + 0 > 0) }' || {
      echo "publish_frequency=${f} (更新時のみ出す設定なので測らない)"
      return 0
    }
    hz_at_least "/$1_costmap/costmap" "$(awk -v f="${f}" 'BEGIN { printf "%.2f", f * 0.5 }')" 10
  }
  item_warn "/local_costmap/costmap が publish_frequency 相当で出ている" check_costmap_hz local
  item_warn "/global_costmap/costmap が publish_frequency 相当で出ている" check_costmap_hz global

  # RViz の Navigation 2 パネルの Reset を押すと、停止が逆順なので
  # velocity_smoother が先に落ち、waypoint_follower の停止で固まって
  # behavior_server だけが active で残る (回転だけが止まらなくなる)。
  # そのとき lifecycle_manager は is_active にも応答しなくなるので、
  # 応答するかどうかがそのまま見分けになる。
  check_lifecycle_alive() {
    local out
    out="$(ros_run 10 service call /lifecycle_manager_navigation/is_active \
      std_srvs/srv/Trigger '{}' 2>/dev/null | tr '\n' ' ')"
    [[ -n "${out}" ]] || {
      echo "is_active に応答しない (Reset のあとで固まった疑い)"
      return 1
    }
    echo "応答あり"
    grep -q 'success=True' <<<"${out}"
  }
  item "lifecycle_manager_navigation が is_active に応答する" check_lifecycle_alive
else
  skip "コストマップと lifecycle" "nav2:=false では立たない"
fi

# ── 先読みの入口 ────────────────────────────────────────────────────────────
# nav2:=true では、vi_planner の waypoint_prefetch は /waypoints が来ないと
# **エラーも警告も出ないまま何もしない**。順路を latch するのはパネルと
# joy_teleop の 2 か所だけで、/follow_waypoints へ直接投げる経路では届かない。
if has_node /vi_planner; then
  check_prefetch_wired() {
    local on
    on="$(ros_run 10 param get /vi_planner waypoint_prefetch 2>/dev/null | tr -d '\r')"
    echo "${on}"
    grep -qi 'false' <<<"${on}" && return 0
    has_topic /waypoints || {
      echo "waypoint_prefetch=true なのに /waypoints が居ない — 先読みは起きない"
      return 1
    }
  }
  item_warn "waypoint_prefetch の入口 (/waypoints) が繋がっている" check_prefetch_wired
fi

# 巡回の出どころ。実機ではパネルが載らないので joy_teleop だけだが、そちらは
# waypoints_file が空だと巡回そのものを断る (既定の順路は 2026-08-04 に廃止)。
if has_node /joy_teleop; then
  check_waypoints_file() {
    local f
    f="$(ros_run 10 param get /joy_teleop waypoints_file 2>/dev/null |
      sed 's/.*value is: //' | tr -d "\r'\"")"
    echo "${f:-空}"
    [[ -n "${f}" && "${f}" != "None" ]]
  }
  item_warn "joy_teleop に waypoints_file が入っている" check_waypoints_file
fi

finish
