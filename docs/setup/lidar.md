# LiDARとオドメトリ

`mapping.launch.py`と`navigation.launch.py`は`lidar:=2d|mid360`でセンサー構成を切り替えます。どちらも入力を`/scan_raw`へ集約し、角度フィルタ後の`/scan`をSLAMとNav2へ渡します。

既定の`config/sensors/scan_filter.yaml`は、コネクタがある後方60度（+150度から-150度まで、±180度をまたぐ範囲）を無効化します。

センサーごとのトピックの流れは次のとおりです。

```text
2D LiDAR      : ドライバ → /scan_raw → 角度フィルタ → /scan
Mid-360       : /livox/lidar → pointcloud_to_laserscan
                → /scan_mid360_prestamp → restamp_scan.py
                → /scan_raw → 角度フィルタ → /scan
```

## 2D LiDAR

LiDARドライバの出力を`/scan_raw`へremapします。

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

本機のMid-360はPTP/gPTPの時刻同期をしていないため、`livox_ros_driver2`はデバイス
内蔵時計の時刻を付けます。この時計はPiのシステム時計に対して毎分数秒ずれていくため、
そのままではEMCL2の`map -> odom` TFやNav2コストマップのメッセージフィルタが、
起動から数分でデータを「古すぎる」「未来の時刻」として破棄します。

対策として、`lidar:=mid360`のときだけ`scripts/restamp_scan.py`が
`/scan_mid360_prestamp`を購読し、受信時刻でスタンプを打ち直して`/scan_raw`へ再配信
します。これによりスキャンのスタンプが、車輪オドメトリ・TF・Nav2と同じ時計に揃います。
センサー側をPTP同期できるようになれば、この中継は不要になります。

> **既知の制限**: `restamp_scan.py`は`share/autonomous_nav/scripts/`から起動され
> ますが、`CMakeLists.txt`はこのディレクトリをインストールしません。`docker/raspberrypi/`の
> Compose環境では`src/autonomous_nav`が`share/autonomous_nav`へまるごとマウント
> されるため動作しますが、ネイティブビルドと`docker/dev/`ではファイルが存在せず、
> 中継が起動しません。しかも`ExecuteProcess`は失敗しても他のノードを止めないため、
> エラーは出ないまま`/scan_raw`だけが配信されない状態になります。
>
> `docker/raspberrypi/`以外で`lidar:=mid360`を使う場合は、先に`CMakeLists.txt`へ次を追加して
> ください。launch側が`share`配下を参照するため、宛先は`lib/`ではなく`share/`です。
>
> ```cmake
> install(PROGRAMS scripts/restamp_scan.py
>   DESTINATION share/${PROJECT_NAME}/scripts)
> ```

### センサーTF

`base_footprint -> livox_frame`はURDFから配信することを推奨します。未設定の場合は、実測した搭載位置と姿勢をlaunch引数で一時配信できます。位置はメートル、姿勢はラジアンです。

```bash
publish_lidar_tf:=true \
lidar_x:=0.0 lidar_y:=0.0 lidar_z:=0.30 \
lidar_roll:=0.0 lidar_pitch:=0.0 lidar_yaw:=0.0
```

同じTFをURDFとlaunchの両方から配信しないでください。

### IMUと車輪オドメトリ

Mid-360ではIMU融合が既定で有効です。機体側の車輪オドメトリを`/wheel/odom`へremapし、車輪ノード自身による`odom -> base_footprint` TF配信を止めてください。ナビゲーション側のEKFが車輪速度とMid-360のZ軸角速度を融合し、最終的な`/odom`とTFを配信します。

車輪ドライバにTF無効化機能がない場合は、そのノードの`/tf`を未使用トピックへremapします。実際のパッケージ名とノード名に置き換えてください。

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
`/scan_mid360_prestamp`、スタンプを打ち直した結果が`/scan_raw`となります。いずれも
SLAM/Nav2への入力は`/scan`です。Mid-360起動時に`bind failed`となる場合は、設定したホストIPが対象NICへ実際に割り当てられているか確認してください。
