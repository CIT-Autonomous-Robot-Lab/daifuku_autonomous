# Raspberry Pi 5 で動かす

Raspberry Pi 5 では rtmouse カーネルモジュールが動かないので、本体ドライバを
`autonomous_nav` の `raspicat_pi5_driver.py` に差し替えます。ナビゲーション側は
Pi 4 と同じです。

## なぜ差し替えが要るのか

rtmouse（[`rt-net/RaspberryPiMouse`](https://github.com/rt-net/RaspberryPiMouse)）は
BCM2711 のレジスタを `0xfe000000` 基点で `ioremap()` して、GPIO・PWM・クロック
マネージャの 3 ブロックを直接読み書きします（`src/drivers/rtmouse_gpio.c` の
`gpio_map()`）。Pi 5 は BCM2712 + **RP1** サウスブリッジで、40 ピンヘッダの
GPIO / PWM / SPI / I2C はすべて PCIe の先の RP1 側にあります。この物理アドレスには
何もないので、`insmod` が通っても書き込みが効きません。

上流に Pi 5 対応はありません（最新コミット 2025-10-03 時点で `rtmouse.h` の
`#define RASPBERRYPI` は 1 / 2 / 4 のみ。Issue にも PR にも RP1 の話題なし）。

壊れているのは直書きしている GPIO と PWM だけで、SPI と I2C はカーネルの
サブシステム API 経由なのでそのまま使えます。そこで、カーネルモジュールを移植する
代わりに、**モータ経路だけをユーザ空間から叩く ROS 2 ノード**を用意しました。

## 何が置き換わるか

| | Pi 4 (`driver:=raspimouse`) | Pi 5 (`driver:=pi5`) |
| --- | --- | --- |
| ステップクロック | rtmouse が BCM PWM の RNG/DAT を直書き | RP1 の PWM を `/sys/class/pwm` 経由（GPIO12 → ch0、GPIO13 → ch1） |
| 方向・モータ電源 | rtmouse が GPSET/GPCLR を直書き | `/dev/gpiochip*` のキャラクタデバイス（GPIO16 / 6 / 5） |
| パルスカウンタ | rtmouse が `/dev/rtcounter_*` を出す | `/dev/i2c-1` の 0x10 / 0x11 を直読み |
| ROS ノード | `raspimouse`（raspimouse2） | `raspicat_pi5_driver`（`autonomous_nav`） |

上に見せる契約は同じです。`cmd_vel` を購読し、`odom` と `odom -> base_footprint`
TF を出し、`motor_power` サービスを持つ lifecycle ノードなので、Nav2・EKF・emcl2 の
設定は一切変わりません。

LED・ブザー・スイッチ・測距センサは**用意しません**。このワークスペースの中に
`/leds`・`/buzzer`・`/switches`・`/light_sensors` を使うものが無いためです。

## Pi 4 との意図的な違い

- `cmd_vel` → ステップ周波数の換算に `pulses_per_revolution` を使います。上流の
  `raspimouse_component.cpp` はここだけ 400.0 を直書きしていて、パラメータは
  オドメトリ側にしか効きません。
- パルスカウンタが生きているとき、`odom` の Twist は**指令値ではなく実測値**です。
  `config/sensors/mid360_ekf.yaml` はこのメッセージから vx と vyaw だけを取るので、
  指令値を入れると自分の出力でループを閉じることになります。
- `odom_hz` の既定は 50.0（raspimouse は 100.0）。1 周期あたり I2C を 4 トランザク
  ション使うので、62.5 kHz のバスの占有率を半分に落としてあります。
- `publish_tf` パラメータがあります。EKF に `odom -> base_footprint` を出させる構成
  では `false` にしてください（TF は区間ごとに所有者を 1 つだけにする）。

## rtmouse の固着は起きない

`config/README.md` に書いてあるとおり、rtmouse は I2C が 1 回タイムアウトすると
カーネルの mutex を握ったままになり、`/dev/rtcounter_*` を読む者が全員 D 状態で
固着して**リブートでしか復旧しません**。

このノードでは同じことが起きません。ユーザ空間の `ioctl` はエラーを返して戻って
くるだけで、カウンタの読み出しは専用のコールバックグループで走ります。連続
`counter_error_limit` 回失敗すると `cmd_vel` の積分に落ち、`counter_retry_period`
ごとに再試行して、応答が戻れば自動でエンコーダに復帰します。I2C が詰まっている
あいだも `cmd_vel` → モータの経路は生きています。

**ただし無害ではありません。** カウンタの読み出しは `odom` の発行と同じコール
バックの中にあるので、I2C が 1 回タイムアウトするとその周期の `odom` と
`odom -> base_footprint` TF が**バスのタイムアウト（実装により 1 秒前後）だけ遅れ
ます**。`counter_error_limit: 5` なら退避に落ちるまで最大 5 回。
`controller_server` の `transform_tolerance` は 1.0 なので、ここは余裕がありません。
実機で I2C エラーが出るようなら `counter_error_limit` を 1〜2 に下げてください。

姿勢が飛ばないことだけは保証しています。周期が `5 / odom_hz`（最低 0.2 秒）より
伸びた場合、`cmd_vel` 積分の側はその区間を積みません（1 秒前の指令を 1 回で積むと
1 秒ぶんの移動が一気に乗るため）。エンコーダ側にこの制限はありません。パルスの
差分は区間の長さによらず正確だからです。

## 手順

### 1. SD カードを作る

```bash
sudo python3 tools/image/create_image.py all --model pi5 --device /dev/sdX \
  --ssh-key ~/.ssh/id_ed25519.pub
```

`--model pi5` で `config.txt` に次が入ります。

```
dtparam=i2c_arm=on
dtparam=spi=on
dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4
dtparam=i2c_baudrate=62500
```

`pwm-2chan` は bcm2835 向けのオーバレイですが、`bcm2712-rpi.dtsi` が
`pwm: &pwm0` → `pwm0: &rp1_pwm0` と付け替えているので Pi 5 でも RP1 の PWM に
当たります。`func=4` は `pwm-2chan-overlay.dts` 自身が挙げる正当な組み合わせ
（PWM0: 12,4(Alt0) / PWM1: 13,4(Alt0)）です。

ネット上でよく見る `dtoverlay=pwm-pi5` は**使えません**。`rpi-6.12.y` の
`overlays/Makefile` に無く（あるのは `pwm` / `pwm-2chan` / `pwm-gpio` /
`pwm-gpio-fan` / `pwm-ir-tx` / `pwm-pio` / `pwm1`）、書いても無視されます。

`provision.sh` は `tools/image/udev/99-daifuku-pi5.rules` を
`/etc/udev/rules.d/` へ入れます。コンテナは `user: "1000:1000"` で走り、補助
グループを引き継がないので、`/sys/class/pwm` 配下と `/dev/gpiochip*` と
`/dev/i2c-1` の所有者を 1000:1000 にしています。

### 2. 起動する

```bash
docker compose -f docker/raspberrypi/compose.yaml \
               -f docker/raspberrypi/compose.pi5.yaml up -d
```

`compose.pi5.yaml` が raspicat サービスに `driver:=pi5` を渡し、`/sys/class/pwm` と
`/sys/devices` を rw で足します（カーネルの PWM サブシステムにキャラクタデバイスの
API が無いので sysfs を通すしかなく、Docker は既定で `/sys` を read-only で見せる
ため）。

コンテナを使わずホストで直接動かすこともできます。ネイティブの ROS 2 Humble は
Ubuntu 22.04 前提で Pi 5 では使えないので、その場合も ROS 側はコンテナのままです。

### 3. 確認する

```bash
ros2 lifecycle get /raspicat_pi5_driver          # active になっていること
ros2 topic hz /odom
ros2 service call /motor_power std_srvs/srv/SetBool '{data: true}'
ros2 topic pub --times 20 /cmd_vel geometry_msgs/msg/Twist \
  '{linear: {x: 0.05}, angular: {z: 0.0}}'
```

エンコーダが効いているかは、**モータ OFF のまま回転を指令して `odom` が動かない
こと**で見ます。ノードのログは「カウンタが読めた」ことまでしか保証しません。

## 実機で確かめること

ハードウェアで走らせた実績はまだありません。以下は机上の値なので、最初のベンチで
確認してください。ピン・チャネル・アドレス・デバイスパスはすべてパラメータに
出してあるので、`config/robot/raspicat_pi5.yaml` を直せばコードは触らずに済みます。

まず `ls /boot/firmware/overlays | grep pwm` で、`config.txt` に書いたオーバレイが
実在することを確かめてください。無いオーバレイは黙って無視され、症状は
「`/sys/class/pwm` に RP1 の pwmchip が無い」として出ます。

| 項目 | 見かた | 直す場所 |
| --- | --- | --- |
| PWM のオーバレイ | `ls /boot/firmware/overlays \| grep pwm` で `pwm-2chan.dtbo` があること。`func` 番号が違うようなら `drivers/pinctrl/pinctrl-rp1.c` のピンテーブルで確認 | `config.txt` |
| PWM の出どころ | `ls /sys/class/pwm` と `readlink -f /sys/class/pwm/pwmchipN` | `pwmchip_match` / `pwmchip_path` |
| gpiochip | ノードが `configured: gpiochip=... pwmchip=...` とログに出す解決結果 | `gpiochip_label` / `gpiochip_device` |
| 方向の極性 | 前進を指令して両輪が同じ向きに回るか | `direction_*_forward_level` |
| ステップ周波数と実速度 | 0.1 m/s を指令して実測。`wheel_diameter` は 200 mm 実測値 | `pulses_per_revolution` |
| I2C ボーレート | RP1 の I2C は DesignWare 系でタイミング生成が Pi 4 と違う。62.5 kHz が効くか | `config.txt` |
| **電源** | 制御基板は 40 ピンヘッダ経由で Pi に 5V を供給する。Pi 5 の電流で足りるか | — |
| ステッピングのトルク | PWM のクロック源が変わるので、同じ周波数で同じ回転になるか | — |

## 関連

- [`src/autonomous_nav/config/README.md`](../../src/autonomous_nav/config/README.md) — 設定値の由来
- [`tools/image/README.md`](../../tools/image/README.md) — SD カードの作成
- [`docker/raspberrypi/README.md`](../../docker/raspberrypi/README.md) — コンテナ構成
- [Raspberry Pi 4 で動かす](raspberry-pi-4.md) — 既定の構成（rtmouse）
