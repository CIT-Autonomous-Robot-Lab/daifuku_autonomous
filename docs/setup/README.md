# セットアップガイド

このガイドでは、Raspberry Pi CatとナビゲーションPCを同じROS 2ネットワークへ接続し、`autonomous_nav`を起動できる状態にします。

## 1. 実行環境を選ぶ

| 環境 | 向いている用途 | 手順 |
|---|---|---|
| `docker/raspberrypi/` | Raspberry Piやサーバーでの軽量・ヘッドレス実行 | [Docker環境](docker.md) |
| Ubuntu 22.04 + ROS 2 Humble | RVizを含む通常の開発・実行 | [ネイティブ環境](native.md) |
| `docker/dev/` | Raspberry Pi Cat公式PC環境とGUIをまとめた開発 | [GUI付き開発コンテナ](development-container.md) |

`docker/raspberrypi/`のイメージにはRVizを含みません。RVizを使う場合はネイティブ環境または`docker/dev/`を利用してください。

## 2. 機体側を準備する

このリポジトリはモータードライバと車輪オドメトリを提供しません。Raspberry Pi Cat側で次のインターフェースを用意します。

| インターフェース | 型 / TF | 用途 |
|---|---|---|
| `/cmd_vel` | `geometry_msgs/msg/Twist` | 速度指令の受信 |
| `/odom` | `nav_msgs/msg/Odometry` | 2D LiDAR構成の車輪オドメトリ |
| `/wheel/odom` | `nav_msgs/msg/Odometry` | Mid-360 + IMU構成のEKF入力 |
| `/scan_raw` | `sensor_msgs/msg/LaserScan` | 2D LiDARの未処理スキャン |
| `odom -> base_footprint` | TF | ロボット姿勢。配信元は構成により異なる |
| `base_footprint -> センサーフレーム` | TF | センサー搭載位置 |

詳細は[LiDARとオドメトリ](lidar.md)を参照してください。

## 3. ネットワークを合わせる

機体とPCまたはコンテナで、次を一致させます。

- 相互にIP到達可能なネットワーク
- 同じ`ROS_DOMAIN_ID`
- `ROS_LOCALHOST_ONLY=0`
- DDSのUDPおよびマルチキャストを許可するファイアウォール設定

Docker Desktopや専用Ethernet接続の注意点は[ROS 2ネットワーク](network.md)にまとめています。

## 4. セットアップを確認する

機体側ドライバを起動した状態で確認します。

```bash
ros2 topic list
ros2 topic hz /scan_raw
ros2 topic hz /odom
ros2 run tf2_ros tf2_echo odom base_footprint
```

Mid-360 + IMU構成では`/wheel/odom`を確認し、最終的な`/odom`はナビゲーション側のEKF起動後に確認します。

準備ができたら[利用ガイド](../usage/README.md)へ進んでください。
