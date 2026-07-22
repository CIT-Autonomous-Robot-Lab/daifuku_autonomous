# Docker環境

`docker/`はRaspberry Piを含む`arm64`と`amd64`向けの軽量な実行環境です。ROS 2 Humble、Nav2、SLAM Toolbox、EMCL2、価値反復プランナ、Livox関連ノードを含みます。イメージはヘッドレスで、RVizは含みません。

## 必要なもの

- Docker EngineまたはDocker Desktop
- Docker Compose v2（`docker compose`）
- 機体とホスト間のROS 2通信が可能なネットワーク

Docker Desktopでは[ネットワーク設定](network.md#docker-desktop)も先に確認してください。

## ビルドと起動

リポジトリルートで実行します。

```bash
docker compose -f docker/compose.yaml build
docker compose -f docker/compose.yaml up -d
docker compose -f docker/compose.yaml ps
```

Raspberry Pi 4などでメモリが不足する場合はビルド並列数を減らします。

```bash
BUILD_JOBS=1 docker compose -f docker/compose.yaml build
```

PowerShellでは次のように指定します。

```powershell
$env:BUILD_JOBS = "1"
docker compose -f docker/compose.yaml build
```

## ROS_DOMAIN_ID

Composeの既定値は`90`です。機体側が別の値なら、起動前に合わせます。

```bash
export ROS_DOMAIN_ID=10
docker compose -f docker/compose.yaml up -d
```

```powershell
$env:ROS_DOMAIN_ID = "10"
docker compose -f docker/compose.yaml up -d
```

## コンテナを操作する

入口スクリプトがROS 2とビルド済みワークスペースを自動的に読み込みます。

```bash
docker compose -f docker/compose.yaml exec ros2 \
  /ros_entrypoint.sh ros2 pkg list
docker compose -f docker/compose.yaml exec ros2 \
  /ros_entrypoint.sh bash
```

リポジトリに含まれる補助スクリプトから対話シェルを開くこともできます。

```bash
bash docker/tools/bash.sh
```

`src/autonomous_nav`はコンテナのインストール先へマウントされます。ホストで変更したlaunch、config、maps、rvizは再ビルドなしで反映され、コンテナで保存した地図もホストに残ります。依存関係、Dockerfile、CMake設定、外部パッケージを変更した場合は再ビルドしてください。

## 動作確認

```bash
docker compose -f docker/compose.yaml exec ros2 \
  /ros_entrypoint.sh ros2 topic list
docker compose -f docker/compose.yaml exec ros2 \
  /ros_entrypoint.sh ros2 topic echo /scan_raw --once
docker compose -f docker/compose.yaml exec ros2 \
  /ros_entrypoint.sh ros2 topic echo /odom --once
```

## ログと終了

```bash
docker compose -f docker/compose.yaml logs
docker compose -f docker/compose.yaml down
```

キャッシュを使わず再ビルドする場合:

```bash
docker compose -f docker/compose.yaml build --no-cache
```

次は[LiDARとオドメトリ](lidar.md)を設定し、[地図作成](../usage/mapping.md)または[自律移動](../usage/navigation.md)へ進みます。Dockerでlaunchする際は`use_rviz:=false`を指定してください。
