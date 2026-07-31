#!/usr/bin/env bash
# ホスト側スクリプトで共有するCompose操作。dot-sourceして使う:
#
#   COMMON_LIB="$(cd -- "${SCRIPT_DIR}/../../common/lib" && pwd)"
#   source "${COMMON_LIB}/compose.sh"
#   compose_init "${COMPOSE_FILE}"
#   compose ps
#
# 呼び出し元との深さは環境ごとに違うので、パスはBASH_SOURCEから各自で組み立てる
# こと（ここでハードコードしない）。

# compose_init COMPOSE_FILE [COMPOSE_FILE...]
#
# Docker CLIへの到達手段を決めて COMPOSE 配列を用意する。Piではユーザーが
# dockerグループに入っていないことがあるので、パスワード不要のsudoにも落ちる。
compose_init() {
  (($# > 0)) || {
    echo "compose_init: compose ファイルを1つ以上指定してください。" >&2
    return 2
  }

  local docker_cli
  if docker info >/dev/null 2>&1; then
    docker_cli=(docker)
  elif sudo -n docker info >/dev/null 2>&1; then
    docker_cli=(sudo -n docker)
  else
    echo "Dockerへ接続できません。Dockerが起動しているか、実行権限があるか確認してください。" >&2
    return 1
  fi

  COMPOSE=("${docker_cli[@]}" compose)
  local file
  for file in "$@"; do
    COMPOSE+=(-f "${file}")
  done
}

# compose ARGS...
compose() {
  ((${#COMPOSE[@]} > 0)) || {
    echo "compose: 先に compose_init を呼んでください。" >&2
    return 2
  }
  "${COMPOSE[@]}" "$@"
}

# compose_is_running SERVICE
compose_is_running() {
  [[ -n "$(compose ps --status running --quiet "$1")" ]]
}

# compose_ensure_running SERVICE
#
# 起動していなければ起動する。イメージのビルドはしない（ビルドは各環境の
# up スクリプトの仕事で、ここで暗黙にやると想定外の長時間ビルドになる）。
compose_ensure_running() {
  local service="$1"
  if ! compose_is_running "${service}"; then
    echo "${service} コンテナを起動します..."
    compose up -d --no-build "${service}"
  fi
}

# compose_shell SERVICE [BASH_ARGS...]
#
# コンテナ内でシェルを開く。ROS環境の読み込みはイメージ側の
# /ros_entrypoint.sh に任せる（オーバーレイの一覧を持つのは1箇所でよい）。
#
# bash に渡すフラグは呼び出し元が決める。-i を付けるかどうかはイメージ依存で、
# ここで決め打ちにはできない。dev のイメージは .bashrc で
# /opt/ros/humble/setup.bash を読み直すので、-i を付けると entrypoint が積んだ
# ワークスペースのオーバーレイより後ろにベースのROSが入り、AMENT_PREFIX_PATH の
# 優先順位が引っくり返る。
compose_shell() {
  local service="$1"
  shift
  echo "${service} コンテナに入ります。終了するには exit を実行してください。"
  compose exec "${service}" /ros_entrypoint.sh bash "$@"
}
