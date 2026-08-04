# LiDARとオドメトリ

`mapping.launch.py`と`navigation.launch.py`は`lidar:=2d|mid360`でセンサー構成を切り替えます。既定は本機の構成に合わせて`mid360`です。どちらも入力を`/scan_raw`へ集約し、角度フィルタ後の`/scan`をSLAMとNav2へ渡します。

既定の`config/sensors/scan_filter.yaml`は、コネクタがある後方60度（+150度から-150度まで、±180度をまたぐ範囲）を除外します。

センサーごとのトピックの流れは次のとおりです。

```text
2D LiDAR      : urg_node → /scan_raw → 角度フィルタ → /scan
Mid-360       : /livox/lidar → pointcloud_to_laserscan
                → /scan_mid360_prestamp → restamp_scan.py
                → /scan_raw → 角度フィルタ → /scan
```

## 2D LiDAR

`lidar:=2d`を指定すると、raspicatのURG（`urg_node`の`urg_node_driver`）が起動します。
パラメータは`raspicat_bringup`の`config/urg_<urg_interface>.param.yaml`（既定は
`urg_serial.param.yaml`。`/dev/ttyACM0`、`laser_frame_id: lidar_link`）で、出力は
`/scan_raw`へremapされます。

```bash
ros2 launch autonomous_nav navigation.launch.py lidar:=2d
```

Ethernet接続のURGでは`urg_interface:=ethernet`を指定します。別のパラメータファイルを
使う場合は`urg_params_file:=/path/to/urg.param.yaml`を渡します。

`docker/raspberrypi/`環境では、既定がMid-360（Ethernet）のため`/dev/ttyACM0`を
コンテナへ渡していません。シリアル接続のURGを使うときは`compose.yaml`の`ros2`
サービスへ次を足してください。存在しないデバイスを書くと`compose up`自体が
失敗するので、URGを挿したときだけ有効にします。

```yaml
    devices:
      - /dev/ttyACM0:/dev/ttyACM0
```

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

`src/autonomous_nav/config/sensors/MID360_config.json`を実ネットワークに合わせます。

- `host_net_info`内の4個の`*_data_ip`: ドライバを動かすPCの固定IP
- `lidar_configs[0].ip`: Mid-360本体のIP

### タイムスタンプの打ち直し

本機のMid-360はPTP/gPTPで時刻同期していないため、`livox_ros_driver2`は
デバイス内蔵時計の時刻をスタンプに使います。この時計はPiのシステム時計から
毎分数秒ずつずれていきます。そのままにすると、EMCL2の`map -> odom` TFや
Nav2コストマップのメッセージフィルタが、起動から数分でデータを「古すぎる」
「未来の時刻」として破棄します。

対策として、`lidar:=mid360`で実機ドライバを立てるときだけ`src/restamp_scan.py`が
`/scan_mid360_prestamp`を購読し、受信時刻でスタンプを打ち直して`/scan_raw`へ再配信
します。こうするとスキャンのスタンプが、車輪オドメトリ・TF・Nav2と同じ時計に
そろいます。ドリフトのないシミュレータやバッグ再生（`lidar_driver:=false`）では中継を
挟まず、`pointcloud_to_laserscan`が`/scan_raw`へ直接出します。センサー側をPTP同期
できるようになれば、この中継は不要になります。

中継は`share/autonomous_nav/src/restamp_scan.py`を`ExecuteProcess`で直接起動する形
なので、`CMakeLists.txt`が`src`ディレクトリを`share`へインストールしています
（通常のノードとして起動する`prepare_mid360_imu.py`のほうは、これに加えて`lib`へも
入ります）。`ExecuteProcess`は失敗しても他のノードを止めません。そのため、古い`install/`が
残っているなどで`restamp_scan.py`が置かれていない環境では、エラーが出ないまま
`/scan_raw`だけが配信されない状態になります。切り分けは
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

Mid-360ではIMU融合が既定で有効です。機体側の車輪オドメトリを`/wheel/odom`へremapし、車輪ノード自身による`odom -> base_footprint` TF配信を止めてください。ナビゲーション側のEKFが車輪速度とMid-360のZ軸角速度を融合し、最終的な`/odom`とTFを配信します。

自前実装（`driver:=original`）にはこのための`publish_tf`パラメータがあり、
`config/robot/raspicat_driver.yaml`で`false`にすると`odom -> base_footprint`の配信だけを
止められます。TF無効化機能を持たない車輪ドライバでは、そのノードの`/tf`を未使用トピックへ
remapします。実際のパッケージ名とノード名に置き換えてください。

```bash
ros2 run <wheel_package> <wheel_odom_node> --ros-args \
  -r /odom:=/wheel/odom -r /tf:=/wheel/tf_unused
```

IMU融合を無効にする場合は`use_mid360_imu:=false`を指定します。この場合は2D LiDAR構成と同様に、車輪ノードが通常の`/odom`と`odom -> base_footprint`を配信します。

## スキャンフィルタを変更する

恒久的に除外角度を変える場合は`src/autonomous_nav/config/sensors/scan_filter.yaml`の`angle_min`と`angle_max`をラジアンで編集します。別ファイルを使う場合:

```bash
ros2 launch autonomous_nav mapping.launch.py \
  lidar:=2d scan_filter_params_file:=/path/to/scan_filter.yaml
```

一時的に無効化する場合:

```bash
ros2 launch autonomous_nav mapping.launch.py \
  lidar:=2d scan_filter_enabled:=false
```

角度だけを変えたいならファイルごと渡さずに済みます。`overrides:=`は`sensors/`の
パラメータファイルにも重なるので、変えたいキーだけを書けます。行き先はノード名で
決まるので、節の名前はファイル名ではなくノード名です。

```yaml
scan_to_scan_filter_chain:   # -> config/sensors/scan_filter.yaml
  ros__parameters:
    filter1:
      params:
        angle_min: 2.617993878
        angle_max: -2.617993878
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
