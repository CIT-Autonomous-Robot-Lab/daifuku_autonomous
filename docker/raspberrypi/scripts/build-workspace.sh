#!/usr/bin/env bash
# `docker compose up` のたびに走るワークスペースビルド。ソースは compose が
# マウントし、前回の build/ はホストに残るので差分だけが建て直される。
# shell.nixの環境は /ros_entrypoint.sh が読み込み済み。
#
# **パッケージは名前で選ばない。** src/ にあるものは全部建て、建てられないものだけを
# 下の SKIP_PACKAGES で外す。パッケージを足してもこのファイルは直さなくてよい
# (Pi で建たないものを足したときだけ SKIP_PACKAGES に足す)。
#
# 依存はshell.nixで固定する。追加時は `docker compose build` で反映する。
set -eo pipefail

WS="${ROS_WS:-/opt/ros_ws}"
# 既定 4 = Pi 4 の全コア (release の rustc 2 本で available 2.5GB 残る実測)。
# cargo 側の並列数は既定 (nproc) のまま。
BUILD_JOBS="${BUILD_JOBS:-4}"
REPOS_FILE="${WS}/daifuku_autonomous.repos"

# **Pi では建てないもの。** 実機プロファイルに rqt と RViz が
# 無いので、この 2 つを建てるとビルドが落ちる。
SKIP_PACKAGES=(daifuku_rqt daifuku_waypoint_manager)

cd "${WS}"
mkdir -p src

# クレートのレジストリはbuild/に置く。Nixストアのツールチェーンは読み取り専用で使う。
export CARGO_HOME="${WS}/build/.cargo"
mkdir -p "${CARGO_HOME}"

# 外部パッケージの取得。**ネットワークがあれば毎回 vcs import する**
# (--skip-existing は付けないので、repos の version が変わればチェックアウトも動く。
# ただし version がブランチ名のときは、ローカルのブランチを checkout するだけで
# 上流へ進めはしない — vcstool の仕様)。
#
# 無ければ呼ばない — 既存リポジトリにも git fetch が走るので、`Could not resolve
# host` で落ちて `set -e` で up ごと止まる。一度取り込んであればオフラインでも up できる。
#
# どちらでも最後に揃っているかを確かめ、足りなければ名前を並べて落とす (黙って
# 進めると colcon が「そんなパッケージは無い」という無関係な顔で落ちる)。
if [[ -f "${REPOS_FILE}" ]]; then
  # repos ファイルのキー (WS からの相対パス) と URL を 1 行ずつ。
  mapfile -t repo_entries < <(python3 - "${REPOS_FILE}" <<'PY'
import sys
import yaml

# repos ファイルには日本語のコメントが入っている。ロケール依存の既定エンコーディング
# を踏まないよう、バイト列で渡して PyYAML に UTF-8 と判定させる。
with open(sys.argv[1], 'rb') as f:
    repos = yaml.safe_load(f)['repositories'] or {}
for path, spec in repos.items():
    print(f"{path}\t{spec.get('url', '')}")
PY
  )
  # mapfile は右辺が失敗しても成功するので、空なら自分で落とす (パースに失敗した
  # まま「揃っている」と見えると無関係な失敗に化ける)。
  if [[ ${#repo_entries[@]} -eq 0 ]]; then
    echo "daifuku_autonomous.repos を読めませんでした" >&2
    exit 1
  fi
  repo_paths=("${repo_entries[@]%%$'\t'*}")
  probe_url="${repo_entries[0]#*$'\t'}"

  # 疎通の確認は repos の先頭のリモートへの ls-remote (DNS も TLS も込みで見る)。
  # 認証を求められて固まらないよう、プロンプトは切る。
  export GIT_TERMINAL_PROMPT=0
  if timeout 15 git ls-remote -q --exit-code "${probe_url}" HEAD >/dev/null 2>&1; then
    echo "ネットワークに繋がっているので vcs import します"
    # 失敗しても止めない (手を入れたリポジトリの checkout 衝突などで機体が上がらなく
    # なるのを避ける)。足りないものは下で落とす。
    vcs import . < "${REPOS_FILE}" \
      || echo "警告: vcs import が失敗しました。手元のチェックアウトのまま建てます" >&2
  else
    echo "ネットワークに繋がらないので vcs import を省略します"
  fi

  # 空ディレクトリは「無い」とみなす。
  missing=()
  for repo_path in "${repo_paths[@]}"; do
    [[ -n "$(ls -A "${WS}/${repo_path}" 2>/dev/null)" ]] || missing+=("${repo_path}")
  done
  if [[ ${#missing[@]} -ne 0 ]]; then
    printf '取得できませんでした (ネットワークが要ります): %s\n' "${missing[*]}" >&2
    exit 1
  fi
fi

# livox_ros_driver2 は ROS 2 用の manifest と launch を上流で別名に置いている。
# タイムスタンプは保存しない (Windows/Podman のバインドマウントが設定できない)。
if [[ -d src/livox_ros_driver2 ]]; then
  cp --remove-destination src/livox_ros_driver2/package_ROS2.xml src/livox_ros_driver2/package.xml
  mkdir -p src/livox_ros_driver2/launch
  cp -f src/livox_ros_driver2/launch_ROS2/* src/livox_ros_driver2/launch/
fi

# Rust を別段へ飛ばすと、それに依存する launch の package.sh が揃わない。
# CMake/Python/Rust を同じビルドに載せ、colcon に依存順を任せる。
colcon list --base-paths src | awk '$1 == "vi_planner" && $3 ~ /cargo/ {found=1} END {exit !found}' || {
  echo "vi_planner が colcon から見えません (Cargo.toml の additional-packages を確認)" >&2
  exit 1
}

# --symlink-install なので launch や yaml は install/ から src/ への symlink になり、
# 直しただけならビルドは要らない。
colcon build --base-paths src --merge-install --symlink-install \
    --parallel-workers "${BUILD_JOBS}" \
    --packages-skip "${SKIP_PACKAGES[@]}" \
    --cargo-args --profile release \
    --cmake-args -DROS_EDITION=ROS2 \
                 -DEMCL3D_ENABLE_VULKAN="${EMCL3D_ENABLE_VULKAN:-OFF}" \
                 -DDISTRO_ROS="${ROS_DISTRO}" \
                 -DCMAKE_BUILD_TYPE=Release

printf '\nワークスペースのビルドが完了しました: %s/install\n' "${WS}"
