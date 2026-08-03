# config/

ディレクトリは**どの launch がどう読むか**で分かれています。

| 場所 | 読む launch | 渡し方 |
| --- | --- | --- |
| `nav2/*.yaml` | `navigation.launch.py` | 起動時に 1 つへ合成して `params_file` に渡る |
| `overrides/*.yaml` | `navigation.launch.py` | 合成結果へ重ねる（`overrides:=`） |
| `localization/emcl2.yaml` | `navigation.launch.py` | `emcl2_params_file` でノードへ直接 |
| `lifecycle_bond.yaml` | `navigation.launch.py` | `SetParametersFromFile` でグループ内の全ノードへ注入 |
| `sensors/*` | `lidar_bringup.launch.py` | 各ノードへ直接（`scan_filter_params_file` など） |
| `mapping/slam_toolbox.yaml` | `mapping.launch.py` | `slam_params_file` でノードへ直接 |
| `robot/raspicat.yaml` | `robot_bringup.launch.py` | `raspimouse` (LifecycleNode) へ直接。`driver:=raspimouse` (既定 / 公式実装 / Pi 4) |
| `robot/raspicat_driver.yaml` | `robot_bringup.launch.py` | `raspicat_driver` (LifecycleNode) へ直接。`driver:=original` (自前実装 / Pi 4・Pi 5) |

`robot/raspicat.yaml` だけは**上流ファイルの完全なコピー**で、差分ではありません。
launch_ros はノード自身の `parameters=` をグローバル (`SetParametersFromFile`) より
**後に**展開する = ノード側が勝つため、上流 `raspicat.launch.py` を include して
差分を重ねる方式では上書きできないからです。

## コメントの書き方

**すべてのキーに 1 行**で `# 既定 <値>: <何の値か>` を書きます。既定から変えた
キーには、続けて変えた理由も書きます。

```yaml
controller_frequency: 10.0   # 既定 20.0 [Hz]: 制御周期。Pi4 が飽和し bond 心拍が途絶えた
resample_interval: 1         # 既定 1: 何回の更新ごとにリサンプルするか
```

各ファイルの末尾には、**そのノードが持っているのにここで設定していないキー**を
コメントアウトで並べます。既定値と説明も同じ形で書きます。

```yaml
    # ── 未設定 (既定のまま) ──
    # first_map_only: false   # 最初に受け取った /map だけを使い、以後の更新を無視する
```

「既定」はすべて**ノードの実装**（`declare_parameter` など）から採った値です。
出どころは各ファイルの冒頭に書いてあります。

| ファイル | 「既定」の出どころ |
| --- | --- |
| `nav2/amcl.yaml` | `nav2_amcl` の `amcl_node.cpp` |
| `nav2/behaviors.yaml` | `nav2_smoother` / `nav2_behaviors` / `nav2_waypoint_follower` / `nav2_velocity_smoother` |
| `nav2/bt_navigator.yaml` | `nav2_bt_navigator` と `nav2_behavior_tree` の `bt_action_server_impl.hpp` |
| `nav2/controller_server.yaml` | `nav2_controller` と `nav2_dwb_controller`（`dwb_core` / `dwb_plugins` / `dwb_critics`） |
| `nav2/costmaps.yaml` | `nav2_costmap_2d` の `costmap_2d_ros.cpp` と各レイヤプラグイン |
| `nav2/map_server.yaml` | `nav2_map_server` の `map_server.cpp` / `map_saver.cpp` |
| `nav2/planner_server.yaml` | `nav2_planner` と `nav2_navfn_planner` |
| `nav2/vi_planner.yaml` | 各ノードの `main.rs` の `declare_parameter` |
| `localization/emcl2.yaml` | `src/emcl2_ros2` の `emcl2_node.cpp` |
| `mapping/slam_toolbox.yaml` | `slam_toolbox` の `slam_toolbox_common.cpp` / `slam_mapper.cpp` / `laser_utils.cpp` / `ceres_solver.cpp` |
| `sensors/mid360_ekf.yaml` | `robot_localization` の `ros_filter.cpp` |
| `sensors/mid360_scan.yaml` | `pointcloud_to_laserscan` の `pointcloud_to_laserscan_node.cpp` |
| `sensors/scan_filter.yaml` | `laser_filters` の `sector_filter.h`（既定なし＝全項目必須） |
| `robot/raspicat.yaml` | 上流 `raspicat_ros` の `raspicat/config/raspicat.param.yaml` |
| `robot/raspicat_driver.yaml` | `src/raspicat_driver` の `src/raspicat_driver/node.py` |
| `overrides/*.yaml` | 重ねる先の断片の値（「断片 60:」のように書きます） |

見ているブランチは、`robot_localization` が `humble-devel`、`laser_filters` が
`ros2`（Humble ブランチが無いため）、それ以外はすべて `humble` です。

`nav2_bringup` が配っている `nav2_params.yaml` は**ノードの既定とは違う**値を
いくつか持つので（`use_astar`、`expected_planner_frequency`、`save_map_timeout`、
`FollowPath.plugin` など）、そちらを「既定」と呼ばないでください。食い違うキーは
ファイル側に注記してあります。

1 行に収まらない計測や経緯は、ファイルには書かずに下の「値の由来」へ書き、
コメントからは `../README.md` を指します。地図や環境に固有の話は
`overrides/<名前>.yaml` 側に置きます。

### 効かないキー

宣言されていないキーを書いても、ROS 2 はエラーも警告も出さずに無視します。
調査で見つかったものは、消さずに「効かない」と注記してあります。

| キー | 状況 |
| --- | --- |
| `emcl2.open_space_threshold` | 上流 README の表にはあるが、この版の `emcl2_ros2` は `declare_parameter` していない |
| `ekf_filter_node.odom0_nodelay` / `imu0_nodelay` | 上流ドキュメントにはあるが、ROS 2 版の `ros_filter.cpp` は読まない（ROS 1 の `tcpNoDelay` 由来） |
| `controller_server.FollowPath.stateful` | DWB に無い。ゴール判定側の同名パラメータと混同したもの |

## nav2/ の合成

`nav2/*.yaml` はファイル名順に読まれ、**深くマージ**されて 1 つの一時ファイルになります。
そのパスは起動ログに出ます。

```
[INFO] [launch.user]: params: composed 8 fragments from .../config/nav2 -> /tmp/nav2_params_xxxx.yaml
```

分割は「ノード単位で重複なし」が前提です。同じノードの同じキーが 2 つの断片に
書かれていると、起動時にエラーで止まります（どちらが勝つか分からない状態を
作らないため）。

| ファイル | 含むノード |
| --- | --- |
| `nav2/amcl.yaml` | `amcl` |
| `nav2/behaviors.yaml` | `smoother_server`, `behavior_server`, `waypoint_follower`, `velocity_smoother` |
| `nav2/bt_navigator.yaml` | `bt_navigator` と付随の 2 ノード |
| `nav2/controller_server.yaml` | `controller_server`（DWB） |
| `nav2/costmaps.yaml` | `local_costmap`, `global_costmap` |
| `nav2/map_server.yaml` | `map_server`, `map_saver` |
| `nav2/planner_server.yaml` | `planner_server`（navfn） |
| `nav2/vi_planner.yaml` | `vi_planner`, `vi_global_planner` |

`amcl` が `localization/` ではなく `nav2/` にあるのは、nav2 の
`localization_launch.py` が `params_file` の中から読むためです。

## 上書き（override）

優先順位は下ほど強く、**後勝ち**です。

1. `nav2/*.yaml` の合成結果（`params_file:=` を明示した場合はそのファイル）
2. `overrides:=<名前>` → `overrides/<名前>.yaml`（カンマ区切りで複数可）
3. `extra_params_file:=<パス>` → 任意のファイル（リポジトリ外の一時的な上書き用）

`overrides` の既定値は **`map_19f`** です。既定の地図（`maps/map_19f.yaml`）に
対応する調整を、素の起動でも載せるためです。地図を変えるときは**置き換え**に
なります（追加ではありません）。

```bash
ros2 launch autonomous_nav navigation.launch.py \
    map:=$PWD/src/autonomous_nav/maps/map_tsudanuma.yaml \
    overrides:=map_tsudanuma planner:=vi local_planner:=nav2
```

何も重ねないときは `overrides:=none` です。`ros2 launch` は値が空の
`overrides:=` を malformed として弾くので、空文字ではなく `none` を使います。

**地図を渡し替えて `overrides` を放置しないでください。** 別の地図に 19F 用の
EMCL2 調整（リセット閾値など）が載ったまま走ります。対応する override を持たない
地図（`maps/turtlebot3.yaml` など）では `overrides:=none` を明示してください。
存在しない名前を渡した場合は、選べる名前を並べたエラーで止まります。

`simulator/container/nav_container.sh` と `simulator/container/run_case.sh` は、
`MAP_NAME` と同名の override があればそれを、無ければ `none` を**必ず明示的に**
渡します（`OVERRIDES=` で上書き可）。既定任せにすると同じ取り違えが起きるためです。

### emcl2 は params_file を通らない

emcl2 は nav2 のノードではないので、合成結果（`params_file`）ではなく
`emcl2_params_file` がノードへ直接渡ります。そのため `navigation.launch.py` は
**emcl2 用の合成も別に行い**、同じ `overrides` / `extra_params_file` の
`emcl2:` セクションだけを `localization/emcl2.yaml` の上に重ねて
`emcl2_params_file` を差し替えます。

この配線が無いと、`overrides/<地図>.yaml` に `emcl2:` を書いても**エラーも警告も
出さずに無視されます**。起動ログの次の行で差し替えを確認できます。

```
[INFO] [launch.user]: params: composed emcl2 .../config/localization/emcl2.yaml -> /tmp/emcl2_params_xxxx.yaml (+ overrides:map_19f)
```

### 新しい override を足す

`overrides/<地図名や状況>.yaml` を作り、**変えたいキーだけ**を書きます。
ノード名と `ros__parameters` の 2 段は必要です。

```yaml
vi_global_planner:
  ros__parameters:
    safety_radius_penalty: 1
```

`nav2/*.yaml` を丸ごと複製しないでください。既定値が変わったときに追従できません。

### 効かないときに疑うところ

`SetParameter` / `SetParametersFromFile` は、`params_file` に**既にあるキー**を
上書きできません（launch_ros はグローバルパラメータを先に、ノード個別の
`parameters=` を後に渡すため、後勝ちでノード側が勝つ）。
そのため override は `params_file` そのものをマージして作っています。
逆に `lifecycle_bond.yaml` の `bond_timeout` が `SetParametersFromFile` で効くのは、
そのキーが `nav2/*.yaml` のどこにも無いからです。

## 値の由来

パラメータのコメントに書ききれない計測と経緯。

### `costmaps.yaml` の `footprint`

以前は `robot_radius: 0.22` でした。`nav2_bringup` の `nav2_params.yaml` の値を
そのまま使っていたもので、この機体を測った値ではありません（ノードの既定は
`robot_radius: 0.1`）。2026-08-03 の実測で車体は 420 (幅) x 450 (奥行) mm、`base_footprint`
は車軸中心の真下にあり、タイヤ（直径 200mm）の前端が車体前端と一致するので車軸は
前端から 100mm です。したがって `base_footprint` 基準の張り出しは
**前 +0.10 / 後 −0.35 / 左右 ±0.21 [m]** で、原点は大きく前寄りになります。

円ではなく多角形にしたのは、外接円 `sqrt(0.35^2 + 0.21^2) = 0.408m` が実面積の
2.8 倍あり、狭い通路を無駄に塞ぐためです。`inflation_radius` 0.55 はこの外接円より
大きいので整合しています。0.408 未満に下げると nav2 が警告し、回り込みが破綻します。

`footprint` と `robot_radius` を両方書かないでください（nav2 は `footprint` が
空でなければそちらを優先するので、`robot_radius` は読まれないまま残ります）。
`local_costmap` と `global_costmap` で同じ値を 2 度書いているのは、YAML アンカーの
置き場になる最上位キーを足すと、rcl のパラメータパーサが「ノード名 +
`ros__parameters`」として解釈できずに読み込みごと落ちるためです。

### `raspicat.yaml` の `use_pulse_counters: false`

上流も既定は `false`（開ループ = 最後に受け取った `cmd_vel` を積分）ですが、本来は
`true`（`/dev/rtcounter_{l,r}*` のロータリエンコーダ）が正しい設定です。`false` の
実害は 2026-07-29 の実機で確認済みです。

* nav2 を回転中に止めるとゼロ速度が届かず、`odom` が −45deg/s で回り続ける
* その幻の回転を emcl2 が打ち消そうとして `map->odom` も振り回される
* モータ OFF でも `odom` が動くので dry-run が成立しない（静止したまま自己位置が
  ゴールまで「走る」）

`true` にすると同じ指令で `odom` yaw の peak-to-peak は 0.000 deg、x は 0.0000 m に
なりました。**それでも `false` にしてあります。** この個体の I2C カウンタが
不安定だからです。同じセッション中に 2 回、`read` がタイムアウトして復旧不能に
なりました。

```
i2c-bcm2835 fe804000.i2c: i2c transfer timed out
i2c_counter_read: Failed reading from i2c counter device, addr=0x10 / 0x11
```

一度失敗するとドライバの mutex が握られたままになり、以後 `/dev/rtcounter_*` を
読む者は全員 D (uninterruptible) 状態に永久固着します。`raspimouse` は単一スレッド
なのでノードごと沈黙し（`/odom` も TF も lifecycle 応答も停止）、**SIGKILL でも
殺せず復旧はリブートのみ**です。`cat /dev/rtcounter_l0` でも同じ症状になります。

天秤は「`false` = `odom` の幻回転（ゼロ Twist を投げれば必ず止まる）」対
「`true` = `odom` は正しいが走行中にランダムでロボットごと固着」で、実験中の突然死の
ほうが害が大きいと判断しています。カウンタ基板側の I2C が直れば `true` が正解です。
緩和案（未検証）: `odom_hz` を 100 から下げて I2C トラフィックを減らす。

`false` のあいだの運用として、nav2 を止めた直後に必ずゼロ Twist を投げてください。

```bash
ros2 topic pub --times 5 /cmd_vel geometry_msgs/msg/Twist \
  '{linear: {x: 0.0}, angular: {z: 0.0}}'
```

ノードの自己診断はあてになりません。`raspimouse_component.cpp` の「Testing
counters」は `ifstream::is_open()` を見るだけで `read` を試さないので、
"Using pulse counters for odometry" は**フラグが適用された**証拠にはなっても
**エンコーダが読める**証拠にはなりません。確認は「モータ OFF で回転を指令して
`odom` が動かないこと」で行ってください。

### `raspicat.yaml` の `wheel_diameter` / `wheel_tread`

上流の既定は 0.1524 / 0.27918 ですが、この個体は車輪も車軸間隔も違います
（2026-08-03 実測で 200mm / 350mm）。この 2 値は `cmd_vel` → モータ指令の換算と、
`use_pulse_counters: false` のときの `odom` 積分の両方に使われるため、上流値のままだと

* 直進 `0.200 / 0.1524 = 1.312` 倍
* 旋回 `1.312 * 0.27918 / 0.350 = 1.047` 倍

で、実機が指令より 31% 速く走り、`odom` はその分だけ過少報告していたことになります
（ゴールのオーバーシュートや emcl2 の運動モデル破綻の原因になりえます）。

**未検証**: `pulses_per_revolution: 400.0` は上流のままです。車輪と一緒に減速比まで
替わっていれば上の比は成立しません。確認は「モータ ON で 0.1 m/s を 10 秒指令し、
巻尺の実移動距離と `/odom` の変位を比べる」。この修正後は両者が一致するはずです。
ずれるなら残差は `odometry_scale_{left,right}_wheel` ではなく寸法側で詰めてください。

### `raspicat_driver.yaml`（自前ドライバ / Pi 4・Pi 5）

`raspicat.yaml` は公式実装（`driver:=raspimouse`）用で、rtmouse カーネルモジュールが
出す `/dev/rt*` を `raspimouse` ノードが読む構成が前提です。自前実装
（`driver:=original`、[`src/raspicat_driver`](../../raspicat_driver/README.md)）は
モータ経路をユーザ空間から直接扱い、そのパラメータがこのファイルです。`driver:` の
既定は `raspimouse` なので、既存の運用は変わりません。

Pi 4 と Pi 5 で 1 ファイルです。機種差は `model: auto` が device-tree から判定して
チップの同定（`gpiochip` のラベルと `pwmchip`）だけを切り替えます。ピン番号・PWM
チャネル・I2C アドレスは制御基板側の性質なので両機種で同じです。

* **Pi 5 ではこちらしか選べません。** rtmouse は BCM2711 のレジスタを `ioremap` する
  ので、GPIO/PWM が RP1 側にある Pi 5 では動きません。
* **Pi 4 では公式実装と排他です。** rtmouse が載っていると `configure` を拒否します
  （`allow_rtmouse` で上書き可）。両方が GPIO 16/6/5 と PWM を持つと、カーネルは
  衝突を検出しないまま車輪が逆に回り得ます。

寸法と補正係数は上の 2 節と同じ値です。`raspicat.yaml` と違うのは次の 3 点で、理由は
[`docs/setup/raspberry-pi-4.md`](../../../docs/setup/raspberry-pi-4.md) と
[`raspberry-pi-5.md`](../../../docs/setup/raspberry-pi-5.md)。

* `use_pulse_counters` の既定が `true`。ユーザ空間の `ioctl` は失敗を返して戻って
  くるので、rtmouse のような D 状態固着が起きません。連続失敗が
  `counter_error_limit` に達すると `cmd_vel` 積分へ落ち、応答が戻れば自動で復帰
  します。
* `odom_hz` の既定が 50.0（`raspicat.yaml` は 100.0）。1 周期あたり I2C を
  4 トランザクション使うので、62.5 kHz のバス占有を半分に落としてあります。
* `publish_tf` があります。EKF に `odom -> base_footprint` を出させる構成では
  `false` にしてください。

配線に関わるキー（`gpio_*` / `pwm*` / `i2c_*` / `direction_*_forward_level`）は
すべて rtmouse の `rtmouse.h` から写した値で、**実機で確認していません**。
確認項目と直しかたは上の 2 つのドキュメントの表にあります。

### `lifecycle_bond.yaml` の `bond_timeout: 60.0`

既定は 4.0 秒。Pi4 では非コンポジション起動時に 8 プロセスが同時に spin up して
load が 10〜19 まで跳ね、bond 形成が間に合わずライフサイクルマネージャが
"unable to be reached after 4.00s by bond → Aborting bringup" で落ちました
（2026-07-24 実測）。形成待ちと心拍途絶判定を 60 秒まで許容します。

### `emcl2.yaml` の `sensor_reset` と `open_space_threshold`

どちらも上流の README（`src/emcl2_ros2/README.md`）の表と、実際にビルドされる
コードが食い違っています。**コードが正**です。

* `sensor_reset`: README の表は既定 `true` ですが、`emcl2_node.cpp` の
  `declare_parameter("sensor_reset", false)` と上流 `config/emcl2.param.yaml` は
  ともに `false` です。この断片が `true` にしているのは a3f2899（`emcl2` の導入
  コミット）以来で、理由は記録されていません。README の表を見て入れたものと
  思われます。**未検証**: `false` に戻して 19F 以外の地図で挙動が変わるかは
  測っていません（19F では `overrides/map_19f.yaml` が `false` にするので同じです）。
* `open_space_threshold`: README の表にはありますが、この版の `emcl2_ros2` は
  `declare_parameter` していないので**読まれません**。値を書いても効かず、
  エラーも警告も出ません。

### `vi_planner` / `vi_global_planner` の `safety_radius_penalty: 30`

単位は「秒/セル」で、`safety_radius`（0.2m）以内のセルを通るときの加算コストです。
1 手のコストが 1 秒なので、30 は「近寄るくらいなら 30 手迂回する」という強い忌避に
なります。細い通路ばかりの地図では下げる必要があります（`map_tsudanuma` の実例は
下の節）。

### `controller_server` の `transform_tolerance: 1.0`

変更前は 0.2 でした（DWB のノード既定は 0.1 で、0.2 は `nav2_bringup` の
`nav2_params.yaml` にあった RPP ブロックの値。DWB へ差し替えたときに一緒に
引き継がれたものです）。2026-07-29 の実機計測で `map->odom` の遅延は 67 件すべてが
0.20〜0.47 s に収まり、最小値がちょうど当時の閾値 0.2 と同値でした。閾値の直上に
貼り付いた定常オフセットで、emcl2 の TF 再発行は約 110ms 間隔で一定（＝止まって
はいない）。遅延の実体は
`livox -> pointcloud_to_laserscan -> restamp_scan -> filter chain -> emcl2` の
パイプラインで、TF はスキャン時刻で打たれます。結果、制御周期の 11% が
"Transform data too old" で落ち、連続失敗が `failure_tolerance` 0.3 s を超えた
時点で `follow_path` が Aborting → ゴールが ABORTED になりました。

1.0 は `amcl` セクションと同値。代償は最大 1 s 古い姿勢で制御しうること
（最高速 0.2 m/s なので位置ずれは最大 0.2 m）。本筋の対処はスキャン経路の遅延
そのものを削ることです。

### `bt_navigator` の `default_server_timeout: 500`

nav2 既定は 20ms。Pi4 の CPU 飽和時（`nr_throttled` が 10 万回級）には
`compute_path_to_pose` / `spin` / `wait` / `backup` が軒並みゴール受理 ack を
取りこぼし、ゴールが 0.2 秒で ABORTED になりました（`simulator/` の pi4_sim ハーネスの Pi4 相当
環境で再現・切り分け済み）。資源に余裕があれば 20ms でも通るので、これは保険です。

### `bt_navigator` の `wait_for_service_timeout: 60000`

nav2 既定は 1000ms。`planner:=vi` では `vi_global_planner` が `/map` を受け取って
から `compute_path_to_pose` を作るので、地図が大きいほど遅れます。`map_tsudanuma`
（23.5MB）では間に合わず、`bt_navigator` が `on_configure` で
"action server not available" を投げて bringup 全体が止まりました。

### `vi_global_planner` の `cost_drawing_threshold`

表示専用（`value_function` の色スケール上限、単位はステップ数≒秒）。
**地図ごとに測り直す値**なので、断片（`nav2/vi_planner.yaml`）は地図非依存の 60 に
置き、19F 用の 180 は `overrides/map_19f.yaml` にあります。

2026-07-29 実機計測（19F の地図を 0.10 m/cell に縮めた 458x289）では、60 だと
到達可能セルの 66% が 100 に張り付き、グラデーションが出るのはゴールから 60
ステップ以内の 6.83% だけでした（未到達 -1 が 80.07% / 飽和 100 が 13.10% /
1..99 が 6.83%）。1 ステップ = `action_forward_m` 0.3 m なので 60 ステップ ≒ 18 m
相当で、建物一周の廊下長に足りていません。同じ計測で到達可能セルの最大は
680 ステップ（≒204 m）、中央値 60 / p90 110 / p99 300。180 は p90 と p99 の間で、
運用上通る範囲に階調を集中させ、遠い裾だけ飽和させる選び方です。

別の地図では分布が変わります（飽和が広ければ小さすぎ、階調が下位に偏っていれば
大きすぎ）。`overrides/map_tsudanuma.yaml` はこれを設定していないので 60 で動きます。

### `overrides/map_tsudanuma.yaml` の `safety_radius_penalty: 1`

既定は 30 [秒/セル]。1 手のコストは 1 秒なので、30 は「近寄るくらいなら 30 手
迂回する」という強い忌避です。津田沼は通路が細く（未観測を障害物とみなすため
1〜2 m 幅）、0.15 m セルではほとんどの自由セルが `safety_radius` 0.2 m 以内に
入ります。VI の遷移はサブセルサンプリング付きの確率モデルなので、隣接状態間で
ペナルティの重みが変わり、価値関数が局所的に ±3 秒ゆらぎます。1 手 1 秒の前進より
揺らぎが大きいと、ノイズ無しの貪欲ロールアウトが降下できず、隣接 θ 間で往復して
`LoopDetected` になります（実測: penalty 30 で 83 手目で固着）。1.0 にすると同じ
地図・同じゴールで単調に降下し、104 姿勢でゴールに到達しました。揺らぎはペナルティ
にほぼ比例するので、10 秒未満なら 1 手 1 秒の進捗を下回るはずです。

### `map_tsudanuma` で `planner:=vi` を使うときの制約

* `local_planner:=vi`（`vi_planner` 1 ノード）と `local_planner:=nav2`
  （`vi_global_planner` + `controller_server`）のどちらも使えます。どちらも
  `map_scale` とアウトオブコア経路 (`frontier2d_sparse_compact`) を持ちます。
  `vi_planner` の狭域追従だけは密な状態配列を要るので、全域ではなくロボット近傍の
  パッチ（±1m ウィンドウ + 遷移到達距離 + 余裕、0.25m セルで 27x27x60 ≒ 2.5MB）を
  compact の場から起こして回します。ただしこの地図では **`global_sweep: false` が
  必須**です（下節）。compact には狭域 → 広域のフィードバックに使う共有場が無く、
  true のままだと「黙って効かない設定」として launch が止めます。
* `map_scale: 5` は `downsample_policy: optimistic`（ブロック内に free が 1 つでも
  あれば free）とセットです。既定の保守的プーリング（障害物優先）だと通路のセル幅が
  VI の遷移分布（約 2 セル幅）を下回り、`map_scale >= 4` で波がゴール近傍から広がりません。
  楽観側は通路を細らせない代わりに壁側に寄るので、実測の `footprint`
  （420x450mm、外接円 0.408m）/ `inflation_radius` 0.55 と合わせて通れるかは
  経路ごとに確認を。`map_scale: 5` 到達を確認した当時のコストマップは
  `robot_radius` 0.22（`nav2_bringup` の yaml のまま）だったので、実機のほうが厳しい。
* この地図は 68.2% が未観測 (205) で、真の占有セルは 0.4% しかありません。
  `unknown_as_obstacle: true`（既定）だと未観測が全て壁になる＝舗装路のみ通行可。
  一方 emcl2/AMCL のスキャンマッチングは占有セルの尤度場を使うので、この地図では
  拠り所がほとんどありません（別途要検討）。
* メモリは `map_scale: 5` + compact で `vi_global_planner` / `vi_planner` とも
  ピーク RSS 約 1.5GB（匿名 0.83GB + sink の mmap 0.66GB）。`map_scale: 3` +
  保守的プーリングだった頃の 3.98GB（匿名 2.16GB + mmap 1.81GB）から下がり、
  Pi4 4GB の枠には収まります。実測の詳細は `simulator/docs/pi4_sim.md`。
* 一方 **`simulator/` の pi4_sim ハーネスの枠（0.6 コアを stack 全体で共有）では、solve 中に
  emcl2 まで巻き込んで 900 秒でも `/plan` が出ません**。実機 Pi4 は 4 コアあるので
  同じにはなりませんが、`vi_threads: 3` を明示して 1 コアを stack に残すのは
  そのためです。実機での通し確認は別途。

### `overrides/map_19f.yaml` の `map_scale: 2`

`map_scale` は**プランナ内部だけ**の作業解像度です。`/map`・コストマップ・emcl2 は
0.05 m/cell のままで、粗くなるのは VI が解く格子だけです。

| | scale 1 | scale 2 |
| --- | --- | --- |
| プランナ内部の格子 | 915x577 @0.05m | 458x289 @0.10m |
| 状態数（x60 θ） | 3168 万 | 794 万 |
| compact の確定出力（12 B/state） | 0.38 GB | 0.095 GB |
| **密の常駐（80 B/state）** | **2.53 GB** | **0.65 GB**（実測 654.8 MB） |

**2 にしている理由は密ソルバを載せるためです。** `vi_planner` の狭域 → 広域の
フィードバック（`global_sweep`、下節）は `states` を共有場として使うので、密で
なければ成立しません。密の常駐は 80 B/state — `states` 56 B に加えて
`set_sweep_orders` が掃き順を 6 本ぶん持つので +24 B/state — で、scale 1 の
2.53 GB は 4 GB の Pi4 に他のノードと同居させられません。scale 2 の実測は
**654.8 MB**（`states` 444.7 MB + `sweep_orders` 210.1 MB）です。

以前ここには「`map_scale > 1` は密ソルバでは通らない（launch が止める）」と
書いてありましたが、前提が逆でした。`map_scale` は密を**載せるための**手段です。
launch の `_validate_vi_solver` もそれに合わせて直してあり、メモリ上限を見るのは
ノード側の `dense_limit_mb`（既定 1500 MB、19F の断片では 2048 MB）です。地図の
実寸はノードしか知らないので、そちらのほうが正確に判定できます。

代償は 0.10 m/cell の粗さと、保守的プーリングで通路が片側最大 0.05 m 細ること。
**未検証** — この地図・この scale での solve 時間と経路そのものは測っていません
（参考: 2026-07-29 の実機は compact scale 1 で solve 29.25 s / RSS 833 MB / OOM なし）。

`map_tsudanuma` の `map_scale: 5` は `downsample_policy` /`action_forward_m` /
`goal_margin_radius` とセットでないと波がゴール近傍で止まりますが、2 では
1 手 = 3 セル（`action_forward_m` 0.3 m）、ゴール半径 3 セル、`safety_radius` 2 セルが
残るので断片のままにしてあります。楽観プーリングが要るのは `map_scale >= 4` からです。

なお `cost_drawing_threshold` の分布計測（上節）自体が 0.10 m/cell の 458x289 で
行われているので、scale 2 のほうが計測条件と一致します。

### `vi_planner` の `global_sweep`（狭域 → 広域のフィードバック）

`vi_planner` は 1 本の価値関数を広域（`compute_path_to_pose` のロールアウト）と
狭域（`follow_path` の ±1m ウィンドウ）で共有します。狭域はスキャンのヒット点に
`local_penalty` を書き込み、その場でウィンドウ内の価値反復を回して障害物を避けます。

**ここに穴がありました。** ウィンドウ内の価値反復（`refine_pass_until`）が掃くのは
ウィンドウの中だけなので、上がった値はそこで止まります。20 m 先から降りてくる
広域のロールアウトは塞がった通路へ降り続け、着いてから初めて気づきます。結果として
`compute_path_to_pose` が `LoopDetected` を返し、BT が復帰行動へ落ちます。

`global_sweep` はこれを埋めるもので、同じ `states` を全域 Gauss–Seidel で掃き直し、
局所で上がった値を外へ広げます。新しい Bellman 更新は書かず `value_iteration_at` を
そのまま使うので、狭域・広域・solve の 3 者は同一の更新式のままです。

* **密ソルバ専用です。** compact は `states` を作らず、追従は sink から起こした
  パッチの上で回り、それは置き直しのたびに捨てられます（`hydrate` が
  `local_penalty = 0` で潰します）。共有場が無いので掃きようがありません。
  compact + `global_sweep: true` は launch が止めます（黙って効かない設定なので）。
* **反応速度**: 合成テストでの実測は **1 掃き目で広域の価値が動き**、30 掃きで
  ほぼ収束、数値的な完全収束（Δ=0）は約 80 掃きでした。収束を待つ必要はありません
  — 掃くたび不動点へ単調に近づき、経路が変わるのは遥かに手前です。
* **1 掃きの実時間**: 掃き速度の host 実測は 5.23 M cells/s なので、19F の scale 2
  （794 万状態）なら 1.5 秒。Pi4 は同種の処理で 5〜8 倍遅いので **8〜11 秒**の見込み
  です。既定の `global_sweep_budget_ms: 20` / `global_sweep_idle_ms: 60`（1 コアの
  25%）だとその 4 倍かかります。**実測値は起動ログの `global sweep done in ...` に
  出る**ので、そこから詰めてください。**未検証** — 実機での 1 掃きは測っていません。
* **ロックの持ち方**が肝です。10 Hz の追従ループは同じ `Mutex<PlannerCore>` を
  `try_lock` で取り、3 tick 続けて取れないとロボットを止めます。掃きは
  `global_sweep_budget_ms` だけ掃いてロックを手放し、`global_sweep_idle_ms` 待ちます。
  budget を伸ばすときは idle も一緒に伸ばさないと、走行がぎくしゃくします。
* **止まるとき**: 1 掃き丸ごとで Δ=0 になったら新しい不動点に達したので、次に狭域が
  場を動かすまで掃きは止まります（CPU を焼き続けません）。

**ウィンドウの外の `local_penalty` は誰も消しません**（`set_local_cost` は
`in_local_area` の中しか触らない）。障害物の脇を通り過ぎると、その penalty はその
ゴールの間ずっと `states` に残り、全域掃きのたびに広域の場を歪め続けます。本家
`ViNode` から引き継いだ挙動で、「一度通れないと分かった場所を覚えておく」という
望ましい側の効果でもあるため意図的にそのままにしてあります。消したければゴールを
取り直してください。誤検知（この地図では emcl2 の有効ビームの 28% が壁を貫通する）が
残り続ける経路でもあるので、挙動が怪しいときはここを疑ってください。

### `vi_planner` / `vi_global_planner` の `compact_ram_limit_mb: 2048`

**これはプロセス全体のメモリ上限ではありません。** compact の確定出力（sink）を
RAM に置いたままにする上限で、超えたぶんだけ `/tmp/vi_*_sink` へ mmap で逃がす、
という分岐の閾値です。プロセスの実際のピークはこれとは別に決まります（19F の
scale 1 では sink 0.38 GB がノード既定の 512 MB に収まって RAM に載っていたのに、
実測の RSS は 833 MB でした）。

**いま同梱している 2 つの地図では、512 → 2048 に上げても動作は変わりません。**
19F の `vi_planner` は密ソルバなので sink を作らず、このキーは**そもそも読まれません**
（`vi_global_planner` 側は compact のままで、scale 2 の sink 0.095 GB は 512 MB でも
2048 MB でも RAM に載ります）。`map_tsudanuma` は両ノードとも `compact_sink_dir` を
指定しているので、そもそもこの分岐（`main.rs` の「未指定でも上限を超えるなら退避」）に
入りません。値が効いてくるのは `overrides:=none` や新しい地図で、
`compact_sink_dir` を指定しないまま sink が 0.5 GB を超えたときです。そこで
「SD カードへ逃がさず 2 GiB までは RAM で持つ」という意味になります。

密ソルバ側の上限は別のキーです（`dense_limit_mb`、既定 1500 MB）。こちらは
「超えたら退避する」ではなく **「超えたら起動を止める」** で、黙って確保して OOM
killer に落とされるより理由を出して止めるためのものです。

**代償**: 逃がさない代わりに、その 2 GiB は匿名メモリとして居座ります。Pi4（4 GB）で
`compact_sink_dir` 無しの広域地図を解くと、512 MB で退避していたときには起きなかった
OOM kill があり得ます（`vi_global_planner` が SIGKILL された実測は
`simulator/docs/pi4_sim.md` の「C. 本命」）。広域地図を足すときは `compact_sink_dir` を
セットで書くこと。

VI のメモリを実際に頭打ちにしたいなら、手段は `map_scale` を上げる（状態数を
減らす）、`compact_sink_dir` を実ディスクに向ける（sink を匿名メモリから外す）、
`dense_limit_mb` で密の上限を切る、コンテナ側で `mem_limit` を掛ける、の
いずれかです。

このキーは元々 `vi_global_planner` にしかありませんでした。2026-08-04 に
`vi_planner` にも同じ既定値（512 MB）・同じ判断順で実装したので、いまは両方に
置けます。自動退避先だけがノードごとに違います（`/tmp/vi_global_planner_sink` /
`/tmp/vi_planner_sink`）。

ただし**ディスクへ逃がしたときの代償は `vi_planner` のほうが大きい**です。広域を
1 回解くだけの `vi_global_planner` と違い、`vi_planner` の追従は 10 Hz の制御ループの
中でパッチを置き直すたびに sink を読みます。コンテナの `/tmp` は tmpfs ではなく
書き込み層（= SD カード）なので、自動退避に頼らず `compact_sink_dir` で速い場所を
明示するほうが安全です。なお 19F の `vi_planner` はこの経路を使いません（密ソルバ）。
