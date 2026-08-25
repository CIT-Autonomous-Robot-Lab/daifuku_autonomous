# daifuku_autonomous

[![CI](https://github.com/CIT-Autonomous-Robot-Lab/daifuku_autonomous/actions/workflows/ci.yml/badge.svg)](https://github.com/CIT-Autonomous-Robot-Lab/daifuku_autonomous/actions/workflows/ci.yml)

[Raspberry Pi Cat](https://rt-net.jp/products/raspicat)をROS 2 Navigation2で自律移動させるためのワークスペースです。

- 2D LiDARまたはLivox Mid-360に対応
- SLAM Toolboxによる地図作成
- EMCL2またはAMCLによる自己位置推定
- 価値反復プランナ（既定）またはNavFnによる経路計画
- キーボードとジョイスティックによる遠隔操作
- Docker（ヘッドレス）またはUbuntu 22.04 / ROS 2 Humbleで実行可能

本体ドライバは自前実装（`driver:=original`、[`src/raspicat_driver`](src/raspicat_driver/README.md)）と公式実装（rtmouse + raspimouse）から選べます。Docker運用の標準は前者で、公式実装はrtmouseが動くRaspberry Pi 4に限られます（[Pi 4](docs/setup/raspberry-pi-4.md) / [Pi 5](docs/setup/raspberry-pi-5.md)）。

## はじめる

1. [セットアップガイド](docs/setup/README.md)で実行環境とセンサーを準備する
2. [利用ガイド](docs/usage/README.md)で地図作成または自律移動を開始する

## ドキュメント

個々のページは下の目次から辿ってください。**ここに一覧を写さないこと**（二重に持つと片方が古くなる）。

- [セットアップ](docs/setup/README.md) — 実行環境・SDカード・LiDAR・ネットワーク
- [使い方](docs/usage/README.md) — 地図作成・自律移動・日常操作・トラブルシューティング
- [`src/daifuku_config/README.md`](src/daifuku_config/README.md) — 設定の合成規則と、各値の由来
- [`docker/README.md`](docker/README.md) — 実機用（`raspberrypi/`）と開発用（`dev/`）の2環境
- [`simulator/README.md`](simulator/README.md) — 実機の前にPi 4相当の速度で試すハーネス。実機で観測した事象の実測記録は[`simulator/docs/pi4_sim.md`](simulator/docs/pi4_sim.md)
- [`AGENTS.md`](AGENTS.md) — このリポジトリで作業するエージェント向けの指針

## ライセンス

[Apache License 2.0](LICENSE)
