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

上の図は`use_mid360_imu:=false`のときの構成です。**既定は`true`**で、そのときは車輪入力が
`/wheel/odom`に変わり、EKFが最終的な`/odom`と`odom -> base_footprint`を生成します。
**車輪側の付け替えもEKFの起動も`robot_bringup.launch.py`ひとつの仕事**なので、引数1つで
両方が同時に切り替わります。既定値は環境変数`USE_MID360_IMU`（`.env`の1行）から
取ります。詳細は[LiDARとオドメトリ](../setup/lidar.md#imuと車輪オドメトリ)。

**センサーを立てるのは`daifuku_bringup`だけです。** LiDARの前処理もEKFも
`robot_bringup.launch.py`が`include`していて、`docker compose up`で常駐します。
`navigation.launch.py`と`mapping.launch.py`は生データを`/scan`へ変える段を持ち、
`/odom`を読みます。**この配置のおかげで、navigationを立て直しても
LiDARの初期化待ちは入らず、EKFが再起動して`/odom`が原点へ飛ぶこともありません。**

速度指令は`twist_mux`が優先度で1本に束ねます（`robot_bringup.launch.py`の
`twist_mux:=true`が既定）。自律側は`/cmd_vel`（優先度100）、遠隔操作は
`/cmd_vel_teleop`（10）で、勝っている側が出しているあいだだけ`/cmd_vel_mux`へ
中継されます。**自律側のほうが上なので、自律走行中は遠隔操作が通りません**
（手動へ渡すには先にゴールを取り消す）。**優先度は非常停止ではありません**
（止めるのはモーター電源）。
配線と設定は[`src/daifuku_config/README.md`](../../src/daifuku_config/README.md#駆動と停止の理由)。

Mid-360は時刻同期がないためスタンプが実時計からずれていきます。`restamp_scan.py`が
`/scan_mid360_prestamp`を受信時刻で打ち直して`/scan_raw`へ流し、以降は2D LiDARと同じ
経路になります。詳細は[LiDARとオドメトリ](../setup/lidar.md#タイムスタンプの打ち直し)を
参照してください。

## 自前パッケージの分担

機体側と自律移動側で、設定とlaunchの責務を分けます。**`daifuku_bringup`と
`daifuku_stack`は互いに依存しません**（どちらも`daifuku_config_manager`と
`daifuku_config`にだけ依存）。

| パッケージ | 持つもの | 立てかた |
| --- | --- | --- |
| `daifuku_bringup` | 駆動ドライバ・URDF・`twist_mux`・ゲームパッド・**LiDAR**・**EKF**。`src/`のPythonノード4本 | `docker compose up`で常駐 |
| `daifuku_stack` | Nav2 / SLAM Toolbox / EMCL2のlaunch、地図、RViz、`waypoints/`、`src/system_monitor.py`、**点群を`/scan`に変える段**（`scan_pipeline.launch.py`と`src/elevation_filter.py` / `src/restamp_scan.py`） | 人が`navigation` / `mapping`を立てる |
| `daifuku_config_manager` | 設定の合成規則（`params.py`）と`src/`のPythonノード2本。**設定の実体は持ちません** | 上2つのlaunchが立てる（自前の入口は無い） |
| `daifuku_config` | 設定の実体だけ。`bringup/`・`stack/`・`overrides/*.yaml`と、走らせる場所を1行で持つ`src/daifuku_config/site` | ノードを持たない（`src/`の下でもなく、`src/`と並ぶ`src/daifuku_config/`） |

`src/daifuku_config/site`はどのlaunchからも同じものが見えます。すべてのlaunchが`overrides`の
既定をここから取り、`navigation.launch.py`は`map`の既定もここから導きます。**場所が
変わるとLiDARの帯・EMCL2と価値反復の調整・地図の3つが同時に変わるので、人が動かす値を
1つにまとめてあります**（切り替えは`tools/site.sh <名前>`。
[日常操作](operations.md#走らせる場所を切り替える)）。**その3つはどれも
`daifuku_stack`側が読みます** — 2026-08-25にLiDARの帯が`/scan`を作る段ごと移ったので、
機体（常駐している`raspicat`サービス）は場所を知りません。切り替えたら
`navigation` / `mapping`を立て直すだけです。

### daifuku_config_managerのPythonノード

**葉のパッケージですが、ノードを2本持ちます。** どちらも自分では入口を持たず、
上2つのlaunchが立てます。

- `site_manager`は`src/daifuku_config/site`の読み書きと告知（`/daifuku/site`）を受け持ちます。
  **立てるのは`robot_bringup.launch.py`の1か所だけ**です。2つ立てると同じファイルを
  2つのノードが書きに行きます。機体は常駐しているので、navigationを立てていない
  あいだも`ros2 param set /site_manager site <名前>`が通ります
- `config_sentinel`は、自分のlaunchが起動時に読んだ設定が書き変わっていないかを
  見張ります。**top-levelのlaunchが1つずつ**立てます（`robot_bringup`、
  `navigation`、`mapping`）。`include`される側でも立てると、1つのlaunch木に見張りが
  3つ並んでそれぞれが勝手に落としにかかります

見ているのは`daifuku_config`のうち自分のパッケージの段（`bringup/`か`stack/`）の
`*.yaml`全部と、重ねている`overrides`のうち自分の部分木です。**中身を正規化してから指紋を取るので、
コメントや並び順を直しただけでは反応しません。** 落とすかどうかの判断と、落ちた
あと誰が上げ直すかは[日常操作](operations.md#設定変更を反映する)。

### daifuku_bringupのPythonノード

`prepare_mid360_imu.py`が生のIMUメッセージに共分散を付け、ジャイロのバイアスを引き、
加速度をgから m/s² へ直してEKFへ渡します（`lidar:=mid360`かつ`use_mid360_imu:=true`の
ときのみ。どちらも既定）。`joy_teleop.py`は`joy:=true`（既定）で`joy_node`と一緒に
立ち、`/joy`のボタンの長押しでteleopと自律走行を切り替えます
（[ゲームパッドで操作する](joystick.md)）。

**`restamp_scan.py`と`elevation_filter.py`は2026-08-25に`daifuku_stack`へ移りました**
（`/scan`を作る段ごと）。

### daifuku_stackのPythonノード

`elevation_filter.py`は点群を仰角で切り（`elevation_filter:=true`が既定）、
`restamp_scan.py`はスキャンのスタンプを打ち直します（`lidar:=mid360`かつ
実機ドライバを立てる`lidar_driver:=true`のときのみ。どちらも既定）。2本とも
`scan_pipeline.launch.py`が立てるので、`navigation`でも`mapping`でも上がります。

`system_monitor.py`は`navigation.launch.py`だけが立てます（`use_system_monitor:=true`が
既定）。`/proc`を1 Hzで読み、CPUと温度を`/diagnostics`へ出す役で、受け取るのは
[操作パネル（rqt）](control-panel.md)です。**機体側へ移していないのは、プロセス別の
内訳がPID名前空間の中しか見えないため**です（`pid: host`は設定していないので、
`raspicat`コンテナへ移すとNav2とVIが内訳から消えます）。ホスト全体のCPUと
ロードアベレージのほうは`/proc/stat`が名前空間化されないのでどちらでも同じです。

## raspicat_driver

本体ドライバの自前実装です（`ament_python`）。`robot_bringup.launch.py`の
`driver:=original`で立ち、ステップクロックを`/sys/class/pwm`、方向とモーター電源を
`/dev/gpiochip*`、パルスカウンタを`/dev/i2c-1`から、いずれもユーザ空間で直接扱います。
rtmouseカーネルモジュールは使いません。

ROSに見える契約は`driver:=raspimouse`（公式実装）と同じです。相対名`cmd_vel`を購読し
（`twist_mux:=true`なら`/cmd_vel_mux`へremapされる）、
`/odom`と`odom -> base_footprint` TFを配信し、`motor_power`サービスを持つlifecycleノード
なので、Nav2・EKF・EMCL2の設定は変わりません。LED（`/leds`）、ブザー（`/buzzer`）、
スイッチ（`/switches`）も公式実装と同じ型で持ちます。持たないのは測距センサ
（`/light_sensors`）だけです。

Pi 4とPi 5の両方に対応し、機種差はチップの同定だけです。パラメータは
`src/daifuku_config/bringup/robot/raspicat_driver.yaml`、実装と機種ごとの前提は
[`src/raspicat_driver/README.md`](../../src/raspicat_driver/README.md)にまとめています。

## launchファイルの構成

**機体側（`daifuku_bringup`）と自律移動側（`daifuku_stack`）に分かれています。**
入口は2つで、`robot_bringup`が`docker compose up`で常駐し、`navigation`／`mapping`を
人が立てます。

| パッケージ | ファイル | 役割 |
| --- | --- | --- |
| `daifuku_bringup` | `robot_bringup.launch.py` | **入口。** 機体ドライバとURDF、下2つの`include`。`driver:=original`（標準 / 自前実装 / [Pi 4](../setup/raspberry-pi-4.md)・[Pi 5](../setup/raspberry-pi-5.md)）または`driver:=raspimouse`（公式実装 / rtmouse入りのPi 4のみ。引数そのものの既定値はこちら） |
| `daifuku_bringup` | `lidar_bringup.launch.py` | LiDARのドライバと取り付け位置の静的TF（出すのは生データまで） |
| `daifuku_bringup` | `odom_fusion.launch.py` | 車輪オドメトリとMid-360 IMUのEKF融合 |
| `daifuku_stack` | `navigation.launch.py` | **入口。** 自律移動。地図と自己位置推定、その上のNav2／価値反復スタック |
| `daifuku_stack` | `mapping.launch.py` | **入口。** 地図作成。SLAM Toolboxとその入力 |
| `daifuku_stack` | `scan_pipeline.launch.py` | 点群（または生スキャン）→仰角フィルタ→スキャン→打ち直し→角度フィルタ→`/scan`。上2つが`include`する |

入口でない3つは単独でも立てられます。`simulator/`は駆動ドライバが要らないので、
`robot_bringup`を通さず`odom_fusion`を直接立て、`/scan`を作る段は`navigation`に
任せています（`lidar_driver:=false`を渡す）。

**`scan_pipeline`が`daifuku_stack`に居るのは2026-08-25から**です。場所ごとに変わる値
（LiDARの帯と仰角）を持つのはこの段だけで、機体側に置いていたあいだは常駐している
`raspicat`サービスが`src/daifuku_config/site`を読む必要がありました。移したことで
機体は場所を知らなくなり、地図を変えても立て直しが要らなくなっています。裏返しに
**`lidar:=`と`lidar_driver:=`は2つのlaunchにまたがる**ので、既定を環境変数
（`LIDAR` / `LIDAR_DRIVER`）から取ってCompose に配らせています。

引数の合成やチェックといった補助的な処理は、launchファイル本体から切り出しています。

| モジュール | 場所 | 内容 |
| --- | --- | --- |
| `params.py` | `daifuku_config_manager` | 設定ファイルへの上書きの合成（土台 → `overrides` → `extra_params_file`）と、`config_sentinel`の起動・停止の配線（`sentinel_actions`）。**すべてのlaunchが使う共有部品** |
| `backends.py` | `daifuku_stack/launch/daifuku_stack_launch/` | `localization`の解決とVI設定の起動前チェック |
| `nav2_params.py` | `daifuku_stack/launch/daifuku_stack_launch/` | `src/daifuku_config/stack/nav2/*.yaml`と`src/daifuku_config/stack/vi_planner.yaml`の合成と、`overrides`からの`map:=`の決定。`params.py`へ`base_resolvers`で渡す |
| `lidar.py` | `daifuku_bringup/launch/daifuku_bringup_launch/` | LiDARドライバの共通引数と`lidar_bringup`の`include` |
| `scan.py` | `daifuku_stack/launch/daifuku_stack_launch/` | `/scan`を作る段の共通引数と`scan_pipeline`の`include` |

`lidar`や`lidar_z`のようなLiDARドライバの引数は、`robot_bringup`と`lidar_bringup`の
2ファイルが同じものを宣言します。既定値の出どころは`lidar.py`の1箇所だけです。
`/scan`を作る段の引数（`elevation_filter`や`scan_filter_enabled`）も同様に、
`navigation` / `mapping` / `scan_pipeline`の3ファイルが`scan.py`の1つの表から
宣言します。

> `robot_bringup`の`urdf_lidar_frame`（URDFの2D LiDARリンク名。既定`lidar_link`）と
> `lidar_bringup`の`lidar_frame`（Mid-360のフレーム。既定`livox_frame`）は**別物**です。
> 同じ名前にすると`include`のときに親の値が子へ漏れます。

これらのモジュールは`launch/`の下にあるので、launchやconfigと同じく、編集だけなら
再ビルドは要りません（`colcon build --symlink-install`）。ただし`install/`のsymlinkは
ビルド時に張られるため、ファイルを新しく足したときは一度`colcon build`
（`docker/raspberrypi/`では`docker compose up`）が必要です。

## 自己位置推定

- `localization:=emcl2`（既定）: 外部パッケージ`emcl2`が`map -> odom`を推定
- `localization:=emcl3d`: 外部パッケージ`emcl3d`が`/livox/lidar`とPCD地図（`site: map: pointcloud:`）で
  `map -> odom`を推定（6DoF）。経路計画の`/map`は2D地図のまま
- `localization:=amcl`: Nav2標準`nav2_amcl`を使用
- `localization:=vi`: **emcl2を立てず**、`vi_planner`自身が推定する（上流のVIOLA）。
  `map_server`は残る。`map -> odom`を出すのも`vi_planner`（`publish_tf`）で、
  `pose_topic`は`initialpose`＝**手動シード**（RVizの2D Pose Estimate）になる。

**どの推定器を使うかはlaunchではなく`src/daifuku_config/stack/vi_planner.yaml`の`localizer`**
（`external`／`grid`／`adaptive`／`belief`／`viterbi`）。launch引数が持つのは
「内蔵を使うか」だけなので、2つが噛み合わなければ起動時に止まります。

## 経路計画と追従

`vi_planner`をstandaloneで起動し、経路計画と追従に同じ価値関数を使います。
`compute_path_to_pose`、`follow_path`、`navigate_to_pose`、`follow_waypoints`を提供し、
アクション型は`nav2_msgs`なのでRVizや操作パネルからそのまま使えます。
`navigate_through_poses`は提供しません。複数点の巡回は`follow_waypoints`へ渡します。

静的地図からゴールごとに価値関数を解き、追従中は`/scan`のペナルティを機体周辺の
窓へ反映します。`global_sweep`は変化した範囲を広域へ伝播します。投げ直しの上限は
`goal_retry_limit`で、待機中も止まったままスキャンを取り込んで場を更新します。
設定と広域地図の注意点は[自律移動](navigation.md#経路計画と追従)を参照してください。

## 共有するNav2ユーティリティ

地図配信の`nav2_map_server`、AMCL、速度平滑化の`nav2_velocity_smoother`、
それらを管理する`nav2_lifecycle_manager`は残ります。BT、NavFn、DWB、
コストマップ、Nav2のbehavior・waypoint followerは起動しません。
`velocity_smoother:=false`ならnavigation側のsmootherとマネージャも立ちません。

RVizの「Nav2 Goal」は使用できます。「Navigation 2」パネルは`/waypoints`へ
MarkerArrayを出し、自前パネルのPathと型が衝突するため追加しないでください。

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

外部ソースはDockerビルド時または`vcs import`時に`daifuku_autonomous.repos`から取得します。
`raspimouse2`が要るのは`driver:=raspimouse`のときだけです。`raspicat_ros`は`driver:=`に
よらず要ります。`robot_bringup.launch.py`が`robot_state_publisher`の起動を
`raspicat_bringup`のlaunchへ任せているためです。
