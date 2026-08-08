#!/usr/bin/env bash
# 種 05 センサ / 項 01 IMU・EKF・オドメトリ (機体は動かさない)。
#
# use_mid360_imu が true (既定) なら odom -> base_footprint と /odom の所有者は
# EKF で、本体ドライバは /wheel/odom を出すだけになる。**片方だけ切り替わる状態は
# 作れない** (1 つの launch が両方を立てる) ので、ここでは「どちらの構成か」を
# 決めてから、その構成で在るべきものを見る。

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

section 0501 "IMU / EKF / オドメトリ"

need_ros

require "/odom がある" has_topic /odom
item "/odom が 15Hz 以上" hz_at_least /odom 15

# odom -> base_footprint は**引けるだけでは足りない**。時刻が進んでいないと、
# emcl2 の publishOdomFrame が (スキャンの時刻が系内で最も新しいので) 必ず
# extrapolation で落ち、それを DEBUG で握り潰すため map -> odom が 1 度も出ない。
item "odom -> base_footprint の時刻が進んでいる" tf_advancing odom base_footprint
on_fail && diagnose "odom -> base_footprint が出ない・止まっている" \
  "EKF (ekf_filter_node) と本体ドライバの両方が /tf に居るか|所有者が 2 つある。**自己位置だけが静かに壊れる**|0301 の TF 所有者へ戻る。use_mid360_imu はどちらか一方しか選べない" \
  "/imu/mid360 の購読者に ekf_filter_node が居ないか|IMU が丸ごと捨てられている (frame_id の食い違い)。robot_localization は transform_timeout ぶん黙るだけで**何も言わない**|IncludeLaunchDescription の launch_arguments が GroupAction で囲まれているか。lidar_frame の漏れで livox_frame が消える" \
  "/odom そのものが来ていないか|ドライバか EKF が上がっていない|0201 のコンテナと 0601 のドライバ状態へ戻る"

if has_node /ekf_filter_node; then
  result CHECK "構成" "use_mid360_imu=true (EKF が /odom と TF を持つ)"

  item "/livox/imu が 100Hz 以上" hz_at_least /livox/imu 100
  item "/imu/mid360 が 100Hz 以上" hz_at_least /imu/mid360 100
  item "/wheel/odom がある (ドライバは TF を出さない側)" has_topic /wheel/odom

  # EKF が IMU を 1 度も受け取らないまま車輪だけで回っていないか。
  # frame_id の食い違いで IMU が丸ごと捨てられても robot_localization は
  # transform_timeout ぶん黙るだけで、エラーも警告も出さない。
  check_ekf_gets_imu() {
    local subs
    subs="$(topic_subscribers /imu/mid360)"
    echo "${subs:-購読者なし}"
    grep -q 'ekf_filter_node' <<<"${subs}"
  }
  item "EKF が /imu/mid360 を購読している" check_ekf_gets_imu

  # ジャイロは電源投入時バイアスが大きい (この個体は z +0.80 deg/s)。
  # prepare_mid360_imu が起動後の静止区間から測って引くが、**動いていると測れず
  # ログに still moving が出るだけで補正なしのまま通る**。
  if [[ -z "${CHECKLIST_NATIVE:-}" ]]; then
    check_bias_measured() {
      local logs
      logs="$(compose logs --no-color raspicat 2>/dev/null | grep -i 'still moving\|bias' | tail -n 3)"
      [[ -n "${logs}" ]] || {
        echo "ログに手がかりが無い (compose logs が流れた)"
        return 0
      }
      echo "$(tr '\n' ' ' <<<"${logs}")"
      ! grep -qi 'still moving' <<<"${logs}"
    }
    item_warn "起動時のジャイロバイアス補正が通っている" check_bias_measured
  else
    skip "起動時のジャイロバイアス補正" "コンテナのログを読めない"
  fi
else
  result CHECK "構成" "use_mid360_imu=false (ドライバが /odom と TF を持つ)"
  skip "IMU 関係" "EKF が居ない構成"
fi

# ── 静止しているのに積算していないか ────────────────────────────────────────
# ジャイロのバイアスが残っていると、止めたまま置いてあるだけで姿勢が回り続ける。
# 走らせずに分かる唯一の場所なので、ここで測る。
odom_pose() {
  ros_run 10 topic echo --once --field pose.pose /odom 2>/dev/null |
    sed -n 's/^[[:space:]]*\(x\|y\|z\|w\):[[:space:]]*//p' | tr '\n' ' '
}
check_odom_still() {
  local a b
  a="$(odom_pose)"
  [[ -n "${a}" ]] || {
    echo "/odom を読めない"
    return 1
  }
  sleep 10
  b="$(odom_pose)"
  awk -v a="${a}" -v b="${b}" '
    BEGIN {
      na = split(a, A, " "); nb = split(b, B, " ")
      if (na < 7 || nb < 7) { print "pose を読めない"; exit 1 }
      d = sqrt((B[1] - A[1]) ^ 2 + (B[2] - A[2]) ^ 2)
      # 平面なので yaw = 2 * atan2(qz, qw)。
      ya = 2 * atan2(A[6], A[7]); yb = 2 * atan2(B[6], B[7])
      dy = (yb - ya) * 180 / 3.14159265358979
      while (dy > 180) dy -= 360
      while (dy < -180) dy += 360
      printf "10 秒で %.3f m / %.2f deg\n", d, dy
      exit !(d < 0.02 && (dy < 2 && dy > -2))
    }'
}
# ジャイロのバイアスが引けているかは、ログではなく値で見るのが確か。
# **prepare_mid360_imu が通したあとの /imu/mid360 を見る** (生の /livox/imu には
# バイアスが乗ったままなので、そちらを見ると常に落ちる)。この個体の生値は
# z +0.013960 rad/s = +0.80 deg/s で、補正が効いていればその 1/10 以下に落ちる。
# **1 発だけ取って判定しないこと。** 100Hz を超える MEMS の 1 サンプルは
# それ自体が ±0.2 deg/s を軽く超えて散るので、健全な機体でも当たり外れで
# WARN が出る。数秒ぶんを平均して初めて閾値が意味を持つ。
check_gyro_bias() {
  local vals
  vals="$(ros_run 5 topic echo --field angular_velocity.z /imu/mid360 2>/dev/null |
    grep -E '^-?[0-9]' | tr '\n' ' ')"
  awk -v v="${vals}" '
    BEGIN {
      n = split(v, V, " ")
      if (n < 20) { printf "/imu/mid360 を十分に読めない (%d 通)\n", n; exit 1 }
      for (i = 1; i <= n; i++) s += V[i]
      z = s / n * 180 / 3.14159265358979
      printf "z 平均 %+.3f deg/s / %d 通 (無補正なら +0.80 前後)\n", z, n
      exit !(z < 0.2 && z > -0.2)
    }'
}

if confirm "機体は静止しているか (15 秒ほどそのままにする)"; then
  if has_topic /imu/mid360; then
    item_warn "静止時のジャイロにバイアスが残っていない" check_gyro_bias
    on_fail && diagnose "ジャイロのバイアスが引けていない" \
      "起動直後に機体が動いていたか|prepare_mid360_imu は起動後の静止区間から測る。動いていると測れないまま**補正なしで通り、ログに still moving が出るだけ**|機体を静止させたまま docker compose restart raspicat" \
      "0.80 deg/s 前後そのままか|補正が 1 度も入っていない|prepare_mid360_imu が居るか (0301 のノード一覧) と、その起動ログを見る"
  else
    skip "静止時のジャイロ" "/imu/mid360 が無い (Mid-360 の構成ではない)"
  fi
  item_warn "静止 10 秒で /odom が積算しない" check_odom_still
else
  skip "静止時のジャイロ" "静止を確認できない"
  skip "静止 10 秒で /odom が積算しない" "静止を確認できない"
fi

finish
