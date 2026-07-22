# 日常操作と確認

## 起動状態を確認する

```bash
ros2 node list
ros2 topic list
ros2 topic hz /scan_raw
ros2 topic hz /scan
ros2 topic hz /odom
ros2 run tf2_ros tf2_echo odom base_footprint
```

Mid-360 + IMUでは追加で確認します。

```bash
ros2 topic hz /livox/lidar
ros2 topic hz /livox/imu
ros2 topic hz /imu/mid360
ros2 topic hz /wheel/odom
```

## Dockerでコマンドを実行する

```bash
docker compose -f docker/compose.yaml exec ros2 \
  /ros_entrypoint.sh ros2 topic list
docker compose -f docker/compose.yaml exec ros2 \
  /ros_entrypoint.sh bash
```

補助スクリプト:

```bash
bash docker/tools/bash.sh
```

## 設定変更を反映する

`src/autonomous_nav`配下のlaunch、config、maps、rvizはDockerコンテナへマウントされるため、通常はイメージの再ビルドが不要です。起動中のノードを再起動して反映します。

次を変更した場合はイメージを再ビルドします。

- `docker/Dockerfile`
- 依存パッケージ
- `autonomous_bot.repos`
- CMakeやビルド対象
- 外部パッケージのソース

```bash
docker compose -f docker/compose.yaml down
docker compose -f docker/compose.yaml build
docker compose -f docker/compose.yaml up -d
```

## ログを見る

```bash
docker compose -f docker/compose.yaml logs
docker compose -f docker/compose.yaml logs -f ros2
```

## 終了する

```bash
docker compose -f docker/compose.yaml down
```

ネイティブ環境ではlaunchを実行したターミナルで`Ctrl+C`を押します。停止後も機体側ドライバが動いている場合があるため、必要に応じて機体側も安全に停止してください。
