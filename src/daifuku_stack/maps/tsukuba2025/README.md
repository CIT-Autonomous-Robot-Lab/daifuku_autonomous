# Tsukuba Challenge 2025 / VTC

2025年の公式課題図をVTC world XYへ対応付けたシミュレーション用の候補。
完走済み設定ではない。第28回は地点0..93を通過し、地点94の期限超過で不合格。
第29回の実スキャン付き部分診断では92..94、54..57、121..124を通過した。
30度旋回では60方位の一部から目標の終端方位へ遷移できないため、
2025専用設定を20度旋回へ変更した。compact solverとgreedy追従は維持している。
同じ停止姿勢での20度変更だけでは94を回復できず、旋回設定だけを停止の
唯一の原因とは断定していない。部分診断は全コース完走には数えない。
診断記録は`simulator/runs/vtc/isaac-course-20261016/attempt-29-2025-chatter-probes/`。

第25回は地点0..122を通過し、地点123の期限超過で不合格。
2,053.325 mを実時間比0.999999で走行し、実画像38,619フレームを保存した。
地点102・118の到達誤差は0.183 m・0.029 mで、以前の観測不足は解消した。
検証記録は`simulator/runs/vtc/isaac-course-20261016/attempt-25-2025/`に保存している。

地点123の停止姿勢では車体底面と元地形の間隔が約0.6 mm、右駆動輪の間隔が
約8.1 mmだった。実指令は下流まで届いたが、20秒間の旋回は要求10.45 radに対し
約0.0065 radで、物理的な支持・干渉が疑われた。元地形の急傾斜面を経路計画側の
28セルへ障害物として追加し、第26回の二つの部分診断で地点123・124を通過した。
後者は第25回の進入XY・向きを使う新規スポーンで、動的関節状態の完全再現ではない。
元地形・ロボット・位置推定地図・全150目標は変更していない。
航行地図の生成元は`navigation-terrain-v4`。部分診断は全コース完走には数えない。

保存時の検証: 全41枚の画素一致と経路・設定・重力補正を含む61テスト、
両ROSパッケージの33検査、全地図の閾値検算が合格。分離install後の専用launchで
両map_serverと実点群の`/scan_loc`接続を確認した。起動確認では初期位置を
投入しておらず、位置推定の収束や全コース完走の検証には数えていない。

## 保存内容

- `navigation.yaml` / `navigation.pgm`: 全域の経路計画地図、0.2m/cell。
- `localization.yaml` / `localization.pgm`: 全域の独立した位置推定地図、0.15m/cell。
  元衝突メッシュの側面を投影した`localization-sides-v4`。水平キャップと
  凸包内部の塗り潰しを除き、重なる側面・内壁は残す。観測SLAM地図ではない。
- `regions/`: 120m四方の39地図。全域計画地図の画素を変更せず切り出したもの。
- `course.json`: 全150地点、約2454.302mの順路、領域対応、出典、
  座標登録精度、必須折返し点、派生モデルの変更履歴。
- `../../waypoints/waypoints_tsukuba2025_v1.0.yaml`: 既存Waypoint Manager形式。
  名目間隔20mで、曲がり角・公式図の制御点・往復を保持。

全体地図は806.4m四方、原点は[-403.2,-403.2]m。元CIT/VTC資産は変更せず、
公式2025ルートに必要な派生地形・市役所前モデルに対応する地図を保存した。
この2D地図だけでは対応するIsaac物理ワールドは生成しない。

## 起動

新規ファイルのinstall反映には、既存のROS環境で一度ビルドする。

```bash
colcon build --symlink-install --packages-select daifuku_config daifuku_stack
source install/setup.bash
```

### 実機

既定は`use_sim_time:=false`、`use_rviz:=false`。`lidar_driver`は機体と同じ
環境変数`LIDAR_DRIVER`（未設定なら`true`）を使う。ドライバ自体はこのlaunchでは
起動せず、既存の`robot_bringup`から`/livox/lidar`、`/odom`、
`odom -> base_footprint`とLiDARのTFを受け取る。`/clock`は不要。
既存`site`は書き換えない。

Pi上では依存と新規ファイルを反映してから起動する。

```bash
docker compose build
docker compose up -d
docker compose exec ros2 /ros_entrypoint.sh \
  ros2 launch daifuku_stack navigation_tsukuba2025.launch.py
```

実機では両系統の仰角フィルタで点群の時刻を受信時刻へ直してからTF投影する。
計画側は既存の`restamp_scan`も通す。Livoxのデバイス時計のずれを
自己位置側だけに残さないためで、`lidar_driver:=false`では両系統とも打ち直さない。
この構成はMid-360専用で、2D LiDAR用ではない。

初期位置は地図座標の`/initialpose`へ与える。RVizを使うときだけ
`use_rviz:=true`を添えて2D Pose Estimateを使う。表示しない場合の例は次のとおり。
これは同梱順路の先頭を向く例で、実際の開始位置に合わせて変更する。

```bash
docker compose exec ros2 /ros_entrypoint.sh ros2 topic pub --once /initialpose \
  geometry_msgs/msg/PoseWithCovarianceStamped \
  '{header: {frame_id: map}, pose: {pose: {position: {x: 298.2, y: -146.6}, orientation: {z: 1.0, w: 0.0}}, covariance: [0.25, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.25, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0685]}}'
```

地図は下記のVTC由来の候補で、実測地図への置換や現地での位置合わせは別途必要。
既定の計画範囲と全コースの切替条件は次節を参照。実機走行の完了証拠はない。

### シミュレーション

```bash
ros2 launch daifuku_stack navigation_tsukuba2025.launch.py use_sim_time:=true
```

この場合の`lidar_driver`は既定`false`となり、点群とスキャンの時刻を保持する。
事前に対応するusimワールド、`/clock`、`/livox/lidar`、`/odom`、
`odom -> base_footprint`とLiDARのTFを供給する。シミュレータ自体は起動しない。

設定の実体は既存規約に従い
`daifuku_config/overrides/tsukuba2025.yaml`と
`daifuku_config/stack/tsukuba2025/`に置く。専用launchは通常のnavigationに加えて、
自己位置用`/scan_loc`と経路計画用`/scan`を別々に生成する。
自己位置側は9.5..75度、経路計画側は4.5..75度の実点群を使う。
自己位置側のみ30mより遠方では下限4.5度、60mより遠方では1.25度とし、
測距上限は100m。
高さ上限5.5mと近距離の仰角選択を保ち、地形しか観測しない空白区間を減らす。
対応するIsaac設定はLiDAR100m、実仰角0/1.5/5/10/15/20度、常駐範囲106m、
表示5Hz。経路計画側は従来の30mのまま。
地点102の静止試験では、有効観測が0本から54本へ増え、実測点群からの再投影は
最大15.3µm以内で一致した。同じ側面地図の尤度最大は真値から約10.6cm。
実時間比1.000807で動作したが、静止試験を全コースの完走証拠には数えない。
`localization_gravity`は同位置の重力補正フレームで、odomのroll/pitchだけを使い、
位置やグローバルyawを自己位置推定へ戻さない。
既定の実機EKFは`two_d_mode: true`なのでroll/pitchは0で、このフレームは
機体と同じ向きになる。実機のIMUから傾斜を推定する機能ではない。

solverはユーザー指定の`frontier2d_sparse_compact`、追従は`greedy`。
全コース実験用の`navigation-params.yaml`も同じ組合せに揃えた。
第21回までの記録は変更前の`frontier2d_sparse`＋`dwa`によるもので、
新しい組合せの完走証拠ではない。

既定の計画地図は`regions/region-000.yaml`のみ。150地点を単純送信しても
全域は走れない。全コース実験では`course.json`の`goal_regions`に従い領域を
切り替え、対応するVIを再構成する既存の`run_course.py`を使っている。
専用launchはその自動コース実験を開始しない。全体地図をVIへ直接渡すと
状態数が大きくなるため、既定にはしない。

記録した実験のemcl2はスキャン鮮度とリサンプリング後の重み初期化の修正を含む。
修正ソース・テスト証拠は同実験ディレクトリの`attempt-01/emcl2-source`、
`attempt-17-2025/resampling-{red,green}.log`を参照。この設定保存だけで
別環境のemcl2バイナリが更新されるわけではない。

## 出典・精度

公式出典と画像URL・SHA256は`course.json`に保存。市役所の周回方向は、
ユーザー指定どおり公式図の矢印を採用した。14対応点の登録RMSは約4.97m、
最大約9.29m。舗道への補正も含み、測量精度や現地の歩道内走行を保証しない。
公園内は許容された舗装路で必須折返しへ往復する。任意の配送課題は認定しない。

VTC、fuRo、GSIおよび元データの帰属条件は`NOTICE`を保持する。
地図全体を一律Apache-2.0として扱わない。再生成入口は
`simulator/runs/vtc/isaac-course-20261016/export_package_maps.py`。
