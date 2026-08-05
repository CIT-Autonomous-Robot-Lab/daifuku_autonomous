#!/usr/bin/env bash
# raspberrypi/ と dev/ の両イメージで共有するentrypoint。
#
# オーバーレイの集合はイメージごとに違うが、互いに素なので「あれば読む」で
# まとめられる。存在しないものは黙って飛ばすため、どちらのイメージでも
# 従来と同じ順序・同じ結果になる。
#
#   raspberrypi/: ros2_rust_ws -> ROS_WS (/opt/ros_ws)
#   dev/        : RASPICAT2_WS (/opt/raspicat2) -> AUTONOMOUS_WS (マウントした作業ツリー)
set -e

source "/opt/ros/${ROS_DISTRO:-humble}/setup.bash"

# ros2_rust だけ local_setup.bash。--merge-install したオーバーレイなので、
# 親のsetup.bashを再帰的に読み直させない。
if [[ -f /opt/ros2_rust_ws/install/local_setup.bash ]]; then
  source /opt/ros2_rust_ws/install/local_setup.bash
fi

for ws in \
  "${RASPICAT2_WS:-/opt/raspicat2}" \
  "${ROS_WS:-/opt/ros_ws}" \
  "${AUTONOMOUS_WS:-/workspaces/daifuku_autonomous}"
do
  if [[ -f "${ws}/install/setup.bash" ]]; then
    source "${ws}/install/setup.bash"
  fi
done

# 前回の `docker exec` の残骸を止めてから先へ進む。
#
# exec で立てた木は、クライアント (compose exec) が SIGINT を届けないまま死ぬと
# -- tmux のペインを閉じた、ssh が切れた、端末ごと落とした -- コンテナの PID 1 に
# 引き取られて**走り続ける**。ROS は同じ名前のノードを何個でも立てられるので、
# 気づかないまま navigation や mapping が二重三重に走り、**エラーも警告も出ない
# まま CPU だけが減る** (2026-08-05 の実機で 3 組が重なって load 12。3 つの
# elevation_filter が同じ /livox/lidar を読んで 1 コア半を食っていた)。
#
# 見分けは PPid だけでつく。exec の木はそれぞれ独立したセッションで、リーダの
# PPid は 0 (親が PID 名前空間の外にいる)。exec が死ぬと 1 へ付け替わるので、
# **PPid が 1 のものが残骸**。コンテナ自身の command はセッション 1 なので
# (init: true の tini 配下でも) そこで外れる。ros2 デーモンだけは自分で setsid
# するので常に PPid 1 になり、名指しで除いてある (消しても次の ros2 で立ち直る
# が、CLI を叩くたびに消していては遅くなるだけ)。
#
# 止めるのはプロセスグループ単位。リーダが先に死んでいても残った子は同じ pgid を
# 持ったままなので、`kill -- -<pgid>` で木ごと落ちる。
#
# **消したものは必ず名前を出す。** 黙って消すと、自分で `docker exec -d` して
# 意図的に離した木が消えたときに追えない。そういう使い方をするなら
# DAIFUKU_NO_REAP=1 で止めること。
orphan_groups() {
  local pid rest cmd fields
  for pid in $(ls /proc 2>/dev/null); do
    case "${pid}" in ''|*[!0-9]*) continue ;; esac
    [[ "${pid}" != 1 ]] || continue
    rest="$(cat "/proc/${pid}/stat" 2>/dev/null)" || continue
    # comm には空白も括弧も入りうるので、最後の ") " より後だけを見る。
    # 以降は state(0) ppid(1) pgrp(2) session(3) と並ぶ。
    read -r -a fields <<<"${rest##*") "}"
    [[ "${fields[1]}" == 1 && "${fields[3]}" != 1 ]] || continue
    cmd="$(tr '\0' ' ' < "/proc/${pid}/cmdline" 2>/dev/null)" || continue
    [[ -n "${cmd}" && "${cmd}" != *ros2cli.daemon* ]] || continue
    printf '%s %s\n' "${fields[2]}" "${cmd}"
  done
}

reap_orphans() {
  [[ -z "${DAIFUKU_NO_REAP:-}" ]] || return 0
  local -A victims=()
  local pgid cmd waited=0
  while read -r pgid cmd; do
    [[ -n "${pgid}" && -z "${victims[${pgid}]:-}" ]] || continue
    victims["${pgid}"]="${cmd}"
  done < <(orphan_groups)
  ((${#victims[@]})) || return 0

  for pgid in "${!victims[@]}"; do
    echo "entrypoint: 前回の exec の残骸を止めます (pgid ${pgid}): ${victims[${pgid}]:0:110}" >&2
    # **SIGTERM ではなく SIGINT。** Humble の rclcpp / rclpy が入れるハンドラは
    # SIGINT だけで、SIGTERM は既定動作にも落ちず**黙って無視される**
    # (2026-08-05 の実機で static_transform_publisher が 10 秒粘って SIGKILL に
    # なった)。SIGINT は Ctrl-C と同じ経路なので、ros2 launch が生きていれば
    # 子を順に畳んでから終わり、死んでいれば各ノードが自分で終わる。
    kill -INT -- "-${pgid}" 2>/dev/null || true
  done
  # 落ちるにつれて孫が PPid 1 へ付け替わるが、pgid は変わらないので同じ走査で
  # そのまま拾える。
  while ((waited < 100)) && [[ -n "$(orphan_groups)" ]]; do
    sleep 0.1
    waited=$((waited + 1))
  done
  [[ -n "$(orphan_groups)" ]] || return 0
  for pgid in "${!victims[@]}"; do
    kill -KILL -- "-${pgid}" 2>/dev/null || true
  done
  echo "entrypoint: 10 秒で終わらなかったので SIGKILL しました" >&2
}

reap_orphans

exec "$@"
