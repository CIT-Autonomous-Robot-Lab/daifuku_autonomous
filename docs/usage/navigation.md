# 自律移動

保存済み地図を読み込み、自己位置推定、経路計画、経路追従を起動します。

場所別の `navigation.<場所>.launch.py` は、すべてPCD地図を使うEMCL3Dが既定です。
19FのPCDは同梱です。[津田沼](../../src/daifuku_maps/maps/tsudanuma/README.md)と
[つくば2026](../../src/daifuku_maps/maps/tsukuba2026/README.md)は別途配置し、
[つくば2025](../../src/daifuku_maps/maps/tsukuba2025/README.md)は座標を合わせたPCDを
`map_pcd:=`で指定します。

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
tmux send-keys -t nav:nav 'docker compose exec ros2 /ros_entrypoint.sh ros2 launch daifuku_bringup navigation.19f.launch.py use_sim_time:=false' Enter

tmux new-window -t nav -c "$PWD" -n motor
tmux send-keys -t nav:motor 'bash tools/control.sh motor on'

tmux new-window -t nav -c "$PWD" -n check
tmux send-keys -t nav:check 'bash tools/control.sh status' Enter

tmux attach -t nav
```

| 窓 | 中身 | 操作 |
|---|---|---|
| `nav` | 価値反復プランナ（経路計画・追従）、emcl3d（自己位置推定）、地図サーバ | 終了は`Ctrl-C` |
| `motor` | モーター電源 | `Enter`で実行。止めるときは同じ窓で`control.sh stop` |
| `check` | ノードとトピックの確認 | |

`motor`の窓だけ`Enter`を送っていません。機体が動ける状態になる操作なので、周囲を
確認してから自分で実行してください。

`check`の窓では、起動後に次を確認します。

```bash
bash tools/control.sh ros topic hz /scan
bash tools/control.sh ros topic hz /odom
bash tools/control.sh ros node list
```

初期姿勢の設定とゴールの指定はRVizから行います。`docker/raspberrypi/`のイメージには
RVizが入っていないため、PC側の[GUI付き開発コンテナ](../setup/development-container.md)か
ネイティブ環境でRVizを開き、同じ`ROS_DOMAIN_ID`で接続してください。操作の順序は
[ゴールを指定する](#ゴールを指定する)にまとめています。

作業を終えるときは、モーター電源を切ってからセッションを閉じます。

```bash
bash tools/control.sh motor off
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

**LiDARのドライバの引数はnavigationのlaunchにはありません。** センサーを立てるのは
`raspicat`サービス（`robot.launch.py`）のほうで、`lidar`も`lidar_z`も
`publish_lidar_tf`もそちらの引数です。2D LiDAR構成にするならリポジトリルートの
`.env`に`LIDAR=2d`を書いて`docker compose up -d`してください（ネイティブ環境では
`robot.launch.py`へ`lidar:=2d`を渡します）。`lidar_z`の既定0.275はこの機体の
Mid-360の搭載高さ（接地面から275mm、2026-08-03実測）なので、機体を変えたら実測し
直してください。`use_rviz`の既定は`false`です。

津田沼で走らせるときは`navigation.tsudanuma.launch.py`を使います（[津田沼（EMCL3D）で動かす](#津田沼emcl3dで動かす)）。

## 基本起動

価値反復グローバル／ローカルプランナとMid-360が既定構成です。RVizは既定では
起動しません（実機がheadlessのため。PC側から開きます）。

**自己位置推定は19FがEMCL2、津田沼がEMCL3Dです。** `vi_planner`は`localizer: "external"`のまま
外部推定器の推定姿勢を読みます。

**地図も調整も渡しません。** 走らせる場所はlaunchの名前で決まり（`navigation.19f.launch.py`なら`19f`）、
`overrides`はその場所の`src/daifuku_config/sites/<場所>/overrides.yaml`に固定です。**地図はその
overrides自身が`site:`節で宣言します。**

```yaml
site:
  map:
    navigation:   19f/map_19f.yaml   # 経路計画（/map）。daifuku_maps の maps/ からの相対パス
    localization: 19f/map_19f.yaml   # 自己位置推定（/map_loc）
```

`site:`はパッケージ名の段に並ばない予約節で、「その場所そのものに付く値」の置き場です
（いまは地図だけ）。**overridesの名前は場所であって、地図のファイル名ではありません**
（`19f` / `tsudanuma`。`maps/`のフォルダ名と同じ）。地図を差し替えるならこの行を直します。
場所を変えるときは別の場所のlaunchを立てます（**機体は立て直しません**）
（[日常操作](operations.md#走らせる場所を切り替える)）。

### 地図は2枚

**経路計画と自己位置推定で別の地図を使えます**。
経路計画には入ってほしくない場所を手で塗り潰した地図を使い、自己位置推定には実測の
ままの地図を使う、という分け方が上流（[mugimaru_bringup](https://github.com/CIT-Autonomous-Robot-Lab/mugimaru_bringup)）の
運用です。1枚しか持てないと、塗り潰した壁をLiDARが見つけられずに自己位置が壊れるか、
経路が入ってほしくない場所へ引かれるかのどちらかになります。

| 役 | トピック | ノード | 読むもの | launch引数 |
|---|---|---|---|---|
| `navigation` | `/map` | `map_server` | `vi_planner` | `map:=` |
| `localization` | `/map_loc` | `map_server_loc` | `emcl2` | `map_loc:=` |

経路計画用の`/map`は`vi_planner`、自己位置推定用の`/map_loc`は`emcl2`が読みます。

**同じ地図で走らせるときも2行とも書きます。** 片方だけ書けるようにすると、書き忘れが
もう一方の地図で黙って走る形になるためです（起動時にエラーで止まります）。同梱の
`19f`は2行とも同じ地図、`tsudanuma`は別の地図です。

**2枚を別にする構成は`localization:=emcl2`または`emcl3d`で使います。** `amcl`は従来の
起動契約を保ち、異なる2枚を指定するとエラーにします。`localization:=vi`（VIOLA）は
`vi_planner`の地図の購読が1つしかないので、経路計画と同じ地図を使います。

**`localization:=vi`（VIOLA）では`map_server_loc`を立てません。**
同じVIノードが`/map`を使って推定し、`map→odom`を所有します。
既定のEMCL3DはPCDで推定し、`/map_loc`を読みません。それでも自己位置推定用の地図が
決まっていれば`map_server_loc`を立てて`/map_loc`を出します。RVizで実環境と見比べるためです。

RVizには`Map (navigation)`と`Map (localization)`の2つの表示があります。**既定でオンなのは
後者（`/map_loc`）です**——経路計画用の地図は手で壁を描き足したり回廊の外を塗り潰したり
してあるので、実環境と見比べるには実測のままの自己位置推定側のほうが読めます。同じ理由で
**自己位置がその場で回り出す症状を追うときもこちら**です（貫通しているスキャンと
突き合わせるべきなのは自己位置推定が使っている地図。[困ったとき](troubleshooting.md)）。
経路計画が「なぜそこを通らないか」を見たいときだけ`Map (navigation)`をオンにしてください。

**`localization:=amcl`も`/map_loc`を使います。** `daifuku_localization`が
自己位置推定用のmap serverとAMCLを起動します。VIでは`/map_loc`を出さないので、
RVizの表示は`Map (navigation)`へ切り替えます。

`map:=`を明示することもできますが、`site: map:`と別のファイルを指していると**起動時に
エラーで止まります**（別の場所の帯とEMCL2調整を載せたまま走るのを防ぐため）。承知の
うえで外部の地図を使う場合は、場所別入口ではなく
`navigation.vtc.launch.py overrides:=none map:=<外部地図> map_loc:=<外部地図>`を使います。
場所別入口に`overrides:=none`を渡して場所の固定を外すことはできません。
いずれも既定の地図へ落とさず、地図が決まらなければ起動エラーにします。

```bash
ros2 launch daifuku_bringup navigation.19f.launch.py \
  use_sim_time:=false
```

EMCL3DはMid-360の点群を使います。2D LiDARだけで走る場合は
`lidar:=2d localization:=emcl2`を明示します。RVizを同じ端末から開く場合は`use_rviz:=true`を渡します。

軽量Docker環境:

```bash
docker compose exec ros2 \
  /ros_entrypoint.sh ros2 launch daifuku_bringup navigation.19f.launch.py \
  use_sim_time:=false
```

## 自己位置推定を選ぶ

Nav2標準AMCLへ変更する場合:

```bash
ros2 launch daifuku_bringup navigation.19f.launch.py \
  localization:=amcl
```

指定可能な値は`emcl2`（別名`emcl`）と`emcl3d`と`amcl`と`vi`です。

### `localization:=emcl3d`（点群地図での6DoF推定）

外部パッケージ`emcl3d`（`NOPLAB/emcl3d`）を立てます。`/scan`ではなく、仰角フィルタ後・
2D投影前の`/livox/lidar_elevation`をPCDの点群地図と照合し、`map -> odom`のTFと
`/mcl_pose`を出します。`vi_planner`は`map -> odom -> base_footprint`のTFから姿勢を取得します。
地図は`overrides`の`site: map: pointcloud:`
（`19f`は`19f/map_19f.pcd`）か`map_pcd:=`で、経路計画用の`/map`は従来どおり2D地図です。
**PCDの座標が2D地図と揃っていないと、推定は合っていても経路がずれます**（19Fは未検証）。

```bash
ros2 launch daifuku_bringup navigation.19f.launch.py
```

`elevation_filter:=false`を指定すると、既定の購読先は生点群の`/livox/lidar`に戻ります。

### `localization:=vi`（プランナ内蔵の推定器）

`vi_planner`は2026-08-09の上流の更新から自己位置推定も持ちます（VIOLA）。これに
切り替えると**emcl2を立てず**、`map -> odom`も`vi_planner`が出します（`map_server`は
残ります）。

**使うかどうかはこの引数、どれを使うかは`localizer`**です。断片
（`src/daifuku_config/navigation/vi_planner.yaml`）の既定は`external`（＝外部の推定を読む）で、
そのまま`localization:=vi`を渡すと**起動時に止まります**。

**同梱の地図はいずれも`external`のままです。** 内蔵推定器を試すときだけ`localizer`を
下の値へ変更し、`localization:=vi`と組み合わせてください。

| 値 | 中身 |
| --- | --- |
| `adaptive` | 窓つきヒストグラムMCLの多重解像度版（belief窓は数MB）。観測が合わなくなると広域レベルへ広げて再定位するので、誘拐から戻れて未シードでも立ち上がる。**内蔵を使うならまずこれ** |
| `grid` | その1レベル版（要シード、再定位なし） |
| `belief` | 全地図にbeliefを持つ和積。未シードでも最初のスキャンからfree一様で立ち上がるが、窓がないぶん重い（VIと同じ格子＝`map_scale`後の全域に載る） |
| `viterbi` | 同じ場をmin-plusで回す変種。1観測183 msで追従の40 ms予算を超えるため実走行向きではない |

**「迷ったら分かる場所へ寄る」（`active_reloc`）は`localizer`だけでなく`solver`も選びます。**
判別点を出せるのは`adaptive` / `belief` / `viterbi`の3つ（2026-08-09の上流の更新で
`adaptive`だけではなくなった）ですが、アウトオブコアの解は単一ゴール専用なので、
`solver: "frontier2d_sparse_compact"`のまま`true`と書くと起動時に止まります。
19Fではemcl2への切り替えに伴い、`active_reloc`・`map_clear_from_scan`・`qmdp`の
上書きを外しました。`solver`は密（`frontier2d_sparse`）のまま維持しています。
`map_scale: 2`込みで実測655 MB、`waypoint_prefetch: true`で場が2本になり
**1.31 GB**なので、**Pi 4（4 GB）では先読みを外してください**。
密へ戻した経緯は[`src/daifuku_config/README.md`](../../src/daifuku_config/README.md)にあります。

```bash
# 試す場所の設定で localizer を adaptive などへ変更した場合のみ
ros2 launch daifuku_bringup navigation.19f.launch.py \
  localization:=vi
```

初期姿勢はRVizの**2D Pose Estimate**（`/initialpose`）で与えます。内蔵推定器にとって
`pose_topic`は「自己位置の連続入力」ではなく**手動シード**で、メッセージが来るたび
beliefを張り直すためです（`adaptive`と`belief`は未シードでも立ち上がります）。推定姿勢は
`/mcl_pose`ではなく`/viola_pose`に出ます。

逆向きの取り違え——`localizer`を内蔵にしたまま`localization:=emcl2`で立てる——も
起動時に止まります。通すと`map -> odom`の出し手が2つになり、さらに`pose_topic`
（`mcl_pose`）が20 Hzの手動シードとして読まれて、**エラーも警告も出ないまま自己位置
だけが壊れる**ためです。

## 経路計画と追従

`vi_planner`をstandaloneで起動し、1本の価値関数で経路計画と追従を担います。
`navigate_to_pose`と`follow_waypoints`も同じノードが提供します。
BTやNav2のコントローラは立てず、NavFn・DWBへの切り替え引数もありません。

速度平滑化は`velocity_smoother`と`lifecycle_manager_navigation`が担います。
`velocity_smoother:=false`なら両方を外し、VIが直接`cmd_vel`を出します。
地図サーバ、AMCL、ライフサイクル管理などの共有ユーティリティは引き続き使います。

```bash
ros2 launch daifuku_bringup navigation.19f.launch.py
ros2 launch daifuku_bringup navigation.19f.launch.py velocity_smoother:=false
```

投げ直しの前には3秒止まったままスキャンを取り込んで場を更新します。
上限は`src/daifuku_config/navigation/vi_planner.yaml`の`goal_retry_limit`です。
巡回の失敗時動作は同じファイルの`stop_on_failure`で設定します。

`vi_planner`は既定でアウトオブコアソルバ（`frontier2d_sparse_compact`）で解きます。
状態配列を確保せず、確定した価値関数と方策だけを12バイト/状態で持つので、地図が
大きくなっても載ります。経路計画と経路追従はその確定出力を共有場として使い、追従が
スキャンから書いたペナルティを全域伝播（`global_sweep`、既定で有効）が広域の経路まで
広げます。狭域・広域は独立したworkerで常時反復し、収束後も再確認します。
CPU配分は1コア基準で`local_cpu_share: 0.6`、`global_cpu_share: 0.2`です。
局所は有限の正値で1を超えると補助スレッドを使えます。広域は`0 < share <= 1`です。
実際のスレッドCPU時間で休止量を決め、制御周期やロック待ちは計算時間と区別します。
初回solve・先読み・自己位置推定・制御のCPU使用量はこの配分に含みません。

`19f`では`map_scale: 2`でプランナ内部だけを0.10 m/セルに粗くしています（地図、
自己位置推定は0.05 mのままです）。**この地図は2026-08-09に密ソルバ
（`frontier2d_sparse`）へ戻したので、`map_scale: 2`はいまは必須です**——`active_reloc`が
アウトオブコアを受け付けないためで、密は状態1つあたり80バイト要り、scale 1では2.53 GBに
なります（scale 2の実測655 MB。マシンの`MemAvailable`を
超える地図では、確保してからOOMされる代わりに起動を止めます。上限のキー`dense_limit_mb`は
2026-08-09の上流の整理で消え、`/proc/meminfo`の値そのものが基準になりました）。値の導出は
[`src/daifuku_config/README.md`](../../src/daifuku_config/README.md)にあります。

## 津田沼（EMCL3D）で動かす

`navigation.tsudanuma.launch.py`は自己位置推定の既定が`emcl3d`です。地図は3つ使います。

| 役 | ファイル（`daifuku_maps`の`maps/tsudanuma/`から） |
|---|---|
| 経路計画（`/map`） | `navigation/tsudanuma-challenge_nav_corridor.yaml` |
| 2D自己位置推定（`/map_loc`） | `localization/tsudanuma-challenge_loc.yaml`（`tsudanuma-challenge_loc3.pgm`） |
| EMCL3D | `map_tsudanuma_aligned.pcd` |

PCDはGitに含めません。取得と座標の整合（`tools/maps/align_tsudanuma_pcd.py`）は
[地図の説明](../../src/daifuku_maps/maps/tsudanuma/README.md)の手順で行います。
変換後のPCDが無いと起動前の地図検査で止まります。

```bash
ros2 launch daifuku_bringup navigation.tsudanuma.launch.py
```

起動後はRVizの「2D Pose Estimate」で初期位置と向きを与えます。実機では
`use_sim_time:=false`で、実機とPCの時計を同期しておきます。

計画地図は順路から生成した回廊です。順路を変えたら
`navigation/tsudanuma-challenge_nav.yaml`（`tsudanuma-challenge_nav3_9.pgm`）を元に
`tools/maps/corridor_map.py`で再生成し、`tools/maps/verify_{map_thresholds,corridor_orientation,waypoints}.py`で
自由セル・連結性・上下方向を検算します（PGMの行0は地図のy最大側）。
順路の半径下限と既知事項は[`src/daifuku_config/README.md`](../../src/daifuku_config/README.md)にあります。

価値反復は`map_scale: 2`で内部解像度0.20 mのcompactソルバを使い、確定出力は約711 MBです。
`waypoint_prefetch`が有効で場を2本持つため、**8 GB機が前提**です。4 GB機では
`src/daifuku_config/sites/tsudanuma/overrides.yaml`の`waypoint_prefetch`を`false`にしてください
（`overrides:=none`では自己位置推定の調整まで失います）。

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
ゴールを全部止めます。** パネルが持っていないゴール——RVizを立て直したあと、
「Nav2 Goal」の単発ゴール——もこれで止まります。詳細は
[`src/daifuku_waypoint_manager/README.md`](../../src/daifuku_waypoint_manager/README.md)。

`daifuku_maps/waypoints/`に津田沼の順路を版ごとに置いてあります
（`waypoints_tsudanuma_v1.0.yaml`と`v1.1`。回廊地図の座標系）。パネルの「Load YAML」で読みます（`19f`では座標が地図の外に
出るため使えません）。

RVizのFixed Frameとwaypointの`frame_id`が一致している必要があります。ずれていると
追加も追加読み込みも拒否され、パネルのステータス行にだけ理由が出ます。

点と点のあいだに人工的な待ち時間は入れず、到達後は次の点へ進みます。
到達範囲は`goal_margin_radius`と`goal_margin_theta`で指定します。
失敗時の動作は`src/daifuku_config/navigation/vi_planner.yaml`の
`stop_on_failure`で設定します。
`stop_on_failure: false`なら行けない点があっても巡回を続け、完了時に取りこぼした点数が
ステータス行に出ます。

### 次の点を走行中に解いておく（`waypoint_prefetch`）

次のゴールに一致するキャッシュや先読み結果がなければ、価値関数のsolve中は停止します。
solveの過去の実測は19Fが29秒、津田沼が87秒です。
キャッシュと先読みの照合は`(x, y, 整数度の向き)`の完全一致で、同じセル内でも
座標が異なれば再利用しません。向きは0以上360未満へ正規化し、整数度へ切り捨てます。

`src/daifuku_config/navigation/vi_planner.yaml`の`waypoint_prefetch`を`true`にすると、いまの点へ
走っているあいだに次の点を別スレッドで解いておき、着いたらsolveを飛ばして受け取ります。

**いま`true`なのは`19f`だけです。** 断片（`src/daifuku_config/navigation/vi_planner.yaml`）は
`false`で、それを上書きしているのは`sites/19f/overrides.yaml`だけです。津田沼は
2026-08-07に`true`にしたあと、**2026-08-08に`false`へ戻しました**——走行中の固まりの
容疑者を切り分けるためで、あちらは場が648 MB×2＝1.3 GBになります。2026-08-04にも一度
**断片**で`true`へ反転して同日の実機で固まりが出たため、容疑者の1つとして戻した経緯が
あります（切り分けは未了）。効いた回はログに出ます。

```
vi_planner: prefetched the value function for (12.30, -4.50) in 31.20s
vi_planner: path with 412 poses in 0.34s (solved_now=true, iters=0, prefetched)
```

順路は`/follow_waypoints`のゴールそのものが先読みへ渡ります。
効いているかは上のログで判断してください。

有効なぶん代償も常時払います。価値関数が同時に2つ生きるので、場も2つ要ります。
密ソルバではメモリがそのまま2倍です。compactでsinkがディスクへ出るのは
`compact_sink_dir`を指定したときと`compact_ram_limit_mb`を超えたときだけで、
**同梱の3地図はいまどれも出ません**。したがって場は丸ごとRAMに載ります——**19Fは
2026-08-09に`solver`を密へ戻したので、sinkの95 MB×2ではなく場そのものが
655 MB×2＝1.31 GB**です（密にはディスクへ逃がす口がありません）。津田沼は`true`へ
戻せば648 MB×2＝1.3 GBが匿名メモリとして居座ります。
solveのCPUも取られます（追従の`try_lock`は邪魔しませんが、10Hzの制御周期がずれ得ます）。
**Pi 4（4 GB）で走らせるなら`sites/19f/overrides.yaml`の`waypoint_prefetch`を`false`へ
戻してください**——既定の場所が`19f`なので、引数を何も足さずに立てるとPi 4でも
これが効きます（Pi 5の8 GBを前提にしている点は`compact_ram_limit_mb`と同じ事情です）。
走行中に固まるようになったときも、まずここを戻して切り分けます。
**まだ実機でもpi4_simでも通していません。**

詳細は[`src/daifuku_waypoint_manager/README.md`](../../src/daifuku_waypoint_manager/README.md)。

### 経路が引けた時点で走り出す（`early_start`）

先読みが「解く時刻を早める」のに対して、こちらは**解く量を減らす**手です。解いて
いるのは地図の全域ぶんの価値関数ですが、走り出すのに要るのは**いまの姿勢から
ゴールまでの経路**だけなので、それが引けた時点でsolveを打ち切れます。

`early_start`を`true`にすると打ち切ります（断片の`src/daifuku_config/navigation/vi_planner.yaml`は
`false`のままで、**同梱の3地図は`sites/<場所>/overrides.yaml`で`true`にしています**）。判定は
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
**走りながら**埋まっていきます（早期走り出しで未確定の場を渡されたときだけは起点が要るので、
密は地図を丸ごと種に積み、compactは丸ごと修復の待ち行列へ入れます）。

**効かない地図があります。** compact（断片の既定。2026-08-09に`19f`を密へ戻したので、
いまcompactなのは津田沼だけです）の確定は値バンド単位でしか進まず、
バンド幅は`4 × 1手で進む最大セル数 × 最大ペナルティ`です
（`frontier2d_sparse_compact.rs`の`couple_margin`）。`19f`の
0.1 m/セル・`action_forward_m` 0.5 m・`map_penalty: 30`なら
4×5×30＝600ステップ、1ステップ0.5 mなので**300 m相当**になります。**これは式に
値を入れただけで、実測ではありません**（バンド幅の実測は取っていません）。
地図の値域が丸ごと1バンドに収まると波2つで解き終わってしまい、打ち切る隙がありません。
このとき**エラーも警告も出ず、ただ何も短くなりません**。建物1フロア程度の広さは
こちら側の見込みで、効くのはそれより広い地図です。**19Fと津田沼では`true`にしてあります。
19Fは密へ戻したのでバンドが無くなり、効くようになりました**（上の600ステップの話は
compactだった頃のものです）。前進量とペナルティを下げても
`map_scale`を上げてもバンドは狭くなり、効きやすくなります。効いたかは上のログの`cut short` /
`truncated`で判断してください。

密ソルバ（`solver: "frontier2d_sparse"`に戻したとき）にはバンドが無いので、地図の
広さに依らず効きます。

**まだ実機でもpi4_simでも通していません。**

## 価値反復の表示

キャッシュのないゴールでは、地図サイズにより計算に数秒から数十秒かかる場合があります。
座標と整数度の向きが完全に一致するゴールへの再計画は、価値関数キャッシュを再利用します。

`rviz/navigation.rviz`には次のOccupancyGrid表示があります。

- `/value_function`: 価値関数のθ=0スライス。計算途中も既定500 ms間隔で更新
- `/local_window_value`: 機体周りの追従ウィンドウ（既定はカプセル、前後2.5 m・横2.0 m）の値。スキャン由来のペナルティと局所反復をリアルタイムに表示
  **2026-08-20から窓の形がそのまま見えます**——`local_shape`で
  矩形以外にすると、形の外は未知（-1）で出るためです
- `/belief`: VIOLAの自己位置belief（`localization:=vi`かつ`localizer`が`external`以外のときのみ）。**既定では表示オフ**——未収束のbeliefは自由空間ほぼ全域が非ゼロで、最前面に0.9の濃さで塗ると地図も価値関数も隠れるため。見るときはDisplaysでチェックを入れる

価値関数は1本しかないため、以前あった`/local_value_function`はありません。

表示にはRVizのMapを使い、Color Schemeを`costmap`にします。
