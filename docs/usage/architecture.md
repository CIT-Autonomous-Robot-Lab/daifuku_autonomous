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
`navigation.launch.py`と`mapping.launch.py`は`/scan`と`/odom`の消費者に徹し、
センサーの引数を1つも持ちません。**この配置のおかげで、navigationを立て直しても
LiDARの初期化待ちは入らず、EKFが再起動して`/odom`が原点へ飛ぶこともありません。**

速度指令は`twist_mux`が優先度で1本に束ねます（`robot_bringup.launch.py`の
`twist_mux:=true`が既定）。自律側は`/cmd_vel`（優先度100）、遠隔操作は
`/cmd_vel_teleop`（10）で、勝っている側が出しているあいだだけ`/cmd_vel_mux`へ
中継されます。**自律側のほうが上なので、自律走行中は遠隔操作が通りません**
（手動へ渡すには先にゴールを取り消す）。**優先度は非常停止ではありません**
（止めるのはモーター電源）。
配線と設定は[`config/README.md`](../../config/README.md#twist_muxyaml-の配線と優先度)。

Mid-360は時刻同期がないためスタンプが実時計からずれていきます。`restamp_scan.py`が
`/scan_mid360_prestamp`を受信時刻で打ち直して`/scan_raw`へ流し、以降は2D LiDARと同じ
経路になります。詳細は[LiDARとオドメトリ](../setup/lidar.md#タイムスタンプの打ち直し)を
参照してください。

## 自前パッケージの分担

独自のC++ノードは持たず、設定とlaunchとPythonノードだけです。**`daifuku_bringup`と
`daifuku_stack`は互いに依存しません**（どちらも`daifuku_config_manager`と
`daifuku_config`にだけ依存）。

| パッケージ | 持つもの | 立てかた |
| --- | --- | --- |
| `daifuku_bringup` | 駆動ドライバ・URDF・`twist_mux`・ゲームパッド・**LiDAR**・**EKF**。`src/`のPythonノード4本 | `docker compose up`で常駐 |
| `daifuku_stack` | Nav2 / SLAM Toolbox / EMCL2のlaunch、地図、RViz、`behavior_trees/`、`waypoints/`、`src/system_monitor.py` | 人が`navigation` / `mapping`を立てる |
| `daifuku_config_manager` | 設定の合成規則（`params.py`）と`src/`のPythonノード2本。**設定の実体は持ちません** | 上2つのlaunchが立てる（自前の入口は無い） |
| `daifuku_config` | 設定の実体だけ。`bringup/`・`stack/`・`overrides/*.yaml`と、走らせる場所を1行で持つ`config/site` | ノードを持たない（`src/`の下でもなく、`src/`と並ぶ`config/`） |

`config/site`はどのlaunchからも同じものが見えます。すべてのlaunchが`overrides`の
既定をここから取り、`navigation.launch.py`は`map`の既定もここから導きます。**場所が
変わるとLiDARの帯・EMCL2と価値反復の調整・地図の3つが同時に変わるので、人が動かす値を
1つにまとめてあります**（切り替えは`tools/site.sh <名前>`。
[日常操作](operations.md#走らせる場所を切り替える)）。

### daifuku_config_managerのPythonノード

**葉のパッケージですが、ノードを2本持ちます。** どちらも自分では入口を持たず、
上2つのlaunchが立てます。

- `site_manager`は`config/site`の読み書きと告知（`/daifuku/site`）を受け持ちます。
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

2本は`lidar:=mid360`のときだけ立ちます。`restamp_scan.py`がスキャンの
スタンプを打ち直し（実機ドライバを立てる`lidar_driver:=true`のときのみ。既定は`true`）、
`prepare_mid360_imu.py`が生のIMUメッセージに共分散を付け、ジャイロのバイアスを引き、
加速度をgから m/s² へ直してEKFへ渡します（`use_mid360_imu:=true`のときのみ。既定は
`true`）。`elevation_filter.py`は点群を仰角で切ります（`elevation_filter:=true`が既定）。
`joy_teleop.py`は`joy:=true`（既定）で`joy_node`と一緒に立ち、`/joy`のボタンの長押しで
teleopと自律走行を切り替えます（[ゲームパッドで操作する](joystick.md)）。

### daifuku_stackのPythonノード

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
`config/bringup/robot/raspicat_driver.yaml`、実装と機種ごとの前提は
[`src/raspicat_driver/README.md`](../../src/raspicat_driver/README.md)にまとめています。

## launchファイルの構成

**機体側（`daifuku_bringup`）と自律移動側（`daifuku_stack`）に分かれています。**
入口は2つで、`robot_bringup`が`docker compose up`で常駐し、`navigation`／`mapping`を
人が立てます。

| パッケージ | ファイル | 役割 |
| --- | --- | --- |
| `daifuku_bringup` | `robot_bringup.launch.py` | **入口。** 機体ドライバとURDF、下2つの`include`。`driver:=original`（標準 / 自前実装 / [Pi 4](../setup/raspberry-pi-4.md)・[Pi 5](../setup/raspberry-pi-5.md)）または`driver:=raspimouse`（公式実装 / rtmouse入りのPi 4のみ。引数そのものの既定値はこちら） |
| `daifuku_bringup` | `lidar_bringup.launch.py` | LiDARの前処理一式（点群→スキャン→角度フィルタ） |
| `daifuku_bringup` | `odom_fusion.launch.py` | 車輪オドメトリとMid-360 IMUのEKF融合 |
| `daifuku_stack` | `navigation.launch.py` | **入口。** 自律移動。地図と自己位置推定、その上のNav2／価値反復スタック |
| `daifuku_stack` | `mapping.launch.py` | **入口。** 地図作成。SLAM Toolboxとその入力 |

下2つは単独でも立てられます。`simulator/`は駆動ドライバが要らないので、
`robot_bringup`を通さずこの2つを直接立てています。

引数の合成やチェックといった補助的な処理は、launchファイル本体から切り出しています。

| モジュール | 場所 | 内容 |
| --- | --- | --- |
| `params.py` | `daifuku_config_manager` | 設定ファイルへの上書きの合成（土台 → `overrides` → `extra_params_file`）と、`config_sentinel`の起動・停止の配線（`sentinel_actions`）。**すべてのlaunchが使う共有部品** |
| `backends.py` | `daifuku_stack/launch/daifuku_stack_launch/` | `localization`／`planner`／`local_planner`／`nav2`の解決と起動前チェック |
| `nav2_params.py` | `daifuku_stack/launch/daifuku_stack_launch/` | `config/stack/nav2/*.yaml`の合成と、`overrides`からの`map:=`の決定。`params.py`へ`base_resolvers`で渡す |
| `lidar.py` | `daifuku_bringup/launch/daifuku_bringup_launch/` | LiDAR構成の共通引数と`lidar_bringup`の`include` |

`lidar`や`lidar_z`のようなLiDAR構成の引数は、`robot_bringup`と`lidar_bringup`の
2ファイルが同じものを宣言します。既定値の出どころは`lidar.py`の1箇所だけです。

> `robot_bringup`の`urdf_lidar_frame`（URDFの2D LiDARリンク名。既定`lidar_link`）と
> `lidar_bringup`の`lidar_frame`（Mid-360のフレーム。既定`livox_frame`）は**別物**です。
> 同じ名前にすると`include`のときに親の値が子へ漏れます。

これらのモジュールは`launch/`の下にあるので、launchやconfigと同じく、編集だけなら
再ビルドは要りません（`colcon build --symlink-install`）。ただし`install/`のsymlinkは
ビルド時に張られるため、ファイルを新しく足したときは一度`colcon build`
（`docker/raspberrypi/`では`docker compose up`）が必要です。

## 自己位置推定

- `localization:=emcl2`（既定）: 外部パッケージ`emcl2`が`map -> odom`を推定
- `localization:=amcl`: Nav2標準`nav2_amcl`を使用
- `localization:=vi`: **emcl2を立てず**、`vi_planner`自身が推定する（上流のVIOLA）。
  `map_server`は残る。`map -> odom`を出すのも`vi_planner`（`publish_tf`）で、
  `pose_topic`は`initialpose`＝**手動シード**（RVizの2D Pose Estimate）になる。
  `planner:=vi`と`nav2:=false`（どちらも既定）が要る

**どの推定器を使うかはlaunchではなく`config/stack/nav2/vi_planner.yaml`の`localizer`**
（`external`／`grid`／`adaptive`／`belief`／`viterbi`）。launch引数が持つのは
「内蔵を使うか」だけなので、2つが噛み合わなければ起動時に止まります。

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
- `local_planner:=nav2`では同じ`vi_planner`を`follow: false`（広域のみ）で立て、追従は
  Nav2標準`controller_server`が担う。**立つVIのノードはどちらでも1つ**（2026-08-08の
  上流の整理まで、広域専用は`vi_global_planner`という別パッケージだった）。
  `map_scale`とアウトオブコアソルバが要る広域地図は、どちらの構成でも扱える
  （`vi_planner`はロボット近傍のパッチだけを密に起こす）。詳細は
  [自律移動](navigation.md#広域地図map_tsudanumaで動かす)を参照

`planner:=navfn`:

- Nav2標準の`planner_server`と`NavfnPlanner`を使う
- `local_planner:=auto`ではNav2標準の`controller_server`とDWBを使う

## Nav2を立てない構成（`nav2:=false`）

**`nav2`の既定は`false`で、素で起動するとNav2のnavigation側はBTも
コントローラも立ちません。** `vi_planner`が`navigate_to_pose`と`follow_waypoints`も
提供するためです（残るのは`velocity_smoother`とそのマネージャだけ。下記）。
`planner:=navfn`や`local_planner:=nav2`へ落とすときは`nav2:=auto`が
要ります（付け忘れると起動時にエラーで止まります）。

アクションの型と名前は`nav2_msgs`のままなので、RVizの`Nav2 Goal`も
`daifuku_waypoint_manager`も`daifuku_rqt`も`joy_teleop`も、配線は一切変わりません。

立たなくなるもの: `bt_navigator`、`behavior_server`、`smoother_server`、
`waypoint_follower`、そして`behavior_trees/`の2つの木。残るのは`map_server`
（自己位置側）と、`velocity_smoother:=true`（既定）なら`velocity_smoother`です。
`lifecycle_manager_navigation`はそのsmootherを起こすためだけに残り、**管理下は
1ノードだけ**になります（`velocity_smoother:=false`にするとマネージャごと消えます）。
`nav2:=true`で従来の構成に戻せます。

読む設定ファイルも減ります。`config/stack/nav2/`のうち実際に効くのは
`vi_planner.yaml`と`map_server.yaml`、それに`behaviors.yaml`の`velocity_smoother`の
節だけです。`bt_navigator.yaml`・`controller_server.yaml`・`costmaps.yaml`と
`behaviors.yaml`の残り3ノード分は**合成には入るが宛先のノードが立たないので黙って
無視されます**。同じ意味のキーは`vi_planner.yaml`へ移してあります
（`stop_on_failure`、`waypoint_pause_sec`）。

### 何が変わるか

BTを挟まなくなることで、VIが損をしていた点が4つ消えます。

| 従来（`nav2:=true`） | `nav2:=false` |
| --- | --- |
| BTが`ComputePathToPose`を毎秒呼ぶ。キャッシュヒットでもロールアウトと補間を**共有ロックの中で**回すので、10 Hzの追従ループと毎秒取り合う | ロールアウトはゴールにつき1回だけ。しかも`plan`トピックへの**表示専用**で、走行は方策を1手ずつ引く |
| ロールアウトが振動（`LoopDetected`）すると`ComputePathToPose`が失敗し、ゴールごと死ぬ | 経路が引けなくても走る。「方策が無い」と「貪欲降下が振動した」は別物なので、後者では走れることが多い |
| リカバリの`Spin`／`BackUp`は`local_costmap/costmap_raw`を待つので、コストマップの無いVI構成では**必ず失敗**する。動くのは`Wait`だけで、その`Wait`の間は`follow_path`が走っていない＝価値関数が1ミリも動かない | 投げ直しの間に3秒（ノード内の固定値）だけ**止まったまま場を更新する**。スキャンを取り込み続けるので、一度「通れない」と塗った場所のペナルティが半減で消えていく |
| 巡回の先読み（`waypoint_prefetch`）は`/waypoints`を出すものがいないと**警告も出さずに何もしない** | 順路は`follow_waypoints`のゴールそのものなので、必ず届く |

投げ直しの回数は`goal_retry_limit`（既定3、負で無制限）で、BTの
`RecoveryNode number_of_retries: 6`の置き換えです。

RVizの「Navigation 2」パネルは**ほぼ空になります**。あれはライフサイクル
マネージャを叩くパネルで、`nav2:=false`で管理下にあるのは`velocity_smoother`
1つだけ（`velocity_smoother:=false`ならマネージャ自体が居ません）。故障ではあり
ません。ゴールを出すのは同じ「Nav2 Goal」ツールで、経路は`/plan`にそのまま出ます
（表示専用で、走行は方策を1手ずつ引きます）。

## Nav2コンポーネント

構成に応じて次を使用します（`nav2:=false`で立つのは最初の3つだけです）。

- `nav2_map_server`: 地図配信
- `nav2_velocity_smoother`: 速度平滑化（`velocity_smoother:=false`で外せる）
- `nav2_lifecycle_manager`: ライフサイクル管理（`nav2:=false`では自己位置側の1つと、
  `velocity_smoother`を起こす1つ）
- `nav2_bt_navigator`: NavigateToPose等のビヘイビアツリー
- `nav2_costmap_2d`: ローカル／グローバルコストマップ
- `nav2_smoother`: 経路平滑化
- `nav2_behaviors`: Spin、BackUp、Wait等
- `nav2_waypoint_follower`: 経由地点追従

ローカルコストマップはVoxelLayer + InflationLayer、グローバルコストマップはStaticLayer + ObstacleLayer + InflationLayerを使い、障害物入力は`/scan`です。**VI構成ではどちらも使いません**（`vi_planner`はコストマップを持たず、障害物は価値関数のペナルティとして扱う）。

### プロセス構成

Nav2の各ノードは既定でプロセスを分けて起動します（`use_composition:=False`）。
Raspberry Pi 4で1プロセスへ合成すると、DDS参加者あたりのエンドポイント数が大きく
なりすぎて、新規参加者からディスカバリできなくなります。さらにCPU飢餓でライフ
サイクルマネージャのbond心拍が途絶え、自動シャットダウンする事象が頻発しました。
あわせて`config/stack/lifecycle_bond.yaml`でbondのタイムアウトを60秒へ延長しています。

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

外部ソースはDockerビルド時または`vcs import`時に`daifuku_autonomous.repos`から取得します。
`raspimouse2`が要るのは`driver:=raspimouse`のときだけです。`raspicat_ros`は`driver:=`に
よらず要ります。`robot_bringup.launch.py`が`robot_state_publisher`の起動を
`raspicat_bringup`のlaunchへ任せているためです。
