# daifuku_waypoint_manager

RViz2 上で waypoint を作り、並べ替え・YAML 保存/読込を行い、vi_planner の
`/follow_waypoints`（`nav2_msgs/action/FollowWaypoints`）へ送るパネルプラグイン。

`nav2_astar` ブランチの `nav2_waypoint_manager` を移植したもの。名前と namespace を
`daifuku_*` にそろえてある。

## `/navigate_through_poses` は使わない

`vi_planner` は `/navigate_through_poses` を提供しないため、
`/follow_waypoints` へ順路を送ります。停止時間と失敗時の継続は
`src/daifuku_config/stack/vi_planner.yaml` の `waypoint_pause_sec` と
`stop_on_failure` で設定します。

## Pi では建てない

RViz プラグインなので、実機イメージ (`ros:humble-ros-base`) では**ビルドできない**。
`daifuku_rqt` と同じ扱いで、`docker/raspberrypi/scripts/build-workspace.sh` の
`SKIP_PACKAGES` から**名前を外さないこと**（外すと `up` のビルドが通らなくなる）。
ビルドには `docker/dev/`（`humble-desktop-full`）を使います。

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
- サーバが落ちていて、パネルが「巡回中」の表示から抜けた後（下記）
- `Nav2 Goal` から出した単発ゴール（`navigate_to_pose` 側）

2 秒間投げ直すのは、無線でその 1 発が落ちると**エラーも出ないまま何も止まらない**ため。

裏返しとして、**Cancel は自分が出していない巡回も止める**。取り消しを投げている
あいだは Start を受け付けない（まだ飛んでいる取り消しが、いま出したゴールを
巻き込むため）。どちらのサービスも見えなかったときは「サーバが居ないので何も
取り消していない」とステータス行に出る — その状態で機体が走っていれば、止めるのは
ジョイスティックかモータ電源になる（`cmd_vel_timeout` は 60 秒）。

## 「巡回中」の表示から抜ける道

表示は `follow_waypoints` の**結果が届くまで**続く。ところが結果は**届かないことが
ある** — `vi_planner` が巡回の途中や直後に落ちる・立て直される、無線が切れる、
といった場面で、クライアントは待ち続けたまま二度と起こされない。

**戻す合図は人が Cancel を押したこと**にしてある。取り消しを投げ切ってどちらの
キャンセルサービスも見えなかったら、そのゴールの結果はもう来ないので、そこで表示を
戻す（ステータス行には「サーバが居ないので何も取り消していない」が出る）。
サービスが見えたときは戻さない — 2 秒は「1 発落ちたから投げ直す」の尺であって、
サーバが取り消して結果を返すまでの尺ではないので（VI の solve 境界で数十秒、無線が
詰まっていればさらに乗る）、そこで戻すと**効いた取り消しに対して「結果が来ないので
諦めた」と出したあとで結果が届く**ことになる。

**時間では戻さない。** かつては「サーバが 10 秒見えなければ自分で戻す」タイマを
持っていて、それが誤って戻したときのために「あとから `feedback` が届いたらゴールを
持ち直す」世代管理（`goal_epoch_` / `abandoned_goal_` / `readoptGoal`）を抱えていた。
自作自演なので 2026-08-20 に両方消した。表示が「巡回中」のまま残るのは
**Cancel を押すまで**で、Cancel は常に押せる。

戻した後もゴール自体は走り続けうる（パネルは知らないまま）。だが Cancel は
ゴールを名指しせずサービスで全ゴールを止めるので、それでも機体は止められる。

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
ため。73 点の `waypoints_tsudanuma v1.0.yaml` で効く）。

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

`follow_waypoints` を `vi_planner` 自身が受けるので、順路はゴールにも含まれます。
このトピックは別の入口として残っています。

`vi_planner` 側はトピック名が `waypoints`。`waypoint_prefetch` は
`src/daifuku_config/stack/vi_planner.yaml`・ノードの宣言ともに `false` だが、
**既定の場所である `19f` の overrides が `true` へ上書きしている**ので、引数を何も
足さずに立てれば先読みは入っています。先読みは価値関数を2本保持するため、
メモリ使用量が増えます。

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

`load_waypoints` は UTF-8 を明示して開く。同梱の `waypoints_tsudanuma v1.0.yaml` は冒頭に
日本語の注記を持っていて、ロケールが C の環境（実機のコンテナは `LANG` を持たない）では
既定の encoding が ASCII になり、**読み込みごと失敗する**。

## 保存済みの waypoint

`daifuku_stack/waypoints/waypoints_tsudanuma v1.0.yaml`（73 点、`tsudanuma` 用）と、
そこから採り直した `v1.1`（73 点）・`v1.2`（69 点）。地図に紐づくデータなので
`daifuku_stack` 側の `maps/` の隣に置いてある。`19f` では座標が地図の外に出るので
使えない。**ファイル名に空白が入っている**ので、`waypoints_file` へシェルから渡すときは
引用符で囲むこと（囲まないと `-p waypoints_file:=...` が途中で切れ、空扱いになって
**START+BACK が黙って巡回を断る**）。冒頭の日本語の注記を持つのは `v1.0` だけ。

保存は一時ファイルへ書いてから差し替える（`QSaveFile`）ので、途中で落ちても既存の
YAML は壊れない。
