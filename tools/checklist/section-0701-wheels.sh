#!/usr/bin/env bash
# 種 07 駆動 / 項 01 車輪 (**機体が動く。ジャッキアップして走らせる**)。
#
# --armed が無いと走らない。動かす直前には毎回 [y/N] を聞き、既定は「いいえ」。
# 非対話では 1 度も動かない (同意を推測しない)。
#
# 見るのは**向きだけ**で、量は見ない。公式実装は車輪径を 400mm 直書きしていて
# 実機 (200mm / トレッド 350mm) と 1.96 倍違うので、指令どおりの距離は出ない。
# 量の検算は 0801 (床に降ろして巻尺) の仕事。
#
# 各項は「動かす前の許可 → 機械の判定 (オドメトリ) → 目で見た判定 → 原因の
# 切り分け」の 4 段。オドメトリは車輪の回転を積んでいるだけなので、**左右の
# 入れ替わりや空転は機械の判定を素通りする**。だから目で見た判定を必ず挟む。

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

section 0701 "車輪の駆動 (ジャッキアップ)"

armed_or_skip
need_ros

confirm_or_abort "車輪が床から浮いているか (ジャッキアップして、下に何も無いこと)"
confirm_or_abort "機体の周囲に人が居らず、モータ電源のスイッチに手が届くか"

# ここから先で死んでも 0 を出してモータ電源を落とす。自前ドライバの
# cmd_vel_timeout は既定 60 秒なので、スクリプトが落ちただけで 1 分回り続ける。
arm_safety_trap

# 自律側 (/cmd_vel、優先度 100) が出ていると手動 (/cmd_vel_teleop、10) は通らず、
# **そのときエラーも出ない**。測っても何も分からないので先に止める。
check_nav_quiet() {
  if hz_at_least /cmd_vel 0.1 3 >/dev/null 2>&1; then
    echo "自律側が出している。先にゴールを取り消すこと"
    return 1
  fi
  echo "黙っている"
}
require "自律側 /cmd_vel が黙っている" check_nav_quiet

ODOM_TOPIC=/odom
has_topic /wheel/odom && ODOM_TOPIC=/wheel/odom
result CHECK "見るオドメトリ" "${ODOM_TOPIC} (車輪の回転を積んだもの)"

# ── モータ通電 ──────────────────────────────────────────────────────────────
if ask_go "モータ電源を入れる" \
  "車輪はまだ回りません (速度指令はまだ出しません)。" \
  "ただし通電するので、指を挟まない位置に手を置くこと。"; then
  item "モータ電源を入れる" ros_run 10 service call "${MOTOR_SERVICE}" \
    std_srvs/srv/SetBool '{data: true}'
  on_fail && diagnose "モータ電源が入らない" \
    "サービス呼び出しがタイムアウトしたか|ドライバノードが active でない|0601 に戻って lifecycle get を見る" \
    "呼べたが false が返ったか|ハードウェアが応答していない (電池・スイッチ・ヒューズ)|バッテリ電圧とモータ電源スイッチを見る" \
    "Pi 5 で driver:=raspimouse になっていないか|rtmouse 用の実装は /dev/rt* を要求する|driver:=original (compose.original.yaml) へ替える"
  on_fail && abort_section
else
  abort_section
fi

# ── 測り方 ──────────────────────────────────────────────────────────────────
pose7() {
  ros_run 10 topic echo --once --field pose.pose "$1" 2>/dev/null |
    sed -n 's/^[[:space:]]*\(x\|y\|z\|w\):[[:space:]]*//p' | tr '\n' ' '
}

# delta_after_drive 並進 旋回 秒 → "前後変位[m] 回頭[deg]" の 2 数を返す。
delta_after_drive() {
  local lin="$1" ang="$2" secs="$3" a b
  a="$(pose7 "${ODOM_TOPIC}")"
  [[ -n "${a}" ]] || return 1
  drive "${lin}" "${ang}" "${secs}"
  sleep 1
  b="$(pose7 "${ODOM_TOPIC}")"
  [[ -n "${b}" ]] || return 1
  awk -v a="${a}" -v b="${b}" '
    BEGIN {
      if (split(a, A, " ") < 7 || split(b, B, " ") < 7) exit 1
      ya = 2 * atan2(A[6], A[7])
      fwd = (B[1] - A[1]) * cos(ya) + (B[2] - A[2]) * sin(ya)
      dyaw = (2 * atan2(B[6], B[7]) - ya) * 180 / 3.14159265358979
      while (dyaw > 180) dyaw -= 360
      while (dyaw < -180) dyaw += 360
      printf "%.3f %.2f\n", fwd, dyaw
    }'
}

# run_case 並進 旋回 秒 awk条件 — 条件は f (前後 m) と y (回頭 deg) で書く。
run_case() {
  local out fwd dyaw
  out="$(delta_after_drive "$1" "$2" "$3")" || {
    echo "${ODOM_TOPIC} を読めない"
    return 1
  }
  read -r fwd dyaw <<<"${out}"
  printf '前後 %+.3f m / 回頭 %+.2f deg\n' "${fwd}" "${dyaw}"
  awk -v f="${fwd}" -v y="${dyaw}" "BEGIN { exit !($4) }"
}

# 車輪が回らない / 逆に回るときの枝は 3 つの項で共通。
WHEEL_BRANCHES=(
  "両輪ともまったく回らなかったか|指令が仲裁を通っていない、またはモータ電源が落ちた|0601 に戻って /cmd_vel_teleop の購読者を見る。twist_mux:=false なら CMD_VEL_TOPIC=/cmd_vel で叩き直す"
  "両輪とも指令と逆向きに回ったか|モータ配線の極性が左右とも逆|HAT のモータ端子を左右とも差し替える。ソフト側では直さない"
  "片輪だけ回ったか|その側の配線・ドライバ出力・エンコーダのどれか|回らない側の端子を反対側へ差し替えて症状が移るか見る。移れば配線、移らなければドライバ"
  "左右が逆向きに回って機体が回頭したか|左右のモータ配線が入れ替わっている|左右の端子を入れ替える"
  "目では正しく回ったのにオドメトリが動かなかったか|エンコーダを読めていない|src/raspicat_driver のエンコーダ設定 (1118 パルス) と /dev/rt* の見え方を確かめる"
)

# ── 前進 ────────────────────────────────────────────────────────────────────
if ask_go "前進" \
  "0.10 m/s の前進指令を 3 秒。**車輪は浮いているので機体は進みません**" \
  "(車輪だけが車体前方向へ回ります)。終わったら必ずゼロ速度を出します。"; then
  bad=0
  item "前進指令でオドメトリが前へ進み、まっすぐである" \
    run_case 0.10 0.0 3 'f > 0.05 && y > -15 && y < 15'
  on_fail && bad=1
  ask_ok "目で見た前進" "左右の車輪が同じ速さで、車体の前方向へ回った"
  on_fail && bad=1
  ((bad)) && diagnose "前進が期待どおりでない" "${WHEEL_BRANCHES[@]}"
fi

# ── 後退 ────────────────────────────────────────────────────────────────────
if ask_go "後退" \
  "-0.10 m/s の後退指令を 3 秒。車輪が車体後ろ方向へ回ります。" \
  "前進が正しくてここだけ逆なら、それはソフト側の符号の話ではありません。"; then
  bad=0
  item "後退指令でオドメトリが後ろへ下がる" run_case -0.10 0.0 3 'f < -0.05'
  on_fail && bad=1
  ask_ok "目で見た後退" "左右の車輪が同じ速さで、車体の後ろ方向へ回った"
  on_fail && bad=1
  ((bad)) && diagnose "後退が期待どおりでない" \
    "前進は正しかったのに後退だけおかしいか|片方向だけ出ない = ドライバの H ブリッジ側|そのモータ出力を別チャンネルへ差し替えて症状が移るか見る" \
    "${WHEEL_BRANCHES[@]}"
fi

# ── 旋回 ────────────────────────────────────────────────────────────────────
if ask_go "その場旋回 (左)" \
  "+z 0.6 rad/s を 3 秒。左車輪が後ろ向き、右車輪が前向きに回ります" \
  "(上から見て反時計回り)。"; then
  bad=0
  item "+z 旋回指令でオドメトリが左 (反時計回り) へ回る" run_case 0.0 0.6 3 'y > 5'
  on_fail && bad=1
  ask_ok "目で見た旋回" "左車輪が後ろ向き、右車輪が前向きに、同じ速さで回った"
  on_fail && bad=1
  ((bad)) && diagnose "旋回が期待どおりでない" \
    "逆 (時計回り) に回ったか|左右のモータ配線が入れ替わっている|前進と後退が正しいならここだけ。左右の端子を入れ替える" \
    "左右の速さが違ったか|片輪の出力不足、または車輪径・トレッドの設定違い|0801 で実測との比を取る。設定は src/raspicat_driver" \
    "${WHEEL_BRANCHES[@]}"
fi

# ── 通電を切る ──────────────────────────────────────────────────────────────
item "モータ電源を切る" ros_run 10 service call "${MOTOR_SERVICE}" \
  std_srvs/srv/SetBool '{data: false}'
ask_ok "通電が切れた" "車輪が手で自由に回り、ドライバの通電表示が消えている"

finish
