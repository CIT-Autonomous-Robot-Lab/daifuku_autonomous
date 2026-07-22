# ネイティブ環境

Ubuntu 22.04へインストールしたROS 2 Humble上で直接ビルドします。RVizを同じPCで使う場合に適した構成です。

## 必要なROS 2パッケージ

- Navigation2 / `nav2_bringup`
- SLAM Toolbox
- RViz2
- `laser_filters`
- `pointcloud_to_laserscan`
- `robot_localization`
- Raspberry Pi CatとセンサーのROS 2ドライバ

価値反復プランナ（`planner:=vi`、既定）を使う場合は、さらにRust toolchain、`colcon-cargo`、`colcon-ros-cargo`、`cargo-ament-build`、ビルド済みの`ros2_rust`ワークスペースが必要です。Dockerfileでは`rclrs`をコミット`2c6b926`に固定して構築しています。詳しい再現例は`docker/Dockerfile`を参照してください。

## 外部リポジトリを取得する

リポジトリルートで実行します。

```bash
vcs import . < autonomous_bot.repos
```

これにより`src/`へ次を取得します。

- `livox_ros_driver2` 1.2.6
- `emcl2_ros2`
- `value_iteration3`

## Mid-360を使う場合

Livox SDK2とROS 2ドライバをセットアップします。スクリプトはUbuntu 22.04 / ROS 2 Humbleを前提に、必要パッケージの導入、SDKのビルド、ドライバの準備とビルドを行います。

```bash
bash scripts/setup_livox_native.sh
```

低メモリ環境では並列数を1にします。

```bash
bash scripts/setup_livox_native.sh --jobs 1
```

主なオプションは`--workspace`、`--jobs`、`--sdk-ref`、`--driver-ref`、`--skip-apt`です。

2D LiDARだけを使う場合、この手順は不要です。利用する2D LiDARドライバを別途導入してください。

## 依存関係を導入する

```bash
source /opt/ros/humble/setup.bash
rosdep update
rosdep install --from-paths src --ignore-src -r -y --rosdistro=humble
```

`rclrs`系の依存をrosdepが解決できなくても、`-r`により残りの依存導入は継続します。

## ビルドする

価値反復プランナとMid-360を含む構成:

```bash
source /opt/ros/humble/setup.bash
source /path/to/ros2_rust_ws/install/local_setup.bash
colcon build --packages-select \
  autonomous_nav emcl2 livox_ros_driver2 \
  vi_global_planner vi_local_planner \
  --symlink-install \
  --cmake-args -DROS_EDITION=ROS2 -DDISTRO_ROS=humble
source install/setup.bash
```

NavFnだけを使う構成ではRust環境と価値反復パッケージは不要です。2D LiDARだけならLivoxドライバも省略できます。

```bash
source /opt/ros/humble/setup.bash
colcon build --packages-select autonomous_nav emcl2 --symlink-install
source install/setup.bash
```

新しいターミナルを開くたびに環境を読み込みます。

```bash
source /opt/ros/humble/setup.bash
source /path/to/daifuku_autonomous/install/setup.bash
```

続いて[ネットワーク](network.md)と[LiDAR](lidar.md)を設定してください。
