#!/usr/bin/env bash
# 種 08 駆動 / 項 01 オドメトリの検算 (**床の上を実際に走る**)。
#
# 0701 が向きなら、こちらは量。**巻尺が要る。** 走った距離を人が測って入れると、
# オドメトリとの比を出す。ここが 2 倍近くずれるのは既知で、公式実装が車輪径を
# 400mm 直書きしているのに実機は 200mm (トレッド 350mm) だから — 直せないので、
# 「いくつずれているか」を数字で残すのがこの section の目的。
#
# EKF が居る構成では /odom (融合後) と /wheel/odom (車輪だけ) を並べて出す。
# 開きが大きいときは IMU の側を疑う。
#
# **0701 を先に通しておくこと。** 極性が逆のまま床に降ろすと、前へ出るつもりで
# 後ろへ走る。

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

section 0801 "オドメトリの検算 (床上)"

armed_or_skip
need_ros

confirm_or_abort "0701 (ジャッキアップでの駆動確認) を通してあるか"
confirm_or_abort "車輪を床に降ろし、前方 3m と左右 1m に人も物も無いか"
confirm_or_abort "モータ電源のスイッチに手が届き、いつでも切れるか"

arm_safety_trap

check_nav_quiet() {
  if hz_at_least /cmd_vel 0.1 3 >/dev/null 2>&1; then
    echo "自律側が出している。先にゴールを取り消すこと"
    return 1
  fi
  echo "黙っている"
}
require "自律側 /cmd_vel が黙っている" check_nav_quiet

if ask_go "モータ電源を入れる" \
  "車輪はまだ回りません。**ただし機体は床の上です。**" \
  "この先の項では実際に前へ 1m ほど走ります。"; then
  item "モータ電源を入れる" ros_run 10 service call "${MOTOR_SERVICE}" \
    std_srvs/srv/SetBool '{data: true}'
  on_fail && abort_section
else
  abort_section
fi

pose7() {
  ros_run 10 topic echo --once --field pose.pose "$1" 2>/dev/null |
    sed -n 's/^[[:space:]]*\(x\|y\|z\|w\):[[:space:]]*//p' | tr '\n' ' '
}

delta() { # delta 前 後 → "距離[m] 回頭[deg]"
  awk -v a="$1" -v b="$2" '
    BEGIN {
      if (split(a, A, " ") < 7 || split(b, B, " ") < 7) exit 1
      d = sqrt((B[1] - A[1]) ^ 2 + (B[2] - A[2]) ^ 2)
      dyaw = (2 * atan2(B[6], B[7]) - 2 * atan2(A[6], A[7])) * 180 / 3.14159265358979
      while (dyaw > 180) dyaw -= 360
      while (dyaw < -180) dyaw += 360
      printf "%.3f %.2f\n", d, dyaw
    }'
}

# 走らせて、その間の /odom (融合後) と /wheel/odom (車輪だけ) の変化を両方置く。
ODOM_DELTA=""
WHEEL_DELTA=""
run_and_report() { # run_and_report 並進 旋回 秒
  local a1 a2 b1 b2
  a1="$(pose7 /odom)"
  a2=""
  has_topic /wheel/odom && a2="$(pose7 /wheel/odom)"
  drive "$1" "$2" "$3"
  sleep 1
  b1="$(pose7 /odom)"
  b2=""
  has_topic /wheel/odom && b2="$(pose7 /wheel/odom)"
  ODOM_DELTA="$(delta "${a1}" "${b1}")"
  WHEEL_DELTA=""
  [[ -n "${a2}" ]] && WHEEL_DELTA="$(delta "${a2}" "${b2}")"
}

# ask_ratio 説明 オドメトリ値 単位 — 巻尺の値を人に入れてもらって比を出す。
ask_ratio() {
  local desc="$1" odom="$2" unit="$3" actual="" out
  if _noninteractive; then
    result SKIP "${desc}" "非対話 (巻尺の値を入れられない)"
    return 1
  fi
  printf '  [%s?    %s] %s\n         実測値 [%s] (測っていなければ空 Enter): ' \
    "$(_c $'\033[36m')" "$(_c $'\033[0m')" "${desc}" "${unit}"
  read -r actual
  if [[ -z "${actual}" ]]; then
    result SKIP "${desc}" "実測なし"
    return 1
  fi
  if out="$(awk -v o="${odom}" -v a="${actual}" '
    BEGIN {
      if (a + 0 == 0) { print "実測値が 0"; exit 1 }
      r = (o + 0) / (a + 0)
      printf "オドメトリ %.3f / 実測 %.3f = %.3f 倍", o, a, r
      exit !(r > 0.9 && r < 1.1)
    }')"; then
    result CHECK "${desc}" "${out}"
    return 0
  fi
  result FAIL "${desc}" "${out}"
  return 1
}

RATIO_BRANCHES=(
  "比が 2 倍前後 (1.8〜2.2) か|公式実装が車輪径 400mm を直書きしていて、実機は 200mm|driver:=original (src/raspicat_driver) を使う。公式実装のままでは直せない"
  "比が 1 に近いのに向きだけ違うか|オドメトリではなく走行の側の問題|0701 に戻る"
  "毎回ばらつくか|車輪の空転か、床の摩擦が足りない|別の床面で取り直す。それでも散るならエンコーダの取りこぼしを疑う"
  "/odom と /wheel/odom が大きく食い違うか|EKF に入る IMU が壊れているか捨てられている|0501 の frame_id とバイアス補正へ戻る"
)

# ── 直進 ────────────────────────────────────────────────────────────────────
if ask_go "直進 (約 1m)" \
  "0.15 m/s を 7 秒。**機体は前へ 1m ほど実際に走ります。**" \
  "走る前に開始位置に印を付けてください (あとで巻尺で測ります)。"; then
  run_and_report 0.15 0.0 7
  result CHECK "直進のオドメトリ" \
    "/odom: ${ODOM_DELTA:-?}${WHEEL_DELTA:+  /wheel/odom: ${WHEEL_DELTA}}"
  ask_ok "直進の走り" "まっすぐ前へ進み、横へ流れたり回頭したりしなかった"
  on_fail && diagnose "直進が曲がる" \
    "片側へ一定に流れたか|左右の車輪径かゲインの差|0701 の旋回で左右の速さが揃っていたか見直す" \
    "床の継ぎ目や傾斜があるか|外乱|平らな場所で取り直す"
  ask_ratio "直進のオドメトリが実測と 10% 以内で合う" "${ODOM_DELTA%% *}" m
  on_fail && diagnose "直進の比が合わない" "${RATIO_BRANCHES[@]}"
fi

# ── 旋回 ────────────────────────────────────────────────────────────────────
if ask_go "その場旋回 (約 172 度)" \
  "0.5 rad/s を 6 秒。**機体はその場で半回転ほど回ります。**" \
  "回る前に機体の向きに印を付けてください (分度器か床の目印で測ります)。"; then
  run_and_report 0.0 0.5 6
  result CHECK "旋回のオドメトリ" \
    "/odom: ${ODOM_DELTA:-?}${WHEEL_DELTA:+  /wheel/odom: ${WHEEL_DELTA}}"
  ask_ok "旋回の走り" "その場で反時計回りに回り、中心が動かなかった"
  on_fail && diagnose "旋回が期待どおりでない" \
    "回りながら前後に流れたか|左右の出力差|0701 の旋回へ戻る" \
    "回転が重い・引っかかるか|キャスタか車輪の機械的な問題|手で押して転がり抵抗を見る"
  ask_ratio "旋回のオドメトリが実測と 10% 以内で合う" "${ODOM_DELTA##* }" deg
  on_fail && diagnose "旋回の比が合わない" \
    "直進の比は合っていたのに旋回だけずれるか|車輪径ではなくトレッド (実機 350mm) の設定違い|src/raspicat_driver のトレッドを見る" \
    "${RATIO_BRANCHES[@]}"
fi

item "モータ電源を切る" ros_run 10 service call "${MOTOR_SERVICE}" \
  std_srvs/srv/SetBool '{data: false}'

finish
