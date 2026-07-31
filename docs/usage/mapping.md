# 地図作成

SLAM Toolboxを起動し、機体を遠隔操作して地図を保存します。

## 1. 機体側ドライバを起動する

2D LiDARの場合:

- LiDARを`/scan_raw`へ配信
- 車輪オドメトリを`/odom`へ配信
- `odom -> base_footprint`とセンサーTFを配信

Mid-360 + IMUの場合:

- 車輪オドメトリを`/wheel/odom`へ配信
- 車輪側の`odom -> base_footprint` TFを停止
- `config/sensors/MID360_config.json`のIPとセンサーTFを設定

詳しくは[LiDARとオドメトリ](../setup/lidar.md)を参照してください。

## 2. SLAMを起動する

2D LiDAR:

```bash
ros2 launch autonomous_nav mapping.launch.py \
  lidar:=2d use_sim_time:=false
```

Mid-360（搭載値は例。実測値へ変更）:

```bash
ros2 launch autonomous_nav mapping.launch.py \
  lidar:=mid360 use_sim_time:=false \
  publish_lidar_tf:=true lidar_z:=0.30
```

> ネイティブ環境と`docker/dev/`で`lidar:=mid360`を使う場合は、事前に
> [スタンプ打ち直しの既知の制限](../setup/lidar.md#タイムスタンプの打ち直し)を
> 確認してください。対応しないと`/scan_raw`が配信されません。

軽量Docker環境:

```bash
docker compose -f docker/raspberrypi/compose.yaml exec ros2 \
  /ros_entrypoint.sh ros2 launch autonomous_nav mapping.launch.py \
  lidar:=2d use_sim_time:=false use_rviz:=false
```

## 3. 地図を作る

地図を作成する範囲をゆっくり走行します。操作ノードは`/cmd_vel`へ`geometry_msgs/msg/Twist`を配信する必要があります。

軽量Docker環境では、モーター電源を入れてから`control.sh`で操作できます。SLAMを
起動したターミナルとは別のターミナルで実行してください。

```bash
bash docker/raspberrypi/tools/control.sh motor on
bash docker/raspberrypi/tools/control.sh teleop keyboard
# ジョイスティックを使う場合
bash docker/raspberrypi/tools/control.sh teleop joystick
```

速度は`TELEOP_LINEAR_SPEED`と`TELEOP_ANGULAR_SPEED`で変更できます。地図作成では
既定より遅くしたほうが安定します。

```bash
TELEOP_LINEAR_SPEED=0.1 bash docker/raspberrypi/tools/control.sh teleop keyboard
```

RVizを使える環境では、次を確認しながら走行します。

- スキャンと壁の位置が一致している
- 地図が連続し、大きくずれていない
- TFエラーやスキャン欠落が継続していない

## 4. 地図を保存する

ネイティブ環境:

```bash
ros2 run nav2_map_server map_saver_cli \
  -f src/autonomous_nav/maps/map
```

軽量Docker環境:

```bash
docker compose -f docker/raspberrypi/compose.yaml exec ros2 \
  /ros_entrypoint.sh ros2 run nav2_map_server map_saver_cli \
  -f /opt/ros_ws/install/share/autonomous_nav/maps/map
```

`src/autonomous_nav`はコンテナへマウントされているため、次のファイルがホスト側にも残ります。

- `src/autonomous_nav/maps/map.yaml`
- `src/autonomous_nav/maps/map.pgm`

保存が終わったらモーター電源を切ります。

```bash
bash docker/raspberrypi/tools/control.sh motor off
```

保存後は[自律移動](navigation.md)へ進みます。
