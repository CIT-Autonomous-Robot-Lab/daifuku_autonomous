#!/usr/bin/env bash
# 種 04 センサ / 項 01 LiDAR。
#
# **一番の狙いは「Mid-360 が LAN に居ないまま boot した」を見つけること。**
# そのときコンテナは正常に上がったように見え、ros2 launch は子ノードが死んでも
# 終了しないので、/livox/lidar が来ないまま restart: unless-stopped の出番も無い。
#
# **/scan の鮮度は見ない。** restamp_scan が now() で打ち直すので必ず新しく見える。
# 遅れを見るのは odom -> base_footprint と map -> odom のほう (0501 / 0901)。

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

section 0401 "LiDAR"

need_ros

MID360_JSON="${ROOT}/config/bringup/sensors/MID360_config.json"

# ── センサが LAN に居るか ───────────────────────────────────────────────────
if has_topic /livox/lidar || grep -q '"ip"' "${MID360_JSON}" 2>/dev/null; then
  check_mid360_reachable() {
    local ip
    ip="$(grep -o '"ip"[[:space:]]*:[[:space:]]*"[0-9.]*"' "${MID360_JSON}" |
      grep -o '[0-9][0-9.]*' | head -n 1)"
    [[ -n "${ip}" ]] || {
      echo "MID360_config.json から IP を読めない"
      return 1
    }
    echo "${ip}"
    ping -c 1 -W 2 "${ip}" >/dev/null 2>&1
  }
  item "Mid-360 が LAN に居る" check_mid360_reachable
else
  skip "Mid-360 が LAN に居る" "MID360_config.json が読めない"
fi

# ── 配信 ────────────────────────────────────────────────────────────────────
if has_topic /livox/lidar; then
  item "/livox/lidar が 8Hz 以上" hz_at_least /livox/lidar 8
else
  skip "/livox/lidar" "トピックが無い (lidar:=2d か、ドライバが上がっていない)"
fi

require "/scan がある" has_topic /scan
item "/scan が 8Hz 以上" hz_at_least /scan 8
on_fail && diagnose "/scan が来ない" \
  "/livox/lidar は来ているか|点群はあるのに /scan が無い = 切り出し (pointcloud_to_laserscan) 側|min_height / max_height と仰角フィルタの帯を見る。潰れていると空になる" \
  "Mid-360 の ping が通らなかったか|センサが LAN に居ない。**コンテナは正常に上がったように見える**|結線と電源、MID360_config.json の IP を見る" \
  "ping は通るのに点群が来ないか|bind failed (別プロセスが同じポートを掴んでいる)|docker compose restart raspicat。残骸は docs/usage/troubleshooting.md" \
  "lidar:=2d で立てているか|URG のデバイスが見えていない|/dev/ttyACM* と udev ルールを見る"

# 全ビームが inf = 何も見えていない。帯の切り方を間違えるとこうなり、
# **エラーも警告も出ない**まま emcl2 も costmap も動かなくなる。
# --full-length が要る。既定の echo は配列を 100 要素で打ち切るので、720 本の
# スキャンのうち最初の 100 本だけを見ることになり、**そちらがたまたま開けている
# だけで健全な LiDAR を「有効ビーム 0 本」と言う**。
check_scan_has_returns() {
  local out n
  out="$(ros_run 10 topic echo --once --full-length --field ranges /scan 2>/dev/null)"
  [[ -n "${out}" ]] || {
    echo "1 通も取れない"
    return 1
  }
  n="$(grep -o '[0-9][0-9]*\.[0-9]' <<<"${out}" | wc -l)"
  echo "有効ビーム ${n} 本"
  ((n > 0))
}
item "/scan に有効なビームがある" check_scan_has_returns

# ── フレーム名 ──────────────────────────────────────────────────────────────
# livox_ros_driver2 は IMU の frame_id を無視して livox_frame をべた書きする
# (lddc.cpp の Lddc::PublishImuData)。点群側だけ lidar_frame に化けると EKF が
# 引く TF が消え、robot_localization は transform_timeout ぶん黙るだけで
# **エラーも警告も出さない**。だから 2 つを突き合わせる。
frame_of() {
  ros_run 10 topic echo --once --field header.frame_id "$1" 2>/dev/null | head -n 1 | tr -d "\r'\""
}
if has_topic /livox/lidar && has_topic /livox/imu; then
  check_livox_frames() {
    local pc imu
    pc="$(frame_of /livox/lidar)"
    imu="$(frame_of /livox/imu)"
    echo "点群 ${pc:-?} / IMU ${imu:-?}"
    [[ -n "${pc}" && "${pc}" == "${imu}" ]]
  }
  item "点群と IMU の frame_id が一致している" check_livox_frames
else
  skip "点群と IMU の frame_id" "Mid-360 の構成ではない"
fi

# ── 帯 (仰角フィルタと切り出しは組で決まる) ─────────────────────────────────
if has_node /elevation_filter; then
  # 仰角フィルタは pointcloud_to_laserscan の手前に入り、切り出しの下限を
  # lidar_z + 距離 x tan(min_elevation_deg) へ変える。下限が距離とともに上がるので、
  # max_height をその下に置くと帯が潰れ、**range_max を伸ばしてもエラーも警告も
  # 出ないまま手前で何も入らなくなる**。
  check_band() {
    local deg minh maxh rmax
    deg="$(param_num /elevation_filter min_elevation_deg)"
    minh="$(param_num /pointcloud_to_laserscan min_height)"
    maxh="$(param_num /pointcloud_to_laserscan max_height)"
    rmax="$(param_num /pointcloud_to_laserscan range_max)"
    [[ -n "${deg}" && -n "${minh}" && -n "${maxh}" && -n "${rmax}" ]] || {
      echo "パラメータを読めない"
      return 1
    }
    awk -v deg="${deg}" -v minh="${minh}" -v maxh="${maxh}" -v rmax="${rmax}" '
      BEGIN {
        printf "min_elevation %.1f deg / 帯 %.3f-%.3f m / range_max %.1f m", deg, minh, maxh, rmax
        if (deg <= 0) { printf "  仰角は素通し (帯は全距離で一定)\n"; exit 0 }
        t = sin(deg * 3.14159265358979 / 180) / cos(deg * 3.14159265358979 / 180)
        d = (maxh - minh) / t
        printf "  %.1f m 先で帯が潰れる\n", d
        exit !(d >= rmax)
      }'
  }
  item_warn "仰角フィルタと切り出しの帯が range_max まで生きている" check_band
else
  skip "仰角フィルタの帯" "elevation_filter が居ない (elevation_filter:=false)"
fi

finish
