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
# Pi4 は 4 コアだがメモリが 4GB しかない。cargo の release ビルドと並ぶと
# 効くので既定は控えめにしておく (compose の BUILD_JOBS で上書きできる)。
BUILD_JOBS="${BUILD_JOBS:-2}"

cd "${WS}"
mkdir -p src

# クレートのレジストリはビルド用ボリュームに置く。イメージ側の /opt/cargo には
# ツールチェーンしか入っていない (レジストリを焼き込むとイメージが太るだけで、
# ワークスペースが要るクレートとは限らない)。ここに置けば up をまたいで残る。
export CARGO_HOME="${WS}/build/.cargo"
mkdir -p "${CARGO_HOME}"

# 外部パッケージの取得。既にあるものは触らないので、揃っていればネットワークは
# 要らない。ホスト側で vcs import 済みならそれをそのまま使う。
if [[ -f "${WS}/autonomous_bot.repos" ]]; then
  vcs import . --skip-existing < "${WS}/autonomous_bot.repos"
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
    --packages-select autonomous_nav emcl2 livox_ros_driver2 \
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
