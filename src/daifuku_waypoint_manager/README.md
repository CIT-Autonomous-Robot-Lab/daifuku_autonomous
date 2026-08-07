# daifuku_waypoint_manager

RViz2 上で waypoint を作り、並べ替え・YAML 保存/読込を行い、Nav2 の
`/follow_waypoints`（`nav2_msgs/action/FollowWaypoints`）へ送るパネルプラグイン。

`nav2_astar` ブランチの `nav2_waypoint_manager` を移植したもの。名前と namespace を
`daifuku_*` にそろえてある。

## `/navigate_through_poses` は使わない

移植元は `/navigate_through_poses` へ投げていたが、**`daifuku_stack` の既定
（`planner:=vi`）ではどう転んでも動かない。** VI 系プランナは
`compute_path_to_pose` しか提供せず、nav2 既定の through_poses BT が要求する
`compute_path_through_poses` が無いためで、構成によって出かたが 2 通りに分かれる。

* **`nav2:=false`（既定）**: `bt_navigator` 自体が立たないので、`navigate_through_poses`
  の**サーバがそもそも居ない**。パネルは「サーバがいません」で止まる。
* **`nav2:=true`**: `navigation.launch.py` が `default_nav_through_poses_bt_xml` を
  `daifuku_stack/behavior_trees/nav_through_poses_stub.xml`（中身は `AlwaysFailure`）
  へ差し替えているので、Start を押した瞬間に **ステータス行が `Failed (aborted)` に
  なり、機体はまったく動かない**。

そこで投げ先は `/follow_waypoints` にしてある。これは 1 点ずつ単発ゴールへ落とす
だけなので、`planner:=vi` でも `planner:=navfn` でも通る。**受けるノードは構成で
変わるが、アクションの型と名前は同じ**なので、パネル側の配線は 1 つで足りる。

| | 受けるノード | 停止時間 | 取りこぼしの扱い |
| --- | --- | --- | --- |
| `nav2:=false`（既定） | `vi_planner` 自身 | `config/nav2/vi_planner.yaml` の `waypoint_pause_sec`（0.2 s） | 同ファイルの `stop_on_failure: false` |
| `nav2:=true` | `nav2_waypoint_follower` | `config/nav2/behaviors.yaml` の `waypoint_pause_duration`（200 ms） | 同ファイルの `stop_on_failure: false` |

代償として **nav2 に「通過点をまとめて 1 本の経路にする」最適化はさせない**（1 点
ずつ止まって次を計画する）。どちらの構成でも `stop_on_failure: false` なので、途中で
行けない点があっても巡回は続き、取りこぼし数だけが完了時のステータス行に出る。

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

## Cancel は常に押せて、常に「全部」を止める

**Cancel はパネルの状態によらず押せる。** ゴールを名指しでは止めず、
`follow_waypoints` と `navigate_to_pose` の**キャンセルサービスへ空の `goal_info`
（= そのサーバの全ゴール）を直接投げる**（`joy_teleop._cancel_goals` と同じ手）。
0.25 秒おきに 8 回、2 秒かけて投げ直す。

こうしてあるのは、**パネルがゴールを持っているかどうかと、機体が走っているかどうかが
別のことだから**。次はどれも「パネルは何も持っていないのに機体は走っている」場面で、
名指しでしか止められない作りだと全部が「Cancel が効かない」になる。

- `joy_teleop` の START+BACK で始まった巡回（パネルは一度もゴールを持たない）
- RViz を立て直した・パネルを閉じて開いた後
- サーバが 10 秒見えず、パネルが先に諦めた後（下記）
- `Nav2 Goal` から出した単発ゴール（`navigate_to_pose` 側）

投げ先が 2 つあるのは `nav2:=true` のためでもある。あちらでは `follow_waypoints` の
下に `navigate_to_pose` の子ゴールがぶら下がるので、`nav2_waypoint_follower` が
固まっていると**上だけ止めても `bt_navigator` 側が走り続ける**。
2 秒間投げ直すのは、無線でその 1 発が落ちると**エラーも出ないまま何も止まらない**ため。

裏返しとして、**Cancel は自分が出していない巡回も止める**。取り消しを投げている
あいだは Start を受け付けない（まだ飛んでいる取り消しが、いま出したゴールを
巻き込むため）。どちらのサービスも見えなかったときは「サーバが居ないので何も
取り消していない」とステータス行に出る — その状態で機体が走っていれば、止めるのは
ジョイスティックかモータ電源になる（`cmd_vel_timeout` は 60 秒）。

## 「巡回中」の表示から抜ける道

表示は `follow_waypoints` の**結果が届くまで**続く。ところが結果は**届かないことが
ある** — サーバ（`nav2:=false` なら `vi_planner` 自身、`nav2:=true` なら
`nav2_waypoint_follower`）が巡回の途中や直後に落ちる・立て直される、無線が切れる、
といった場面で、クライアントは待ち続けたまま二度と起こされない。戻す道は 3 つ。

1. 巡回中に `follow_waypoints` の action サーバが **10 秒**見えないままなら、結果は
   もう来ないと見なして自分で戻す（無線のディスカバリが一瞬途切れただけで戻さない
   ための 10 秒）
2. 諦めたゴールの結果が後から届いたら、拾い直さずに終わり方だけ出す

**取り消しを投げ切った時点では戻さない。** 2 秒は「1 発落ちたから投げ直す」の尺で
あって、サーバが取り消して結果を返すまでの尺ではない（VI の solve 境界で数十秒、
無線が詰まっていればさらに乗る）。そこで戻すと、**効いた取り消しに対して「結果が
来ないので諦めた」と出したあとで結果が届く**ことになる。戻すのは上の 10 秒に任せる。

**戻し過ぎても嘘のままにはならない。** 諦めたあとも `feedback` が届いたら
「巡回はまだ続いている」ということなので、ゴールを持ち直して「巡回中」へ戻す
（ステータス行に `still running - the panel picked the goal back up`）。そのために
諦めたゴールの handle は**捨てずに持っておく** — `rclcpp_action` のクライアントは
goal handle を `weak_ptr` で持つので、最後の `shared_ptr` を落とすと feedback も
結果も二度と来なくなる。ただし `vi_planner` が feedback を出すのは**点が進んだとき
だけ**なので、戻るまでに次の点まで掛かる（`nav2_waypoint_follower` は出し続ける）。

**Cancel はゴールを送った直後（受理待ちの窓）にも押せる。** 諦めた後で受理が届いた
ゴールは、拾い直さずその場で取り消す（放っておくと**画面に出ていない巡回が走り
出す**）。

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

**ただし、この Path が要るのは `nav2:=true` のときだけである。** 既定の `nav2:=false`
では `follow_waypoints` を `vi_planner` 自身が受けるので、順路はゴールと同じ経路で
そのまま届く（このトピックはもう 1 つの入口として残っているだけ）。

`vi_planner` 側はトピック名が `waypoints`、`waypoint_prefetch` は
`daifuku_stack/config/nav2/vi_planner.yaml`・ノードの宣言ともに **`false`** である
（2026-08-04 に一度 `true` へ反転したが、同日の実機で走行中の固まりが出たため容疑者の
1 つとして戻した）。先読みを試すときにこのパネルが出す順路が要るので、ここを直すと
`planner:=vi` + `nav2:=true` の挙動が変わる。代償（メモリ 2 倍）は上の yaml 側に書いてある。

同じものを `daifuku_bringup/src/joy_teleop.py`（START+BACK での巡回開始）も出す。
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
`daifuku_bringup/src/joy_teleop.py` の `load_waypoints` である。**両方が同じ規則で
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
