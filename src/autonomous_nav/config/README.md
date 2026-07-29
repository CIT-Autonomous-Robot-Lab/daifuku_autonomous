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
| `nav2/vi_planner.yaml` | `vi_global_planner`, `vi_local_planner` |

`amcl` が `localization/` ではなく `nav2/` にあるのは、nav2 の
`localization_launch.py` が `params_file` の中から読むためです。

## 上書き（override）

優先順位は下ほど強く、**後勝ち**です。

1. `nav2/*.yaml` の合成結果（`params_file:=` を明示した場合はそのファイル）
2. `overrides:=<名前>` → `overrides/<名前>.yaml`（カンマ区切りで複数可）
3. `extra_params_file:=<パス>` → 任意のファイル（リポジトリ外の一時的な上書き用）

```bash
ros2 launch autonomous_nav navigation.launch.py \
    map:=$PWD/src/autonomous_nav/maps/map_tsudanuma.yaml \
    overrides:=map_tsudanuma planner:=vi local_planner:=nav2
```

存在しない名前を渡すと、選べる名前を並べたエラーが出ます。

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
取りこぼし、ゴールが 0.2 秒で ABORTED になりました（`tools/pi4_sim` の Pi4 相当
環境で再現・切り分け済み）。資源に余裕があれば 20ms でも通るので、これは保険です。

### `bt_navigator` の `wait_for_service_timeout: 60000`

nav2 既定は 1000ms。`planner:=vi` では `vi_global_planner` が `/map` を受け取って
から `compute_path_to_pose` を作るので、地図が大きいほど遅れます。`map_tsudanuma`
（23.5MB）では間に合わず、`bt_navigator` が `on_configure` で
"action server not available" を投げて bringup 全体が止まりました。

### `vi_global_planner` の `cost_drawing_threshold`

表示専用（`value_function` の色スケール上限、単位はステップ数≒秒）。
2026-07-29 実機計測（`map_10cm`, 458x289）では、nav2 既定側の 60 だと到達可能セルの
66% が 100 に張り付き、グラデーションが出るのはゴールから 60 ステップ以内の 6.83%
だけでした（未到達 -1 が 80.07% / 飽和 100 が 13.10% / 1..99 が 6.83%）。
1 ステップ = `action_forward_m` 0.3 m なので 60 ステップ ≒ 18 m 相当で、建物一周の
廊下長に足りていません。同じ計測で到達可能セルの最大は 680 ステップ（≒204 m）、
中央値 60 / p90 110 / p99 300 でした。現在値と選び方は `nav2/vi_planner.yaml` に
書いてあります（地図とゴールで変わるので別の地図では測り直すこと）。

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

* `local_planner:=vi` は使えません。`vi_local_planner` はアウトオブコア経路も
  `map_scale` も持たず、全域を密に解き直すためです（launch が明示的に止めます）。
* `map_scale: 3` の 3x3 プーリングは障害物優先なので、通路は片側最大 0.10m 細ります。
  `robot_radius` 0.22 / `inflation_radius` 0.55 と合わせて通れるかは経路ごとに確認を。
* この地図は 68.2% が未観測 (205) で、真の占有セルは 0.4% しかありません。
  `unknown_as_obstacle: true`（既定）だと未観測が全て壁になる＝舗装路のみ通行可。
  一方 emcl2/AMCL のスキャンマッチングは占有セルの尤度場を使うので、この地図では
  拠り所がほとんどありません（別途要検討）。
* `vi_global_planner` のピーク RSS は実測 3.98GB（匿名 2.16GB + mmap 1.81GB）で、
  **Raspberry Pi 4 4GB で通るかは未確認**です。実測の詳細は
  `tools/pi4_sim/README.md`。
