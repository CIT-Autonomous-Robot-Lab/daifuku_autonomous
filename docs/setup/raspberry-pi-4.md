# Raspberry Pi 4 で動かす

Pi 4 は既定の構成です。本体ドライバは上流 raspimouse2 の `raspimouse` ノード
（`robot_bringup.launch.py` の `driver:=raspimouse`、既定）で、rtmouse カーネル
モジュールが出す `/dev/rt*` を読みます。Pi 5 では rtmouse が動かないので構成が
変わります（[Raspberry Pi 5 で動かす](raspberry-pi-5.md)）。

ここには Pi 4 に固有のことだけを書きます。Docker の使い方は
[Docker 環境](docker.md)、SD カード作成のオプション一覧は
[`tools/image/README.md`](../../tools/image/README.md) にあります。

## ホスト側に置くもの

ナビゲーション一式はコンテナの中です。ホストに置くしかないのは次の 4 つで、
どれも `create_image.py` と `provision.sh` が入れます。

| もの | 何のため | 入れるところ |
| --- | --- | --- |
| rtmouse カーネルモジュール | `/dev/rt*`。コンテナからは `insmod` できない | `provision.sh`（`--model pi4` では既定で有効。`create_image.py --no-rtmouse` で省略） |
| `config.txt` の i2c / spi / anyspi | rtmouse が使う I2C・SPI と A/D | `create_image.py --model pi4` |
| スワップ 2048 MB | 価値反復プランナが 4 GB に収まらないことがある | `provision.sh`（`vm.swappiness=10` も同時に） |
| Fast DDS プロファイルの指定 | ホストとコンテナで食い違うと片側だけが SHM を使い、通信が静かに止まる | `provision.sh` が `~/.bashrc` へ |

Pi 4 は Ubuntu 22.04 が動くので、ROS 2 Humble のネイティブ環境
（[`tools/setup/`](../../tools/setup/)、[ネイティブ環境](native.md)）も選べます。
Pi 5 は 24.04 なので選べません。ただし実運用はコンテナです。

## `/dev` を丸ごと渡している理由

`compose.yaml` の `raspicat` サービスは `/dev` をまるごとマウントし、
`device_cgroup_rules: c *:* rwm` を付けています。キャラクタデバイスについては
ほぼ privileged 相当で、狭めたくなる形ですが、**狭めると静かに壊れます**。

rtmouse は out-of-tree モジュールで、キャラクタデバイスのメジャー番号を `insmod` の
たびに動的に取ります（実測 497〜506）。`devices:` での名指しやメジャー固定の
`device_cgroup_rules` は、モジュールの再読込やリブートで古い番号を焼き付けたまま
モータとエンコーダを掴めなくなります。`use_pulse_counters: true` のときは、それが
「`odom` がまったく動かない」という別のバグの顔をして出ます。

狭めるなら rtmouse 側でメジャー番号を固定し、ここに列挙する必要があります。

## パルスカウンタは切ってある

`config/robot/raspicat.yaml` の `use_pulse_counters` は `false`（`cmd_vel` の積分）
です。本来は `true`（`/dev/rtcounter_{l,r}*` のロータリエンコーダ）が正しい設定
ですが、この個体の I2C カウンタが走行中にランダムで固着します。

一度 I2C がタイムアウトするとドライバの mutex が握られたままになり、以後
`/dev/rtcounter_*` を読む者は全員 D 状態に永久固着します。`raspimouse` は単一
スレッドなのでノードごと沈黙し（`/odom` も TF も lifecycle 応答も停止）、
**SIGKILL でも殺せず復旧はリブートだけ**です。`cat /dev/rtcounter_l0` を試すのも
同じで、確認のつもりで打つと機体ごと道連れになります。

運用に効く帰結が 2 つあります。天秤と経緯は
[`config/README.md`](../../src/autonomous_nav/config/README.md) にあります。

- **モータ OFF の dry-run が成立しません。** `odom` は指令値の積分なので、モータ
  電源を切ったまま指令を出しても自己位置だけがゴールまで「走り」ます（Pi 5 は
  エンコーダが生きているので、この確認ができます）。
- Nav2 を回転中に止めるとゼロ速度が届かず、`odom` が回り続けます。その幻の回転を
  emcl2 が打ち消そうとして `map->odom` まで振り回されます。止めるときは
  `/cmd_vel` へゼロを投げてください（`control.sh stop`）。

## 4 GB の制約

Pi 4 のメモリとコア数に由来するもので、Pi 5 では緩みます。

- **ビルド並列数。** `compose.yaml` の `BUILD_JOBS` の既定は 2 ですが、Pi 4 では 1 に
  してください。`provision.sh` が書く `docker/raspberrypi/.env` には 1 が入るので、
  そのまま使う分は変更不要です。手で環境変数を渡すときだけ意識します。
- **`use_composition` の既定 `False` は意図的です。** `True` にすると参加者あたりの
  エンドポイントが増えすぎて新規参加者からディスカバリできなくなり、CPU 飢餓で bond の
  心拍も途絶えます。`config/lifecycle_bond.yaml` の `bond_timeout: 60.0` も同じ事情
  （既定の 4 秒では 8 プロセス同時起動の load 10〜19 に間に合わない）。
- **UDP だけだと TF が遅れます。** 参加者が 20 個近くになるとカーネルが飽和し、TF の
  タイムスタンプが 20 秒以上遅れてゴールが次々と中断します。同一ホスト内は SHM を
  使ってください（`ipc: host` と Fast DDS プロファイル）。
- **価値反復プランナ。** 広域地図での所要時間とピーク RSS は
  [`docs/usage/navigation.md`](../usage/navigation.md#広域地図map_tsudanumaで動かす) に
  あります。スワップはこのための保険です。

症状から引くときは[トラブルシューティング](../usage/troubleshooting.md)へ。

## LED・ブザー・スイッチ・測距センサ

rtmouse があるので Pi 4 では出ます（`use_light_sensors: true` で `/light_sensors`）。
ただし、このワークスペースの中にこれらを使うものはありません。Pi 5 の
ドライバがこれらを持たないのはそのためです。

## 手順

### 1. SD カードを作る

```bash
sudo python3 tools/image/create_image.py all --model pi4 --device /dev/sdX \
  --ssh-key ~/.ssh/id_ed25519.pub
```

`--model pi4` の既定は Ubuntu 22.04、スワップ 2048 MB、rtmouse の導入あり、
`--build-jobs 1` です。`config.txt` に次が入ります。

```
dtparam=i2c_arm=on
dtparam=spi=on
dtparam=i2c_baudrate=62500
dtoverlay=anyspi:spi0-0,dev="microchip,mcp3204",speed=1000000
```

`i2c_baudrate` を標準の 100 kHz から落としてあるのはパルスカウンタ（0x10 / 0x11）の
タイムアウト対策（rt-net の推奨値）、`anyspi` は kernel 5.16 以降の rtmouse が
A/D（MCP3204）をこのオーバレイ経由で取るためです。rtmouse 付属の
`set_configs.bash` は同じ内容を書くだけなので `provision.sh` は走らせません。

### 2. 起動する

```bash
docker compose -f docker/raspberrypi/compose.yaml up -d
```

Pi 5 と違って重ねる compose ファイルはありません（`driver:` の既定が
`raspimouse`）。初回はワークスペース全体を建てるので 1〜2 時間かかります。

### 3. 確認する

まずホスト側で、rtmouse が載っていることを見ます。

```bash
lsmod | grep rtmouse
ls /dev/rt*
```

`/dev/rt*` が無いとノードはモータもエンコーダも掴めません。ここで
**`cat /dev/rtcounter_l0` で中身を見にいかないこと**（前述のとおり固着します）。

```bash
ros2 lifecycle get /raspimouse                 # active になっていること
ros2 topic hz /odom
ros2 service call /motor_power std_srvs/srv/SetBool '{data: true}'
ros2 topic pub --times 20 /cmd_vel geometry_msgs/msg/Twist \
  '{linear: {x: 0.05}, angular: {z: 0.0}}'
```

`odom` は指令値の積分なので、この出力は「ノードが指令を受け取った」ことしか
示しません。実際に動いたかは巻尺で見てください。

## 実機で分かっていること

| 項目 | 分かっていること | 出どころ |
| --- | --- | --- |
| 寸法 | 車輪径 200 mm / トレッド 350 mm（2026-08-03 の実測値）。上流 raspicat の既定 152.4 / 279.18 mm に戻すと並進が 1.31 倍ずれる | [`config/README.md`](../../src/autonomous_nav/config/README.md) |
| オドメトリの検算 | モータ ON で 0.1 m/s を 10 秒指令し、巻尺の実移動距離と `/odom` の変位を比べる。ずれる場合は補正係数ではなく寸法側で詰める | 同上 |
| パルスカウンタ | I2C タイムアウト → mutex 固着 → リブートでしか戻らない。切ってある側（`false`）の実害も 2026-07-29 の実機で確認済み | 同上 |
| DDS とライフサイクル | UDP のみでは TF が 20 秒以上遅れる。bond は既定 4 秒では間に合わない | [`troubleshooting.md`](../usage/troubleshooting.md) |

## 関連

- [`src/autonomous_nav/config/README.md`](../../src/autonomous_nav/config/README.md) — 設定値の由来
- [`tools/image/README.md`](../../tools/image/README.md) — SD カードの作成
- [`docker/raspberrypi/README.md`](../../docker/raspberrypi/README.md) — コンテナ構成
- [Raspberry Pi 5 で動かす](raspberry-pi-5.md) — Pi 5 での差分
