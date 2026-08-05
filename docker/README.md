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
| `entrypoint.sh` | 両イメージ | `/ros_entrypoint.sh`として入る。ROS 2と、存在するオーバーレイを読み、**前回の`docker exec`の残骸を止めてから**コマンドを実行する |
| `lib/compose.sh` | ホスト側のbashスクリプト | Docker CLIへの到達手段の決定とCompose呼び出し。dot-sourceして使う |

オーバーレイの集合はイメージごとに違いますが互いに素なので、`entrypoint.sh`は
「あればsourceする」形にして1つにまとめてあります。存在しないものは飛ばすため、
どちらのイメージでも読み込む順序と結果は分けていた頃と同じです。

`lib/compose.sh`への相対パスは呼び出し元の深さによって変わるので、各スクリプトが
`BASH_SOURCE`から組み立てます。

### 前回の残骸を止める

`docker compose exec`で立てたlaunchは、**クライアントがSIGINTを届けないまま死ぬと
走り続けます**。tmuxのペインを閉じた、sshが切れた、端末ごと落とした場合で、
コンテナのPID 1に引き取られたまま残ります。ROSは同じ名前のノードを何個でも
立てられるので、気づかないまま`navigation`や`mapping`が二重三重に走り、
**エラーも警告も出ないままCPUだけが減ります**（2026-08-05の実機で3組が重なり
load 12。3つの`elevation_filter`が同じ`/livox/lidar`を読んで1コア半を食っていた）。

そこで`entrypoint.sh`は、コマンドを実行する前に残骸を止めます。見分けは`PPid`で
つきます — `exec`の木はそれぞれ独立したセッションで、リーダの`PPid`は0
（親がPID名前空間の外）。`exec`が死ぬと1へ付け替わるので、**`PPid`が1のものが
残骸**です。コンテナ自身のcommandはセッション1なので外れ、`ros2`デーモンだけは
自分で`setsid`するため名指しで除いてあります。止めるのはプロセスグループ単位
（`kill -- -<pgid>`）なので、リーダが先に死んでいても木ごと落ちます。

止めたものは必ずログに出ます。`docker exec -d`で意図的に離した木を残したい場合は
`DAIFUKU_NO_REAP=1`を渡してください（`-d`のプロセスは`PPid`が0のままなので、
通常はこの指定は要りません）。

あわせて`ros2`と`raspicat`は`init: true`（PID 1をtiniにする）です。`sleep infinity`も
`ros2 launch`も引き取った孤児を`wait`しないので、これが無いと**ゾンビが永久に
溜まります**。tiniは回収するだけで、生きたまま残る木のほうは上の仕組みが止めます。

Windows側（`dev/tools/windows/`）はPodman固有の処理が主で`raspberrypi/`側に対応物が
ないため、共通化せず`dev/tools/windows/common.ps1`にまとめています。

## Composeプロジェクト名

どのComposeファイルも`name:`を明示しています。既定ではCompose
ファイルのあるディレクトリ名がプロジェクト名になり、ネットワーク名や
名前付きボリューム名がそこから作られるため、ディレクトリを動かすと
`dev/`のcolconキャッシュ（`autonomous-build`/`-install`/`-log`）が
別ボリュームに化けます。

| Composeファイル | プロジェクト名 |
|---|---|
| `raspberrypi/compose.original.yaml` | `daifuku-autonomous` |
| `raspberrypi/compose.rt.yaml` | `daifuku-autonomous` |
| `dev/compose.yaml` | `daifuku-raspicat-dev` |

`raspberrypi/`の2つは本体ドライバ違いの入口で、どちらも共通部分
（`compose.common.yaml`）を`include:`します。**プロジェクト名をわざと同じに
してあります**。違えると、ドライバを替えた瞬間にビルドキャッシュのボリュームが
別物になり、1〜2時間かけて建て直すことになります。`include:`されたファイルの
`name:`は無視されるので、入口2つの側に書いてあります。
