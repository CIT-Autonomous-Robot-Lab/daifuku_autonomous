#!/usr/bin/env bash
# `docker compose up` のたびに走るワークスペースビルド。ソースは compose が
# マウントし、前回の build/ は名前付きボリュームに残るので差分だけが建て直される。
# ROS と ros2_rust のオーバーレイは /ros_entrypoint.sh が読み込み済み。
#
# **rosdep はここでは回さない** (apt の解決はイメージ側の責務。回すとネットワークが
# 要るうえ apt の状態が毎回変わる)。**新しい apt 依存を足したら
# `docker compose build` からやり直すこと。**
set -eo pipefail

WS="${ROS_WS:-/opt/ros_ws}"
# 既定 4 = Pi 4 の全コア (release の rustc 2 本で available 2.5GB 残る実測)。
# cargo 側の並列数は既定 (nproc) のまま。
BUILD_JOBS="${BUILD_JOBS:-4}"

cd "${WS}"
mkdir -p src

# クレートのレジストリはビルド用ボリュームに置く (up をまたいで残る。イメージ側の
# /opt/cargo にはツールチェーンしか入っていない)。
export CARGO_HOME="${WS}/build/.cargo"
mkdir -p "${CARGO_HOME}"

# 外部パッケージの取得。**揃っているときは vcs import を呼ばない** —
# `--skip-existing` でも既存リポジトリには git fetch まで走るので、ネットワークが
# 無いと `Could not resolve host` で落ちて `set -e` で up ごと止まる。作業ツリーは
# どのみち変わらないので、呼ばないのと結果は同じ。
#
# 足りないときだけ import し、失敗したら名前を並べて落とす (黙って進めると colcon が
# 「そんなパッケージは無い」という無関係な顔で落ちる)。
if [[ -f "${WS}/daifuku_autonomous.repos" ]]; then
  # repos ファイルのキーが取り込み先のパス (WS からの相対)。
  mapfile -t repo_paths < <(python3 - "${WS}/daifuku_autonomous.repos" <<'PY'
import sys
import yaml

# repos ファイルには日本語のコメントが入っている。ロケール依存の既定エンコーディング
# を踏まないよう、バイト列で渡して PyYAML に UTF-8 と判定させる。
with open(sys.argv[1], 'rb') as f:
    print('\n'.join(yaml.safe_load(f)['repositories'] or {}))
PY
  )
  # mapfile は右辺が失敗しても成功するので、空なら自分で落とす (パースに失敗した
  # まま「揃っている」と見えると無関係な失敗に化ける)。
  if [[ ${#repo_paths[@]} -eq 0 ]]; then
    echo "daifuku_autonomous.repos を読めませんでした" >&2
    exit 1
  fi

  # 空ディレクトリは「無い」とみなす。
  missing=()
  for repo_path in "${repo_paths[@]}"; do
    [[ -n "$(ls -A "${WS}/${repo_path}" 2>/dev/null)" ]] || missing+=("${repo_path}")
  done

  if [[ ${#missing[@]} -eq 0 ]]; then
    echo "外部パッケージは揃っているので vcs import を省略します (ネットワーク不要)"
  else
    vcs import . --skip-existing < "${WS}/daifuku_autonomous.repos" || true
    still_missing=()
    for repo_path in "${missing[@]}"; do
      [[ -n "$(ls -A "${WS}/${repo_path}" 2>/dev/null)" ]] || still_missing+=("${repo_path}")
    done
    if [[ ${#still_missing[@]} -ne 0 ]]; then
      printf '取得できませんでした (ネットワークが要ります): %s\n' "${still_missing[*]}" >&2
      exit 1
    fi
  fi
fi

# livox_ros_driver2 は ROS 2 用の manifest と launch を上流で別名に置いている。
# タイムスタンプは保存しない (Windows/Podman のバインドマウントが設定できない)。
if [[ -d src/livox_ros_driver2 ]]; then
  cp -f src/livox_ros_driver2/package_ROS2.xml src/livox_ros_driver2/package.xml
  mkdir -p src/livox_ros_driver2/launch
  cp -f src/livox_ros_driver2/launch_ROS2/* src/livox_ros_driver2/launch/
fi

MULTIARCH="$(dpkg-architecture -qDEB_HOST_MULTIARCH)"

# CMake パッケージは ros2_rust のオーバーレイ抜きで建てる。--symlink-install なので
# launch や yaml は install/ から src/ への symlink になり、直しただけならビルドは要らない。
colcon build --merge-install --symlink-install \
    --parallel-workers "${BUILD_JOBS}" \
    --packages-select daifuku_bringup daifuku_config daifuku_config_manager daifuku_stack \
                     emcl2 livox_ros_driver2 \
                     raspicat_bringup raspicat_description raspicat_driver \
                     raspimouse raspimouse_msgs \
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

# 価値反復プランナは rclrs を使うので、上で建てた install/ を載せてから建てる。
source "${WS}/install/local_setup.bash"
# **見えなければ落ちること。** vi_planner が見つからないと --packages-select は
# 警告 1 行で 0 個建てて exit 0 するので、機体が前のバイナリのまま上がり、
# `docker compose up` を何度通しても直らない。
colcon list --packages-select vi_planner --names-only 2>/dev/null \
    | grep -qx vi_planner || {
  printf 'vi_planner が colcon から見えません (vi_rs/Cargo.toml の\n' >&2
  printf '[workspace.metadata.colcon] additional-packages を確認)\n' >&2
  exit 1
}
colcon build --merge-install --symlink-install \
    --parallel-workers "${BUILD_JOBS}" \
    --packages-select vi_planner \
    --cargo-args --release

# ament_cargo は <install>launch</install> を実行しないので、launch は自前で置く。
# **上流の移動でパスが変わったら、黙って何も入らないのではなく落ちること** —
# 入らないと local_planner:=nav2 が原因から遠いところで起動時に落ちる。
src_dir="src/value_iteration3/vi_rs/vi_planner/launch"
for launch_file in "${src_dir}"/*.py; do
  install -D -m 0644 "${launch_file}" \
      "${WS}/install/share/vi_planner/launch/${launch_file##*/}"
done

printf '\nワークスペースのビルドが完了しました: %s/install\n' "${WS}"
