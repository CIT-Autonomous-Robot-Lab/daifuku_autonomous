#!/usr/bin/env bash
# raspberrypi/ と dev/ の両イメージで共有する entrypoint。オーバーレイの集合は
# イメージごとに違うが互いに素なので、「あれば読む」でまとめてある。
set -e

source "/opt/ros/${ROS_DISTRO:-humble}/setup.bash"

# ros2_rust だけ local_setup.bash (--merge-install したオーバーレイなので、親の
# setup.bash を再帰的に読み直させない)。
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

# 前回の `docker exec` の残骸を止めてから先へ進む。exec の木はクライアントが
# SIGINT を届けないまま死ぬと PID 1 に引き取られて**走り続ける** — ROS は同名の
# ノードを何個でも立てられるので、**エラーも警告も出ないまま navigation が二重三重に
# 走って CPU だけが減る** (2026-08-05 の実機で load 12)。
#
# **見分けは PPid が 1 であること** (exec の木は独立したセッションで、リーダの PPid は
# exec が死ぬと 0 -> 1 へ付け替わる)。コンテナ自身の command はセッション 1 なので
# 外れる。ros2 デーモンだけは自分で setsid して常に PPid 1 なので名指しで除く。
# 止めるのはプロセスグループ単位 (リーダが先に死んでも子は同じ pgid を持つ)。
# **消したものは必ず名前を出す。** 意図して離した木があるなら DAIFUKU_NO_REAP=1。
orphan_groups() {
  local pid rest cmd fields
  for pid in $(ls /proc 2>/dev/null); do
    case "${pid}" in ''|*[!0-9]*) continue ;; esac
    [[ "${pid}" != 1 ]] || continue
    rest="$(cat "/proc/${pid}/stat" 2>/dev/null)" || continue
    # comm には空白も括弧も入るので、最後の ") " より後だけを見る
    # (以降は state(0) ppid(1) pgrp(2) session(3))。
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
    # SIGINT だけで、**SIGTERM は既定動作にも落ちずに黙って無視される**。
    kill -INT -- "-${pgid}" 2>/dev/null || true
  done
  # 孫が PPid 1 へ付け替わっても pgid は変わらないので、同じ走査で拾える。
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
