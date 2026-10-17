# Tsukuba Challenge 2025 / VTC

2025年の公式課題図をVTC world XYへ対応付けたシミュレーション用の候補。
完走済み設定ではない。第21回は地点0..51を通過し、地点52で実到達誤差
0.616458mとなり、基準0.6mを超えて不合格。検証記録は
`simulator/runs/vtc/isaac-course-20261016/attempt-21-2025/`に保存している。

保存時の検証: 全41枚の画素一致と経路・設定・重力補正を含む61テスト、
両ROSパッケージの27検査、全地図の閾値検算が合格。分離install後の専用launchで
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
自己位置側のみ30mより遠方では下限4.5度とし、測距上限は60m。
高さ上限5.5mと近距離の仰角選択を保ち、地形しか観測しない空白区間を減らす。
対応するIsaac設定はLiDAR60m、常駐範囲75m。経路計画側は従来の30mのまま。
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
