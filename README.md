# daifuku_autonomous

[![CI](https://github.com/CIT-Autonomous-Robot-Lab/daifuku_autonomous/actions/workflows/ci.yml/badge.svg)](https://github.com/CIT-Autonomous-Robot-Lab/daifuku_autonomous/actions/workflows/ci.yml)

[Raspberry Pi Cat](https://rt-net.jp/products/raspicat)をROS 2 Navigation2で自律移動させるためのワークスペースです。

- 2D LiDARまたはLivox Mid-360に対応
- SLAM Toolboxによる地図作成
- EMCL3Dによる自己位置推定（2D LiDARではEMCL2やAMCLを選択可能）
- 価値反復プランナによる経路計画と追従
- キーボードとジョイスティックによる遠隔操作
- Dockerのヘッドレス実行環境とGUI付き開発環境

本体ドライバは自前実装（`driver:=original`、[`src/raspicat_driver`](src/raspicat_driver/README.md)）と公式実装（rtmouse + raspimouse）から選べます。Docker運用の標準は前者で、公式実装はrtmouseが動くRaspberry Pi 4に限られます（[Pi 4](docs/setup/raspberry-pi-4.md) / [Pi 5](docs/setup/raspberry-pi-5.md)）。

## はじめる

1. [セットアップガイド](docs/setup/README.md)で実行環境とセンサーを準備する
2. [利用ガイド](docs/usage/README.md)で地図作成または自律移動を開始する

## ノードとトピックの流れ

名前空間なしの実機構成です。`robot.launch.py` は機体とセンサーを立ち上げ、
`navigation.<場所>.launch.py` は点群処理・自己位置推定・経路計画を立ち上げます。
場所別の自律移動は、Mid-360 と EMCL3D を使う構成が既定です。

### 点群・地図・自己位置

```text
[Mid-360] --> [livox_lidar_publisher] -- /livox/lidar --> [elevation_filter]
                                                            |
                                                  /livox/lidar_elevation
                                                            |
    +-------------------------------------------------------+
    |
    +--> [emcl3d] -- map -> odom (TF)
    |        ^
    |        +-- (file) -- PCD map
    |        +-- /initialpose -- [RViz]
    |
    +--> [pointcloud_to_laserscan]                          [vi_planner]
             |                                                    ^ ^
          /scan_mid360_prestamp                                   | |
             |                                                    | |
             v                                                    | |
         [restamp_scan] -- /scan_raw --> [scan filter or relay]   | |
                                               |                  | |
                                               +-- /scan ---------+ |
                                                                    |
    Planning map (YAML + image) -- (file) --> [map_server] -- /map -+
```

`scan filter or relay` は `scan_to_scan_filter_chain`（角度フィルタ）または
`unfiltered_scan_relay`（素通し）です。`restamp_scan` は実機ドライバを使う場合の
経路で、無効時は 2D 投影から直接 `/scan_raw` に出ます。EMCL3D は 2D 投影を通らず、
仰角フィルタ後の点群を PCD と照合します。フィルタを無効にすると `/livox/lidar` を読みます。
大域自己位置推定で自由空間を絞る場合は、EMCL3D も計画用の `/map` を読みます。

### オドメトリと TF

```text
[livox_lidar_publisher] -- /livox/imu --> [imu_throttle]
    -- /livox/imu_throttled --> [prepare_mid360_imu] -- /imu/mid360 --+
                                                                      v
[raspicat_driver] -- /wheel/odom ---------------------------> [ekf_filter_node]
                                                                      |
                                      /odom <-------------------------+

TF frame chain:
map --(emcl3d: /tf)--> odom --(ekf_filter_node: /tf)--> base_footprint
base_footprint --(mid360_static_transform: /tf_static)--> livox_frame
```

EMCL3D は `/odom` や `/livox/imu` を直接読むのではなく、点群の時刻で
`odom -> base_footprint` と `base_footprint -> livox_frame` の TF を参照します。
Mid-360 の IMU を使わない場合は、機体ドライバが `/odom` と `odom -> base_footprint` を出します。
外部推定時の `vi_planner` は、推定器の姿勢トピックではなく `map -> base_footprint` の
TF を使います。TF の最新共通時刻と地図補正の更新を確認し、どちらかが 0.5 秒以上古く
なると追従を止めます。

### ゴールから駆動まで

```text
[RViz / Waypoint Manager]
    -- action: /navigate_to_pose or /follow_waypoints --> [vi_planner]
         -- /cmd_vel_nav --> [velocity_smoother] -- /cmd_vel ----------+
                                                                       v
[joy_node] -- /joy --> [joy_teleop] -- /cmd_vel_teleop ------------> [twist_mux]
                                                                       |
                                                                 /cmd_vel_mux
                                                                       |
                                                                       v
                                                               [raspicat_driver]
                                                                       |
                                                                       v
                                                                    Motors
```

`twist_mux` は自律走行と手動操作の速度指令をまとめます。手動側が指令を出している間は
そちらが優先です。公式ドライバを選んだ場合、図の `raspicat_driver` は `raspimouse` に置き換わります。

### 2D LiDAR・地図作成の場合

```text
[2D LiDAR] --> [urg_node] -- /scan_raw --> [scan_to_scan_filter_chain] --> /scan

Navigation with EMCL2:
2D localization map -- (file) --> [map_server_loc] -- /map_loc --> [emcl2]
/scan (or /scan_loc) --------------------------------------------> [emcl2]
[emcl2] -- map -> odom (TF) --> map -> base_footprint (TF) --> [vi_planner]

Mapping instead of navigation:
/scan ------------------------> [slam_toolbox] -- /map --> Occupancy grid
/tf (odom -> base_footprint) -> [slam_toolbox] -- /tf ---> map -> odom
```

2D LiDAR だけで自律移動する場合は `lidar:=2d localization:=emcl2` などを指定します。
つくば2025で EMCL2 を使う場合は、位置推定専用の `/scan_loc` を使います。
地図作成では `mapping.launch.py` を使い、自律移動の launch とは同時に起動しません。
起動方法は [自律移動](docs/usage/navigation.md) と [地図作成](docs/usage/mapping.md) を参照してください。

## ドキュメント

個々のページは下の目次から辿ってください。**ここに一覧を写さないこと**（二重に持つと片方が古くなる）。

- [セットアップ](docs/setup/README.md) — 実行環境・SDカード・LiDAR・ネットワーク
- [使い方](docs/usage/README.md) — 地図作成・自律移動・日常操作・トラブルシューティング
- [`src/daifuku_config/README.md`](src/daifuku_config/README.md) — 設定の合成規則と、各値の由来
- [`docker/README.md`](docker/README.md) — 実機用（`raspberrypi/`）と開発用（`dev/`）の2環境
- [`simulator/README.md`](simulator/README.md) — usim VTC ワールドで地図作成から自律移動まで確認するハーネス
- [`AGENTS.md`](AGENTS.md) — このリポジトリで作業するエージェント向けの指針

## ライセンス

[Apache License 2.0](LICENSE)
