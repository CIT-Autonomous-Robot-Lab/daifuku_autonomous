# 自律移動

保存済み地図を読み込み、自己位置推定、経路計画、経路追従を起動します。

## 基本起動

EMCL2、価値反復グローバル／ローカルプランナ、2D LiDARが既定構成です。

```bash
ros2 launch autonomous_nav navigation.launch.py \
  map:=$PWD/src/autonomous_nav/maps/map.yaml \
  use_sim_time:=false localization:=emcl2 lidar:=2d
```

Mid-360 + IMU:

```bash
ros2 launch autonomous_nav navigation.launch.py \
  map:=$PWD/src/autonomous_nav/maps/map.yaml \
  use_sim_time:=false localization:=emcl2 lidar:=mid360 \
  publish_lidar_tf:=true lidar_z:=0.30
```

軽量Docker環境:

```bash
docker compose -f docker/compose.yaml exec ros2 \
  /ros_entrypoint.sh ros2 launch autonomous_nav navigation.launch.py \
  map:=/opt/ros_ws/install/share/autonomous_nav/maps/map.yaml \
  use_sim_time:=false localization:=emcl2 lidar:=2d use_rviz:=false
```

## 自己位置推定を選ぶ

Nav2標準AMCLへ変更する場合:

```bash
ros2 launch autonomous_nav navigation.launch.py \
  map:=$PWD/src/autonomous_nav/maps/map.yaml \
  localization:=amcl
```

指定可能な値は`emcl2`（別名`emcl`）と`amcl`です。

## プランナを選ぶ

既定の`planner:=vi`は`vi_global_planner`を使います。経路追従も自動的に`vi_local_planner`になります。

NavFnとNav2 DWBへ切り替える場合:

```bash
ros2 launch autonomous_nav navigation.launch.py \
  map:=$PWD/src/autonomous_nav/maps/map.yaml \
  planner:=navfn
```

グローバルは価値反復のまま、ローカルだけDWBへ変更する場合:

```bash
ros2 launch autonomous_nav navigation.launch.py \
  map:=$PWD/src/autonomous_nav/maps/map.yaml \
  planner:=vi local_planner:=nav2
```

`local_planner:=auto`（既定）は、`planner:=vi`なら`vi`、`planner:=navfn`なら`nav2`を選びます。

## ゴールを指定する

RVizで次の順に操作します。

1. 「2D Pose Estimate」で地図上の初期姿勢を設定
2. センサーデータと地図が重なることを確認
3. 「Nav2 Goal」で移動先を指定

自律移動中は緊急停止をすぐ操作できる状態を保ってください。

## 価値反復の表示

`planner:=vi`では、新しいゴールの最初の計算で地図全体を解くため、地図サイズにより数秒から数十秒かかる場合があります。同じゴールへの再計画は価値関数キャッシュにより高速です。

`rviz/nav2_default.rviz`には次のOccupancyGrid表示があります。

- `/value_function`: グローバル価値関数のθ=0スライス。計算途中も既定500 ms間隔で更新
- `/local_value_function`: ローカル価値関数の計算過程と完成形。既定のRViz設定では非表示
- `/local_window_value`: ロボット周辺±1 mの値。スキャン由来ペナルティと局所反復をリアルタイム表示

表示にはRVizのMapを使い、Color Schemeを`costmap`にします。
