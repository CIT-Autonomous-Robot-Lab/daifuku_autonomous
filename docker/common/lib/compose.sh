#!/usr/bin/env bash
# ホスト側スクリプトで共有する Compose 操作。dot-source して compose_init を
# 呼んでから使う。呼び出し元との深さは環境ごとに違うので、パスは BASH_SOURCE から
# 各自で組み立てること (ここでハードコードしない)。

# compose_init COMPOSE_FILE [COMPOSE_FILE...]
# Docker CLI への到達手段を決める。Pi ではユーザーが docker グループに入っていない
# ことがあるので、パスワード不要の sudo にも落ちる。
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
# 起動していなければ起動する。**ビルドはしない** (暗黙にやると想定外の長時間ビルド)。
compose_ensure_running() {
  local service="$1"
  if ! compose_is_running "${service}"; then
    echo "${service} コンテナを起動します..."
    compose up -d --no-build "${service}"
  fi
}

# compose_shell SERVICE [BASH_ARGS...]
# コンテナ内でシェルを開く。ROS 環境の読み込みは /ros_entrypoint.sh に任せる。
# **-i を付けるかは呼び出し元が決める** — dev のイメージは .bashrc で base の ROS を
# 読み直すので、付けるとオーバーレイより後ろに入って AMENT_PREFIX_PATH が逆転する。
compose_shell() {
  local service="$1"
  shift
  echo "${service} コンテナに入ります。終了するには exit を実行してください。"
  compose exec "${service}" /ros_entrypoint.sh bash "$@"
}
