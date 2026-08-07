# セットアップガイド

Raspberry Pi CatとナビゲーションPCを同じROS 2ネットワークへ接続し、機体側
（`daifuku_bringup`）と自律移動側（`daifuku_stack`）を起動できる状態にするまでの手順です。

## 1. 実行環境を選ぶ

| 環境 | 向いている用途 | 手順 |
|---|---|---|
| `docker/raspberrypi/` | Raspberry Piやサーバーでの軽量・ヘッドレス実行 | [Docker環境](docker.md) |
| Ubuntu 22.04 + ROS 2 Humble | RVizを含む通常の開発・実行 | [ネイティブ環境](native.md) |
| `docker/dev/` | Raspberry Pi Cat公式のPC環境とGUIをそろえた開発 | [GUI付き開発コンテナ](development-container.md) |

`docker/raspberrypi/`のイメージにはRVizが入っていません。RVizを使う場合はネイティブ環境または`docker/dev/`を利用してください。

Raspberry Pi 4 / 5のSDカードを一から用意する場合は、[`tools/image/`](../../tools/image/README.md)の
`create_image.py`でOSイメージの書き込みと初期設定をまとめて行えます。Docker、rtmouseカーネル
モジュール、DDS向けのカーネルパラメータなど、コンテナの外に置くしかないものは、ここで
まとめて設定します。

機種ごとの手順と注意点は次にまとめています。本体ドライバは自前実装
（[`src/raspicat_driver`](../../src/raspicat_driver/README.md)、`driver:=original`）と
公式実装（rtmouse + `raspimouse`、`driver:=raspimouse`）から選べます。**標準は自前実装**で、
`.env`の雛形である`.env.example`の`COMPOSE_FILE`もそちらを指しています。Pi 5ではrtmouseが
動かないので選択肢がありません。

- [Raspberry Pi 5で動かす](raspberry-pi-5.md)（自前実装のみ）
- [Raspberry Pi 4で動かす](raspberry-pi-4.md)（どちらも選べる。公式実装にするなら`.env`を書き換える）

## 2. 機体側を準備する

標準の自前実装（`driver:=original`）ではモーター経路を
[`src/raspicat_driver`](../../src/raspicat_driver/README.md)が持つので、機体側に別途
用意するものはありません。公式実装（`driver:=raspimouse`）や自作の機体で動かす場合は、
モータードライバと車輪オドメトリがこのリポジトリに含まれないため、次のインターフェースを
Raspberry Pi Cat側で用意してください。

| インターフェース | 型 / TF | 用途 |
|---|---|---|
| `/cmd_vel` | `geometry_msgs/msg/Twist` | 速度指令の受信 |
| `/odom` | `nav_msgs/msg/Odometry` | 2D LiDAR構成の車輪オドメトリ |
| `/wheel/odom` | `nav_msgs/msg/Odometry` | Mid-360 + IMU構成のEKF入力 |
| `/scan_raw` | `sensor_msgs/msg/LaserScan` | 2D LiDARの未処理スキャン |
| `odom -> base_footprint` | TF | 機体の姿勢。配信元は構成によって異なる |
| `base_footprint -> センサーフレーム` | TF | センサー搭載位置 |

詳細は[LiDARとオドメトリ](lidar.md)を参照してください。

## 3. ネットワークを合わせる

機体側とPC（またはコンテナ）で、次をそろえます。

- 相互にIP到達できるネットワーク
- 同じ`ROS_DOMAIN_ID`
- `ROS_LOCALHOST_ONLY=0`
- DDSのUDPとマルチキャストを通すファイアウォール設定

Docker Desktopや専用Ethernet接続の注意点は[ROS 2ネットワーク](network.md)にまとめています。

## 4. セットアップを確認する

機体側ドライバを起動した状態で確認します。

```bash
ros2 topic list
ros2 topic hz /scan_raw
ros2 topic hz /odom
ros2 run tf2_ros tf2_echo odom base_footprint
```

Mid-360 + IMU構成では`/wheel/odom`と`/odom`の両方が`raspicat`サービス側から出ます
（ドライバが前者、同じlaunchが立てるEKFが後者）。`/odom`のpublisherは1つが正です。

準備ができたら[利用ガイド](../usage/README.md)へ進んでください。
