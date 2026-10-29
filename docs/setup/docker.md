# Docker環境

`docker/raspberrypi/`は、Raspberry Piを含む`arm64`環境と`amd64`環境向けの軽量な実行環境です。ROS 2 Humble、Nav2、SLAM Toolbox、EMCL2、価値反復プランナ、Livox関連ノード、速度指令の仲裁（`twist_mux`）、ゲームパッド（`joy`）、teleopノード（`teleop_twist_keyboard`、`teleop_twist_joy`）を含みます。イメージはヘッドレスで、RVizは含みません。

共通のentrypointとCompose補助処理は[`docker/common/`](../../docker/common)にあります。

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
composeがリポジトリ全体を `/opt/ros_ws` に bind mount し、`up`のたびに
コンテナの中で`colcon build`します。
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
パッケージだけが建て直されます。構成変更時は生成物を自動整理して再ビルドします。
成果物はホストのリポジトリ直下の `build/`・`install/`・`log/` に残ります。
名前付きボリュームは使いません。

`BUILD_JOBS`が効くのは`up`のときの`colcon build`だけです（既定は4＝Pi 4の
全コア）。メモリが不足してOOMで落ちるときだけ減らします。

```bash
BUILD_JOBS=1 docker compose up -d
```

**イメージのビルドには効きません。**そちらを絞るときは`--build-arg`で明示します
（環境変数を渡すだけで効いてしまうと、`up`を絞った瞬間にイメージのビルド
キャッシュが全部外れて1〜2時間の焼き直しになるため）。

```bash
docker compose build --build-arg BUILD_JOBS=1
```

PowerShellでは次のように指定します。

```powershell
$env:BUILD_JOBS = "1"
docker compose up -d
```

## Composeの構成

`COMPOSE_FILE`は`compose.common.yaml`を先に、ドライバ別ファイルを後に並べます。
逆順ではcommonのplaceholderが勝ち、`raspicat`がexit 1で止まります。
ドライバ側からcommonを`include`するとサービス上書きが競合するので、2ファイルを重ねます。

- `compose.original.yaml`: 自前ドライバ。既定で、Pi 5では必須
- `compose.rt.yaml`: rtmouseを使う公式ドライバ。Pi 4専用
- `compose.none.yaml`: 本体ドライバを立てず`workspace-build`と`ros2`を使う環境

どの入口もプロジェクト名は`daifuku-autonomous`で、同じリポジトリを bind mount します。
`Dockerfile`は依存とツールチェーン、`scripts/build-workspace.sh`はコンテナ内の
ビルド、`fastdds_udp_whitelist.xml`はDDSの設定です。

## 本体ドライバを選ぶ

`raspicat`サービスが立てる本体ドライバは、リポジトリルートの`.env`の`COMPOSE_FILE`で
選びます。標準は自前実装（`compose.original.yaml`＝`driver:=original`）で、
`.env.example`をコピーした時点でこちらになっています。公式実装
（`compose.rt.yaml`＝`driver:=raspimouse`）はrtmouseカーネルモジュールをホスト側で
読み込んである前提なので、rtmouseが動かないRaspberry Pi 5では選べません。

```bash
cp .env.example .env    # 初回だけ。provision.sh は機種を見て自動で作る

# 公式実装（rtmouse 入りの Pi 4）に替えるとき
sed -i 's|^COMPOSE_FILE=.*|COMPOSE_FILE=docker/raspberrypi/compose.common.yaml:docker/raspberrypi/compose.rt.yaml|' .env
docker compose up -d
```

`.env`の`USE_MID360_IMU`（既定`true`）は、同じ`robot.launch.py`が
立てるドライバとEKFの両方へ効きます。

```bash
sed -i 's|^#*USE_MID360_IMU=.*|USE_MID360_IMU=false|' .env
docker compose up -d    # 環境変数は起動時に読まれるのでコンテナを作り直す
```

読むのは`raspicat`サービス1つだけです。**ドライバもEKFも同じlaunch
（`robot.launch.py`）が立てるので、片方だけ切り替わる状態は作れません。**
配線と、`true`のときの注意（**機体の電源投入時に静止させること**——バイアス測定は
bootのタイミングになりました）は[LiDARとオドメトリ](lidar.md#imuと車輪オドメトリ)。

もう1つ`LIDAR`（既定`mid360`。`2d`はraspicatのURG）があり、`raspicat`サービスの
`lidar:=`と、自律移動・地図作成側の`/scan`を作る段へ展開されます。
**ドライバを立てるのは`raspicat`だけ**ですが、`navigation` / `mapping`側も
同じ`LIDAR`と`LIDAR_DRIVER`を読むので、両側を揃えてください。

```bash
sed -i 's|^#*LIDAR=.*|LIDAR=2d|' .env
docker compose up -d
```

**`.env`は2つ読まれ、値は合成されます。** リポジトリルートのものと、
`docker/raspberrypi/.env`（`provision.sh`が`ROS_DOMAIN_ID`と`BUILD_JOBS`を書いて生成する）
の両方で、**同じキーが両方にあるとあちらが勝ちます**。`COMPOSE_FILE`だけはルート側でしか
効きません。

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
bash tools/enter-shell.sh
```

モーター電源、遠隔操作、状態確認は`tools/control.sh`にまとめています。

```bash
bash tools/control.sh status
bash tools/control.sh teleop keyboard
```

サブコマンドと環境変数の一覧は[日常操作と確認](../usage/operations.md#controlshで操作する)を参照してください。

設定・ソースの変更を反映する手順は[日常操作](../usage/operations.md#設定変更を反映する)を参照してください。

## 外部ソースと再起動

実行時に使う外部ソースはホスト側の`src/`です。`workspace-build`はネットワークに
繋がっていれば`daifuku_autonomous.repos`に従って`vcs import`します。オフラインや
import失敗時は既存のチェックアウトを使い、足りないリポジトリがあれば名前を出して止まります。
最初の取り込みにはネットワークが必要です。タグ・コミットの変更は次の`up`で反映されますが、
ブランチ名はローカルのcheckoutだけなので、上流へ進めるには明示的な更新が必要です。

```bash
git -C src/value_iteration3 fetch origin && git -C src/value_iteration3 merge --ff-only origin/main
```

`ros2`と`raspicat`は`restart: unless-stopped`でPiの再起動後も戻りますが、
そのとき`workspace-build`は走らず、ボリューム内の前回の成果物を使います。
C++やRustの変更は`docker compose up -d`を人手で通してください。
`stop`したサービスは止まったままで、`down`した場合は次に`up`が必要です。

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
