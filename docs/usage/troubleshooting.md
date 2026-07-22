# トラブルシューティング

## 機体のトピックが見つからない

1. 機体とPC／コンテナの`ROS_DOMAIN_ID`を一致させる
2. 両側の`ROS_LOCALHOST_ONLY`が`0`であることを確認する
3. PCから機体のIPへ到達できるか確認する
4. Docker Desktopのhost networkingを有効にする
5. Windows／LinuxファイアウォールでDDS通信を許可する
6. 機体側ドライバが起動しているか確認する
7. VPNや不要なNICを一時的に切り分ける

`docker/compose.yaml`と`docker_dev/compose.yaml`の`ROS_DOMAIN_ID`既定値は`90`です。

## `/scan`が配信されない

- 2D LiDARドライバが`/scan_raw`へ出しているか確認する
- `lidar:=2d`または`lidar:=mid360`が構成と一致しているか確認する
- まず`ros2 topic hz /scan_raw`、次に`ros2 topic hz /scan`を確認する
- フィルタを切り分けるため`scan_filter_enabled:=false`を試す

## Mid-360で`bind failed`になる

`MID360_config.json`の`host_net_info`に設定したIPが、ROS 2ノードを動かすPCの対象NICへ実際に割り当てられているか確認します。LiDAR本体IPも同一セグメントに合わせます。

## TFが競合または不安定になる

Mid-360 + IMUでは、EKFと車輪ノードが同時に`odom -> base_footprint`を配信していないか確認します。車輪側TFを停止するか、`/tf`を未使用トピックへremapしてください。

センサーTFもURDFと`publish_lidar_tf:=true`の両方から配信しないでください。

```bash
ros2 run tf2_ros tf2_echo odom base_footprint
ros2 topic hz /wheel/odom
ros2 topic hz /odom
```

## RVizが表示されない

軽量な`docker/`イメージにはRVizがありません。ネイティブ環境または`docker_dev/`を使ってください。

GUI付き環境では次を確認します。

- X ServerまたはWSLgが動作している
- `DISPLAY`と`WAYLAND_DISPLAY`が正しい
- Windows X Serverが外部クライアントを許可している
- Linux X11のアクセス許可を設定している

## コンテナ内で`ros2`が見つからない

Compose経由でシェルを開きます。`docker/entrypoint.sh`が環境を読み込みます。

```bash
docker compose -f docker/compose.yaml exec ros2 \
  /ros_entrypoint.sh bash
```

## Raspberry PiでDockerビルドが停止する

メモリ不足の可能性があります。並列数を1にします。

```bash
BUILD_JOBS=1 docker compose -f docker/compose.yaml build
```

## 価値反復の最初の経路計算が遅い

新しいゴールでは地図全体の価値関数を解くため、数秒から数十秒かかる場合があります。ログで計算時間を確認してください。同じゴールへの再計画はキャッシュされます。

## ログを確認する

```bash
docker compose -f docker/compose.yaml logs -f ros2
```

ネイティブ環境ではlaunchを起動したターミナルのログを確認します。
