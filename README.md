# daifuku_autonomous

[株式会社アールティのRaspberry Pi Cat](https://rt-net.jp/products/raspicat)を、
ROS 2 Navigation2で自律移動させるためのワークスペースです。

ROS 2、Nav2、SLAM Toolbox、RViz、EMCL2は、DockerコンテナまたはネイティブのROS 2
環境で実行できます。Dockerを使う場合、ホストへのROS 2のインストールは不要です。

グローバルプランナには、価値反復 (Value Iteration) ベースの
[`vi_global_planner`](https://github.com/NOPLAB/value_iteration3)（デフォルト、
`planner:=vi`）と、Nav2標準のNavFn（`planner:=navfn`）を選択できます。
`vi_global_planner`はNav2の`planner_server`の代わりに`compute_path_to_pose`アクションを
提供するRust製ノードで、ゴールごとに価値関数を`frontier2d_sparse`ソルバで計算し、
最適方策のロールアウトで経路を生成します。同一ゴールへのリプランは価値関数
キャッシュにより高速です。

狭域プランナ（経路追従）はデフォルトでグローバルプランナに連動します:
`planner:=vi`では同リポジトリの`vi_local_planner`（`controller_server`の代わりに
`follow_path`アクションを提供し、レーザスキャンでローカルに価値関数を補正しながら
`cmd_vel`を出力するRust製ノード）が使われます。`local_planner:=nav2`で
Nav2標準のDWB（controller_server）に切り替えられます。

> **重要:** Raspberry Pi Catのモータードライバと車輪オドメトリは含まれていません。
> Mid-360ドライバとLiDAR前処理は含まれます。2D LiDARを使う場合は、機体側の
> ドライバから生スキャンを`/scan_raw`へ配信してください。

## システム構成

DockerコンテナまたはネイティブPCと、Raspberry Pi Catは同じROS 2ネットワークに
参加します。

```text
Raspberry Pi Cat                         Docker / ネイティブPC
────────────────────                    ──────────────────────
モータードライバ  ←── /cmd_vel ─────── Nav2 / 遠隔操作
オドメトリ        ─── /odom ─────────→ Nav2 / SLAM / EMCL2
2D LiDAR          ─── /scan_raw ─┐
Mid-360 ─ /livox/lidar ─ 3D→2D ─┴→ 角度フィルタ → /scan
TF                ─── odom → base_footprint → センサーフレーム
                                          │
                                          └─ RViz
```

機体側で必要なROS 2インターフェース:

- `/cmd_vel`（`geometry_msgs/msg/Twist`）を受信する
- `/odom`（`nav_msgs/msg/Odometry`）を配信する
- 2D LiDAR使用時は`/scan_raw`（`sensor_msgs/msg/LaserScan`）を配信する
- Mid-360 + IMU使用時は車輪オドメトリを`/wheel/odom`へ配信する
- `odom`から`base_footprint`までのTFを配信する
- LiDARなどのセンサーフレームから`base_footprint`までのTFを配信する

Raspberry Pi CatはRaspberry Pi Mouseと同じデバイスドライバを利用できる機体ですが、
使用するROS 2ドライバの導入・起動方法は、実機側の構成に合わせて別途用意してください。

## 動作環境

- Docker EngineまたはDocker Desktop
- Docker Compose v2（`docker compose`コマンド）
- Raspberry Pi CatとDockerホストが通信できるネットワーク
- RVizをX11転送する場合はVcXsrvなどのWindows用Xサーバー

コンテナ内ではROS 2 Humble（Ubuntu 22.04）を使用します。主な導入済みソフトウェアは
次のとおりです。

- Navigation2
- SLAM Toolbox
- RViz2
- EMCL2
- `autonomous_nav`パッケージ
- `vi_global_planner`（value_iteration3の価値反復グローバルプランナ、Rust/rclrs製。
  ビルドに必要なros2_rustワークスペースはイメージ内`/opt/ros2_rust_ws`に構築済み）
- Livox SDK2 / `livox_ros_driver2`
- `pointcloud_to_laserscan`、`laser_filters`、`robot_localization`

## ネイティブ環境でのセットアップ

Dockerを使わず、Ubuntu 22.04へインストールしたROS 2 Humble上でも実行できます。

### 必要なもの

- ROS 2 Humble
- `nav2_bringup`
- `slam_toolbox`
- `rviz2`
- `laser_filters`
- `pointcloud_to_laserscan`
- `robot_localization`
- Raspberry Pi CatのROS 2対応ドライバとセンサードライバ

`planner:=vi`（デフォルト）で`vi_global_planner`をビルドする場合は追加で:

- Rust toolchain（rustup）
- `pip install colcon-cargo colcon-ros-cargo`と`cargo install cargo-ament-build`
- ビルド済みros2_rustワークスペース（rclrs @ 2c6b926）と`nav2_msgs`のRust
  バインディング。手順は`docker/Dockerfile`の`/opt/ros2_rust_ws`構築部分、
  または`src/value_iteration3/vi_ros2/docker/Dockerfile`を参照

ワークスペースのルートでEMCL2、value_iteration3、`livox_ros_driver2`を取得します。

```bash
vcs import . < autonomous_bot.repos
```

Mid-360を使う場合はLivox SDK2を先にインストールし、公式ドライバをROS 2用に準備します。
Dockerイメージではこの処理は自動です。

```bash
git clone --depth 1 --branch v1.3.1 \
  https://github.com/Livox-SDK/Livox-SDK2.git /tmp/Livox-SDK2
cmake -S /tmp/Livox-SDK2 -B /tmp/Livox-SDK2/build -DCMAKE_BUILD_TYPE=Release
cmake --build /tmp/Livox-SDK2/build --parallel
sudo cmake --install /tmp/Livox-SDK2/build
sudo ldconfig

cp src/livox_ros_driver2/package_ROS2.xml src/livox_ros_driver2/package.xml
cp -a src/livox_ros_driver2/launch_ROS2 src/livox_ros_driver2/launch
```

依存パッケージをインストールします（rclrs系のキーは解決できないため`-r`で
続行します）。

```bash
rosdep update
rosdep install --from-paths src --ignore-src -r -y
```

### ビルド

```bash
source /opt/ros/humble/setup.bash
source /path/to/ros2_rust_ws/install/local_setup.bash  # planner:=vi を使う場合
colcon build --packages-select autonomous_nav emcl2 vi_global_planner vi_local_planner \
  livox_ros_driver2 --symlink-install \
  --cmake-args -DROS_EDITION=ROS2 -DDISTRO_ROS=humble
source install/setup.bash
```

（`planner:=vi`（デフォルト）では狭域プランナもデフォルトで`vi_local_planner`に
なるため、両方ビルドしておきます。DWBを使う場合（`local_planner:=nav2`）は
`vi_local_planner`を省けます。）

`vi_global_planner`を使わない場合（`planner:=navfn`のみ）はros2_rust環境が不要です。

```bash
colcon build --packages-select autonomous_nav emcl2 --symlink-install
```

新しいターミナルを開くたびに、ROS 2とワークスペースの環境設定を読み込んでください。

```bash
source /opt/ros/humble/setup.bash
source install/setup.bash
```

## LiDARの切り替えと前処理

`mapping.launch.py`と`navigation.launch.py`は`lidar:=2d|mid360`で入力を切り替えます。
どちらも生スキャンを`/scan_raw`へ集約し、`config/scan_filter.yaml`を通した結果だけを
`/scan`へ配信します。既定ではコネクタがある後方60度（+150度から-150度まで、
±180度をまたぐ範囲）を無効化します。

恒久的に制限角度を変える場合は`config/scan_filter.yaml`の`angle_min`と`angle_max`を
ラジアンで編集します。別ファイルを使う場合、または一時的に無効化する場合は次のように
指定します。

```bash
ros2 launch autonomous_nav mapping.launch.py \
  lidar:=2d \
  scan_filter_params_file:=/path/to/custom_scan_filter.yaml

ros2 launch autonomous_nav mapping.launch.py \
  lidar:=2d scan_filter_enabled:=false
```

### 2D LiDARを使う

2D LiDARドライバの出力を`/scan`ではなく`/scan_raw`へremapしてから、地図作成または
ナビゲーションを起動します。ドライバ固有のlaunch引数がない場合のROS remap例です。

```bash
ros2 run <2d_lidar_package> <2d_lidar_node> \
  --ros-args -r scan:=/scan_raw
```

`lidar:=2d`では従来どおり、車輪オドメトリノードが`/odom`と
`odom -> base_footprint` TFを配信します。

### Mid-360を使う

最初に`config/MID360_config.json`の次のアドレスを実ネットワークに合わせます。

- `host_net_info`内の4個の`*_data_ip`: ドライバを動かすPCの固定IP
- `lidar_configs[0].ip`: Mid-360本体のIP

`base_footprint -> livox_frame` TFはロボットのURDFから配信するのが推奨です。URDFに
まだ追加していない場合は、実測した搭載位置・姿勢をlaunch引数で一時配信できます。
値はメートル、姿勢はラジアンです。

```bash
publish_lidar_tf:=true lidar_x:=0.0 lidar_y:=0.0 lidar_z:=0.30 \
lidar_roll:=0.0 lidar_pitch:=0.0 lidar_yaw:=0.0
```

Mid-360ではIMU融合が既定で有効です。Raspberry Pi Catの車輪オドメトリを
`/wheel/odom`へremapし、車輪ノード自身の`odom -> base_footprint` TF配信を無効にして
ください。EKFがMid-360のZ軸角速度と車輪速度を融合し、最終的な`/odom`と
`odom -> base_footprint`を配信します。同じTFを車輪ノードとEKFの両方から配信しては
いけません。

車輪ドライバにTF無効化機能がない場合は、そのノードの`/tf`出力を未使用トピックへ
remapします。具体的なパラメータ名は使用する車輪ドライバに合わせてください。

```bash
# 概念例。実際のpackage/node名とTF無効化引数へ置き換える
ros2 run <wheel_package> <wheel_odom_node> --ros-args \
  -r /odom:=/wheel/odom -r /tf:=/wheel/tf_unused
```

IMU/EKFだけを切り分ける場合は`use_mid360_imu:=false`を指定します。この場合は2D
LiDAR時と同様に、車輪ノードから通常の`/odom`とTFを配信してください。

### 地図作成

Raspberry Pi Cat側でセンサー、オドメトリ、TFを起動してから、SLAMを起動します。

```bash
ros2 launch autonomous_nav mapping.launch.py lidar:=2d use_sim_time:=false
```

Mid-360の場合（下記の搭載値は例なので実測値へ変更）:

```bash
ros2 launch autonomous_nav mapping.launch.py \
  lidar:=mid360 use_sim_time:=false \
  publish_lidar_tf:=true lidar_z:=0.30
```

機体側の操作ノードやゲームパッドで走行した後、地図を保存します。

```bash
ros2 run nav2_map_server map_saver_cli -f src/autonomous_nav/maps/map
```

### 自律移動

EMCL2を使う場合（グローバルプランナはデフォルトで`vi_global_planner`）:

```bash
ros2 launch autonomous_nav navigation.launch.py \
  map:=$PWD/src/autonomous_nav/maps/map.yaml \
  use_sim_time:=false localization:=emcl2 lidar:=2d
```

Mid-360を使う場合（IMU融合は既定で有効）:

```bash
ros2 launch autonomous_nav navigation.launch.py \
  map:=$PWD/src/autonomous_nav/maps/map.yaml \
  use_sim_time:=false localization:=emcl2 lidar:=mid360 \
  publish_lidar_tf:=true lidar_z:=0.30
```

Nav2標準のAMCLを使う場合:

```bash
ros2 launch autonomous_nav navigation.launch.py \
  map:=$PWD/src/autonomous_nav/maps/map.yaml \
  use_sim_time:=false localization:=amcl
```

### 起動確認

別ターミナルで、共通出力とセンサー別の入力を確認できます。

```bash
# 両方式共通。/scan_rawが入力、角度制限後の/scanがNav2/SLAM入力
ros2 topic hz /scan_raw
ros2 topic hz /scan

# Mid-360 + IMUの場合
ros2 topic hz /livox/lidar
ros2 topic hz /livox/imu
ros2 topic hz /imu/mid360
ros2 topic hz /wheel/odom
ros2 topic hz /odom
ros2 run tf2_ros tf2_echo odom base_footprint
```

Mid-360起動時に`bind failed`となる場合は、`MID360_config.json`のホストIPが実際に
ROS 2を動かすPCへ設定されていること、そのIPが対象NICに割り当てられていることを確認します。

NavFnプランナへ切り替える場合は`planner:=navfn`を追加します:

```bash
ros2 launch autonomous_nav navigation.launch.py \
  map:=$PWD/src/autonomous_nav/maps/map.yaml \
  use_sim_time:=false localization:=emcl2 planner:=navfn
```

狭域プランナ（経路追従）はデフォルトでグローバルプランナに連動します:
`planner:=vi`（デフォルト）ではcontroller_serverの代わりに`vi_local_planner`が
`follow_path`を提供し、レーザスキャンでローカルに価値関数を補正しながら
`cmd_vel`を出します。狭域だけDWB（controller_server）に戻す場合は
`local_planner:=nav2`を追加します:

```bash
ros2 launch autonomous_nav navigation.launch.py \
  map:=$PWD/src/autonomous_nav/maps/map.yaml \
  use_sim_time:=false localization:=emcl2 local_planner:=nav2
```

RVizの「2D Pose Estimate」で初期姿勢を設定し、「Nav2 Goal」で移動先を指定します。

`planner:=vi`では、新しいゴールを受けた最初の経路計算で価値反復が地図全体を
解くため、地図サイズに応じて数秒〜数十秒かかることがあります（`vi_global_planner`の
ログに計算時間が出力されます）。同じゴールへのリプランはキャッシュされた
価値関数を使うため高速です。

価値反復の計算過程はRVizで見られます（`rviz/nav2_default.rviz`に表示を追加済み）。
いずれもOccupancyGridで、`costmap`カラースキームのMap表示で描画されます。

- `/value_function` — グローバル（`vi_global_planner`）の価値関数（θ=0スライス）。
  solve中も`value_publish_interval_ms`（既定500ms）ごとに途中経過が配信される
  ため、ゴールから波面が広がる様子が見える
- `/local_value_function` — ローカル（`vi_local_planner`）が自前で解く価値関数の
  solve経過と完成形（既定のRViz設定では非表示。グローバルとほぼ同じ絵になるため）
- `/local_window_value` — 追従中にロボット周辺±1mのローカルウィンドウを現在方位の
  θスライスで配信。スキャン由来のペナルティ注入と局所反復の結果がリアルタイムに
  見える（`local_planner:=vi`のとき）

## Docker環境の起動

リポジトリのルートで実行します。

```powershell
docker compose -f docker/compose.yaml build
docker compose -f docker/compose.yaml up -d
docker compose -f docker/compose.yaml ps
```

`src/autonomous_nav`はコンテナの`/opt/ros_ws/src/autonomous_nav`へバインドマウント
されます。ホスト側で変更した設定、launchファイル、地図はコンテナから参照でき、
コンテナ内で保存した地図もホスト側に残ります。

EMCL2はイメージのビルド時に`autonomous_bot.repos`から取得されます。

## ROS 2コマンドの実行

`/ros_entrypoint.sh`はROS 2とビルド済みワークスペースを読み込んでから、指定された
コマンドを起動します。

```powershell
docker compose -f docker/compose.yaml exec ros2 `
  /ros_entrypoint.sh ros2 pkg list
```

対話シェルを開く場合:

```powershell
docker compose -f docker/compose.yaml exec ros2 /ros_entrypoint.sh bash
```

以降の操作では、複数のPowerShellターミナルから同じコンテナへコマンドを実行します。

## ROS 2ネットワークの設定

Composeでは、実機とのDDS通信のためにホストネットワーク（`network_mode: host`）を
使用します。

Docker Desktopを使用する場合は、ホストネットワークに対応するDocker Desktop 4.34以降を
使用し、設定画面のResources > Networkで「Enable host networking」を有効にしてください。
また、ROS 2のUDP通信とマルチキャストがWindowsファイアウォールで許可されていることを
確認してください。

機体とコンテナの`ROS_DOMAIN_ID`を一致させます。既定値は`0`です。変更する場合は、
コンテナを起動する前に環境変数を設定します。

```powershell
$env:ROS_DOMAIN_ID = "10"
docker compose -f docker/compose.yaml up -d
```

通信を確認します。

```powershell
docker compose -f docker/compose.yaml exec ros2 `
  /ros_entrypoint.sh ros2 topic list

docker compose -f docker/compose.yaml exec ros2 `
  /ros_entrypoint.sh ros2 topic echo /scan --once

docker compose -f docker/compose.yaml exec ros2 `
  /ros_entrypoint.sh ros2 topic echo /odom --once
```

## RVizの表示

WindowsでRVizを表示する場合は、VcXsrvなどのXサーバーを起動してDockerからの接続を
許可してください。Composeの既定値は次のとおりです。

```text
DISPLAY=host.docker.internal:0.0
LIBGL_ALWAYS_SOFTWARE=1
```

別の表示先を使う場合:

```powershell
$env:DISPLAY = "host.docker.internal:0.0"
docker compose -f docker/compose.yaml up -d
```

## 地図の作成

### 1. 機体側ドライバを起動

Raspberry Pi Cat側でモータードライバ、オドメトリ、LiDAR、TFを起動します。2D LiDARは
`/scan_raw`へremapします。Mid-360 + IMUでは車輪オドメトリを`/wheel/odom`へremapし、
車輪側のodom TFを停止します。Docker側でフィルタ後の`/scan`、最終的な`/odom`、必要な
TFを確認してから次へ進みます。

### 2. SLAMを起動

ターミナル1でSLAM ToolboxとRVizを起動します。

```powershell
docker compose -f docker/compose.yaml exec ros2 `
  /ros_entrypoint.sh ros2 launch autonomous_nav mapping.launch.py `
  use_sim_time:=false
```

### 3. Raspberry Pi Catを操作

機体側の操作ノードやゲームパッドを使い、地図を作成する範囲を走行します。操作ノードは
`/cmd_vel`へ`geometry_msgs/msg/Twist`を配信する必要があります。

動作前に周囲の安全を確認し、緊急停止スイッチをすぐ押せる状態で操作してください。

### 4. 地図を保存

```powershell
docker compose -f docker/compose.yaml exec ros2 `
  /ros_entrypoint.sh ros2 run nav2_map_server map_saver_cli `
  -f /opt/ros_ws/src/autonomous_nav/maps/map
```

次のファイルがホスト側へ保存されます。

- `src/autonomous_nav/maps/map.yaml`
- `src/autonomous_nav/maps/map.pgm`

## 自律移動

### EMCL2を使う場合

```powershell
docker compose -f docker/compose.yaml exec ros2 `
  /ros_entrypoint.sh ros2 launch autonomous_nav navigation.launch.py `
  map:=/opt/ros_ws/src/autonomous_nav/maps/map.yaml `
  use_sim_time:=false localization:=emcl2
```

### Nav2標準のAMCLを使う場合

```powershell
docker compose -f docker/compose.yaml exec ros2 `
  /ros_entrypoint.sh ros2 launch autonomous_nav navigation.launch.py `
  map:=/opt/ros_ws/src/autonomous_nav/maps/map.yaml `
  use_sim_time:=false localization:=amcl
```

NavFnプランナへ切り替える場合は、上記コマンドに`planner:=navfn`を追加します
（デフォルトは`planner:=vi`の`vi_global_planner`）。

RVizの「2D Pose Estimate」で地図上の初期姿勢を設定し、「Nav2 Goal」で移動先を指定
します。

自律移動を開始する前に、次を確認してください。

- 地図と実環境が一致している
- LiDARの障害物がRVizに正しく表示される
- `/cmd_vel`の並進・旋回方向が機体と一致している
- オドメトリとTFに不連続や大きな遅延がない
- 緊急停止スイッチを操作できる

## 現在の構成

このワークスペースは、Nav2を中心にした地図作成・自己位置推定・経路計画・追従の構成です。

```text
daifuku_autonomous
├── docker
│   ├── Dockerfile
│   ├── compose.yaml
│   └── entrypoint.sh
├── README.md
├── autonomous_bot.repos
└── src
    ├── autonomous_nav
    │   ├── config
    │   │   ├── nav2_params.yaml
    │   │   ├── emcl2_params.yaml
    │   │   └── slam_toolbox_params.yaml
    │   ├── launch
    │   │   ├── mapping.launch.py
    │   │   └── navigation.launch.py
    │   ├── maps
    │   └── rviz
    ├── emcl2_ros2
    │   ├── config
    │   ├── launch
    │   ├── include
    │   └── src
    └── value_iteration3
        ├── vi_rs          (価値反復ソルバ本体, Rust crate)
        └── vi_ros2
            └── vi_global_planner (Nav2用グローバルプランナノード +
                            vi版navigation_launch.py)
```

Dockerイメージ内ではEMCL2とvalue_iteration3を`/opt/ros_ws/src/`へ取得します。
ネイティブ環境では`vcs import`により、このリポジトリの`src/`へ取得します。

### パッケージ

- `autonomous_nav`
  - このリポジトリ側のナビゲーション設定パッケージ
  - Nav2、SLAM Toolbox、RViz、地図ファイル、起動ファイルをまとめている
  - C++/Pythonノードは持たず、`config`、`launch`、`maps`、`rviz`をインストールする
- `emcl2`
  - `src/emcl2_ros2`に配置される外部自己位置推定パッケージ
  - パッケージ名は`emcl2`
  - `emcl2_node`を起動し、AMCLの代わりに`map -> odom`の自己位置推定TFを担当する
- `vi_global_planner`
  - `src/value_iteration3/vi_ros2/vi_global_planner`に配置される価値反復グローバルプランナ
    （rclrs製Rustノード）
  - `planner_server`の代わりに`compute_path_to_pose`アクションを提供する
  - `/map`（静的地図）から3次元 (x, y, θ) の価値反復を解き、最適方策の
    ロールアウトで`nav_msgs/Path`を生成する。動的障害物の回避はDWBローカル
    プランナ（ローカルコストマップ）または`local_planner:=vi`時の
    `vi_local_planner`が担当する
  - 自己位置はTFではなく`pose_topic`（emcl2: `mcl_pose` / AMCL: `amcl_pose`）
    から取得する（rclrsにtf2バインディングがないため）
  - パラメータは`config/nav2_params.yaml`の`vi_global_planner`セクション
    （ソルバ名、スレッド数、キャッシュ許容差、経路補間間隔など）
- `vi_local_planner`（`local_planner`が`vi`のとき。`planner:=vi`ではデフォルト）
  - `src/value_iteration3/vi_ros2/vi_local_planner`に配置される価値反復狭域
    プランナ（rclrs製Rustノード）
  - `nav2_controller`の`controller_server`の代わりに`follow_path`アクションを
    提供し、経路終端をゴールとして解いた価値関数を、ロボット周囲±1mの
    ローカルウィンドウ内で`/scan`由来のペナルティとともに再反復しながら、
    貪欲方策を`cmd_vel`として出力する（本家value_iteration2の
    `ValueIteratorLocal`方式）
  - ゴール判定は価値反復の`final_state`（`goal_margin_*`パラメータ）そのもの
  - 自己位置は`vi_global_planner`と同じく`pose_topic`から取得する
  - パラメータは`config/nav2_params.yaml`の`vi_local_planner`セクション
    （制御周期`control_frequency`、局所反復の時間予算`refine_budget_ms`など）

### Nav2で使っているもの

`src/autonomous_nav/config/nav2_params.yaml`で、以下のNav2コンポーネントを使っています。

- 自己位置推定
  - `localization:=emcl2`の場合: `emcl2`パッケージの`emcl2_node`
  - `localization:=amcl`の場合: Nav2標準の`nav2_amcl`
- 地図配信
  - `nav2_map_server`
  - `map`起動引数で指定したYAML地図を配信する
- ビヘイビアツリーによるナビゲーション
  - `nav2_bt_navigator`
  - `NavigateToPose`、`NavigateThroughPoses`、経路計算、経路追従、リカバリ、キャンセル系BTノードを使用する
- 制御
  - `local_planner`はデフォルト（`auto`）でグローバルプランナに連動する
    （`planner:=vi`なら`vi`、`planner:=navfn`なら`nav2`）
  - `local_planner`が`vi`の場合（`planner:=vi`時のデフォルト）:
    `vi_local_planner`（value_iteration3）が`controller_server`の代わりに
    `follow_path`を提供する
  - `local_planner`が`nav2`の場合: `nav2_controller`
    - ローカルプランナは`dwb_core::DWBLocalPlanner`
    - 進捗チェックは`nav2_controller::SimpleProgressChecker`
    - ゴール判定は`nav2_controller::SimpleGoalChecker`
- 経路計画
  - `planner:=vi`（デフォルト）の場合: `vi_global_planner`（value_iteration3）が
    `planner_server`の代わりに`compute_path_to_pose`を提供する
    （`vi_global_planner/launch/navigation_launch.py`が`planner_server`抜きで
    Nav2を起動する）
  - `planner:=navfn`の場合: `nav2_planner`の
    `nav2_navfn_planner/NavfnPlanner`（`use_astar: false`のDijkstra系）
- コストマップ
  - `nav2_costmap_2d`
  - ローカルコストマップ: `VoxelLayer` + `InflationLayer`
  - グローバルコストマップ: `StaticLayer` + `ObstacleLayer` + `InflationLayer`
  - 障害物入力は`/scan`
- 経路平滑化
  - `nav2_smoother::SimpleSmoother`
- 復旧動作・行動
  - `nav2_behaviors`
  - `Spin`、`BackUp`、`DriveOnHeading`、`AssistedTeleop`、`Wait`
- 経由地点
  - `nav2_waypoint_follower::WaitAtWaypoint`
- 速度平滑化
  - `nav2_velocity_smoother`
- ライフサイクル
  - `nav2_lifecycle_manager`
  - EMCL2構成では`map_server`用のライフサイクルマネージャーをこのパッケージ側で起動する

### Nav2以外の外部パッケージ

- `slam_toolbox`
  - `mapping.launch.py`で`async_slam_toolbox_node`を起動する
  - `/scan`と`odom`から地図を作成する
- `emcl2`
  - AMCLの代替として使う自己位置推定パッケージ
  - `src/emcl2_ros2`にソースがある
- Raspberry Pi CatのROS 2ドライバ
  - このリポジトリには含まれない
  - `/cmd_vel`、`/odom`、TFなど、実機とのインターフェースを担当する
- LiDARドライバ
  - このリポジトリには含まれない
  - 障害物入力となる`/scan`を配信する
- `rviz2`
  - 地図作成用とナビゲーション用の可視化に使う

### 起動ファイル

#### `src/autonomous_nav/launch/mapping.launch.py`

SLAMで地図を作るための起動ファイルです。

起動するもの:

- `slam_toolbox`の`async_slam_toolbox_node`
- `rviz2`（`use_rviz:=true`の場合）

主な起動引数:

- `namespace`
- `slam_params_file`
  - デフォルト: `config/slam_toolbox_params.yaml`
- `rviz_config`
  - デフォルト: `rviz/mapping.rviz`
- `use_sim_time`
- `use_rviz`

この起動ファイルは実機側のロボットドライバを起動しません。別ターミナルまたはRaspberry Pi Cat
側でセンサー、オドメトリ、TFを起動してから使います。

#### `src/autonomous_nav/launch/navigation.launch.py`

保存済み地図を使ってNav2を起動する起動ファイルです。

共通で起動するもの:

- `rviz2`（`use_rviz:=true`の場合）

`localization:=amcl`かつ`planner:=navfn`の場合:

- `nav2_bringup/launch/bringup_launch.py`をincludeする
- Nav2標準の`amcl`、`map_server`、planner、controller、BT navigatorなどをまとめて起動する

`localization:=amcl`かつ`planner:=vi`の場合:

- `nav2_bringup/launch/localization_launch.py`（`amcl` + `map_server`）
- `vi_global_planner/launch/navigation_launch.py`（下記; `pose_topic:=amcl_pose`）

`localization:=emcl2`または`localization:=emcl`の場合:

- `nav2_map_server`の`map_server`
- `emcl2`パッケージの`emcl2_node`
- `nav2_lifecycle_manager`の`lifecycle_manager_map_server`
- `planner:=navfn`なら`nav2_bringup/launch/navigation_launch.py`、
  `planner:=vi`なら`vi_global_planner/launch/navigation_launch.py`（`pose_topic:=mcl_pose`）
- `use_composition:=true`の場合は`rclcpp_components`の`component_container_isolated`

主な起動引数:

- `map`
  - 使用する地図YAMLへのフルパス
  - 存在しない場合は起動時にエラーになる
- `params_file`
  - デフォルト: `config/nav2_params.yaml`
- `emcl2_params_file`
  - デフォルト: `config/emcl2_params.yaml`
- `rviz_config`
  - デフォルト: `rviz/nav2_default.rviz`
- `use_sim_time`
- `localization`
  - `emcl2`、`emcl`、`amcl`
- `planner`
  - `vi`（デフォルト）、`navfn`
  - `planner:=vi`は`vi_global_planner`パッケージのビルドが必要（起動時に検証される）
- `local_planner`
  - `auto`（デフォルト: グローバルプランナに連動し、`planner:=vi`なら`vi`、
    それ以外は`nav2`）、`nav2`（controller_server/DWB）、`vi`（`vi_local_planner`）
  - `vi`になる場合は`planner:=vi`が前提で、`vi_local_planner`パッケージの
    ビルドが必要（いずれも起動時に検証される）
- `use_rviz`
- `autostart`
- `use_composition`
- `use_respawn`
- `namespace`
- `use_namespace`

#### `vi_global_planner/launch/navigation_launch.py`（value_iteration3側）

`nav2_bringup/launch/navigation_launch.py`（Humble）から派生した、ロボット非依存の
起動ファイルです。`vi_global_planner`パッケージ（`src/value_iteration3/vi_ros2/vi_global_planner`）
が提供し、`planner:=vi`のときに`navigation.launch.py`からincludeされます。相違点:

- `nav2_planner`の`planner_server`を起動せず、lifecycle管理リストからも除外する
- 代わりに`vi_global_planner`ノードを起動する（非composable・非lifecycleの単独プロセス）
- `pose_topic`起動引数（デフォルト`mcl_pose`）を`vi_global_planner`へ渡す
- `local_planner:=vi`の場合はさらに`nav2_controller`の`controller_server`も
  起動せず（lifecycle管理リストからも除外）、代わりに`vi_local_planner`ノードを
  起動する（`cmd_vel`は`cmd_vel_nav`にリマップされ`velocity_smoother`を経由する）

Nav2を使う任意のロボットで、`nav2_bringup/launch/navigation_launch.py`の代わりに
このファイルをincludeすれば価値反復プランナへ切り替えられます。

### 設定ファイル

- `config/nav2_params.yaml`
  - Nav2全体のパラメータ
  - AMCL、ビヘイビアツリーナビゲーター、制御、コストマップ、地図配信、経路計画、経路平滑化、各種行動、経由地点追従、速度平滑化を設定する
  - `vi_global_planner`セクションで価値反復プランナ（ソルバ名`solver`、スレッド数
    `vi_threads`、キャッシュ許容差`goal_tolerance_*`、経路補間間隔`path_spacing`
    など）を設定する
- `config/emcl2_params.yaml`
  - `emcl2_node`用のパラメータ
  - フレーム名、初期姿勢、粒子数、オドメトリモデル、センサリセットなどを設定する
- `config/slam_toolbox_params.yaml`
  - SLAM Toolbox用のパラメータ
  - `map`、`odom`、`base_footprint`、`/scan`を使うmappingモードの設定
- `rviz/mapping.rviz`
  - 地図作成用RViz設定
- `rviz/nav2_default.rviz`
  - Nav2操作用RViz設定
- `maps/*.yaml`、`maps/*.pgm`
  - 保存済み地図

Raspberry Pi Catへ搭載するLiDAR、機体寸法、速度、オドメトリ特性に合わせて、コストマップの
footprint、最大速度、加速度、ノイズモデルなどを調整してください。

## ソース変更後の再ビルド

launch、config、maps、rvizはバインドマウントされているため、通常はイメージの再ビルドが
不要です。依存関係、CMake設定、Dockerfile、EMCL2の取得設定を変更した場合は再ビルド
します。

```powershell
docker compose -f docker/compose.yaml down
docker compose -f docker/compose.yaml build
docker compose -f docker/compose.yaml up -d
```

キャッシュを使わない場合:

```powershell
docker compose -f docker/compose.yaml build --no-cache
```

## 終了

```powershell
docker compose -f docker/compose.yaml down
```

イメージも削除する場合:

```powershell
docker image rm daifuku-autonomous:humble
```

## トラブルシューティング

### Raspberry Pi Catのトピックが見つからない

- 機体側とDocker側の`ROS_DOMAIN_ID`を一致させる
- Dockerのホストネットワークが有効か確認する
- WindowsファイアウォールでDDSの通信を許可する
- 機体とDockerホストが同じネットワークから到達可能か確認する
- 機体側のROS 2ドライバが起動しているか確認する

### RVizが表示されない

- Windows側のXサーバーが起動しているか確認する
- Xサーバーが外部クライアントからの接続を許可しているか確認する
- `DISPLAY`が`host.docker.internal:0.0`になっているか確認する

### コンテナ内で`ros2`が見つからない

`/ros_entrypoint.sh`経由で実行します。

```powershell
docker compose -f docker/compose.yaml exec ros2 /ros_entrypoint.sh bash
```

### コンテナのログを確認する

```powershell
docker compose -f docker/compose.yaml logs
```
