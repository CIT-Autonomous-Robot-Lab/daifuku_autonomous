# daifuku_autonomous

Nav2による自律移動

## Requirements

- ROS 2がインストール済み
- `turtlebot3_gazebo`、`turtlebot3_teleop`、`nav2_bringup`、`slam_toolbox`

不足している場合はインストールコマンド:

```bash
rosdep install --from-paths src --ignore-src -r -y
```

## EMCL2のインストール

```bash
vcs import src < autonomous_bot.repo
rosdep install --from-paths src --ignore-src -r -y
```

## Build

```bash
colcon build --packages-select autonomous_nav emcl2 nav2_waypoint_manager
source install/setup.sh
```

## Run

### 1. GazeboでTurtleBot3 worldを起動する

ターミナル1でGazeboを起動します。

```bash
pkill -f gzserver
pkill -f gzclient
pkill -f gazebo
source /usr/share/gazebo/setup.sh
export TURTLEBOT3_MODEL=burger
ros2 launch turtlebot3_gazebo turtlebot3_world.launch.py
```

このlaunchにより、Gazebo上にTurtleBot3、`/scan`、`/odom`、TF、`/clock` などが立ち上がります。

### 2. SLAMを起動して地図を作る

#### SLAMを起動

```bash
ros2 launch autonomous_nav mapping.launch.py use_sim_time:=true
```

#### キーボード操作(/cmd_vel)
```bash
ros2 run turtlebot3_teleop teleop_keyboard
```

#### 地図の保存

```bash
ros2 run nav2_map_server map_saver_cli -f src/autonomous_nav/maps/map
```

保存後、次のファイルが作成または更新される

- `src/autonomous_nav/maps/map.yaml`
- `src/autonomous_nav/maps/map.pgm`

### 3. 保存した地図でNav2を起動

自己位置推定のアルゴリズムを変更したい場合，`localization:=`パラメータを変更すること

- `localization:=emcl2`（デフォルト）
- `localization:=amcl`

#### Simulator

```bash
ros2 launch autonomous_nav navigation.launch.py map:=$PWD/src/autonomous_nav/maps/turtlebot3.yaml use_sim_time:=true localization:=emcl2
```

#### Real Robot
事前作成済みの地図がある場合は，mapパスで指定すること

```bash
ros2 launch autonomous_nav navigation.launch.py map:=$PWD/src/autonomous_nav/maps/map.yaml use_sim_time:=false localization:=emcl2
```


RVizの**2D Pose Estimate** で地図上のロボットの初期姿勢を設定する

### Waypoint Managerパネル

Nav2起動後、RViz2で **Panels → Add New Panel** から
`nav2_waypoint_manager/WaypointManagerPanel` を追加します。パネルで登録した
Waypointは `/waypoint_markers` に表示され、**Start** で
`/follow_waypoints` Actionへ送信されます。詳細は
[`nav2_waypoint_manager/README.md`](src/nav2_waypoint_manager/README.md) を参照してください。

### 4. RVizからゴールを送る

RVizからゴールの2D Poseを指定

## 現在の構成

このワークスペースは、Nav2を中心にした地図作成・自己位置推定・経路計画・追従の構成です。

```text
autonomous_bot
├── README.md
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

### パッケージ

- `autonomous_nav`
  - このリポジトリ側のナビゲーション設定パッケージ
  - Nav2、SLAM Toolbox、RViz、地図ファイル、起動ファイルをまとめている
  - C++/Pythonノードは持たず、`config`、`launch`、`maps`、`rviz`をインストールする
- `emcl2`
  - `src/emcl2_ros2`に配置されている外部自己位置推定パッケージ
  - パッケージ名は`emcl2`
  - `emcl2_node`を起動し、AMCLの代わりに`map -> odom`の自己位置推定TFを担当する

### Nav2で使っているもの

`src/autonomous_nav/config/nav2_params.yaml`で、以下のNav2コンポーネントを使っています。

- 自己位置推定
  - `localization:=emcl2`の場合: `emcl2`パッケージの`emcl2_node`
  - `localization:=amcl`の場合: Nav2標準の`nav2_amcl`
- 地図配信
  - `nav2_map_server`
  - `map` launch引数で指定したYAML地図を配信する
- Behavior Treeナビゲーション
  - `nav2_bt_navigator`
  - `NavigateToPose`、`NavigateThroughPoses`、経路計算、経路追従、リカバリ、キャンセル系BTノードを使用する
- Controller
  - `nav2_controller`
  - ローカルプランナは`dwb_core::DWBLocalPlanner`
  - 進捗チェックは`nav2_controller::SimpleProgressChecker`
  - ゴール判定は`nav2_controller::SimpleGoalChecker`
- Planner
  - `nav2_planner`
  - グローバルプランナは`nav2_navfn_planner/NavfnPlanner`
  - `use_astar: false`なのでDijkstra系のNavFnとして使う
- Costmap
  - `nav2_costmap_2d`
  - local costmap: `VoxelLayer` + `InflationLayer`
  - global costmap: `StaticLayer` + `ObstacleLayer` + `InflationLayer`
  - 障害物入力は`/scan`
- Smoother
  - `nav2_smoother::SimpleSmoother`
- Recovery / Behavior
  - `nav2_behaviors`
  - `Spin`、`BackUp`、`DriveOnHeading`、`AssistedTeleop`、`Wait`
- Waypoint
  - `nav2_waypoint_follower::WaitAtWaypoint`
- 速度平滑化
  - `nav2_velocity_smoother`
- Lifecycle
  - `nav2_lifecycle_manager`
  - EMCL2構成では`map_server`用のlifecycle managerをこのパッケージ側で起動する

### Nav2以外の外部パッケージ

- `slam_toolbox`
  - `mapping.launch.py`で`async_slam_toolbox_node`を起動する
  - `/scan`と`odom`から地図を作成する
- `emcl2`
  - AMCLの代替として使う自己位置推定パッケージ
  - `src/emcl2_ros2`にソースがある
- `turtlebot3_gazebo`
  - シミュレーション用のTurtleBot3 world、ロボット、センサ、TF、`/clock`を起動する
- `turtlebot3_teleop`
  - 地図作成時に`/cmd_vel`へ速度指令を出すために使う
- `rviz2`
  - 地図作成用とナビゲーション用の可視化に使う

### launchファイル

#### `src/autonomous_nav/launch/mapping.launch.py`

SLAMで地図を作るためのlaunchです。

起動するもの:

- `slam_toolbox`の`async_slam_toolbox_node`
- `rviz2`（`use_rviz:=true`の場合）

主なlaunch引数:

- `namespace`
- `slam_params_file`
  - デフォルト: `config/slam_toolbox_params.yaml`
- `rviz_config`
  - デフォルト: `rviz/mapping.rviz`
- `use_sim_time`
- `use_rviz`

このlaunchはGazeboや実機側のロボットドライバは起動しません。別ターミナルでTurtleBot3 Gazeboまたは実機のセンサ・オドメトリ・TFを起動してから使います。

#### `src/autonomous_nav/launch/navigation.launch.py`

保存済み地図を使ってNav2を起動するlaunchです。

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

主なlaunch引数:

- `map`
  - 使用する地図YAMLへのフルパス
  - 存在しない場合はlaunch時にエラーになる
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
  - AMCL、BT Navigator、Controller、Costmap、Map Server、Planner、Smoother、Behavior、Waypoint Follower、Velocity Smootherを設定する
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
