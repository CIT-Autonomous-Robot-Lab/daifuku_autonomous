# トラブルシューティング

## 機体のトピックが見つからない

1. 機体とPC／コンテナの`ROS_DOMAIN_ID`を一致させる
2. 両側の`ROS_LOCALHOST_ONLY`が`0`であることを確認する
3. PCから機体のIPへ到達できるか確認する
4. Docker Desktopのhost networkingを有効にする
5. Windows／LinuxファイアウォールでDDS通信を許可する
6. 機体側ドライバが起動しているか確認する
7. VPNや不要なNICを一時的に切り分ける

`docker/raspberrypi/compose.yaml`と`docker/dev/compose.yaml`の`ROS_DOMAIN_ID`既定値は`90`です。

Pi本体のネイティブノードと同じPi上のコンテナとの間でトピックが見えない場合は、
下記のディスカバリとSHMの項目も確認してください。

## ノードが現れたり消えたりする（ディスカバリが不安定）

Pi本体でネイティブノードと`docker/raspberrypi/`コンテナを同時に動かす構成で、負荷が上がると
発生します。原因は、各DDS参加者が相手から到達できないwlan0側のロケータまで広告し、
そのぶんUDPバッファが逼迫することです。

`docker/raspberrypi/fastdds_udp_whitelist.xml`をホストとコンテナの両方で使ってください。
コンテナ側は`compose.yaml`が設定済みなので、必要な作業はPi本体側だけです。

```bash
# Pi本体の ~/.bashrc へ追記
export FASTRTPS_DEFAULT_PROFILES_FILE=/path/to/daifuku_autonomous/docker/raspberrypi/fastdds_udp_whitelist.xml
echo "$FASTRTPS_DEFAULT_PROFILES_FILE"
```

whitelist内の`192.168.1.50`はPiの固定IPです。ロボットLANのアドレスが異なる場合は
XMLを書き換えてください。詳細は[ROS 2ネットワーク](../setup/network.md#raspberry-pi本体でのdds設定)を参照してください。

## TFが20秒以上遅れてゴールが中断する

Pi 4で参加者が20個近くになり、すべてUDPで通信していると、購読者ごとの`sendmsg`で
カーネルが飽和します（実測でsys 57%、load 24）。TFのタイムスタンプが大きく遅れ、
Nav2のゴールが次々と中断します。

同一ホスト内の通信を共有メモリ（SHM）へ切り替えて解消します。上記のプロファイル
設定に加えて、次の2点を確認してください。

- `docker/raspberrypi/compose.yaml`に`ipc: host`があること（`/dev/shm`をホストと共有する）
- `docker/raspberrypi/compose.yaml`の`user`が、ホストのROSプロセスのuidと一致していること

Fast DDSはSHMセグメントを0644で作成するため、root権限のコンテナと非rootのホストが
混在すると互いのポートを開けません。Fast DDS 2.6にはUDPへのフォールバックがないので、
トピックがエラーなく止まります。

```bash
# ホスト側のROSプロセスのuidを確認する
id -u
ls -l /dev/shm | head
```

## `Aborting bringup`でNav2が落ちる

ログに`… unable to be reached after 4.00s by bond`と出て、ライフサイクルマネージャが
`CRITICAL FAILURE`から自動シャットダウンする場合です。Pi 4では非合成起動時に8個の
プロセスが同時に立ち上がってloadが10〜19まで跳ね、bond形成が既定の4秒に間に合いま
せん。

`config/lifecycle_bond.yaml`でタイムアウトを60秒へ延長しています。値が効いて
いるか確認してください。

```bash
ros2 param get /lifecycle_manager_navigation bond_timeout
ros2 param get /lifecycle_manager_localization bond_timeout
```

なお`use_composition:=True`にすると、参加者あたりのエンドポイント数が大きくなり
すぎて新規参加者からディスカバリできなくなるうえ、CPU飢餓でbond心拍も途絶しやすく
なります。Pi 4では既定の`False`のまま使ってください。

## Mid-360のスキャンが「古すぎる」と拒否される

`message filter dropping message` や、TF・コストマップでスタンプが未来／過去に
ずれている旨のログが、起動から数分後に出る場合です。Mid-360がPTP同期していないため、
デバイス内蔵時計がPiのシステム時計に対して毎分数秒ずれていくことが原因です。

`lidar:=mid360`では`scripts/restamp_scan.py`が受信時刻でスタンプを打ち直します。
中継が動いているか確認してください。

```bash
ros2 node list | grep restamp_scan
ros2 topic hz /scan_mid360_prestamp
ros2 topic hz /scan_raw
```

`/scan_mid360_prestamp`だけが流れて`/scan_raw`が止まっている場合は、中継ノードが
起動していません。`CMakeLists.txt`が`scripts/`をインストールしないため、
`docker/raspberrypi/`のCompose環境以外では`share/autonomous_nav/scripts/restamp_scan.py`が存在
しないことが原因です（`docker/raspberrypi/`では`src/autonomous_nav`がマウントされるため動作
します）。`ExecuteProcess`の失敗は他のノードを止めないので、エラーが出ないまま
`/scan_raw`だけが欠けた状態になります。

対処は[LiDARとオドメトリ](../setup/lidar.md#タイムスタンプの打ち直し)のインストール
規則を追加することです。

## EMCL2の推定姿勢がその場で回転する

RESETのログが毎スキャン出て、推定姿勢が回り続ける場合です。非貫通率（alpha）が
`alpha_threshold`を下回り続け、膨張リセットとセンサーリセットが常時発動しています。

根本原因は地図と実環境の不整合です。実測では有効ビームの28%が地図上の壁を貫通して
おり、alphaが0.0〜0.4に張り付いていました。

1. RVizでスキャンと地図の壁が重なるか確認する
2. ずれている場合は[地図作成](mapping.md)からやり直す
3. 地図を取り直すまでの暫定処置として、`config/localization/emcl2.yaml`の
   `alpha_threshold`を下げ、`sensor_reset: false`にしてリセットを抑制する

現在の設定値と背景は[設定リファレンス](configuration.md#自己位置推定の暫定設定)を
参照してください。地図を取り直したあとは既定寄りの値へ戻してください。

## `/scan`が配信されない

- 2D LiDARドライバが`/scan_raw`へ出しているか確認する
- `lidar:=2d`または`lidar:=mid360`が構成と一致しているか確認する
- 上流から順に確認する。2D LiDARは`/scan_raw` → `/scan`、Mid-360は`/livox/lidar` →
  `/scan_mid360_prestamp` → `/scan_raw` → `/scan`
- フィルタを切り分けるため`scan_filter_enabled:=false`を試す

## Mid-360で`bind failed`になる

`config/sensors/MID360_config.json`の`host_net_info`に設定したIPが、ROS 2ノードを動かすPCの対象NICへ実際に割り当てられているか確認します。LiDAR本体IPも同一セグメントに合わせます。

## TFが競合または不安定になる

Mid-360 + IMUでは、EKFと車輪ノードが同時に`odom -> base_footprint`を配信していないか確認します。車輪側TFを停止するか、`/tf`を未使用トピックへremapしてください。

センサーTFもURDFと`publish_lidar_tf:=true`の両方から配信しないでください。

```bash
ros2 run tf2_ros tf2_echo odom base_footprint
ros2 topic hz /wheel/odom
ros2 topic hz /odom
```

## RVizが表示されない

軽量な`docker/raspberrypi/`イメージにはRVizがありません。ネイティブ環境または`docker/dev/`を使ってください。

GUI付き環境では次を確認します。

- X ServerまたはWSLgが動作している
- `DISPLAY`と`WAYLAND_DISPLAY`が正しい
- Windows X Serverが外部クライアントを許可している
- Linux X11のアクセス許可を設定している

## コンテナ内で`ros2`が見つからない

Compose経由でシェルを開きます。`docker/raspberrypi/entrypoint.sh`が環境を読み込みます。

```bash
docker compose -f docker/raspberrypi/compose.yaml exec ros2 \
  /ros_entrypoint.sh bash
```

## Raspberry PiでDockerビルドが停止する

メモリ不足の可能性があります。並列数を1にします。

```bash
BUILD_JOBS=1 docker compose -f docker/raspberrypi/compose.yaml build
```

## 価値反復の最初の経路計算が遅い

新しいゴールでは地図全体の価値関数を解くため、数秒から数十秒かかる場合があります。ログで計算時間を確認してください。同じゴールへの再計画はキャッシュされます。

## ログを確認する

```bash
docker compose -f docker/raspberrypi/compose.yaml logs -f ros2
```

ネイティブ環境ではlaunchを起動したターミナルのログを確認します。
