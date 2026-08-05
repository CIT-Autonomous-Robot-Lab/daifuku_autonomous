# Docker環境

`docker/raspberrypi/`は、Raspberry Piを含む`arm64`環境と`amd64`環境向けの軽量な実行環境です。ROS 2 Humble、Nav2、SLAM Toolbox、EMCL2、価値反復プランナ、Livox関連ノード、速度指令の仲裁（`twist_mux`）、ゲームパッド（`joy`）、teleopノード（`teleop_twist_keyboard`、`teleop_twist_joy`）を含みます。イメージはヘッドレスで、RVizは含みません。

ディレクトリ内の各ファイルの役割は[`docker/raspberrypi/README.md`](../../docker/raspberrypi/README.md)にまとめています。

## 必要なもの

- Docker EngineまたはDocker Desktop
- Docker Compose v2（`docker compose`）
- 機体とホスト間のROS 2通信が可能なネットワーク

Docker Desktopでは[ネットワーク設定](network.md#docker-desktop)も先に確認してください。

## Pi本体側で先に設定すること

Pi本体でもネイティブのROS 2ノード（モータードライバなど）を動かす構成では、
コンテナを起動する前に、ホスト側のFast DDSを同じプロファイルへそろえてください。
`docker/raspberrypi/compose.common.yaml`はコンテナへ`docker/raspberrypi/fastdds_udp_whitelist.xml`をマウントし、
UDPの送信インターフェースをループバックとロボットLANへ限定したうえで、同一ホスト内の
通信に共有メモリ（SHM）を使います。ホスト側が既定設定のままだと、SHMを使う側と
使わない側が混在して通信が成立しません。

Pi本体の`~/.bashrc`へ次を追記します。

```bash
export FASTRTPS_DEFAULT_PROFILES_FILE=/path/to/daifuku_autonomous/docker/raspberrypi/fastdds_udp_whitelist.xml
```

次の2点は環境によって書き換えが必要です。

- whitelist内の`192.168.1.50`はPiの固定IPです。ロボットLANのアドレスが異なる場合は
  XMLを編集してください。
- `compose.common.yaml`の`user: "1000:1000"`は、ホストのROSプロセスがuid 1000（`ubuntu`）で
  動くことを前提にしています。Fast DDSはSHMセグメントを0644で作成するため、uidが
  一致しないと互いのSHMポートを開けません。Fast DDS 2.6にはUDPへのフォールバックが
  ないため、ホストからコンテナへのトピックがエラーも出ないまま止まります。

SHMを使うため、`compose.common.yaml`は`ipc: host`で`/dev/shm`をホストと共有します。理由と
経緯は[ROS 2ネットワーク](network.md#raspberry-pi本体でのdds設定)を参照してください。

## ビルドと起動

イメージが持つのはaptの依存とツールチェーンだけです。ワークスペースのソースは
composeが`src/`をマウントし、`up`のたびにコンテナの中で`colcon build`します。
`up`はまず`workspace-build`サービスを走らせ、その正常終了を待ってから`ros2`と
`raspicat`を起動します。

**リポジトリルートで実行します。** どのComposeファイルを使うかはルートの`.env`が持つので、
別のディレクトリからだと`no configuration file provided`で止まります。`.env`が無ければ
先に`.env.example`から作ってください（[本体ドライバを選ぶ](#本体ドライバを選ぶ)）。

```bash
docker compose build
docker compose up -d
docker compose ps
```

初回の`up`はワークスペース全体を建てるので時間がかかります（Raspberry Pi 4で
1〜2時間、大半は価値反復プランナのRustのreleaseビルド）。2回目以降は変更のあった
パッケージだけが建て直されます。成果物は名前付きボリュームに残ります。

`BUILD_JOBS`はイメージのビルドと`up`のときの`colcon build`の両方に効きます
（既定は4＝Pi 4の全コア）。メモリが不足してOOMで落ちるときだけ減らします。

```bash
BUILD_JOBS=1 docker compose up -d
```

PowerShellでは次のように指定します。

```powershell
$env:BUILD_JOBS = "1"
docker compose up -d
```

## 本体ドライバを選ぶ

`raspicat`サービスが立てる本体ドライバは、リポジトリルートの`.env`の`COMPOSE_FILE`で
選びます。標準は自前実装（`compose.original.yaml`＝`driver:=original`）で、
`.env.example`をコピーした時点でこちらになっています。公式実装
（`compose.rt.yaml`＝`driver:=raspimouse`）はrtmouseカーネルモジュールをホスト側で
読み込んである前提なので、rtmouseが動かないRaspberry Pi 5では選べません。

```bash
cp .env.example .env    # 初回だけ。provision.sh は機種を見て自動で作る

# 公式実装（rtmouse 入りの Pi 4）に替えるとき
sed -i 's|^COMPOSE_FILE=.*|COMPOSE_FILE=docker/raspberrypi/compose.rt.yaml|' .env
docker compose up -d
```

**`.env`は2つ読まれ、値は合成されます。** リポジトリルートのものと、
`docker/raspberrypi/.env`（`provision.sh`が`ROS_DOMAIN_ID`と`BUILD_JOBS`を書いて生成する）
の両方で、**同じキーが両方にあるとあちらが勝ちます**。`COMPOSE_FILE`だけはルート側でしか
効きません（[`docker/raspberrypi/README.md`](../../docker/raspberrypi/README.md#起動)）。

`compose.original.yaml`のほうは`raspicat`サービスに`driver:=original`を渡し、
`/sys/class/pwm`まわりのマウントを足します。Pi 5では必須、Pi 4では任意です。Pi 4で自前実装を選ぶときはrtmouseを載せないでください。
両方がGPIO 16/6/5とPWMを奪い合いますが、rtmouseはレジスタを直書きするためカーネルは
衝突を検出しません（ノード側が起動時に拒否します）。機種ごとの前提と確認手順は
[Raspberry Pi 4で動かす](raspberry-pi-4.md)と[Raspberry Pi 5で動かす](raspberry-pi-5.md)に
まとめています。

## ROS_DOMAIN_ID

Composeの既定値は`90`です。機体側が別の値なら、起動前に合わせます。

```bash
export ROS_DOMAIN_ID=10
docker compose up -d
```

```powershell
$env:ROS_DOMAIN_ID = "10"
docker compose up -d
```

## コンテナを操作する

エントリポイントスクリプト（`/ros_entrypoint.sh`）がROS 2とビルド済みワークスペースを自動で読み込みます。

```bash
docker compose exec ros2 \
  /ros_entrypoint.sh ros2 pkg list
docker compose exec ros2 \
  /ros_entrypoint.sh bash
```

リポジトリに含まれる補助スクリプトから対話シェルを開くこともできます。コンテナが
停止していれば自動的に起動します。

```bash
bash docker/raspberrypi/tools/shell.sh
```

モーター電源、遠隔操作、状態確認は`docker/raspberrypi/tools/control.sh`にまとめています。

```bash
bash docker/raspberrypi/tools/control.sh status
bash docker/raspberrypi/tools/control.sh teleop keyboard
```

サブコマンドと環境変数の一覧は[日常操作と確認](../usage/operations.md#controlshで操作する)を参照してください。

`src/`はまるごとコンテナへマウントされ、`colcon build --symlink-install`が`install/`から`src/`へsymlinkを張ります。したがってホストで変更したlaunch、config、maps、rvizはビルドすら要らずノードの再起動だけで反映され、コンテナで保存した地図もホストに残ります。C++やRustのコードを変更した場合は`docker compose up`で差分ビルドされます。`docker compose build`からやり直すのは、aptの依存、`Dockerfile`、`package.xml`の依存を変更したときと、`docker/common/entrypoint.sh`または`docker/raspberrypi/scripts/build-workspace.sh`を変更したときです（後者2つはイメージへコピーされます）。

## 動作確認

```bash
docker compose exec ros2 \
  /ros_entrypoint.sh ros2 topic list
docker compose exec ros2 \
  /ros_entrypoint.sh ros2 topic echo /scan_raw --once
docker compose exec ros2 \
  /ros_entrypoint.sh ros2 topic echo /odom --once
```

## ログと終了

```bash
docker compose logs
docker compose down
```

キャッシュを使わず再ビルドする場合:

```bash
docker compose build --no-cache
```

次は[LiDARとオドメトリ](lidar.md)を設定し、[地図作成](../usage/mapping.md)または[自律移動](../usage/navigation.md)へ進みます。`use_rviz`の既定は`false`なので、Dockerでlaunchする際に指定するものはありません。
