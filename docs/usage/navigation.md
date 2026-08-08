# 自律移動

保存済み地図を読み込み、自己位置推定、経路計画、経路追従を起動します。

## tmuxで一式を起動する

Raspberry Pi本体にSSHでつなぎ、保存済み地図で自律移動を始める手順です。そのまま
貼り付けて実行できます。tmuxの基本操作は[日常操作と確認](operations.md#tmuxで作業する)を
参照してください。

まずコンテナを起動します。`raspicat`サービスが機体ドライバを立ち上げます。どの実装になるかは
`.env`の`COMPOSE_FILE`で決まり、標準は自前実装の`compose.original.yaml`です（Raspberry Pi 5では
必須。公式実装に替えるには`compose.rt.yaml`。
[Docker環境](../setup/docker.md#本体ドライバを選ぶ)）。

```bash
cd ~/daifuku_autonomous   # リポジトリを置いた場所
docker compose up -d
```

続いてセッションを作り、3つの窓に割り当てます。

```bash
cd ~/daifuku_autonomous
tmux new-session -d -s nav -c "$PWD" -n nav
tmux send-keys -t nav:nav 'docker compose exec ros2 /ros_entrypoint.sh ros2 launch daifuku_stack navigation.launch.py use_sim_time:=false localization:=emcl2 planner:=vi' Enter

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

Mid-360のIMU融合（`use_mid360_imu`）は既定の`true`のまま使っています。`/odom`と
`odom -> base_footprint`を出すのはEKFで、`raspicat`サービスの本体ドライバは
`/wheel/odom`を出すだけです。**切り替えはリポジトリルートの`.env`の`USE_MID360_IMU`で**
行ってください（読むのは`raspicat`サービス1つだけです。変えたら
`docker compose up -d`）。**ドライバとEKFは同じlaunchが立てるので、片方だけ
切り替わる状態は作れません**
（[LiDARとオドメトリ](../setup/lidar.md#imuと車輪オドメトリ)）。

**起動時は機体を静止させておいてください。** Mid-360のジャイロの電源投入時バイアス
（実測+0.80 deg/s = 48 deg/min）を`prepare_mid360_imu`が測って引きます。

**LiDARの引数は`navigation.launch.py`にはありません。** センサーを立てるのは
`raspicat`サービス（`robot_bringup.launch.py`）のほうで、`lidar`も`lidar_z`も
`publish_lidar_tf`もそちらの引数です。2D LiDAR構成にするならリポジトリルートの
`.env`に`LIDAR=2d`を書いて`docker compose up -d`してください（ネイティブ環境では
`robot_bringup.launch.py`へ`lidar:=2d`を渡します）。`lidar_z`の既定0.275はこの機体の
Mid-360の搭載高さ（接地面から275mm、2026-08-03実測）なので、機体を変えたら実測し
直してください。`use_rviz`の既定は`false`です。

広域地図`map_tsudanuma`で走らせるときは`tools/site.sh map_tsudanuma`で場所ごと
切り替えます（[広域地図（map_tsudanuma）で動かす](#広域地図map_tsudanumaで動かす)）。

## 基本起動

EMCL2、価値反復グローバル／ローカルプランナ、Mid-360が既定構成です。RVizは既定では
起動しません（実機がheadlessのため。PC側から開きます）。

**地図も調整も渡しません。** 走らせる場所は`config/site`の1行（既定は`map_19f`）で、
`overrides`はその名前の`config/overrides/<名前>.yaml`になります。**地図はその
overrides自身が`site:`節で宣言します。**

```yaml
site:
  map: map_19f.yaml   # daifuku_stack の maps/ からの相対パス（絶対パスも可）
```

`site:`はパッケージ名の段に並ばない予約節で、「その場所そのものに付く値」の置き場です
（いまは地図だけ）。**overridesの名前と地図のファイル名は揃っていなくて構いません。**
地図を差し替えるならこの1行を直します。場所の切り替えは`tools/site.sh <名前>`で、
機体側（LiDARの帯）の立て直しまで含めて1コマンドです
（[日常操作](operations.md#走らせる場所を切り替える)）。

`map:=`を明示することもできますが、`site: map:`と別のファイルを指していると**起動時に
エラーで止まります**（別の場所の帯とEMCL2調整を載せたまま走るのを防ぐため）。承知の
うえでやるなら`overrides:=none`を添えてください（`ros2 launch`は値が空の`overrides:=`を
受け付けません）。**そのときは`map:=`が要ります**——場所を名乗っていない以上、地図は
決まりません。`site: map:`の無いoverridesを重ねたときも同じです。どちらも**既定の
地図へ落とさずエラーで止めます**。別の場所にいるのに19Fの地図で自己位置を推定し始める
ほうが危ないためです。

```bash
ros2 launch daifuku_stack navigation.launch.py \
  use_sim_time:=false localization:=emcl2
```

**コマンドはLiDARの構成によらず同じです。** `/scan`を出すのは機体側なので、
Mid-360でも2D LiDARでもこちらは変わりません。RVizを同じ端末から開く場合は
`use_rviz:=true`を渡します。

軽量Docker環境:

```bash
docker compose exec ros2 \
  /ros_entrypoint.sh ros2 launch daifuku_stack navigation.launch.py \
  use_sim_time:=false localization:=emcl2
```

## 自己位置推定を選ぶ

Nav2標準AMCLへ変更する場合:

```bash
ros2 launch daifuku_stack navigation.launch.py \
  localization:=amcl
```

指定可能な値は`emcl2`（別名`emcl`）と`amcl`です。

## プランナを選ぶ

既定の`planner:=vi`は価値反復プランナを使います。`local_planner`も既定（`auto`）では`vi`になり、`vi_planner`1ノードが1本の価値関数で経路計画と経路追従の両方を担います。価値反復の計算はゴールごとに1回だけです。

NavFnとNav2 DWBへ切り替える場合:

```bash
ros2 launch daifuku_stack navigation.launch.py \
  planner:=navfn nav2:=auto
```

グローバルは価値反復のまま、ローカルだけDWBへ変更する場合:

```bash
ros2 launch daifuku_stack navigation.launch.py \
  planner:=vi local_planner:=nav2 nav2:=auto
```

**どちらも`nav2:=auto`が要ります。** `nav2`の既定は`false`＝Nav2を立てない
（次節）で、これらの構成では`navigate_to_pose`を出すものが居なくなるため、
付け忘れると**起動時にエラーで止まります**（黙ってNav2を立て直したりはしません）。
`nav2:=auto`はプランナに連動して`true`/`false`を選び、`nav2:=true`は明示的に
Nav2を立てます。

`local_planner:=auto`（既定）は、`planner:=vi`なら`vi`、`planner:=navfn`なら`nav2`を選びます。

### Nav2を立てるかどうか（`nav2:=false`が既定）

**素で起動するとNav2のBTもコントローラも立ちません。**
`vi_planner`が`navigate_to_pose`と`follow_waypoints`も提供するので、
`bt_navigator`・`behavior_server`・`smoother_server`・`waypoint_follower`が
不要になります。残るのは`velocity_smoother`と、それを起こすためだけの
`lifecycle_manager_navigation`（管理下は1ノード）で、`velocity_smoother:=false`に
するとどちらも消えます。アクション型は`nav2_msgs`のままなので、
RVizの「Nav2 Goal」もパネルもパッドも、操作は何も変わりません。

```bash
# 何も付けなければ Nav2 抜き（vi_planner 1 ノード）
ros2 launch daifuku_stack navigation.launch.py
ros2 launch daifuku_stack navigation.launch.py \
  nav2:=true                     # 従来どおりBTを挟む構成に戻す
ros2 launch daifuku_stack navigation.launch.py \
  velocity_smoother:=false       # 残る唯一のlifecycleノードも外す
```

`nav2:=false`が成り立つのは`planner:=vi`かつ狭域も`vi`に解決されるときだけです。
`planner:=navfn`や`local_planner:=nav2`と組み合わせると`navigate_to_pose`を出す
ものが居なくなるので、**起動時にエラーで止まります**（`nav2:=auto`か`nav2:=true`を
足してください。前節）。黙ってNav2を立て直さないのは、「立てないつもりだったのに
立っていた」が起動ログを1行ずつ読むまで気付けないためです。

BTを外すと、VIが損をしていた点が消えます。**毎秒の再計画が無くなり**（BTは
`ComputePathToPose`を1 Hzで呼び、キャッシュヒットでもロールアウトを共有ロックの中で
回していました。10 Hzの追従ループはそれと毎秒取り合っていました）、**経路が引けなくても
ゴールが死ななくなり**（ロールアウトの振動＝`LoopDetected`は「方策が無い」とは別物です）、
**投げ直しの待ちに意味が出ます**。最後のが一番効きます — BTの`Wait`のあいだは
`follow_path`が走っていないので価値関数が1ミリも動かず、待っても状況が変わりません。
`nav2:=false`では`goal_retry_settle_sec`（既定3秒）のあいだ**止まったままスキャンを
取り込み続ける**ので、一度「通れない」と塗った場所のペナルティが実際に薄れていきます。
投げ直しの上限は`goal_retry_limit`（既定3、負で無制限）です。

読む設定ファイルも減ります。効くのは`config/stack/nav2/vi_planner.yaml`と`map_server.yaml`、
`config/stack/localization/emcl2.yaml`、それに`behaviors.yaml`の`velocity_smoother`の節だけです。
`bt_navigator.yaml`・`controller_server.yaml`・`costmaps.yaml`と`behaviors.yaml`の
残り3ノード分、`behavior_trees/`は**合成には入るが宛先の
ノードが立たないので黙って無視されます**。`behaviors.yaml`の`waypoint_follower`にあった
`stop_on_failure`と`waypoint_pause_duration`に当たるものは`vi_planner.yaml`の
`stop_on_failure`と`waypoint_pause_sec`です。詳しくは
[architecture.md](architecture.md#nav2を立てない構成nav2false)。

`vi_planner`は既定でアウトオブコアソルバ（`frontier2d_sparse_compact`）で解きます。
状態配列を確保せず、確定した価値関数と方策だけを12バイト/状態で持つので、地図が
大きくなっても載ります。経路計画と経路追従はその確定出力を共有場として使い、追従が
スキャンから書いたペナルティを全域掃き（`global_sweep`、既定で有効）が広域の経路まで
広げます。掃きは追従中もバックグラウンドで回り、1コアの25%を使います（20:60）。
2026-08-04に60:100（37%）へ上げたところ機体が1〜2秒おきに固まったため戻しました。
実時間は起動ログの`global sweep done in ...`に出ます。

`map_19f`では`map_scale: 2`でプランナ内部だけを0.10 m/セルに粗くしています（地図、
コストマップ、自己位置推定は0.05 mのままです）。solveと伝播を軽くするためで、必須では
ありません。密ソルバ（`frontier2d_sparse`）に戻すこともできますが、そちらは状態1つ
あたり80バイト要るので`map_scale: 2`とセットです（実測655 MB。`dense_limit_mb`を
超える地図では、確保してからOOMされる代わりに起動を止めます）。値の導出は
[`config/README.md`](../../config/README.md)にあります。

## 広域地図（map_tsudanuma）で動かす

`maps/map_tsudanuma.yaml`は5888×4000セル（0.05 m/セル、294.4 m×200 m）の広域地図です。
価値反復はゴールごとに`nx × ny × theta_cell_num`の状態空間を扱います。この地図を
0.05 mのまま解くと状態数は14.1億に達し、既定の密ソルバは状態配列だけで79 GBを要求
するため、起動と同時に落ちます。

`config/overrides/map_tsudanuma.yaml`を`overrides:=map_tsudanuma`で重ねると、プランナ内部だけが
0.25 m/セル（`map_scale: 5`、1178×800×60＝5650万状態）に粗くなり、状態配列を確保しない
アウトオブコアソルバ（`frontier2d_sparse_compact`）へ切り替わります。確定した価値関数と方策
（実測648 MB）はRAMに置かれます。地図サーバ、コストマップ、自己位置推定は0.05 mのままです。

置き場を決めるのは`compact_sink_dir`と`compact_ram_limit_mb`で、**判定は明示指定が
無条件に優先**です。`compact_sink_dir`が空でなければそのディレクトリへmmapし、上限は
読みません。空のときだけ上限と比べ、超えていれば`/tmp/vi_planner_sink`へ逃がします。
この地図は2026-08-04まで`compact_sink_dir`を明示していましたが、Raspberry Pi 5（8 GB、
走行中の実測で空き5.6 GB）では逃がす理由がないため外しました。いまは断片の
`compact_ram_limit_mb: 4096`に648 MBが収まることでRAMに載っています。**4 GB機で使うなら
`compact_sink_dir`を戻してください。**

```bash
tools/site.sh map_tsudanuma   # 場所を切り替える（機体も立て直す）
ros2 launch daifuku_stack navigation.launch.py \
  planner:=vi
```

`local_planner`は既定の`auto`（`planner:=vi`なので`vi`）でも`nav2`でも動きます。どちらも同じ
`vi_planner`で、`map_scale`もアウトオブコア経路も同じだからです（`nav2`は`follow: false`）。`vi_planner`の
狭域追従だけは密な状態配列を必要とします。ただし全域ではなく、ロボット近傍のパッチだけを
確定出力（sink）から起こして回します（±1 mウィンドウ＋遷移到達距離＋余裕。
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
（[`config/README.md`](../../config/README.md)の`global_sweep`の節）。

NavFnとDWBで動かす場合、`map_tsudanuma`の価値反復向け設定は要りません。ただし
`overrides:=none`はEMCL2の調整も一緒に落とすので、**ふつうは場所を切り替えたまま
`planner:=navfn`だけを渡してください**（VI向けの節は`vi_planner`宛で、
それらが立たない構成では宛先が無いだけです）。どうしても何も重ねたくないときは
`map:=`を自分で渡します——`overrides:=none`は場所を名乗らないので、地図を導けません。

```bash
tools/site.sh map_tsudanuma
ros2 launch daifuku_stack navigation.launch.py \
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
  `map_scale: 3`＋保守的プーリングだった頃の広域専用ノード`vi_global_planner`の3.98 GBから
  下がり、Raspberry Pi 4の4 GBに収まります（そのノードは2026-08-08の上流の整理で消え、
  広域だけの構成も同じ`vi_planner`になりました）。
  **ただしこれはsinkをディスクへ逃がしていた頃の値です。** RAM出力にした2026-08-04
  以降は同じ648 MBが匿名メモリになり、カーネルが追い出せません。上の「Pi 4の4 GBに
  収まる」はもう成り立たない前提です（RAM化後のピークは**未計測**）。
  Pi 5では走行中の実測で`vi_planner`のRSSが931 MB、コンテナのピークが2.12 GiB、
  `oom_kill`は0でした。
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
`config/README.md`です。`simulator/docs/pi4_sim.md`にもPi 4相当での
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

**「Cancel」はいつでも押せて、`/follow_waypoints`と`/navigate_to_pose`の
ゴールを全部止めます。** パネルから始めていない巡回——ゲームパッドのSTART+BACK、
RVizを立て直したあと、「Nav2 Goal」の単発ゴール——もこれで止まります。詳細は
[`src/daifuku_waypoint_manager/README.md`](../../src/daifuku_waypoint_manager/README.md)。

`daifuku_stack/waypoints/waypoints_tsudanuma.yaml`に津田沼の73点を置いてあります。
パネルの「Load YAML」で読みます（`map_19f`では座標が地図の外に出るため使えません）。

RVizのFixed Frameとwaypointの`frame_id`が一致している必要があります。ずれていると
追加も追加読み込みも拒否され、パネルのステータス行にだけ理由が出ます。

点と点のあいだではいったん止まります。停止時間は既定（`nav2:=false`）では
`config/stack/nav2/vi_planner.yaml`の`waypoint_pause_sec`（0.2秒。この間も価値関数の更新は
続きます）、`nav2:=true`では`config/stack/nav2/behaviors.yaml`の`waypoint_pause_duration`
（200 ms）です。行けない点があっても巡回は続き、完了時に取りこぼした点数がステータス行に
出ます（`stop_on_failure: false`。これも構成ごとに置き場が違い、既定では
`vi_planner.yaml`の側です）。

### 次の点を走行中に解いておく（`waypoint_prefetch`）

`planner:=vi`では、上の「いったん止まる」がポーズ時間だけでは済みません。VIは
ゴールごとに価値関数を解き直すので、**点が変わるたびに丸ごと1回のsolveが入り、その
間ずっと機体が止まっています**（実測で19Fが29秒、津田沼が87秒）。

`config/stack/nav2/vi_planner.yaml`の`waypoint_prefetch`を`true`にすると、いまの点へ
走っているあいだに次の点を別スレッドで解いておき、着いたらsolveを飛ばして受け取ります。

**いま`true`なのは`map_19f`だけです。** 断片（`config/stack/nav2/vi_planner.yaml`）は
`false`で、それを上書きしているのは`overrides/map_19f.yaml`だけです。津田沼は
2026-08-07に`true`にしたあと、**2026-08-08に`false`へ戻しました**——走行中の固まりの
容疑者を切り分けるためで、あちらは場が648 MB×2＝1.3 GBになります。2026-08-04にも一度
**断片**で`true`へ反転して同日の実機で固まりが出たため、容疑者の1つとして戻した経緯が
あります（切り分けは未了）。効いた回はログに出ます。

```
vi_planner: prefetched the value function for (12.30, -4.50) in 31.20s
vi_planner: path with 412 poses in 0.34s (solved_now=true, iters=0, prefetched)
```

既定の`nav2:=false`では、順路は`/follow_waypoints`のゴールそのものが先読みへ渡ります。
`nav2:=true`だけは違い、順路を`/waypoints`（`nav_msgs/Path`、latch）へ出す
`daifuku_waypoint_manager`のパネルか`joy_teleop`（START+BACK）を通った巡回でしか
効きません。そちらで`/follow_waypoints`へ直接投げた場合と単発ゴールは順路が無いので
対象外で、そのときも**エラーは出ません**。効いているかは上のログで判断してください。

有効なぶん代償も常時払います。価値関数が同時に2つ生きるので、場も2つ要ります。
密ソルバではメモリがそのまま2倍です。compactでsinkがディスクへ出るのは
`compact_sink_dir`を指定したときと`compact_ram_limit_mb`を超えたときだけで、
**同梱の2地図はいまどちらも出ません**。したがって2つとも丸ごとRAMに載ります
（津田沼648 MB×2＝1.3 GB、19F 95 MB×2）。津田沼を巡回するなら、その1.3 GBが
匿名メモリとして居座ることになります。solveのCPUも取られます（追従の`try_lock`は
邪魔しませんが、10Hzの制御周期がずれ得ます）。**Pi 4（4 GB）で走らせるなら、使う
地図の`overrides`（`map_19f.yaml` / `map_tsudanuma.yaml`）の`waypoint_prefetch`を
`false`へ戻してください**（Pi 5の8 GBを前提にしている点は`dense_limit_mb`・
`compact_ram_limit_mb`と同じ事情です）。
走行中に固まるようになったときも、まずここを戻して切り分けます。
**まだ実機でもpi4_simでも通していません。**

詳細は[`src/daifuku_waypoint_manager/README.md`](../../src/daifuku_waypoint_manager/README.md)。

### 経路が引けた時点で走り出す（`early_start`）

先読みが「解く時刻を早める」のに対して、こちらは**解く量を減らす**手です。解いて
いるのは地図の全域ぶんの価値関数ですが、走り出すのに要るのは**いまの姿勢から
ゴールまでの経路**だけなので、それが引けた時点でsolveを打ち切れます。

`config/stack/nav2/vi_planner.yaml`の`early_start`を`true`にすると打ち切ります。判定は
ロールアウトそのもの（`compute_path_to_pose`が返すのと同じ辿り方）なので、
**打ち切った場でも経路は必ず引けます**。先読みとは別物なので、両方同時に有効に
できます。

```
vi_planner: value function solved in 11.40s (iters=93), cut short at the first path to the goal [follow_path]
vi_planner: path with 388 poses in 0.31s (solved_now=true, iters=93, truncated)
```

代償は**経路の外が未確定のまま残る**ことです。機体が経路から外れて方策が引けなく
なると、打ち切った場を捨てて最後まで解き直します。このとき**機体は走行中に止まった
まま**フルのsolveを待つので（津田沼なら87秒）、打ち切らなかった場合より待ちは長く
なります。

```
vi_planner: dropped the truncated value function (early_start) after 30 ticks without an action; the next request solves it to convergence
```

外れなければ解き直しは要りません。`global_sweep: true`（既定）なら、打ち切った残りは
**走りながら**埋まっていきます（密は全域掃き、compactは追従が窓を書き戻すたびに積まれる
タイル修復）。

**効かない地図があります。** compact（同梱の既定）の確定は値バンド単位でしか進まず、
バンド幅は`4 × 1手で進む最大セル数 × 最大ペナルティ`です
（`frontier2d_sparse_compact.rs`の`couple_margin`）。`map_19f`の
0.1 m/セル・`action_forward_m` 0.5 m・`safety_radius_penalty: 30`なら
4×5×30＝600ステップ、1ステップ0.5 mなので**300 m相当**になります。**これは式に
値を入れただけで、実測ではありません**（バンド幅の実測は取っていません）。
地図の値域が丸ごと1バンドに収まると波2つで解き終わってしまい、打ち切る隙がありません。
このとき**エラーも警告も出ず、ただ何も短くなりません**。建物1フロア程度の広さは
こちら側の見込みで、効くのは津田沼のような広域地図です。前進量とペナルティを下げても
`map_scale`を上げてもバンドは狭くなり、効きやすくなります（津田沼が
`safety_radius_penalty: 1`なのは別の理由——貪欲ロールアウトが降下できないため——ですが、
バンドもそのぶん狭くなります）。効いたかは上のログの`cut short` /
`truncated`で判断してください。

密ソルバ（`solver: "frontier2d_sparse"`に戻したとき）にはバンドが無いので、地図の
広さに依らず効きます。

**まだ実機でもpi4_simでも通していません。**

## 価値反復の表示

`planner:=vi`では、新しいゴールの最初の計算で地図全体を解くため、地図サイズにより数秒から数十秒かかる場合があります。同じゴールへの再計画は価値関数キャッシュにより高速です。

`rviz/navigation.rviz`には次のOccupancyGrid表示があります。

- `/value_function`: 価値関数のθ=0スライス。計算途中も既定500 ms間隔で更新
- `/local_window_value`: 機体周辺±1 mの値。スキャン由来のペナルティと局所反復をリアルタイムに表示
  （`local_planner:=vi`のときのみ）

価値関数は1本しかないため、以前あった`/local_value_function`はありません。

表示にはRVizのMapを使い、Color Schemeを`costmap`にします。
