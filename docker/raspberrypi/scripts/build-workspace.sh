#!/usr/bin/env bash
# `docker compose up` のたびに走るワークスペースビルド。
#
# イメージが持つのは apt 依存とツールチェーン (ROS, Livox SDK, Rust, ros2_rust)
# だけで、ソースは compose が ../../src を ${ROS_WS}/src へマウントして供給する。
# colcon は前回の build/ を名前付きボリュームに残しているので、変わった
# パッケージだけが建て直される。
#
# **rosdep はここでは回さない。** apt の依存解決はイメージ側の責務で、
# 「apt パッケージを変えたときだけイメージを焼き直す」という切り分けを保つため。
# 起動のたびに rosdep を回すとネットワークが要るうえ、apt の状態が毎回変わる。
# 新しい apt 依存を足したときは `docker compose build` をやり直すこと。
#
# ROS と ros2_rust のオーバーレイは /ros_entrypoint.sh が読み込み済み。
set -eo pipefail

WS="${ROS_WS:-/opt/ros_ws}"
# Pi4 の 4 コアを使い切る。メモリは 4GB しかないが、実測では release の rustc
# 2 本で RSS 750MB・available 2.5GB と余っていた (低メモリ時は BUILD_JOBS で
# 絞る)。cargo 側の並列数は既定 (nproc) のままにしてある。
BUILD_JOBS="${BUILD_JOBS:-4}"

cd "${WS}"
mkdir -p src

# クレートのレジストリはビルド用ボリュームに置く。イメージ側の /opt/cargo には
# ツールチェーンしか入っていない (レジストリを焼き込むとイメージが太るだけで、
# ワークスペースが要るクレートとは限らない)。ここに置けば up をまたいで残る。
export CARGO_HOME="${WS}/build/.cargo"
mkdir -p "${CARGO_HOME}"

# 外部パッケージの取得。ホスト側で vcs import 済みならそれをそのまま使う。
#
# **揃っているときは vcs import を呼ばない。** `--skip-existing` はチェックアウトを
# 動かさないだけで、URL が一致する既存リポジトリには git fetch まで走る。つまり
# 揃っていても Wi-Fi が無いと `Could not resolve host` で落ち、`set -e` で
# `docker compose up` ごと止まる (2026-08-05 に踏んだ)。--skip-existing がある以上
# fetch しても作業ツリーは変わらないので、呼ばないことと結果は同じ。
#
# 足りないものがあるときだけ import する。そこで失敗したら、名前を並べて落とす
# (黙って進めると colcon が「そんなパッケージは無い」という無関係な顔で落ちる)。
if [[ -f "${WS}/autonomous_bot.repos" ]]; then
  # repos ファイルのキーが取り込み先のパス (WS からの相対)。
  mapfile -t repo_paths < <(python3 - "${WS}/autonomous_bot.repos" <<'PY'
import sys
import yaml

# repos ファイルには日本語のコメントが入っている。ロケール依存の既定エンコーディング
# を踏まないよう、バイト列で渡して PyYAML に UTF-8 と判定させる。
with open(sys.argv[1], 'rb') as f:
    print('\n'.join(yaml.safe_load(f)['repositories'] or {}))
PY
  )
  # mapfile は右辺が失敗しても成功するので、空なら自分で落とす (パースに失敗した
  # まま「揃っている」と見えると、上と同じ無関係な失敗に化ける)。
  if [[ ${#repo_paths[@]} -eq 0 ]]; then
    echo "autonomous_bot.repos を読めませんでした" >&2
    exit 1
  fi

  # 空ディレクトリは「無い」とみなす (マウントし損ねた跡が残っていても取りに行く)。
  missing=()
  for repo_path in "${repo_paths[@]}"; do
    [[ -n "$(ls -A "${WS}/${repo_path}" 2>/dev/null)" ]] || missing+=("${repo_path}")
  done

  if [[ ${#missing[@]} -eq 0 ]]; then
    echo "外部パッケージは揃っているので vcs import を省略します (ネットワーク不要)"
  else
    vcs import . --skip-existing < "${WS}/autonomous_bot.repos" || true
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
# タイムスタンプを保存しないでコピーする (Windows/Podman のバインドマウントは
# 一部の Unix タイムスタンプを設定できない)。
if [[ -d src/livox_ros_driver2 ]]; then
  cp -f src/livox_ros_driver2/package_ROS2.xml src/livox_ros_driver2/package.xml
  mkdir -p src/livox_ros_driver2/launch
  cp -f src/livox_ros_driver2/launch_ROS2/* src/livox_ros_driver2/launch/
fi

MULTIARCH="$(dpkg-architecture -qDEB_HOST_MULTIARCH)"

# CMake パッケージは ros2_rust のオーバーレイ抜きで建てる。
# --symlink-install により、launch/config/params といった非コンパイル資産は
# install/ から src/ への symlink になる。つまり YAML や launch を直しただけなら
# ビルドすら要らず、ノードを起動し直すだけで反映される。
colcon build --merge-install --symlink-install \
    --parallel-workers "${BUILD_JOBS}" \
    --packages-select daifuku_bringup daifuku_config_manager daifuku_stack \n                     emcl2 livox_ros_driver2 \
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
colcon build --merge-install --symlink-install \
    --parallel-workers "${BUILD_JOBS}" \
    --packages-select vi_global_planner vi_planner \
    --cargo-args --release

# ament_cargo は package.xml の <install>launch</install> を実行しないので、
# launch ファイルは自前で install/ へ置く。
for pkg in vi_global_planner vi_planner; do
  src_dir="src/value_iteration3/vi_ros2/${pkg}/launch"
  [[ -d "${src_dir}" ]] || continue
  for launch_file in "${src_dir}"/*.py; do
    [[ -f "${launch_file}" ]] || continue
    install -D -m 0644 "${launch_file}" \
        "${WS}/install/share/${pkg}/launch/${launch_file##*/}"
  done
done

printf '\nワークスペースのビルドが完了しました: %s/install\n' "${WS}"
