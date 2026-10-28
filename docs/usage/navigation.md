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
tmux send-keys -t nav:nav 'docker compose exec ros2 /ros_entrypoint.sh ros2 launch daifuku_bringup navigation.19f.launch.py use_sim_time:=false localization:=emcl2' Enter

tmux new-window -t nav -c "$PWD" -n motor
tmux send-keys -t nav:motor 'bash tools/control.sh motor on'

tmux new-window -t nav -c "$PWD" -n check
tmux send-keys -t nav:check 'bash tools/control.sh status' Enter

tmux attach -t nav
```

| 窓 | 中身 | 操作 |
|---|---|---|
| `nav` | 価値反復プランナ（経路計画・追従）、emcl2（自己位置推定）、地図サーバ | 終了は`Ctrl-C` |
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

広域地図`tsudanuma`で走らせるときは`navigation.tsudanuma.launch.py`を使います（[広域地図（map_tsudanuma）で動かす](#広域地図map_tsudanumaで動かす)）。

## 基本起動

価値反復グローバル／ローカルプランナとMid-360が既定構成です。RVizは既定では
起動しません（実機がheadlessのため。PC側から開きます）。

**自己位置推定は19Fも津田沼もEMCL2です。** `localization:=emcl2`がlaunchの既定で、
`vi_planner`は`localizer: "external"`のままemcl2の推定姿勢を読みます。

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
`19f`と`tsudanuma`は2行とも同じ地図です。

**2枚を別にする構成は`localization:=emcl2`で使います。** `amcl`は従来の
起動契約を保ち、異なる2枚を指定するとエラーにします。`localization:=vi`（VIOLA）は
`vi_planner`の地図の購読が1つしかないので、経路計画と同じ地図を使います。

**`localization:=vi`（VIOLA）では`map_server_loc`を立てません。**
同じVIノードが`/map`を使って推定し、`map→odom`を所有します。
同梱の`19f`・`tsudanuma`・`tsudanuma_mugimaru`はemcl2を使うので、
いずれも自己位置推定には`/map_loc`を使います。

RVizには`Map (navigation)`と`Map (localization)`の2つの表示があります。**既定でオンなのは
後者（`/map_loc`）です**——経路計画用の地図は手で壁を描き足したり回廊の外を塗り潰したり
してあるので、実環境と見比べるには実測のままの自己位置推定側のほうが読めます。同じ理由で
**自己位置がその場で回り出す症状を追うときもこちら**です（貫通しているスキャンと
突き合わせるべきなのは自己位置推定が使っている地図。[困ったとき](troubleshooting.md)）。
経路計画が「なぜそこを通らないか」を見たいときだけ`Map (navigation)`をオンにしてください。

**`localization:=amcl`も`/map_loc`を使います。** `daifuku_localization`が
自己位置推定用のmap serverとAMCLを起動します。`vi`では`/map_loc`を出さないので、
RVizの表示は`Map (navigation)`へ切り替えます。

`map:=`を明示することもできますが、`site: map:`と別のファイルを指していると**起動時に
エラーで止まります**（別の場所の帯とEMCL2調整を載せたまま走るのを防ぐため）。承知の
うえで外部の地図を使う場合は、場所別入口ではなく
`navigation.vtc.launch.py overrides:=none map:=<外部地図> map_loc:=<外部地図>`を使います。
場所別入口に`overrides:=none`を渡して場所の固定を外すことはできません。
いずれも既定の地図へ落とさず、地図が決まらなければ起動エラーにします。

```bash
ros2 launch daifuku_bringup navigation.19f.launch.py \
  use_sim_time:=false localization:=emcl2
```

**コマンドはLiDARの構成によらず同じです。** `/scan`を出すのは機体側なので、
Mid-360でも2D LiDARでもこちらは変わりません。RVizを同じ端末から開く場合は
`use_rviz:=true`を渡します。

軽量Docker環境:

```bash
docker compose exec ros2 \
  /ros_entrypoint.sh ros2 launch daifuku_bringup navigation.19f.launch.py \
  use_sim_time:=false localization:=emcl2
```

## 自己位置推定を選ぶ

Nav2標準AMCLへ変更する場合:

```bash
ros2 launch daifuku_bringup navigation.19f.launch.py \
  localization:=amcl
```

指定可能な値は`emcl2`（別名`emcl`）と`emcl3d`と`amcl`と`vi`です。

### `localization:=emcl3d`（点群地図での6DoF推定）

emcl2の代わりに外部パッケージ`emcl3d`（`NOPLAB/emcl3d`）を立てます。`/scan`ではなく
`/livox/lidar`の3D点群をPCDの点群地図と照合し、`map -> odom`と`mcl_pose`を出します
（`vi_planner`はそのまま`mcl_pose`を読む）。地図は`overrides`の`site: map: pointcloud:`
（`19f`は`19f/map_19f.pcd`）か`map_pcd:=`で、経路計画用の`/map`は従来どおり2D地図です。
**PCDの座標が2D地図と揃っていないと、推定は合っていても経路がずれます**（19Fは未検証）。

```bash
ros2 launch daifuku_bringup navigation.19f.launch.py localization:=emcl3d
```

### `localization:=vi`（プランナ内蔵の推定器）

`vi_planner`は2026-08-09の上流の更新から自己位置推定も持ちます（VIOLA）。これに
切り替えると**emcl2を立てず**、`map -> odom`も`vi_planner`が出します（`map_server`は
残ります）。

**使うかどうかはこの引数、どれを使うかは`localizer`**です。断片
（`src/daifuku_config/navigation/vi_planner.yaml`）の既定は`external`（＝外部の推定を読む）で、
そのまま`localization:=vi`を渡すと**起動時に止まります**。

**同梱の3地図はいずれも`external`のままです。** 19Fの`belief`上書きも外してあり、
通常は`localization:=emcl2`で起動します。内蔵推定器を試すときだけ`localizer`を
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
巡回の`stop_on_failure`と`waypoint_pause_sec`も同じファイルで設定します。

`vi_planner`は既定でアウトオブコアソルバ（`frontier2d_sparse_compact`）で解きます。
状態配列を確保せず、確定した価値関数と方策だけを12バイト/状態で持つので、地図が
大きくなっても載ります。経路計画と経路追従はその確定出力を共有場として使い、追従が
スキャンから書いたペナルティを全域伝播（`global_sweep`、既定で有効）が広域の経路まで
広げます。掃きは追従中もバックグラウンドで回り、1コアの25%を使います（`global_sweep_duty: 25`。
2026-08-09の上流の整理まで`global_sweep_budget_ms`と`global_sweep_idle_ms`の20:60でした）。
2026-08-04に37%相当へ上げたところ機体が1〜2秒おきに固まったため戻しました。
実時間は起動ログの`global propagation settled in ...`に出ます。

`19f`では`map_scale: 2`でプランナ内部だけを0.10 m/セルに粗くしています（地図、
自己位置推定は0.05 mのままです）。**この地図は2026-08-09に密ソルバ
（`frontier2d_sparse`）へ戻したので、`map_scale: 2`はいまは必須です**——`active_reloc`が
アウトオブコアを受け付けないためで、密は状態1つあたり80バイト要り、scale 1では2.53 GBに
なります（scale 2の実測655 MB。マシンの`MemAvailable`を
超える地図では、確保してからOOMされる代わりに起動を止めます。上限のキー`dense_limit_mb`は
2026-08-09の上流の整理で消え、`/proc/meminfo`の値そのものが基準になりました）。値の導出は
[`src/daifuku_config/README.md`](../../src/daifuku_config/README.md)にあります。

## 広域地図（map_tsudanuma）で動かす

`maps/tsudanuma/map_tsudanuma.yaml`は5888×4000セル（0.05 m/セル、294.4 m×200 m）の広域地図です。
価値反復はゴールごとに`nx × ny × theta_cell_num`の状態空間を扱います。この地図を
0.05 mのまま解くと状態数は14.1億に達し、既定の密ソルバは状態配列だけで79 GBを要求
するため、起動と同時に落ちます。

`src/daifuku_config/sites/tsudanuma/overrides.yaml`を`overrides:=tsudanuma`で重ねると、プランナ内部だけが
0.25 m/セル（`map_scale: 5`、1178×800×60＝5650万状態）に粗くなり、状態配列を確保しない
アウトオブコアソルバ（`frontier2d_sparse_compact`）へ切り替わります。確定した価値関数と方策
（実測648 MB）はRAMに置かれます。地図サーバと自己位置推定は0.05 mのままです。

置き場を決めるのは`compact_sink_dir`と`compact_ram_limit_mb`で、**判定は明示指定が
無条件に優先**です。`compact_sink_dir`が空でなければそのディレクトリへmmapし、上限は
読みません。空のときだけ上限と比べ、超えていれば`/tmp/vi_planner_sink`へ逃がします。
この地図は2026-08-04まで`compact_sink_dir`を明示していましたが、Raspberry Pi 5（8 GB、
走行中の実測で空き5.6 GB）では逃がす理由がないため外しました。いまは断片の
`compact_ram_limit_mb: 4096`に648 MBが収まることでRAMに載っています。**4 GB機で使うなら
`compact_sink_dir`を戻してください。**

```bash
ros2 launch daifuku_bringup navigation.tsudanuma.launch.py
```

`vi_planner`の狭域追従は、全域ではなくロボット近傍のパッチだけを
確定出力（sink）から起こします。実寸は起動ログの`vi_planner: follow patch ...`に
MBで出るので、`local_xy_range`や`local_shape`を変更したら確認してください。

狭域→広域のフィードバック（`global_sweep`）もこの地図で効きます。compactでは共有場が
状態配列ではなくmmapの確定出力なので、伝播の粒度がセルではなく
タイル（更新する16セル角＋遷移到達距離だけの凍結境界）になり、そこを起こして掃いて書き戻す
形になります。仕事量が地図の大きさではなく値が実際に動く範囲に比例するのは、密でも
compactでも同じです（2026-08-20の上流の更新まで、密だけが地図を丸ごと掃いていました）。
**この地図で伝播にどれだけかかるかは未計測**です。2026-08-04にPi 5で見た限りでは、
静止したままでもタイル修復は240秒回ってなお`queued 29`のまま減りませんでした（**2026-08-20の
上流の更新より前の観測**です。あの更新まではフロンティア系ソルバが値の「下降」しか伝播せず、
ペナルティで上がった値は黙って止まっていたので、この固着はそれで説明がつく側にあります）。窓に壁が
入っていれば毎tickペナルティが塗り直されるので、**走行中に`global propagation settled in ...`は
出ません**（設計どおり）。動いているかは2秒ごとの`global propagation running for ...`のほうで
見てください（`... (412 tiles done, 27 queued)`。密なら単位は`cells`で、こちらも
2026-08-20から同じ行が出ます）。長すぎるようなら`global_sweep_duty`（既定25 %）を上げますが、**上限を決めているのはCPUではなく追従ループと共有するMutex**です
（[`src/daifuku_config/README.md`](../../src/daifuku_config/README.md)の`global_sweep`の節）。

注意点:

- `map_scale: 5`は単独では効きません。`downsample_policy: optimistic`（ブロック内にfreeが
  1つでもあればfree）、`action_forward_m`、`goal_margin_radius`が
  セットで、1つでも欠けると波がゴール近傍で止まります。値は
  `src/daifuku_config/sites/tsudanuma/overrides.yaml`のコメントにそろえてあります。
- 保守的プーリング（障害物優先。`downsample_policy`の既定）だと通路のセル幅が価値反復の
  遷移分布（約2セル幅）を下回り、`map_scale`が4以上で波がゴール近傍から広がりません。
  楽観側は通路を細らせない代わりに、自由セルの境界が壁へ寄ります。
- 車体寸法と障害物回避の余裕が実際の経路で足りるか確認してください。
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
- この地図は68.2%が未観測セルで、占有セルは0.4%しかありません。EMCL2やAMCLの
  スキャンマッチングは占有セルの尤度場に依存するため、現状では自己位置推定の
  拠り所がほとんどありません（経路計画とは別の課題です）。

実測値の出どころは`src/daifuku_config/sites/tsudanuma/overrides.yaml`のヘッダと
[`src/daifuku_config/README.md`](../../src/daifuku_config/README.md)です。

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

`daifuku_maps/waypoints/`に津田沼の順路を版ごとに置いてあります（`v1.0`と`v1.1`が
73点、`v1.2`が69点）。パネルの「Load YAML」で読みます（`19f`では座標が地図の外に
出るため使えません）。

RVizのFixed Frameとwaypointの`frame_id`が一致している必要があります。ずれていると
追加も追加読み込みも拒否され、パネルのステータス行にだけ理由が出ます。

点と点のあいだの停止時間は`src/daifuku_config/navigation/vi_planner.yaml`の
`waypoint_pause_sec`で設定します。この間も価値関数の更新は続きます。
`stop_on_failure: false`なら行けない点があっても巡回を続け、完了時に取りこぼした点数が
ステータス行に出ます。

### 次の点を走行中に解いておく（`waypoint_prefetch`）

上の「いったん止まる」がポーズ時間だけでは済みません。VIは
ゴールごとに価値関数を解き直すので、**点が変わるたびに丸ごと1回のsolveが入り、その
間ずっと機体が止まっています**（実測で19Fが29秒、津田沼が87秒）。

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
0.1 m/セル・`action_forward_m` 0.5 m・`safety_radius_penalty: 30`なら
4×5×30＝600ステップ、1ステップ0.5 mなので**300 m相当**になります。**これは式に
値を入れただけで、実測ではありません**（バンド幅の実測は取っていません）。
地図の値域が丸ごと1バンドに収まると波2つで解き終わってしまい、打ち切る隙がありません。
このとき**エラーも警告も出ず、ただ何も短くなりません**。建物1フロア程度の広さは
こちら側の見込みで、効くのは津田沼のような広域地図です（津田沼は0.25 m/セル・
`safety_radius_penalty: 1`なので4×2×1＝8ステップ）。**2地図とも`true`にしてあります。
19Fは密へ戻したのでバンドが無くなり、効くようになりました**（上の600ステップの話は
compactだった頃のものです）。前進量とペナルティを下げても
`map_scale`を上げてもバンドは狭くなり、効きやすくなります（津田沼が
`safety_radius_penalty: 1`なのは別の理由——貪欲ロールアウトが降下できないため——ですが、
バンドもそのぶん狭くなります）。効いたかは上のログの`cut short` /
`truncated`で判断してください。

密ソルバ（`solver: "frontier2d_sparse"`に戻したとき）にはバンドが無いので、地図の
広さに依らず効きます。

**まだ実機でもpi4_simでも通していません。**

## 価値反復の表示

新しいゴールの最初の計算で地図全体を解くため、地図サイズにより数秒から数十秒かかる場合があります。同じゴールへの再計画は価値関数キャッシュにより高速です。

`rviz/navigation.rviz`には次のOccupancyGrid表示があります。

- `/value_function`: 価値関数のθ=0スライス。計算途中も既定500 ms間隔で更新
- `/local_window_value`: 機体周りの追従ウィンドウ（既定はカプセル、前後2.5 m・横2.0 m）の値。スキャン由来のペナルティと局所反復をリアルタイムに表示
  **2026-08-20から窓の形がそのまま見えます**——`local_shape`で
  矩形以外にすると、形の外は未知（-1）で出るためです
- `/belief`: VIOLAの自己位置belief（`localization:=vi`かつ`localizer`が`external`以外のときのみ）。**既定では表示オフ**——未収束のbeliefは自由空間ほぼ全域が非ゼロで、最前面に0.9の濃さで塗ると地図も価値関数も隠れるため。見るときはDisplaysでチェックを入れる

価値関数は1本しかないため、以前あった`/local_value_function`はありません。

表示にはRVizのMapを使い、Color Schemeを`costmap`にします。
