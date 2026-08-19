#!/usr/bin/env bash
# 種 09 自律 / 項 01 自己位置。**navigation を立てていないと全部 SKIP。**
#
# 目玉は 1 つ。**map -> odom が実際に出ているか。** /mcl_pose は 20Hz で出続けた
# まま map -> odom だけが 1 度も出ない、という壊れ方をする (emcl2 の
# publishOdomFrame が extrapolation で落ちるのを DEBUG で握り潰すため)。
# RViz では Fixed Frame が map なので全部消えるが、ログには何も出ない。
# だから見るのはトピックではなく TF。

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

section 0901 "自己位置"

need_ros

# localization:=vi では emcl2 も amcl も居ない — 推定を持つのは vi_planner 自身
# (上流の VIOLA)。**map -> odom を出すのもそちら** (publish_tf) なので下の目玉は
# そのまま効き、違うのは推定姿勢のトピック名だけ。emcl2 構成でも vi_planner は
# 立っているので、**見る順は emcl2 / amcl が先**。
if has_node /emcl2 || has_node /amcl; then
  LOCALIZER=/emcl2
  has_node /amcl && LOCALIZER=/amcl
  POSE_TOPIC=/mcl_pose
elif has_node /vi_planner; then
  LOCALIZER=/vi_planner
  POSE_TOPIC=/viola_pose
else
  skip "自己位置" "navigation が立っていない (ros2 launch daifuku_stack navigation.launch.py)"
  finish 0
fi
result CHECK "自己位置推定" "${LOCALIZER}"

require "/map がある" has_topic /map

# 地図は src/daifuku_config/site → その overrides の site: map: で決まる。走っている
# map_server がそれと違うものを読んでいたら、別の場所の地図で推定している。
check_map_matches_site() {
  local site ov want got
  site="$(site_name)"
  ov="${ROOT}/src/daifuku_config/overrides/${site}.yaml"
  want="$(sed -n '/^site:/,/^[^ #]/p' "${ov}" 2>/dev/null |
    sed -n 's/^[[:space:]]*map:[[:space:]]*//p' | head -n 1)"
  got="$(ros_run 10 param get /map_server yaml_filename 2>/dev/null |
    sed 's/.*value is: //' | tr -d "\r'\"")"
  echo "site=${site} 期待 ${want:-?} / 実際 ${got##*/}"
  [[ -n "${want}" && "${got##*/}" == "${want##*/}" ]]
}
item "map_server が src/daifuku_config/site の指す地図を読んでいる" check_map_matches_site

item "map -> odom の時刻が進んでいる" tf_advancing map odom
on_fail && diagnose "map -> odom が出ない" \
  "/mcl_pose は 20Hz で出ているか|**これがこの穴の顔**。publishOdomFrame が extrapolation で落ちるのを DEBUG で握り潰している|0501 へ戻る。odom -> base_footprint が古い (EKF が IMU を捨てて 5Hz に落ちた) のが根" \
  "RViz で地図もロボットも全部消えているか|Fixed Frame が map なので、この TF が無いと全部消える|上と同じ。トピックではなく TF を追うこと" \
  "その場で推定姿勢が回り続けるか|スキャンが地図の壁を貫通している (地図と実環境の不整合)|overrides の emcl2 2 点 (alpha_threshold / expansion_radius_orientation) は対症療法。**地図を取り直すのが本筋**"

item "${POSE_TOPIC} が 5Hz 以上" hz_at_least "${POSE_TOPIC}" 5

# emcl2 は 1 枚のスキャンを odom_freq ÷ /scan の周期 回だけ食う。
# ExpResetMcl2::sensorUpdate に「同じスキャンなら抜ける」ガードが無いので、
# 同じ観測を独立な証拠として二度数える形になり、**エラーも警告も出ないまま
# 自己位置がスキャン寄りに硬くなる**。既定は 20Hz / 10Hz = 2 倍。
if [[ "${LOCALIZER}" == "/emcl2" ]]; then
  check_double_count() {
    local freq scan
    freq="$(ros_run 10 param get /emcl2 odom_freq 2>/dev/null | grep -o '[0-9][0-9.]*' | tail -n 1)"
    scan="$(ros_run 8 topic hz --window 20 /scan 2>/dev/null |
      grep -o 'average rate: *[0-9.]*' | tail -n 1 | grep -o '[0-9.]*$')"
    [[ -n "${freq}" && -n "${scan}" ]] || {
      echo "odom_freq か /scan の周期を読めない"
      return 1
    }
    awk -v f="${freq}" -v s="${scan}" '
      BEGIN { printf "odom_freq %s / scan %.1f Hz = %.1f 倍数える\n", f, s, f / s }'
  }
  item_warn "同じスキャンを何倍数えているか" check_double_count

  # alpha が 0 付近に張り付くのは、スキャンが地図の壁を貫通している (地図と実環境の
  # 不整合)。19F の overrides の 3 つは、その対症療法として入っている。
  if has_topic /alpha; then
    check_alpha() {
      local v
      v="$(ros_run 8 topic echo --once --field data /alpha 2>/dev/null | head -n 1 | tr -d '\r')"
      [[ -n "${v}" ]] || {
        echo "取れない"
        return 1
      }
      echo "alpha=${v}"
      awk -v v="${v}" 'BEGIN { exit !(v + 0 > 0.4) }'
    }
    item_warn "emcl2 の alpha が 0.4 より大きい" check_alpha
  fi
fi

finish
