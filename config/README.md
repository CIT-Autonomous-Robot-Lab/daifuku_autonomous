# config/

**設定の実体はここに全部あります**（パッケージ名は `daifuku_config`）。合成規則
（`params.py`）と `site_manager` / `config_sentinel` は `src/daifuku_config_manager`
の側で、あちらは実体を持ちません。

1 段目の `bringup/` と `stack/` は**どの launch が読むか**で分かれていて、これが
`overrides/*.yaml` の 1 段目（`daifuku_bringup:` / `daifuku_stack:`）と
`config_sentinel` が指紋を取る単位の両方を兼ねます。`overrides/*.yaml` は
`MID360_config.json` を除く全部に重ねられます（下の「上書き」）。

| 場所 | 読む launch | 渡し方 |
| --- | --- | --- |
| `overrides/*.yaml` | すべて | 下のどれかへ重ねる（`overrides:=`）。行き先はパッケージ名とノード名で決まる |
| `stack/nav2/*.yaml` | `navigation.launch.py` | 起動時に 1 つへ合成して `params_file` に渡る |
| `stack/localization/emcl2.yaml` | `navigation.launch.py` | `emcl2_params_file` でノードへ直接 |
| `stack/lifecycle_bond.yaml` | `navigation.launch.py` | `bond_params_file` を `SetParametersFromFile` でグループ内の全ノードへ注入 |
| `stack/mapping/slam_toolbox.yaml` | `mapping.launch.py` | `slam_params_file` でノードへ直接 |
| `bringup/sensors/*.yaml` | `lidar_bringup.launch.py` / `odom_fusion.launch.py` | 各ノードへ直接（`scan_filter_params_file`、`mid360_ekf_params_file` など） |
| `bringup/sensors/MID360_config.json` | `lidar_bringup.launch.py` | `livox_ros_driver2` へ直接。**ROS のパラメータファイルではない**ので上書きの対象外 |
| `bringup/robot/raspicat_driver.yaml` | `robot_bringup.launch.py` | `raspicat_driver` (LifecycleNode) へ直接。`driver:=original` (自前実装 / 標準 / Pi 4・Pi 5) |
| `bringup/robot/raspicat.yaml` | `robot_bringup.launch.py` | `raspimouse` (LifecycleNode) へ直接。`driver:=raspimouse` (公式実装 / rtmouse 入りの Pi 4 のみ) |
| `bringup/robot/twist_mux.yaml` | `robot_bringup.launch.py` | `twist_mux` へ直接。`twist_mux:=true` (既定) のときだけ |
| `bringup/robot/joy_teleop.yaml` | `robot_bringup.launch.py` | `joy_node` と `joy_teleop` の**両方**へ直接（1 ファイルに 2 ノード分）。`joy:=true` (既定) のときだけ |

**`bringup/` の値を変えたら `docker compose up -d` が要ります。** navigation を
立て直しても、常駐している raspicat サービスは読み直しません。

`lidar_bringup.launch.py` と `odom_fusion.launch.py` は `robot_bringup.launch.py` が
include します。単独でも立てられます（`simulator/` はそうしています）。

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
| `sensors/mid360_elevation.yaml` | `src/elevation_filter.py` の `declare_parameter` |
| `sensors/scan_filter.yaml` | `laser_filters` の `sector_filter.h`（既定なし＝全項目必須） |
| `robot/raspicat.yaml` | 上流 `raspicat_ros` の `raspicat/config/raspicat.param.yaml` |
| `robot/raspicat_driver.yaml` | `src/raspicat_driver` の `src/raspicat_driver/node.py` |
| `robot/twist_mux.yaml` | `twist_mux` の `twist_mux.cpp`（既定なし＝書いた値がすべて） |
| `robot/joy_teleop.yaml` | `joy` の `joy_node.cpp` と、`src/joy_teleop.py` の `declare_parameter` |
| `overrides/*.yaml` | 重ねる先の設定ファイルの値（「断片 60:」のように書きます） |

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
| `emcl2.laser_min_range` / `laser_max_range` | 宣言はされ `initPF` で一度読まれるが、`Mcl::setScan` が毎スキャン `/scan` の `range_min` / `range_max` で上書きする。実効値は `sensors/mid360_scan.yaml` の 0.23 / 70.0（同じ `Scan` の `scan_increment` のほうは上書きされないので効く） |
| `ekf_filter_node.odom0_nodelay` / `imu0_nodelay` | 上流ドキュメントにはあるが、ROS 2 版の `ros_filter.cpp` は読まない（ROS 1 の `tcpNoDelay` 由来） |
| `controller_server.FollowPath.stateful` | DWB に無い。ゴール判定側の同名パラメータと混同したもの |

## nav2/ の合成

`nav2/*.yaml` はファイル名順に読まれ、1 つの一時ファイルへ束ねられます。
そのパスは起動ログに出ます。

```
[INFO] [launch.user]: params: params_file: 8 fragments from .../config/nav2 -> /tmp/params_file_xxxx.yaml
```

分割は「ノード単位で重複なし」が前提です。同じノード名が 2 つの断片にあると、
キーが重なっていなくても起動時にエラーで止まります。束ねる前にノード名だけを見て
弾くので、1 つのノードの設定を 2 ファイルに割れません（どちらが勝つか分からない
状態を作らないため）。断片どうしは深くマージしません。深いマージが効くのは
次節の override を重ねるときだけです。

| ファイル | 含むノード |
| --- | --- |
| `nav2/amcl.yaml` | `amcl` |
| `nav2/behaviors.yaml` | `smoother_server`, `behavior_server`, `waypoint_follower`, `velocity_smoother` |
| `nav2/bt_navigator.yaml` | `bt_navigator` と付随の 2 ノード |
| `nav2/controller_server.yaml` | `controller_server`（DWB） |
| `nav2/costmaps.yaml` | `local_costmap`, `global_costmap` |
| `nav2/map_server.yaml` | `map_server`, `map_saver` |
| `nav2/planner_server.yaml` | `planner_server`（navfn） |
| `nav2/vi_planner.yaml` | `vi_planner` |

`amcl` が `localization/` ではなく `nav2/` にあるのは、nav2 の
`localization_launch.py` が `params_file` の中から読むためです。

### 合成には入るが読まれない断片（`nav2:=false`）

**束ねるのは常に 8 ファイル全部です。何が実際に読まれるかは、どのノードが立つかで
決まります。** `nav2` の既定は `false`（[navigation.md](../docs/usage/navigation.md#nav2を立てるかどうかnav2falseが既定)）
で、素で起動すると Nav2 の navigation ノードが 1 つも立ちません。そのとき効くのは

| 断片 | `nav2:=false` |
| --- | --- |
| `vi_planner.yaml` | 効く |
| `map_server.yaml` | 効く（自己位置側なので構成によらない） |
| `behaviors.yaml` | `velocity_smoother` の節だけ効く。残り 3 ノードは立たない |
| `amcl.yaml` | `localization:=amcl` のときだけ |
| `bt_navigator.yaml` / `controller_server.yaml` / `costmaps.yaml` / `planner_server.yaml` | **どれも立たない = まるごと無視** |

無視されるほうを書き換えても**エラーも警告も出ません**。同じ意味のキーの移り先は
`vi_planner.yaml` です（`behaviors.yaml` の `waypoint_follower` の `stop_on_failure` /
`waypoint_pause_duration` → `vi_planner.yaml` の `stop_on_failure` /
`waypoint_pause_sec`、BT の `RecoveryNode number_of_retries` → `goal_retry_limit`）。

`overrides/*.yaml` の行き先判定は**この影響を受けません**。行き先はノード名で決まり、
判定するのは「その名前を宣言している断片があるか」だけなので、立たないノードへの
override も**通ります**（そして黙って無視されます）。

## 上書き（override）

優先順位は下ほど強く、**後勝ち**です。

1. 土台 = その launch が渡している設定ファイル（`navigation` の `params_file` だけは
   `nav2/*.yaml` の合成結果。`params_file:=` を明示した場合はそのファイル）
2. `overrides:=<名前>` → `overrides/<名前>.yaml`（カンマ区切りで複数可）
3. `extra_params_file:=<パス>` → 任意のファイル（リポジトリ外の一時的な上書き用）

**行き先はパッケージ名とノード名で決まります。** 1 段目が `daifuku_bringup:` か
`daifuku_stack:` で、**各 launch は自分のパッケージ名の部分木しか読みません**。
2 段目がノード名で、`emcl2:` の節は `localization/emcl2.yaml` へ、`slam_toolbox:` は
`mapping/slam_toolbox.yaml` へ、`pointcloud_to_laserscan:` は
`daifuku_bringup` の `sensors/mid360_scan.yaml` へ、というように、同じノード名を
宣言している設定ファイルの上に深くマージされます。書きかたと重ね方は 3 つとも
同じで、`extra_params_file` も同じ規則で配られます。

**1 地図 = 1 ファイル**です。場所が決まれば LiDAR の帯（機体側）も emcl2 の調整
（自律移動側）も決まる、という 1 つの話なので、パッケージでは割っていません。
パッケージ名の段は、その 1 ファイルをどちらの launch がどこまで読むかを
**明示するため**にあります。

その launch が読まない設定ファイル宛の節は何も起こしません
（`mapping.launch.py` に `overrides:=map_19f` を渡しても、`emcl2:` と `vi_planner:` は
単に行き先が無いだけで害はありません）。**パッケージが違う節はそもそも読まれません**
（`lidar_bringup` は `daifuku_stack:` の下を見ません）。

`params_file:=` で土台を差し替えても行き先は変わりません。渡したファイルに
書かれていなくても、`nav2/*.yaml` が宣言しているノードの節はそこへ載ります
（土台を替えた途端に nav2 宛の節が黙って消えると、探しようがないため）。

上書きできるのは、上の表のうち `MID360_config.json` を除く全部です。
`livox_ros_driver2` の設定は ROS のパラメータファイルではない（ノード名も
`ros__parameters` も無い）ので、この仕組みに乗りません。`mid360_config:=<パス>` で
ファイルごと差し替えてください。

`overrides` の既定値は **`daifuku_config_manager` の `config/site` の 1 行**（既定
`map_19f`）で、すべての launch が同じものを見ます。さらに `navigation.launch.py` は
`map` の既定もそこから導きます。場所が変われば LiDAR の帯も EMCL2 の調整も地図も
一緒に変わるので、**人が動かす値を 1 つにしてある**という趣旨です。
`overrides` は地図を変えると**置き換え**になります（追加ではありません）。

**どの地図を読むかは、その overrides 自身が `site:` 節で宣言します。**

```yaml
site:
  map: map_19f.yaml   # daifuku_stack の maps/ からの相対パス (絶対パスも可)
```

`site:` は 1 段目に書ける予約節で、パッケージ名の段には並べません。「その場所そのものに
付く値」の置き場で、いまは地図だけが入っています。**overrides の名前と地図のファイル名は
揃っていなくて構いません**（2026-08-07 に「同じ名前の地図を読む」規約をやめました。
どの地図を読むかがファイルのどこにも書かれておらず、差し替えるには名前ごと揃え直す
必要があったためです）。

**切り替えは `tools/site.sh <名前>`。** 機体側（LiDAR の帯）を読むのは常駐している
raspicat サービスで、**起動時にしか読みません**。スクリプトはファイルの書き換えと
`docker compose restart raspicat` の両方をやります。`overrides:=` を navigation へ
渡しても効くのは `daifuku_stack:` の部分木だけで、`mapping` から LiDAR の帯を
変えられないのも同じ理由です（新しい場所で地図を作るときは、SLAM を始める前に
`tools/site.sh` を通してください）。

```bash
# 場所を切り替える (config/site を書いて raspicat を立て直す)
tools/site.sh map_tsudanuma

# 自律移動側。map も overrides も config/site から来るので渡さない
ros2 launch daifuku_stack navigation.launch.py planner:=vi local_planner:=nav2
```

何も重ねないときは `overrides:=none` です。`ros2 launch` は値が空の
`overrides:=` を malformed として弾くので、空文字ではなく `none` を使います。
**`none` は場所を名乗らないので、`map:=` と対で渡してください**（作ったばかりで
まだ override の無い地図を試すとき）。`site: map:` の無い overrides を
重ねたときも同じです。どちらも**既定の地図へ落とさず起動時にエラーで止めます** —
別の場所にいるのに 19F の地図で自己位置を推定し始めるほうが危ないためです。

**地図を渡し替えて `overrides` を放置することはもうできません。** `map:=` を明示した
ときは `site: map:` と同じファイルを指しているかを見て、違えば起動時にエラーで止まります
（`nav2_params.resolve_map`）。以前は別の地図に 19F 用の EMCL2 調整が載ったまま
黙って走っていました。存在しない override 名を渡した場合は、選べる名前を並べた
エラーで止まります。

`simulator/container/nav_container.sh` と `simulator/container/run_case.sh` は、
`MAP_NAME` と同名の override があればそれを、無ければ `none` を**必ず明示的に**
渡します（`OVERRIDES=` で上書き可）。既定任せにすると同じ取り違えが起きるためです。
**参照先は `daifuku_config_manager` の share** で、`maps/` を持つ `daifuku_stack` とは
置き場が違います（2026-08-07 まで後者を見ていて、どの地図でも `none` に落ちていました）。

### 何がどこへ行ったかを見る

重なった設定ファイルは一時ファイルに書き出され、起動ログにその 1 行が出ます。
`(+ ...)` が「どの override のどの節を重ねたか」です。

```
[INFO] [launch.user]: params: params_file: 8 fragments from .../daifuku_config/stack/nav2 -> /tmp/params_file_xxxx.yaml (+ overrides:map_19f -> vi_planner)
[INFO] [launch.user]: params: emcl2_params_file: .../config/stack/localization/emcl2.yaml -> /tmp/emcl2_params_file_xxxx.yaml (+ overrides:map_19f -> emcl2)
```

行が出ないファイルは、重なるものが無かったので土台がそのままノードへ渡っています
（`params_file` だけは断片の合成が要るので必ず出ます）。書いたのに行が出ないなら、
その launch がその設定ファイルを読んでいません。

### 新しい override を足す

`config/overrides/<地図名や状況>.yaml` を作り、
**変えたいキーだけ**を書きます。パッケージ名・ノード名・`ros__parameters` の 3 段が
必要です。

```yaml
daifuku_bringup:            # 機体側。変えたら docker compose up -d
  elevation_filter:         # -> daifuku_bringup の sensors/mid360_elevation.yaml
    ros__parameters:
      min_elevation_deg: 5.0

daifuku_stack:              # 自律移動側
  vi_planner:               # -> nav2/vi_planner.yaml (params_file の合成結果)
    ros__parameters:
      safety_radius_penalty: 1

  emcl2:                    # -> localization/emcl2.yaml
    ros__parameters:
      alpha_threshold: 0.2

  local_costmap:            # -> nav2/costmaps.yaml (1 段深い形もそのまま書く)
    local_costmap:
      ros__parameters:
        inflation_layer:
          inflation_radius: 0.45
```

**ファイルを新しく足したときは 1 度ビルドを通してください。** `setup.py` の `glob` は
ビルド時にしか展開されないので、足しただけでは `overrides:=` の一覧に出てきません
（既にあるファイルの値を直すだけならビルドは要りません）。

間違えると起動時にエラーで止まります。**パッケージ名**が `KNOWN_PACKAGES`
（`params.py`）に無い場合——どの launch からも読まれない部分木になるため——と、
**ノード名**がそのパッケージのどの設定ファイルにも無い場合（近い名前を出します）の
2 通りです。黙って消えると「書いたのに効かない」を探せないためで、その launch が
読まないだけの節（`mapping` での `emcl2:`）はエラーにしません。

`nav2/*.yaml` を丸ごと複製しないでください。既定値が変わったときに追従できません。

### 効かないときに疑うところ

`SetParameter` / `SetParametersFromFile` は、設定ファイルに**既にあるキー**を
上書きできません（launch_ros はグローバルパラメータを先に、ノード個別の
`parameters=` を後に渡すため、後勝ちでノード側が勝つ）。
そのため override は設定ファイルそのものをマージして作っています。
逆に `lifecycle_bond.yaml` の `bond_timeout` が `SetParametersFromFile` で効くのは、
そのキーが `nav2/*.yaml` のどこにも無いからです。

上書きが効かないときに見るのは順に、起動ログの `params:` の行（上の節）、
ノード名の綴り、`ros__parameters` の段（1 段忘れると節ごと無視されます。
`costmaps.yaml` のようにノード名が 2 段のものはその形のまま書く）、
そのキーがノード側で `declare_parameter` されているか（宣言されていないキーは
ROS 2 が黙って捨てます）です。

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
ros2 topic pub --times 5 /cmd_vel_teleop geometry_msgs/msg/Twist \
  '{linear: {x: 0.0}, angular: {z: 0.0}}'
```

`/cmd_vel` ではなく `/cmd_vel_teleop` なのは、そちらが仲裁（`twist_mux.yaml`）の
手動側の入口だからです（優先度は自律側のほうが上なので、**自律走行中は届きません**）。
`twist_mux:=false` で立てているなら `/cmd_vel` へ投げてください。

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

**`pulses_per_revolution: 400.0` は上流のままで、この機体では間違いです。** 2026-08-04 に
Pi 5 実機で、右車輪だけを一定周波数で回してエンコーダで止める形で測りました
（`cmd_vel` は左が 0 rad/s になる `linear.x = ω × tread/2` の組み合わせ、ステップ
周波数は PWM の `period` を読んだ実測値）。

| パルス | 実測の回転 | エンコーダ | ステップ |
| --- | --- | --- | --- |
| 5506 | 4.944 回転（5 回転に 20° 足りず） | 1113.6 /回転 | 567.8 /回転 |
| 11148 | 9.972 回転（10 回転に 10° 足りず） | 1118.1 /回転 | 570.4 /回転 |

目視誤差が半分になる後者を採って **1118 / 570** としています。**この 2 つは 1.960 倍
違います**（比のほうは 142 秒の計測なので 0.5% 以下の精度で出ています）。エンコーダと
ステッピングが同じ軸に載っていない、ということです。上流はこれを 1 つの数で兼ねて
いるので、この機体では**どちらに合わせても片方が壊れます**。

`raspimouse` ノード（`driver:=raspimouse`）はここが直せません。`cmd_vel` → ステップ
周波数の換算に **400 を直書き**していて、パラメータはオドメトリ側にしか効かないため
です。結果として、この機体を公式実装で走らせると**指令の 70% の速度**で走ります
（`400 / 570`）。`use_pulse_counters: false` なので `odom` は指令値の積分、つまり
**指令どおりの値を報告し続けます**。ログにも `odom` にも何も出ません。

寸法を実測値へ直した 2026-08-03 の修正で、この誤差は**大きくなりました**。上流の
車輪径 0.1524 と直書きの 400 は互いに打ち消し合っていて（`0.200/0.1524 × 400/568 =
0.924`）、偶然 92% に収まっていたためです。径だけを正すと 70% になります。**径を
戻してはいけません**（径は実測値で、回頭はトレッドとの比で決まるため、径を歪めると
旋回だけが壊れます）。直すなら上流の直書きを直すしかありません。

自前実装（`raspicat_driver.yaml`）はこの 2 つを別のキーに分けてあるので、そちらは
正しく走ります。巻尺での最終確認（0.1 m/s を 10 秒指令して実移動距離と `/odom` の
変位を比べる）は、荷重が乗った状態での脱調とすべりを見るために別途必要です。

### `raspicat_driver.yaml`（自前ドライバ / Pi 4・Pi 5）

`raspicat.yaml` は公式実装（`driver:=raspimouse`）用で、rtmouse カーネルモジュールが
出す `/dev/rt*` を `raspimouse` ノードが読む構成が前提です。自前実装
（`driver:=original`、[`src/raspicat_driver`](../src/raspicat_driver/README.md)）は
モータ経路をユーザ空間から直接扱い、そのパラメータがこのファイルです。**リポジトリの
標準はこちら**で、Docker の入口（`.env` の `COMPOSE_FILE`）が `driver:=original` を
渡します。`driver:` という引数そのものの既定値だけは `raspimouse` のままです。

Pi 4 と Pi 5 で 1 ファイルです。機種差は `model: auto` が device-tree から判定して
チップの同定（`gpiochip` のラベルと `pwmchip`）だけを切り替えます。ピン番号・PWM
チャネル・I2C アドレスは制御基板側の性質なので両機種で同じです。

* **Pi 5 ではこちらしか選べません。** rtmouse は BCM2711 のレジスタを `ioremap` する
  ので、GPIO/PWM が RP1 側にある Pi 5 では動きません。
* **Pi 4 では公式実装と排他です。** rtmouse が載っていると `configure` を拒否します
  （`allow_rtmouse` で上書き可）。両方が GPIO 16/6/5 と PWM を持つと、カーネルは
  衝突を検出しないまま車輪が逆に回り得ます。

寸法と補正係数は上の 2 節と同じ値です。`raspicat.yaml` と違うのは次の 5 点で、理由は
[`docs/setup/raspberry-pi-4.md`](../docs/setup/raspberry-pi-4.md) と
[`raspberry-pi-5.md`](../docs/setup/raspberry-pi-5.md)。

* **`pulses_per_revolution`（1118.0）と `steps_per_revolution`（570.0）が別のキー**
  です。前者はエンコーダのパルス数で `odom` 専用、後者はステップ数で `cmd_vel` →
  周波数専用。上の実測のとおり 1.96 倍違うので、上流のように 1 つで兼ねると、機体は
  指令より遅く走るのに `odom` は指令どおりを報告する、という形で静かにずれます。
* `use_pulse_counters` の既定が `true`。ユーザ空間の `ioctl` は失敗を返して戻って
  くるので、rtmouse のような D 状態固着が起きません。連続失敗が
  `counter_error_limit` に達すると `cmd_vel` 積分へ落ち、応答が戻れば自動で復帰
  します。
* `odom_hz` の既定が 50.0（`raspicat.yaml` は 100.0）。1 周期あたり I2C を
  6 トランザクション使うので、62.5 kHz のバス占有を半分に落としてあります
  （カウンタ 1 個につき 3 回。上位バイトで下位バイトを挟んで桁上がりを検出する）。
* **`control_mode` があります**（`wheel_kp` / `wheel_ki` / `wheel_correction_limit`）。
  **既定は `closed`** で、エンコーダが返した車輪速度と指令との差を PI で積んで
  周波数に足します（実装は `src/raspicat_driver/src/raspicat_driver/control.py`）。
  `open` にすると上流とまったく同じ、指令をそのままステップ周波数にする経路に
  なります。**閉ループはすべりと負荷を消すためのもので、脱調の対策にはなりません。**
  パルスの符号は直前に書いた方向線から借りているので、脱調した車輪も「前進した」
  カウントを返します。ループはそれを「遅れている」と読んで周波数を上げ、脱調を
  さらに悪化させます。`wheel_correction_limit`（既定 2.0 rad/s ≒ 車輪外周で
  0.2 m/s）を小さく保つのはそのためで、補正が指令と逆向きに回すことも無いように
  してあります。**荷重をかけて遅くなるようなら脱調なので `open` へ戻してください。**
  `use_pulse_counters: false` と組むと測る相手が居ないので `open` に落ちます
  （起動時に `error` を出し、`configured:` の行の `control=` にも `open` と出る。
  ここで `configure` を落とすと、カウンタを切って切り分けようとしただけで LiDAR と
  EKF まで道連れになり、`restart: unless-stopped` で回り続けるため）。
  **ゲインの 3 つだけは実行中に読み直す**
  ので、走らせながら `ros2 param set /raspicat_driver wheel_ki 2.0` で詰められます
  （この yaml を直すと `config_sentinel` が launch ごと落とすため）。`wheel_kp` の
  既定が 0.0 なのは、`odom_hz` の 1 周期ではエンコーダ 1 カウントが 0.28 rad/s
  あり、比例項がその量子化を毎周期そのまま周波数に出してしまうからです。積分項に
  同じ問題はありません（カウンタが自由走行なので、積分値は「走らなかった距離」に
  収束する）。
* `publish_tf` があります。EKF に `odom -> base_footprint` を出させる構成
  （`use_mid360_imu:=true`）では `false` になります。`robot_bringup.launch.py` が
  その引数を受けたときに自分で渡すので、**このファイルを直す必要はありません**。

* **LED・ブザー・スイッチのキーがあります**（`use_leds` / `use_switches` /
  `use_buzzer` / `gpio_leds` / `gpio_switches` / `gpio_buzzer` / `switch_pull_up` /
  `switches_hz` / `buzzer_pwm_channel` / `buzzer_max_frequency`）。トピックの型は
  公式実装と同じで、`/leds` が `raspimouse_msgs/Leds`、`/switches` が
  `raspimouse_msgs/Switches`（true が押下）、`/buzzer` が `std_msgs/Int16`（Hz、
  0 で停止）です。掴めないピンがあっても走行には影響しません（起動ログの
  `peripherals:` の行に出るだけ）。
  **`buzzer_pwm_channel` の既定 `-1`（ソフト生成）は意図的です。** ブザーの GPIO19 は
  右モータのステップクロックと同じ PWM チャネルで、sysfs からはピンの alt 機能を
  変えられないので、両方を PWM に mux すると鳴らすたびに右車輪が回ります。モータと
  同じ番号を書いた場合と、Pi 4 で 0 以上を書いた場合は `configure` が拒否します。
  経緯は [`src/raspicat_driver/README.md`](../src/raspicat_driver/README.md)。

配線に関わるキー（`gpio_*` / `pwm*` / `i2c_*` / `direction_*_forward_level`）は
すべて rtmouse の `rtmouse.h` から写した値で、**実機で確認していません**。
確認項目と直しかたは上の 2 つのドキュメントの表にあります。

### `twist_mux.yaml` の配線と優先度

**入れた理由は書き手が 2 つあったからです。** 自律側は `velocity_smoother` が
`cmd_vel_smoothed -> cmd_vel` の remap で `/cmd_vel` へ出し（nav2 の
`navigation_launch.py` と `vi_planner` のそれ、どちらも同じ）、手動側は
`control.sh teleop` の `teleop_twist_keyboard` / `teleop_twist_joy` が同じ
`/cmd_vel` へ直接出していました。仲裁が無いので、自律走行中に遠隔操作を開くと
両者のメッセージがそのまま交互にドライバへ届きます。

配線は次のとおりで、**ドライバの購読先だけを変えてあります**。

```text
controller_server / vi_planner ─ /cmd_vel_nav ─ velocity_smoother ─ /cmd_vel ─┐
                                                                              ├→ twist_mux → /cmd_vel_mux → ドライバ
teleop / control.sh stop ─────────────────────── /cmd_vel_teleop ─────────────┘
```

nav2 側の remap には触れていません（include している上流の launch の中にあるので
外から差し替えられない）。そのぶん `/cmd_vel` は「自律側の出力」の意味になり、
ドライバが購読するのは `/cmd_vel_mux` です。両ドライバとも相対名 `cmd_vel` で
購読しているので、`robot_bringup.launch.py` の remap 1 行で両方に効きます。

**優先度は自律側（`/cmd_vel`、100）が上で、teleop（`/cmd_vel_teleop`、10）が下です。**
twist_mux が中継するのは、その時点で優先度が最大のトピックが**メッセージを受けた
とき**だけなので、teleop が通るのは自律側が `timeout`（0.5 秒）のあいだ黙っている
ときに限られます。**したがって自律走行中は `control.sh teleop` も `control.sh stop`
も効きません**（エラーは出ず、ただ機体が言うことを聞かない）。パッド
（`joy_teleop`）だけは別で、teleop に入るときに `/follow_waypoints` と
`/navigate_to_pose` を取り消すので自律側が黙り、0.5 秒後に手動が通ります。

**これは非常停止ではありません。** 確実に止める手段は今までどおりモータ電源
（`motor_power` / `control.sh motor off` / パッドの BACK 長押し）です。

一方で、**中継が止まると指令も止まります**。twist_mux が落ちるとドライバには何も
届かず、ドライバは最後の指令を `cmd_vel_timeout`（既定 60 秒）まで保持します。
アクチュエータ経路にノードを 1 つ増やした代償で、この変更で新しく増えた失敗の形は
これです。仲裁ごと外すなら `twist_mux:=false`（ドライバの購読先は `/cmd_vel` に
戻り、`control.sh` には `CMD_VEL_TOPIC=/cmd_vel` を渡す）。

`locks`（`std_msgs/Bool` で下位のトピックをまとめて塞ぐ層）は書いていません。
`isLocked()` が `hasExpired() || data` で、受信前のスタンプは 0.0 = 期限切れ扱い
なので、**配信元を用意せずに書くと塞がったまま**になり、その優先度未満の指令が
すべて止まります。非常停止スイッチを足すときにセットで入れるものです。

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

### `emcl2.yaml` の運動モデル（`odom_*_dev_*`）

**症状**: 初期位置を合わせても emcl2 が自己位置を動かし続け、オドメトリが効いて
いないように見える。**原因**: そう設定してあったからです。4 つの `odom_*_dev_*` は
2026-08-07 まで emcl2 のノード既定のままで、この機体の実力より 1 桁大きい値でした。

`OdomModel::setDev` は分散を移動量に比例させます（`fw_dev = sqrt(|length| *
fw_var_per_fw + |angle| * fw_var_per_rot)`）。距離 L [m] を直進したときの粒子の
ばらつきは σ = 値 × √L なので、既定値は次の意味になります。

| | 既定 | 1 m 直進 | 10 m 直進 | 90 度旋回 |
| --- | --- | --- | --- | --- |
| 位置 `odom_fw_dev_per_fw` | 0.19 | 0.19 m | 0.60 m | — |
| 方位 `odom_rot_dev_per_fw` | 0.13 | 7.4 度 | 24 度 | — |
| 方位 `odom_rot_dev_per_rot` | 0.2 | — | — | 14 度 |

この機体のオドメトリは、車輪径 0.2m・トレッド 0.35m・エンコーダ 1118 pulses/rev の
**実測値**を入れた `raspicat_driver` と、方位を Mid-360 のジャイロ（バイアス除去済み、
残ノイズ 0.074 deg/s）で押さえる EKF の組です。1 m で 7.4 度も曲がりません。

事前分布が実力の 10 倍広いと、その中では観測がほぼ一意に姿勢を決めます。emcl2 の
重みは尤度場（壁から 0.2 m で 0 に落ちる線形）を**全ビーム（0.5 度刻みの 720 本）
ぶん足した和**なので、数 cm ずれた粒子は 1 回の更新で大きく負けます。結果として
出力は実質スキャンマッチャそのものになり、オドメトリは探索範囲を与えるだけの役に
なっていました。

新しい値は 0.05 / 0.02 / 0.05（1 m 直進で 0.05 m・1.15 度、90 度旋回で 3.6 度）で、
実測の実力よりはまだ数倍広く取ってあります。**これ以上は下げないこと。**
リサンプリングは roughening（ノイズの再注入）をしないので、**この 4 つが粒子の
多様性の唯一の供給源**です。潰すと本当にずれたときに二度と復帰できません
（`alpha_threshold` による膨張リセットは、貫通を検知したときにしか起きません）。

**変えていないもの: `odom_freq: 20`。** 同じスキャンを 2 回食っています。
`emcl2_node.cpp` の `main` は `odom_freq` の周期で `loop()` を回し、毎回
`sensorUpdate`（重み付け → リサンプリング）を通します。`Mcl::sensorUpdate` には
「同じスキャンなら抜ける」ガードがありますが、**実際に呼ばれる
`ExpResetMcl2::sensorUpdate` にはありません**。`/scan` は
`lidar_bringup.launch.py` の `mid360_publish_freq` 既定 10.0 Hz なので、倍率は 2 です。

**それでも 10 へ下げていません。** `loop()` は運動更新・観測更新・`map->odom` の
発行を同じ 1 回で済ませるので、下げると**TF の発行間隔も倍**になります
（`transform_tolerance` 0.2 秒に対し、実測の遅延はもともと 0.20〜0.47 秒 =
`controller_server` の節）。しかも同じ公称周期の 2 ループは互いに滑るので、
倍率は 2 で固定ではなく 0 回・1 回・2 回が混じる形になり、かえって読みにくく
なります。加えて、事前分布を 4〜6 倍狭めた今は**二度食いの効きも小さくなって
います**（狭い事前分布に同じ証拠を 2 回掛けても、広い事前分布のときほど動かない）。
まず支配的な原因だけを直して測り、残りが問題になってから触る、という順です
（同時に 2 つ変えると実機で切り分けられません）。

残る 2 つは設定では直せません。どちらもコード側（`src/emcl2_ros2` = `vcs import`
で入る外部パッケージ）の話です。

* リサンプリング後に重みが 1/N に戻らない（`Mcl::resampling` が `w_` ごとコピー
  する）ので、勝った粒子は複製と重みの両方で有利になり、観測の効きが増幅されます。
  出力の `meanPose` は重みなしの平均なので効き方は間接的です。
* 事前分布と観測の時刻が合っていません。`getOdomPose` は `rclcpp::Time(0)` ＝
  最新の odom を引いて粒子を現在時刻まで進めますが、スキャンは
  `livox -> pointcloud_to_laserscan -> restamp_scan -> filter chain` を通ってきた
  過去の情報で、動き補正はどこにも入りません。**旋回中に最も悪化します。**

**未検証** — 実機では走らせていません。確かめ方は次の順です。

1. **前提**: オドメトリが健全であること。`raspicat_driver` は I2C 読み出しが 5 回
   連続で失敗すると（`counter_error_limit`）**黙って `cmd_vel` の積分に落ちます**。
   モータ電源 OFF で回転を指令し、`/wheel/odom` が動かないことを見てください。
   落ちていれば emcl2 は正しく壊れたオドメトリと戦っているので、この節の話は
   前提から成立しません。
2. モータ電源 OFF で機体を手で数 m 押し、`/odom` の軌跡と `mcl_pose` の差を見る。
   これがオドメトリの実力で、0.05 × √L より十分小さければこの値は正当です。
3. RViz の `/particle_cloud` が走行中も潰れずに広がりを保っていること。
4. ログの `ALPHA: <値> / <閾値>` に `RESET` が混ざっていないこと。混ざるなら
   それは運動モデルではなく地図と実環境の不整合の側（`overrides/map_19f.yaml`）で、
   この変更では直りません。
5. `map->odom` の遅延を測り直すこと。事前分布が狭まったぶん 1 周期あたりの
   引っ張りが変わるので、`controller_server` の節の 0.20〜0.47 秒（20 Hz・旧
   運動モデルでの実測）はそのままでは使えません。

戻すときは 4 つとも `emcl2_node.cpp` の既定（0.19 / 0.0001 / 0.13 / 0.2）へ。
**機体固有の値なので `overrides/` ではなくこの断片に置いてあります。**

### `vi_planner` の `safety_radius_penalty: 30`

単位は「秒/セル」で、`safety_radius`（0.2m）以内のセルを通るときの加算コストです。
1 手のコストが 1 秒なので、30 は「近寄るくらいなら 30 手迂回する」という強い忌避に
なります。細い通路ばかりの地図では下げる必要があります（`map_tsudanuma` の実例は
下の節）。

### `vi_planner` の `action_forward_m`（前へ出る 3 つを 0.5 m へ）

`action_forward_m` の 1・4・6 番目（`forward` / `rightfw` / `leftfw`）を **0.5 m** に
してあります。ノード既定は `[0.3, -0.2, 0.0, 0.2, 0.0, 0.2]` なので、直進が 0.3 → 0.5、
旋回付きが 0.2 → 0.5 です。**後退（2 番目）は 0.2 のまま**。津田沼の override は元から
0.5 だったので、前進の 3 つはここで断片と同値になりました（後退だけ 5/3 倍の 0.3333）。

この配列は**二役**です。価値反復の 1 手の変位であると同時に、`follow_path` が
`linear.x` へそのまま入れる速度指令でもあります（`vi_planner/src/main.rs` の
`Decision::Action` — `delta_fw` → `linear.x`、`delta_rot` → `angular.z`）。
1 手のコストは着地セルの `penalty`（自由セルなら 1）だけで、**進んだ距離には
依存しません**。つまり価値反復が最小化しているのは「1 m あたりの手数」で、
0.2 m の手は 0.3 m の手より**1 m あたり 1.5 倍高くつきます**。

経緯は 2 段階です（どちらも 2026-08-05）。まず旋回付きだけが既定の 0.2 で、
曲がりながら進む手がほとんど選ばれず**その場旋回 → 直進の繰り返し**になっていた
ので、直進と同じ 0.3 へそろえました。次に**全体の速度が遅かった**ので、前へ出る
3 つをまとめて 0.5 へ上げています。等しくしてあるのは「向きを変えるかどうかで
1 m あたりの手数が変わらない」状態を保つためで、**3 つは一緒に動かすこと**。

**上限を決めるのは `velocity_smoother` で、いまそこは 0.4 です。** `follow_path` の
`cmd_vel` は `velocity_smoother` を通ってから車輪へ行き（`nav2:=false` でも通る。
`navigation.launch.py` が `cmd_vel` → `cmd_vel_nav` → `cmd_vel_smoothed` → `cmd_vel`
と繋ぐ）、その `max_velocity` は `nav2/behaviors.yaml` が **DWB の `max_vel_x` に
合わせた `[0.4, 0.0, 1.0]`** に落としています（ノード既定は `[0.5, 0.0, 2.5]`）。
**上限を超える値を書くとそこで黙ってクリップされ**、価値反復は「1 手 = 1 秒」で
解いているのに機体はそれより遅く走ります（経路は closed-loop なので破綻はしませんが、
旋回半径が計画より小さくなり内側を切ります）。**つまりこの 0.5 は、断片のままだと
どの地図でも 0.4 で頭打ちです** — 外してあるのは `overrides/map_tsudanuma.yaml` の
`velocity_smoother` だけ（2026-08-08）。他の地図でも 0.5 を出したいなら、そちらに
同じ節を置くか `behaviors.yaml` の `max_velocity` を上げてください。実際の値は
`ros2 param get /velocity_smoother max_velocity` で見えます。

代償が 3 つあります。

* **旋回半径が 0.859 m から 1.432 m へ広がります**（`linear.x / angular.z` =
  0.5 / 20 deg/s）。曲がりきれない狭さでは価値反復が `right` / `left`（前進 0）を
  選ぶだけなので**壊れはしません**が、**狭隘部はこの変更で不利になります**。
  半径を戻したいなら `action_rotation_deg` を上げるほう（0.86 m 相当は 33 度）で、
  前進量を下げると 1 m あたりの手数の話に戻ります。
* **薄い壁のすり抜けが 0.3 m から 0.5 m へ広がります。** 遷移は着地セルしか見ない
  ので、1 手より薄い壁は跳び越え得ます。19F は `map_scale: 2` = 0.10 m/cell なので
  **5 セル未満の壁**が対象です。`safety_radius` 0.2 m の膨張が両側に入るぶん実際の
  忌避はもう少し手前から効きますが、**跳べる壁は確実に厚くなりました**。
* **ゴール圏との余裕が薄くなります。** ゴール判定は半径 `goal_margin_radius`
  0.3 m の円なので直径 0.6 m、1 手が 0.5 m。直線で近づく限り 0.6 > 0.5 で必ず
  どこかの手が円内へ落ちますが、**余裕は 0.1 m しかありません**（0.3 m の頃は
  倍あった）。ゴール手前で行ったり来たりするようなら疑うのはここで、動かすのは
  `goal_margin_radius`（= ゴール許容差そのものなので、緩める判断とセット）。

パッチのほうは通ります。`max_fw`（配列の絶対値の最大）が 0.5 になるので
`new_patch` の `reach_bound` は `floor(0.5/res)+1`、19F（0.10 m/cell）で 6 セル・
`half` 28 セルに対しウィンドウ 10 セルなので `win + reach < half` を満たします
（津田沼 0.25 m/cell では 4 / 14 / 4）。起動時に実測で検査されるので、破れば
`action_forward_m is too large` で止まります。

`overrides/map_19f.yaml` の `cost_drawing_threshold` は 180 → **108** にしました。
表示専用で、2026-07-29 の実測（分布）は 1 手 0.3 m の頃のものなので、同じ距離
（≒54 m）を指すよう 0.6 倍しただけです。**分布は測り直していません。**

**未検証** — 実機では走らせていません。旋回付きが実際に選ばれるようになったか、
狭隘部がどう変わるか、`velocity_smoother` の既定 `max_accel` 2.5 m/s²（`joy_teleop`
が脱調を避けて置いている 0.9 の 2.8 倍）で 0 → 0.5 の立ち上がりが脱調しないかは
どれも測っていません。脱調はエラーを出さず**指令より遅く・左右バラバラに回る**だけ
なので、直進が曲がるようなら `velocity_smoother.yaml` を作って `max_accel` を
下げるのが戻し方です。

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

nav2 既定は 1000ms。`planner:=vi` では `vi_planner` が `/map` を受け取って
から `compute_path_to_pose` を作るので、地図が大きいほど遅れます。`map_tsudanuma`
（23.5MB）では間に合わず、`bt_navigator` が `on_configure` で
"action server not available" を投げて bringup 全体が止まりました。

### `vi_planner` の `cost_drawing_threshold`

表示専用（`value_function` の色スケール上限、単位はステップ数≒秒）。経路にも
メモリにも影響しないので、読みにくければ動かして構いません。**地図ごとに測り直す
値**で、同梱の 2 地図はどちらも `overrides/` で上げてあります（19F が 108、津田沼が
600）。断片（`nav2/vi_planner.yaml`）の 60 が出るのは、どちらでもない地図のときです。

**60 では 19F の階調はほとんど潰れます。** 2026-07-29 実機計測（19F の地図を
0.10 m/cell に縮めた 458x289）では、到達可能セルの 66% が 100 に張り付き、
グラデーションが出るのはゴールから 60 ステップ以内の 6.83% だけでした
（未到達 -1 が 80.07% / 飽和 100 が 13.10% / 1..99 が 6.83%）。1 ステップ =
`action_forward_m` 0.3 m なので 60 ステップ ≒ 18 m 相当で、建物一周の廊下長に
足りていません。

同じ計測で到達可能セルの最大は 680 ステップ（≒204 m）、中央値 60 / p90 110 /
p99 300 でした。**`overrides/map_19f.yaml` の値はここから採っています。** p90 と
p99 の間へ置くと、運用上通る範囲に階調を集中させ、遠い裾だけ飽和させる選び方に
なるためで、当時は 180（≒54 m）でした。

**いま入っているのは 108 です。** 計測当時は 1 ステップ 0.3 m でしたが、断片の
`action_forward_m` が 0.5 m へ上がったぶん、同じ距離が 0.6 倍の手数で済みます
（下の `action_forward_m` の節）。180 × 0.6 = 108 で、**指している距離は ≒54 m の
まま**です。分布そのものは測り直していません。

別の地図では分布が変わります（飽和が広ければ小さすぎ、階調が下位に偏っていれば
大きすぎ）。**`overrides/map_tsudanuma.yaml` の 600 はこの実測にあたるものがなく、
地図の広がりから置いた見込みです**（未計測）。1 ステップは向こうも 0.5 m（前へ出る
3 つは断片と同値）なので 600 ≒ 300 m にあたり、294.4 x 200 m の地図をほぼ端まで
階調に入れる、という置き方です。断片の 60 では 30 m 相当しかなく、地図の大半が飽和
して 1 色になります。

**`window_cost_drawing_threshold`（`vi_planner` だけが持つ、`/local_window_value` の
色スケール上限）は連動しません。** ±1 m の窓なので別の値（断片の 10）のままです。

### `overrides/map_tsudanuma.yaml` の `safety_radius_penalty: 1`

既定は 30 [秒/セル]。1 手のコストは 1 秒なので、30 は「近寄るくらいなら 30 手
迂回する」という強い忌避です。津田沼は通路が細く（未観測を障害物とみなすため
1〜2 m 幅）、0.15 m セルではほとんどの自由セルが `safety_radius` 0.2 m 以内に
入ります（**この計測は `map_scale: 3` = 0.15 m セルの頃のもの**です。いまの
`map_scale: 5` = 0.25 m セルではセルがさらに粗く、同じことがより強く起きます）。VI の遷移はサブセルサンプリング付きの確率モデルなので、隣接状態間で
ペナルティの重みが変わり、価値関数が局所的に ±3 秒ゆらぎます。1 手 1 秒の前進より
揺らぎが大きいと、ノイズ無しの貪欲ロールアウトが降下できず、隣接 θ 間で往復して
`LoopDetected` になります（実測: penalty 30 で 83 手目で固着）。1.0 にすると同じ
地図・同じゴールで単調に降下し、104 姿勢でゴールに到達しました。揺らぎはペナルティ
にほぼ比例するので、10 秒未満なら 1 手 1 秒の進捗を下回るはずです。

### `map_tsudanuma` で `planner:=vi` を使うときの制約

* `local_planner:=vi`（両アクション）と `local_planner:=nav2`（同じ `vi_planner` を
  `follow: false` で立てて `controller_server` が追従）のどちらも使えます。どちらも
  同じノードなので `map_scale` もアウトオブコア経路 (`frontier2d_sparse_compact`) も
  同じです。
  `vi_planner` の狭域追従だけは密な状態配列を要るので、全域ではなくロボット近傍の
  パッチ（±1m ウィンドウ + 遷移到達距離 + 余裕、0.25m セルで 27x27x60 ≒ 2.5MB）を
  compact の場から起こして回します。狭域 → 広域のフィードバック（`global_sweep`、
  下節）もこの地図で効きます（compact では sink のタイル修復として動きます）。
  ただし**伝播にかかる時間はこの地図では未計測**です。
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
* メモリは `map_scale: 5` + compact で、`vi_planner` のピーク RSS が **実測 1.60GB**
  （うち sink の mmap が 648MB）。`map_scale: 3` + 保守的プーリングだった頃の
当時の広域専用ノード `vi_global_planner` の 3.98GB（匿名 2.16GB + mmap 1.81GB）から
  下がり、Pi4 4GB の枠には収まります（そのノードは 2026-08-08 の上流の整理で消え、
  広域だけの構成も同じ `vi_planner` になりました）。実測の詳細は
  `simulator/docs/pi4_sim.md` と `overrides/map_tsudanuma.yaml` のヘッダ。
  **ただしこの 1.60GB は sink をディスクへ逃がしていた頃の値です。** 2026-08-04 に
  `compact_sink_dir` を外して RAM 出力へ変えた（Pi 5 の 8GB が前提）ので、同じ 648MB が
  mmap ではなく匿名メモリになります。カーネルが追い出せなくなるぶん、**Pi4 4GB の枠に
  収まるという上の話はもう成り立ちません**（4GB 機で使うなら `compact_sink_dir` を
  戻すこと）。RAM 化後のピークは**未計測**です。
* 一方 **`simulator/` の pi4_sim ハーネスの枠（0.6 コアを stack 全体で共有）では、solve 中に
  emcl2 まで巻き込んで 900 秒でも `/plan` が出ません**。実機 Pi4 は 4 コアあるので
  同じにはなりませんが、`vi_threads: 3` を明示して 1 コアを stack に残すのは
  そのためです。実機での通し確認は別途。
* **`action_forward_m` を既定から変えるので、`value_iteration3` は ec2579d
  （2026-08-04、「vi_core の params を捨てて launch から行動集合を決められるようにする」）
  以降が要ります。** それより前の VI は行動集合を `vi_rs` のコンパイル時定数
  （`ACTION_FW = [0.3, -0.2, 0.0, 0.2, 0.0, 0.2]`）と照合し、違えば **exit 1 で即死**
  します。ログに出るのは launch の `process has died ... exit code 1` だけなので、
  症状は「プランナだけ上がらず nav2 が inactive のまま」に見えます。
* **全域スライスの 1 枚が重い地図です。** `/value_function` は 94 万セル（19F の
  13 万セルの 7 倍）で、compact なので 1 枚作るのに sink を全走査します。作るのは
  掃きスレッドが**ロックを握ったまま** 2 秒ごとなので、ここが 300 ms を超えると
  10 Hz の追従ループが 3 tick 連続で `try_lock` に失敗して機体が止まります。**未計測** —
  走行中に `/cmd_vel_mux` が 2 秒おきに途切れるなら、まず `publish_value_function` と
  `value_publish_interval_ms` を疑ってください（後者を 0 にすると掃き中の出し直しが
  止まり、solve 完了時の 1 枚だけになります）。

### `overrides/map_19f.yaml` の `map_scale: 2`

`map_scale` は**プランナ内部だけ**の作業解像度です。`/map`・コストマップ・emcl2 は
0.05 m/cell のままで、粗くなるのは VI が解く格子だけです。

| | scale 1 | scale 2 |
| --- | --- | --- |
| プランナ内部の格子 | 915x577 @0.05m | 458x289 @0.10m |
| 状態数（x60 θ） | 3168 万 | 794 万 |
| compact の確定出力（12 B/state） | 0.38 GB | 0.095 GB |
| **密の常駐（80 B/state）** | **2.53 GB** | **0.65 GB**（実測 654.8 MB） |

**2 にしている理由は solve を軽くするためです。** 断片の `solver` が compact に
なった今は必須ではありません（2026-07-29 の実機は compact scale 1 で solve 29.25 s /
RSS 833 MB で通っています）。状態数が 1/4 になるぶん solve も伝播も速くなります。

**密に戻すならこの scale 2 とセットです。** 密の常駐は 80 B/state — `states` 56 B に
加えて `set_sweep_orders` が掃き順を 6 本ぶん持つので +24 B/state — で、scale 1 の
2.53 GB は 4 GB の Pi4 に他のノードと同居させられません。scale 2 の実測は
**654.8 MB**（`states` 444.7 MB + `sweep_orders` 210.1 MB）です。

以前ここには「`map_scale > 1` は密ソルバでは通らない（launch が止める）」と
書いてありましたが、前提が逆でした。`map_scale` は密を**載せるための**手段です。
メモリ上限を見るのはノード側の `dense_limit_mb`（既定 1500 MB、断片では 4096 MB）
です。地図の実寸はノードしか知らないので、そちらのほうが正確に判定できます。

**この地図は `waypoint_prefetch` を `true` にしてあるので、表の数字は 2 倍で
読んでください。** 先読み中は価値関数が 2 本生きます。いま使っている compact では
sink が 0.095 GB × 2 = 0.19 GB で、断片の `compact_ram_limit_mb`（4096 MB）に
収まるのでディスクへも出ません。**密に戻すなら scale 2 で 1.3 GB、scale 1 で
5.0 GB** です。`dense_limit_mb` が見ているのが 1 本ぶんか 2 本の合計かは**未確認**で、
scale 1 + 先読みが起動時に止まってくれるかどうかも分かりません。

先読みそのものの効き方・条件・ログは
[`docs/usage/navigation.md`](../docs/usage/navigation.md#次の点を走行中に解いておくwaypoint_prefetch)。
ここで `true` にしているのは、巡回で点が変わるたびに入る 1 回ぶんの solve（19F の
実測で 29 秒、そのあいだ機体は止まったまま）を消すためです。**`map_tsudanuma` は
2026-08-07 に `true` にしたあと、2026-08-08 に `false` へ戻しました**（走行中の
固まりの容疑者を切り分けるため。あちらは solve が 87 秒、場は 648 MB × 2 = 1.3 GB
です）。**いま `true` なのはこの地図だけ**で、4 GB 機では外してください。

代償は 0.10 m/cell の粗さと、保守的プーリングで通路が片側最大 0.05 m 細ること。
**未検証** — この地図・この scale での solve 時間と経路そのものは測っていません
（参考: 2026-07-29 の実機は compact scale 1 で solve 29.25 s / RSS 833 MB / OOM なし）。

`map_tsudanuma` の `map_scale: 5` は `downsample_policy` /`action_forward_m` /
`goal_margin_radius` とセットでないと波がゴール近傍で止まりますが、2 では
1 手 = 5 セル（`action_forward_m` 0.5 m）、ゴール半径 3 セル、`safety_radius` 2 セルが
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

`global_sweep` はこれを埋めるもので、共有場を全域で掃き直して局所で上がった値を外へ
広げます。新しい Bellman 更新は書かず `value_iteration_at` をそのまま使うので、
狭域・広域・solve・伝播の 4 者は同一の更新式のままです。

* **密と compact で掃き方が違います（結果は同じ）。** 密は上のとおり `states` を
  全域 Gauss–Seidel で掃きます。compact に全域の `states` は無いので、代わりに
  sink を**タイル単位**で修復します。1 タイル = 更新する interior（16 セル角）+
  遷移が届く距離だけの halo で、halo を凍結境界にして interior を掃き、変わった列を
  sink へ返し、変化から遷移到達距離以内のタイルを待ち行列へ入れ直します。待ち行列が
  空になったら収束で、これはブロック Gauss–Seidel なので**全域掃きと同じ不動点**に
  なります（合成テストで、ウィンドウの外のセルの値が密の全域掃きと一致することを
  確認済み）。compact 側の共有場は狭域が動かした窓の毎 tick の書き戻しと、それを
  裏付ける `local_penalty` の 1 B/セルの全域表です。
  * **仕事量は地図の大きさではなく、変化が及ぶ範囲に比例します。** 値が動かない
    タイルは 1 パスで抜けて隣を起こさないので、波面はそこで止まります。逆に全域へ
    波及する変化なら全域 1 掃きぶん働きます（津田沼 0.25 m/cell で約 3,700 タイル
    訪問 = 読み 1.3 GB / 書き 0.7 GB）。**打ち切りではありません** — 動く範囲は
    全部掃きます。
  * **常駐メモリはタイル 1 枚ぶんだけ**（0.25 m/cell で約 1.9 MB、追従パッチ
    3.5 MB と合わせて 6 MB 弱）。
  * **実機での伝播の実時間は未計測**です。host の合成テスト（5 m 四方・0.05 m/cell）
    では、通路を幅いっぱいに塞いでから 2.5 m 先の値が落ち着くまで、追従 119 tick
    （= 12 秒相当）・タイル訪問 358 回でした。
* **反応速度**: 合成テスト（密）での実測は **1 掃き目で広域の価値が動き**、30 掃きで
  ほぼ収束、数値的な完全収束（Δ=0）は約 80 掃きでした。収束を待つ必要はありません
  — 掃くたび不動点へ単調に近づき、経路が変わるのは遥かに手前です。compact の
  タイル修復も同じで、同じ合成テストでは 1 タイルあたり平均 36 訪問（= 72 パス）で
  Δ=0 に達しました。
* **密の 1 掃きの実時間**: 掃き速度の host 実測は 5.23 M cells/s なので、19F の scale 2
  （794 万状態）なら 1.5 秒。Pi4 は同種の処理で 5〜8 倍遅いので **8〜11 秒**の見込み
  です。ノード既定の `global_sweep_budget_ms: 20` / `global_sweep_idle_ms: 60`（1 コアの
  25%）だとその 4 倍かかります。`nav2/vi_planner.yaml` も**この 20 / 60 のまま**です。
  2026-08-04 に 60 / 100（37%）まで上げましたが（Pi 5 で 4 コア中 2 コアが空いていたため）、
  同日の実機で機体が 1〜2 秒おきに固まったので戻しました（犯人の切り分けは未了）。
  **実測値は起動ログの `global sweep done in ...` に出る**ので、そこから詰めてください。
  **未検証** — 実機での 1 掃きは測っていません。
* **ロックの持ち方**が肝です。10 Hz の追従ループは同じ `Mutex<PlannerCore>` を
  `try_lock` で取り、3 tick 続けて取れないとロボットを止めます。掃きは
  `global_sweep_budget_ms` だけ掃いてロックを手放し、`global_sweep_idle_ms` 待ちます。
  budget を伸ばすときは idle も一緒に伸ばさないと、走行がぎくしゃくします。
  **上限を決めているのは CPU ではなくこの Mutex です。** 掃きは 1 スレッドで、しかも
  追従ループが毎 tick 最大 `refine_budget_ms`（40ms）握ります。10 Hz なら追従だけで
  この 1 本のロックの 40% を先に取っているので、掃きを 60:100（37%）にすると 2 つで
  8 割方を取り合う計算になります。コアが空いていても増やせないのはこのためで、
  **2026-08-04 に実際そこで固まりました**（上の項）。
  加えて **budget + idle を追従の周期（`control_frequency: 10.0` = 100ms）の整数倍に
  しないこと** — 位相が固定されて、追従の tick が毎回 budget 側に落ち得ます。
  掃きは `lock()`（`try_lock` では
  なく）なので追従の解放ごとに位相がずれ直し、机上で連続失敗数を数えても当たりません。
  観測すべきは走行中の `ros2 topic hz /cmd_vel_mux` が 20 Hz を保つかどうかです。
* **止まるとき**: 密は 1 掃き丸ごとで Δ=0 になったら、compact は待ち行列が空に
  なったら、新しい不動点に達したので次に狭域が場を動かすまで止まります（CPU を
  焼き続けません）。ただし**走行中はまず止まりません** — 壁が窓（±1m）に入って
  いれば `set_local_cost` が毎 tick penalty を塗り直すので、次の伝播が積まれ続けます
  （host 実測で 1000 tick 中 987 tick）。

#### 効いているか確かめる

2026-08-04 に 19F の実機で「途中で塞いだが全域に伝播しなかった」ように見えた件は、
下の 2 つで説明がつきます。伝播そのものは動いていました。

* **見るトピックを間違えない。** `/value_function`（全域スライス）は以前は
  **solve が実際に走ったときしか出ていません**でした。BT の 1 Hz の再計画は同じ
  ゴールならキャッシュヒットで solve を飛ばすので、走行中はずっと solve した瞬間の
  絵が残ります。RViz で動いて見えるのは `/local_window_value`（±1m）だけで、これは
  機体と一緒に動くため、**離れると固まったままの全域が下から出てきて「上書きされた」
  ように見えます**。いまは掃きスレッドが 2 秒ごとに `/value_function` を出し直します
  （`publish_value_function: true` かつ `global_sweep: true` のとき）。
* **塞ぎ方で桁が変わります。** `set_local_cost` が置くのは壁ではなくコストなので、
  通路の一部だけを塞いでも脇を抜けられれば遠方の値はほとんど動きません（host 実測:
  幅 2m の通路を幅 0.4m 塞いで **+0.75 ステップ** = `cost_drawing_threshold: 60`
  なら色 1 段）。幅いっぱい塞ぐと桁が変わります（同 **13 → 38 ステップ**）。
  これは伝播の不具合ではなく、迂回できるなら値は上がらないという正しい挙動です。

ログは 2 種類出ます。走行中は前者がまず出ないので、後者で見てください。

```
vi_planner: global sweep done in 3.4s, 358 tiles (still_dirty=false)   # 待ち行列が空になった
vi_planner: tile repair running for 6.0s (412 visits, 27 tiles queued) # 2 秒ごとの進捗
```

**ウィンドウの外の `local_penalty` は誰も消しません**（`set_local_cost` は
`in_local_area` の中しか触らない）。障害物の脇を通り過ぎると、その penalty はその
ゴールの間ずっと残り（密は `states`、compact は 1 B/セルの全域表）、
掃きのたびに広域の場を歪め続けます。本家
`ViNode` から引き継いだ挙動で、「一度通れないと分かった場所を覚えておく」という
望ましい側の効果でもあるため意図的にそのままにしてあります。消したければゴールを
取り直してください。誤検知（この地図では emcl2 の有効ビームの 28% が壁を貫通する）が
残り続ける経路でもあるので、挙動が怪しいときはここを疑ってください。

### `vi_planner` の `compact_ram_limit_mb: 4096`

**これはプロセス全体のメモリ上限ではありません。** compact の確定出力（sink）を
RAM に置いたままにする上限で、超えたぶんだけ `/tmp/vi_*_sink` へ mmap で逃がす、
という分岐の閾値です。プロセスの実際のピークはこれとは別に決まります（19F の
scale 1 では sink 0.38 GB がノード既定の 512 MB に収まって RAM に載っていたのに、
実測の RSS は 833 MB でした）。

**判定は「明示指定が先、上限による自動退避が後」で、明示指定が無条件に勝ちます**
（`main.rs` の `compact_sink_dir()`）。`compact_sink_dir` が空でなければ上限は一切
読まれません。「上限を超えなければ RAM に残る」が成り立つのは**未指定のときだけ**です。

**同梱している 2 つの地図では、いまはどちらも `compact_sink_dir` が空 = RAM 出力で、
この上限がそのまま効きます。** 19F は scale 2 の sink 0.095 GB なので 512 MB でも
4096 MB でも RAM に載り、値を上げても何も変わりません。**`map_tsudanuma` だけが
この値に依存しています** — sink 648 MB はノード既定の 512 MB を超えるので、既定の
ままだと黙って `/tmp/vi_planner_sink`（= SD カード）へ落ちます。ただし条件は
「648 MB を上回っていること」だけで、**4096 である必要はありません**（上げる前の
2048 でも同じく RAM に載ります）。下げるときに割ってはいけない線がこの 648 MB です。

（2026-08-04 まで `map_tsudanuma` は両ノードとも `compact_sink_dir` を明示していたので、
この分岐にそもそも入りませんでした。**上限を上げても sink はディスクのまま**という
関係だったのが、`compact_sink_dir` を外したことで逆になっています。）

密ソルバ側の上限は別のキーです（`dense_limit_mb`、既定 1500 MB、断片では 4096 MB）。
こちらは「超えたら退避する」ではなく **「超えたら起動を止める」** で、黙って確保して
OOM killer に落とされるより理由を出して止めるためのものです。

**2 つとも 2048 → 4096 へ上げたのは 2026-08-04 で、Pi 5（8 GB）が前提です。**
`map_tsudanuma` を走らせながらの実測で空きが 5.6 GB あり（`vi_planner` の RSS 931 MB、
コンテナのピーク 2.12 GiB、`oom_kill` 0）、2048 は締めすぎでした。**swap が無いので
余裕は坂ではなく崖**で、上限が線として意味を持つのはそのためです。**Pi 4（4 GB）では
この値は保護になりません** — 止めるべきところで起動を通してしまい、理由の出ない
OOM kill に変わります。4 GB 機で使うなら 2048 以下へ戻してください。

**代償**: 逃がさない代わりに、sink は**この上限いっぱいまで**匿名メモリとして居座り
得ます（実際に居座るのは sink の実寸で、津田沼なら 648 MB）。Pi4（4 GB）で
`compact_sink_dir` 無しの広域地図を解くと、512 MB で退避していたときには起きなかった
OOM kill があり得ます（当時の広域専用ノード `vi_global_planner` が SIGKILL された実測は
`simulator/docs/pi4_sim.md` の「C. 本命」）。広域地図を足すときは `compact_sink_dir` を
セットで書くこと。

VI のメモリを実際に頭打ちにしたいなら、手段は `map_scale` を上げる（状態数を
減らす）、`compact_sink_dir` を実ディスクに向ける（sink を匿名メモリから外す）、
`dense_limit_mb` で密の上限を切る、コンテナ側で `mem_limit` を掛ける、の
いずれかです。

このキーは元々、当時の広域専用ノード `vi_global_planner` にしかありませんでした。
2026-08-04 に `vi_planner` にも同じ既定値（512 MB）・同じ判断順で実装し、2026-08-08 に
あちらのノードごと消えたので、いまの宛先は `vi_planner` だけです。自動退避先は
`/tmp/vi_planner_sink`。

ただし**ディスクへ逃がしたときの代償は追従する構成のほうが大きい**です。広域を
1 回解くだけの `local_planner:=nav2`（`follow: false`）と違い、`local_planner:=vi` の
追従は 10 Hz の制御ループの中でパッチを置き直すたびに sink を読みます。コンテナの `/tmp` は tmpfs ではなく
書き込み層（= SD カード）なので、自動退避に頼らず `compact_sink_dir` で速い場所を
明示するほうが安全です。いまはどちらの地図も `compact_sink_dir` が空なので、この
経路には入りません（19F は sink が小さくて、`map_tsudanuma` は上限 4096 MB に収まって）。
