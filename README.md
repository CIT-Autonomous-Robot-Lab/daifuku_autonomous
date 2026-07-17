# daifuku_autonomous

[株式会社アールティのRaspberry Pi Cat](https://rt-net.jp/products/raspicat)を、
ROS 2 Navigation2で自律移動させるためのワークスペースです。

ROS 2、Nav2、SLAM Toolbox、RViz、EMCL2は、DockerコンテナまたはネイティブのROS 2
環境で実行できます。Dockerを使う場合、ホストへのROS 2のインストールは不要です。

> **重要:** このリポジトリにはRaspberry Pi Catのデバイスドライバやセンサードライバは
> 含まれていません。機体側でROS 2のドライバを起動し、必要なトピックとTFを配信して
> ください。

## システム構成

DockerコンテナまたはネイティブPCと、Raspberry Pi Catは同じROS 2ネットワークに
参加します。

```text
Raspberry Pi Cat                         Docker / ネイティブPC
────────────────────                    ──────────────────────
モータードライバ  ←── /cmd_vel ─────── Nav2 / 遠隔操作
オドメトリ        ─── /odom ─────────→ Nav2 / SLAM / EMCL2
LiDAR             ─── /scan ─────────→ Nav2 / SLAM / EMCL2
TF                ─── odom → base_footprint → センサーフレーム
                                          │
                                          └─ RViz
```

機体側で必要なROS 2インターフェース:

- `/cmd_vel`（`geometry_msgs/msg/Twist`）を受信する
- `/odom`（`nav_msgs/msg/Odometry`）を配信する
- `/scan`（`sensor_msgs/msg/LaserScan`）を配信する
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

## ネイティブ環境でのセットアップ

Dockerを使わず、Ubuntu 22.04へインストールしたROS 2 Humble上でも実行できます。

### 必要なもの

- ROS 2 Humble
- `nav2_bringup`
- `slam_toolbox`
- `rviz2`
- Raspberry Pi CatのROS 2対応ドライバとセンサードライバ

ワークスペースのルートでEMCL2を取得します。

```bash
vcs import . < autonomous_bot.repos
```

依存パッケージをインストールします。

```bash
rosdep update
rosdep install --from-paths src --ignore-src -r -y
```

### ビルド

```bash
source /opt/ros/humble/setup.bash
colcon build --packages-select autonomous_nav emcl2 --symlink-install
source install/setup.bash
```

新しいターミナルを開くたびに、ROS 2とワークスペースの環境設定を読み込んでください。

```bash
source /opt/ros/humble/setup.bash
source install/setup.bash
```

### 地図作成

Raspberry Pi Cat側でセンサー、オドメトリ、TFを起動してから、SLAMを起動します。

```bash
ros2 launch autonomous_nav mapping.launch.py use_sim_time:=false
```

機体側の操作ノードやゲームパッドで走行した後、地図を保存します。

```bash
ros2 run nav2_map_server map_saver_cli -f src/autonomous_nav/maps/map
```

### 自律移動

EMCL2を使う場合:

```bash
ros2 launch autonomous_nav navigation.launch.py \
  map:=$PWD/src/autonomous_nav/maps/map.yaml \
  use_sim_time:=false localization:=emcl2
```

Nav2標準のAMCLを使う場合:

```bash
ros2 launch autonomous_nav navigation.launch.py \
  map:=$PWD/src/autonomous_nav/maps/map.yaml \
  use_sim_time:=false localization:=amcl
```

RVizの「2D Pose Estimate」で初期姿勢を設定し、「Nav2 Goal」で移動先を指定します。

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

Raspberry Pi Cat側でモータードライバ、オドメトリ、LiDAR、TFを起動します。Docker側で
`/scan`、`/odom`、必要なTFを確認してから次へ進みます。

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
    └── emcl2_ros2
        ├── config
        ├── launch
        ├── include
        └── src
```

Dockerイメージ内ではEMCL2を`/opt/ros_ws/src/emcl2_ros2`へ取得します。ネイティブ環境では
`vcs import`により、このリポジトリの`src/emcl2_ros2`へ取得します。

### パッケージ

- `autonomous_nav`
  - このリポジトリ側のナビゲーション設定パッケージ
  - Nav2、SLAM Toolbox、RViz、地図ファイル、起動ファイルをまとめている
  - C++/Pythonノードは持たず、`config`、`launch`、`maps`、`rviz`をインストールする
- `emcl2`
  - `src/emcl2_ros2`に配置される外部自己位置推定パッケージ
  - パッケージ名は`emcl2`
  - `emcl2_node`を起動し、AMCLの代わりに`map -> odom`の自己位置推定TFを担当する

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
  - `nav2_controller`
  - ローカルプランナは`dwb_core::DWBLocalPlanner`
  - 進捗チェックは`nav2_controller::SimpleProgressChecker`
  - ゴール判定は`nav2_controller::SimpleGoalChecker`
- 経路計画
  - `nav2_planner`
  - グローバルプランナは`nav2_navfn_planner/NavfnPlanner`
  - `use_astar: false`なのでDijkstra系のNavFnとして使う
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

`localization:=amcl`の場合:

- `nav2_bringup/launch/bringup_launch.py`をincludeする
- Nav2標準の`amcl`、`map_server`、planner、controller、BT navigatorなどをまとめて起動する

`localization:=emcl2`または`localization:=emcl`の場合:

- `nav2_map_server`の`map_server`
- `emcl2`パッケージの`emcl2_node`
- `nav2_lifecycle_manager`の`lifecycle_manager_map_server`
- `nav2_bringup/launch/navigation_launch.py`
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
- `use_rviz`
- `autostart`
- `use_composition`
- `use_respawn`
- `namespace`
- `use_namespace`

### 設定ファイル

- `config/nav2_params.yaml`
  - Nav2全体のパラメータ
  - AMCL、ビヘイビアツリーナビゲーター、制御、コストマップ、地図配信、経路計画、経路平滑化、各種行動、経由地点追従、速度平滑化を設定する
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
