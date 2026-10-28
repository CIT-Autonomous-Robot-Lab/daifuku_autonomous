#!/usr/bin/env bash
# `docker compose up` のたびに走るワークスペースビルド。ソースは compose が
# マウントし、前回の build/ は名前付きボリュームに残るので差分だけが建て直される。
# ROS と ros2_rust のオーバーレイは /ros_entrypoint.sh が読み込み済み。
#
# **パッケージは名前で選ばない。** src/ にあるものは全部建て、建てられないものだけを
# 下の SKIP_PACKAGES で外す。パッケージを足してもこのファイルは直さなくてよい
# (Pi で建たないものを足したときだけ SKIP_PACKAGES に足す)。
#
# **rosdep はここでは回さない** (apt の解決はイメージ側の責務。回すとネットワークが
# 要るうえ apt の状態が毎回変わる)。**新しい apt 依存を足したら
# `docker compose build` からやり直すこと。**
set -eo pipefail

WS="${ROS_WS:-/opt/ros_ws}"
# 既定 4 = Pi 4 の全コア (release の rustc 2 本で available 2.5GB 残る実測)。
# cargo 側の並列数は既定 (nproc) のまま。
BUILD_JOBS="${BUILD_JOBS:-4}"
REPOS_FILE="${WS}/daifuku_autonomous.repos"

# **Pi では建てないもの。** 実機イメージ (ros:humble-ros-base) に rqt と RViz が
# 無いので、この 2 つを建てるとビルドが落ちる。
SKIP_PACKAGES=(daifuku_rqt daifuku_waypoint_manager)

cd "${WS}"
mkdir -p src

# クレートのレジストリはビルド用ボリュームに置く (up をまたいで残る。イメージ側の
# /opt/cargo にはツールチェーンしか入っていない)。
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
  cp -f src/livox_ros_driver2/package_ROS2.xml src/livox_ros_driver2/package.xml
  mkdir -p src/livox_ros_driver2/launch
  cp -f src/livox_ros_driver2/launch_ROS2/* src/livox_ros_driver2/launch/
fi

# cargo のパッケージ (vi_planner) は rclrs を使うので、CMake / Python のパッケージを
# 建てて install/ を載せてから 2 段目で建てる。型で見分けるので名前は書かない。
# **colcon list の失敗は変数代入で拾う** (プロセス置換だと set -e が効かず、空の
# 一覧のまま cargo パッケージを 1 段目に混ぜてしまう)。
pkg_list="$(colcon list --base-paths src)"
mapfile -t cargo_pkgs < <(awk '$3 ~ /cargo/ {print $1}' <<<"${pkg_list}")

MULTIARCH="$(dpkg-architecture -qDEB_HOST_MULTIARCH)"

# --symlink-install なので launch や yaml は install/ から src/ への symlink になり、
# 直しただけならビルドは要らない。
colcon build --base-paths src --merge-install --symlink-install \
    --parallel-workers "${BUILD_JOBS}" \
    --packages-skip "${SKIP_PACKAGES[@]}" "${cargo_pkgs[@]}" \
    --cmake-args -DROS_EDITION=ROS2 \
                 -DDISTRO_ROS="${ROS_DISTRO}" \
                 "-DCMAKE_LIBRARY_PATH=/opt/ros/${ROS_DISTRO}/lib;/usr/lib/${MULTIARCH};/lib/${MULTIARCH}" \
                 "-DCMAKE_INCLUDE_PATH=/usr/include;/usr/include/eigen3;/usr/include/pcl-1.12" \
                 "-DCMAKE_CXX_FLAGS=-O2 -I/usr/include/eigen3" \
                 -DEigen3_DIR=/usr/share/eigen3/cmake \
                 -DOPENSSL_ROOT_DIR=/usr \
                 -DOPENSSL_INCLUDE_DIR=/usr/include \
                 -DOPENSSL_CRYPTO_LIBRARY="/usr/lib/${MULTIARCH}/libcrypto.so" \
                 -DOPENSSL_SSL_LIBRARY="/usr/lib/${MULTIARCH}/libssl.so"

# **vi_planner が見えなければ落ちること。** 見えないまま進むと建てるものが 0 個で
# exit 0 になり、機体が前のバイナリのまま上がって `docker compose up` を何度通しても
# 直らない。
printf '%s\n' "${cargo_pkgs[@]}" | grep -qx vi_planner || {
  printf 'vi_planner が colcon から見えません (vi_rs/Cargo.toml の\n' >&2
  printf '[workspace.metadata.colcon] additional-packages を確認)\n' >&2
  exit 1
}

source "${WS}/install/local_setup.bash"
colcon build --base-paths src --merge-install --symlink-install \
    --parallel-workers "${BUILD_JOBS}" \
    --packages-select "${cargo_pkgs[@]}" \
    --cargo-args --release

printf '\nワークスペースのビルドが完了しました: %s/install\n' "${WS}"
