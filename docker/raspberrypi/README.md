# 軽量ヘッドレス実行環境

`docker/raspberrypi/`はRaspberry Pi本体（`arm64`）とPC（`amd64`）で共通に使える、実行専用の
Docker環境です。ROS 2 Humble、Nav2、SLAM Toolbox、EMCL2、価値反復プランナ、
Livox関連ノード、teleopノードを含みます。RVizは含みません。

セットアップ手順の全体は[Docker環境](../../docs/setup/docker.md)を、日常操作は
[日常操作と確認](../../docs/usage/operations.md)を参照してください。ここでは
`docker/raspberrypi/`ディレクトリ自体の構成をまとめます。

## ファイル構成

| ファイル | 用途 |
|---|---|
| `compose.yaml` | サービス`ros2`の定義。`network_mode: host`、`ipc: host`で起動する |
| `Dockerfile` | マルチステージビルド。外部パッケージはビルド中に`vcs import`で取得する |
| `fastdds_udp_whitelist.xml` | Fast DDSのトランスポート設定（後述） |
| `tools/control.sh` | モーター電源、遠隔操作、状態確認をまとめた操作スクリプト |
| `tools/shell.sh` | 起動済みコンテナで対話シェルを開く |

entrypointとホスト側スクリプトの共通部分は[`docker/common/`](../common)にあります。
イメージには`docker/common/entrypoint.sh`が`/ros_entrypoint.sh`として入り、
`tools/`配下は`docker/common/lib/compose.sh`を読み込みます。

## 起動

リポジトリルートから実行します。

```bash
docker compose -f docker/raspberrypi/compose.yaml build
docker compose -f docker/raspberrypi/compose.yaml up -d
```

Raspberry Pi 4などメモリの少ない環境では、ビルド並列数を下げてください。

```bash
BUILD_JOBS=1 docker compose -f docker/raspberrypi/compose.yaml build
```

`emcl2_ros2`、`livox_ros_driver2`、`value_iteration3`は`.dockerignore`で
ビルドコンテキストから除外しています。ホスト側の作業用チェックアウトの状態に
関わらず、イメージには`autonomous_bot.repos`で固定したリビジョンが入ります。

## tools/control.sh

Raspberry Pi Catの操作をまとめたスクリプトです。コンテナが停止していれば自動的に
起動します。

```bash
bash docker/raspberrypi/tools/control.sh help
```

| サブコマンド | 動作 |
|---|---|
| `motor on` | モーター電源を入れる |
| `motor off` | `/cmd_vel`へ停止指令を送ってからモーター電源を切る |
| `stop` | `/cmd_vel`へ停止指令を1回送る |
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
| `CMD_VEL_TOPIC` | `/cmd_vel` | 速度指令トピック |
| `ROS_TIMEOUT` | `10` | ROS操作のタイムアウト秒数 |
| `TELEOP_LINEAR_SPEED` | `0.2` | キーボード操作の並進速度 m/s |
| `TELEOP_ANGULAR_SPEED` | `1.0` | キーボード操作の旋回速度 rad/s |
| `JOYSTICK_ID` | `0` | joyデバイスID |
| `JOYSTICK_CONFIG` | `xbox` | `teleop_twist_joy`の設定名 |

ジョイスティックを使うため、`compose.yaml`は`/dev/input`を読み取り専用でマウントし、
`device_cgroup_rules`でキャラクタデバイス13番を許可しています。コンテナ作成後に
接続したコントローラも利用できます。

## tools/shell.sh

コンテナ内で対話シェルを開きます。ROS 2とワークスペースは読み込み済みです。

```bash
bash docker/raspberrypi/tools/shell.sh
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
- `compose.yaml`の`user: "1000:1000"`は、ホストのROSプロセスがuid 1000（`ubuntu`）
  で動くことを前提にしています。Fast DDSはSHMセグメントを0644で作るため、root権限の
  コンテナと非rootのホストが混在すると互いのポートを開けず、通信が静かに止まります。
  ホスト側のユーザーが異なる場合はこの値を合わせてください。
