# 自律移動

保存済み地図を読み込み、自己位置推定、経路計画、経路追従を起動します。

## tmuxで一式を起動する

Raspberry Pi本体にSSHでつなぎ、保存済み地図で自律移動を始める手順です。そのまま
貼り付けて実行できます。tmuxの基本操作は[日常操作と確認](operations.md#tmuxで作業する)を
参照してください。

まずコンテナを起動します。`raspicat`サービスが機体ドライバを立ち上げます。既定は公式実装の
`raspimouse`で、自前実装に替えるには`compose.original.yaml`を重ねます（Raspberry Pi 5では
必須。[Docker環境](../setup/docker.md#本体ドライバを自前実装に替える)）。

```bash
cd ~/daifuku_autonomous   # リポジトリを置いた場所
docker compose -f docker/raspberrypi/compose.yaml up -d
```

続いてセッションを作り、3つの窓に割り当てます。

```bash
cd ~/daifuku_autonomous
tmux new-session -d -s nav -c "$PWD" -n nav
tmux send-keys -t nav:nav 'docker compose -f docker/raspberrypi/compose.yaml exec ros2 /ros_entrypoint.sh ros2 launch daifuku_stack navigation.launch.py map:=/opt/ros_ws/install/share/daifuku_stack/maps/map_19f.yaml use_sim_time:=false localization:=emcl2 planner:=vi use_mid360_imu:=false' Enter

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

`use_mid360_imu:=false`は`raspicat`サービスに合わせた指定です。本体ドライバ
（`raspimouse`でも`raspicat_driver`でも）は`/odom`と`odom -> base_footprint`を自分で
配信し、`/wheel/odom`は出しません。既定の`true`のままだとEKFが入力を受け取れないうえ、
`/odom`とTFの配信元が二重になります。

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
ros2 launch daifuku_stack navigation.launch.py \
  map:=$PWD/src/daifuku_stack/maps/map_19f.yaml \
  use_sim_time:=false localization:=emcl2
```

2D LiDAR（raspicatのURGが起動します）:

```bash
ros2 launch daifuku_stack navigation.launch.py \
  map:=$PWD/src/daifuku_stack/maps/map_19f.yaml \
  use_sim_time:=false localization:=emcl2 lidar:=2d
```

RVizを同じ端末から開く場合は`use_rviz:=true`を渡します。

軽量Docker環境:

```bash
docker compose -f docker/raspberrypi/compose.yaml exec ros2 \
  /ros_entrypoint.sh ros2 launch daifuku_stack navigation.launch.py \
  map:=/opt/ros_ws/install/share/daifuku_stack/maps/map_19f.yaml \
  use_sim_time:=false localization:=emcl2
```

## 自己位置推定を選ぶ

Nav2標準AMCLへ変更する場合:

```bash
ros2 launch daifuku_stack navigation.launch.py \
  map:=$PWD/src/daifuku_stack/maps/map_19f.yaml \
  localization:=amcl
```

指定可能な値は`emcl2`（別名`emcl`）と`amcl`です。

## プランナを選ぶ

既定の`planner:=vi`は価値反復プランナを使います。`local_planner`も既定（`auto`）では`vi`になり、`vi_planner`1ノードが1本の価値関数で経路計画と経路追従の両方を担います。価値反復の計算はゴールごとに1回だけです。

NavFnとNav2 DWBへ切り替える場合:

```bash
ros2 launch daifuku_stack navigation.launch.py \
  map:=$PWD/src/daifuku_stack/maps/map_19f.yaml \
  planner:=navfn
```

グローバルは価値反復のまま、ローカルだけDWBへ変更する場合:

```bash
ros2 launch daifuku_stack navigation.launch.py \
  map:=$PWD/src/daifuku_stack/maps/map_19f.yaml \
  planner:=vi local_planner:=nav2
```

`local_planner:=auto`（既定）は、`planner:=vi`なら`vi`、`planner:=navfn`なら`nav2`を選びます。

`vi_planner`は既定でアウトオブコアソルバ（`frontier2d_sparse_compact`）で解きます。
状態配列を確保せず、確定した価値関数と方策だけを12バイト/状態で持つので、地図が
大きくなっても載ります。経路計画と経路追従はその確定出力を共有場として使い、追従が
スキャンから書いたペナルティを全域掃き（`global_sweep`、既定で有効）が広域の経路まで
広げます。掃きは追従中もバックグラウンドで回り、1コアの37%を使います（`config/nav2/`の
60:100。ノード既定の20:60なら25%）。実時間は起動ログの`global sweep done in ...`に
出ます。

`map_19f`では`map_scale: 2`でプランナ内部だけを0.10 m/セルに粗くしています（地図、
コストマップ、自己位置推定は0.05 mのままです）。solveと伝播を軽くするためで、必須では
ありません。密ソルバ（`frontier2d_sparse`）に戻すこともできますが、そちらは状態1つ
あたり80バイト要るので`map_scale: 2`とセットです（実測655 MB。`dense_limit_mb`を
超える地図では、確保してからOOMされる代わりに起動を止めます）。値の導出は
[`config/README.md`](../../src/daifuku_stack/config/README.md)にあります。

## 広域地図（map_tsudanuma）で動かす

`maps/map_tsudanuma.yaml`は5888×4000セル（0.05 m/セル、294.4 m×200 m）の広域地図です。
価値反復はゴールごとに`nx × ny × theta_cell_num`の状態空間を扱います。この地図を
0.05 mのまま解くと状態数は14.1億に達し、既定の密ソルバは状態配列だけで79 GBを要求
するため、起動と同時に落ちます。

`config/overrides/map_tsudanuma.yaml`を`overrides:=map_tsudanuma`で重ねると、プランナ内部だけが
0.25 m/セル（`map_scale: 5`、1178×800×60＝5650万状態）に粗くなり、状態配列を確保しない
アウトオブコアソルバ（`frontier2d_sparse_compact`）へ切り替わります。確定した価値関数と方策は
`compact_sink_dir`のmmapファイル（実測648 MB）に置かれます。地図サーバ、コストマップ、
自己位置推定は0.05 mのままです。

```bash
ros2 launch daifuku_stack navigation.launch.py \
  map:=$PWD/src/daifuku_stack/maps/map_tsudanuma.yaml \
  overrides:=map_tsudanuma \
  planner:=vi
```

`local_planner`は既定の`auto`（`planner:=vi`なので`vi`）でも`nav2`でも動きます。`vi_planner`と
`vi_global_planner`のどちらも`map_scale`とアウトオブコア経路を持つためです。`vi_planner`の
狭域追従だけは密な状態配列を必要とします。ただし全域ではなく、ロボット近傍のパッチだけを
`compact_sink_dir`のmmapファイルから起こして回します（±1 mウィンドウ＋遷移到達距離＋余裕。
0.25 mセルで27×27×60≒2.5 MB）。

狭域→広域のフィードバック（`global_sweep`）もこの地図で効きます。compactでは共有場が
状態配列ではなくmmapの確定出力なので、掃きは全域Gauss–Seidelではなく、そこを
タイル単位（更新する16セル角＋遷移到達距離だけの凍結境界）で起こして掃いて書き戻す
形になります。仕事量は地図の大きさではなく、値が実際に動く範囲に比例します。
**この地図で伝播にどれだけかかるかは未計測**です。2026-08-04にPi 5で見た限りでは、
静止したままでもタイル修復は240秒回ってなお`queued 29`のまま減りませんでした。窓に壁が
入っていれば毎tickペナルティが塗り直されるので、**走行中に`global sweep done in ...`は
出ません**（設計どおり）。動いているかは2秒ごとの`tile repair running for ...`のほうで
見てください。長すぎるようなら`global_sweep_budget_ms`と`global_sweep_idle_ms`の比を
変えますが、**上限を決めているのはCPUではなく追従ループと共有するMutex**です
（[`config/README.md`](../../src/daifuku_stack/config/README.md)の`global_sweep`の節）。

NavFnとDWBで動かす場合、`map_tsudanuma`の価値反復向け設定は要りませんが、
`overrides:=none`を渡してください。省略すると既定の`map_19f`が載り、この地図には
合わない19F用のEMCL2調整が適用されます。

```bash
ros2 launch daifuku_stack navigation.launch.py \
  map:=$PWD/src/daifuku_stack/maps/map_tsudanuma.yaml \
  overrides:=none \
  planner:=navfn
```

注意点:

- `map_scale: 5`は単独では効きません。`downsample_policy: optimistic`（ブロック内にfreeが
  1つでもあればfree）、`action_forward_m`、`goal_margin_radius`が
  セットで、1つでも欠けると波がゴール近傍で止まります。値は
  `config/overrides/map_tsudanuma.yaml`のコメントにそろえてあります。
- 保守的プーリング（障害物優先。`downsample_policy`の既定）だと通路のセル幅が価値反復の
  遷移分布（約2セル幅）を下回り、`map_scale`が4以上で波がゴール近傍から広がりません。
  楽観側は通路を細らせない代わりに、自由セルの境界が壁へ寄ります。
- 実測のfootprint（420×450 mm、外接円0.408 m）と`inflation_radius` 0.55 mで実際に通れるかは、
  経路ごとに確認してください。`map_scale: 5`で解けることを確かめた当時のコストマップは
  `robot_radius: 0.22`（`nav2_bringup`のyamlのまま）だったため、実機の条件はこれより
  厳しくなります。
- ピークRSSは`vi_planner`で実測1.60 GB（うちsinkのmmapが648 MB）です。
  `map_scale: 3`＋保守的プーリングだった頃の`vi_global_planner`の3.98 GBから下がり、
  Raspberry Pi 4の4 GBに収まります。`vi_global_planner`をこの`map_scale: 5`で
  測ってはいませんが、解像度もソルバも同じなので同程度になるはずです（**未計測**）。
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
`src/daifuku_stack/config/README.md`です。`simulator/docs/pi4_sim.md`にもPi 4相当での
走行記録がありますが、そちらは`map_scale: 3`＋保守的プーリングだった頃のものなので、
所要時間もメモリもここの値とは一致しません。

## ゴールを指定する

RVizで次の順に操作します。

1. 「2D Pose Estimate」で地図上の初期姿勢を設定
2. センサーデータと地図が重なることを確認
3. 「Nav2 Goal」で移動先を指定

自律移動中は緊急停止をすぐ操作できる状態を保ってください。

**「2D Goal Pose」ではゴールを出せません。** waypoint を足すための`/waypoint_pose`へ
付け替えてあります（次節）。押しても機体は動かず、エラーも出ません。単発のゴールは
上の「Nav2 Goal」を使ってください。

## Waypointを並べて巡回する

`WaypointManagerPanel`（`daifuku_waypoint_manager`）で、複数の通過点を並べて
`/follow_waypoints`へ投げられます。RVizの左に出ていない場合は
**Panels → Add New Panel**から追加します。

1. 「2D Goal Pose」で地図上をクリック＋ドラッグ。クリック位置が座標、ドラッグ方向が
   向きになり、パネルの一覧に1点ずつ増える
2. 「Move Up」「Move Down」で順番を、「Delete Selected」などで不要な点を整理
3. 「Start」で巡回開始、「Cancel」で停止

`daifuku_stack/waypoints/waypoints_tsudanuma.yaml`に津田沼の73点を置いてあります。
パネルの「Load YAML」で読みます（`map_19f`では座標が地図の外に出るため使えません）。

RVizのFixed Frameとwaypointの`frame_id`が一致している必要があります。ずれていると
追加も追加読み込みも拒否され、パネルのステータス行にだけ理由が出ます。

`nav2_waypoint_follower`が1点ずつ`navigate_to_pose`を呼ぶ形なので、点と点のあいだで
いったん止まります（停止時間は`config/nav2/behaviors.yaml`の
`waypoint_pause_duration`）。行けない点があっても巡回は続き、完了時に取りこぼした
点数がステータス行に出ます。

### 次の点を走行中に解いておく（`waypoint_prefetch`）

`planner:=vi`では、上の「いったん止まる」がポーズ時間だけでは済みません。VIは
ゴールごとに価値関数を解き直すので、**点が変わるたびに丸ごと1回のsolveが入り、その
間ずっと機体が止まっています**（実測で19Fが29秒、津田沼が87秒）。

`config/nav2/vi_planner.yaml`の`waypoint_prefetch`を`true`にすると、いまの点へ走って
いるあいだに次の点を別スレッドで解いておき、着いたらsolveを飛ばして受け取ります。
効いた回はログに出ます。

```
vi_planner: prefetched the value function for (12.30, -4.50) in 31.20s
vi_planner: path with 412 poses in 0.34s (solved_now=true, iters=0, prefetched)
```

順路を`/waypoints`（`nav_msgs/Path`、latch）へ出すのは
`daifuku_waypoint_manager`のパネルと`joy_teleop`（START+BACK）の2つです。
`/follow_waypoints`へ直接投げる経路と単発ゴールは順路が無いので対象外で、そのときも
**エラーは出ません**。効いているかは上のログで判断してください。

既定が`false`なのは代償があるためです。価値関数が同時に2つ生きるので、密ソルバなら
メモリが、compactならsinkのディスクが2倍要ります。津田沼のsinkは648MBなので2つで1.3GB、
Piの空き1.5GBに対しては危険側です。19Fは95MBなので余裕があります。solveのCPUも取られ
ます（追従の`try_lock`は邪魔しませんが、10Hzの制御周期がずれ得ます）。
**まだ実機でもpi4_simでも通していません。**

詳細は[`src/daifuku_waypoint_manager/README.md`](../../src/daifuku_waypoint_manager/README.md)。

## 価値反復の表示

`planner:=vi`では、新しいゴールの最初の計算で地図全体を解くため、地図サイズにより数秒から数十秒かかる場合があります。同じゴールへの再計画は価値関数キャッシュにより高速です。

`rviz/navigation.rviz`には次のOccupancyGrid表示があります。

- `/value_function`: 価値関数のθ=0スライス。計算途中も既定500 ms間隔で更新
- `/local_window_value`: 機体周辺±1 mの値。スキャン由来のペナルティと局所反復をリアルタイムに表示
  （`local_planner:=vi`のときのみ）

価値関数は1本しかないため、以前あった`/local_value_function`はありません。

表示にはRVizのMapを使い、Color Schemeを`costmap`にします。
