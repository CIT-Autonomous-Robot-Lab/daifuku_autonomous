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
**車輪側の付け替えもEKFの起動も`robot.launch.py`ひとつの仕事**なので、引数1つで
両方が同時に切り替わります。既定値は環境変数`USE_MID360_IMU`（`.env`の1行）から
取ります。詳細は[LiDARとオドメトリ](../setup/lidar.md#imuと車輪オドメトリ)。

**センサーを立てるのは機体側の`robot.launch.py`だけです。** 駆動ドライバ、LiDARドライバ、
EKFは`daifuku_robot`の実装で、`docker compose up`で常駐します。navigationとmappingの
公開launchは生データを`/scan`へ変える段（`daifuku_perception`）を持ち、`/odom`を読みます。
**この配置のおかげで、navigationを立て直してもLiDARの初期化待ちは入らず、
EKFが再起動して`/odom`が原点へ飛ぶこともありません。**

速度指令は`twist_mux`が優先度で1本に束ねます（`robot.launch.py`の
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

機能ごとにパッケージを分け、人が立てる公開の合成は`daifuku_bringup`に集めます。
機能パッケージどうしは公開入口として互いを使いません。設定の実体と合成規則は
`daifuku_config`にあり、どの機能パッケージもそこへ依存します。

| パッケージ | 持つもの |
| --- | --- |
| `daifuku_robot` | 駆動ドライバの起動、URDF、`twist_mux`、ゲームパッド（teleop）、LiDARドライバ、EKF、Mid-360 IMUの前処理 |
| `daifuku_perception` | 点群（または生スキャン）を`/scan`に変える段。`scan_pipeline.launch.py`、仰角フィルタ、スタンプの打ち直し |
| `daifuku_localization` | 自己位置推定器（EMCL2 / EMCL3D / AMCL）の起動と leveling |
| `daifuku_navigation` | 価値反復の計画・追従（`vi_planner`）、速度平滑化、計画用の map server。再利用用の`navigation.launch.py` |
| `daifuku_mapping` | SLAM Toolbox による地図作成。bringupから`include`し、自分から機体やスキャン生成を起動しない |
| `daifuku_maps` | 地図（`maps/`）と順路（`waypoints/`） |
| `daifuku_bringup` | 公開の合成launch（`robot.launch.py`、`navigation.<場所>.launch.py`、`mapping.launch.py`）、RViz設定（`rviz/`）、`system_monitor` |
| `daifuku_config` | 設定の実体（`robot/`・`perception/`・`localization/`・`mapping/`・`navigation/`・`sites/<場所>/`）、合成規則のPython（`src/daifuku_config/daifuku_config/`）、`site_manager`と`config_sentinel` |

### 場所の決まりかた

**navigationで走る場所はlaunchの名前で決まります。** `navigation.19f.launch.py`は19Fを、
`navigation.tsudanuma.launch.py`は津田沼を走らせ、その場所の地図と
`src/daifuku_config/sites/<場所>/overrides.yaml`を必ず使います。`OVERRIDES`環境変数や
`src/daifuku_config/site`ファイルで別の場所へ変えることはできません。

`src/daifuku_config/site`と`site_manager`は、**次に立てる場所**を任意で記録するだけです。
`tools/site.sh <名前>`は次の場所を選び、それに対応する起動コマンドを表示します。
走っているlaunchには触れません。機体（常駐している`raspicat`サービス）は場所を知らないので、
場所を変えるときに機体を立て直す必要はありません。

### config_sentinel

`config_sentinel`は、自分のlaunchが起動時に読んだ設定が書き変わっていないかを見張ります。
**top-levelのlaunchがちょうど1つずつ**立て、`include`される側では立てません。
見張る範囲は次のとおりです。

| launch | 見張る設定 |
| --- | --- |
| `robot.launch.py` | `robot/`だけ |
| `navigation.<場所>.launch.py` | その場所の navigation・localization・perception の設定と、使用中プロファイルが明示したファイル |
| `mapping.launch.py` | `mapping/`と`perception/`（重ねた`overrides`の該当部分を含む） |

**中身を正規化してから指紋を取るので、コメントや並び順を直しただけでは反応しません。**
固定された場所の設定が変わると、設定どおり機体の静止を待ってからlaunchを終了します。
`site`ファイルの書き換えのような、走っている場所と関係しない選択では終了しません。
落ちたあと誰が上げ直すかは[日常操作](operations.md#設定変更を反映する)。

### Pythonノード

`daifuku_robot`の`prepare_mid360_imu.py`は生のIMUメッセージに共分散を付け、
ジャイロのバイアスを引き、加速度をgから m/s² へ直してEKFへ渡します
（`lidar:=mid360`かつ`use_mid360_imu:=true`のときのみ。どちらも既定）。
`joy_teleop.py`は`joy:=true`（既定）で`joy_node`と一緒に立ち、`/joy`のボタンの長押しで
teleopと自律走行を切り替えます（[ゲームパッドで操作する](joystick.md)）。

`daifuku_perception`の`elevation_filter.py`は点群を仰角で切り（`elevation_filter:=true`が既定）、
`restamp_scan.py`はスキャンのスタンプを打ち直します（`lidar:=mid360`かつ
実機ドライバを立てる`lidar_driver:=true`のときのみ。どちらも既定）。2本とも
`scan_pipeline.launch.py`が立てるので、navigationでもmappingでも上がります。

`daifuku_bringup`の`system_monitor.py`はnavigationの公開launchだけが立てます
（`use_system_monitor:=true`が既定）。`/proc`を1 Hzで読み、CPUと温度を`/diagnostics`へ
出す役で、受け取るのは[操作パネル（rqt）](control-panel.md)です。**機体側で立てないのは、
プロセス別の内訳がPID名前空間の中しか見えないため**です（`pid: host`は設定していないので、
`raspicat`コンテナで立てるとNav2とVIが内訳から消えます）。

## raspicat_driver

本体ドライバの自前実装です（`ament_python`）。`robot.launch.py`の
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
`src/daifuku_config/robot/robot/raspicat_driver.yaml`、実装と機種ごとの前提は
[`src/raspicat_driver/README.md`](../../src/raspicat_driver/README.md)にまとめています。

## launchファイルの構成

人が使う入口は`daifuku_bringup`の公開launchだけです。機体は`robot.launch.py`が
`docker compose up`で常駐し、navigationかmappingを人が立てます。**navigationとmappingは
TFと`/scan`が重なるので同時に立てません。**

| パッケージ | ファイル | 役割 |
| --- | --- | --- |
| `daifuku_bringup` | `robot.launch.py` | **公開入口。** 機体ドライバとURDF、LiDAR、EKF。`driver:=original`（標準 / 自前実装 / [Pi 4](../setup/raspberry-pi-4.md)・[Pi 5](../setup/raspberry-pi-5.md)）または`driver:=raspimouse`（公式実装 / rtmouse入りのPi 4のみ） |
| `daifuku_bringup` | `navigation.19f.launch.py`、`navigation.tsudanuma.launch.py`、`navigation.tsudanuma_mugimaru.launch.py`、`navigation.tsukuba2025.launch.py`、`navigation.tsukuba2026.launch.py` | **公開入口。** 場所を固定した自律移動。地図、自己位置推定、価値反復、`/scan`を作る段 |
| `daifuku_bringup` | `mapping.launch.py` | **公開入口。** 地図作成。`overrides:=<場所>`（既定`none`） |
| `daifuku_robot` | `robot_bringup.launch.py`、`lidar_bringup.launch.py`、`odom_fusion.launch.py` | 機体、LiDARドライバと取り付け位置の静的TF、車輪オドメトリとIMUのEKF融合 |
| `daifuku_perception` | `scan_pipeline.launch.py` | 点群（または生スキャン）→仰角フィルタ→スキャン→打ち直し→角度フィルタ→`/scan` |
| `daifuku_navigation` | `navigation.launch.py` | 合成から使う再利用部品。**これ単体は公開の完全な構成ではありません** |
| `daifuku_mapping` | `mapping.launch.py` | SLAM Toolbox。`daifuku_bringup`の`mapping.launch.py`が使う |

シミュレータの公開入口は`navigation.vtc.launch.py`で、外部の地図を明示して渡し、
`overrides`は`none`です（[VTC ハーネス](../../simulator/README.md)）。駆動ドライバが
要らないので機体の駆動とEKFは立てず、`lidar:=2d lidar_driver:=false`で`/scan`を作る段だけ使います。

`lidar:=`と`lidar_driver:=`は機体とnavigation / mappingの両方にまたがるので、
既定を環境変数（`LIDAR` / `LIDAR_DRIVER`）から取ってComposeに配らせています。

引数の合成やチェックといった補助的な処理は、launchファイル本体から切り出しています。

| モジュール | 場所 | 内容 |
| --- | --- | --- |
| `params.py` | `daifuku_config` | 設定ファイルへの上書きの合成（土台 → `overrides` → `extra_params_file`）と、`config_sentinel`の起動・停止の配線。**すべてのlaunchが使う共有部品** |
| `lidar.py` | `daifuku_robot/launch/daifuku_robot_launch/` | LiDARドライバの共通引数と`lidar_bringup`の`include` |
| `scan.py` | `daifuku_perception/launch/daifuku_perception_launch/` | `/scan`を作る段の共通引数と`scan_pipeline`の`include` |
| 推定器の解決、Nav2断片と`vi_planner.yaml`の合成 | `daifuku_navigation` / `daifuku_localization` | `localization`の解決、VI設定の起動前チェック、`src/daifuku_config/navigation/`の合成と地図の決定 |

`lidar`や`lidar_z`のようなLiDARドライバの引数は、`robot_bringup`と`lidar_bringup`の
2ファイル（`daifuku_robot`）が同じものを宣言します。既定値の出どころは`lidar.py`の1箇所だけです。
`/scan`を作る段の引数（`elevation_filter`や`scan_filter_enabled`）も同様に、
navigation / mapping / `scan_pipeline`のlaunchが`scan.py`の1つの表から
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

**どの推定器を使うかはlaunchではなく`src/daifuku_config/navigation/vi_planner.yaml`の`localizer`**
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
よらず要ります。`robot.launch.py`が`robot_state_publisher`の起動を
`raspicat_bringup`のlaunchへ任せているためです。
