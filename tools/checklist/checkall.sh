#!/usr/bin/env bash
# 航空機のチェックリスト。section-*.sh をファイル名順に全部まわす。
#
#   tools/checklist/checkall.sh                    全部 (機体は動かさない)
#   tools/checklist/checkall.sh --list             何が走るかだけ見る
#   tools/checklist/checkall.sh --only 0401,0501   その section だけ
#   tools/checklist/checkall.sh --from 0601        その番号以降
#   tools/checklist/checkall.sh --armed            機体を動かす section も走らせる
#   tools/checklist/checkall.sh --non-interactive  人に聞く項目を全部 SKIP にする
#   tools/checklist/checkall.sh --stop-on-fail     FAIL が 1 つ出たらそこでやめる
#   tools/checklist/checkall.sh --native           コンテナ越しでなくホストの ros2 を使う
#
# 番号は aabb。**aa = 種 (フェーズ)、bb = その中の項**で、どちらも 01 から。
# 種 01 の項 01 なら 0101。glob の順 = 実行順 = 危険度の順になっている
# (静的で安全なものが先、機体が動くものが最後)。項を足すときは bb を、
# 段を足すときは aa を増やす。
#
#   01 静的検査   02 インフラ   03 ROS グラフ   04 LiDAR   05 IMU/EKF
#   06 モータドライバ   07 車輪 (動く)   08 オドメトリ (動く)
#   09 自己位置   10 ナビゲーション
#
# 既定は**最後まで走って最後に集計**する。触る前に全体像が要るため。止まるのは
# その section の中で require が落ちたときだけ (残りを飛ばして次の section へ)。
# --stop-on-fail を付けると FAIL が 1 つ出た時点で全部やめる。
#
# 機体が動く項は、**動かす前に必ず「これから何が起きるか」を出して [y/N] を聞く**。
# 既定は N で、非対話では必ず「いいえ」に倒れる (同意を推測しない)。走ったあとは
# 期待する挙動を出して [Y/n] を聞き、n なら原因の切り分けへ枝分かれする。
#
# **このツールは config/ の下に一切書かない** (書くと config_sentinel が launch を
# 落とす)。読み方と道具は lib.sh の冒頭。

set -uo pipefail

DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

ONLY=""
FROM=""
LIST=""
STOP_ON_FAIL=""

usage() {
  # 冒頭のコメント塊がそのままヘルプ。二重に持つと片方が古くなる。
  sed -n '2,/^[^#]/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'
}

while (($# > 0)); do
  case "$1" in
    --list) LIST=1 ;;
    --only)
      ONLY="${2:-}"
      shift
      ;;
    --from)
      FROM="${2:-}"
      shift
      ;;
    --armed) export CHECKLIST_ARMED=1 ;;
    --non-interactive) export CHECKLIST_NONINTERACTIVE=1 ;;
    --native) export CHECKLIST_NATIVE=1 ;;
    --stop-on-fail) STOP_ON_FAIL=1 ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      echo "不明な引数: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

wanted() {
  local id="$1"
  if [[ -n "${ONLY}" ]]; then
    [[ ",${ONLY}," == *",${id},"* ]] || return 1
  fi
  if [[ -n "${FROM}" ]]; then
    [[ "${id}" > "${FROM}" || "${id}" == "${FROM}" ]] || return 1
  fi
  return 0
}

sections=()
for path in "${DIR}"/section-*.sh; do
  [[ -e "${path}" ]] || continue
  name="${path##*/}"
  id="${name:8:4}"
  wanted "${id}" || continue
  sections+=("${path}")
done

if ((${#sections[@]} == 0)); then
  echo "走らせる section がありません。" >&2
  exit 2
fi

# --only 0401,0510 のような綴り違いは、**片方だけが走って全部走ったように見える**。
# 番号を足したあとの打ち間違いが一番危ないので、ここで止める。
if [[ -n "${ONLY}" ]]; then
  missing=()
  for id in ${ONLY//,/ }; do
    compgen -G "${DIR}/section-${id}-*.sh" >/dev/null || missing+=("${id}")
  done
  if ((${#missing[@]} > 0)); then
    echo "--only にその番号の section がありません: ${missing[*]}" >&2
    echo "--list で一覧を見てください。" >&2
    exit 2
  fi
fi

if [[ -n "${LIST}" ]]; then
  for path in "${sections[@]}"; do
    name="${path##*/}"
    printf '%s  %s\n' "${name:8:4}" "${name:13:${#name}-16}"
  done
  exit 0
fi

TALLY="$(mktemp)"
export CHECKLIST_TALLY="${TALLY}"
trap 'rm -f "${TALLY}"' EXIT

failed=()
aborted=()
result_missing=()
for path in "${sections[@]}"; do
  name="${path##*/}"
  before="$(wc -l <"${TALLY}")"
  bash "${path}"
  rc=$?
  if (($(wc -l <"${TALLY}") > before)); then
    # 打ち切った section にも FAIL が入っていることがある (前提そのものが落ちた
    # とき)。終了コードだけで振り分けると、その FAIL が一覧から消える。
    [[ "$(awk 'END { print $3 }' "${TALLY}")" != "0" ]] && failed+=("${name:8:4}")
  else
    # finish に届かずに死んだ。集計に 1 行も足していないので、黙って通ったように
    # 見えてしまう。
    result_missing+=("${name:8:4}")
  fi
  ((rc == 2)) && aborted+=("${name:8:4}")
  if [[ -n "${STOP_ON_FAIL}" && "${rc}" == 1 ]]; then
    echo
    echo "FAIL が出たので中断します (--stop-on-fail)。"
    break
  fi
done

c=0 w=0 f=0 s=0
while read -r a b d e _; do
  c=$((c + a))
  w=$((w + b))
  f=$((f + d))
  s=$((s + e))
done <"${TALLY}"

echo
echo "===================================================================="
printf '  合計   CHECK %d / WARN %d / FAIL %d / SKIP %d\n' "${c}" "${w}" "${f}" "${s}"
((${#failed[@]} > 0)) && printf '  FAIL のあった section : %s\n' "${failed[*]}"
((${#aborted[@]} > 0)) && printf '  前提不足で飛ばした    : %s\n' "${aborted[*]}"
((${#result_missing[@]} > 0)) && printf '  集計に届かず落ちた    : %s\n' "${result_missing[*]}"
if [[ -z "${CHECKLIST_ARMED:-}" ]]; then
  echo "  機体を動かす section は走らせていない (--armed で有効)。"
fi
echo "===================================================================="

((f > 0 || ${#result_missing[@]} > 0)) && exit 1
((${#aborted[@]} > 0)) && exit 2
exit 0
