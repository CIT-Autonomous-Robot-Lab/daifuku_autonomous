# 設定リファレンス

## 主なファイル

| ファイル | 内容 |
|---|---|
| `config/nav2/*.yaml` | Nav2、価値反復プランナ、コストマップ、速度、ゴール判定（起動時に1つへ合成） |
| `config/overrides/*.yaml` | 地図・環境ごとの上書き（`overrides:=`で重ねる）。行き先はノード名で決まるので、この表の`MID360_config.json`以外すべてを上書きできる |
| `behavior_trees/*.xml` | `planner:=vi`用のビヘイビアツリー（起動時に自動で選択）。**`nav2:=true`のときだけ読まれる**（既定では`bt_navigator`が立たない） |
| `config/localization/emcl2.yaml` | EMCL2のフレーム、初期姿勢、粒子数、オドメトリモデル |
| `config/robot/raspicat.yaml` | 公式実装（`driver:=raspimouse`）のパラメータ。車輪径、トレッド、オドメトリ源 |
| `config/robot/raspicat_driver.yaml` | 自前実装（`driver:=original`）のパラメータ。上に加えてGPIO・PWM・I2Cの配線 |
| `config/robot/twist_mux.yaml` | 速度指令の仲裁（購読トピックと優先度）。`twist_mux:=true`（既定）のときだけ |
| `config/robot/joy_teleop.yaml` | ゲームパッド。`joy_node`と`joy_teleop`の2ノード分が1ファイルに入る。`joy:=true`（既定）のときだけ |
| `config/lifecycle_bond.yaml` | ライフサイクルマネージャのbondタイムアウト |
| `config/mapping/slam_toolbox.yaml` | SLAM Toolboxのmapping設定 |
| `config/sensors/scan_filter.yaml` | LiDARの角度フィルタ |
| `config/sensors/MID360_config.json` | Mid-360とホストのIP |
| `config/sensors/mid360_scan.yaml` | 3D点群から2D LaserScanへの変換 |
| `config/sensors/mid360_ekf.yaml` | Mid-360 IMUと車輪オドメトリの融合 |
| `rviz/mapping.rviz` | 地図作成用RViz |
| `rviz/navigation.rviz` | 自律移動用RViz |
| `maps/*.yaml`, `maps/*.pgm` | 保存済み地図 |

すべて`src/daifuku_stack/`以下にあります。`config/`の分け方と合成順序は`src/daifuku_stack/config/README.md`にまとめてあります。

## navigation.launch.py

主な起動引数:

| 引数 | 既定値 | 説明 |
|---|---|---|
| `map` | パッケージ内`maps/map_19f.yaml` | 使用する地図YAMLのフルパス |
| `params_dir` | `config/nav2` | 合成するNav2パラメータ断片のディレクトリ |
| `params_file` | 空（`params_dir`を合成） | Nav2パラメータを1ファイルで与える。指定すると`params_dir`は無視 |
| `overrides` | `map_19f`（既定の地図に対応） | `config/overrides/<名前>.yaml`を重ねる。カンマ区切りで複数可。**置き換え**なので`overrides:=map_tsudanuma`とすると19F用の調整は外れる。何も重ねないなら`overrides:=none`。行き先はノード名で決まる（下） |
| `extra_params_file` | 空（無効） | `overrides`の後に重ねる任意パスのファイル。カンマ区切りで複数可 |
| `emcl2_params_file` | `config/localization/emcl2.yaml` | EMCL2パラメータ |
| `bond_params_file` | `config/lifecycle_bond.yaml` | ライフサイクルマネージャのbondタイムアウト |
| `localization` | `emcl2` | `emcl2` / `emcl` / `amcl` |
| `planner` | `vi` | `vi` / `navfn` |
| `local_planner` | `auto` | `auto` / `vi` / `nav2` |
| `nav2` | `false` | Nav2のnavigationノードを立てるか。`false`（既定）では`vi_planner`が`navigate_to_pose`と`follow_waypoints`も出すので、`bt_navigator`・`behavior_server`・`waypoint_follower`・`smoother_server`が立たない。`planner:=navfn`や`local_planner:=nav2`へ落とすときは`auto`か`true`が要る（**付け忘れると起動時にエラーで止まる**） |
| `velocity_smoother` | `true` | `nav2:=false`のときだけ効く。`velocity_smoother`を`vi_planner`と`twist_mux`の間に残すか。`false`にするとnavigation側のlifecycleノードが1つも無くなる |
| `lidar` | `mid360` | `mid360` / `2d`（`2d`ではraspicatのURG（`urg_node`）を起動する） |
| `use_rviz` | `false` | RVizを起動するか。実機はheadlessのため既定は`false`で、表示はPC側から開く |
| `use_system_monitor` | `true` | CPUを`/diagnostics`へ出す`system_monitor`を起動するか。1Hzで`/proc`を読むだけだがDDS参加者が1つ増えるので、ディスカバリの切り分け時は`false`にする |
| `use_sim_time` | `false` | シミュレーション時刻を使うか |
| `use_composition` | `False` | Nav2ノードを1プロセスへ合成するか（Pi 4では既定の分離を推奨） |
| `scan_filter_enabled` | `true` | 角度フィルタを使うか |
| `use_mid360_imu` | `true` | Mid-360のIMU融合を使うか。**この機体では`false`を明示する。** 既定の`true`はEKFが`/wheel/odom`を受け取る構成向けで、同梱の本体ドライバはどちらも`/odom`と`odom -> base_footprint`を自分で出すため、そのままだとEKFが入力を得られず配信元も二重になる |
| `publish_lidar_tf` | `true` | センサーTFを配信するか。配信されるのは`lidar:=mid360`のときだけ（URDFは`lidar_link`しか出さず、`livox_frame`は誰も出さない） |
| `lidar_driver` | `true` | LiDARの実機ドライバ（`mid360`: livox_ros_driver2 + restamp / `2d`: `urg_node`）を起動するか。シミュレータでは`false` |
| `urg_interface` | `serial` | `lidar:=2d`のときのURGの接続方式（`serial` / `ethernet`）。`raspicat_bringup`の`config/urg_<方式>.param.yaml`を選ぶ |
| `urg_params_file` | 空（上記から決める） | URGのパラメータファイルを直接指定する |
| `wheel_odom_topic` | `/wheel/odom` | EKFへ渡す車輪オドメトリ |

このほかに`namespace`、`use_namespace`、`autostart`、`use_respawn`、`log_level`と、LiDARの搭載姿勢を指定する各種引数があります。全件は次のコマンドで確認できます。

```bash
ros2 launch daifuku_stack navigation.launch.py --show-args
```

## mapping.launch.py

主な引数は`slam_params_file`、`rviz_config`、`use_sim_time`、`use_rviz`、`lidar`と、スキャンフィルタおよびMid-360関連の設定です。

```bash
ros2 launch daifuku_stack mapping.launch.py --show-args
```

## robot_bringup.launch.py

本体ドライバとURDFを起動します。`docker/raspberrypi/`環境では`raspicat`サービスがこれを
立てるため、通常は直接叩きません。

| 引数 | 既定値 | 説明 |
|---|---|---|
| `driver` | `raspimouse` | 本体ドライバ。`raspimouse`（公式実装。rtmouseが要る。Pi 4のみ）または`original`（自前実装。Pi 4 / Pi 5） |
| `model` | 空（`raspicat_driver.yaml`の`model`に従う。既定は`auto`） | `driver:=original`のときの機種。`pi4` / `pi5` / `auto`。`driver:=raspimouse`に渡すとエラーになる |
| `params_file` | 空（`driver:=`に応じて`config/robot/`から選ぶ） | ドライバのパラメータファイル |
| `twist_mux` | `true` | 速度指令の仲裁を挟むか。`true`だと**ドライバが購読するのは`/cmd_vel`ではなく`/cmd_vel_mux`**になり、人が出す指令は`/cmd_vel_teleop`（優先度100）へ。`false`で全員が`/cmd_vel`へ書く従来の配線に戻る |
| `twist_mux_params_file` | 空（`config/robot/twist_mux.yaml`） | `twist_mux`のパラメータファイル |
| `joy` | `true` | ゲームパッドでの手動走行を立てるか。`joy_node`と`joy_teleop`が上がり、STARTの2秒長押しでteleopの入/切、BACK単体の2秒長押しでモータ電源の入/切、START+BACK同時2秒でウェイポイント巡回を始める（[ゲームパッドで操作する](joystick.md)）。挿していなくても他のノードは動く |
| `joy_teleop_params_file` | 空（`config/robot/joy_teleop.yaml`） | ゲームパッドのパラメータファイル。`joy_node`と`joy_teleop`の両方に渡る |
| `lidar_frame` | `lidar_link` | URDFへ渡すLiDARのフレーム名 |
| `use_joint_state_publisher` | `True` | `joint_state_publisher`を起動するか |

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
`config/localization/emcl2.yaml`ではなく`config/overrides/map_19f.yaml`にあります。

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

`config/overrides/<名前>.yaml`に書いた節は、**同じノード名を宣言している設定ファイル**の
上に重なります。行き先を決めるのはノード名だけなので、Nav2のパラメータもEMCL2も
SLAM Toolboxも機体ドライバも、1つのファイルにまとめて書けます。

```yaml
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

`overrides`と`extra_params_file`は`navigation` / `mapping` / `lidar_bringup` /
`robot_bringup`の4つが同じ既定（`map_19f`）で受けます。その launch が読まない
設定ファイル宛の節は、何も起こしません（`mapping.launch.py`に`emcl2:`を渡しても
害はない）。上書きできないのは`config/sensors/MID360_config.json`だけで、これは
ROSのパラメータファイルではないためです。

ノード名を間違えると、どの設定ファイルにも行き先が無いので起動時にエラーで止まります。
何がどこへ重なったかは起動ログの`params:`の行に出ます。

```
[INFO] [launch.user]: params: emcl2_params_file: .../emcl2.yaml -> /tmp/emcl2_params_file_xxxx.yaml (+ overrides:map_19f -> emcl2)
```

書きかたと優先順位の詳細は`src/daifuku_stack/config/README.md`にあります。

## 機体固有の調整

実機に合わせて、少なくとも次を確認してください。

- footprintとinflation radius
- 最大並進・旋回速度、加速度
- DWBまたは価値反復ローカルプランナの制御パラメータ
- EMCL2 / AMCLのオドメトリノイズモデル
- LiDAR搭載位置と除外角度
- ゴール許容誤差

ソルバ、スレッド数、キャッシュ許容差、経路補間間隔、制御周期、局所反復時間などは`config/nav2/vi_planner.yaml`で設定します。同ファイルには`vi_planner`（既定の統合ノード）と`vi_global_planner`（`local_planner:=nav2`用の広域専用ノード）の2セクションがあり、同時に起動されることはありません。
