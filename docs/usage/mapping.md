# 地図作成

SLAM Toolboxを起動し、機体を遠隔操作して地図を保存します。

## tmuxで一式を起動する

Raspberry Pi本体にSSHでつなぎ、`docker/raspberrypi/`環境で地図を作る手順です。
そのまま貼り付けて実行できます。tmuxの基本操作は[日常操作と確認](operations.md#tmuxで作業する)を
参照してください。

まずコンテナを起動します。`raspicat`サービスが`robot_bringup.launch.py`を立ち上げるので、
これでモーターと車輪オドメトリに加えて**LiDARとEKF**——つまりSLAMの入力になる`/scan`と
`/odom`——が揃います。

```bash
cd ~/daifuku_autonomous   # リポジトリを置いた場所
docker compose up -d
```

続いてセッションを作り、3つの窓に割り当てます。

```bash
cd ~/daifuku_autonomous
tmux new-session -d -s mapping -c "$PWD" -n slam
tmux send-keys -t mapping:slam 'docker compose exec ros2 /ros_entrypoint.sh ros2 launch daifuku_stack mapping.launch.py use_sim_time:=false' Enter

tmux new-window -t mapping -c "$PWD" -n teleop
tmux send-keys -t mapping:teleop 'bash tools/control.sh motor on'

tmux new-window -t mapping -c "$PWD" -n check
tmux send-keys -t mapping:check 'bash tools/control.sh status' Enter

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
TELEOP_LINEAR_SPEED=0.1 bash tools/control.sh teleop keyboard
```

走り終えたら`check`の窓で地図を保存します。**保存先は`src/`側**です（コンテナが
マウントしているので、そのままホストに残ります）。

```bash
docker compose exec ros2 \
  /ros_entrypoint.sh ros2 run nav2_map_server map_saver_cli \
  -f /opt/ros_ws/src/daifuku_stack/maps/19f/map_19f
```

保存を確認してから片付けます。`kill-session`はセッション内のノードもまとめて止めるため、
先にモーター電源を切ってください。

```bash
bash tools/control.sh motor off
tmux kill-session -t mapping
```

Mid-360のIMU融合（`use_mid360_imu`）は既定の`true`のまま使っています。`/odom`と
`odom -> base_footprint`を出すのはEKFで、`raspicat`サービスの本体ドライバは
`/wheel/odom`を出すだけです。**切り替えはリポジトリルートの`.env`の`USE_MID360_IMU`で**
行ってください（読むのは`raspicat`サービス1つだけです。変えたら
`docker compose up -d`）。**ドライバとEKFは同じlaunchが立てるので、片方だけ
切り替わる状態は作れません**
（[LiDARとオドメトリ](../setup/lidar.md#imuと車輪オドメトリ)）。

**起動時は機体を静止させておいてください。** Mid-360のジャイロの電源投入時バイアス
（実測+0.80 deg/s = 48 deg/min）を`prepare_mid360_imu`が測って引きます。

**LiDARの引数は`mapping.launch.py`にはありません。** センサーを立てるのは
`raspicat`サービス（`robot_bringup.launch.py`）のほうで、`lidar`も`lidar_z`も
`publish_lidar_tf`もそちらの引数です。2D LiDAR構成で地図を作るならリポジトリルートの
`.env`に`LIDAR=2d`を書いて`docker compose up -d`してください（ネイティブ環境では
`robot_bringup.launch.py`へ`lidar:=2d`を渡します）。`lidar_z`の既定0.275はこの機体の
Mid-360の搭載高さ（接地面から275mm、2026-08-03実測）なので、機体を変えたら実測し
直してください。**上のコマンド自体はLiDARの構成によらず同じです。**

ネイティブ環境では[コマンドの読み替え](README.md#コマンドの読み替え)に従って前置きの
`docker compose`部分を外します。地図の様子を見るなら`use_rviz:=true`を足してください。

以降の節では、各手順の内容と選べる引数を説明します。

## 1. 機体側ドライバを起動する

`docker/raspberrypi/`環境では、Composeの`raspicat`サービスが`robot_bringup.launch.py`
（本体ドライバ、`robot_state_publisher`、**LiDAR**、**EKF**）を起動します。どの本体
ドライバになるかは`.env`の`COMPOSE_FILE`で決まります（既定は自前実装の`compose.original.yaml`。公式実装は
`compose.rt.yaml`で、rtmouse 入りの Pi 4 専用。
[Pi 4](../setup/raspberry-pi-4.md) / [Pi 5](../setup/raspberry-pi-5.md)）。

```bash
docker compose up -d
```

このサービスは`restart: unless-stopped`で動きます。**LiDARとEKFもこちら側です。**
ナビゲーション側の`ros2`サービスを
入れ替えてもドライバは動き続けるため、オドメトリの累積が途切れません。

別の機体で動かす場合は、次を機体側で用意してください。

2D LiDARの場合:

- LiDARを`/scan_raw`へ配信
- 車輪オドメトリを`/odom`へ配信
- `odom -> base_footprint`とセンサーTFを配信

Mid-360 + IMUの場合:

- 車輪オドメトリを`/wheel/odom`へ配信
- 車輪側の`odom -> base_footprint` TFを停止
- `src/daifuku_config/bringup/sensors/MID360_config.json`のIPとセンサーTFを設定

詳しくは[LiDARとオドメトリ](../setup/lidar.md)を参照してください。

## 2. SLAMを起動する

```bash
ros2 launch daifuku_stack mapping.launch.py use_sim_time:=false
```

**LiDARの構成によらず同じコマンドです。** `/scan`を出すのは機体側なので、Mid-360でも
2D LiDARでもこちらは変わりません（構成の切り替えは前節）。地図の様子をその場で見るなら
`use_rviz:=true`を足します（既定は`false`）。

SLAM Toolboxの値を差し替えるなら、`slam_params_file:=`でファイルごと渡すほかに、
`overrides:=`で一部のキーだけを重ねられます（`slam_toolbox:`の節を書きます。
`mapping.launch.py`も既定で`src/daifuku_config/site`の名前を受けるので、別の場所を測るときは
先に`tools/site.sh`で切り替えるか、`overrides:=none`を渡してください）。書きかたは
[設定](configuration.md)の「上書き（overrides）の行き先」にあります。

**ただし、走らせたまま直さないでください。** `mapping.launch.py`も`config_sentinel`を
1つ立てていて、`daifuku_stack`の`src/daifuku_config/`の値が書き変わると**このlaunchごと終了します**。
`navigation`と違って地図作成は途中経過に価値があるのに、上げ直す人は居らず、SLAM Toolboxは
終了時に保存しないので、**そこまで走った分が消えます**。長丁場のときは見張りを黙らせて
おくのが安全です。

```bash
ros2 launch daifuku_stack mapping.launch.py use_sim_time:=false config_watch:=warn
```

`warn`は変化をログに出すだけ、`off`は見張りごと立てません。値を反映するには、どちらでも
地図を保存してから立て直してください（[日常操作](operations.md#走らせたまま設定を直したとき)）。

軽量Docker環境:

```bash
docker compose exec ros2 \
  /ros_entrypoint.sh ros2 launch daifuku_stack mapping.launch.py \
  use_sim_time:=false
```

## 3. 地図を作る

地図を作成する範囲をゆっくり走行します。操作ノードは`/cmd_vel_teleop`へ`geometry_msgs/msg/Twist`を配信する必要があります（`twist_mux`の手動側の入口。`twist_mux:=false`で起動したなら`/cmd_vel`）。地図作成中は自律側が`/cmd_vel`を出さないので、優先度が下でもそのまま通ります。

軽量Docker環境では、モーター電源を入れてから`control.sh`で操作できます。SLAMを
起動したターミナルとは別のターミナルで実行してください。

```bash
bash tools/control.sh motor on
bash tools/control.sh teleop keyboard
# ジョイスティックを使う場合
bash tools/control.sh teleop joystick
```

速度は`TELEOP_LINEAR_SPEED`と`TELEOP_ANGULAR_SPEED`で変更できます。地図作成では
既定より遅くしたほうが安定します。

```bash
TELEOP_LINEAR_SPEED=0.1 bash tools/control.sh teleop keyboard
```

RVizを使える環境では、次を確認しながら走行します。

- スキャンと壁の位置が一致している
- 地図が連続し、大きくずれていない
- TFエラーやスキャン欠落が継続していない

## 4. 地図を保存する

ネイティブ環境:

```bash
ros2 run nav2_map_server map_saver_cli \
  -f src/daifuku_stack/maps/19f/map_19f
```

軽量Docker環境:

```bash
docker compose exec ros2 \
  /ros_entrypoint.sh ros2 run nav2_map_server map_saver_cli \
  -f /opt/ros_ws/src/daifuku_stack/maps/19f/map_19f
```

**`src/`側へ書いてください。** `src/daifuku_stack`はコンテナへマウントされているので、
次のファイルがそのままホスト側に残ります（`install/`側の`maps/`はビルド時に張った
symlinkなので、そちらへ書くとホストに残るかどうかがsymlinkの張り方に依存します）。

- `src/daifuku_stack/maps/19f/map_19f.yaml`
- `src/daifuku_stack/maps/19f/map_19f.pgm`

**保存したら`free_thresh`を`0.15`へ直してください。** `map_saver_cli`が書く既定は
`0.25`ですが、同じ`map_saver_cli`が未観測に使う画素205はp=(255-205)/255=0.196なので、
`p < free_thresh`が成立して**未観測セルが全部「空き」として読み込まれます**。そうなると
VIの`unknown_as_obstacle`もコストマップの`track_unknown_space`も、未観測が存在しない
ことになるので**エラーも警告も出ないまま効きません**（19Fの地図では自由セルが実際の
10.6万に対し51.9万＝4.9倍に膨れ、建物の外まで経路が引けます）。0.196ちょうどではなく
0.15にするのは、0.196が205のpとほぼ同値で実装によって空き側へ転びうるためです
（[`simulator/docs/pi4_sim.md`](../../simulator/docs/pi4_sim.md#free_thresh-を下げるときの注意)）。

`map_19f`は19Fの地図の名前で、`src/daifuku_config/site`の既定値でもあります。別の場所の地図を
作るときは名前を変えてください。そのとき`src/daifuku_config/overrides/<同じ名前>.yaml`も用意し、
`tools/site.sh <名前>`で切り替えます。**どの地図を読むかは、そのoverridesの`site:`節に
書きます**（`site: map:`の下に`navigation:`と`localization:`の2行。`maps/`からの相対パス。
[自律移動](navigation.md#地図は2枚)）。overridesの名前と地図の
ファイル名は揃っていなくて構いませんが、**書き忘れると起動時にエラーで止まります**
（既定の地図へは落としません）。

**新しい名前で保存したときと`overrides/`にファイルを足したときは、一度ビルドを通して
ください**（`docker compose up -d`。`install/`のsymlinkはビルド時にしか張られないので、
足しただけでは`map:=`にも`overrides:=`の一覧にも出てきません）。既にある
名前へ上書きしたときは要りません。詳細は[設定](configuration.md)と
`src/daifuku_config/README.md`を参照してください。

保存が終わったらモーター電源を切ります。

```bash
bash tools/control.sh motor off
```

保存後は[自律移動](navigation.md)へ進みます。
