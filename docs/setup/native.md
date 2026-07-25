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

価値反復プランナ（`planner:=vi`、既定）を使う場合は、さらにRust toolchain、`colcon-cargo`、`colcon-ros-cargo`、`cargo-ament-build`、ビルド済みの`ros2_rust`ワークスペースが必要です。`rclrs`はコミット`2c6b926`に固定して構築します。

## セットアップスクリプト

`scripts/`にコンポーネントごとのセットアップスクリプトを用意しています。すべてUbuntu 22.04 / ROS 2 Humbleを前提とし、リポジトリルートから実行します。

| スクリプト | 対象 |
|---|---|
| `setup_native.sh` | 下記すべてを順に実行する一括セットアップ |
| `setup_native_base.sh` | 共通のaptパッケージ、`vcs import`、`rosdep`、`autonomous_nav`と`emcl2`のビルド |
| `setup_native_livox.sh` | Livox SDK2と`livox_ros_driver2`（Mid-360） |
| `setup_native_ros2_rust.sh` | Rust toolchainと`ros2_rust`（`rclrs`）ワークスペース |
| `setup_native_vi.sh` | `vi_global_planner`と`vi_local_planner` |

### 一括で導入する

```bash
bash scripts/setup_native.sh
```

低メモリ環境では並列数を1にします。

```bash
bash scripts/setup_native.sh --jobs 1
```

構成に応じて省略できます。2D LiDARだけならLivoxを、NavFnだけを使うなら価値反復プランナを外します。

```bash
bash scripts/setup_native.sh --no-livox --no-vi
```

主なオプションは`--workspace`、`--ros2-rust-ws`、`--jobs`、`--skip-apt`、`--no-livox`、`--no-vi`です。

### 個別に導入する

`setup_native.sh`と同じ順序で実行します。`--jobs`と`--skip-apt`は各スクリプトに共通です。

```bash
bash scripts/setup_native_base.sh
bash scripts/setup_native_livox.sh
bash scripts/setup_native_ros2_rust.sh
bash scripts/setup_native_vi.sh
```

`setup_native_base.sh`は`vcs import . < autonomous_bot.repos`により`src/`へ次を取得します。

- `livox_ros_driver2` 1.2.6
- `emcl2_ros2`
- `value_iteration3`

`rclrs`系の依存をrosdepが解決できなくても、`-r`により残りの依存導入は継続します。

`setup_native_ros2_rust.sh`は既定で`~/ros2_rust_ws`へ`ros2_rust`を構築し、Rust toolchainを`~/.cargo`、colcon拡張を`~/.local`へ導入します。場所を変える場合は`--ros2-rust-ws`を使い、`setup_native_vi.sh`にも同じ値を渡します。Rust toolchainを常用するには`~/.bashrc`へ次を追記します。

```bash
export PATH="${HOME}/.local/bin:${HOME}/.cargo/bin:${PATH}"
```

`setup_native_livox.sh`は`--sdk-ref`と`--driver-ref`でバージョンを、`setup_native_vi.sh`は`--debug`でcargoのプロファイルを変更できます。

2D LiDARだけを使う場合、Livoxの手順は不要です。利用する2D LiDARドライバを別途導入してください。

## 環境を読み込む

新しいターミナルを開くたびに実行します。

```bash
source /opt/ros/humble/setup.bash
source ~/ros2_rust_ws/install/local_setup.bash
source /path/to/daifuku_autonomous/install/setup.bash
```

NavFnだけを使う構成では`ros2_rust`の読み込みは不要です。

続いて[ネットワーク](network.md)と[LiDAR](lidar.md)を設定してください。
