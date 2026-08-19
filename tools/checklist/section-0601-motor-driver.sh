#!/usr/bin/env bash
# 種 06 駆動 / 項 01 モータドライバ (**車輪は回さない**)。
#
# 見るのは配線と状態だけ。ここで見つけたいのは「どのトピックへ投げれば車輪が
# 動くのか」の食い違いで、間違ったトピックへ投げても**エラーは出ず、ただ機体が
# 動かない**。回すのは 0701。

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

section 0601 "モータドライバ"

need_ros

DRIVER=""
for n in /raspicat_driver /raspimouse; do
  has_node "${n}" && DRIVER="${n}"
done
check_driver_present() {
  [[ -n "${DRIVER}" ]] || {
    echo "raspicat_driver も raspimouse も居ない"
    return 1
  }
  echo "${DRIVER}"
}
require "本体ドライバが居る" check_driver_present

# ドライバは lifecycle ノード。**finalized まで落ちると launch ごと終了する**
# ので、LiDAR と EKF も道連れになって全部が上がり直す。
check_driver_active() {
  local st
  st="$(ros_run 10 lifecycle get "${DRIVER}" 2>/dev/null | head -n 1)"
  echo "${st:-取れない}"
  grep -q 'active' <<<"${st}"
}
item "ドライバが active" check_driver_active

# ── モータ電源 ──────────────────────────────────────────────────────────────
require "${MOTOR_SERVICE} がある" has_service "${MOTOR_SERVICE}"
check_motor_type() {
  local t
  t="$(ros_run 10 service type "${MOTOR_SERVICE}" 2>/dev/null | head -n 1)"
  echo "${t:-取れない}"
  [[ "${t}" == "std_srvs/srv/SetBool" ]]
}
item "${MOTOR_SERVICE} の型が std_srvs/srv/SetBool" check_motor_type

# ── 仲裁 (twist_mux) の配線 ─────────────────────────────────────────────────
if has_node /twist_mux; then
  result CHECK "仲裁" "twist_mux:=true — **車輪が見ているのは /cmd_vel_mux**"

  # ここが空だと control.sh teleop も joy も**エラーを出さずに効かない**。
  check_teleop_wired() {
    local s
    s="$(topic_subscribers /cmd_vel_teleop)"
    echo "${s:-購読者なし}"
    grep -q 'twist_mux' <<<"${s}"
  }
  item "/cmd_vel_teleop を twist_mux が購読している" check_teleop_wired
  on_fail && diagnose "手動の指令が仲裁に入っていない" \
    "twist_mux のノードは居るのに購読者に出ないか|src/daifuku_config/bringup/robot/twist_mux.yaml の topics: の綴り違い。**TwistMux に既定値は無く、綴りを間違えても黙って動く**|yaml の teleop: topic: を見る" \
    "0701 で車輪を回すのにこのまま進みたいか|/cmd_vel_teleop へ投げても誰も聞かない|CMD_VEL_TOPIC=/cmd_vel を渡して直接ドライバへ入れる (仲裁を外すので自律側と喧嘩する)"

  check_mux_to_driver() {
    local s
    s="$(topic_subscribers /cmd_vel_mux)"
    echo "${s:-購読者なし}"
    [[ -n "${s}" ]]
  }
  item "/cmd_vel_mux をドライバが購読している" check_mux_to_driver

  # 自律側 (/cmd_vel = 優先度 100) のほうが手動 (10) より強い。**自律走行中は
  # teleop も stop も効かず、そのときエラーも出ない。** 0701 で車輪を回す前に、
  # 自律側が黙っていることを確かめておく。
  check_nav_quiet() {
    if hz_at_least /cmd_vel 0.1 3 >/dev/null 2>&1; then
      echo "自律側が出している — この状態では teleop も stop も通らない"
      return 1
    fi
    echo "黙っている"
  }
  item "自律側 /cmd_vel が今は黙っている" check_nav_quiet
else
  result WARN "仲裁" "twist_mux が居ない — 速度指令は /cmd_vel へ直接入れること"
fi

# ── 指令が途切れたときの挙動 ────────────────────────────────────────────────
# 自前実装の cmd_vel_timeout は既定 60 秒。**teleop を出すものは自分で 0 を出して
# 止めること。** 公式実装にはこのキーが無く、止まるかどうかは未確認。
check_cmd_vel_timeout() {
  local v
  v="$(ros_run 10 param get "${DRIVER}" cmd_vel_timeout 2>/dev/null | grep -o '[0-9][0-9.]*' | tail -n 1)"
  [[ -n "${v}" ]] || {
    echo "このドライバには無い (公式実装。途切れたときの挙動は未確認)"
    return 0
  }
  echo "${v} 秒 — 指令が途切れてもこの間は走り続ける"
}
item_warn "cmd_vel_timeout を確かめる" check_cmd_vel_timeout

finish
