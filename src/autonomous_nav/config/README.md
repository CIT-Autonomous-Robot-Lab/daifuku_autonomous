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
| `robot/raspicat.yaml` | `robot_bringup.launch.py` | `raspimouse` (LifecycleNode) へ直接 |

`robot/raspicat.yaml` だけは**上流ファイルの完全なコピー**で、差分ではありません。
launch_ros はノード自身の `parameters=` をグローバル (`SetParametersFromFile`) より
**後に**展開する = ノード側が勝つため、上流 `raspicat.launch.py` を include して
差分を重ねる方式では上書きできないからです。

## コメントの書き方

**既定値から変えたキーにだけ**、1 行で理由を書きます。変えていないキーには
コメントを付けません（付けると、どれを触ったのか分からなくなります）。

```yaml
controller_frequency: 10.0   # 既定 20.0: Pi4 が飽和し bond 心拍が途絶えた
```

「既定」が何を指すかは各ファイルの冒頭に書いてあります。

| ファイル | 「既定」の出どころ |
| --- | --- |
| `nav2/*.yaml` | `nav2_bringup` の `nav2_params.yaml` |
| `nav2/vi_planner.yaml` | 各ノードの `main.rs` の `declare_parameter` |
| `localization/emcl2.yaml` | `src/emcl2_ros2` の `declare_parameter` |
| `robot/raspicat.yaml` | 上流 `raspicat_ros` の `config/raspicat.param.yaml` |
| `overrides/*.yaml` | 重ねる先の断片の値（「断片 60:」のように書きます） |

1 行に収まらない計測や経緯は、ファイルには書かずに下の「値の由来」へ書き、
コメントからは `../README.md` を指します。地図や環境に固有の話は
`overrides/<名前>.yaml` 側に置きます。

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

既定は `robot_radius: 0.22`（nav2 の既定値そのままで、この機体を測った値では
ありません）。2026-08-03 の実測で車体は 420 (幅) x 450 (奥行) mm、`base_footprint`
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

既定は 0.2。2026-07-29 の実機計測で `map->odom` の遅延は 67 件すべてが
0.20〜0.47 s に収まり、最小値がちょうど 0.2 = 閾値そのものでした。閾値の直上に
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
  compact の場から起こして回します。ただし **`map_scale > 1` で密ソルバのままだと
  launch が止めます** — 全域の状態配列 56 B/state を実際に確保してしまうためです。
* `map_scale: 5` は `downsample_policy: optimistic`（ブロック内に free が 1 つでも
  あれば free）とセットです。既定の保守的プーリング（障害物優先）だと通路のセル幅が
  VI の遷移分布（約 2 セル幅）を下回り、`map_scale >= 4` で波がゴール近傍から広がりません。
  楽観側は通路を細らせない代わりに壁側に寄るので、実測の `footprint`
  （420x450mm、外接円 0.408m）/ `inflation_radius` 0.55 と合わせて通れるかは
  経路ごとに確認を。`map_scale: 5` 到達を確認した当時のコストマップは
  `robot_radius` 0.22（nav2 の既定値のまま）だったので、実機のほうが厳しい。
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
