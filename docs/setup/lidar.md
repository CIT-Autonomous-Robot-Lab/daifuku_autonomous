# LiDARとオドメトリ

**LiDARとEKFを立てるのは`daifuku_bringup`の`robot_bringup.launch.py`です。**
`docker compose up`で常駐しているので、`navigation.launch.py`と`mapping.launch.py`は
`/scan`と`/odom`の消費者に徹し、センサーの引数を1つも持ちません。手元で単独に
立てるときは先に次を通してください。

```bash
ros2 launch daifuku_bringup robot_bringup.launch.py
```

内訳は`lidar_bringup.launch.py`（LiDAR一式）と`odom_fusion.launch.py`（車輪＋IMUの
EKF）の2つで、どちらも単独でも立てられます（`simulator/`はそうしています）。

`lidar:=2d|mid360`でセンサー構成を切り替えます。既定は本機の構成に合わせて`mid360`
です。どちらも入力を`/scan_raw`へ集約し、角度フィルタ後の`/scan`をSLAMとNav2へ
渡します。

既定の`config/bringup/sensors/scan_filter.yaml`は、コネクタがある後方50度
（+155度から-155度まで、±180度をまたぐ範囲）を除外します。

センサーごとのトピックの流れは次のとおりです。

```text
2D LiDAR      : urg_node → /scan_raw → 角度フィルタ → /scan
Mid-360       : /livox/lidar → elevation_filter.py（仰角フィルタ）
                → /livox/lidar_elevation → pointcloud_to_laserscan
                → /scan_mid360_prestamp → restamp_scan.py
                → /scan_raw → 角度フィルタ → /scan
IMU（Mid-360）: /livox/imu → prepare_mid360_imu.py → /imu/mid360 ┐
                                                                  ├ ekf_node
                車輪ドライバ → /wheel/odom ──────────────────────┘
                → /odom、odom → base_footprint
```

**LiDARの帯（仰角と高さ）を場所ごとに変えるには`tools/site.sh <名前>`を使います。**
読むのは常駐しているraspicatサービスなので、`navigation`や`mapping`を立て直すだけでは
変わりません（スクリプトは`raspicat`の立て直しまでやります）。

`elevation_filter:=false`にすると仰角フィルタは立たず、`/livox/lidar`が
`pointcloud_to_laserscan`へ直接入ります（relayは挟みません）。

### 仰角フィルタ（勾配のある場所向け）

`pointcloud_to_laserscan`の`min_height`は接地面からの**固定の高さ**で切るため、
勾配のある床を落とせません。相対傾斜αの床は`min_height / tan α`の先で必ず帯に入り、
地図にない壁として立ちます。下限を上げれば遠ざけられますが、その高さ未満の障害物が
costmapから消え、近くはセンサーの垂直FOVから外れて死角になります。

`elevation_filter.py`はセンサー原点からの**仰角**で切ります。切り出しの下限が距離に
比例する（`base_footprint`で見ると`lidar_z + 距離 × tan(min_elevation_deg)`）ので、
床も天井も遠方では仰角0度へ収束することを使い、`min_elevation_deg`より緩い勾配を
**どの距離でも入らない**ようにします。近くほど下限が低いので、低い障害物もcostmapに
残ります。

代償は、見える最大距離が対象の高さで決まることです。

```text
最大距離 = (対象の高さ − lidar_z) / tan(min_elevation_deg)
```

`min_elevation_deg: 5.0`・`lidar_z: 0.275`なら、高さ3mの壁は31m、5mの壁は54mまでです。
`min_elevation_deg`より急な勾配には効きません（高さで切るより早く入ってきます）ので、
実機で偽の壁が出る距離dを測り、`α = atan(1.0/d)`より上に設定してください。

**角度を0度より上げたときは、`pointcloud_to_laserscan`の`max_height`と`range_max`が
それと組になります。** 実効下限は距離とともに上がるので、`max_height`をそれより下に
置くと帯が潰れ、`range_max`を伸ばしても**エラーも警告も出ないまま**その手前で何も
入らなくなります（5度なら70m先の実効下限は6.40m）。

**同梱の2地図はいまどちらも0度なので、この組は効いていません**（`map_tsudanuma`は
2026-08-08に5.0から戻しました）。0度は搭載高の水平面そのもの＝断片の
`min_height: 0.275`と同じ切り方なので、仰角フィルタは実質素通しで、帯は全距離で
`min_height`〜`max_height`のままです。地図ごとの値と経緯は
`config/overrides/map_tsudanuma.yaml`にあります。

`range_max`の既定はセンサの測距上限に合わせた70m（反射率80%で70m、10%では40m）です。
ただし実際に70mを使うのは`emcl2`だけで、costmapは`obstacle_max_range: 2.5`、
SLAMは`max_laser_range: 10.0`で頭打ちになります。

## 2D LiDAR

`lidar:=2d`を指定すると、raspicatのURG（`urg_node`の`urg_node_driver`）が起動します。
パラメータは`raspicat_bringup`の`config/urg_<urg_interface>.param.yaml`（既定は
`urg_serial.param.yaml`。`/dev/ttyACM0`、`laser_frame_id: lidar_link`）で、出力は
`/scan_raw`へremapされます。

```bash
ros2 launch daifuku_bringup robot_bringup.launch.py lidar:=2d
```

Ethernet接続のURGでは`urg_interface:=ethernet`を指定します。別のパラメータファイルを
使う場合は`urg_params_file:=/path/to/urg.param.yaml`を渡します。

`docker/raspberrypi/`環境でLiDARを立てるのは`raspicat`サービスです。このサービスは
モータ制御の都合で`/dev`を丸ごとマウントし`device_cgroup_rules: c *:* rwm`を持って
いるので、**シリアル接続のURGでも追加のデバイス設定は要りません**。見えているかは
`docker compose exec raspicat ls -l /dev/ttyACM0`で確認できます。

イメージには`ros-humble-urg-node`が要ります。入っていない場合は
`docker compose build`からやり直してください。

別の2D LiDARを使う場合は`lidar_driver:=false`でドライバの起動を止め、そのLiDARの出力を
`/scan_raw`へremapして自分で起動してください。

```bash
ros2 run <2d_lidar_package> <2d_lidar_node> \
  --ros-args -r scan:=/scan_raw
```

機体側の車輪ノードは次を配信します。

- `/odom`
- `odom -> base_footprint` TF
- `base_footprint -> LiDARフレーム` TF（通常はURDFから配信）

## Mid-360

### IPアドレス

`config/bringup/sensors/MID360_config.json`を実ネットワークに合わせます。

- `host_net_info`内の4個の`*_data_ip`: ドライバを動かすPCの固定IP
- `lidar_configs[0].ip`: Mid-360本体のIP

### タイムスタンプの打ち直し

本機のMid-360はPTP/gPTPで時刻同期していないため、`livox_ros_driver2`は
デバイス内蔵時計の時刻をスタンプに使います。この時計はPiのシステム時計から
毎分数秒ずつずれていきます。そのままにすると、EMCL2の`map -> odom` TFや
Nav2コストマップのメッセージフィルタが、起動から数分でデータを「古すぎる」
「未来の時刻」として破棄します。

対策として、`lidar:=mid360`で実機ドライバを立てるときだけ`daifuku_bringup`の
`src/restamp_scan.py`が
`/scan_mid360_prestamp`を購読し、受信時刻でスタンプを打ち直して`/scan_raw`へ再配信
します。こうするとスキャンのスタンプが、車輪オドメトリ・TF・Nav2と同じ時計に
そろいます。ドリフトのないシミュレータやバッグ再生（`lidar_driver:=false`）では中継を
挟まず、`pointcloud_to_laserscan`が`/scan_raw`へ直接出します。センサー側をPTP同期
できるようになれば、この中継は不要になります。

中継は`prepare_mid360_imu.py`・`joy_teleop.py`と同じく通常の
`Node`として起動します（`lib/daifuku_bringup/restamp_scan.py`。トピックは相対名の
`scan_in` / `scan_out`で、launch側が remap します）。実行ファイルが無ければlaunchごと
エラーで止まるので、古い`install/`が残っていても黙って`/scan_raw`だけが欠けることは
ありません。切り分けは
[トラブルシューティング](../usage/troubleshooting.md#mid-360のスキャンが古すぎると拒否される)を
参照してください。

### センサーTF

`base_footprint -> livox_frame`はURDFから配信することを推奨します。URDFから配信していない場合は、実測した搭載位置と姿勢をlaunch引数で渡し、暫定的に配信できます。位置はメートル、姿勢はラジアンです。

```bash
publish_lidar_tf:=true \
lidar_x:=0.0 lidar_y:=0.0 lidar_z:=0.275 \
lidar_roll:=0.0 lidar_pitch:=0.0 lidar_yaw:=0.0
```

`lidar_z`の既定値は0.275で、この機体の実測値（接地面からMid-360まで275mm、2026-08-03実測）です。上の例は既定と同じ値を明示しているだけなので、この機体では省略できます。

同じTFをURDFとlaunchの両方から配信しないでください。

### IMUと車輪オドメトリ

Mid-360ではIMU融合が**既定で有効**です（`use_mid360_imu`、既定`true`）。`odom ->
base_footprint`と`/odom`の所有者はEKFで、EKFが車輪の並進速度とMid-360のZ軸角速度を
融合して両方を出します。車輪ノードは生値を`/wheel/odom`へ出すだけの立場です。
`false`にすると2D LiDAR構成と同様に、車輪ノードが`/odom`と`odom -> base_footprint`を
自分で配信する形に戻ります。

**この引数は`robot_bringup.launch.py`ひとつに閉じています。** 同じlaunchがドライバと
EKF（`odom_fusion.launch.py`）の両方を立てるので、**片方だけ切り替わる状態は作れません**。
`true`のときだけドライバの`odom`を`/wheel/odom`へremapし、TF配信を止めます（自前実装は
`publish_tf: false`、公式実装は`publish_tf`を持たないのでノードの`/tf`を
`/wheel/tf_unused`へ捨てます）。

> 2026-08-07より前はEKFが`lidar_bringup`側（ナビゲーションが起動する側）にあり、
> `robot_bringup`と`navigation`の両方へ同じ値を渡さないと壊れました。ナビゲーション側
> だけ`true`にするとEKFが入力を得られないまま`/odom`とTFの配信元が二重になり、
> **エラーも警告も出ませんでした**。2026-08-05の実機では静止中に姿勢が細かく震え、
> ウェイポイント追従を始めた瞬間に機体が回り出しました。EKFを機体側へ移したので
> この穴はありません。

**`lidar:=2d`と`use_mid360_imu:=true`は同時に指定できません**（起動時にエラーで
止まります）。URGにIMUは無いので、通すとEKFが`imu0`を一度も受け取らないまま車輪だけで
回り、融合しているつもりで融合していない状態になります。

### 切り替えは`.env`で

**既定値は環境変数`USE_MID360_IMU`から取ります**。読むのは`raspicat`サービス1つだけ
です。

```bash
# リポジトリルートの .env
USE_MID360_IMU=false
```

```bash
docker compose up -d   # 環境変数は起動時に読まれるのでコンテナを作り直す
```

`true` / `false`のほか`1` / `0` / `yes` / `no` / `on` / `off`も受けます。**読めない値は
起動時にエラーで止まります**（既定へ黙って落とすと、`USE_MID360_IMU=TRUE`のつもりが
違う構成で走ってしまうため）。launch引数`use_mid360_imu:=`を明示すればそちらが勝ちます。

### ジャイロのバイアス

**機体の電源投入時（`docker compose up`とPiの再起動）は静止させておいてください。**
EKFは常駐する`raspicat`サービスの一部になったので、**測定のタイミングは
navigationを立てるときではなくbootのとき**です。Mid-360のジャイロには大きな電源投入時
バイアスがあり、この個体はZ軸で+0.013960 rad/s（+0.800 deg/s、5001サンプル、2026-08-05実測）
でした。放置すると静止しているだけで48 deg/minヨーが回ります。`robot_localization`は
センサのバイアスを推定しないので、`prepare_mid360_imu`が起動後の静止区間から測って
引きます（`config/bringup/sensors/mid360_ekf.yaml`の`prepare_mid360_imu`節）。
測定はメッセージ駆動（静止した400サンプルが溜まるまで待つ）なので、`/livox/imu`が
後から来ても取りこぼしません。

測れたかはログで分かります。

```
prepare_mid360_imu: gyro bias = [+0.000112, -0.000305, +0.013960] rad/s
                    (z = +0.800 deg/s = 48.0 deg/min of yaw if left in), ...
```

動いていて測れないあいだは`still moving, so the gyro bias is not measured yet`が出ます。
値が分かっているなら`estimate_gyro_bias: false` + `gyro_bias: [x, y, z]`で固定できます。

## スキャンフィルタを変更する

恒久的に除外角度を変える場合は`config/bringup/sensors/scan_filter.yaml`の`angle_min`と`angle_max`をラジアンで編集します。別ファイルを使う場合:

```bash
ros2 launch daifuku_bringup robot_bringup.launch.py \
  lidar:=2d scan_filter_params_file:=/path/to/scan_filter.yaml
```

一時的に無効化する場合:

```bash
ros2 launch daifuku_bringup robot_bringup.launch.py \
  lidar:=2d scan_filter_enabled:=false
```

角度だけを変えたいならファイルごと渡さずに済みます。`overrides:=`は`sensors/`の
パラメータファイルにも重なるので、変えたいキーだけを書けます。行き先は
**パッケージ名とノード名**で決まるので、節の名前はファイル名ではありません。

```yaml
daifuku_bringup:
  scan_to_scan_filter_chain:   # -> daifuku_bringup の sensors/scan_filter.yaml
    ros__parameters:
      filter1:
        params:
          angle_min: 2.705260341
          angle_max: -2.705260341
```

`pointcloud_to_laserscan`（`mid360_scan.yaml`）、`ekf_filter_node`
（`mid360_ekf.yaml`）、`urg_node`（`urg_params_file`が指すファイル）も同じです。
JSONの`MID360_config.json`だけは対象外で、こちらは`mid360_config:=`でファイルごと
差し替えます。行き先の決まりかたは[設定](../usage/configuration.md)の
「上書き（overrides）の行き先」にあります。

## 確認コマンド

```bash
# 共通
ros2 topic hz /scan_raw
ros2 topic hz /scan

# Mid-360 + IMU
ros2 topic hz /livox/lidar
ros2 topic hz /livox/imu
ros2 topic hz /imu/mid360
ros2 topic hz /scan_mid360_prestamp
ros2 topic hz /wheel/odom
ros2 topic hz /odom
ros2 run tf2_ros tf2_echo odom base_footprint
```

2D LiDARでは`/scan_raw`がセンサー入力です。Mid-360ではセンサー入力が
`/scan_mid360_prestamp`、スタンプを打ち直した結果が`/scan_raw`になります。いずれの
構成でも、SLAM/Nav2への入力は`/scan`です。

Mid-360の起動時に`bind failed`となる場合は、設定したホストIPが対象NICへ
実際に割り当てられているか確認してください。
