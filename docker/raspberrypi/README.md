# ヘッドレス実行環境

`docker/raspberrypi/`はRaspberry Pi本体（`arm64`）とPC（`amd64`）で共通に使える
Docker環境です。ROS 2 Humble、Nav2、SLAM Toolbox、EMCL2、価値反復プランナ、
Livox関連ノード、teleopノードを含みます。RVizは含みません。

イメージが持つのはaptの依存とツールチェーン（ROS、Livox SDK、Rust、ros2_rust）だけです。
ワークスペースのソースはイメージに焼かず、composeが`src/`をマウントし、
`up`のたびにコンテナの中で`colcon build`します。差分ビルドなので、変えていない
パッケージは建て直されません。

| 変更した対象 | 必要な操作 |
|---|---|
| launch / config / YAML | なし（`--symlink-install`で`install/`が`src/`を指しているため、ノードの再起動だけ） |
| C++ / Rustのコード | `docker compose up`（変わったパッケージだけ再ビルド） |
| aptパッケージ（`Dockerfile`、`package.xml`の依存）、`entrypoint.sh`と`build-workspace.sh` | `docker compose build`からやり直す |

セットアップ手順の全体は[Docker環境](../../docs/setup/docker.md)を、日常操作は
[日常操作と確認](../../docs/usage/operations.md)を参照してください。ここでは
`docker/raspberrypi/`ディレクトリ自体の構成をまとめます。

## ファイル構成

| ファイル | 用途 |
|---|---|
| `compose.common.yaml` | 本体ドライバに依存しない部分。サービス`workspace-build` / `ros2` / `raspicat`の共通定義。`network_mode: host`、`ipc: host`で起動する。**直接`-f`で渡さない** |
| `compose.original.yaml` | 入口。自前の本体ドライバ（`driver:=original`、[`src/raspicat_driver`](../../src/raspicat_driver/README.md)）。Pi 5では必須。**既定** |
| `compose.rt.yaml` | 入口。公式実装の本体ドライバ（`driver:=raspimouse` + rtmouse）。**Pi 4専用** |
| `Dockerfile` | apt依存とツールチェーンだけを持つイメージ。ワークスペースはビルドしない |
| `fastdds_udp_whitelist.xml` | Fast DDSのトランスポート設定（後述） |
| `scripts/build-workspace.sh` | `up`のときにコンテナ内で走る`colcon build`（`/usr/local/bin/build-workspace`） |

`scripts/`はイメージへ入れてコンテナの中で走らせるものです。ホスト側から叩く
`tools/control.sh`と`tools/shell.sh`は**リポジトリルートの[`tools/`](../../tools)**に
あります（`docker compose`は`.env`をカレントディレクトリから読むので、どのみち
ルートから叩くもの）。

entrypointとホスト側スクリプトの共通部分は[`docker/common/`](../common)にあります。
イメージには`docker/common/entrypoint.sh`が`/ros_entrypoint.sh`として入り、
ルートの`tools/`配下は`docker/common/lib/compose.sh`を読み込みます。

## 起動

**リポジトリルートから実行します。** 入口の compose ファイルはリポジトリルートの
`.env`で選びます（`.env.example`をコピーして作る。`.env`は`.gitignore`）。

```bash
cp .env.example .env    # 初回だけ。provision.sh は機種を見て自動で作る
docker compose build
docker compose up -d
```

```bash
# .env（既定）
COMPOSE_FILE=docker/raspberrypi/compose.original.yaml   # 自前実装。Pi 5では必須
#COMPOSE_FILE=docker/raspberrypi/compose.rt.yaml        # 公式実装。Pi 4専用
```

**Composeが`COMPOSE_FILE`を読むのはカレントディレクトリの`.env`なので、リポジトリ
ルート以外から叩くと効きません**（`no configuration file provided`で止まります）。
`-f`を明示したときはそちらが優先されるので、開発用の`docker/dev/`は今までどおりです。

**`.env`は2つ読まれます。** リポジトリルートのものと、この`docker/raspberrypi/.env`
（`provision.sh`が`ROS_DOMAIN_ID`と`BUILD_JOBS`を書いて生成する）の両方で、値は
合成されます。**同じキーが両方にあるとこちらが勝ちます**（Composeのproject
directoryは`COMPOSE_FILE`の1つめがあるディレクトリで、そちらが後勝ちになるため）。
`COMPOSE_FILE`だけはリポジトリルート側でしか効きません。実機で「ルートの`.env`を
直したのに効かない」ときはここを見てください。
機種ごとの前提は[Raspberry Pi 4](../../docs/setup/raspberry-pi-4.md)と
[Raspberry Pi 5](../../docs/setup/raspberry-pi-5.md)。

入口を切り替えてもプロジェクト名（`daifuku-autonomous`）は同じなので、名前付き
ボリュームのビルドキャッシュはそのまま使い回せます。`raspicat`コンテナだけが
作り直されます。

`up`はまず`workspace-build`サービスを走らせ、その正常終了を待ってから`ros2`と
`raspicat`を起動します。初回はワークスペース全体を建てるので時間がかかります
（Raspberry Pi 4で1〜2時間、大半は価値反復プランナのRustのreleaseビルド）。
2回目以降は変更のあったパッケージだけです。

`ros2`と`raspicat`は`restart: unless-stopped`なので、一度`up`しておけば
Raspberry Piを再起動しても自動で上がります（Dockerデーモンが`enabled`であること。
`systemctl is-enabled docker`）。**このとき`workspace-build`は走りません。**
名前付きボリュームの`install/`がそのまま見えるので前回の成果物で起動します。
したがってC++やRustを直した分は再起動しても反映されません。反映するには
`docker compose up -d`を人手で通してください（launch / config / YAMLは
`--symlink-install`なのでノードの再起動だけで足ります）。

`docker compose stop`で止めたものは再起動後も止まったままです（`unless-stopped`）。
`down`したものはコンテナごと消えるので、次は`up`が要ります。

`BUILD_JOBS`が効くのは`up`のときの`colcon build`だけです（既定は4＝Pi 4の
全コア）。メモリが足りずにOOMで落ちるときだけ下げてください。

```bash
BUILD_JOBS=1 docker compose up -d
```

**イメージのビルドには効きません**（`build.args`には渡していない）。渡すと
下げた瞬間にイメージのビルドキャッシュが全部外れて1〜2時間の焼き直しになる
ためです。イメージ側を絞るときは明示します。

```bash
docker compose build --build-arg BUILD_JOBS=1
```

外部パッケージ（`emcl2_ros2`、`livox_ros_driver2`、`value_iteration3`など）は
`.dockerignore`でビルドコンテキストから除外してあり、イメージのビルド中に
`vcs import`したものはrosdepにapt依存を解決させたあと捨てられます。
実行時に使うのはホスト側の`src/`で、無ければ`build-workspace.sh`が
`daifuku_autonomous.repos`にしたがって`vcs import`します。したがって特定の
リビジョンに固定したい場合は、ホスト側の`src/`のチェックアウトを合わせてください。

**`vcs import`は`--skip-existing`付きで、既にあるリポジトリは更新しません。**
一度cloneしたあとに`daifuku_autonomous.repos`のリビジョンを変えても、古いままの
チェックアウトがそのままビルドされます（起動時に「そんなパラメータは知らない」
という顔をして出るので、設定の問題と取り違えやすい）。更新するときは各リポジトリで
明示的に合わせてください。

```bash
git -C src/value_iteration3 fetch origin && git -C src/value_iteration3 merge --ff-only origin/main
```

**そのうえで、`src/`が揃っているときは`vcs import`をそもそも呼びません。**
`--skip-existing`はチェックアウトを動かさないだけで、URLの一致する既存リポジトリにも
`git fetch`まで走ります。つまり呼ぶ限りネットワークが要り、Wi-Fiのないところでは
`Could not resolve host`で`docker compose up`ごと止まります（`set -e`）。
`--skip-existing`が付いている以上fetchしても作業ツリーは変わらないので、呼ばないことと
結果は同じです。したがって一度取り込んであればオフラインでも`up`できます。
足りないものがあるときだけ`vcs import`し、それでも埋まらなければ足りない
リポジトリ名を並べて止まります（黙って進めるとcolconが「そんなパッケージは無い」と
いう無関係な顔で落ちるため）。何も取り込んでいない環境では、最初の1回だけ
ネットワークが要ります。

ビルド成果物は名前付きボリューム`autonomous-build` / `autonomous-install` /
`autonomous-log`に入ります。作り直したいときは次のようにします。

```bash
docker compose down -v
```

## tools/control.sh

Raspberry Pi Catの操作をまとめたスクリプトです。コンテナが停止していれば自動的に
起動します。

```bash
bash tools/control.sh help
```

| サブコマンド | 動作 |
|---|---|
| `motor on` | モーター電源を入れる |
| `motor off` | `CMD_VEL_TOPIC`へ停止指令を送ってからモーター電源を切る |
| `stop` | `CMD_VEL_TOPIC`へ停止指令を1回送る |
| `teleop keyboard` | キーボードで操作する（Ctrl-Cで終了） |
| `teleop joystick` | ジョイスティックで操作する（Ctrl-Cで終了） |
| `status` | コンテナ、ROSノード、モーターサービスを確認する |
| `nodes` / `topics` / `services` | それぞれの一覧を表示する |
| `ros ARGS...` | 任意の`ros2`コマンドを実行する |
| `logs [ARGS...]` | コンテナのログを表示する（例: `logs -f`） |

動作は環境変数で変更できます。

| 環境変数 | 既定値 | 用途 |
|---|---|---|
| `CONTROL_SERVICE` | `ros2` | Composeサービス名 |
| `MOTOR_SERVICE` | `/motor_power` | モーター電源サービス |
| `CMD_VEL_TOPIC` | `/cmd_vel_teleop` | 速度指令トピック。仲裁（`twist_mux`）の手動側の入口で、**優先度は自律側のほうが上**（自律走行中は`teleop`も`stop`も効かない）。`twist_mux:=false`で起動したなら`/cmd_vel` |
| `ROS_TIMEOUT` | `10` | ROS操作のタイムアウト秒数 |
| `TELEOP_LINEAR_SPEED` | `0.2` | キーボード操作の並進速度 m/s |
| `TELEOP_ANGULAR_SPEED` | `1.0` | キーボード操作の旋回速度 rad/s |
| `JOYSTICK_ID` | `0` | joyデバイスID |
| `JOYSTICK_CONFIG` | `xbox` | `teleop_twist_joy`の設定名 |

ジョイスティックを使うため、`compose.common.yaml`は`/dev/input`を読み取り専用でマウントし、
`device_cgroup_rules`でキャラクタデバイス13番を許可しています。コンテナ作成後に
接続したコントローラも利用できます。

## tools/shell.sh

コンテナ内で対話シェルを開きます。ROS 2とワークスペースは読み込み済みです。

```bash
bash tools/shell.sh
```

## Fast DDSの設定

`fastdds_udp_whitelist.xml`は`/etc/fastdds/udp_whitelist.xml`へマウントされ、
`FASTRTPS_DEFAULT_PROFILES_FILE`から読み込まれます。狙いは2点です。

1. UDPの通信インターフェースをループバックとロボットLAN（`192.168.1.50`）に限定する。
   制限しない場合、参加者はwlan0側のロケータも広告し、相手から到達できないロケータと
   UDPバッファの逼迫でディスカバリが不安定になります。
2. 同一ホスト内の通信に共有メモリ（SHM）を使う。約20個の参加者をUDPのみで動かすと、
   購読者ごとの`sendmsg`でカーネルが飽和し、TFのタイムスタンプが20秒以上遅れて
   ナビゲーションが中断しました。

**この設定はPi本体側でも同じファイルを指す必要があります。** ホストとコンテナで
プロファイルが食い違うと、片側だけがSHMを使う状態になり通信が成立しません。Pi側の
`~/.bashrc`へ次を追記してください。

```bash
export FASTRTPS_DEFAULT_PROFILES_FILE=/path/to/daifuku_autonomous/docker/raspberrypi/fastdds_udp_whitelist.xml
```

あわせて次の2点に注意してください。

- whitelistの`192.168.1.50`はPiの固定IPをそのまま書いています。ロボットLANの
  アドレスが異なる場合はXMLを書き換えてください。
- `compose.common.yaml`の`user: "1000:1000"`は、ホストのROSプロセスがuid 1000（`ubuntu`）
  で動くことを前提にしています。Fast DDSはSHMセグメントを0644で作るため、root権限の
  コンテナと非rootのホストが混在すると互いのポートを開けず、通信が静かに止まります。
  ホスト側のユーザーが異なる場合はこの値を合わせてください。
