# 設定リファレンス

## 主なファイル

**設定は3つのパッケージに分かれています。** `daifuku_stack`が自律移動、
`daifuku_bringup`が機体（駆動・LiDAR・EKF）、`daifuku_config_manager`が
`overrides/`です。下の表の`config/`は各パッケージのものです。

**機体側（`daifuku_bringup`）の値を変えたら`docker compose up -d`が要ります。**
読むのは常駐している`raspicat`サービスなので、navigationを立て直しても変わりません。

| ファイル | パッケージ | 内容 |
|---|---|---|
| `config/overrides/*.yaml` | `config_manager` | 地図・環境ごとの上書き（`overrides:=`で重ねる）。行き先は**パッケージ名とノード名**で決まるので、この表の`MID360_config.json`以外すべてを上書きできる |
| `config/nav2/*.yaml` | `stack` | Nav2、価値反復プランナ、コストマップ、速度、ゴール判定（起動時に1つへ合成） |
| `behavior_trees/*.xml` | `stack` | `planner:=vi`用のビヘイビアツリー（起動時に自動で選択）。**`nav2:=true`のときだけ読まれる**（既定では`bt_navigator`が立たない） |
| `config/localization/emcl2.yaml` | `stack` | EMCL2のフレーム、初期姿勢、粒子数、オドメトリモデル |
| `config/lifecycle_bond.yaml` | `stack` | ライフサイクルマネージャのbondタイムアウト |
| `config/mapping/slam_toolbox.yaml` | `stack` | SLAM Toolboxのmapping設定 |
| `rviz/mapping.rviz` | `stack` | 地図作成用RViz |
| `rviz/navigation.rviz` | `stack` | 自律移動用RViz |
| `maps/*.yaml`, `maps/*.pgm` | `stack` | 保存済み地図 |
| `config/robot/raspicat.yaml` | `bringup` | 公式実装（`driver:=raspimouse`）のパラメータ。車輪径、トレッド、オドメトリ源 |
| `config/robot/raspicat_driver.yaml` | `bringup` | 自前実装（`driver:=original`）のパラメータ。上に加えてGPIO・PWM・I2Cの配線 |
| `config/robot/twist_mux.yaml` | `bringup` | 速度指令の仲裁（購読トピックと優先度）。`twist_mux:=true`（既定）のときだけ |
| `config/robot/joy_teleop.yaml` | `bringup` | ゲームパッド。`joy_node`と`joy_teleop`の2ノード分が1ファイルに入る。`joy:=true`（既定）のときだけ |
| `config/sensors/scan_filter.yaml` | `bringup` | LiDARの角度フィルタ |
| `config/sensors/MID360_config.json` | `bringup` | Mid-360とホストのIP |
| `config/sensors/mid360_scan.yaml` | `bringup` | 3D点群から2D LaserScanへの変換 |
| `config/sensors/mid360_elevation.yaml` | `bringup` | 3D点群の仰角フィルタ（勾配のある床を落とす） |
| `config/sensors/mid360_ekf.yaml` | `bringup` | Mid-360 IMUと車輪オドメトリの融合 |

`config/`の分け方と合成順序、それぞれの値の由来は
`src/daifuku_stack/config/README.md`に（3パッケージ分まとめて）あります。

## navigation.launch.py

主な起動引数:

| 引数 | 既定値 | 説明 |
|---|---|---|
| `map` | 空（`overrides`の`site: map:`から導く） | 使用する地図YAMLのパス。**空なら重ねた`overrides`の`site: map:`。** 明示すると`site: map:`と同じファイルを指しているかを見て、違えば起動時にエラー（別の場所の帯と`emcl2`の調整を載せたまま走るのを防ぐ）。**地図が決まらないとき（`overrides:=none`、または`site: map:`の無いoverrides）は明示が要り、既定の地図へは落とさずに止まる。** 既定の場所ごと変えるのは`tools/site.sh` |
| `params_dir` | `config/nav2` | 合成するNav2パラメータ断片のディレクトリ |
| `params_file` | 空（`params_dir`を合成） | Nav2パラメータを1ファイルで与える。指定すると`params_dir`は無視 |
| `overrides` | `config/site`の1行（既定`map_19f`） | `daifuku_config_manager`の`overrides/<名前>.yaml`を重ねる。カンマ区切りで複数可。**置き換え**なので`map_tsudanuma`にすると19F用の調整は外れる。何も重ねないなら`overrides:=none`。行き先は**パッケージ名とノード名**で決まる（下）。**機体側（LiDARの帯）はraspicatサービスが起動時に読むので、切り替えは`tools/site.sh`で**（下） |
| `extra_params_file` | 空（無効） | `overrides`の後に重ねる任意パスのファイル。カンマ区切りで複数可 |
| `emcl2_params_file` | `config/localization/emcl2.yaml` | EMCL2パラメータ |
| `bond_params_file` | `config/lifecycle_bond.yaml` | ライフサイクルマネージャのbondタイムアウト |
| `localization` | `emcl2` | `emcl2` / `emcl` / `amcl` |
| `planner` | `vi` | `vi` / `navfn` |
| `local_planner` | `auto` | `auto` / `vi` / `nav2` |
| `nav2` | `false` | Nav2のnavigationノードを立てるか。`false`（既定）では`vi_planner`が`navigate_to_pose`と`follow_waypoints`も出すので、`bt_navigator`・`behavior_server`・`waypoint_follower`・`smoother_server`が立たない。`planner:=navfn`や`local_planner:=nav2`へ落とすときは`auto`か`true`が要る（**付け忘れると起動時にエラーで止まる**） |
| `velocity_smoother` | `true` | `nav2:=false`のときだけ効く。`velocity_smoother`を`vi_planner`と`twist_mux`の間に残すか。`false`にするとnavigation側のlifecycleノードが1つも無くなる |
| `use_rviz` | `false` | RVizを起動するか。実機はheadlessのため既定は`false`で、表示はPC側から開く |
| `use_system_monitor` | `true` | CPUを`/diagnostics`へ出す`system_monitor`を起動するか。1Hzで`/proc`を読むだけだがDDS参加者が1つ増えるので、ディスカバリの切り分け時は`false`にする |
| `use_sim_time` | `false` | シミュレーション時刻を使うか |
| `use_composition` | `False` | Nav2ノードを1プロセスへ合成するか（Pi 4では既定の分離を推奨） |

このほかに`namespace`、`use_namespace`、`autostart`、`use_respawn`、`log_level`、
`rviz_config`と、EMCL2のノードを差し替える`emcl2_package` / `emcl2_executable` /
`emcl2_node_name`があります。**LiDARの引数は1つもありません**（センサーを立てるのは
`robot_bringup.launch.py`だけです）。全件は次のコマンドで確認できます。

```bash
ros2 launch daifuku_stack navigation.launch.py --show-args
```

## mapping.launch.py

引数は`slam_params_file`、`rviz_config`、`use_sim_time`、`use_rviz`、`namespace`、
`overrides`、`extra_params_file`だけです。**LiDARの引数はありません**（センサーは
`robot_bringup.launch.py`が立てます）。そのため、**新しい場所で地図を作るときは
`tools/site.sh <名前>`で切り替えてから**SLAMを始めてください。
この launch へ`overrides:=`を渡しても効くのは`daifuku_stack:`の部分木だけです。

```bash
ros2 launch daifuku_stack mapping.launch.py --show-args
```

## robot_bringup.launch.py

**機体を丸ごと起動します**——本体ドライバ、URDF、`twist_mux`、ゲームパッド、
**LiDAR**（`lidar_bringup.launch.py`）、**EKF**（`odom_fusion.launch.py`）。
`docker/raspberrypi/`環境では`raspicat`サービスがこれを立てるため、通常は直接
叩きません。**値を変えたら`docker compose up -d`が要ります。**

`navigation.launch.py`と`mapping.launch.py`はセンサーの引数を1つも持ちません。

| 引数 | 既定値 | 説明 |
|---|---|---|
| `driver` | `raspimouse` | 本体ドライバ。`raspimouse`（公式実装。rtmouseが要る。Pi 4のみ）または`original`（自前実装。Pi 4 / Pi 5） |
| `model` | 空（`raspicat_driver.yaml`の`model`に従う。既定は`auto`） | `driver:=original`のときの機種。`pi4` / `pi5` / `auto`。`driver:=raspimouse`に渡すとエラーになる |
| `params_file` | 空（`driver:=`に応じて`config/robot/`から選ぶ） | ドライバのパラメータファイル |
| `twist_mux` | `true` | 速度指令の仲裁を挟むか。`true`だと**ドライバが購読するのは`/cmd_vel`ではなく`/cmd_vel_mux`**になり、人が出す指令は`/cmd_vel_teleop`（優先度100）へ。`false`で全員が`/cmd_vel`へ書く従来の配線に戻る |
| `twist_mux_params_file` | 空（`config/robot/twist_mux.yaml`） | `twist_mux`のパラメータファイル |
| `joy` | `true` | ゲームパッドでの手動走行を立てるか。`joy_node`と`joy_teleop`が上がり、STARTの2秒長押しでteleopの入/切、BACK単体の2秒長押しでモータ電源の入/切、START+BACK同時2秒でウェイポイント巡回を始める（[ゲームパッドで操作する](joystick.md)）。挿していなくても他のノードは動く |
| `joy_teleop_params_file` | 空（`config/robot/joy_teleop.yaml`） | ゲームパッドのパラメータファイル。`joy_node`と`joy_teleop`の両方に渡る |
| `urdf_lidar_frame` | `lidar_link` | URDFへ渡す2D LiDARのリンク名。**`lidar_bringup`の`lidar_frame`（Mid-360の`livox_frame`）とは別物** |
| `use_joint_state_publisher` | `True` | `joint_state_publisher`を起動するか |
| `lidar` | `mid360` | `mid360` / `2d`（`2d`ではraspicatのURG（`urg_node`）を起動する） |
| `scan_filter_enabled` | `true` | 角度フィルタを使うか |
| `elevation_filter` | `true` | 点群を仰角で切るか（`lidar:=mid360`のときだけ効く）。既定の設定`config/sensors/mid360_elevation.yaml`は0〜90度＝搭載高の水平面から上で、19Fの断片の`min_height: 0.275`と同じ切り方のため既定では挙動が変わらない。実際に狭めるのは`overrides/`の側（`map_tsudanuma`が5度）。**`pointcloud_to_laserscan`の`max_height`と組で決まる**（[LiDAR](../setup/lidar.md#仰角フィルタ勾配のある場所向け)） |
| `use_mid360_imu` | `true`（環境変数`USE_MID360_IMU`） | Mid-360のIMU融合を使うか。`true`ではEKFが`/wheel/odom`と`/imu/mid360`を融合して`/odom`と`odom -> base_footprint`を出し、ドライバは車輪の生値を`/wheel/odom`へ出すだけになる。**この2つは同じlaunchが立てるので片方だけ切り替わる状態は作れない。** 切り替えは`.env`の`USE_MID360_IMU`で。**`lidar:=2d`とは併用できない**（URGにIMUが無いので起動時にエラーで止まる）（[LiDARとオドメトリ](../setup/lidar.md#imuと車輪オドメトリ)） |
| `publish_lidar_tf` | `true` | センサーTFを配信するか。配信されるのは`lidar:=mid360`のときだけ（URDFは`lidar_link`しか出さず、`livox_frame`は誰も出さない） |
| `lidar_driver` | `true` | LiDARの実機ドライバ（`mid360`: livox_ros_driver2 + restamp / `2d`: `urg_node`）を起動するか。シミュレータでは`false` |
| `urg_interface` | `serial` | `lidar:=2d`のときのURGの接続方式（`serial` / `ethernet`）。`raspicat_bringup`の`config/urg_<方式>.param.yaml`を選ぶ |
| `urg_params_file` | 空（上記から決める） | URGのパラメータファイルを直接指定する |
| `wheel_odom_topic` | `/wheel/odom` | EKFへ渡す車輪オドメトリ |

どちらのドライバも、相対名`cmd_vel`を購読し`/odom`と`odom -> base_footprint` TFを配信する
lifecycleノードという同じ契約です（`twist_mux:=true`なら購読先が`/cmd_vel_mux`へ
remapされます）。切り替えの前提と確認手順は
[Raspberry Pi 4](../setup/raspberry-pi-4.md)と[Raspberry Pi 5](../setup/raspberry-pi-5.md)を
参照してください。

## Raspberry Pi 4向けの調整値

実機での負荷試験を受けて、次の値を調整しています。いずれもPi 4の制約に合わせたものなので、
PCなど余裕のある環境では`nav2_bringup`が配る値へ戻して構いません。

| 項目 | 値 | 理由 |
|---|---|---|
| `controller_server.controller_frequency` | `10.0` | 20 HzではPi 4のCPUが飽和し、bond心拍の途絶を招いた。最高速0.2 m/s級の車体には10 Hzで十分 |
| `planner_server.expected_planner_frequency` | `1.0` | `nav2_bringup`が配る`nav2_params.yaml`の20.0に対し、実測は7.6 Hzしか出ずWARNが続いた。ノード既定の1.0へ戻した |
| `lifecycle_manager_*.bond_timeout` | `60.0` | 非合成起動では8プロセスが同時に立ち上がりloadが10〜19まで上がるため、既定の4秒ではbond形成が間に合わない |
| `use_composition` | `False` | 合成起動時のディスカバリ不能とbond途絶を回避する |

この表のうち`controller_server`と`planner_server`、`lifecycle_manager_navigation`は
**`nav2:=true`（または`nav2:=auto` + `planner:=navfn`）のときだけ起動します**。既定の
`nav2:=false`ではどれも立たないので、値を変えても何も起きません
（`planner_server`はさらに`planner:=navfn`が要ります）。

## 自己位置推定の暫定設定

EMCL2のリセット関連は、19Fの地図に合わせた**暫定値**です。地図固有の値なので
`config/localization/emcl2.yaml`ではなく`daifuku_config_manager`の`overrides/map_19f.yaml`にあります。

| パラメータ | `overrides/map_19f.yaml` | 断片側 | EMCL2の既定 |
|---|---|---|---|
| `alpha_threshold` | `0.2` | `0.5` | `0.5` |
| `expansion_radius_orientation` | `0.05` | `0.2` | `0.2` |
| `sensor_reset` | `false` | `true` | `false` |

`sensor_reset`だけは断片側もEMCL2の既定と違います（上流READMEの表が`true`と
書いているのに、コードの`declare_parameter`は`false`。経緯は
`src/daifuku_stack/config/README.md`の「値の由来」）。

有効ビームの28%が地図上の壁を貫通しており、非貫通率（alpha）が0.0〜0.4に張り付く
状態でした。閾値0.5のままでは膨張リセットとセンサーリセットが毎スキャン発動し、
推定姿勢がその場で回転してしまいます。根本原因は地図と実環境の不整合にあります。
地図を取り直したあとは、この3つを`overrides/map_19f.yaml`から削除してください。

EMCL2はNav2のノードではないため、合成後の`params_file`ではなく
`emcl2_params_file`がノードへ直接渡ります。それでも`overrides`が効くのは、
上書きの行き先が**ノード名だけで決まる**ためです（次節）。

## 上書き（overrides）の行き先

`daifuku_config_manager`の`overrides/<名前>.yaml`に書いた節は、**同じノード名を宣言
している設定ファイル**の上に重なります。**1段目がパッケージ名で、各launchは自分の
名前の部分木しか読みません。** 2段目がノード名です。Nav2もEMCL2もSLAM Toolboxも
LiDARも機体ドライバも、1つのファイルにまとめて書けます（**1地図 = 1ファイル**）。

```yaml
daifuku_bringup:            # 機体側。変更後は docker compose up -d
  elevation_filter:         # -> daifuku_bringup の config/sensors/mid360_elevation.yaml
    ros__parameters:
      min_elevation_deg: 5.0

daifuku_stack:              # 自律移動側
  vi_global_planner:        # -> config/nav2/vi_planner.yaml（params_fileの合成結果）
    ros__parameters:
      safety_radius_penalty: 1

  emcl2:                    # -> config/localization/emcl2.yaml
    ros__parameters:
      alpha_threshold: 0.2

  slam_toolbox:             # -> config/mapping/slam_toolbox.yaml
    ros__parameters:
      resolution: 0.03
```

`overrides`と`extra_params_file`はすべてのlaunchが同じ既定（`config/site`の1行）で
受けます。同じパッケージの中で、その launch が読まない設定ファイル宛の節は何も
起こしません（`mapping.launch.py`に`emcl2:`を渡しても害はない）。**パッケージが違う
節はそもそも読まれません。** 上書きできないのは`config/sensors/MID360_config.json`
だけで、これはROSのパラメータファイルではないためです。

間違えると起動時にエラーで止まります。**パッケージ名**が`KNOWN_PACKAGES`に無いとき
（どのlaunchからも読まれない部分木になるため）と、**ノード名**がそのパッケージの
どの設定ファイルにも無いときの2通りです。
何がどこへ重なったかは起動ログの`params:`の行に出ます。

```
[INFO] [launch.user]: params: emcl2_params_file: .../emcl2.yaml -> /tmp/emcl2_params_file_xxxx.yaml (+ overrides:map_19f -> emcl2)
```

書きかたと優先順位の詳細は`src/daifuku_stack/config/README.md`と
`src/daifuku_config_manager/src/daifuku_config_manager/params.py`の冒頭にあります。

## 機体固有の調整

実機に合わせて、少なくとも次を確認してください。

- footprintとinflation radius
- 最大並進・旋回速度、加速度
- DWBまたは価値反復ローカルプランナの制御パラメータ
- EMCL2 / AMCLのオドメトリノイズモデル
- LiDAR搭載位置と除外角度
- ゴール許容誤差

ソルバ、スレッド数、キャッシュ許容差、経路補間間隔、制御周期、局所反復時間などは`config/nav2/vi_planner.yaml`で設定します。同ファイルには`vi_planner`（既定の統合ノード）と`vi_global_planner`（`local_planner:=nav2`用の広域専用ノード）の2セクションがあり、同時に起動されることはありません。
