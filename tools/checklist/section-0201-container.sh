#!/usr/bin/env bash
# 種 02 インフラ / 項 01 コンテナ。
#
# 「コンテナは正常に上がったように見える」が一番危ない状態なので、running だけを
# 見ずに、再起動ループとビルドの終わり方まで見る。

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
# shellcheck source=/dev/null
source "${ROOT}/docker/common/lib/compose.sh"

section 0201 "コンテナ"

# compose_init は COMPOSE 配列を作る = 副作用があるので、require (サブシェルで
# 走る) には渡せない。ここだけ手で書く。
if compose_init "${COMPOSE_FILE_PATH}" 2>/dev/null; then
  result CHECK "Docker へ接続できる" "${COMPOSE[*]:0:2}"
else
  result FAIL "Docker へ接続できる" "docker が動いているか、実行権限があるか"
  abort_section
fi
# docker | sudo -n docker + compose まで。-f と compose ファイルを落としたもの。
DOCKER_COMPOSE_BASE=("${COMPOSE[@]:0:$((${#COMPOSE[@]} - 2))}")

# Compose が .env (= COMPOSE_FILE) を読むのは**カレントディレクトリ**。リポジトリ
# ルート以外から叩くと no configuration file provided で止まる。人が素手で
# docker compose を叩くときのために、ルートで解決できることを見ておく。
check_root_compose() {
  local out
  out="$(cd "${ROOT}" && "${DOCKER_COMPOSE_BASE[@]}" config --services 2>&1)" || {
    echo "${out}"
    return 1
  }
  tr '\n' ' ' <<<"${out}"
}
item_warn "リポジトリルートで docker compose が解決できる" check_root_compose

PS_ALL="$(compose ps -a 2>/dev/null)"

svc_running() {
  compose_is_running "$1" || {
    echo "動いていない"
    return 1
  }
  echo "running"
}
item "raspicat サービスが動いている" svc_running raspicat
on_fail && diagnose "raspicat が上がらない" \
  "ps の Status が Exited (1) か|compose.common.yaml を単体で渡した (どちらのドライバか決まらない placeholder)|入口の compose.rt.yaml / compose.original.yaml を .env の COMPOSE_FILE で選ぶ" \
  "ログに configure 失敗が出ているか|Pi 5 で driver:=raspimouse を選んでいる (/dev/rt* が要る)|COMPOSE_FILE を compose.original.yaml へ替える" \
  "そもそもコンテナが作られていないか|docker compose up をリポジトリルート以外から叩いた|ルートで docker compose up -d をやり直す"

item "${CHECKLIST_SERVICE} サービスが動いている" svc_running "${CHECKLIST_SERVICE}"

# restart: unless-stopped と組むと、落ちては上がるを延々くり返す。ps の Status に
# Restarting が出ているうちは、その上で何を測っても意味がない。
check_no_restart_loop() {
  local hit
  hit="$(grep -i 'restarting' <<<"${PS_ALL}" | awk '{print $1}' | tr '\n' ' ')"
  [[ -z "${hit}" ]] || {
    echo "再起動ループ: ${hit}"
    return 1
  }
  echo "ループなし"
}
item "再起動ループしているコンテナが無い" check_no_restart_loop

# workspace-build は正常終了で消える。**デーモンが上げ直すとき (Pi の再起動) は
# depends_on が効かないので走らない**ので、C++ / Rust を直したぶんは
# docker compose up -d を人手で通すまで反映されない。
check_build_exit() {
  local line
  line="$(grep -E '(^|[[:space:]])daifuku-autonomous-build|workspace-build' <<<"${PS_ALL}" | head -n 1)"
  [[ -n "${line}" ]] || {
    echo "見あたらない (up の記録が消えている)"
    return 0
  }
  grep -q 'Exited (0)' <<<"${line}" || {
    echo "${line}"
    return 1
  }
  echo "Exited (0)"
}
item "workspace-build が正常終了している" check_build_exit

# コンテナの中の ros2 が使えるか。pkg prefix を引くのは、ros2 が動くことだけでなく
# **entrypoint がワークスペースのオーバーレイを積んでいる**ことまで見たいため。
item "コンテナ内で ros2 とオーバーレイが効いている" \
  ros_run 15 pkg prefix daifuku_bringup

# 15GB の SD に 4.33GB のイメージは入らない。ビルドキャッシュは消さないので、
# 空きが細ると次の up が途中で落ちる。
check_disk() {
  local kb gb
  kb="$(df -P "${ROOT}" 2>/dev/null | awk 'NR == 2 {print $4}')"
  [[ -n "${kb}" ]] || {
    echo "df が読めない"
    return 1
  }
  gb="$(awk -v k="${kb}" 'BEGIN { printf "%.1f", k / 1048576 }')"
  echo "${gb} GB 空き"
  awk -v k="${kb}" 'BEGIN { exit !(k > 2 * 1048576) }'
}
item_warn "ディスクの空きが 2GB 以上ある" check_disk

finish
