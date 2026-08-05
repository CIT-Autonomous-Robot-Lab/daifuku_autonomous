# daifuku_waypoint_manager

RViz2 上で waypoint を作り、並べ替え・YAML 保存/読込を行い、Nav2 の
`/follow_waypoints`（`nav2_msgs/action/FollowWaypoints`）へ送るパネルプラグイン。

`nav2_astar` ブランチの `nav2_waypoint_manager` を移植したもの。名前と namespace を
`daifuku_*` にそろえてある。

## `/navigate_through_poses` は使わない

移植元は `/navigate_through_poses` へ投げていたが、**`daifuku_stack` の既定
（`planner:=vi`）ではそれが必ず失敗する。** VI 系プランナは
`compute_path_to_pose` しか提供せず、nav2 既定の through_poses BT が要求する
`compute_path_through_poses` が存在しないため、`navigation.launch.py` が
`default_nav_through_poses_bt_xml` を
`daifuku_stack/behavior_trees/nav_through_poses_stub.xml`（中身は `AlwaysFailure`）
へ差し替えているから。Start を押した瞬間に **ステータス行が
`Failed (aborted)` になり、機体はまったく動かない**。

`nav2_waypoint_follower` は 1 点ずつ `navigate_to_pose` を呼ぶだけなので、
`planner:=vi` でも `planner:=navfn` でも通る。両方の経路で lifecycle 管理下に
起動している（`vi_global_planner/launch/navigation_launch.py` と nav2 の
`navigation_launch.py`）。

代償として **nav2 に「通過点をまとめて 1 本の経路にする」最適化はさせない**（1 点
ずつ止まって次を計画する）。各点での停止時間は
`config/nav2/behaviors.yaml` の `waypoint_pause_duration`。同ファイルの
`stop_on_failure: false` により、途中で行けない点があっても巡回は続き、取りこぼし
数だけが完了時のステータス行に出る。

## Pi では建てない

RViz プラグインなので、実機イメージ (`ros:humble-ros-base`) では**ビルドできない**。
`daifuku_rqt` と同じ扱いで、`docker/raspberrypi/scripts/build-workspace.sh` の
`--packages-select` に**名前を足さないこと**（足すとイメージのビルドが通らなくなる）。
建てるのは `docker/dev/`（`humble-desktop-full`）と
`tools/setup/setup_native_base.sh` の 2 つ。

## 使い方

`navigation.launch.py` が読む `daifuku_stack/rviz/navigation.rviz` には設定済み。
別の RViz 設定から使うときは以下が要る。

1. **Panels → Add New Panel** から `daifuku_waypoint_manager/WaypointManagerPanel`
2. `MarkerArray` 表示を足し、topic を `/waypoint_markers`、Durability を
   **Transient Local** にする（パネルが latch して出すので、あとから開いても出る）
3. `2D Goal Pose` (`rviz_default_plugins/SetGoal`) の topic を `/waypoint_pose` にする

**3 の付け替えで、`2D Goal Pose` は単発ゴールを出さなくなる。** 通常の 1 点ナビゲー
ションは `Nav2 Goal` (`nav2_rviz_plugins/GoalTool`) 側に残っているのでそちらを使う。
付け替えないと、waypoint を足せるのは `Publish Point` の `/clicked_point` 経由だけに
なり、**向きが常に単位クォータニオン（yaw 0）になる**（パネルに yaw の手入力欄は無い）。

地図上をクリック＋ドラッグで、クリック位置が座標、ドラッグ方向が向きになる。
**Start** で巡回開始、**Cancel** で停止。巡回中は「いま何点目か」がステータス行に出る。

## 選択中の waypoint

リストで選んでいる 1 点は、地図上でも**矢印が黄色くなり、足元に半透明の円が出る**
（マーカの `ns` は `waypoint_selected`）。番号のラベルだけでは、73 点あるときに
リストの行と地図上のどれが対応するのか追えないため。

選択を変えるたびにマーカを出し直すが、`DELETEALL` は付けない（付けると全マーカが
作り直されて瞬く）。`refreshList()` の中の `clear()` / `setCurrentRow()` でも
`currentRowChanged` は飛ぶので、その間の出し直しは止めてある（呼び出し元が直後に
`publishMarkers()` するため）。

## 機体から 1 点目までの線

waypoint どうしを結ぶオレンジの線に加えて、**機体の現在地から 1 点目まで**の区間を
半透明で引く（マーカの `ns` は `waypoint_lead`）。順路の始まりが見えないと、そもそも
どこから走り出すのかが分からないため。

引くには次の 2 つが要る。どちらかが欠けると線は出ないので、**理由をステータス行に
出す**（黙って消えると設定の間違いに気づけない）。

- RViz の Fixed Frame が waypoint の `frame_id` と一致していること（マーカを Fixed
  Frame 基準で作るため）
- TF に `base_footprint` があること（無ければ `base_link` を見る）

この区間だけは機体について動くので、0.5 秒ごとに引き直す。ただし **5 cm 以上動いた
ときだけ** MarkerArray を出し直す（1 回の出し直しが全 waypoint のマーカの再送になる
ため。73 点の `waypoints_tsudanuma.yaml` で効く）。

**巡回中は引かない。** 1 点目はもう機体の後ろにあり、そこへ線を引いても嘘になる。
走行中に向かっている先は Nav2 の `Path` 表示のほうに出る。

## 順路を `/waypoints` に出す（先読み用）

マーカ（見せるため）とは別に、順路そのものを `nav_msgs/Path` で `/waypoints` へ
latch して出す。**これは他ノードが読むためのもの**で、いまの用途は
`vi_planner` の先読み（`waypoint_prefetch`）1 つ。VI は経路計画も経路追従も
ゴールごとの価値反復に依っていて、点が変わるたびに丸ごと 1 回解き直す（実測で
19F が 29 秒、津田沼が 87 秒）。その間**機体は止まっている**ので、次の点が分かって
いれば走行中に解いておける。「次の点」を知る手段がこの Path。

出すのは**並びが変わったとき**だけ（`publishMarkers(reset=true)` の側）。選択の
変更と機体からの線の引き直しでは出さない — 走行中に毎秒飛ばすと購読側が並びを
受け取り直し続けるため。

`vi_planner` 側はトピック名が `waypoints`、`waypoint_prefetch` は
`daifuku_stack/config/nav2/vi_planner.yaml` が **`true`** にしている（2026-08-04 に反転。
ノードの宣言は `false`）。**つまりこのパネルが出す順路がそのまま先読みを動かす**ので、
ここを直すと `planner:=vi` の挙動が変わる。代償（メモリ 2 倍）は上の yaml 側に書いてある。

同じものを `daifuku_stack/src/joy_teleop.py`（START+BACK での巡回開始）も出す。
**実機のイメージにこのパネルは入らない**ので、機体だけで走らせるときはあちらが
出どころになる。トピック名はパネルの `kWaypointPathTopic`・`joy_teleop` の
publisher・`vi_planner` の `waypoint_topic` の 3 か所にあり、1 つだけ変えると
**エラーは出ず、ただ先読みが効かない**。

**パネルだけが絶対名 `"/waypoints"`** で、残る 2 つは相対名 `"waypoints"` である。
名前空間なし（既定）では 3 つとも `/waypoints` に解決されるので噛み合うが、
`namespace:=` を付けた構成ではパネルの出す `/waypoints` と `vi_planner` の見る
`/<名前空間>/waypoints` が別になる。**このときも先読みが黙って効かなくなるだけ**で、
警告は出ない。

## frame_id

RViz の Fixed Frame と waypoint の `frame_id` が一致している必要がある。waypoint が
すでに登録されている状態で別の `frame_id` の Pose を受けると、誤操作防止のため
**黙って捨てずにステータス行にエラーを出して**追加しない。YAML の追加読み込み
（Append）も `frame_id` が一致するファイルだけが対象。

## YAML の受け入れ規則

読み手が 2 つある。このパネルの `readYamlFile` と、実機で巡回を始める
`daifuku_stack/src/joy_teleop.py` の `load_waypoints` である。**両方が同じ規則で
受けること。** 片方だけが通す形にすると、手で書いた順路が「実機では走るのに
パネルでは開けない」（あるいはその逆）になり、しかもどちらの側にも異常が出ない。

- `frame_id` は必須。既定を持たせない — 書き忘れた順路が黙って `map` 上の座標として
  走ると、座標系を取り違えたときに全点が地図の外に出る（`plan` が失敗するだけなので、
  外からは recovery の spin が回っているようにしか見えない）
- `position.z` は省略可（`0.0`）。接地して走る機体なので、無くても意味が決まる
- 有限でない値と、長さが 0 のクォータニオンは弾く。`NaN` のまま `FollowWaypoints` へ
  投げると Nav2 の側で黙って落ちる

`load_waypoints` は UTF-8 を明示して開く。同梱の `waypoints_tsudanuma.yaml` は冒頭に
日本語の注記を持っていて、ロケールが C の環境（実機のコンテナは `LANG` を持たない）では
既定の encoding が ASCII になり、**読み込みごと失敗する**。

## 保存済みの waypoint

`daifuku_stack/waypoints/waypoints_tsudanuma.yaml`（73 点、`map_tsudanuma` 用）。
地図に紐づくデータなので `daifuku_stack` 側の `maps/` の隣に置いてある。
`map_19f` では座標が地図の外に出るので使えない。

保存は一時ファイルへ書いてから差し替える（`QSaveFile`）ので、途中で落ちても既存の
YAML は壊れない。
