# Raspberry Pi 4 で動かす

ここには Pi 4 に固有のことだけを書きます。Docker の使い方は
[Docker 環境](docker.md)、SD カード作成のオプション一覧は
[`tools/image/README.md`](../../tools/image/README.md) にあります。

## 本体ドライバを選ぶ

**Pi 4 だけが 2 つから選べます**（Pi 5 では自前実装しかありません。
[Raspberry Pi 5 で動かす](raspberry-pi-5.md)）。リポジトリの標準はどちらの機種でも
自前実装で、`.env` の `COMPOSE_FILE` もそちらを指しています。

| | 自前実装（標準） | 公式実装 |
| --- | --- | --- |
| 起動 | `driver:=original` | `driver:=raspimouse` |
| ノード | `raspicat_driver`（[`src/raspicat_driver`](../../src/raspicat_driver/README.md)） | `raspimouse`（raspimouse2） |
| モータ経路 | `/sys/class/pwm`・`/dev/gpiochip*`・`/dev/i2c-1` | rtmouse がレジスタを直書き |
| rtmouse | **載っていてはいけない** | **要る** |
| パルスカウンタ | 使う（`use_pulse_counters: true`） | 使わない設定にしてある（下記） |
| LED・ブザー・スイッチ | あり（ブザーはソフト生成。下記） | あり |
| 測距センサ | なし | あり |
| SD カード | `create_image.py --model pi4 --no-rtmouse` | `create_image.py --model pi4` |
| `.env` の `COMPOSE_FILE` | `compose.original.yaml`（既定） | `compose.rt.yaml`（書き換えが要る） |

**`driver:=` という引数そのものの既定値は `raspimouse` です。** Docker では compose が
`driver:=` を明示して渡すのでこの既定は表に出ませんが、`robot_bringup.launch.py` を
手で叩くときだけは効きます（rtmouse の無い機体では configure で落ちます）。

**同時には動かせません。** rtmouse は GPIO と PWM のレジスタを `ioremap()` して直接
書くので、カーネルの pinctrl からは何も見えず、衝突は検出されません。両方が
GPIO 16/6/5 を持てば、モータ電源が入ったまま車輪が逆に回り得ます。自前ドライバは
rtmouse が載っていると configure を拒否します（`/proc/modules` と `/dev/rtmotor*` を
見る）。

**以下は公式実装（rtmouse）を選んだときの話です。** Pi 4 でしか成り立たない前提が多いので
先に置いてあります。標準の自前実装で動かす手順は[自前ドライバで動かす](#自前ドライバで動かす)に
まとめてあり、そちらは Pi 5 と同じ内容です。

## ホスト側に置くもの

ナビゲーション一式はコンテナの中です。ホストに置くしかないのは次の 4 つで、
どれも `create_image.py` と `provision.sh` が入れます。

| もの | 何のため | 入れるところ |
| --- | --- | --- |
| rtmouse カーネルモジュール | `/dev/rt*`。コンテナからは `insmod` できない。**公式実装のときだけ** | `provision.sh`（`--model pi4` では既定で有効。`create_image.py --no-rtmouse` で省略） |
| `config.txt` の i2c / spi / anyspi | rtmouse が使う I2C・SPI と A/D（`--no-rtmouse` では anyspi の代わりに `pwm-2chan`） | `create_image.py --model pi4` |
| udev ルール | 自前実装が使う PWM・gpiochip・i2c の所有権 | `provision.sh`（機種によらず入る） |
| スワップ 2048 MB | 価値反復プランナが 4 GB に収まらないことがある | `provision.sh`（`vm.swappiness=10` も同時に） |
| Fast DDS プロファイルの指定 | ホストとコンテナで食い違うと片側だけが SHM を使い、通信が静かに止まる | `provision.sh` が `~/.bashrc` へ |

Pi 4 は Ubuntu 22.04 が動くので、ROS 2 Humble のネイティブ環境
（[`tools/setup/`](../../tools/setup/)、[ネイティブ環境](native.md)）も選べます。
Pi 5 は 24.04 なので選べません。ただし実運用はコンテナです。

## `/dev` を丸ごと渡している理由

`compose.common.yaml` の `raspicat` サービスは `/dev` をまるごとマウントし、
`device_cgroup_rules: c *:* rwm` を付けています。キャラクタデバイスについては
ほぼ privileged 相当で、狭めたくなる形ですが、**狭めると静かに壊れます**。

rtmouse は out-of-tree モジュールで、キャラクタデバイスのメジャー番号を `insmod` の
たびに動的に取ります（実測 497〜506）。`devices:` での名指しやメジャー固定の
`device_cgroup_rules` は、モジュールの再読込やリブートで古い番号を焼き付けたまま
モータとエンコーダを掴めなくなります。`use_pulse_counters: true` のときは、それが
「`odom` がまったく動かない」という別のバグの顔をして出ます。

狭めるなら rtmouse 側でメジャー番号を固定し、ここに列挙する必要があります。
自前実装が使う `/dev/gpiochip*` と `/dev/i2c-*` はメジャー番号が固定なので、この
制約は公式実装（rtmouse）に固有のものです。

## パルスカウンタは切ってある（公式実装）

`config/robot/raspicat.yaml` の `use_pulse_counters` は `false`（`cmd_vel` の積分）
です。これは rtmouse を経由する公式実装の話で、自前実装（`raspicat_driver.yaml`）は
`true` が既定です。本来は `true`（`/dev/rtcounter_{l,r}*` のロータリエンコーダ）が正しい設定
ですが、この個体の I2C カウンタが走行中にランダムで固着します。

一度 I2C がタイムアウトするとドライバの mutex が握られたままになり、以後
`/dev/rtcounter_*` を読む者は全員 D 状態に永久固着します。`raspimouse` は単一
スレッドなのでノードごと沈黙し（`/odom` も TF も lifecycle 応答も停止）、
**SIGKILL でも殺せず復旧はリブートだけ**です。`cat /dev/rtcounter_l0` を試すのも
同じで、確認のつもりで打つと機体ごと道連れになります。

運用に効く帰結が 2 つあります。天秤と経緯は
[`config/README.md`](../../src/daifuku_stack/config/README.md) にあります。

- **モータ OFF の dry-run が成立しません。** `odom` は指令値の積分なので、モータ
  電源を切ったまま指令を出しても自己位置だけがゴールまで「走り」ます（自前実装は
  エンコーダが生きているので、この確認ができます）。
- Nav2 を回転中に止めるとゼロ速度が届かず、`odom` が回り続けます。その幻の回転を
  emcl2 が打ち消そうとして `map->odom` まで振り回されます。止めるときは
  `/cmd_vel_teleop` へゼロを投げてください（`control.sh stop`）。仲裁
  （`twist_mux:=true` が既定）で優先度が高いのはそちらで、`/cmd_vel` は自律側の
  出力です。`twist_mux:=false` で立てているなら `/cmd_vel` が宛先になります。

## 4 GB の制約

Pi 4 のメモリとコア数に由来するもので、Pi 5 では緩みます。

- **ビルド並列数。** `compose.common.yaml` の `BUILD_JOBS` の既定は 4（Pi 4 の全コア）です。
  `up` のワークスペースビルドはこれで足ります。release の rustc 2 本を走らせた実測で
  RSS 750 MB・available 2.5 GB と余っていました。**イメージそのものを Pi 上で焼く
  ときだけ別**で、`provision.sh` が書く `docker/raspberrypi/.env` には 1 が入ります
  （rclrs のビルドまで含むので桁が違う）。メモリが足りずに OOM で落ちるときは
  `BUILD_JOBS=1` を手で渡してください。
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

LED とスイッチはどちらの実装でも出せます（自前実装は `/dev/gpiochip*` を直接使う）。

**ブザーだけは Pi 4 で事情が違います。** ブザーの GPIO19 は右モータのステップクロック
（GPIO13）と同じ PWM チャネルで、BCM2711 の PWM0 にはチャネルが 2 本しかありません。
rtmouse はレジスタを `ioremap` していて鳴らす瞬間だけ GPIO19 を mux し直すのでこれで
成立しますが、sysfs PWM ではピンの alt 機能を変えられません。両方を PWM に mux したま
まにすると**鳴らすたびに右車輪がステップします**。そのため自前実装の Pi 4 では
`buzzer_pwm_channel` を 0 以上にすると configure を拒否し、既定ではスレッドで GPIO19 を
叩いて鳴らします（音程がわずかに揺れる。理由と Pi 5 での回避策は
[`src/raspicat_driver/README.md`](../../src/raspicat_driver/README.md)）。

**測距センサだけは切ってあります**（`config/robot/raspicat.yaml` の
`use_light_sensors: false`）。`true` に戻すと `raspimouse` が
`/dev/rtlightsensor0` を 100 Hz で読み、rtmouse 側でカーネル oops を起こして
プロセスごと落ちます。ログには何も出ず、`odom` が来ないという形でだけ現れます
（2026-08-03 に Pi 4 Model B Rev 1.5 / 5.15.0-1098-raspi で確認）。症状と復旧は
[トラブルシューティング](../usage/troubleshooting.md)の先頭にあります。

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
docker compose up -d
```

`.env` の `COMPOSE_FILE` が `docker/raspberrypi/compose.rt.yaml` を指していること。
**リポジトリ標準は `compose.original.yaml`（自前実装）なので、rtmouse 入りの Pi 4 は
ここを書き換える必要があります**（`provision.sh` は `--model pi4` かつ rtmouse ありの
ときだけ `compose.rt.yaml` を指す `.env` を自動で作ります）。取り違えると
`raspicat_driver` が rtmouse を見つけて configure を拒否します。初回はワークスペース
全体を建てるので 1〜2 時間かかります。

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

## 自前ドライバで動かす

rtmouse を使わず、`raspicat_driver` がモータ経路をユーザ空間から直接扱う構成です。
Pi 5 と同じコード・同じパラメータファイルで、違うのはチップの同定だけです
（`pinctrl-bcm2835` / `fe20c000.pwm`。Pi 5 は `pinctrl-rp1` / `98000.pwm`）。

### 1. rtmouse の無い SD カードを作る

```bash
sudo python3 tools/image/create_image.py all --model pi4 --no-rtmouse --device /dev/sdX \
  --ssh-key ~/.ssh/id_ed25519.pub
```

`--no-rtmouse` で `config.txt` の中身が変わります。rtmouse の A/D 用 `anyspi` が
落ち、代わりにステップクロック用の PWM オーバレイが入ります。

```
dtparam=i2c_arm=on
dtparam=spi=on
dtparam=i2c_baudrate=62500
dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4
```

`provision.sh` は機種によらず `tools/image/udev/99-daifuku-raspicat.rules` を入れ、
`/sys/class/pwm` 配下・`/dev/gpiochip*`・`/dev/i2c-*` の所有者を 1000:1000 にします
（コンテナは `user: "1000:1000"` で走り、補助グループを引き継がないため）。

既に rtmouse 入りで運用している機体を移す場合は、SD を焼き直さずに次でも足ります。

```bash
sudo rmmod rtmouse
sudo rm -f /etc/modules-load.d/rtmouse.conf
# config.txt の anyspi を消し、dtoverlay=pwm-2chan,... を足して再起動
```

### 2. 起動する

```bash
# .env が COMPOSE_FILE=docker/raspberrypi/compose.original.yaml を指していること（既定）
docker compose up -d
```

`compose.original.yaml` が `driver:=original` を渡し、`/sys/class/pwm` と
`/sys/devices` を rw で足します（カーネルの PWM サブシステムにキャラクタデバイスの
API が無く、Docker は既定で `/sys` を read-only で見せるため）。

### 3. 確認する

```bash
ros2 lifecycle get /raspicat_driver              # active になっていること
ros2 topic hz /odom
ros2 service call /motor_power std_srvs/srv/SetBool '{data: true}'
```

起動ログの `configured: model=pi4 (BCM2711) gpiochip=... pwmchip=...` が、機種判定と
チップの解決結果です。ここが違っていれば `model` / `gpiochip_device` /
`pwmchip_path` で名指しできます。

エンコーダが効いているかは、**モータ OFF のまま回転を指令して `odom` が動かない
こと**で見ます（公式実装ではこれができません）。

### 実機で確かめること

Pi 4 でも Pi 5 でも走らせた実績はまだありません。見る順に:

| 項目 | 見かた | 直す場所 |
| --- | --- | --- |
| PWM のオーバレイ | `ls /boot/firmware/overlays \| grep pwm` に `pwm-2chan.dtbo` があること。無いオーバレイは黙って無視される | `config.txt` |
| PWM の出どころ | `ls /sys/class/pwm` と `readlink -f /sys/class/pwm/pwmchipN`（`fe20c000.pwm` が出るはず） | `pwmchip_match` / `pwmchip_path` |
| オンボード音声との競合 | オーバレイがあるのに `/sys/class/pwm` が空なら `config.txt` の `dtparam=audio=on` を疑う。Pi 4 のヘッドフォン出力は GPIO12/13 と同じ PWM ブロックを使う | `config.txt`（`dtparam=audio=off`） |
| gpiochip | ノードの `configured: ...` ログ（`pinctrl-bcm2835` のはず） | `gpiochip_label` / `gpiochip_device` |
| 方向の極性 | 前進を指令して両輪が同じ向きに回るか | `direction_*_forward_level` |
| ステップ周波数と実速度 | 0.1 m/s を指令して巻尺で実測。実測較正は Pi 5 で済ませてあり（エンコーダ 1118 / ステップ 570、制御基板側の性質なので機種によらない）、ずれるなら脱調かすべり | `steps_per_revolution` |
| パルスカウンタ | モータ OFF で回転指令 → `odom` が動かないこと | `use_pulse_counters` |
| I2C の安定性 | `pulse counters failed ...` が出ないか。出るなら `counter_error_limit` を 1〜2 へ | `counter_error_limit` |

この個体の I2C カウンタは公式実装で固着の実績があります（上記）。自前ドライバでは
リブートが要る固着にはなりませんが、1 回のタイムアウトでその周期の `odom` と TF が
1 秒前後遅れます。`controller_server` の `transform_tolerance` は 1.0 で余裕がありません。

## 実機で分かっていること

| 項目 | 分かっていること | 出どころ |
| --- | --- | --- |
| 寸法 | 車輪径 200 mm / トレッド 350 mm（2026-08-03 の実測値）。上流 raspicat の既定 152.4 / 279.18 mm に戻すと並進が 1.31 倍ずれる | [`config/README.md`](../../src/daifuku_stack/config/README.md) |
| オドメトリの検算 | モータ ON で 0.1 m/s を 10 秒指令し、巻尺の実移動距離と `/odom` の変位を比べる。ずれる場合は補正係数ではなく寸法側で詰める | 同上 |
| パルスカウンタ | I2C タイムアウト → mutex 固着 → リブートでしか戻らない。切ってある側（`false`）の実害も 2026-07-29 の実機で確認済み | 同上 |
| DDS とライフサイクル | UDP のみでは TF が 20 秒以上遅れる。bond は既定 4 秒では間に合わない | [`troubleshooting.md`](../usage/troubleshooting.md) |

## 関連

- [`src/daifuku_stack/config/README.md`](../../src/daifuku_stack/config/README.md) — 設定値の由来
- [`tools/image/README.md`](../../tools/image/README.md) — SD カードの作成
- [`docker/raspberrypi/README.md`](../../docker/raspberrypi/README.md) — コンテナ構成
- [Raspberry Pi 5 で動かす](raspberry-pi-5.md) — Pi 5 での差分
