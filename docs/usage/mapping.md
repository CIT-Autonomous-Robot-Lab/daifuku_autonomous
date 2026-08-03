# 地図作成

SLAM Toolboxを起動し、機体を遠隔操作して地図を保存します。

## tmuxで一式を起動する

Raspberry Pi本体にSSHでつなぎ、`docker/raspberrypi/`環境で地図を作る手順です。
そのまま貼り付けて実行できます。tmuxの基本操作は[日常操作と確認](operations.md#tmuxで作業する)を
参照してください。

まずコンテナを起動します。`raspicat`サービスが機体ドライバを立ち上げるため、これで
モーターと車輪オドメトリが使える状態になります。

```bash
cd ~/daifuku_autonomous   # リポジトリを置いた場所
docker compose -f docker/raspberrypi/compose.yaml up -d
```

続いてセッションを作り、3つの窓に割り当てます。

```bash
cd ~/daifuku_autonomous
tmux new-session -d -s mapping -c "$PWD" -n slam
tmux send-keys -t mapping:slam 'docker compose -f docker/raspberrypi/compose.yaml exec ros2 /ros_entrypoint.sh ros2 launch autonomous_nav mapping.launch.py use_mid360_imu:=false use_sim_time:=false' Enter

tmux new-window -t mapping -c "$PWD" -n teleop
tmux send-keys -t mapping:teleop 'bash docker/raspberrypi/tools/control.sh motor on'

tmux new-window -t mapping -c "$PWD" -n check
tmux send-keys -t mapping:check 'bash docker/raspberrypi/tools/control.sh status' Enter

tmux attach -t mapping
```

| 窓 | 中身 | 操作 |
|---|---|---|
| `slam` | SLAM Toolboxとセンサー | 地図作成を終えるときは`Ctrl-C` |
| `teleop` | モーター電源と遠隔操作 | コマンドを入力した状態で待機。`Enter`で実行する |
| `check` | 状態確認と地図の保存 | |

`teleop`の窓だけ`Enter`を送っていません。機体が動き出す操作なので、周囲を確認してから
自分で実行してください。モーター電源を入れたら、同じ窓で遠隔操作を始めます。

```bash
TELEOP_LINEAR_SPEED=0.1 bash docker/raspberrypi/tools/control.sh teleop keyboard
```

走り終えたら`check`の窓で地図を保存します。

```bash
docker compose -f docker/raspberrypi/compose.yaml exec ros2 \
  /ros_entrypoint.sh ros2 run nav2_map_server map_saver_cli \
  -f /opt/ros_ws/install/share/autonomous_nav/maps/map_19f
```

保存を確認してから片付けます。`kill-session`はセッション内のノードもまとめて止めるため、
先にモーター電源を切ってください。

```bash
bash docker/raspberrypi/tools/control.sh motor off
tmux kill-session -t mapping
```

`use_mid360_imu:=false`は`raspicat`サービスに合わせた指定です。`raspimouse`は`/odom`と
`odom -> base_footprint`を自分で配信し、`/wheel/odom`は出しません。既定の`true`のままだと
EKFが入力を受け取れないうえ、`/odom`とTFの配信元が二重になります。

`lidar:=mid360`、`use_rviz:=false`、`publish_lidar_tf:=true`、`lidar_z:=0.275`は
すべてlaunchの既定値になったため、上のコマンドでは省いています。`lidar_z`の既定
0.275はこの機体のMid-360の搭載高さ（接地面から275mm、2026-08-03実測）です。機体を
変えたら実測し直してください。2D LiDAR構成では`lidar:=2d`を渡します（`urg_node`が
起動します）。ネイティブ環境では[コマンドの読み替え](README.md#コマンドの読み替え)に
従って前置きの`docker compose`部分を外し、地図の様子を見るなら`use_rviz:=true`を
足してください。

以降の節では、各手順の内容と選べる引数を説明します。

## 1. 機体側ドライバを起動する

`docker/raspberrypi/`環境では、Composeの`raspicat`サービスが`robot_bringup.launch.py`
（`raspimouse`と`robot_state_publisher`）を起動します。

```bash
docker compose -f docker/raspberrypi/compose.yaml up -d
```

このサービスは`restart: unless-stopped`で動きます。ナビゲーション側の`ros2`サービスを
入れ替えてもドライバは動き続けるため、オドメトリの累積が途切れません。

別の機体で動かす場合は、次を機体側で用意してください。

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

Mid-360（既定）:

```bash
ros2 launch autonomous_nav mapping.launch.py use_sim_time:=false
```

`lidar_z`の既定0.275はこの機体の実測値です。別の機体では`lidar_z:=<実測値>`を渡します。

2D LiDAR（raspicatのURGが起動します）:

```bash
ros2 launch autonomous_nav mapping.launch.py \
  lidar:=2d use_sim_time:=false
```

地図の様子をその場で見るなら`use_rviz:=true`を足します（既定は`false`）。

軽量Docker環境:

```bash
docker compose -f docker/raspberrypi/compose.yaml exec ros2 \
  /ros_entrypoint.sh ros2 launch autonomous_nav mapping.launch.py \
  lidar:=2d use_sim_time:=false
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
  -f src/autonomous_nav/maps/map_19f
```

軽量Docker環境:

```bash
docker compose -f docker/raspberrypi/compose.yaml exec ros2 \
  /ros_entrypoint.sh ros2 run nav2_map_server map_saver_cli \
  -f /opt/ros_ws/install/share/autonomous_nav/maps/map_19f
```

`src/autonomous_nav`はコンテナへマウントされているため、次のファイルがホスト側にも残ります。

- `src/autonomous_nav/maps/map_19f.yaml`
- `src/autonomous_nav/maps/map_19f.pgm`

`map_19f`は19Fの地図の名前で、`navigation.launch.py`の`map`の既定値です。別の場所の
地図を作るときは名前を変えてください。その場合、自律移動では`map:=`と一緒に
`overrides:=`も指定し直します（既定の`overrides:=map_19f`が載ったままになると、
19F向けのEMCL2調整が別の地図に適用されます）。詳細は
[設定](configuration.md)と`src/autonomous_nav/config/README.md`を参照してください。

保存が終わったらモーター電源を切ります。

```bash
bash docker/raspberrypi/tools/control.sh motor off
```

保存後は[自律移動](navigation.md)へ進みます。
