#!/usr/bin/env bash
# 航空機のチェックリストと同じ形で機体を点検する。section-*.sh が source する道具。
#
#   #!/usr/bin/env bash
#   source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
#   section 0401 "LiDAR"
#   need_ros
#   item "/livox/lidar が 8Hz 以上" hz_at_least /livox/lidar 8
#   on_fail && diagnose "来ない" "問い|原因|対処" "問い|原因|対処"
#   finish
#
# 判定は 4 つだけ。**CHECK** 合格 / **WARN** 直したほうがよい / **FAIL** 直すまで
# 進まない / **SKIP** 前提が無い (機体が上がっていない、人に聞けない、危険なので
# 許可が要る)。section の終了コードは 0 = FAIL なし、1 = FAIL あり、
# 2 = 前提不足で打ち切った。
#
# 機体が動く項は、**動かす前に必ず ask_go で [y/N] を聞く** (既定は N、非対話では
# 必ず「いいえ」)。動いたあとは ask_ok で期待する挙動を出して [Y/n]、落ちたら
# on_fail && diagnose で原因の枝へ入る。番号は aabb (種 aa / 項 bb、どちらも 01 から)。
#
# ## このツールが守る 2 つのこと
#
# **config/ の下には一切書かない。** 書くと config_sentinel が指紋の変化を見て
# launch を落とす。機体は restart: unless-stopped が上げ直すが、人が立てた
# navigation / mapping は終わったままになる (mapping では作りかけの地図が消える)。
# ここは読むだけ。
#
# **timeout はコンテナの中で掛ける。** ホスト側で掛けると docker のクライアントを
# 殺すだけで、コンテナの中の ros2 は走り続ける。`ros2 topic hz` を素で叩いて
# 中断したときの残骸 (docs/usage/troubleshooting.md の「そのノードだけ届かなく
# なる」) を、チェックリスト自身が作らないため。
#
# ROS を叩く口は**自動判別しない**。既定は control.sh と同じ
# `compose exec -T ros2 /ros_entrypoint.sh ros2` で、CHECKLIST_NATIVE=1 のときだけ
# ホストの ros2 を使う。Pi のホストには ros2 が居るのに実行系を消してあるので、
# 「native が使える」の自動判別は偽陽性になる。

set -uo pipefail   # -e は使わない。1 つ落ちても最後まで見たいので。

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"

CHECKLIST_SERVICE="${CHECKLIST_SERVICE:-ros2}"
COMPOSE_FILE_PATH="${COMPOSE_FILE_PATH:-${ROOT}/docker/raspberrypi/compose.common.yaml}"
ROS_TIMEOUT="${ROS_TIMEOUT:-15}"
# 人が出す速度指令の入口。twist_mux:=false で立てているなら /cmd_vel を渡すこと。
CMD_VEL_TOPIC="${CMD_VEL_TOPIC:-/cmd_vel_teleop}"
MOTOR_SERVICE="${MOTOR_SERVICE:-/motor_power}"

SECTION_ID=""
SECTION_TITLE=""
LAST_STATUS=""
N_CHECK=0
N_WARN=0
N_FAIL=0
N_SKIP=0

ROS_TOPICS=""
ROS_NODES=""
ROS_SERVICES=""
ROS_INIT_REASON=""
_ros_ready=""

# ── 表示 ────────────────────────────────────────────────────────────────────
# 点リーダで右寄せにはしない。日本語の表示幅を数えるには locale 依存の細工が
# 要るのに、ずれても分かることは何も増えないため。
_c() { [[ -t 1 ]] && printf '%s' "$1"; }

result() {
  local st="$1" text="$2" detail="${3:-}" color=""
  LAST_STATUS="${st}"
  case "${st}" in
    CHECK) color=$'\033[32m'; N_CHECK=$((N_CHECK + 1)) ;;
    WARN)  color=$'\033[33m'; N_WARN=$((N_WARN + 1)) ;;
    FAIL)  color=$'\033[31m'; N_FAIL=$((N_FAIL + 1)) ;;
    SKIP)  color=$'\033[90m'; N_SKIP=$((N_SKIP + 1)) ;;
  esac
  printf '  [%s%-5s%s] %s%s\n' \
    "$(_c "${color}")" "${st}" "$(_c $'\033[0m')" "${text}" \
    "${detail:+  — ${detail}}"
}

section() {
  SECTION_ID="$1"
  SECTION_TITLE="$2"
  printf '\n%s%s %s%s\n' "$(_c $'\033[1m')" "${SECTION_ID}" "${SECTION_TITLE}" "$(_c $'\033[0m')"
}

# finish [終了コード]
finish() {
  local forced="${1:-}"
  printf '  %s----%s CHECK %d / WARN %d / FAIL %d / SKIP %d\n' \
    "$(_c $'\033[90m')" "$(_c $'\033[0m')" "${N_CHECK}" "${N_WARN}" "${N_FAIL}" "${N_SKIP}"
  if [[ -n "${CHECKLIST_TALLY:-}" ]]; then
    printf '%d %d %d %d %s\n' "${N_CHECK}" "${N_WARN}" "${N_FAIL}" "${N_SKIP}" \
      "${SECTION_ID}" >>"${CHECKLIST_TALLY}"
  fi
  [[ -n "${forced}" ]] && exit "${forced}"
  ((N_FAIL > 0)) && exit 1
  exit 0
}

abort_section() {
  printf '  %s…… 前提が満たされないので、この section の残りは飛ばします。%s\n' \
    "$(_c $'\033[90m')" "$(_c $'\033[0m')"
  finish 2
}

# ── 検査 ────────────────────────────────────────────────────────────────────
# どれも「説明 コマンド...」の形。コマンドの終了コードが合否、標準出力の 1 行目
# あたりが右に出る補足になる。補足は各ヘルパが自分で echo する。
_run_capture() {
  local out rc
  out="$("$@" 2>&1)"
  rc=$?
  # 改行を潰して 1 行に収める。長い traceback をそのまま出しても読めない。
  printf '%s' "${out}" | tr '\n' ' ' | sed 's/  */ /g; s/^ //; s/ $//' | cut -c1-110
  return "${rc}"
}

item() {
  local desc="$1"
  shift
  local out
  if out="$(_run_capture "$@")"; then result CHECK "${desc}" "${out}"; else result FAIL "${desc}" "${out}"; fi
}

# 落ちても WARN 止まり。推奨だが、それだけで機体が壊れるわけではないもの。
item_warn() {
  local desc="$1"
  shift
  local out
  if out="$(_run_capture "$@")"; then result CHECK "${desc}" "${out}"; else result WARN "${desc}" "${out}"; fi
}

# 前提。落ちたらこの section の残りは意味を持たないので打ち切る。
require() {
  local desc="$1"
  shift
  local out
  if out="$(_run_capture "$@")"; then
    result CHECK "${desc}" "${out}"
  else
    result FAIL "${desc}" "${out}"
    abort_section
  fi
}

skip() { result SKIP "$1" "${2:-}"; }

# ── 人が答える項目 ──────────────────────────────────────────────────────────
# 3 つある。**前提の確認 (confirm)**、**動かす前の許可 (ask_go)**、**動いたあとの
# 判定 (ask_ok)**。既定値の向きが違うので分けてある。
#
#   confirm  yes と打たせる。惰性の Enter で通らない。非対話は SKIP で 1
#   ask_go   [y/N]。**既定は「いいえ」。** 非対話は必ず「いいえ」
#   ask_ok   [Y/n]。既定は「はい」。非対話は SKIP
#
# 非対話で ask_go が通らないのは仕様。同意を推測して機体を走らせない。

_noninteractive() { [[ -n "${CHECKLIST_NONINTERACTIVE:-}" || ! -t 0 ]]; }

# 惰性で通せない確認。安全の前提 (ジャッキアップしたか等) に使う。
confirm() {
  local prompt="$1" ans=""
  if _noninteractive; then
    result SKIP "${prompt}" "非対話"
    return 1
  fi
  printf '  [%s?    %s] %s\n         確認できたら yes と打つ: ' \
    "$(_c $'\033[36m')" "$(_c $'\033[0m')" "${prompt}"
  read -r ans
  if [[ "${ans}" == "yes" ]]; then
    result CHECK "${prompt}"
    return 0
  fi
  result FAIL "${prompt}" "yes 以外が入力された"
  return 1
}

confirm_or_abort() { confirm "$1" || abort_section; }

# ask_go "見出し" "これから起きること" ... — **機体が動く直前に必ず通す門**。
# 何が起きるかを先に全部出してから [y/N]。既定は N。
ask_go() {
  local title="$1" ans=""
  shift
  printf '\n  %s┌─ これから機体が動きます: %s%s\n' "$(_c $'\033[33m')" "${title}" "$(_c $'\033[0m')"
  local line
  for line in "$@"; do
    printf '  %s│%s   %s\n' "$(_c $'\033[33m')" "$(_c $'\033[0m')" "${line}"
  done
  printf '  %s└─ いつでもモータ電源を切れるようにしておくこと。%s\n' \
    "$(_c $'\033[33m')" "$(_c $'\033[0m')"
  if _noninteractive; then
    result SKIP "${title}" "非対話では動かさない"
    return 1
  fi
  printf '         進めてよいか [y/N]: '
  read -r ans
  [[ "${ans}" == [yY] ]] && return 0
  result SKIP "${title}" "人が見送った"
  return 1
}

# ask_ok "説明" "期待する挙動" — 動いたあとの判定。既定は Y。
# n が返ると LAST_STATUS が FAIL になるので、続けて on_fail && diagnose ... と書く。
ask_ok() {
  local desc="$1" expect="$2" ans=""
  if _noninteractive; then
    result SKIP "${desc}" "非対話 (期待: ${expect})"
    return 1
  fi
  printf '  [%s?    %s] %s\n         期待する挙動: %s\n         そうなったか [Y/n]: ' \
    "$(_c $'\033[36m')" "$(_c $'\033[0m')" "${desc}" "${expect}"
  read -r ans
  if [[ -z "${ans}" || "${ans}" == [yY] ]]; then
    result CHECK "${desc}"
    return 0
  fi
  result FAIL "${desc}" "期待: ${expect}"
  return 1
}

# 直前の判定が FAIL だったか。`on_fail && diagnose ...` の形で使う。
on_fail() { [[ "${LAST_STATUS}" == "FAIL" ]]; }

# diagnose "見出し" "問い|原因|対処" ... — 原因の切り分け。
# 上から順に [y/N] で聞き、**最初に「はい」と答えたところで止める**。実際の
# チェックリストと同じで、当てはまるものが 1 つ見つかればそこが枝の出口になる。
# 非対話では表をそのまま出す (聞けないので、読めるようにだけしておく)。
diagnose() {
  local title="$1" entry q rest cause act
  shift
  printf '  %s┌─ 原因の切り分け: %s%s\n' "$(_c $'\033[35m')" "${title}" "$(_c $'\033[0m')"
  for entry in "$@"; do
    q="${entry%%|*}"
    rest="${entry#*|}"
    cause="${rest%%|*}"
    act="${rest#*|}"
    if _noninteractive; then
      printf '  %s│%s  ? %s\n  %s│%s      原因: %s\n  %s│%s      対処: %s\n' \
        "$(_c $'\033[35m')" "$(_c $'\033[0m')" "${q}" \
        "$(_c $'\033[35m')" "$(_c $'\033[0m')" "${cause}" \
        "$(_c $'\033[35m')" "$(_c $'\033[0m')" "${act}"
      continue
    fi
    printf '  %s│%s  ? %s [y/N]: ' "$(_c $'\033[35m')" "$(_c $'\033[0m')" "${q}"
    local ans=""
    read -r ans
    if [[ "${ans}" == [yY] ]]; then
      printf '  %s└─ 原因: %s\n     対処: %s%s\n' \
        "$(_c $'\033[35m')" "${cause}" "${act}" "$(_c $'\033[0m')"
      return 0
    fi
  done
  if _noninteractive; then
    printf '  %s└─ (非対話なので聞かずに表だけ出した)%s\n' "$(_c $'\033[35m')" "$(_c $'\033[0m')"
  else
    printf '  %s└─ どれにも当てはまらない。docs/usage/troubleshooting.md を見ること。%s\n' \
      "$(_c $'\033[35m')" "$(_c $'\033[0m')"
  fi
  return 1
}

# ── 機体を動かす section の門 ───────────────────────────────────────────────
armed_or_skip() {
  [[ -n "${CHECKLIST_ARMED:-}" ]] && return 0
  result SKIP "機体を動かす section" "--armed が無いので実行しない"
  finish 0
}

# 途中で死んでも機体を止める。自前ドライバの cmd_vel_timeout は既定 60 秒なので、
# スクリプトが落ちただけで 1 分走り続ける。twist_mux の優先度は非常停止では
# ないので、最後の砦はモータ電源のほう。
safe_stop() {
  ros_run 5 topic pub --once "${CMD_VEL_TOPIC}" geometry_msgs/msg/Twist '{}' >/dev/null 2>&1
  ros_run 8 service call "${MOTOR_SERVICE}" std_srvs/srv/SetBool '{data: false}' >/dev/null 2>&1
}

# INT / TERM は**止めるところまでやる**。bash は INT ハンドラを走らせたあと
# 次の行から再開するので、safe_stop だけを仕掛けると Ctrl-C しても後続の走行
# 指令がそのまま続く (電源は切れているので回りはしないが、「Ctrl-C で止まる」
# という、この section が保証すべき唯一の性質が嘘になる)。
arm_safety_trap() {
  trap safe_stop EXIT
  trap 'safe_stop; exit 130' INT TERM
}

# drive 並進[m/s] 旋回[rad/s] 秒 — 指定秒だけ流して、必ず 0 を 1 発置いて終わる。
drive() {
  local lin="$1" ang="$2" secs="$3" n
  n=$((secs * 10))
  ros_run $((secs + 8)) topic pub -r 10 -t "${n}" "${CMD_VEL_TOPIC}" \
    geometry_msgs/msg/Twist "{linear: {x: ${lin}}, angular: {z: ${ang}}}" >/dev/null 2>&1
  ros_run 5 topic pub --once "${CMD_VEL_TOPIC}" geometry_msgs/msg/Twist '{}' >/dev/null 2>&1
}

# ── ROS への口 ──────────────────────────────────────────────────────────────
ros_init() {
  ROS_INIT_REASON=""
  [[ -n "${_ros_ready}" ]] && return 0
  if [[ -n "${CHECKLIST_NATIVE:-}" ]]; then
    if ! command -v ros2 >/dev/null 2>&1; then
      ROS_INIT_REASON="ros2 が PATH に無い (setup.bash を読んだか)"
      return 1
    fi
  else
    # shellcheck source=/dev/null
    source "${ROOT}/docker/common/lib/compose.sh"
    if ! compose_init "${COMPOSE_FILE_PATH}" >/dev/null 2>&1; then
      ROS_INIT_REASON="Docker へ接続できない"
      return 1
    fi
    if ! compose_is_running "${CHECKLIST_SERVICE}"; then
      ROS_INIT_REASON="${CHECKLIST_SERVICE} コンテナが動いていない"
      return 1
    fi
  fi
  _ros_ready=1
}

# ros_run 秒 ARGS... — コンテナの中で timeout を掛けて ros2 を叩く。
#
# PYTHONUNBUFFERED を立てるのは `ros2 topic hz` のため。パイプ越しだと Python の
# stdout がブロックバッファになるので、SIGINT でほどける前に -k の SIGKILL が
# 来ると**中身ごとバッファが消え、正常に出ているトピックが「1 通も来ない」に
# 化ける**。
ros_run() {
  local secs="$1"
  shift
  if [[ -n "${CHECKLIST_NATIVE:-}" ]]; then
    PYTHONUNBUFFERED=1 timeout -s INT -k 3 "${secs}" ros2 "$@"
  else
    compose exec -T -e PYTHONUNBUFFERED=1 "${CHECKLIST_SERVICE}" \
      /ros_entrypoint.sh timeout -s INT -k 3 "${secs}" ros2 "$@"
  fi
}

ros() { ros_run "${ROS_TIMEOUT}" "$@"; }

# need_ros — ROS を使う section の先頭で 1 回。一覧はここで 1 度だけ取る
# (has_topic などは item の中 = サブシェルで動くので、そこで取ると毎回叩きに行く)。
# 届かないのは FAIL ではなく SKIP。**その FAIL を出すのは 0201 の仕事**で、
# ここで各 section が同じことを言うと、本当に見たかった FAIL が埋もれる。
need_ros() {
  if ! ros_init; then
    result SKIP "ROS へ届く" "${ROS_INIT_REASON}"
    abort_section
  fi
  result CHECK "ROS へ届く" \
    "${CHECKLIST_NATIVE:+ホストの ros2}${CHECKLIST_NATIVE:-compose exec ${CHECKLIST_SERVICE}}"
  ROS_TOPICS="$(ros topic list 2>/dev/null)"
  ROS_NODES="$(ros node list 2>/dev/null)"
  ROS_SERVICES="$(ros service list 2>/dev/null)"
}

# **どれも既定の名前空間 (namespace:= 無し) を前提にしている。** namespace を
# 付けた構成では名前が一致せず、ROS 側の section が丸ごと SKIP になる。
has_topic() { grep -qx -- "$1" <<<"${ROS_TOPICS}"; }
has_node() { grep -qx -- "$1" <<<"${ROS_NODES}"; }
has_service() { grep -qx -- "$1" <<<"${ROS_SERVICES}"; }

# count_nodes 正規表現 — 同じ役の二重起動を数える。
count_nodes() { grep -cE -- "$1" <<<"${ROS_NODES}"; }

# topic info --verbose は Publisher と Subscription の 2 ブロックを出す。**片方
# だけを拾うこと。** ekf_filter_node のように同じトピックの publisher でも
# subscriber でもあるノードが居るので、両方混ぜると「出しているのが 2 つある」
# の検査が黙って通ってしまう。
_topic_side() {
  ros topic info "$2" --verbose 2>/dev/null | awk -v want="$1" '
    /^Publisher count/    { on = (want == "P"); next }
    /^Subscription count/ { on = (want == "S"); next }
    on && /Node name:/    { sub(/^[[:space:]]*Node name:[[:space:]]*/, ""); print }
  ' | sort -u | tr '\n' ' '
}
topic_publishers() { _topic_side P "$1"; }
topic_subscribers() { _topic_side S "$1"; }

# ── 判定ヘルパ ──────────────────────────────────────────────────────────────
# 落ちた理由も通った値も stdout に 1 行で出す。item がそれを右に出す。

# hz_at_least トピック 最低Hz [観測秒]
hz_at_least() {
  local topic="$1" min="$2" secs="${3:-6}" rate
  rate="$(ros_run "${secs}" topic hz --window 30 "${topic}" 2>/dev/null |
    grep -o 'average rate: *[0-9.]*' | tail -n 1 | grep -o '[0-9.]*$')"
  if [[ -z "${rate}" ]]; then
    echo "${secs} 秒で 1 通も来ない"
    return 1
  fi
  echo "${rate} Hz (最低 ${min})"
  awk -v r="${rate}" -v m="${min}" 'BEGIN { exit !(r + 0 >= m + 0) }'
}

# tf_stamp 親 子 — 最後に引けた時刻。引けなければ空。
tf_stamp() {
  ros_run 5 run tf2_ros tf2_echo "$1" "$2" 2>/dev/null |
    grep -o 'At time [0-9.]*' | tail -n 1 | grep -o '[0-9.]*$'
}

tf_ok() {
  local t
  t="$(tf_stamp "$1" "$2")"
  if [[ -z "${t}" ]]; then
    echo "$1 -> $2 が引けない"
    return 1
  fi
  echo "t=${t}"
}

# tf_advancing 親 子 — 引けるだけでなく、時刻が進んでいるか。
# /mcl_pose が 20Hz で出ているのに map->odom だけが 1 度も更新されない、という
# 壊れ方をするので「引ける」では足りない。
tf_advancing() {
  local a b
  a="$(tf_stamp "$1" "$2")"
  [[ -n "${a}" ]] || {
    echo "$1 -> $2 が引けない"
    return 1
  }
  b="$(tf_stamp "$1" "$2")"
  [[ -n "${b}" ]] || {
    echo "2 度目が引けない"
    return 1
  }
  echo "${a} -> ${b}"
  awk -v a="${a}" -v b="${b}" 'BEGIN { exit !(b + 0 > a + 0) }'
}

# param_num ノード パラメータ — 数値のパラメータを 1 つ取る。取れなければ空。
param_num() {
  ros_run 10 param get "$1" "$2" 2>/dev/null | grep -o '[-0-9][0-9.e+-]*' | tail -n 1
}

# ── 静的検査で使う小物 ──────────────────────────────────────────────────────
# 自前パッケージの場所。daifuku_config だけ src/ の下ではなく config/ に居る。
OWN_PKGS=(
  src/daifuku_bringup src/daifuku_stack src/daifuku_config_manager
  src/daifuku_rqt src/daifuku_waypoint_manager src/raspicat_driver config
)

# /proc/net/snmp の Ip: / Udp: 行を「見出し 行」の組で読む。カウンタの位置は
# カーネルの版で動くので、番号ではなく名前で引くこと。
snmp_counter() { # snmp_counter Ip ReasmFails
  awk -v sec="$1:" -v key="$2" '
    $1 == sec { if (!seen) { for (i = 2; i <= NF; i++) k[i] = $i; seen = 1 }
                else       { for (i = 2; i <= NF; i++) if (k[i] == key) print $i } }
  ' /proc/net/snmp 2>/dev/null | head -n 1
}
# config/site の値 (params.read_site_file と同じ規則: 1 つめの空でない非コメント行)
site_name() {
  sed -e 's/[[:space:]]*$//' "${ROOT}/config/site" 2>/dev/null |
    grep -v '^[[:space:]]*#' | grep -v '^[[:space:]]*$' | head -n 1
}

# Pi の機種名。取れないときは空。
pi_model() {
  [[ -r /proc/device-tree/model ]] || return 0
  tr -d '\0' </proc/device-tree/model
}

is_pi4() { [[ "$(pi_model)" == *"Raspberry Pi 4"* ]]; }
is_pi5() { [[ "$(pi_model)" == *"Raspberry Pi 5"* ]]; }
