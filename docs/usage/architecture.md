# 構成とパッケージ

## システム構成

```text
Raspberry Pi Cat                         Docker / ネイティブPC
────────────────────                    ──────────────────────
モータードライバ  ←── /cmd_vel ─────── 経路追従 / 遠隔操作
車輪オドメトリ    ─── /odom ─────────→ Nav2 / SLAM / 自己位置推定
2D LiDAR          ─── /scan_raw ───────────────────┐
Mid-360 ─ /livox/lidar ─ 3D→2D ─ スタンプ打ち直し ─┴→ 角度フィルタ → /scan
TF                ─── odom → base_footprint → センサーフレーム
```

Mid-360 + IMUの場合、車輪入力は`/wheel/odom`となり、EKFが最終的な`/odom`と`odom -> base_footprint`を生成します。

Mid-360は時刻同期がないためスタンプが実時計からずれていきます。`restamp_scan.py`が
`/scan_mid360_prestamp`を受信時刻で打ち直して`/scan_raw`へ流し、以降は2D LiDARと同じ
経路になります。詳細は[LiDARとオドメトリ](../setup/lidar.md#タイムスタンプの打ち直し)を
参照してください。

## autonomous_nav

このリポジトリの設定パッケージです。C++や通常のPythonノードは持たず、次をまとめます。

- Nav2、SLAM Toolbox、EMCL2の設定
- LiDAR前処理とEKF
- launchファイル
- 地図とRViz設定

## 自己位置推定

- `localization:=emcl2`: 外部パッケージ`emcl2`が`map -> odom`を推定
- `localization:=amcl`: Nav2標準`nav2_amcl`を使用

## 経路計画と追従

`planner:=vi`（既定）:

- `vi_global_planner`が`planner_server`の代わりに`compute_path_to_pose`アクションを提供
- 静的地図から(x, y, θ)の価値関数を計算し、方策のロールアウトで経路を生成
- 同一ゴールへの再計画はキャッシュを利用
- `local_planner:=auto`では`vi_local_planner`が`controller_server`の代わりに`follow_path`を提供
- ローカル価値関数へ`/scan`由来のペナルティを加え、`cmd_vel`を生成

`planner:=navfn`:

- Nav2標準`planner_server`と`NavfnPlanner`を使用
- `local_planner:=auto`ではNav2標準`controller_server`とDWBを使用

## Nav2コンポーネント

構成に応じて次を使用します。

- `nav2_map_server`: 地図配信
- `nav2_bt_navigator`: NavigateToPose等のビヘイビアツリー
- `nav2_costmap_2d`: ローカル／グローバルコストマップ
- `nav2_smoother`: 経路平滑化
- `nav2_behaviors`: Spin、BackUp、Wait等
- `nav2_waypoint_follower`: 経由地点追従
- `nav2_velocity_smoother`: 速度平滑化
- `nav2_lifecycle_manager`: ライフサイクル管理

ローカルコストマップはVoxelLayer + InflationLayer、グローバルコストマップはStaticLayer + ObstacleLayer + InflationLayerを使い、障害物入力は`/scan`です。

### プロセス構成

Nav2の各ノードは既定でプロセスを分けて起動します（`use_composition:=False`）。
Raspberry Pi 4で1プロセスへ合成すると、DDS参加者あたりのエンドポイント数が大きく
なりすぎて新規参加者からディスカバリできなくなり、さらにCPU飢餓でライフサイクル
マネージャのbond心拍が途絶して自動シャットダウンする事象が頻発しました。あわせて
`config/lifecycle_bond.yaml`でbondのタイムアウトを60秒へ延長しています。

PCなど余裕のある環境では`use_composition:=True`も利用できます。

## 外部パッケージ

- `slam_toolbox`: `/scan`とオドメトリから地図を作成
- `emcl2_ros2`: EMCL2自己位置推定
- `value_iteration3`: Rust/rclrs製の広域・狭域価値反復プランナ
- `livox_ros_driver2`: Mid-360ドライバ
- `pointcloud_to_laserscan`: Mid-360点群の2D化
- `robot_localization`: IMUと車輪オドメトリの融合

外部ソースはDockerビルド時または`vcs import`時に`autonomous_bot.repos`から取得します。
