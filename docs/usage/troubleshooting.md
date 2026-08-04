# トラブルシューティング

## EMCL2が`failed to compute odom pose`／`can't get odometry info`と出る

`odom -> base_footprint`が誰も配信していない状態です。EMCL2は`map -> odom`を出す前に
この区間を引くので、無ければ何も推定できません。まず配信元が生きているか見ます。

```bash
ros2 topic hz /odom
ros2 run tf2_ros tf2_echo odom base_footprint
```

`Invalid frame ID "odom"`が返るなら本体ドライバが死んでいます。ログには何も出ないので
（ノードは`activated`まで到達してから落ちる）、プロセスの状態を直接見てください。

```bash
ps -eo pid,stat,comm | grep raspimouse    # Zl+ = ゾンビ
sudo dmesg -T | tail -40
```

**`use_light_sensors: true`でrtmouseがカーネルoopsを起こします**（2026-08-03、
Pi 4 Model B Rev 1.5 / 5.15.0-1098-raspi で確認）。`raspimouse`が`/dev/rtlightsensor0`を
`light_sensors_hz`（100 Hz）で読み、rtmouse側の`mcp3204_get_value`でページフォルトして
プロセスごと落ちます。

```
Unable to handle kernel paging request at virtual address ...
pc : osq_lock+0x7c/0x1a0
  mcp3204_get_value+0x98/0x120 [rtmouse]
  sensor_read+0x98/0x2f4 [rtmouse]
note: raspimouse[...] exited with preempt_count 1
```

`config/robot/raspicat.yaml`の`use_light_sensors`は`false`にしてあります。
`/light_sensors`を使うものはこのワークスペースにありません。

背景として、mainlineの`mcp320x`（IIO）が同じSPIチップセレクトを掴んでいます。

```bash
ls -l /sys/bus/spi/drivers/mcp320x/    # spi0.0 -> ... があれば競合
```

rtmouseもMCP3204を自前で叩くので、両者が同じデバイスを取り合う形です。光センサを
使いたい場合は`mcp320x`をブラックリストに入れてから試してください。

**oopsが出た後はリブートしてください。** ロックを握ったままプロセスが死ぬので
（`preempt_count`が漏れ、`lsmod`の参照数が戻らない）、`rmmod`も効きません。

## 遠隔操作しても機体が動かない

`twist_mux`（`robot_bringup.launch.py`の`twist_mux:=true`が既定）を挟むと、ドライバが
購読するのは`/cmd_vel`ではなく`/cmd_vel_mux`です。ただし`/cmd_vel`のほうも仲裁の
入力（優先度10）として残っているので、**ほかに誰も出していなければそのまま届きます**。
効かなくなるのは自律走行中で、`/cmd_vel`へ投げると自律側の出力と取り合いになります。

```bash
ros2 node list | grep twist_mux
ros2 topic hz /cmd_vel_mux          # 指令を出しているあいだだけ流れる
```

人が出す指令は`/cmd_vel_teleop`（優先度100）です。`control.sh`の`CMD_VEL_TOPIC`は
これが既定なので、`twist_mux:=false`で起動しているときだけ`CMD_VEL_TOPIC=/cmd_vel`を
渡してください。逆に`twist_mux`ノードが立っていない（イメージを焼き直していない）
場合は、`ros2 launch`が`package 'twist_mux' not found`で止まります。

自律走行中に遠隔操作が効くのは、**publishしているあいだと0.5秒だけ**です。キーを
離せば自律側（`/cmd_vel`）へ戻ります。確実に止めるのはモーター電源
（`control.sh motor off`）で、優先度は非常停止ではありません。

## 機体のトピックが見つからない

1. 機体とPC／コンテナの`ROS_DOMAIN_ID`を一致させる
2. 両側の`ROS_LOCALHOST_ONLY`が`0`であることを確認する
3. PCから機体のIPへ到達できるか確認する
4. Docker Desktopのhost networkingを有効にする
5. Windows／LinuxファイアウォールでDDS通信を許可する
6. 機体側ドライバが起動しているか確認する
7. VPNや不要なNICを一時的に切り分ける

`docker/raspberrypi/compose.yaml`と`docker/dev/compose.yaml`の`ROS_DOMAIN_ID`既定値は`90`です。

Pi本体のネイティブノードと同じPi上のコンテナとの間でトピックが見えない場合は、
下記のディスカバリとSHMの項目も確認してください。

WSL2から見ている場合、**3のpingが通ってもDDSが通るとは限りません**。既定のNATでは
pingとsshだけがSNATで通り、DDSは参加者が広告するロケータ（`172.31.x.x`）へ機体側から
返せないため見えません。`wslinfo --networking-mode`を確認し、
[ROS 2ネットワーク](../setup/network.md#wsl2から直接つなぐ)のbridged設定を使ってください。
`None`と表示される場合はmirroredの適用に失敗していて、WSLにNICがありません。

## ノードが現れたり消えたりする（ディスカバリが不安定）

Pi本体でネイティブノードと`docker/raspberrypi/`コンテナを同時に動かす構成で、負荷が上がると
発生します。原因は、各DDS参加者が相手から到達できないwlan0側のロケータまで広告し、
そのぶんUDPバッファが逼迫することです。

`docker/raspberrypi/fastdds_udp_whitelist.xml`をホストとコンテナの両方で使ってください。
コンテナ側は`compose.yaml`が設定済みなので、必要な作業はPi本体側だけです。

```bash
# Pi本体の ~/.bashrc へ追記
export FASTRTPS_DEFAULT_PROFILES_FILE=/path/to/daifuku_autonomous/docker/raspberrypi/fastdds_udp_whitelist.xml
echo "$FASTRTPS_DEFAULT_PROFILES_FILE"
```

whitelist内の`192.168.1.50`はPiの固定IPです。ロボットLANのアドレスが異なる場合は
XMLを書き換えてください。詳細は[ROS 2ネットワーク](../setup/network.md#raspberry-pi本体でのdds設定)を参照してください。

## `ros2`コマンドを何度か中断したあと、そのノードだけ届かなくなる

`ros2 topic echo`や`ros2 service call`を`timeout`や`Ctrl-C`で落とすことを繰り返すと、
**グラフ上は正常なまま、データだけが流れなくなる**ことがあります。`ros2 node info`も
`ros2 topic info`も購読者数まで正しく答えるのに、`ros2 topic pub`が出した指令はノードに
届かず、`ros2 topic echo`は何も受け取りません。原因を見誤りやすいので先に疑ってください
（2026-08-04、Pi 5のraspicat_driverで遭遇。ノード側にログを仕込んでコールバックが
呼ばれていないことまで確認した）。

**そのノードのコンテナを再起動すれば直ります。** 原因は特定していません（片方向だけが
落ちること、グラフは無傷であること、再起動で戻ることまでが実測）。調べるときは
`--once`や`-t <回数>`を付けて、CLIを殺さず自分で終わらせるようにしてください。

## TFが20秒以上遅れてゴールが中断する

Pi 4で参加者が20個近くになり、すべてUDPで通信していると、購読者ごとの`sendmsg`で
カーネルが飽和します（実測でsys 57%、load 24）。TFのタイムスタンプが大きく遅れ、
Nav2のゴールが次々と中断します。

同一ホスト内の通信を共有メモリ（SHM）へ切り替えて解消します。上記のプロファイル
設定に加えて、次の2点を確認してください。

- `docker/raspberrypi/compose.yaml`に`ipc: host`があること（`/dev/shm`をホストと共有する）
- `docker/raspberrypi/compose.yaml`の`user`が、ホストのROSプロセスのuidと一致していること

Fast DDSはSHMセグメントを0644で作成するため、root権限のコンテナと非rootのホストが
混在すると互いのポートを開けません。Fast DDS 2.6にはUDPへのフォールバックがないため、
トピックがエラーも出ないまま止まります。

```bash
# ホスト側のROSプロセスのuidを確認する
id -u
ls -l /dev/shm | head
```

## Waypointパネルの「Start」が即座に`Failed (aborted)`になる

押した瞬間にステータス行が変わり、機体はまったく動かず、経路計算のログも出ない場合
です。パネルが`/navigate_through_poses`へ投げているのに、`planner:=vi`（既定）では
その行動木が常に失敗するスタブ（`daifuku_stack/behavior_trees/nav_through_poses_stub.xml`）
に差し替わっているのが原因です。VI系プランナは`compute_path_to_pose`しか提供せず、
nav2既定の行動木が要求する`compute_path_through_poses`が無いためです。

パネルは`/follow_waypoints`（`nav2_waypoint_follower`）へ送る形に直してあります。
古いプラグインが`install/`に残っていると直っていないほうが読まれることがあるので、
症状が続くならRVizを起動しているコンテナで`daifuku_waypoint_manager`を建て直して
ください。

```bash
ros2 action list | grep -E 'follow_waypoints|navigate_through_poses'
ros2 node info /waypoint_follower
```

数秒動いてから中断する場合は別件です。TFの遅延（下の「TFが20秒以上遅れて…」）か、
Pi 4のCPU飽和によるゴール受理ackの取りこぼし（`bt_navigator`の
`default_server_timeout`）を疑ってください。

## その場で左に回り続ける

自律走行のつもりが前へ進まず、その場で反時計回りにぐるぐる回り続ける場合です。
**これは故障ではなくnav2のrecoveryです。** `spin`は`+1.57 rad`（反時計回り）を
`max_rotational_vel`（`config/nav2/behaviors.yaml`、1.0 rad/s）で回すので、失敗した
ゴールの数だけ左回りが繰り返されます。

```bash
ros2 topic echo /rosout --field msg | grep -E 'Running spin|Turning|Goal failed'
ros2 topic echo /compute_path_to_pose/_action/status --once | grep -c 'status: 6'
```

`Turning 1.57 for spin behavior.`が繰り返し出て、`compute_path_to_pose`が軒並み
`status: 6`（ABORTED）なら、**経路がそもそも引けていません**。まず疑うのは地図と
ゴールの噛み合わせです。

```bash
ros2 topic echo /map --once --no-arr          # width/height/resolution/origin
ros2 topic echo /rosout --field msg | grep 'Begin navigating'
```

`Begin navigating … to (x, y)`の座標が
`origin`〜`origin + [width, height] × resolution`の外なら、地図の外へ投げています。
巡回中なら順路が地図と対になっていません（[joystick.md](joystick.md#巡回を始める)）。
`stop_on_failure: false`なので1点ずつ失敗しながら最後まで進み、**そのあいだずっと
左回りが続きます**。

止めるのはモータ電源です。**RVizの「Navigation 2」パネルの`Reset`を押してはいけません。**
ライフサイクルマネージャは逆順に停止するので、先に`velocity_smoother`が落ち、
`waypoint_follower`の停止で（走りっぱなしのコールバックを待って）固まります。すると
`behavior_server`だけがactiveのまま残り、**`spin`は`velocity_smoother`を経由せず
`/cmd_vel`へ直接出すので、回転だけが止まらなくなります**。この状態は
`ros2 lifecycle get /velocity_smoother`が`inactive [2]`、
`/lifecycle_manager_navigation/is_active`が無応答、で見分けられます。抜けるには
`navigation.launch.py`を立て直してください。

## `Aborting bringup`でNav2が落ちる

ログに`… unable to be reached after 4.00s by bond`と出て、ライフサイクルマネージャが
`CRITICAL FAILURE`から自動シャットダウンする場合です。Pi 4では非合成起動時に8個の
プロセスが同時に立ち上がってloadが10〜19まで跳ね、bond形成が既定の4秒に間に合いま
せん。

`config/lifecycle_bond.yaml`でタイムアウトを60秒へ延長しています。値が効いて
いるか確認してください。

```bash
ros2 param get /lifecycle_manager_navigation bond_timeout
ros2 param get /lifecycle_manager_localization bond_timeout
```

なお`use_composition:=True`にすると、参加者あたりのエンドポイント数が大きくなり
すぎて新規参加者からディスカバリできなくなるうえ、CPU飢餓でbond心拍も途絶しやすく
なります。Pi 4では既定の`False`のまま使ってください。

## Mid-360のスキャンが「古すぎる」と拒否される

`message filter dropping message` や、TF・コストマップでスタンプが未来／過去に
ずれている旨のログが、起動から数分後に出る場合です。Mid-360がPTP同期していないため、
デバイス内蔵時計がPiのシステム時計に対して毎分数秒ずれていくことが原因です。

`lidar:=mid360`では`src/restamp_scan.py`が受信時刻でスタンプを打ち直します。
中継が動いているか確認してください。

```bash
ros2 node list | grep restamp_scan
ros2 topic hz /scan_mid360_prestamp
ros2 topic hz /scan_raw
```

`/scan_mid360_prestamp`だけが流れて`/scan_raw`が止まっている場合は、中継ノードが
起動していません。中継は`share/daifuku_stack/src/restamp_scan.py`を
`ExecuteProcess`で直接起動します。`ExecuteProcess`は失敗しても他のノードを止めないため、
エラーが出ないまま`/scan_raw`だけが欠けた状態になります。まずファイルの有無を
確認してください。

```bash
ros2 pkg prefix daifuku_stack
ls $(ros2 pkg prefix daifuku_stack)/share/daifuku_stack/src/
```

見つからなければ、`src`をインストールする前の`install/`が残っています（`scripts/`から
`src/`へ移す前の`install/`も同じで、古い`share/daifuku_stack/scripts/`だけが残ります）。
`colcon build`を
やり直してください（`docker/raspberrypi/`では`docker compose up`）。

なお`lidar_driver:=false`（シミュレータやバッグ再生）では中継そのものを挟まず、
`pointcloud_to_laserscan`が`/scan_raw`へ直接出します。この構成では
`/scan_mid360_prestamp`は流れないので、上の切り分けは当てはまりません。

## EMCL2の推定姿勢がその場で回転する

RESETのログが毎スキャン出て、推定姿勢が回り続ける場合です。非貫通率（alpha）が
`alpha_threshold`を下回り続け、膨張リセットとセンサーリセットが常時発動しています。

根本原因は地図と実環境の不整合です。実測では有効ビームの28%が地図上の壁を貫通して
おり、alphaが0.0〜0.4に張り付いていました。

1. RVizでスキャンと地図の壁が重なるか確認する
2. ずれている場合は[地図作成](mapping.md)からやり直す
3. 地図を取り直すまでの暫定処置として、`alpha_threshold`を下げ、
   `expansion_radius_orientation`を狭め、`sensor_reset: false`にしてリセットを抑制する。
   この3つは地図固有の値なので、断片の`config/localization/emcl2.yaml`ではなく
   `config/overrides/map_19f.yaml`に置く（19Fの地図では設定済み）

現在の設定値と背景は[設定リファレンス](configuration.md#自己位置推定の暫定設定)を
参照してください。地図を取り直したあとは既定寄りの値へ戻してください。

## `/scan`が配信されない

- 2D LiDARドライバ（`lidar:=2d`では`urg_node`）が`/scan_raw`へ出しているか確認する
- `lidar:=2d`または`lidar:=mid360`が構成と一致しているか確認する（既定は`mid360`）
- 上流から順に確認する。2D LiDARは`/scan_raw` → `/scan`、Mid-360は`/livox/lidar` →
  `/scan_mid360_prestamp` → `/scan_raw` → `/scan`
- フィルタを切り分けるため`scan_filter_enabled:=false`を試す

## Mid-360で`bind failed`になる

`config/sensors/MID360_config.json`の`host_net_info`に設定したIPが、ROS 2ノードを動かすPCの対象NICへ実際に割り当てられているか確認します。LiDAR本体IPも同一セグメントに合わせます。

## TFが競合または不安定になる

Mid-360 + IMUでは、EKFと車輪ノードが同時に`odom -> base_footprint`を配信していないか確認します。車輪側TFを停止するか、`/tf`を未使用トピックへremapしてください。自前実装（`driver:=original`）なら`config/robot/raspicat_driver.yaml`の`publish_tf: false`で止まります。

センサーTFもURDFと`publish_lidar_tf:=true`の両方から配信しないでください。

```bash
ros2 run tf2_ros tf2_echo odom base_footprint
ros2 topic hz /wheel/odom
ros2 topic hz /odom
```

## RVizが表示されない

軽量な`docker/raspberrypi/`イメージにはRVizがありません。ネイティブ環境または`docker/dev/`を使ってください。

GUI付き環境では次を確認します。

- X ServerまたはWSLgが動作している
- `DISPLAY`と`WAYLAND_DISPLAY`が正しい
- Windows X Serverが外部クライアントを許可している
- Linux X11のアクセス許可を設定している

## コンテナ内で`ros2`が見つからない

Compose経由でシェルを開きます。`docker/common/entrypoint.sh`（イメージへは
`/ros_entrypoint.sh`として入ります）が環境を読み込みます。

```bash
docker compose -f docker/raspberrypi/compose.yaml exec ros2 \
  /ros_entrypoint.sh bash
```

## Raspberry PiでDockerビルドが停止する

メモリ不足の可能性があります。並列数を1にします。

```bash
BUILD_JOBS=1 docker compose -f docker/raspberrypi/compose.yaml build
```

## 価値反復の最初の経路計算が遅い

新しいゴールでは地図全体の価値関数を解くため、数秒から数十秒かかる場合があります。ログで計算時間を確認してください。同じゴールへの再計画はキャッシュされます。

## 通路を塞いでも`/value_function`が変わらない

`vi_planner`は狭域（±1mウィンドウ）で上げた値を全域へ広げますが、確かめるときに引っかかるところが2つあります。

まず**見るトピック**です。RVizで走行中に動いて見えるのは`/local_window_value`（±1m）だけで、これは機体と一緒に動きます。離れると下から`/value_function`が出てくるため、**上げた値が上書きされたように見えます**。`/value_function`は掃きスレッドが2秒ごとに出し直しますが、それには`global_sweep: true`と`publish_value_function: true`の両方が要ります（`--show-args`ではなく`ros2 param get /vi_planner global_sweep`で確認）。

次に**塞ぎ方**です。スキャンが置くのは壁ではなくコストなので、通路の一部だけを塞いでも脇を抜けられるなら遠方の値はほとんど上がりません（host実測で+0.75ステップ＝`cost_drawing_threshold: 60`なら色1段）。幅いっぱいを塞ぐと桁が変わります（同13→38ステップ）。迂回できるなら上がらないのが正しい挙動です。

伝播が動いているかはログで見ます。走行中は壁が窓に入るたびに次の伝播が積まれるので、**上の行はまず出ません**。

```
vi_planner: global sweep done in 3.4s, 358 tiles (still_dirty=false)   # 待ち行列が空になった
vi_planner: tile repair running for 6.0s (412 visits, 27 tiles queued) # 2秒ごとの進捗
```

後者も出ないなら伝播が回っていません。`global_sweep`と、`planner:=vi`側の`solver`を確認してください（詳細は[`src/daifuku_stack/config/README.md`](../../src/daifuku_stack/config/README.md#効いているか確かめる)）。

## ログを確認する

```bash
docker compose -f docker/raspberrypi/compose.yaml logs -f ros2
```

**これで見えるのは`compose up`が建てたぶんだけです。** `docker compose exec`から
`ros2 launch`した場合、出力はそれを叩いたターミナルにしか出ません（コンテナのPID 1は
`sleep infinity`なので`logs`は空のままです）。閉じてしまったあとは、コンテナ内の
`$ROS_LOG_DIR`（`/tmp/ros/log`）にあるノードごとのログを読みます。**日付のディレクトリに
入っているのは`launch.log`だけで、ノードのログはその1つ上に`<ノード名>_<PID>_<epoch>.log`
の形で並びます。**

```bash
C=docker/raspberrypi/compose.yaml
docker compose -f $C exec ros2 ls -t /tmp/ros/log | head
docker compose -f $C exec ros2 sh -c 'cat /tmp/ros/log/lifecycle_manager_*.log'
```

生きているノードだけでよければ`/rosout`でも読めます（`ros2 topic echo /rosout`）。
ネイティブ環境ではlaunchを起動したターミナルのログを確認します。
