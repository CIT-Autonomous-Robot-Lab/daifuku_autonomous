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
- `MID360_config.json`のIPとセンサーTFを設定

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

軽量Docker環境:

```bash
docker compose -f docker/compose.yaml exec ros2 \
  /ros_entrypoint.sh ros2 launch autonomous_nav mapping.launch.py \
  lidar:=2d use_sim_time:=false use_rviz:=false
```

## 3. 地図を作る

機体側の操作ノードやゲームパッドで、地図を作成する範囲をゆっくり走行します。操作ノードは`/cmd_vel`へ`geometry_msgs/msg/Twist`を配信する必要があります。

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
docker compose -f docker/compose.yaml exec ros2 \
  /ros_entrypoint.sh ros2 run nav2_map_server map_saver_cli \
  -f /opt/ros_ws/install/share/autonomous_nav/maps/map
```

`src/autonomous_nav`はコンテナへマウントされているため、次のファイルがホスト側にも残ります。

- `src/autonomous_nav/maps/map.yaml`
- `src/autonomous_nav/maps/map.pgm`

保存後は[自律移動](navigation.md)へ進みます。
