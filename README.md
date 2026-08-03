# daifuku_autonomous

[Raspberry Pi Cat](https://rt-net.jp/products/raspicat)をROS 2 Navigation2で自律移動させるためのワークスペースです。

- 2D LiDARまたはLivox Mid-360に対応
- SLAM Toolboxによる地図作成
- EMCL2またはAMCLによる自己位置推定
- 価値反復プランナ（既定）またはNavFnによる経路計画
- キーボードとジョイスティックによる遠隔操作
- Docker（ヘッドレス）またはUbuntu 22.04 / ROS 2 Humbleで実行可能

> Raspberry Pi 4ではモータードライバと車輪オドメトリは含まれません。`/cmd_vel`、`/odom`、TF、センサーデータは機体側（rtmouse + raspimouse）で用意してください。Raspberry Pi 5はrtmouseが動かないため、モーター経路だけこのリポジトリが持ちます（[Raspberry Pi 5で動かす](docs/setup/raspberry-pi-5.md)）。

## はじめる

1. [セットアップガイド](docs/setup/README.md)で実行環境とセンサーを準備する
2. [利用ガイド](docs/usage/README.md)で地図作成または自律移動を開始する

## ドキュメント

- [セットアップ](docs/setup/README.md)
  - [Docker環境](docs/setup/docker.md)
  - [ネイティブ環境](docs/setup/native.md)
  - [ROS 2ネットワーク](docs/setup/network.md)
  - [LiDARとオドメトリ](docs/setup/lidar.md)
  - [GUI付き開発コンテナ](docs/setup/development-container.md)
- [使い方](docs/usage/README.md)
  - [地図作成](docs/usage/mapping.md)
  - [自律移動](docs/usage/navigation.md)
  - [日常操作と確認](docs/usage/operations.md)
  - [設定リファレンス](docs/usage/configuration.md)
  - [構成とパッケージ](docs/usage/architecture.md)
  - [トラブルシューティング](docs/usage/troubleshooting.md)

`docker/`以下には、実機用（`raspberrypi/`）と開発用（`dev/`）の2つのDocker環境があります。
全体像は[`docker/README.md`](docker/README.md)、各環境のディレクトリ構成は
[`docker/raspberrypi/README.md`](docker/raspberrypi/README.md)と
[`docker/dev/README.md`](docker/dev/README.md)にまとめています。

実機に載せる前にRaspberry Pi 4相当の速度で試すハーネスが[`simulator/`](simulator/README.md)に
あります。Isaac Sim版と疑似ロボット版の2つです。実機で観測した事象の実測記録は
[`simulator/docs/pi4_sim.md`](simulator/docs/pi4_sim.md)にまとまっているので、どちらを使う
場合でも先に読んでください。

## ライセンス

[Apache License 2.0](LICENSE)
