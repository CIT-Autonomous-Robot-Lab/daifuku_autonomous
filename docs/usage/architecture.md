# 構成とパッケージ

## システム構成

```text
Raspberry Pi Cat                         Docker / ネイティブPC
────────────────────                    ──────────────────────
モータードライバ  ←─ /cmd_vel_mux ── twist_mux ←┬ /cmd_vel        ← 経路追従
                                                └ /cmd_vel_teleop ← 遠隔操作
車輪オドメトリ    ─── /odom ─────────→ Nav2 / SLAM / 自己位置推定
2D LiDAR ─ urg_node ─ /scan_raw ───────────────────┐
Mid-360 ─ /livox/lidar ─ 3D→2D ─ スタンプ打ち直し ─┴→ 角度フィルタ → /scan
TF                ─── odom → base_footprint → センサーフレーム
```

Mid-360 + IMUの場合、車輪入力は`/wheel/odom`となり、EKFが最終的な`/odom`と`odom -> base_footprint`を生成します。

速度指令は`twist_mux`が優先度で1本に束ねます（`robot_bringup.launch.py`の
`twist_mux:=true`が既定）。自律側は`/cmd_vel`（優先度10）、遠隔操作は
`/cmd_vel_teleop`（100）で、勝っている側が出しているあいだだけ`/cmd_vel_mux`へ
中継されます。**優先度は非常停止ではありません**（止めるのはモーター電源）。
配線と設定は[`config/README.md`](../../src/daifuku_stack/config/README.md#twist_muxyaml-の配線と優先度)。

Mid-360は時刻同期がないためスタンプが実時計からずれていきます。`restamp_scan.py`が
`/scan_mid360_prestamp`を受信時刻で打ち直して`/scan_raw`へ流し、以降は2D LiDARと同じ
経路になります。詳細は[LiDARとオドメトリ](../setup/lidar.md#タイムスタンプの打ち直し)を
参照してください。

## daifuku_stack

このリポジトリの設定パッケージです。独自のC++ノードは持たず、次のものをまとめています。

- Nav2、SLAM Toolbox、EMCL2の設定
- LiDAR前処理とEKF
- launchファイル
- 地図とRViz設定
- `planner:=vi`用のビヘイビアツリー（`behavior_trees/`）と保存済みwaypoint（`waypoints/`）
- `src/`のPythonノード4本

このうち2本は`lidar:=mid360`のときだけ立ちます。`restamp_scan.py`がスキャンの
スタンプを打ち直し（実機ドライバを立てる`lidar_driver:=true`のときのみ）、
`prepare_mid360_imu.py`が生のIMUメッセージに共分散を付けてEKFへ渡します
（`use_mid360_imu:=true`のときのみ）。どちらの追加条件も既定は`true`です。

残る2本はLiDAR構成によりません。`system_monitor.py`は`navigation.launch.py`だけが
立てます（`use_system_monitor:=true`が既定）。`/proc`を1 Hzで読み、CPUと温度を
`/diagnostics`へ出す役で、受け取るのは[操作パネル（rqt）](control-panel.md)です。
`joy_teleop.py`は`robot_bringup.launch.py`が`joy:=true`（既定）で`joy_node`と一緒に
立てます。`/joy`のボタンの長押しでteleopと自律走行を切り替える役で、詳細は
[ゲームパッドで操作する](joystick.md)にあります。

## raspicat_driver

本体ドライバの自前実装です（`ament_python`）。`robot_bringup.launch.py`の
`driver:=original`で立ち、ステップクロックを`/sys/class/pwm`、方向とモーター電源を
`/dev/gpiochip*`、パルスカウンタを`/dev/i2c-1`から、いずれもユーザ空間で直接扱います。
rtmouseカーネルモジュールは使いません。

ROSに見える契約は既定の`driver:=raspimouse`（公式実装）と同じです。相対名`cmd_vel`を購読し
（`twist_mux:=true`なら`/cmd_vel_mux`へremapされる）、
`/odom`と`odom -> base_footprint` TFを配信し、`motor_power`サービスを持つlifecycleノード
なので、Nav2・EKF・EMCL2の設定は変わりません。LED（`/leds`）、ブザー（`/buzzer`）、
スイッチ（`/switches`）も公式実装と同じ型で持ちます。持たないのは測距センサ
（`/light_sensors`）だけです。

Pi 4とPi 5の両方に対応し、機種差はチップの同定だけです。パラメータは
`config/robot/raspicat_driver.yaml`、実装と機種ごとの前提は
[`src/raspicat_driver/README.md`](../../src/raspicat_driver/README.md)にまとめています。

## launchファイルの構成

`src/daifuku_stack/launch/`の中身です。

| ファイル | 役割 |
| --- | --- |
| `navigation.launch.py` | 自律移動。地図と自己位置推定、その上のNav2／価値反復スタック |
| `mapping.launch.py` | 地図作成。SLAM Toolboxとその入力 |
| `lidar_bringup.launch.py` | LiDARの前処理一式。上の2つから`include`される |
| `robot_bringup.launch.py` | 機体ドライバとURDF。`driver:=raspimouse`（既定 / 公式実装 / Pi 4のみ）または`driver:=original`（自前実装 / [Pi 4](../setup/raspberry-pi-4.md)・[Pi 5](../setup/raspberry-pi-5.md)） |

引数の合成やチェックといった補助的な処理は、launchファイル本体から
`launch/daifuku_stack_launch/`へ切り出しています。

| モジュール | 内容 |
| --- | --- |
| `params.py` | 設定ファイルへの上書きの合成（土台 → `overrides` → `extra_params_file`）。4つのlaunchすべてが使う |
| `backends.py` | `localization`／`planner`／`local_planner`／`nav2`の解決と起動前チェック |
| `lidar.py` | LiDAR構成の共通引数と`lidar_bringup`の`include` |

`lidar`や`lidar_z`のようなLiDAR構成の引数は、`navigation`・`mapping`・
`lidar_bringup`の3ファイルが同じものを宣言します。既定値の出どころは
`lidar.py`の1箇所だけです。

これらのモジュールは`launch/`の下にあるので、launchやconfigと同じく、編集だけなら
再ビルドは要りません（`colcon build --symlink-install`）。ただし`install/`のsymlinkは
ビルド時に張られるため、ファイルを新しく足したときは一度`colcon build`
（`docker/raspberrypi/`では`docker compose up`）が必要です。

## 自己位置推定

- `localization:=emcl2`: 外部パッケージ`emcl2`が`map -> odom`を推定
- `localization:=amcl`: Nav2標準`nav2_amcl`を使用

## 経路計画と追従

`planner:=vi`（既定）:

- `local_planner:=auto`（既定）では`vi_planner`**1ノード**が`planner_server`と
  `controller_server`の両方を置き換え、`compute_path_to_pose`と`follow_path`を提供する
- 静的地図から(x, y, θ)の価値関数を**ゴールにつき1回だけ**全域で解く。経路はその価値
  関数をロールアウトして求め、追従では同じ価値関数を±1 mのウィンドウで精密化する
- ウィンドウへ`/scan`由来のペナルティを加え、貪欲行動を`cmd_vel`として出力する
- 経路計画と追従は**1本の価値関数を共有する**。追従がウィンドウへ書いたペナルティを
  全域掃き（`global_sweep`）が広域へ広げるので、避けた障害物が次の
  `compute_path_to_pose`にも効く。掃きは追従中もバックグラウンドで回り続ける。
  既定のアウトオブコアソルバでは、共有場は状態配列ではなく確定出力（sink）で、掃きは
  それをタイル単位で修復する形になる（同じ更新式・同じ不動点）。密ソルバでも同じ
  ように効く
- 同一ゴールへの再計画ではキャッシュを使う（ロールアウトのみ実行する）
- `local_planner:=nav2`では`vi_global_planner`（広域のみ）とNav2標準`controller_server`
  の組み合わせになる。`map_scale`とアウトオブコアソルバが要る広域地図は、どちらの構成
  でも扱える（`vi_planner`はロボット近傍のパッチだけを密に起こす）。詳細は
  [自律移動](navigation.md#広域地図map_tsudanumaで動かす)を参照

`planner:=navfn`:

- Nav2標準の`planner_server`と`NavfnPlanner`を使う
- `local_planner:=auto`ではNav2標準の`controller_server`とDWBを使う

## Nav2を立てない構成（`nav2:=false`）

**`nav2`の既定は`false`で、素で起動するとNav2のnavigation側のノードは1つも
立ちません。** `vi_planner`が`navigate_to_pose`と`follow_waypoints`も提供する
ためです。`planner:=navfn`や`local_planner:=nav2`へ落とすときは`nav2:=auto`が
要ります（付け忘れると起動時にエラーで止まります）。

アクションの型と名前は`nav2_msgs`のままなので、RVizの`Nav2 Goal`も
`daifuku_waypoint_manager`も`daifuku_rqt`も`joy_teleop`も、配線は一切変わりません。

立たなくなるもの: `bt_navigator`、`behavior_server`、`smoother_server`、
`waypoint_follower`、`lifecycle_manager_navigation`、そして
`behavior_trees/`の2つの木。残るのは`map_server`（自己位置側）と、
`velocity_smoother:=true`（既定）なら`velocity_smoother`とそれを起こす
マネージャ1つだけです。`nav2:=true`で従来の構成に戻せます。

読む設定ファイルも減ります。`config/nav2/`のうち実際に効くのは
`vi_planner.yaml`と`map_server.yaml`だけで、`bt_navigator.yaml`・
`behaviors.yaml`・`controller_server.yaml`・`costmaps.yaml`は**合成には入るが
宛先のノードが立たないので黙って無視されます**。同じ意味のキーは
`vi_planner.yaml`へ移してあります（`stop_on_failure`、`waypoint_pause_sec`）。

### 何が変わるか

BTを挟まなくなることで、VIが損をしていた点が4つ消えます。

| 従来（`nav2:=true`） | `nav2:=false` |
| --- | --- |
| BTが`ComputePathToPose`を毎秒呼ぶ。キャッシュヒットでもロールアウトと補間を**共有ロックの中で**回すので、10 Hzの追従ループと毎秒取り合う | ロールアウトはゴールにつき1回だけ。しかも`plan`トピックへの**表示専用**で、走行は方策を1手ずつ引く |
| ロールアウトが振動（`LoopDetected`）すると`ComputePathToPose`が失敗し、ゴールごと死ぬ | 経路が引けなくても走る。「方策が無い」と「貪欲降下が振動した」は別物なので、後者では走れることが多い |
| リカバリの`Spin`／`BackUp`は`local_costmap/costmap_raw`を待つので、コストマップの無いVI構成では**必ず失敗**する。動くのは`Wait`だけで、その`Wait`の間は`follow_path`が走っていない＝価値関数が1ミリも動かない | 投げ直しの間に`goal_retry_settle_sec`（既定3秒）だけ**止まったまま場を更新する**。スキャンを取り込み続けるので、一度「通れない」と塗った場所のペナルティが半減で消えていく |
| 巡回の先読み（`waypoint_prefetch`）は`/waypoints`を出すものがいないと**警告も出さずに何もしない** | 順路は`follow_waypoints`のゴールそのものなので、必ず届く |

投げ直しの回数は`goal_retry_limit`（既定3、負で無制限）で、BTの
`RecoveryNode number_of_retries: 6`の置き換えです。

RVizの「Navigation 2」パネルは**ほぼ空になります**。あれはライフサイクル
マネージャを叩くパネルで、`nav2:=false`で管理下にあるのは`velocity_smoother`
1つだけ（`velocity_smoother:=false`ならマネージャ自体が居ません）。故障ではあり
ません。ゴールを出すのは同じ「Nav2 Goal」ツールで、経路は`/plan`にそのまま出ます
（表示専用で、走行は方策を1手ずつ引きます）。

## Nav2コンポーネント

構成に応じて次を使用します（`nav2:=false`では上2つ以外は立ちません）。

- `nav2_map_server`: 地図配信
- `nav2_velocity_smoother`: 速度平滑化（`velocity_smoother:=false`で外せる）
- `nav2_bt_navigator`: NavigateToPose等のビヘイビアツリー
- `nav2_costmap_2d`: ローカル／グローバルコストマップ
- `nav2_smoother`: 経路平滑化
- `nav2_behaviors`: Spin、BackUp、Wait等
- `nav2_waypoint_follower`: 経由地点追従
- `nav2_lifecycle_manager`: ライフサイクル管理

ローカルコストマップはVoxelLayer + InflationLayer、グローバルコストマップはStaticLayer + ObstacleLayer + InflationLayerを使い、障害物入力は`/scan`です。**VI構成ではどちらも使いません**（`vi_planner`も`vi_global_planner`もコストマップを持たず、障害物は価値関数のペナルティとして扱う）。

### プロセス構成

Nav2の各ノードは既定でプロセスを分けて起動します（`use_composition:=False`）。
Raspberry Pi 4で1プロセスへ合成すると、DDS参加者あたりのエンドポイント数が大きく
なりすぎて、新規参加者からディスカバリできなくなります。さらにCPU飢餓でライフ
サイクルマネージャのbond心拍が途絶え、自動シャットダウンする事象が頻発しました。
あわせて`config/lifecycle_bond.yaml`でbondのタイムアウトを60秒へ延長しています。

PCなど余裕のある環境では`use_composition:=True`も利用できます。

## 外部パッケージ

- `slam_toolbox`: `/scan`とオドメトリから地図を作成
- `emcl2_ros2`: EMCL2自己位置推定
- `value_iteration3`: Rust/rclrs製の広域・狭域価値反復プランナ
- `livox_ros_driver2`: Mid-360ドライバ
- `pointcloud_to_laserscan`: Mid-360点群の2D化
- `robot_localization`: IMUと車輪オドメトリの融合
- `raspimouse2`: 公式実装の本体ドライバ（`driver:=raspimouse`の`raspimouse`ノード）
- `raspicat_description`: 機体のURDF
- `raspicat_ros`: `raspicat_bringup`。`robot_state_publisher`のlaunch、`lidar:=2d`のURGパラメータ、公式のteleop

外部ソースはDockerビルド時または`vcs import`時に`autonomous_bot.repos`から取得します。
`raspimouse2`が要るのは`driver:=raspimouse`のときだけです。`raspicat_ros`は`driver:=`に
よらず要ります。`robot_bringup.launch.py`が`robot_state_publisher`の起動を
`raspicat_bringup`のlaunchへ任せているためです。
