# daifuku_waypoint_manager

RViz2 上で waypoint を作り、並べ替え・YAML 保存/読込を行うパネルプラグイン。
順路は機体側の `waypoint_runner`（`daifuku_navigation`）へ渡し、巡回は
`waypoint_runner` が vi_planner の `/follow_waypoints`
（`nav2_msgs/action/FollowWaypoints`）へ送る。

`nav2_astar` ブランチの `nav2_waypoint_manager` を移植したもの。名前と namespace を
`daifuku_*` にそろえてある。

## 初期姿勢プリセット

`navigation.rviz` の **2D Pose Estimate** の隣に **Global Estimate Pose** を置きます。
押すと `/emcl3d/global_localization` へ非同期に探索を要求します。探索範囲と結果の適用は
emcl3d の設定に従い、`/initialpose` へは再送しません。ダイアログと Tool Properties の
Status に進捗・適用の有無・失敗理由を表示します。**Cancel search** はこの探索だけを
取り消します。**Close** は表示だけを閉じ、探索中にツールを再度押すと同じ進捗を開きます。
ビルドには `emcl3d/action/GlobalLocalization` を提供する emcl3d が必要です。

`navigation.rviz` には `initial_pose_preset_panel/InitialPosePresetPanel` も設定済みです。
**2D Pose Estimate** で `/initialpose` を指定し、名前を入れて **Add Current** で保存、
**Apply** で再送します。**Add Robot Pose** は現在の `map → base_link` TF を保存します。
保存先は RViz を動かす PC のユーザー設定ディレクトリです。**Open YAML** と
**Save As...** でファイルを選べます。選んだパスを次回も使うには RViz の設定を保存します。

移植元: [initial_pose_preset_panel](https://github.com/CIT-Autonomous-Robot-Lab/daifuku_autonomous_nav2/tree/main/src/initial_pose_preset_panel)（Apache-2.0）。

## `/navigate_through_poses` は使わない

`vi_planner` は `/navigate_through_poses` を提供しないため、
`/follow_waypoints` へ順路を送ります。到達後は人工的な待ち時間を入れず次の点へ進みます。
失敗時の継続は `src/daifuku_config/navigation/vi_planner.yaml` の
`stop_on_failure` で設定します。

## Pi では建てない

RViz プラグインなので、実機イメージ (`ros:humble-ros-base`) では**ビルドできない**。
`daifuku_rqt` と同じ扱いで、`docker/raspberrypi/scripts/build-workspace.sh` の
`SKIP_PACKAGES` から**名前を外さないこと**（外すと `up` のビルドが通らなくなる）。
ビルドには `docker/dev/`（`humble-desktop-full`）を使います。

## 使い方

`navigation.launch.py` が読む `daifuku_bringup/rviz/navigation.rviz` には設定済み。
別の RViz 設定から使うときは以下が要る。

1. **Panels → Add New Panel** から `daifuku_waypoint_manager/WaypointManagerPanel`
2. `MarkerArray` 表示を足し、topic を `/waypoint/markers`、Durability を
   **Transient Local** にする（パネルが latch して出すので、あとから開いても出る）
3. `2D Goal Pose` (`rviz_default_plugins/SetGoal`) の topic を `/waypoint/pose` にする

**3 の付け替えで、`2D Goal Pose` は単発ゴールを出さなくなる。** 通常の 1 点ナビゲー
ションは `Nav2 Goal` (`nav2_rviz_plugins/GoalTool`) 側に残っているのでそちらを使う。
付け替えないと、waypoint を足せるのは `Publish Point` の `/clicked_point` 経由だけに
なり、**向きが常に単位クォータニオン（yaw 0）になる**（パネルに yaw の手入力欄は無い）。

地図上をクリック＋ドラッグで、クリック位置が座標、ドラッグ方向が向きになる。
**Start** で巡回開始、**Cancel** で停止。巡回中は「いま何点目か」がステータス行に出る
（`Robot:` で始まる行は `waypoint_runner` の報告）。

## 停止点（一時停止）

リストで点を選んで **Toggle Stop** を押すと停止点になる（リストに `[STOP]`、矢印が赤、
ラベルに `STOP`）。巡回は停止点に着くと止まり、**Resume** で次の点から再開する。
止まっているあいだに **Start** を押すと最初から、**Cancel** を押すと再開できなくなる。

ゲームパッドの B（丸）でも再開できる。`joy_teleop` が `/waypoint/resume` を出す。
止まっていないとき（走行中・巡回していない）に届いた要求は捨て、理由をステータス行に出す。

### 区切りと再開は機体の上で持つ

止める仕組みは機体側の `waypoint_runner`（`daifuku_navigation`）にある。
順路を停止点で区切り、区間ごとに別の `follow_waypoints` ゴールとして送る。
区間のゴールが SUCCEEDED で返ったら、そこが停止点である。`vi_planner` は停止点を知らない。

パネルに持たせない理由は、**走行中に RViz の PC を外す運用**のためである。
パネルが区間を送ると、PC を外したあいだに結果を取りこぼす。パネルは「まだ走っている」と
思い込み、再開を捨て続ける。B の `/waypoint/resume` も受け手が居ないので消える。
`waypoint_runner` は機体で動くので、どちらも起きない。

パネルと `waypoint_runner` の間は次のトピックでつなぐ。

| トピック | 型 | 向き | 内容 |
| --- | --- | --- | --- |
| `/waypoint/route` | `nav_msgs/Path`（latch） | パネル → 機体 | 順路全体 |
| `/waypoint/stops` | `std_msgs/Int32MultiArray`（latch） | パネル → 機体 | 停止点の番号 |
| `/waypoint/start` | `std_msgs/Empty` | パネル → 機体 | 最初から走る |
| `/waypoint/resume` | `std_msgs/Empty` | パネル・`joy_teleop` → 機体 | 停止点から再開 |
| `/waypoint/status` | `std_msgs/String`（latch） | 機体 → パネル | 状態の文 |
| `/waypoint/next` | `std_msgs/Int32`（latch） | 機体 → パネル | 次に送る点。走行中は -1 |

状態は latch なので、PC をつなぎ直すと今の状態がそのまま見える。`/waypoint/status` は
rosbag にも残る。

- **区間をまたいだ先読みはたぶん効かない。** ゴールに入るのは区間の点だけで、
  `/waypoint/route` には順路全体が流れる。`vi_planner` がどちらを見て「次」を決めるかは
  **未検証**
- **止まっているあいだに点を消す・並べ替える・読み込み直すと、再開できなくなる**
  （順路が変わると再開先の番号が別の点を指すため、`waypoint_runner` が捨てる）。
  停止点を付け外しするだけなら再開できる。同じ順路が届き直しても（PC のつなぎ直し）捨てない

YAML では停止点の点にだけ `stop: true` を書く（省略は `false`）。

## Cancel は常に押せて、常に「全部」を止める

**Cancel はパネルの状態によらず押せる。** ゴールを名指しでは止めず、
`follow_waypoints` と `navigate_to_pose` の**キャンセルサービスへ空の `goal_info`
（= そのサーバの全ゴール）を直接投げる**。
0.25 秒おきに 8 回、2 秒かけて投げ直す。

こうしてあるのは、**パネルがゴールを持っているかどうかと、機体が走っているかどうかが
別のことだから**。次はどれも「パネルは何も持っていないのに機体は走っている」場面で、
名指しでしか止められない作りだと全部が「Cancel が効かない」になる。

- RViz を立て直した・パネルを閉じて開いた後
- `waypoint_runner` が送った巡回（パネルはゴールを持たない）
- `Nav2 Goal` から出した単発ゴール（`navigate_to_pose` 側）

2 秒間投げ直すのは、無線でその 1 発が落ちると**エラーも出ないまま何も止まらない**ため。

裏返しとして、**Cancel は自分が出していない巡回も止める**。取り消しを投げている
あいだは Start を受け付けない（まだ飛んでいる取り消しが、いま出したゴールを
巻き込むため）。どちらのサービスも見えなかったときは「サーバが居ないので何も
取り消していない」とステータス行に出る — その状態で機体が走っていれば、止めるのは
ジョイスティックかモータ電源になる（`cmd_vel_timeout` は 60 秒）。

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
ため。同梱の津田沼の順路でも効く）。

**巡回中は引かない。** 1 点目はもう機体の後ろにあり、そこへ線を引いても嘘になる。
走行中に向かっている先は Nav2 の `Path` 表示のほうに出る。走行中かどうかと、
停止点で止まっているときの線の行き先（再開後の 1 点目）は `/waypoint/next` で決める。

## 順路を `/waypoint/route` に出す

マーカ（見せるため）とは別に、順路そのものを `nav_msgs/Path` で `/waypoint/route` へ
latch して出す。**これは他ノードが読むためのもの**で、用途は 2 つある。
`waypoint_runner` が区間を切ることと、`vi_planner` の先読み（`waypoint_prefetch`）。VI は経路計画も経路追従も
ゴールごとの価値反復に依っていて、一致するキャッシュや先読み結果がなければ
solve 中は機体が止まる（過去の実測で 19F が 29 秒、津田沼が 87 秒）。
次の点が分かっていれば走行中に解いておける。「次の点」を知る手段がこの Path。
キャッシュと先読みは `(x, y, 整数度の向き)` の完全一致で照合する。
向きは 0 以上 360 未満へ正規化して整数度へ切り捨て、同じセル内でも座標が異なれば再利用しない。

出すのは**並びが変わったとき**だけ（`publishMarkers(reset=true)` の側）。選択の
変更と機体からの線の引き直しでは出さない — 走行中に毎秒飛ばすと購読側が並びを
受け取り直し続けるため。

`vi_planner` 側は `src/daifuku_config/navigation/vi_planner.yaml` の `waypoint_topic` で
`waypoint/route` を見る（ノードの既定は `waypoints`）。`waypoint_prefetch` は
`src/daifuku_config/navigation/vi_planner.yaml`・ノードの宣言ともに `false` だが、
**既定の場所である `19f` の overrides が `true` へ上書きしている**ので、引数を何も
足さずに立てれば先読みは入っています。先読みは価値関数を2本保持するため、
メモリ使用量が増えます。

トピック名はパネルの `kWaypointPathTopic`、`waypoint_runner`、`vi_planner` の
`waypoint_topic` の 3 か所にあり、片方だけ変えると**エラーは出ず、巡回も先読みも黙って動かない**。

**パネルは絶対名 `"/waypoint/..."`**、機体側（`waypoint_runner`・`vi_planner`・`joy_teleop`）は
相対名である。名前空間なし（既定）では噛み合うが、`namespace:=` を付けた構成では
パネルの出す名前と機体の見る `/<名前空間>/waypoint/...` が別になる。**警告は出ない。**

## frame_id

RViz の Fixed Frame と waypoint の `frame_id` が一致している必要がある。waypoint が
すでに登録されている状態で別の `frame_id` の Pose を受けると、誤操作防止のため
**黙って捨てずにステータス行にエラーを出して**追加しない。YAML の追加読み込み
（Append）も `frame_id` が一致するファイルだけが対象。

## YAML の受け入れ規則

読み手はこのパネルの `readYamlFile` である。同梱の順路を検算する
`tools/maps/waypoint_yaml.py`（`verify_waypoints.py` が使う）も**同じ規則で受けること。**
ずれると、検算は通るのにパネルでは開けない順路ができる。

- `frame_id` は必須。既定を持たせない — 書き忘れた順路が黙って `map` 上の座標として
  走ると、座標系を取り違えたときに全点が地図の外に出る（`plan` が失敗するだけなので、
  外からは recovery の spin が回っているようにしか見えない）
- `position.z` は省略可（`0.0`）。接地して走る機体なので、無くても意味が決まる
- 有限でない値と、長さが 0 のクォータニオンは弾く。`NaN` のまま `FollowWaypoints` へ
  投げると Nav2 の側で黙って落ちる

`waypoint_yaml.py` は UTF-8 を明示して開く。同梱の `waypoints_tsudanuma_v1.0.yaml` は冒頭に
日本語の注記を持っていて、ロケールが C の環境（実機のコンテナは `LANG` を持たない）では
既定の encoding が ASCII になり、**読み込みごと失敗する**。

## 保存済みの waypoint

`daifuku_maps/waypoints/waypoints_tsudanuma_v1.0.yaml` と `waypoints_tsudanuma_v1.1.yaml`
（`tsudanuma` 用。座標は回廊地図に合わせた順路）。地図に紐づくデータなので
`daifuku_maps` の `maps/` の隣に置いてある。`19f` では座標が地図の外に出るので
使えない。冒頭の日本語の注記を持つのは `v1.0` だけ。

保存は一時ファイルへ書いてから差し替える（`QSaveFile`）ので、途中で落ちても既存の
YAML は壊れない。
