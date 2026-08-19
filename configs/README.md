# configs/

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
| `stack/nav2/*.yaml` と `stack/vi_planner.yaml` | `navigation.launch.py` | 起動時に 1 つへ合成して `params_file` に渡る |
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
| `vi_planner.yaml` | 各ノードの `main.rs` の `declare_parameter` |
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
| `emcl2.laser_min_range` / `laser_max_range` | 宣言はされ `initPF` で一度読まれるが、`Mcl::setScan` が毎スキャン `/scan` の `range_min` / `range_max` で上書きする。実効値は `sensors/mid360_scan.yaml` の 0.0 / 70.0（同じ `Scan` の `scan_increment` のほうは上書きされないので効く） |
| `ekf_filter_node.odom0_nodelay` / `imu0_nodelay` | 上流ドキュメントにはあるが、ROS 2 版の `ros_filter.cpp` は読まない（ROS 1 の `tcpNoDelay` 由来） |
| `controller_server.FollowPath.stateful` | DWB に無い。ゴール判定側の同名パラメータと混同したもの |

## params_file の合成

`nav2/*.yaml` に `vi_planner.yaml` を足したものがファイル名順に読まれ、1 つの
一時ファイルへ束ねられます。そのパスは起動ログに出ます。

`vi_planner.yaml` だけ `nav2/` の外に居るのは、あれが Nav2 のノードではないため
です。それでも合成に入るのは**入れないと効かないから**で、上流の
`navigation_launch.py` は `params_file` を 1 つしか受け取らず、`overrides` が
重なれるノード名もこの合成結果で決まります。

```
[INFO] [launch.user]: params: params_file: 8 fragments from .../daifuku_config/stack/nav2 (+ vi_planner.yaml) -> /tmp/params_file_xxxx.yaml
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
| `vi_planner.yaml` | `vi_planner` |

`amcl` が `localization/` ではなく `nav2/` にあるのは、nav2 の
`localization_launch.py` が `params_file` の中から読むためです。

### 合成には入るが読まれない断片（`nav2:=false`）

**束ねるのは常に 8 ファイル全部です（`nav2/` の 7 つ + `vi_planner.yaml`）。何が実際に読まれるかは、どのノードが立つかで
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
   `nav2/*.yaml` + `vi_planner.yaml` の合成結果。`params_file:=` を明示した場合は
   そのファイル）
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

**2 つの overrides はキーの集合をそろえてあります。** 片方にしか要らない値も、もう
片方へ断片と同じ値で書いて並べます（地図ごとの違いが縦に並んで見えるように）。
`# 断片も同じ値` の行は消しても挙動は変わりませんが、**消さないでください**（例外は
`map_19f.yaml` の内蔵推定器 4 つで、あれは密ソルバが要るので津田沼には置けません）。

その launch が読まない設定ファイル宛の節は何も起こしません
（`mapping.launch.py` に `overrides:=map_19f` を渡しても、`emcl2:` と `vi_planner:` は
単に行き先が無いだけで害はありません）。**パッケージが違う節はそもそも読まれません**
（`lidar_bringup` は `daifuku_stack:` の下を見ません）。

`params_file:=` で土台を差し替えても行き先は変わりません。渡したファイルに
書かれていなくても、断片（`nav2/*.yaml` + `vi_planner.yaml`）が宣言しているノードの節はそこへ載ります
（土台を替えた途端に nav2 宛の節が黙って消えると、探しようがないため）。

上書きできるのは、上の表のうち `MID360_config.json` を除く全部です。
`livox_ros_driver2` の設定は ROS のパラメータファイルではない（ノード名も
`ros__parameters` も無い）ので、この仕組みに乗りません。`mid360_config:=<パス>` で
ファイルごと差し替えてください。

`overrides` の既定値は **`configs/site` の 1 行**（既定
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
# 場所を切り替える (configs/site を書いて raspicat を立て直す)
tools/site.sh map_tsudanuma

# 自律移動側。map も overrides も configs/site から来るので渡さない
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
**参照先は `daifuku_config` の share** で、`maps/` を持つ `daifuku_stack` とは
置き場が違います（2026-08-08 まで後者の `config/overrides/` を見ていました。設定を
`configs/` へ出したときにそこは消えているので、**どの地図でも `none` に落ちていました**）。

### 何がどこへ行ったかを見る

重なった設定ファイルは一時ファイルに書き出され、起動ログにその 1 行が出ます。
`(+ ...)` が「どの override のどの節を重ねたか」です。

```
[INFO] [launch.user]: params: params_file: 8 fragments from .../daifuku_config/stack/nav2 (+ vi_planner.yaml) -> /tmp/params_file_xxxx.yaml (+ overrides:map_19f -> vi_planner)
[INFO] [launch.user]: params: emcl2_params_file: .../configs/stack/localization/emcl2.yaml -> /tmp/emcl2_params_file_xxxx.yaml (+ overrides:map_19f -> emcl2)
```

行が出ないファイルは、重なるものが無かったので土台がそのままノードへ渡っています
（`params_file` だけは断片の合成が要るので必ず出ます）。書いたのに行が出ないなら、
その launch がその設定ファイルを読んでいません。

### 新しい override を足す

`configs/overrides/<地図名や状況>.yaml` を作り、
**変えたいキーだけ**を書きます。パッケージ名・ノード名・`ros__parameters` の 3 段が
必要です。

```yaml
daifuku_bringup:            # 機体側。変えたら docker compose up -d
  elevation_filter:         # -> daifuku_bringup の sensors/mid360_elevation.yaml
    ros__parameters:
      min_elevation_deg: 5.0

daifuku_stack:              # 自律移動側
  vi_planner:               # -> stack/vi_planner.yaml (params_file の合成結果)
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

**ファイルを新しく足したときは 1 度ビルドを通してください。** `install/` の symlink は
ビルド時にしか張られないので、足しただけでは `overrides:=` の一覧に出てきません
（既にあるファイルの値を直すだけならビルドは要りません）。

間違えると起動時にエラーで止まります。**パッケージ名**が `KNOWN_PACKAGES`
（`params.py`）に無い場合——どの launch からも読まれない部分木になるため——と、
**ノード名**がそのパッケージのどの設定ファイルにも無い場合（近い名前を出します）の
2 通りです。黙って消えると「書いたのに効かない」を探せないためで、その launch が
読まないだけの節（`mapping` での `emcl2:`）はエラーにしません。

断片（`nav2/*.yaml` / `vi_planner.yaml`）を丸ごと複製しないでください。既定値が変わったときに追従できません。

### 効かないときに疑うところ

`SetParameter` / `SetParametersFromFile` は、設定ファイルに**既にあるキー**を
上書きできません（launch_ros はグローバルパラメータを先に、ノード個別の
`parameters=` を後に渡すため、後勝ちでノード側が勝つ）。
そのため override は設定ファイルそのものをマージして作っています。
逆に `lifecycle_bond.yaml` の `bond_timeout` が `SetParametersFromFile` で効くのは、
そのキーが断片のどこにも無いからです。

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
* モータ OFF でも `odom` が動くので dry-run が成立しない

`true` にすると同じ指令で `odom` yaw の peak-to-peak は 0.000 deg、x は 0.0000 m に
なりました。**それでも `false` にしてあるのは、この個体の I2C カウンタが不安定だから
です。** 同じセッション中に 2 回、`read` がタイムアウトして復旧不能になりました
（`i2c-bcm2835 fe804000.i2c: i2c transfer timed out` →
`i2c_counter_read: Failed reading from i2c counter device, addr=0x10 / 0x11`）。
一度失敗するとドライバの mutex が握られたままになり、以後 `/dev/rtcounter_*` を読む者は
全員 D (uninterruptible) 状態に永久固着します。`raspimouse` は単一スレッドなので
ノードごと沈黙し（`/odom` も TF も lifecycle 応答も停止）、**SIGKILL でも殺せず復旧は
リブートのみ**です。天秤は「`false` = `odom` の幻回転（ゼロ Twist を投げれば必ず
止まる）」対「`true` = `odom` は正しいが走行中にランダムでロボットごと固着」で、実験中の
突然死のほうが害が大きいという判断です。カウンタ基板側の I2C が直れば `true` が正解。
緩和案（未検証）: `odom_hz` を 100 から下げて I2C トラフィックを減らす。

`false` のあいだの運用として、nav2 を止めた直後に必ずゼロ Twist を投げてください。

```bash
ros2 topic pub --times 5 /cmd_vel_teleop geometry_msgs/msg/Twist   '{linear: {x: 0.0}, angular: {z: 0.0}}'
```

`/cmd_vel` ではなく `/cmd_vel_teleop` なのは、そちらが仲裁の手動側の入口だからです
（**自律走行中は優先度で届きません** = 下の `twist_mux.yaml` の節。`twist_mux:=false`
なら `/cmd_vel` へ）。ノードの自己診断はあてになりません —
`raspimouse_component.cpp` の「Testing counters」は `ifstream::is_open()` を見るだけで
`read` を試さないので、"Using pulse counters for odometry" は**エンコーダが読める証拠に
なりません**。確認は「モータ OFF で回転を指令して `odom` が動かないこと」で行います。

### `raspicat.yaml` の `wheel_diameter` / `wheel_tread`

**モータはギアエンコーダ付きの DC（ギア比 26:1）で、速度の閉ループは制御基板の中に
あります**（[製品仕様](https://rt-net.jp/products/raspberry-pi-cat/)）。ドライバが書く
「ステップ周波数」はその基板への速度指令であって、ステップクロックではありません
（名前は Raspberry Pi Mouse から引き継いだもの）。**基板から車輪径は見えない**ので、
下の改造は基板の中のループには影響せず、換算の側だけの問題として閉じます。

上流の既定は 0.1524 / 0.27918 ですが、この個体は 2026-08-03 実測で 200mm / 350mm です。
この 2 値は `cmd_vel` → モータ指令の換算と `odom` 積分の両方に使われるため、上流値の
ままだと直進 `0.200 / 0.1524 = 1.312` 倍・旋回 `1.312 * 0.27918 / 0.350 = 1.047` 倍 =
実機が指令より 31% 速く走り、`odom` はその分だけ過少報告していたことになります。

**`pulses_per_revolution: 400.0` は上流のままで、この機体では間違いです。** 2026-08-04 に
Pi 5 実機で、右車輪だけを一定周波数で回してエンコーダで止める形で測りました。

| パルス | 実測の回転 | エンコーダ | ステップ |
| --- | --- | --- | --- |
| 5506 | 4.944 回転（5 回転に 20° 足りず） | 1113.6 /回転 | 567.8 /回転 |
| 11148 | 9.972 回転（10 回転に 10° 足りず） | 1118.1 /回転 | 570.4 /回転 |

目視誤差が半分になる後者を採って **1118 / 570**。**この 2 つは 1.960 倍違います**
（比は 142 秒の計測なので 0.5% 以下の精度）。エンコーダが数えるものと基板が指令として
受け取るものが別、ということで、上流はこれを 1 つの数で兼ねているので**どちらに合わせても
片方が壊れます**。2026-08-08 に床の上で測り直して **`1073 / 447`** へ替えました（比は
2.40 へ動きましたが別物という結論は同じ）。導出と、`pulses_per_revolution` が 1 つの値で
全速度に合わない理由（不足が距離ではなく経過時間に比例する。**原因は未解明で、カウンタの
読み出しではない**）は
[`src/raspicat_driver/README.md`](../src/raspicat_driver/README.md#較正2026-08-08-に床の上で測り直し)。

`raspimouse` ノード（`driver:=raspimouse`）はここが直せません。`cmd_vel` → 指令周波数の
換算に **400 を直書き**していてパラメータはオドメトリ側にしか効かないので、この機体を
公式実装で走らせると**指令の 89% の速度**で走ります（`400 / 447`）。
`use_pulse_counters: false` なので `odom` は指令どおりの値を報告し続け、**ログにも
`odom` にも何も出ません**。**径を戻してはいけません** — 上流の 0.1524 と直書きの 400 は
互いに逆を向いていて（`0.200/0.1524 × 400/447 = 1.17`）径が古いままだと 17% 速すぎ、
径だけ正すと 89% になりますが、回頭はトレッドとの比で決まるので径を歪めると旋回だけが
壊れます。直すなら上流の直書きを直すしかありません。自前実装
（`raspicat_driver.yaml`）はこの 2 つを別のキーに分けてあるので正しく走ります。

### `raspicat_driver.yaml`（自前ドライバ / Pi 4・Pi 5）

`raspicat.yaml` は公式実装（`driver:=raspimouse`）用で、rtmouse カーネルモジュールが
出す `/dev/rt*` を読む構成が前提です。自前実装（`driver:=original`、
[`src/raspicat_driver`](../src/raspicat_driver/README.md)）はモータ経路をユーザ空間から
直接扱い、そのパラメータがこのファイルです。**リポジトリの標準はこちら**で、Docker の
入口（`.env` の `COMPOSE_FILE`）が `driver:=original` を渡します（`driver:` という引数
そのものの既定値だけは `raspimouse` のまま）。**Pi 5 ではこちらしか選べず**（rtmouse は
BCM2711 のレジスタを `ioremap` するので RP1 の Pi 5 では動かない）、**Pi 4 では公式実装と
排他**です（両方が GPIO 16/6/5 と PWM を持つとカーネルは衝突を検出しないまま車輪が逆に
回り得るので、rtmouse が載っていると `configure` を拒否する。`allow_rtmouse` で上書き可）。

Pi 4 と Pi 5 で 1 ファイルです。機種差は `model: auto` が device-tree から判定してチップの
同定（`gpiochip` のラベルと `pwmchip`）だけを切り替えます。ピン番号・PWM チャネル・I2C
アドレスは制御基板側の性質なので両機種で同じです。`raspicat.yaml` と違うのは次の 5 点で、
理由は [`docs/setup/raspberry-pi-4.md`](../docs/setup/raspberry-pi-4.md) と
[`raspberry-pi-5.md`](../docs/setup/raspberry-pi-5.md)。

* **`pulses_per_revolution`（1073.0）と `steps_per_revolution`（447.0）が別のキー**です。
  前者はエンコーダのパルス数で `odom` 専用、後者は基板への指令パルス数で `cmd_vel` →
  周波数専用（上の節のとおり 2 倍以上違う）。`pulses_per_revolution` は 1 つの値で全速度
  には合わないので巡航帯（0.20〜0.40 m/s）に合わせてあります（そこでは +0.4%、0.13 m/s
  では `odom` が 4% **少なく**出る。値を下げると `odom` は大きくなるので向きに注意）。
* `use_pulse_counters` の既定が `true`。ユーザ空間の `ioctl` は失敗を返して戻ってくるので
  D 状態固着が起きません。連続失敗が `counter_error_limit` に達すると `cmd_vel` 積分へ
  落ち、応答が戻れば自動で復帰します。
* `odom_hz` の既定が 50.0（`raspicat.yaml` は 100.0）。1 周期あたり I2C を 6 トランザク
  ション使うので、62.5 kHz のバス占有を半分に落としてあります。
* `publish_tf` があります。EKF に `odom -> base_footprint` を出させる構成
  （`use_mid360_imu:=true`）では `false`。`robot_bringup.launch.py` が自分で渡すので
  **このファイルを直す必要はありません**。
* **LED・ブザー・スイッチのキーがあります**。トピックの型は公式実装と同じで、`/leds` が
  `raspimouse_msgs/Leds`、`/switches` が `raspimouse_msgs/Switches`（true が押下）、
  `/buzzer` が `std_msgs/Int16`（Hz、0 で停止）。掴めないピンがあっても走行には影響
  しません（起動ログの `peripherals:` に出るだけ）。**`buzzer_pwm_channel` の既定 `-1`
  （ソフト生成）は意図的です** — ブザーの GPIO19 は右モータのステップクロックと同じ PWM
  チャネルで、両方を PWM に mux すると鳴らすたびに右車輪が回ります（モータと同じ番号、
  および Pi 4 で 0 以上を書いた場合は `configure` が拒否）。

配線に関わるキー（`gpio_*` / `pwm*` / `i2c_*` / `direction_*_forward_level`）はすべて
rtmouse の `rtmouse.h` から写した値で、**実機で確認していません**。

### `twist_mux.yaml` の配線と優先度

**入れた理由は書き手が 2 つあったからです。** 自律側は `velocity_smoother` が
`cmd_vel_smoothed -> cmd_vel` の remap で `/cmd_vel` へ出し、手動側は
`control.sh teleop` の `teleop_twist_keyboard` / `teleop_twist_joy` が同じ `/cmd_vel` へ
直接出していました。仲裁が無いので、自律走行中に遠隔操作を開くと両者のメッセージが
そのまま交互にドライバへ届きます。配線は次のとおりで、**ドライバの購読先だけを
変えてあります**（nav2 側の remap は include している上流 launch の中なので触れない）。

```text
controller_server / vi_planner ─ /cmd_vel_nav ─ velocity_smoother ─ /cmd_vel ─┐
                                                                              ├→ twist_mux → /cmd_vel_mux → ドライバ
teleop / control.sh stop ─────────────────────── /cmd_vel_teleop ─────────────┘
```

両ドライバとも相対名 `cmd_vel` で購読しているので、`robot_bringup.launch.py` の remap
1 行で両方に効きます。**優先度は自律側（`/cmd_vel`、100）が上で、teleop
（`/cmd_vel_teleop`、10）が下です。** twist_mux が中継するのは、その時点で優先度が最大の
トピックが**メッセージを受けたとき**だけなので、teleop が通るのは自律側が `timeout`
（0.5 秒）のあいだ黙っているときに限られます。**したがって自律走行中は
`control.sh teleop` も `control.sh stop` も効きません**（エラーは出ず、ただ機体が言うことを
聞かない）。パッド（`joy_teleop`）だけは別で、teleop に入るときに `/follow_waypoints` と
`/navigate_to_pose` を取り消すので自律側が黙り、0.5 秒後に手動が通ります。
**これは非常停止ではありません。** 確実に止める手段はモータ電源（`motor_power` /
`control.sh motor off` / パッドの BACK 長押し）です。

一方で、**中継が止まると指令も止まります**。twist_mux が落ちるとドライバには何も届かず、
ドライバは最後の指令を `cmd_vel_timeout`（既定 60 秒）まで保持します。仲裁ごと外すなら
`twist_mux:=false`（購読先は `/cmd_vel` に戻り、`control.sh` には
`CMD_VEL_TOPIC=/cmd_vel` を渡す）。

`locks`（`std_msgs/Bool` で下位のトピックをまとめて塞ぐ層）は書いていません。
`isLocked()` が `hasExpired() || data` で、受信前のスタンプは 0.0 = 期限切れ扱いなので、
**配信元を用意せずに書くと塞がったまま**になり、その優先度未満の指令がすべて止まります。
非常停止スイッチを足すときにセットで入れるものです。

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
  ともに `false` です。この断片は a3f2899（`emcl2` の導入コミット）から
  2026-08-09 まで `true` で、理由は記録されていません（README の表を見て入れた
  ものと思われます）。同日にコード既定と同じ `false` へ揃えました。**未検証**:
  同梱の 2 地図はどちらも `overrides/` が `false` にしていたので変わりませんが、
  overrides を持たない地図での挙動は測っていません。
* `open_space_threshold`: README の表にはありますが、この版の `emcl2_ros2` は
  `declare_parameter` していないので**読まれません**。値を書いても効かず、
  エラーも警告も出ません。

### `emcl2.yaml` の運動モデル（`odom_*_dev_*`）

**症状**: 初期位置を合わせても emcl2 が自己位置を動かし続け、オドメトリが効いて
いないように見える。**原因**: そう設定してあったからです。4 つの `odom_*_dev_*` は
2026-08-07 まで emcl2 のノード既定のままで、この機体の実力より 1 桁大きい値でした。

`OdomModel::setDev` は分散を移動量に比例させます（`fw_dev = sqrt(|length| *
fw_var_per_fw + |angle| * fw_var_per_rot)`）。距離 L [m] を直進したときのばらつきは
σ = 値 × √L なので、既定値は次の意味になります。

| | 既定 | 1 m 直進 | 10 m 直進 | 90 度旋回 |
| --- | --- | --- | --- | --- |
| 位置 `odom_fw_dev_per_fw` | 0.19 | 0.19 m | 0.60 m | — |
| 方位 `odom_rot_dev_per_fw` | 0.13 | 7.4 度 | 24 度 | — |
| 方位 `odom_rot_dev_per_rot` | 0.2 | — | — | 14 度 |

この機体のオドメトリは、車輪径 0.2m・トレッド 0.35m・エンコーダ 1073 pulses/rev の
**実測値**を入れた `raspicat_driver` と、方位を Mid-360 のジャイロ（バイアス除去済み、
残ノイズ 0.074 deg/s）で押さえる EKF の組で、1 m で 7.4 度も曲がりません。事前分布が
実力の 10 倍広いと、その中では観測がほぼ一意に姿勢を決めます（重みは尤度場を全ビーム
= 0.5 度刻みの 720 本ぶん足した和なので、数 cm ずれた粒子は 1 回の更新で大きく負ける）。
つまり出力は実質スキャンマッチャそのものになり、オドメトリは探索範囲を与えるだけの役に
なっていました。

2026-08-07 に 0.05 / 0.02 / 0.05 まで下げ、**2026-08-09 に 0.10 / 0.05 / 0.10（1 m 直進で
0.10 m・2.9 度、90 度旋回で 7.2 度）へ上げ直しました**。既定の約半分で、実測の実力よりは
まだ数倍広い側です。**この上げ直しの根拠は記録されておらず、未検証です。** 潰さないこと —
リサンプリングは roughening をしないので**この 4 つが粒子の多様性の唯一の供給源**で、
潰すと本当にずれたときに二度と復帰できません（膨張リセットは貫通を検知したときにしか
起きない）。戻すときは 4 つとも `emcl2_node.cpp` の既定（0.19 / 0.0001 / 0.13 / 0.2）へ。
**機体固有の値なので `overrides/` ではなくこの断片に置いてあります。**

**変えていないもの: `odom_freq: 20`。** 同じスキャンを 2 回食っています
（`main` は `odom_freq` の周期で `loop()` を回して毎回 `sensorUpdate` を通すが、実際に
呼ばれる `ExpResetMcl2::sensorUpdate` には `Mcl::sensorUpdate` にある「同じスキャンなら
抜ける」ガードが無い。`/scan` は `mid360_publish_freq` 既定 10.0 Hz なので倍率は 2）。
**それでも 10 へ下げていません** — `loop()` は運動更新・観測更新・`map->odom` の発行を
同じ 1 回で済ませるので、下げると**TF の発行間隔も倍**になります（`transform_tolerance`
0.2 秒に対し実測の遅延は 0.20〜0.47 秒 = `controller_server` の節）。しかも同じ公称周期の
2 ループは互いに滑るので倍率は 0・1・2 回が混じる形になり、かえって読みにくい。加えて
事前分布を狭めた今は二度食いの効きも小さくなっています。

残る 2 つは設定では直せません（どちらも `src/emcl2_ros2` = 外部パッケージ側）。

* リサンプリング後に重みが 1/N に戻らない（`Mcl::resampling` が `w_` ごとコピーする）
  ので、勝った粒子は複製と重みの両方で有利になり、観測の効きが増幅されます。
* 事前分布と観測の時刻が合っていません。`getOdomPose` は `rclcpp::Time(0)` = 最新の odom
  を引いて粒子を現在時刻まで進めますが、スキャンは
  `livox -> pointcloud_to_laserscan -> restamp_scan -> filter chain` を通ってきた過去の
  情報で、動き補正はどこにも入りません。**旋回中に最も悪化します。**

**未検証** — 実機では走らせていません。確かめ方は、(1) 前提としてオドメトリが健全なこと
（`raspicat_driver` は I2C 読み出しが 5 回連続で失敗すると黙って `cmd_vel` 積分に落ちる。
モータ電源 OFF で回転を指令し `/wheel/odom` が動かないこと）、(2) 手で数 m 押して `/odom`
と `mcl_pose` の差が 0.05 × √L より十分小さいこと、(3) `/particle_cloud` が走行中も
潰れないこと、(4) ログの `ALPHA: <値> / <閾値>` に `RESET` が混ざらないこと（混ざるなら
地図と実環境の不整合の側 = `overrides/map_19f.yaml` で、この変更では直らない）、
(5) `map->odom` の遅延を測り直すこと（`controller_server` の節の値は旧運動モデルでの
実測なのでそのままでは使えない）。

### `emcl2.yaml` の `laser_likelihood_max_dist: 1.0`

ノード既定の 0.2 m から **2026-08-09 に 1.0 m へ広げました**。名前は「尤度場の幅」
ですが、**この 1 つの値が重み付けと貫通判定の両方を決めます**。効きが大きいのは後者です。

尤度場は占有セル（`v > 50`）ごとに `setLikelihood` が正方形に書き込みます。
`cell_num = ceil(range / resolution)` として重みは `255 × (1 − i / cell_num)`（`i` は
**チェビシャフ距離**）で、セルどうしは `max` で合成。0.05 m/cell なので `cell_num` は
**0.2 m で 4、1.0 m で 20**（正方形なので対角へは `range × √2` まで届く）。

* **重み付け側**（`Particle::likelihood`）: 1 本のビームが 255 から 0 へ落ちるまでの距離が
  そのまま `cell_num` 段なので、幅を 5 倍にすると数 cm のずれに対する感度が **1/5** に
  なります。運動モデルを広げた（上の節）のと合わせてオドメトリ寄りへ 2 段振った形です。
* **貫通判定側**（`Particle::isPenetrating`）: レイを刻んで進み、`likelihood == 255`
  （= 占有セルそのもの。ここは幅を変えても動かない）を踏んだ後に **`likelihood == 0` の
  セルへ抜けたら「貫通」**と数えます。この `== 0` が「どの占有セルからもチェビシャフ距離で
  `laser_likelihood_max_dist` 以上」の意味なので、**幅がそのまま「壁の先がどれだけ離れて
  いれば貫通と見なすか」**になります。

これが 19F に効く理屈です。地図の壁と実物の壁が 0.3 m ずれていると、レイは地図の壁を
踏んでから 0.3 m 先で終わりますが、1.0 m ではそこはもう「空き」ではない = **貫通と
数えられません**。本当に見失って壁を突き抜けたレイは 1 m 以上先まで届くので**そちらは
捕まります**。つまり「サブメートルの地図誤差には目をつぶり、大きな食い違いだけ拾う」
判定へ変わり、`alpha`（非貫通率）が上がるぶん `overrides/map_19f.yaml` の
`alpha_threshold: 0.2` による膨張リセットは起きにくくなります。

**ただしこれは連続な鈍り方ではなく、通路幅で切り替わる崖です。** 零セルの条件が「どの
占有セルからも 1.0 m 以上」なので、両側に壁のある**幅 2.0 m 未満の空間には零セルが 1 つも
存在しません**。そこでは `isPenetrating` が構造的に `true` を返せず、貫通判定は鈍るのでは
なく**止まり**、`alpha` は 1.000 に張り付きます。**幅の上限を決めているのは地図ではなく
通路のほう**で、狭ければ 0.5 m（幅 1.0 m 相当）へ落とすこと。**裏を返せば、幅を広げるのは
貫通検知を鈍らせること**です。19F の根因（地図と実環境の不整合）は直らないので、
`alpha_threshold` と同じ**対症療法をもう 1 段重ねた**形 — 地図を取り直したら 3 つとも
既定へ戻すこと。参考値はノード既定 0.2 だけが極端に狭い側です（`open-rdc/orne-box` 0.8、
`uhobeike/raspicat_navigation` 2.0、Nav2 AMCL と当リポジトリの `amcl.yaml` は 2.0）。

**未検証**。確かめ方はログの `ALPHA: <値> / <閾値>` と `RESET` の頻度で、**3 通りを
区別すること**です。

* 0.0〜0.4 の張り付きから **0.5 前後まで上がり `RESET` が減った** … 狙いどおり。
* **1.000 に張り付き `RESET` が一度も出ない** … 上の崖に落ちて**判定が死んでいます**。
  0.5 あたりへ落として測り直すこと。
* **上がらない** … 根因が地図誤差ではありません（TF の遅延・オドメトリ・仰角フィルタ
  由来の偽の壁）。

**この値は断片に置いてあるので両方の地図に効きます。** 地図ごとに変えたくなったときは
`overrides/*.yaml` の `emcl2` の節へ（そのときは 2 つの override にキーをそろえて並べる）。
起動時のコストは占有セル 1 つあたり `(2·cell_num+1)²` の書き込みで 81 → 1681 と 20.8 倍に
なりますが、走るのは `v > 50` のセルだけで、津田沼でも占有は 9.4 万セル（`int8` の 51〜100）なので 1.6 億回
程度 = 起動が目に見えて延びる量ではありません。

### `vi_planner` の `action_forward_m`（前へ出る 3 つを 0.5 m へ）

`action_forward_m` の 1・4・6 番目（`forward` / `rightfw` / `leftfw`）を **0.5 m** に
してあります。ノード既定は `[0.3, -0.2, 0.0, 0.2, 0.0, 0.2]` なので、直進が 0.3 → 0.5、
旋回付きが 0.2 → 0.5 で、**後退（2 番目）は 0.2 のまま**。両 override も 2026-08-09 から
6 つとも断片と同じ配列です（明示してあるのは地図ごとの差を縦に見るため）。

この配列は**二役**です。価値反復の 1 手の変位であると同時に、`follow_path` が `linear.x`
へそのまま入れる速度指令でもあります（`delta_fw` → `linear.x`、`delta_rot` →
`angular.z`）。1 手のコストは着地セルの `penalty`（自由セルなら 1）だけで**進んだ距離には
依存しない**ので、価値反復が最小化しているのは「1 m あたりの手数」です（0.2 m の手は
0.3 m の手より 1 m あたり 1.5 倍高くつく）。経緯は 2 段階（どちらも 2026-08-05）で、まず
旋回付きだけが 0.2 で曲がりながら進む手が選ばれず**その場旋回 → 直進の繰り返し**に
なっていたので直進と同じ 0.3 へそろえ、次に全体が遅かったので 3 つまとめて 0.5 へ。
等しくしてあるのは「向きを変えるかどうかで 1 m あたりの手数が変わらない」状態を保つ
ためで、**3 つは一緒に動かすこと**。

**上限を決めるのは `velocity_smoother` で、いまそこは 0.4 です。** `follow_path` の
`cmd_vel` は `velocity_smoother` を通ってから車輪へ行き（`nav2:=false` でも通る）、その
`max_velocity` は `nav2/behaviors.yaml` が **DWB の `max_vel_x` に合わせた
`[0.4, 0.0, 1.0]`** に落としています（ノード既定は `[0.5, 0.0, 2.5]`）。**上限を超える値は
そこで黙ってクリップされ**、価値反復は「1 手 = 1 秒」で解いているのに機体はそれより遅く
走ります（経路は closed-loop なので破綻はしませんが、旋回半径が計画より小さくなり内側を
切る）。**つまりこの 0.5 は、断片のままだとどの地図でも 0.4 で頭打ち**で、外してあるのは
両 override の `velocity_smoother` の節です。実際の値は
`ros2 param get /velocity_smoother max_velocity` で見えます。

代償が 3 つあります。

* **旋回半径が 0.859 m から 1.432 m へ広がります**（`linear.x / angular.z` = 0.5 / 20 deg/s）。
  曲がりきれない狭さでは価値反復が `right` / `left`（前進 0）を選ぶだけなので**壊れは
  しません**が、**狭隘部はこの変更で不利になります**。半径を戻すなら
  `action_rotation_deg` を上げるほう（0.86 m 相当は 33 度）で、前進量を下げると 1 m あたりの
  手数の話に戻ります。
* **薄い壁のすり抜けが 0.3 m から 0.5 m へ広がります。** 遷移は着地セルしか見ないので、
  1 手より薄い壁は跳び越え得ます（19F は `map_scale: 2` = 0.10 m/cell なので**5 セル未満の
  壁**）。`safety_radius` 0.2 m の膨張が両側に入るぶん忌避はもう少し手前から効きますが、
  **跳べる壁は確実に厚くなりました**。
* **ゴール圏との余裕が薄くなります。** ゴール判定は半径 `goal_margin_radius` 0.3 m の円 =
  直径 0.6 m に対し 1 手 0.5 m。直線で近づく限り必ずどこかの手が円内へ落ちますが、
  **余裕は 0.1 m しかありません**。ゴール手前で行ったり来たりするなら疑うのはここで、
  動かすのは `goal_margin_radius`（= ゴール許容差そのものなので緩める判断とセット）。

パッチのほうは通ります。`max_fw`（配列の絶対値の最大）が 0.5 になるので `new_patch` の
`reach_bound` は `floor(0.5/res)+1`、19F（0.10 m/cell）で 6 セル・`half` 28 セルに対し
ウィンドウ 10 セルなので `win + reach < half` を満たします（津田沼 0.25 m/cell では
4 / 14 / 4）。起動時に実測で検査されるので、破れば `action_forward_m is too large` で
止まります。

**未検証** — 実機では走らせていません。旋回付きが実際に選ばれるようになったか、狭隘部が
どう変わるか、`velocity_smoother` の既定 `max_accel` 2.5 m/s²（`joy_teleop` が置いている
0.9 の 2.8 倍）で 0 → 0.5 の立ち上がりに制御基板のループが追いつくかはどれも測って
いません。飽和はエラーを出さず**指令より遅く・左右バラバラに回る**だけなので、直進が
曲がるようなら `velocity_smoother.yaml` を作って `max_accel` を下げるのが戻し方です。

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

表示専用（`value_function` の色スケール上限、単位はステップ数≒秒）。経路にもメモリにも
影響しないので、読みにくければ動かして構いません。**地図ごとに測り直す値**で、同梱の
2 地図はどちらも `overrides/` で上げてあります（19F が 100、津田沼が 600）。断片
（`stack/vi_planner.yaml`）の 60 が出るのは、どちらでもない地図のときです。

**60 では 19F の階調はほとんど潰れます。** 2026-07-29 実機計測（19F の地図を 0.10 m/cell に
縮めた 458x289 = いまの `map_scale: 2` と同じ条件）では、到達可能セルの 66% が 100 に
張り付き、グラデーションが出るのはゴールから 60 ステップ以内の 6.83% だけでした
（未到達 -1 が 80.07% / 飽和 100 が 13.10% / 1..99 が 6.83%）。同じ計測で到達可能セルの
最大は 680 ステップ（≒204 m）、中央値 60 / p90 110 / p99 300。**19F の値はここから採っています** —
p90 と p99 の間へ置くと運用上通る範囲に階調を集中させ遠い裾だけ飽和させる選び方になる
ためで、当時は 180（1 ステップ 0.3 m の頃の ≒54 m）でした。`action_forward_m` が 0.5 m へ
上がったぶん同じ距離が 0.6 倍の手数で済むので 180 × 0.6 = 108、**2026-08-09 にそこから
100（≒50 m）へ丸めました**（根拠は未記録。**分布は測り直していません**）。

別の地図では分布が変わります（飽和が広ければ小さすぎ、階調が下位に偏っていれば大きすぎ）。
**津田沼の 600 はこの実測にあたるものがなく、地図の広がりから置いた見込みです**（未計測）。
1 ステップは向こうも 0.5 m なので 600 ≒ 300 m にあたり、294.4 x 200 m の地図をほぼ端まで
階調に入れる置き方です。断片の 60 では 30 m 相当しかなく、地図の大半が 1 色になります。

**`/local_window_value`（±1 m の窓）も同じ上限を使います。** 窓だけ別の値にできる
`window_cost_drawing_threshold` は 2026-08-09 の上流の整理で消えたので、地図側で上限を
上げるほど窓のほうは階調が潰れます（表示だけの話です）。

### `overrides/map_tsudanuma.yaml` の `safety_radius_penalty: 1`

単位は「秒/セル」で、`safety_radius`（0.2m）以内のセルを通るときの加算コストです。1 手の
コストが 1 秒なので、断片の 30 は「近寄るくらいなら 30 手迂回する」という強い忌避に
なります。津田沼は通路が細く（未観測を障害物とみなすため 1〜2 m 幅）、ほとんどの自由セルが
`safety_radius` 以内に入ります（**この計測は `map_scale: 3` = 0.15 m セルの頃のもの**で、
いまの `map_scale: 5` = 0.25 m セルでは同じことがより強く起きます）。VI の遷移はサブセル
サンプリング付きの確率モデルなので、隣接状態間でペナルティの重みが変わり価値関数が局所的に
±3 秒ゆらぎます。1 手 1 秒の前進より揺らぎが大きいと、ノイズ無しの貪欲ロールアウトが降下
できず隣接 θ 間で往復して `LoopDetected` になります（実測: penalty 30 で 83 手目で固着）。
1.0 にすると同じ地図・同じゴールで単調に降下し、104 姿勢でゴールに到達しました。揺らぎは
ペナルティにほぼ比例するので、10 秒未満なら 1 手 1 秒の進捗を下回るはずです。

### `map_tsudanuma` で `planner:=vi` を使うときの制約

* `local_planner:=vi`（両アクション）と `local_planner:=nav2`（同じ `vi_planner` を
  `follow: false` で立てて `controller_server` が追従）のどちらも使えます。同じノードなので
  `map_scale` もアウトオブコア経路（`frontier2d_sparse_compact`）も同じです。狭域追従だけは
  密な状態配列が要るので、全域ではなくロボット近傍のパッチ（±1m ウィンドウ + 遷移到達距離 +
  余裕、0.25m セルで 27x27x60 ≒ 2.5MB）を compact の場から起こして回します。狭域 → 広域の
  フィードバック（`global_sweep`、下節）もこの地図で効きます（sink のタイル修復として）。
  ただし**伝播にかかる時間はこの地図では未計測**です。
* `map_scale: 5` は `downsample_policy: optimistic`（ブロック内に free が 1 つでもあれば
  free）とセットです。既定の保守的プーリングだと通路のセル幅が VI の遷移分布（約 2 セル幅）を
  下回り、`map_scale >= 4` で波がゴール近傍から広がりません。楽観側は通路を細らせない代わりに
  壁側に寄るので、実測の `footprint`（420x450mm、外接円 0.408m）/ `inflation_radius` 0.55 と
  合わせて通れるかは経路ごとに確認を（`map_scale: 5` 到達を確認した当時のコストマップは
  `robot_radius` 0.22 だったので、実機のほうが厳しい）。
* この地図は 68.2% が未観測 (205) で、真の占有セルは 0.4% しかありません。
  `unknown_as_obstacle: true`（既定）だと未観測が全て壁になる = 舗装路のみ通行可。一方
  emcl2/AMCL のスキャンマッチングは占有セルの尤度場を使うので、この地図では拠り所が
  ほとんどありません（別途要検討）。
* メモリは `map_scale: 5` + compact で、`vi_planner` のピーク RSS が **実測 1.60GB**（うち
  sink の mmap が 648MB）。**ただしこれは sink をディスクへ逃がしていた頃の値です** —
  2026-08-04 に `compact_sink_dir` を外して RAM 出力へ変えた（Pi 5 の 8GB が前提）ので、同じ
  648MB が匿名メモリになり、**Pi4 4GB の枠に収まるという話は成り立ちません**（4GB 機で使うなら
  `compact_sink_dir` を戻すこと）。RAM 化後のピークは**未計測**。当時の広域専用ノード
  `vi_global_planner` の 3.98GB との比較は `simulator/docs/pi4_sim.md`。
* 一方 **`simulator/` の pi4_sim ハーネスの枠（0.6 コアを stack 全体で共有）では、solve 中に
  emcl2 まで巻き込んで 900 秒でも `/plan` が出ません**。実機 Pi4 は 4 コアあるので同じには
  なりませんが、`vi_threads: 3` を明示して 1 コアを stack に残すのはそのためです。
* **`action_forward_m` を既定から変えるので、`value_iteration3` は ec2579d（2026-08-04）
  以降が要ります。** それより前の VI は行動集合をコンパイル時定数と照合し、違えば **exit 1 で
  即死**します。ログに出るのは launch の `process has died ... exit code 1` だけなので、症状は
  「プランナだけ上がらず nav2 が inactive のまま」に見えます。
* **全域スライスの 1 枚が重い地図です。** `/value_function` は 94 万セル（19F の 13 万セルの
  7 倍）で、compact なので 1 枚作るのに sink を全走査します。作るのは掃きスレッドが**ロックを
  握ったまま** 2 秒ごとなので、ここが 300 ms を超えると 10 Hz の追従ループが 3 tick 連続で
  `try_lock` に失敗して機体が止まります。**未計測** — 走行中に `/cmd_vel_mux` が 2 秒おきに
  途切れるなら、まず `value_publish_interval_ms` を疑ってください（0 にすると掃き中の出し直しが
  止まって solve 完了時の 1 枚だけに、負にすると配信そのものが立ちません。配信の on/off だった
  `publish_value_function` は 2026-08-09 の上流の整理でこのキーに吸収されました）。

### `overrides/map_19f.yaml` の `map_scale: 2`

`map_scale` は**プランナ内部だけ**の作業解像度です。`/map`・コストマップ・emcl2 は
0.05 m/cell のままで、粗くなるのは VI が解く格子だけです。

| | scale 1 | scale 2 |
| --- | --- | --- |
| プランナ内部の格子 | 915x577 @0.05m | 458x289 @0.10m |
| 状態数（x60 θ） | 3168 万 | 794 万 |
| compact の確定出力（12 B/state） | 0.38 GB | 0.095 GB |
| **密の常駐（80 B/state）** | **2.53 GB** | **0.65 GB**（実測 654.8 MB） |

**2 にしている理由は solve を軽くするためです**（状態数が 1/4）。**2026-08-09 にこの地図の
`solver` を密へ戻した（`active_reloc` がアウトオブコアを受け付けないため）ので、いまは
必須です** — 密の scale 1 は 2.53 GB で 4 GB 機に載りません。密の常駐が 80 B/state なのは
`states` 56 B に `set_sweep_orders` の掃き順 6 本ぶん +24 B/state が付くためで、scale 2 の
実測 654.8 MB は `states` 444.7 MB + `sweep_orders` 210.1 MB です。compact だった頃は必須では
ありませんでした（2026-07-29 の実機は compact scale 1 で solve 29.25 s / RSS 833 MB で通って
います）。

**メモリ上限を見るのはノード側です**（地図の実寸はノードしか知らないため）。上限のキー
（`dense_limit_mb`）は 2026-08-09 の上流の整理で消え、`/proc/meminfo` の `MemAvailable` そのものが
基準になりました — 超えたら起動を止め、半分を超えたら警告です。書いても読まれません。

**この地図は `waypoint_prefetch` を `true` にしてあるので、表の数字は 2 倍で読んでください**
（先読み中は価値関数が 2 本生きる = 密の scale 2 で 1.3 GB が匿名メモリに乗る。密には
`compact_ram_limit_mb` のようなディスクへ逃がす口がありません）。**4 GB 機では先読みのほうを
外してください。** ノードのメモリ判定が見ているのが 1 本ぶんか 2 本の合計かは**未確認**です。
`true` にしている理由（巡回で点が変わるたびの solve 29 秒を消す）と、津田沼が 2026-08-08 に
`false` へ戻した経緯（あちらは solve 87 秒、場は 648 MB × 2 = 1.3 GB）は
[`docs/usage/navigation.md`](../docs/usage/navigation.md#次の点を走行中に解いておくwaypoint_prefetch)。

代償は 0.10 m/cell の粗さと、保守的プーリングで通路が片側最大 0.05 m 細ること。津田沼の
`map_scale: 5` と違って `downsample_policy` などとのセットは要りません（2 では 1 手 = 5 セル、
ゴール半径 3 セル、`safety_radius` 2 セルが残る。楽観プーリングが要るのは `map_scale >= 4` から）。
**未検証** — この scale での solve 時間と経路そのものは測っておらず、**密に戻してからは一度も
実機で走らせていません**。

### `vi_planner` の `global_sweep`（狭域 → 広域のフィードバック）

`vi_planner` は 1 本の価値関数を広域（`compute_path_to_pose` のロールアウト）と狭域
（`follow_path` の ±1m ウィンドウ）で共有します。狭域はスキャンのヒット点に `local_penalty`
を書き込み、その場でウィンドウ内の価値反復を回して障害物を避けます。**ここに穴がありました**
— ウィンドウ内の価値反復が掃くのはウィンドウの中だけなので、上がった値はそこで止まります。
20 m 先から降りてくる広域のロールアウトは塞がった通路へ降り続け、着いてから初めて気づいて
`LoopDetected` を返します。`global_sweep` は共有場を全域で掃き直してこれを埋めるもので、
新しい Bellman 更新は書かず `value_iteration_at` をそのまま使います（狭域・広域・solve・
伝播の 4 者が同一の更新式のまま）。

* **密と compact で掃き方が違います（結果は同じ）。** 密は `states` を全域 Gauss-Seidel で
  掃きます。compact に全域の `states` は無いので、代わりに sink を**タイル単位**で修復します
  （1 タイル = 更新する interior 16 セル角 + 遷移が届く halo。halo を凍結境界にして interior を
  掃き、変わった列を sink へ返し、変化から遷移到達距離以内のタイルを待ち行列へ入れ直す）。
  ブロック Gauss-Seidel なので**全域掃きと同じ不動点**になります（合成テストで、ウィンドウの
  外のセルの値が密の全域掃きと一致することを確認済み）。
  * **仕事量は地図の大きさではなく、変化が及ぶ範囲に比例します。** 値が動かないタイルは
    1 パスで抜けて隣を起こさないので波面はそこで止まります。逆に全域へ波及する変化なら全域
    1 掃きぶん働きます（津田沼 0.25 m/cell で約 3,700 タイル訪問 = 読み 1.3 GB / 書き 0.7 GB）。
    **打ち切りではありません。**
  * **常駐メモリはタイル 1 枚ぶんだけ**（0.25 m/cell で約 1.9 MB、追従パッチ 3.5 MB と
    合わせて 6 MB 弱）。
  * **実機での伝播の実時間は未計測**です。host の合成テスト（5 m 四方・0.05 m/cell）では、
    通路を幅いっぱいに塞いでから 2.5 m 先の値が落ち着くまで追従 119 tick（= 12 秒相当）・
    タイル訪問 358 回でした。
* **反応速度**: 合成テスト（密）での実測は **1 掃き目で広域の価値が動き**、30 掃きでほぼ収束、
  完全収束（Δ=0）は約 80 掃き。収束を待つ必要はありません（掃くたび不動点へ単調に近づき、
  経路が変わるのは遥かに手前）。compact のタイル修復も同じで、1 タイルあたり平均 36 訪問
  （= 72 パス）で Δ=0 に達しました。
* **密の 1 掃きの実時間**: 掃き速度の host 実測は 5.23 M cells/s なので、19F の scale 2
  （794 万状態）なら 1.5 秒。Pi4 は同種の処理で 5〜8 倍遅いので **8〜11 秒**の見込みで、
  ノード既定の `global_sweep_duty: 25`（1 コアの 25%）だとその 4 倍かかります
  （2026-08-09 の上流の整理までは `global_sweep_budget_ms: 20` / `global_sweep_idle_ms: 60` の
  2 つのキーで同じ 25% を書いていました）。2026-08-04 に 60 / 100（37%）まで上げましたが、
  同日の実機で機体が 1〜2 秒おきに固まったので戻しました（犯人の切り分けは未了）。
  **実測値は起動ログの `global sweep done in ...` に出ます。未検証。**
* **ロックの持ち方**が肝です。10 Hz の追従ループは同じ `Mutex<PlannerCore>` を `try_lock` で
  取り、3 tick 続けて取れないとロボットを止めます。掃きは `global_sweep_duty` の割合だけ
  ロックを握り、残りは手放して待ちます。**上限を決めているのは CPU ではなくこの Mutex です** —
  掃きは 1 スレッドで、しかも追従ループが毎 tick 最大 `refine_budget_ms`（40ms）握るので、
  10 Hz なら追従だけでこの 1 本のロックの 40% を先に取っています。60:100（37%）にすると 2 つで
  8 割方を取り合う計算で、コアが空いていても増やせないのはこのため（**2026-08-04 に実際そこで
  固まりました**）。加えて **budget + idle を追従の周期（`control_frequency: 10.0` = 100ms）の
  整数倍にしないこと** — 位相が固定されて追従の tick が毎回 budget 側に落ち得ます。掃きは
  `lock()` なので追従の解放ごとに位相がずれ直し、机上で連続失敗数を数えても当たりません。
  観測すべきは走行中の `ros2 topic hz /cmd_vel_mux` が 20 Hz を保つかどうかです。
* **止まるとき**: 密は 1 掃き丸ごとで Δ=0 になったら、compact は待ち行列が空になったら、次に
  狭域が場を動かすまで止まります（CPU を焼き続けません）。ただし**走行中はまず止まりません** —
  壁が窓（±1m）に入っていれば `set_local_cost` が毎 tick penalty を塗り直すので、次の伝播が
  積まれ続けます（host 実測で 1000 tick 中 987 tick）。

#### 効いているか確かめる

2026-08-04 に 19F の実機で「途中で塞いだが全域に伝播しなかった」ように見えた件は、下の
2 つで説明がつきます。伝播そのものは動いていました。

* **見るトピックを間違えない。** `/value_function`（全域スライス）は以前は**solve が実際に
  走ったときしか出ていません**でした。BT の 1 Hz の再計画は同じゴールならキャッシュヒットで
  solve を飛ばすので、走行中はずっと solve した瞬間の絵が残ります。RViz で動いて見えるのは
  `/local_window_value`（±1m）だけで、これは機体と一緒に動くため、**離れると固まったままの
  全域が下から出てきて「上書きされた」ように見えます**。いまは掃きスレッドが 2 秒ごとに
  出し直します（`value_publish_interval_ms` が正、かつ `global_sweep: true` のとき）。
* **塞ぎ方で桁が変わります。** `set_local_cost` が置くのは壁ではなくコストなので、通路の一部
  だけを塞いでも脇を抜けられれば遠方の値はほとんど動きません（host 実測: 幅 2m の通路を
  幅 0.4m 塞いで **+0.75 ステップ**）。幅いっぱい塞ぐと桁が変わります（同 **13 → 38 ステップ**）。
  伝播の不具合ではなく、迂回できるなら値は上がらないという正しい挙動です。

ログは 2 種類出ます。走行中は前者がまず出ないので、後者で見てください。

```
vi_planner: global sweep done in 3.4s, 358 tiles (still_dirty=false)   # 待ち行列が空になった
vi_planner: tile repair running for 6.0s (412 visits, 27 tiles queued) # 2 秒ごとの進捗
```

**ウィンドウの外の `local_penalty` は誰も消しません**（`set_local_cost` は `in_local_area` の
中しか触らない）。障害物の脇を通り過ぎると、その penalty はそのゴールの間ずっと残り、掃きの
たびに広域の場を歪め続けます。「一度通れないと分かった場所を覚えておく」という望ましい側の
効果でもあるため意図的にそのままですが、誤検知（この地図では emcl2 の有効ビームの 28% が壁を
貫通する）が残り続ける経路でもあります。消したければゴールを取り直してください。

### `vi_planner` の `compact_ram_limit_mb: 4096`

**これはプロセス全体のメモリ上限ではありません。** compact の確定出力（sink）を RAM に置いた
ままにする上限で、超えたぶんだけ `/tmp/vi_*_sink` へ mmap で逃がす、という分岐の閾値です
（プロセスのピークは別に決まります。19F の scale 1 では sink 0.38 GB が RAM に載っていたのに
実測 RSS は 833 MB でした）。**判定は「明示指定が先、上限による自動退避が後」で、
`compact_sink_dir` が空でなければ上限は一切読まれません。**

**効くのは compact で解いている地図だけです。** `map_19f` は 2026-08-09 に密へ戻したので
いまこの値は読まれません。残る `map_tsudanuma` は `compact_sink_dir` が空 = RAM 出力なので、
**この地図だけがこの値に依存しています** — sink 648 MB はノード既定の 512 MB を超えるので、
既定のままだと黙って `/tmp/vi_planner_sink`（= SD カード）へ落ちます。ただし条件は
「648 MB を上回っていること」だけで、**4096 である必要はありません**（2048 でも RAM に載る）。
下げるときに割ってはいけない線がこの 648 MB です。

**2048 → 4096 へ上げたのは 2026-08-04 で、Pi 5（8 GB）が前提です。** `map_tsudanuma` を
走らせながらの実測で空きが 5.6 GB あり（`vi_planner` の RSS 931 MB、コンテナのピーク 2.12 GiB、
`oom_kill` 0）、2048 は締めすぎでした。**swap が無いので余裕は坂ではなく崖**で、上限が線として
意味を持つのはそのためです。**Pi 4（4 GB）ではこの値は保護になりません** — 止めるべきところで
起動を通してしまい、理由の出ない OOM kill に変わるので、4 GB 機で使うなら 2048 以下へ戻すか
`compact_sink_dir` を実ディスクに向けてください（**代償**: 逃がさない代わりに sink は匿名メモリ
として居座ります。津田沼なら 648 MB）。密ソルバ側の上限はキーではなく `MemAvailable` そのもの
で（2026-08-09 の整理まで `dense_limit_mb`、既定 1500 MB・断片 4096 MB）、こちらは「超えたら
退避」ではなく**「超えたら起動を止める」**です。

VI のメモリを実際に頭打ちにしたいなら、手段は `map_scale` を上げる、`compact_sink_dir` を実
ディスクに向ける、`compact_ram_limit_mb` を下げる、コンテナ側で `mem_limit` を掛ける、の
いずれかです。**2026-08-09 にノード既定が 512 MB から 0 = `MemAvailable` の半分へ変わりました**
（自動退避先は `/tmp/vi_planner_sink`）。ただし**ディスクへ逃がしたときの代償は追従する構成の
ほうが大きい**です。コンテナの `/tmp` は書き込み層（= SD カード）なので、追従が読むたび制御
周期に響きます。自動退避に頼らず `compact_sink_dir` で速い場所を明示するほうが安全です。

