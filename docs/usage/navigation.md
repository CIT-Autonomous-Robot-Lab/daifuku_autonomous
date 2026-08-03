# 自律移動

保存済み地図を読み込み、自己位置推定、経路計画、経路追従を起動します。

## tmuxで一式を起動する

Raspberry Pi本体にSSHでつなぎ、保存済み地図で自律移動を始める手順です。そのまま
貼り付けて実行できます。tmuxの基本操作は[日常操作と確認](operations.md#tmuxで作業する)を
参照してください。

まずコンテナを起動します。`raspicat`サービスが機体ドライバを立ち上げます。

```bash
cd ~/daifuku_autonomous   # リポジトリを置いた場所
docker compose -f docker/raspberrypi/compose.yaml up -d
```

続いてセッションを作り、3つの窓に割り当てます。

```bash
cd ~/daifuku_autonomous
tmux new-session -d -s nav -c "$PWD" -n nav
tmux send-keys -t nav:nav 'docker compose -f docker/raspberrypi/compose.yaml exec ros2 /ros_entrypoint.sh ros2 launch autonomous_nav navigation.launch.py map:=/opt/ros_ws/install/share/autonomous_nav/maps/map_19f.yaml use_sim_time:=false localization:=emcl2 planner:=vi use_mid360_imu:=false' Enter

tmux new-window -t nav -c "$PWD" -n motor
tmux send-keys -t nav:motor 'bash docker/raspberrypi/tools/control.sh motor on'

tmux new-window -t nav -c "$PWD" -n check
tmux send-keys -t nav:check 'bash docker/raspberrypi/tools/control.sh status' Enter

tmux attach -t nav
```

| 窓 | 中身 | 操作 |
|---|---|---|
| `nav` | Nav2、EMCL2、価値反復プランナ、センサー | 終了は`Ctrl-C` |
| `motor` | モーター電源 | `Enter`で実行。止めるときは同じ窓で`control.sh stop` |
| `check` | ノードとトピックの確認 | |

`motor`の窓だけ`Enter`を送っていません。機体が動ける状態になる操作なので、周囲を
確認してから自分で実行してください。

`check`の窓では、起動後に次を確認します。

```bash
bash docker/raspberrypi/tools/control.sh ros topic hz /scan
bash docker/raspberrypi/tools/control.sh ros topic hz /odom
bash docker/raspberrypi/tools/control.sh nodes
```

初期姿勢の設定とゴールの指定はRVizから行います。`docker/raspberrypi/`のイメージには
RVizが入っていないため、PC側の[GUI付き開発コンテナ](../setup/development-container.md)か
ネイティブ環境でRVizを開き、同じ`ROS_DOMAIN_ID`で接続してください。操作の順序は
[ゴールを指定する](#ゴールを指定する)にまとめています。

作業を終えるときは、モーター電源を切ってからセッションを閉じます。

```bash
bash docker/raspberrypi/tools/control.sh motor off
tmux kill-session -t nav
```

`use_mid360_imu:=false`は`raspicat`サービスに合わせた指定です。`raspimouse`は`/odom`と
`odom -> base_footprint`を自分で配信し、`/wheel/odom`は出しません。既定の`true`のままだと
EKFが入力を受け取れないうえ、`/odom`とTFの配信元が二重になります。

`lidar:=mid360`、`use_rviz:=false`、`publish_lidar_tf:=true`、`lidar_z:=0.275`は
すべてlaunchの既定値になったため、上のコマンドでは省いています。`lidar_z`の既定
0.275はこの機体のMid-360の搭載高さ（接地面から275mm、2026-08-03実測）です。機体を
変えたら実測し直してください。2D LiDAR構成では`lidar:=2d`を渡します（`urg_node`が
起動します）。広域地図
`map_tsudanuma`を使う場合は、`map:`と`overrides:`を[広域地図（map_tsudanuma）で動かす](#広域地図map_tsudanumaで動かす)の
とおりに差し替えてください。

## 基本起動

EMCL2、価値反復グローバル／ローカルプランナ、Mid-360が既定構成です。RVizは既定では
起動しません（実機がheadlessのため。PC側から開きます）。地図は
`maps/map_19f.yaml`（19F）が既定で、その地図向けの調整をまとめた
`config/overrides/map_19f.yaml`も`overrides`の既定値として一緒に載ります。
`overrides`は**置き換え**なので、別の地図では`overrides:=map_tsudanuma`のように
指定し直してください。何も重ねないなら`overrides:=none`です（`ros2 launch`は値が
空の`overrides:=`を受け付けません）。

Mid-360 + IMU（既定）:

```bash
ros2 launch autonomous_nav navigation.launch.py \
  map:=$PWD/src/autonomous_nav/maps/map_19f.yaml \
  use_sim_time:=false localization:=emcl2
```

2D LiDAR（raspicatのURGが起動します）:

```bash
ros2 launch autonomous_nav navigation.launch.py \
  map:=$PWD/src/autonomous_nav/maps/map_19f.yaml \
  use_sim_time:=false localization:=emcl2 lidar:=2d
```

RVizを同じ端末から開く場合は`use_rviz:=true`を渡します。

軽量Docker環境:

```bash
docker compose -f docker/raspberrypi/compose.yaml exec ros2 \
  /ros_entrypoint.sh ros2 launch autonomous_nav navigation.launch.py \
  map:=/opt/ros_ws/install/share/autonomous_nav/maps/map_19f.yaml \
  use_sim_time:=false localization:=emcl2
```

## 自己位置推定を選ぶ

Nav2標準AMCLへ変更する場合:

```bash
ros2 launch autonomous_nav navigation.launch.py \
  map:=$PWD/src/autonomous_nav/maps/map_19f.yaml \
  localization:=amcl
```

指定可能な値は`emcl2`（別名`emcl`）と`amcl`です。

## プランナを選ぶ

既定の`planner:=vi`は価値反復プランナを使います。`local_planner`も既定（`auto`）では`vi`になり、`vi_planner`1ノードが1本の価値関数で経路計画と経路追従の両方を担います。価値反復の計算はゴールごとに1回だけです。

NavFnとNav2 DWBへ切り替える場合:

```bash
ros2 launch autonomous_nav navigation.launch.py \
  map:=$PWD/src/autonomous_nav/maps/map_19f.yaml \
  planner:=navfn
```

グローバルは価値反復のまま、ローカルだけDWBへ変更する場合:

```bash
ros2 launch autonomous_nav navigation.launch.py \
  map:=$PWD/src/autonomous_nav/maps/map_19f.yaml \
  planner:=vi local_planner:=nav2
```

`local_planner:=auto`（既定）は、`planner:=vi`なら`vi`、`planner:=navfn`なら`nav2`を選びます。

## 広域地図（map_tsudanuma）で動かす

`maps/map_tsudanuma.yaml`は5888×4000セル（0.05 m/セル、294.4 m×200 m）の広域地図です。
価値反復はゴールごとに`nx × ny × theta_cell_num`の状態空間を扱います。この地図を
0.05 mのまま解くと状態数は14.1億に達し、既定の密ソルバは状態配列だけで79 GBを要求
するため、起動と同時に落ちます。

`config/overrides/map_tsudanuma.yaml`を`overrides:=map_tsudanuma`で重ねると、プランナ内部だけが
0.25 m/セル（`map_scale: 5`、1178×800×60＝5650万状態）に粗くなり、状態配列を確保しない
アウトオブコアソルバ（`frontier2d_sparse_compact`）へ切り替わります。確定した価値関数と方策は
`compact_sink_dir`のmmapファイル（約0.66 GB）に置かれます。地図サーバ、コストマップ、
自己位置推定は0.05 mのままです。

```bash
ros2 launch autonomous_nav navigation.launch.py \
  map:=$PWD/src/autonomous_nav/maps/map_tsudanuma.yaml \
  overrides:=map_tsudanuma \
  planner:=vi
```

`local_planner`は既定の`auto`（`planner:=vi`なので`vi`）でも`nav2`でも動きます。`vi_planner`と
`vi_global_planner`のどちらも`map_scale`とアウトオブコア経路を持つためです。`vi_planner`の
狭域追従だけは密な状態配列を必要とします。ただし全域ではなく、ロボット近傍のパッチだけを
`compact_sink_dir`のmmapファイルから起こして回します（±1 mウィンドウ＋遷移到達距離＋余裕。
0.25 mセルで27×27×60≒2.5 MB）。

`local_planner:=vi`で`map_scale > 1`のまま密ソルバを指定すると、全域の状態配列を確保して
しまいます。この組み合わせはlaunchが起動前に弾きます。

NavFnとDWBで動かす場合、`map_tsudanuma`の価値反復向け設定は要りませんが、
`overrides:=none`を渡してください。省略すると既定の`map_19f`が載り、この地図には
合わない19F用のEMCL2調整が適用されます。

```bash
ros2 launch autonomous_nav navigation.launch.py \
  map:=$PWD/src/autonomous_nav/maps/map_tsudanuma.yaml \
  overrides:=none \
  planner:=navfn
```

注意点:

- `map_scale: 5`は単独では効きません。`downsample_policy: optimistic`（ブロック内にfreeが
  1つでもあればfree）、`action_forward_m`、`goal_margin_radius`、`allow_action_mismatch`が
  セットで、1つでも欠けると波がゴール近傍で止まります。値は
  `config/overrides/map_tsudanuma.yaml`のコメントにそろえてあります。
- 保守的プーリング（障害物優先。`downsample_policy`の既定）だと通路のセル幅が価値反復の
  遷移分布（約2セル幅）を下回り、`map_scale`が4以上で波がゴール近傍から広がりません。
  楽観側は通路を細らせない代わりに、自由セルの境界が壁へ寄ります。
- 実測のfootprint（420×450 mm、外接円0.408 m）と`inflation_radius` 0.55 mで実際に通れるかは、
  経路ごとに確認してください。`map_scale: 5`で解けることを確かめた当時のコストマップは
  `robot_radius: 0.22`（`nav2_bringup`のyamlのまま）だったため、実機の条件はこれより
  厳しくなります。
- ピークRSSは`vi_planner`と`vi_global_planner`のどちらも約1.5 GB（匿名0.83 GB＋mmap
  0.66 GB）です。`map_scale: 3`＋保守的プーリングだった頃の3.98 GBから下がり、
  Raspberry Pi 4の4 GBに収まります。
- 新しいゴールを与えると、まず地図全体を解きます。BTを外した最小構成をPi 4相当の枠
  （0.6コア、`vi_threads: 3`）で回した実測では、solveとロールアウトに87〜89秒かかりました。
  返した経路は398姿勢で、結果はSUCCEEDEDです。同じゴールへの再計画はキャッシュヒットで
  即座に返ります。
- 同じ0.6コアの枠にBT込みで通すと、solveがCPUを占めてEMCL2まで道連れにし、900秒経っても
  `/plan`が出ませんでした（BTはsolveを待たずリカバリを繰り返してABORTします）。実機のPi 4は
  4コアあるので同じにはならないはずですが、**実機での通し確認はまだ取れていません**。

- `bt_navigator`の`wait_for_service_timeout`は60秒にしてあります。`planner:=vi`では
  プランナが`/map`を受け取ってから`compute_path_to_pose`を作るため、Nav2既定の1秒では
  間に合わずbringupが失敗します。
- この地図は68.2%が未観測セルで、占有セルは0.4%しかありません。EMCL2やAMCLの
  スキャンマッチングは占有セルの尤度場に依存するため、現状では自己位置推定の
  拠り所がほとんどありません（経路計画とは別の課題です）。

実測値の出どころは`config/overrides/map_tsudanuma.yaml`のヘッダ（2026-08-01）と
`src/autonomous_nav/config/README.md`です。`simulator/docs/pi4_sim.md`にもPi 4相当での
走行記録がありますが、そちらは`map_scale: 3`＋保守的プーリングだった頃のものなので、
所要時間もメモリもここの値とは一致しません。

## ゴールを指定する

RVizで次の順に操作します。

1. 「2D Pose Estimate」で地図上の初期姿勢を設定
2. センサーデータと地図が重なることを確認
3. 「Nav2 Goal」で移動先を指定

自律移動中は緊急停止をすぐ操作できる状態を保ってください。

## 価値反復の表示

`planner:=vi`では、新しいゴールの最初の計算で地図全体を解くため、地図サイズにより数秒から数十秒かかる場合があります。同じゴールへの再計画は価値関数キャッシュにより高速です。

`rviz/navigation.rviz`には次のOccupancyGrid表示があります。

- `/value_function`: 価値関数のθ=0スライス。計算途中も既定500 ms間隔で更新
- `/local_window_value`: 機体周辺±1 mの値。スキャン由来のペナルティと局所反復をリアルタイムに表示
  （`local_planner:=vi`のときのみ）

価値関数は1本しかないため、以前あった`/local_value_function`はありません。

表示にはRVizのMapを使い、Color Schemeを`costmap`にします。
