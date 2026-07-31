#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
source "$(cd -- "${DOCKER_DIR}/../common/lib" && pwd)/compose.sh"

COMPOSE_FILE="${DOCKER_DIR}/compose.yaml"
SERVICE="${CONTROL_SERVICE:-ros2}"
MOTOR_SERVICE="${MOTOR_SERVICE:-/motor_power}"
CMD_VEL_TOPIC="${CMD_VEL_TOPIC:-/cmd_vel}"
ROS_TIMEOUT="${ROS_TIMEOUT:-10}"
TELEOP_LINEAR_SPEED="${TELEOP_LINEAR_SPEED:-0.2}"
TELEOP_ANGULAR_SPEED="${TELEOP_ANGULAR_SPEED:-1.0}"
JOYSTICK_ID="${JOYSTICK_ID:-0}"
JOYSTICK_CONFIG="${JOYSTICK_CONFIG:-xbox}"

COMPOSE=()

usage() {
  cat <<'EOF'
使い方:
  control.sh motor on       モーター電源を入れる
  control.sh motor off      停止指令を送ってからモーター電源を切る
  control.sh stop           /cmd_vel へ停止指令を1回送る
  control.sh teleop keyboard キーボードで操作する（Ctrl-Cで終了）
  control.sh teleop joystick ジョイスティックで操作する（Ctrl-Cで終了）
  control.sh status         コンテナ、ROSノード、モーターサービスを確認する
  control.sh nodes          ROSノード一覧を表示する
  control.sh topics         ROSトピック一覧を表示する
  control.sh services       ROSサービス一覧を表示する
  control.sh ros ARGS...    任意の ros2 コマンドを実行する
  control.sh logs [ARGS...] コンテナのログを表示する（例: logs -f）
  control.sh help           このヘルプを表示する

環境変数:
  CONTROL_SERVICE  Composeサービス名（既定: ros2）
  MOTOR_SERVICE    モーター電源サービス（既定: /motor_power）
  CMD_VEL_TOPIC    速度指令トピック（既定: /cmd_vel）
  ROS_TIMEOUT      ROS操作のタイムアウト秒数（既定: 10）
  TELEOP_LINEAR_SPEED  キーボード操作の並進速度 m/s（既定: 0.2）
  TELEOP_ANGULAR_SPEED キーボード操作の旋回速度 rad/s（既定: 1.0）
  JOYSTICK_ID      joyデバイスID（既定: 0）
  JOYSTICK_CONFIG  teleop_twist_joyの設定名（既定: xbox）
EOF
}

die() {
  echo "エラー: $*" >&2
  exit 1
}

require_no_args() {
  (($# == 0)) || die "余分な引数があります: $*"
}

init_docker() {
  # 到達できない理由は compose_init 側が出す。
  compose_init "${COMPOSE_FILE}" || exit 1
}

is_running() {
  compose_is_running "${SERVICE}"
}

ensure_running() {
  compose_ensure_running "${SERVICE}"
}

run_ros() {
  compose exec -T "${SERVICE}" /ros_entrypoint.sh ros2 "$@"
}

run_ros_interactive() {
  # teleop_twist_keyboard reads raw keystrokes from the controlling terminal.
  compose exec "${SERVICE}" /ros_entrypoint.sh ros2 "$@"
}

run_ros_timed() {
  compose exec -T "${SERVICE}" \
    timeout --foreground "${ROS_TIMEOUT}s" /ros_entrypoint.sh ros2 "$@"
}

publish_stop() {
  run_ros_timed topic pub --once "${CMD_VEL_TOPIC}" \
    geometry_msgs/msg/Twist '{}'
}

set_motor_power() {
  local enabled="$1"
  run_ros_timed service call "${MOTOR_SERVICE}" \
    std_srvs/srv/SetBool "{data: ${enabled}}"
}

run_keyboard_teleop() {
  run_ros_interactive run teleop_twist_keyboard teleop_twist_keyboard \
    --ros-args \
    -p "speed:=${TELEOP_LINEAR_SPEED}" \
    -p "turn:=${TELEOP_ANGULAR_SPEED}" \
    -r "cmd_vel:=${CMD_VEL_TOPIC}"
}

run_joystick_teleop() {
  run_ros launch teleop_twist_joy teleop-launch.py \
    "joy_config:=${JOYSTICK_CONFIG}" \
    "joy_dev:=${JOYSTICK_ID}" \
    "joy_vel:=${CMD_VEL_TOPIC}"
}

show_status() {
  compose ps "${SERVICE}"

  if ! is_running; then
    echo "ROS状態: ${SERVICE} コンテナは停止しています。"
    return
  fi

  echo
  echo "ROSノード:"
  run_ros node list

  local service_type
  service_type="$(run_ros service type "${MOTOR_SERVICE}" 2>/dev/null || true)"
  if [[ -n "${service_type}" ]]; then
    echo "モーターサービス: ${MOTOR_SERVICE} (${service_type})"
  else
    echo "モーターサービス: ${MOTOR_SERVICE} は見つかりません。" >&2
  fi
}

main() {
  local command="${1:-help}"
  if (($# > 0)); then
    shift
  fi

  case "${command}" in
    help|-h|--help)
      require_no_args "$@"
      usage
      return
      ;;
    motor)
      local state="${1:-}"
      [[ -n "${state}" ]] || die "motor の後に on または off を指定してください。"
      shift
      require_no_args "$@"
      [[ "${state}" == "on" || "${state}" == "off" ]] || \
        die "不明な motor 操作です: ${state}（on または off を指定してください）"
      [[ "${ROS_TIMEOUT}" =~ ^[1-9][0-9]*$ ]] || die "ROS_TIMEOUT は1以上の整数で指定してください。"
      init_docker
      ensure_running
      case "${state}" in
        on)
          set_motor_power true
          ;;
        off)
          if ! publish_stop; then
            echo "警告: 停止指令の送信を確認できませんでした。モーター電源OFFを続行します。" >&2
          fi
          set_motor_power false
          ;;
      esac
      ;;
    stop)
      require_no_args "$@"
      [[ "${ROS_TIMEOUT}" =~ ^[1-9][0-9]*$ ]] || die "ROS_TIMEOUT は1以上の整数で指定してください。"
      init_docker
      ensure_running
      publish_stop
      ;;
    teleop)
      local input="${1:-}"
      [[ -n "${input}" ]] || die "teleop の後に keyboard または joystick を指定してください。"
      shift
      require_no_args "$@"
      [[ "${input}" == "keyboard" || "${input}" == "joystick" ]] || \
        die "不明な teleop 入力です: ${input}（keyboard または joystick を指定してください）"
      init_docker
      ensure_running
      case "${input}" in
        keyboard)
          run_keyboard_teleop
          ;;
        joystick)
          [[ "${JOYSTICK_ID}" =~ ^[0-9]+$ ]] || die "JOYSTICK_ID は0以上の整数で指定してください。"
          run_joystick_teleop
          ;;
      esac
      ;;
    status)
      require_no_args "$@"
      init_docker
      show_status
      ;;
    nodes|topics|services)
      require_no_args "$@"
      init_docker
      ensure_running
      run_ros "${command%?}" list
      ;;
    ros)
      (($# > 0)) || die "ros の後に ros2 の引数を指定してください。"
      init_docker
      ensure_running
      run_ros "$@"
      ;;
    logs)
      init_docker
      compose logs "$@" "${SERVICE}"
      ;;
    *)
      usage >&2
      die "不明なサブコマンドです: ${command}"
      ;;
  esac
}

main "$@"
