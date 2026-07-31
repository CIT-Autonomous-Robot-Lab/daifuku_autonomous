# Docker環境

用途の違う2つのDocker環境と、その共通部分を置いています。

| ディレクトリ | 用途 | 詳細 |
|---|---|---|
| [`raspberrypi/`](raspberrypi) | 実機Raspberry Pi（`arm64`）とPC（`amd64`）で動く実行専用のヘッドレス環境。RVizは含まない | [README](raspberrypi/README.md) / [Docker環境](../docs/setup/docker.md) |
| [`dev/`](dev) | PC側の開発環境。ROS 2 Desktop Full、Raspberry Pi Cat公式のPC用ワークスペース、GUI（RViz） | [README](dev/README.md) / [GUI付き開発コンテナ](../docs/setup/development-container.md) |
| `common/` | 両方が使う共通部分（後述） | — |

2つは排他ではありません。実機で`raspberrypi/`を動かし、同じLAN上のPCで`dev/`から
RVizで見る、というのが通常の使い方です。`ROS_DOMAIN_ID`の既定はどちらも`90`です。

## common/

| ファイル | 使う側 | 内容 |
|---|---|---|
| `entrypoint.sh` | 両イメージ | `/ros_entrypoint.sh`として入る。ROS 2と、存在するオーバーレイを読んでからコマンドを実行する |
| `lib/compose.sh` | ホスト側のbashスクリプト | Docker CLIへの到達手段の決定とCompose呼び出し。dot-sourceして使う |

オーバーレイの集合はイメージごとに違いますが互いに素なので、`entrypoint.sh`は
「あればsourceする」形にして1つにまとめてあります。存在しないものは飛ばすため、
どちらのイメージでも読み込む順序と結果は分けていた頃と同じです。

`lib/compose.sh`への相対パスは呼び出し元の深さによって変わるので、各スクリプトが
`BASH_SOURCE`から組み立てます。

Windows側（`dev/tools/windows/`）はPodman固有の処理が主で`raspberrypi/`側に対応物が
ないため、共通化せず`dev/tools/windows/common.ps1`にまとめています。

## Composeプロジェクト名

`compose.yaml`は両方とも`name:`を明示しています。既定ではCompose
ファイルのあるディレクトリ名がプロジェクト名になり、ネットワーク名や
名前付きボリューム名がそこから作られるため、ディレクトリを動かすと
`dev/`のcolconキャッシュ（`autonomous-build`/`-install`/`-log`）が
別ボリュームに化けます。

| Composeファイル | プロジェクト名 |
|---|---|
| `raspberrypi/compose.yaml` | `daifuku-autonomous` |
| `dev/compose.yaml` | `daifuku-raspicat-dev` |
