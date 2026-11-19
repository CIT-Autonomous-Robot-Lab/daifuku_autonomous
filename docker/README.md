# Docker環境

用途の違う2つのDocker環境と、その共通部分を置いています。

| ディレクトリ | 用途 | 詳細 |
|---|---|---|
| [`raspberrypi/`](raspberrypi) | 実機Raspberry Pi（`arm64`）とPC（`amd64`）で動く実機向けヘッドレス環境。RVizは含まない | [README](raspberrypi/README.md) / [Docker環境](../docs/setup/docker.md) |
| [`dev/`](dev) | PC側の開発環境。ROS 2 HumbleとGUI（RViz・rqt） | [README](dev/README.md) / [GUI付き開発コンテナ](../docs/setup/development-container.md) |
| `common/` | 両方が使う共通部分（後述） | — |

2つは排他ではありません。実機で`raspberrypi/`を動かし、同じLAN上のPCで`dev/`から
RVizで見る、というのが通常の使い方です。`ROS_DOMAIN_ID`の既定はどちらも`90`です。

依存はルートの`shell.nix`でホストと共有します。Docker内でNix環境を実現し、
最終イメージにはその依存だけを取り出します。ホストにNixを入れる必要はありません。
実機用は描画を含まないプロファイル、開発用はRViz・rqtを加えたプロファイルです。
依存を追加するときは`shell.nix`を編集し、イメージを作り直します。

ワークスペースをコンテナ内でビルドする入口は維持しています。
RustはLinux用の標準ライブラリを使い、Wasm/BPF用とコンパイラ内部の開発用情報を外します。
初回はRustコンパイラのビルドも伴います。LLVM/Clangは同じバージョンの共有ライブラリと
組み込みヘッダーを使い、ワークスペースで使わない静的アーカイブを取り込みません。

取得・コンパイル済みのNixストアとDBは、ベースイメージとCPUアーキテクチャごとの
ビルドキャッシュに一緒に保持します。同時更新はロックで防ぎます。
このキャッシュは最終イメージへ入れず、実現した依存だけをコピーします。
同じ内容のファイルはhardlinkで共有し、コピー先でも出力間の共有を保ちます。
取得通信はHTTP/1.1に固定します。未取得の依存を一括取得するときに起きる
接続エラーは、Dockerの既定ネットワークでは解消を確認できていません。

## common/

| ファイル | 使う側 | 内容 |
|---|---|---|
| `entrypoint.sh` | 両イメージ | `/ros_entrypoint.sh`として入る。ROS 2と、存在するオーバーレイを読み、**前回の`docker exec`の残骸を止めてから**コマンドを実行する |
| `build-nix-environment.sh` | 両イメージのビルド | 固定したNix依存を取り出し、ビルド用のダウンロード・中間生成物を最終イメージから分離する |
| `lib/compose.sh` | ホスト側のbashスクリプト | Docker CLIへの到達手段の決定とCompose呼び出し。dot-sourceして使う |

ワークスペースは`install/local_setup.bash`でNix環境へ重ねます。
過去の`setup.bash`が記録した古いROS環境へ戻らないようにするためです。
初回ビルド前は存在しないオーバーレイを飛ばし、ビルドコマンドでは旧installを読みません。

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

どのComposeファイルも`name:`を明示し、ディレクトリを動かしても同じ
プロジェクトとして扱います。実機用・開発用とも、リポジトリ全体を1つの
bind mount で渡します。名前付きボリュームは使わず、生成物はホストの
リポジトリ直下の `build/`・`install/`・`log/` に保存します。

| Composeファイル | プロジェクト名 |
|---|---|
| `raspberrypi/compose.original.yaml` | `daifuku-autonomous` |
| `raspberrypi/compose.rt.yaml` | `daifuku-autonomous` |
| `dev/compose.yaml` | `daifuku-raspicat-dev` |

`raspberrypi/`の2つは本体ドライバ違いの断片で、共通部分（`compose.common.yaml`）
と**2つ重ねて**使います（`COMPOSE_FILE`にコロン区切りで並べる。commonが先）。
ドライバを替えても同じプロジェクトとホストの成果物を使います。

かつては入口側が`compose.common.yaml`を`include:`していましたが、Compose 2.40は
includeした側のサービスの上書きを`services.raspicat conflicts with imported
resource`で拒みます（2026-09-02にPi 5 + compose 2.40.3で判明）。**戻さないこと。**
