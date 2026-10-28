# 設定リファレンス

## 主なファイル

**設定の実体は`src/daifuku_config/`（パッケージ名は`daifuku_config`）に
全部あります。** 1段目のディレクトリ（`robot/`・`perception/`・`localization/`・
`mapping/`・`navigation/`）は、その値を読む機能パッケージで分けたものです。
場所ごとの値は`sites/<場所>/`に置きます。下の表の「パッケージ」列は、その値を読む
機能パッケージのことで、ファイルの置き場ではありません。

変更の反映手順は[日常操作](operations.md#設定変更を反映する)にまとめています。
機体側（`robot/`）の値をnavigationの再起動だけで反映することはできません。

| ファイル | パッケージ | 内容 |
|---|---|---|
| `src/daifuku_config/site` | なし（任意） | **次に立てる場所**の名前を1語だけ持つ任意の記録。`tools/site.sh <名前>`が書き、対応する起動コマンドを表示する。公開のnavigation launchは場所が名前で固定なので、このファイルでは変わらない |
| `src/daifuku_config/sites/<場所>/overrides.yaml` | 場所ごと | 地図・環境ごとの上書き。行き先は**パッケージ名とノード名**で決まるので、この表の`MID360_config.json`以外すべてを上書きできる |
| `src/daifuku_config/sites/tsukuba2025/{navigation_scan,localization_scan}.yaml`、`sites/tsukuba2026/`の同名ファイル | `daifuku_perception` | つくばの計画用・推定用それぞれの`/scan`の作りかた |
| `src/daifuku_config/navigation/nav2/*.yaml`、`src/daifuku_config/navigation/vi_planner.yaml` | `daifuku_navigation` | 地図サーバ、AMCL、速度平滑化、価値反復プランナ（起動時に1つへ合成） |
| `src/daifuku_config/localization/emcl2.yaml` | `daifuku_localization` | EMCL2のフレーム、初期姿勢、粒子数、オドメトリモデル |
| `src/daifuku_config/localization/emcl3d.yaml` | `daifuku_localization` | EMCL3D（`localization:=emcl3d`）のフレーム、入力点群、初期姿勢、粒子数 |
| `src/daifuku_config/navigation/lifecycle_bond.yaml` | `daifuku_navigation` | ライフサイクルマネージャのbondタイムアウト |
| `src/daifuku_config/mapping/slam_toolbox.yaml` | `daifuku_mapping` | SLAM Toolboxのmapping設定 |
| `src/daifuku_bringup/rviz/mapping.rviz` | `daifuku_bringup` | 地図作成用RViz |
| `src/daifuku_bringup/rviz/navigation.rviz` | `daifuku_bringup` | 自律移動用RViz |
| `src/daifuku_maps/maps/`、`src/daifuku_maps/waypoints/` | `daifuku_maps` | 保存済み地図と巡回の順路 |
| `src/daifuku_config/robot/robot/raspicat.yaml` | `daifuku_robot` | 公式実装（`driver:=raspimouse`）のパラメータ。車輪径、トレッド、オドメトリ源 |
| `src/daifuku_config/robot/robot/raspicat_driver.yaml` | `daifuku_robot` | 自前実装（`driver:=original`）のパラメータ。上に加えてGPIO・PWM・I2Cの配線 |
| `src/daifuku_config/robot/robot/twist_mux.yaml` | `daifuku_robot` | 速度指令の仲裁（購読トピックと優先度）。`twist_mux:=true`（既定）のときだけ |
| `src/daifuku_config/robot/robot/joy_teleop.yaml` | `daifuku_robot` | ゲームパッド。`joy_node`と`joy_teleop`の2ノード分が1ファイルに入る。`joy:=true`（既定）のときだけ |
| `src/daifuku_config/robot/sensors/MID360_config.json` | `daifuku_robot` | Mid-360とホストのIP |
| `src/daifuku_config/robot/sensors/mid360_ekf.yaml` | `daifuku_robot` | Mid-360 IMUと車輪オドメトリの融合 |
| `src/daifuku_config/perception/scan_filter.yaml` | `daifuku_perception` | LiDARの角度フィルタ |
| `src/daifuku_config/perception/mid360_scan.yaml` | `daifuku_perception` | 3D点群から2D LaserScanへの変換 |
| `src/daifuku_config/perception/mid360_elevation.yaml` | `daifuku_perception` | 3D点群の仰角フィルタ（勾配のある床を落とす） |

`src/daifuku_config/`の分け方と合成順序、それぞれの値の由来は
`src/daifuku_config/README.md`に（全パッケージ分まとめて）あります。

## navigationの公開launch

場所ごとに1本ずつあり、場所はファイル名で固定です。

```bash
ros2 launch daifuku_bringup navigation.19f.launch.py
ros2 launch daifuku_bringup navigation.tsudanuma.launch.py
ros2 launch daifuku_bringup navigation.tsudanuma_mugimaru.launch.py
ros2 launch daifuku_bringup navigation.tsukuba2025.launch.py
ros2 launch daifuku_bringup navigation.tsukuba2026.launch.py
```

地図と`sites/<場所>/overrides.yaml`はその場所のものが使われ、`OVERRIDES`環境変数や
`src/daifuku_config/site`では変えられません。`daifuku_navigation`の`navigation.launch.py`は
これらが使う部品で、単体では公開の完全な構成になりません。

主な起動引数:

| 引数 | 既定値 | 説明 |
|---|---|---|
| `map` | 空（`overrides`の`site: map: navigation:`から導く） | **経路計画**に使う地図YAMLのパス。`/map`で配信し`vi_planner`が読む。**空なら重ねた`overrides`の`site: map: navigation:`。** 明示すると同じファイルを指しているかを見て、違えば起動時にエラー（別の場所の帯と`emcl2`の調整を載せたまま走るのを防ぐ）。**地図が決まらないとき（`overrides:=none`、または`site: map:`の無いoverrides）は明示が要り、既定の地図へは落とさずに止まる。** 別の場所を走らせるなら、その場所のlaunchを使う |
| `map_loc` | 空（`overrides`の`site: map: localization:`から導く） | **自己位置推定**に使う地図YAMLのパス。`/map_loc`で配信し`emcl2`が読む。空で宣言も無ければ`map`と同じ地図に落ちる（こちらだけは落とす。1枚で走らせる従来どおりの形なので）。**2枚を別にできるのは`localization:=emcl2`のときだけ**（[自律移動](navigation.md#地図は2枚)） |
| `params_dir` | `src/daifuku_config/navigation/nav2` | 合成するNav2パラメータ断片のディレクトリ |
| `params_file` | 空（`params_dir`を合成） | Nav2パラメータを1ファイルで与える。指定時も`params_dir`の断片を上書き先のノード名の検査に使う |
| `extra_params_file` | 空（無効） | `overrides`の後に重ねる任意パスのファイル。カンマ区切りで複数可 |
| `config_watch` | `shutdown` | 起動後に設定ファイルが書き変わったときどうするか。`shutdown`（既定）は大声で言ったうえで**このlaunchを終了する**（`navigation`は人が立てたものなので、上げ直す人は居ません）。`warn`は言うだけ、`off`は見張り（`config_sentinel`）ごと立てない。見張るのはその場所のnavigation・localization・perceptionの設定と、使用中プロファイルが明示したファイル。[日常操作](operations.md#走らせたまま設定を直したとき) |
| `emcl2_params_file` | `src/daifuku_config/localization/emcl2.yaml` | EMCL2パラメータ |
| `emcl3d_params_file` | `src/daifuku_config/localization/emcl3d.yaml` | EMCL3Dパラメータ |
| `map_pcd` | 空（`overrides`の`site: map: pointcloud:`から導く） | `localization:=emcl3d`が読む点群地図（PCD）のパス。明示すると宣言と同じファイルかを見る。**`emcl3d`で決まらなければ起動時に止まる**。他の`localization`では読まない |
| `bond_params_file` | `src/daifuku_config/navigation/lifecycle_bond.yaml` | ライフサイクルマネージャのbondタイムアウト |
| `localization` | `emcl2` | `emcl2` / `emcl` / `emcl3d` / `amcl` / `vi` |
| `velocity_smoother` | `true` | `velocity_smoother`を`vi_planner`と`twist_mux`の間に残すか。`false`にするとnavigation側のlifecycleノードが1つも無くなる |
| `use_rviz` | `false` | RVizを起動するか。実機はheadlessのため既定は`false`で、表示はPC側から開く |
| `use_system_monitor` | `true` | CPUを`/diagnostics`へ出す`system_monitor`を起動するか。1Hzで`/proc`を読むだけだがDDS参加者が1つ増えるので、ディスカバリの切り分け時は`false`にする |
| `use_sim_time` | `false` | シミュレーション時刻を使うか |
| `use_composition` | `False` | Nav2ノードを1プロセスへ合成するか（Pi 4では既定の分離を推奨） |

このほかに`namespace`、`use_namespace`、`autostart`、`use_respawn`、`log_level`、
`rviz_config`と、EMCL2のノードを差し替える`emcl2_package` / `emcl2_executable` /
`emcl2_node_name`があります。**LiDARのドライバ**を立てるのは`robot.launch.py`だけですが、
`/scan`を作る段の引数はこのlaunchにもあります。全件は次のコマンドで確認できます。

```bash
ros2 launch daifuku_bringup navigation.19f.launch.py --show-args
```

## mapping.launch.py

```bash
ros2 launch daifuku_bringup mapping.launch.py overrides:=<場所>
```

`overrides`の既定は`none`です。場所ごとの調整（LiDARの帯やSLAMの値）を使うときだけ
`overrides:=<場所>`で`sites/<場所>/overrides.yaml`を重ねます。引数は`slam_params_file`、`rviz_config`、`use_sim_time`、`use_rviz`、`namespace`、
`overrides`、`extra_params_file`、`config_watch`に加えて、**`/scan`を作る段の引数**
（`lidar`、`lidar_driver`、`elevation_filter`、`scan_filter_enabled`、各設定ファイル）
です。センサーの**ドライバ**は`robot.launch.py`が立てますが、点群を`/scan`に
変える段（`daifuku_perception`の`scan_pipeline.launch.py`）はこの launch が include します。
だから**`overrides:=<場所>`を渡せばLiDARの帯もその場所のものになります**。`lidar:=`と`lidar_driver:=`は**機体側と揃えること**（既定は
どちらも環境変数`LIDAR` / `LIDAR_DRIVER`から来るので、`.env`に1行書けば揃います）。

`config_watch`の既定は`shutdown`で、**SLAM中に`src/daifuku_config/`の`mapping/`か`perception/`を直すと
このlaunchごと終了します**。上げ直す人は居ないので、そこまで作った地図は消えます
（[地図作成](mapping.md#2-slamを起動する)）。

```bash
ros2 launch daifuku_bringup mapping.launch.py --show-args
```

## robot.launch.py

**機体を丸ごと起動します**——本体ドライバ、URDF、`twist_mux`、ゲームパッド、
**LiDARドライバ**（`lidar_bringup.launch.py`）、**EKF**（`odom_fusion.launch.py`）。
実装は`daifuku_robot`にあり、`daifuku_bringup`の`robot.launch.py`がそれを公開します。
点群を`/scan`に変える段はnavigation / mapping側です。
`docker/raspberrypi/`環境では`raspicat`サービスがこれを立てるため、通常は直接
叩きません。変更の反映は[日常操作](operations.md#設定変更を反映する)を参照してください。

navigationとmappingの公開launchは`/scan`を作る段の引数を持ちます。

| 引数 | 既定値 | 説明 |
|---|---|---|
| `driver` | `raspimouse` | 本体ドライバ。`raspimouse`（公式実装。rtmouseが要る。Pi 4のみ）または`original`（自前実装。Pi 4 / Pi 5） |
| `model` | 空（`raspicat_driver.yaml`の`model`に従う。既定は`auto`） | `driver:=original`のときの機種。`pi4` / `pi5` / `auto`。`driver:=raspimouse`に渡すとエラーになる |
| `params_file` | 空（`driver:=`に応じて`src/daifuku_config/robot/robot/`から選ぶ） | ドライバのパラメータファイル |
| `twist_mux` | `true` | 速度指令の仲裁を挟むか。`true`だと**ドライバが購読するのは`/cmd_vel`ではなく`/cmd_vel_mux`**になり、人が出す指令は`/cmd_vel_teleop`（優先度10）へ。自律側の`/cmd_vel`が優先度100で**上**なので、自律走行中は手動が通らない。`false`で全員が`/cmd_vel`へ書く従来の配線に戻る |
| `twist_mux_params_file` | 空（`src/daifuku_config/robot/robot/twist_mux.yaml`） | `twist_mux`のパラメータファイル |
| `joy` | `true` | ゲームパッドでの手動走行を立てるか。`joy_node`と`joy_teleop`が上がり、STARTの2秒長押しでteleopの入/切、BACK単体の2秒長押しでモータ電源の入/切、START+BACK同時2秒でウェイポイント巡回を始める（[ゲームパッドで操作する](joystick.md)）。挿していなくても他のノードは動く |
| `joy_teleop_params_file` | 空（`src/daifuku_config/robot/robot/joy_teleop.yaml`） | ゲームパッドのパラメータファイル。`joy_node`と`joy_teleop`の両方に渡る |
| `urdf_lidar_frame` | `lidar_link` | URDFへ渡す2D LiDARのリンク名。**`lidar_bringup`の`lidar_frame`（Mid-360の`livox_frame`）とは別物** |
| `use_joint_state_publisher` | `True` | `joint_state_publisher`を起動するか |
| `lidar` | `mid360`（環境変数`LIDAR`） | `mid360` / `2d`（`2d`ではraspicatのURG（`urg_node`）を起動する）。**`navigation` / `mapping` にも同じ引数があり、揃っていないとエラーも警告も出ないまま`/scan`が空になる**。既定を環境変数から取っているのはそのため |
| `scan_filter_enabled` | `true` | **`navigation` / `mapping` 側の引数**。角度フィルタを使うか |
| `restamp_cloud` | `false` | **`navigation` / `mapping` 側の引数**。仰角フィルタ内で点群の時刻を受信時刻に直し、TF投影より前にデバイス時計のずれを除く。`navigation.tsukuba2025.launch.py`は実機入力のときに有効にし、シミュレーションでは無効にする。`elevation_filter:=false`では効かない |
| `elevation_filter` | `true` | **`navigation` / `mapping` 側の引数**（点群を`/scan`に変える段はそちらに居る）。点群を仰角で切るか（`lidar:=mid360`のときだけ効く）。既定の設定`src/daifuku_config/perception/mid360_elevation.yaml`は0〜90度＝搭載高の水平面から上で、断片の`min_height: 0.275`と同じ切り方のため既定では挙動が変わらない。狭めるのは`sites/<場所>/overrides.yaml`の側だが、**同梱の3地図はいまどれも0度＝実質素通し**（`tsudanuma`は2026-08-08に5.0から戻し、`tsudanuma_mugimaru`はそれを写した）。狭めたときだけ**`pointcloud_to_laserscan`の`max_height`と組で決まる**（[LiDAR](../setup/lidar.md#仰角フィルタ勾配のある場所向け)） |
| `use_mid360_imu` | `true`（環境変数`USE_MID360_IMU`） | Mid-360のIMU融合を使うか。`true`ではEKFが`/wheel/odom`と`/imu/mid360`を融合して`/odom`と`odom -> base_footprint`を出し、ドライバは車輪の生値を`/wheel/odom`へ出すだけになる。**この2つは同じlaunchが立てるので片方だけ切り替わる状態は作れない。** 切り替えは`.env`の`USE_MID360_IMU`で。**`lidar:=2d`とは併用できない**（URGにIMUが無いので起動時にエラーで止まる）（[LiDARとオドメトリ](../setup/lidar.md#imuと車輪オドメトリ)） |
| `publish_lidar_tf` | `true` | センサーTFを配信するか。配信されるのは`lidar:=mid360`のときだけ（URDFは`lidar_link`しか出さず、`livox_frame`は誰も出さない） |
| `lidar_driver` | `true`（環境変数`LIDAR_DRIVER`） | LiDARの実機ドライバ（`mid360`: livox_ros_driver2 / `2d`: `urg_node`）を起動するか。シミュレータでは`false`。`lidar`と同じく**両側に渡る**（`false`だと`/scan`を作る段が`restamp_scan`を挟まなくなる） |
| `urg_interface` | `serial` | `lidar:=2d`のときのURGの接続方式（`serial` / `ethernet`）。`raspicat_bringup`の`config/urg_<方式>.param.yaml`を選ぶ |
| `urg_params_file` | 空（上記から決める） | URGのパラメータファイルを直接指定する |
| `wheel_odom_topic` | `/wheel/odom` | EKFへ渡す車輪オドメトリ |
| `config_watch` | `shutdown` | 起動後に`src/daifuku_config/robot/`が書き変わったときどうするか（場所の設定は見ない）。`shutdown`（既定）は**このlaunchを終了する**が、`raspicat`サービスは`restart: unless-stopped`なので新しい設定で上がり直す。`warn`は言うだけ、`off`は見張りごと立てない |

どちらのドライバも、相対名`cmd_vel`を購読し`/odom`と`odom -> base_footprint` TFを配信する
lifecycleノードという同じ契約です（`twist_mux:=true`なら購読先が`/cmd_vel_mux`へ
remapされます）。切り替えの前提と確認手順は
[Raspberry Pi 4](../setup/raspberry-pi-4.md)と[Raspberry Pi 5](../setup/raspberry-pi-5.md)を
参照してください。

## 自己位置推定の暫定設定

EMCL2のリセット関連は、19Fの地図に合わせた**暫定値**です。地図固有の値なので
`src/daifuku_config/localization/emcl2.yaml`ではなく`src/daifuku_config/sites/19f/overrides.yaml`にあります。

| パラメータ | `sites/19f/overrides.yaml` | 断片側 | EMCL2の既定 |
|---|---|---|---|
| `alpha_threshold` | `0.2` | `0.5` | `0.5` |
| `expansion_radius_orientation` | `0.05` | `0.2` | `0.2` |
| `sensor_reset` | `false` | `false` | `false` |

`sensor_reset`は2026-08-09まで断片側だけが`true`でした（上流READMEの表が`true`と
書いているのに、コードの`declare_parameter`は`false`。経緯は
`src/daifuku_config/README.md`の「値の由来」）。いまは3つとも`false`なので、この行は
overridesから消しても挙動は変わりません。

有効ビームの28%が地図上の壁を貫通しており、非貫通率（alpha）が0.0〜0.4に張り付く
状態でした。閾値0.5のままでは膨張リセットとセンサーリセットが毎スキャン発動し、
推定姿勢がその場で回転してしまいます。根本原因は地図と実環境の不整合にあります。
地図を取り直したあとは、この3つを`sites/19f/overrides.yaml`から削除してください。

EMCL2はNav2のノードではないため、合成後の`params_file`ではなく
`emcl2_params_file`がノードへ直接渡ります。それでも`overrides`が効くのは、
上書きの行き先が**ノード名だけで決まる**ためです（次節）。

## 上書き（overrides）の行き先

`src/daifuku_config/sites/<名前>/overrides.yaml`に書いた節は、**同じノード名を宣言
している設定ファイル**の上に重なります。**1段目がパッケージ名で、各launchは自分の
名前の部分木しか読みません。** 2段目がノード名です。Nav2もEMCL2もSLAM Toolboxも
LiDARも機体ドライバも、1つのファイルにまとめて書けます（**1地図 = 1ファイル**）。

```yaml
daifuku_perception:         # /scan を作る段。変更後は navigation / mapping を立て直す
  elevation_filter:         # -> src/daifuku_config/perception/mid360_elevation.yaml
    ros__parameters:
      min_elevation_deg: 5.0

daifuku_navigation:
  vi_planner:               # -> src/daifuku_config/navigation/vi_planner.yaml（params_fileの合成結果）
    ros__parameters:
      safety_radius_penalty: 1

daifuku_localization:
  emcl2:                    # -> src/daifuku_config/localization/emcl2.yaml
    ros__parameters:
      alpha_threshold: 0.2

daifuku_mapping:
  slam_toolbox:             # -> src/daifuku_config/mapping/slam_toolbox.yaml
    ros__parameters:
      resolution: 0.03
```

最上位キーは機能パッケージ名だけで、旧来の`daifuku_stack:`の節はありません。
機体の値は`daifuku_robot:`の下に書きます。同じパッケージの中で、その launch が読まない設定ファイル宛の節は何も
起こしません（mapping構成は`daifuku_localization:`の節を読みません）。**パッケージが違う
節はそもそも読まれません。** 上書きできないのは`src/daifuku_config/robot/sensors/MID360_config.json`
だけで、これはROSのパラメータファイルではないためです。

間違えると起動時にエラーで止まります。**パッケージ名**が`KNOWN_PACKAGES`に無いとき
（どのlaunchからも読まれない部分木になるため）と、**ノード名**がそのパッケージの
どの設定ファイルにも無いときの2通りです。
何がどこへ重なったかは起動ログの`params:`の行に出ます。

```
[INFO] [launch.user]: params: emcl2_params_file: .../emcl2.yaml -> /tmp/emcl2_params_file_xxxx.yaml (+ overrides:19f -> emcl2)
```

書きかたと優先順位の詳細は`src/daifuku_config/README.md`と
`src/daifuku_config/daifuku_config/params.py`の冒頭にあります。

## 機体固有の調整

実機に合わせて、少なくとも次を確認してください。

- 車体寸法と障害物回避の余裕
- 最大並進・旋回速度、加速度
- 価値反復プランナの制御パラメータ
- EMCL2 / AMCLのオドメトリノイズモデル
- LiDAR搭載位置と除外角度
- ゴール許容誤差

ソルバ、スレッド数、キャッシュ許容差、経路補間間隔、制御周期、局所反復時間などは`src/daifuku_config/navigation/vi_planner.yaml`で設定します。同ファイルの節は`vi_planner`の1つだけです。
