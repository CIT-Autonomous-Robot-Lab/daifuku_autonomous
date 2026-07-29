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

> ネイティブ環境と`docker_dev/`で`lidar:=mid360`を使う場合は、事前に
> [スタンプ打ち直しの既知の制限](../setup/lidar.md#タイムスタンプの打ち直し)を
> 確認してください。対応しないと`/scan_raw`が配信されません。

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

既定の`planner:=vi`は価値反復プランナを使います。`local_planner`も既定（`auto`）では`vi`になり、`vi_planner`1ノードが経路計画と経路追従の両方を1本の価値関数から担います（ゴールごとのVI計算は1回だけです）。

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

## 広域地図（map_tsudanuma）で動かす

`maps/map_tsudanuma.yaml`は5888×4000セル（0.05 m/セル、294.4 m×200 m）の広域地図です。
価値反復はゴールごとに`nx × ny × theta_cell_num`の状態空間を扱うため、この地図を
0.05 mのまま解くと14.1億状態になり、既定の密ソルバでは状態配列だけで79 GBを要求して起動と同時に落ちます。

`config/overrides/map_tsudanuma.yaml`を`overrides:=map_tsudanuma`で重ねると、プランナ内部だけを
0.15 m/セル（`map_scale: 3`、1963×1334＝1.57億状態）に粗くし、状態配列を確保しない
アウトオブコアソルバ（`frontier2d_sparse_compact`）へ切り替えます。確定した価値関数と方策は
`compact_sink_dir`のmmapファイル（約1.9 GB）に置かれます。地図サーバ、コストマップ、
自己位置推定は0.05 mのままです。

```bash
ros2 launch autonomous_nav navigation.launch.py \
  map:=$PWD/src/autonomous_nav/maps/map_tsudanuma.yaml \
  overrides:=map_tsudanuma \
  planner:=vi local_planner:=nav2
```

NavFnとDWBで動かす場合、`overrides`は不要です。

```bash
ros2 launch autonomous_nav navigation.launch.py \
  map:=$PWD/src/autonomous_nav/maps/map_tsudanuma.yaml \
  planner:=navfn
```

注意点:

- `local_planner:=vi`は使えません。`vi_planner`はアウトオブコア経路も`map_scale`も
  持たず、地図全体を密に解くためです。`local_planner:=nav2`を指定してください
  （`vi_global_planner`＋`controller_server`の構成になります）。
- 初回のゴールでは地図全体を解くため時間がかかります（ローカル16コアの実測で約25秒）。
  同じゴールへの再計画はキャッシュヒットで即座に返ります。
- `bt_navigator`の`wait_for_service_timeout`は60秒にしてあります。`planner:=vi`では
  `vi_global_planner`が`/map`を受け取ってから`compute_path_to_pose`を作るため、
  Nav2既定の1秒では間に合わずbringupが失敗します。
- `map_scale`の3×3プーリングは障害物優先のため、通路は片側最大0.10 m細くなります。
- メモリはローカル実測で`vi_global_planner`のピークRSS 3.98 GB（内訳: 匿名2.16 GB +
  mmapページキャッシュ1.81 GB）です。mmapに逃がしても匿名2.16 GBが残るため、
  Raspberry Pi 4 4GBでこの設定が通るかは未確認です。減らすには`map_scale`を上げます
  （詳細は`tools/pi4_sim/README.md`）。
- この地図は68.2%が未観測セルで、占有セルは0.4%しかありません。EMCL2やAMCLの
  スキャンマッチングはこの占有セルの尤度場に依存するため、この地図のままでは
  自己位置推定の拠り所がほとんどありません（経路計画とは別の課題です）。

## ゴールを指定する

RVizで次の順に操作します。

1. 「2D Pose Estimate」で地図上の初期姿勢を設定
2. センサーデータと地図が重なることを確認
3. 「Nav2 Goal」で移動先を指定

自律移動中は緊急停止をすぐ操作できる状態を保ってください。

## 価値反復の表示

`planner:=vi`では、新しいゴールの最初の計算で地図全体を解くため、地図サイズにより数秒から数十秒かかる場合があります。同じゴールへの再計画は価値関数キャッシュにより高速です。

`rviz/nav2_default.rviz`には次のOccupancyGrid表示があります。

- `/value_function`: 価値関数のθ=0スライス。計算途中も既定500 ms間隔で更新
- `/local_window_value`: ロボット周辺±1 mの値。スキャン由来ペナルティと局所反復をリアルタイム表示
  （`local_planner:=vi`のときのみ）

価値関数は1本しかないため、以前あった`/local_value_function`はありません。

表示にはRVizのMapを使い、Color Schemeを`costmap`にします。
