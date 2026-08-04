# Raspberry Pi 5 で動かす

Raspberry Pi 5 では rtmouse カーネルモジュールが動かないので、本体ドライバは
自前実装（[`src/raspicat_driver`](../../src/raspicat_driver/README.md)、
`robot_bringup.launch.py` の `driver:=original`）だけが選べます。ナビゲーション側は
Pi 4 と同じです。Pi 4 でも同じドライバを選べます（[Raspberry Pi 4 で動かす](raspberry-pi-4.md)）。

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

| | 公式実装 (`driver:=raspimouse`) | 自前実装 (`driver:=original`) |
| --- | --- | --- |
| ステップクロック | rtmouse が BCM PWM の RNG/DAT を直書き | RP1 の PWM を `/sys/class/pwm` 経由（GPIO12 → ch0、GPIO13 → ch1） |
| 方向・モータ電源 | rtmouse が GPSET/GPCLR を直書き | `/dev/gpiochip*` のキャラクタデバイス（GPIO16 / 6 / 5） |
| パルスカウンタ | rtmouse が `/dev/rtcounter_*` を出す | `/dev/i2c-1` の 0x10 / 0x11 を直読み |
| ROS ノード | `raspimouse`（raspimouse2） | `raspicat_driver`（同名パッケージ） |
| Pi 5 で | 動かない | これを使う |

上に見せる契約は同じです。`cmd_vel` を購読し、`odom` と `odom -> base_footprint`
TF を出し、`motor_power` サービスを持つ lifecycle ノードなので、Nav2・EKF・emcl2 の
設定は一切変わりません。

LED・ブザー・スイッチ・測距センサは**用意しません**。このワークスペースの中に
`/leds`・`/buzzer`・`/switches`・`/light_sensors` を使うものが無いためです。

## 公式実装との意図的な違い

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

**ただしノードが当たることと、PWM が出ることは別です。** このオーバレイは
クロックを設定しません（`/boot/firmware/overlays/README` の pwm-2chan に
「Currently the clock must have been enabled and configured by other means」と
あり、`clock` パラメータは informational）。Pi 4 ではファームウェアが PWM クロックを
立てるので露見しませんが、Pi 5 では RP1 のクロックを誰かが設定する必要があります。
**Ubuntu 24.04 の raspi カーネルでは `clk_pwm0` が孤児クロックのままレート 0 で、
`rpi-pwm 1f00098000.pwm: failed to get clock rate` になり、`period` の書き込みが
EINVAL で弾かれます**（2026-08-04 実測。`6.8.0-1047` と `6.8.0-1060` の両方で同じ。
noble の `linux-raspi` は 6.8 系しか無く、`linux-firmware-raspi` も
`12-0ubuntu1.1` が最新で上げようがない）。

デバイスツリー側は `assigned-clock-rates = <100000000>` を要求しているので、
効いていないのはカーネルの RP1 クロックドライバです。切り分けは debugfs で:

```bash
sudo grep clk_pwm0 /sys/kernel/debug/clk/clk_summary   # xosc の下でレート≠0 か
sudo cat /sys/kernel/debug/clk/clk_pwm0/clk_parent     # 空なら親が選ばれていない
sudo cat /sys/kernel/debug/clk/clk_pwm0/clk_possible_parents
```

実測では `clk_parent` が**空**で、候補は
`pll_video_sec xosc clksrc_gp0..gp5`。このうち `xosc`（50 MHz）と `pll_video_sec` は
登録されているので、親を DT の `assigned-clock-parents` で名指しすれば通る見込みは
あります（未検証）。同じ理由で `clk_audio_in` / `clk_audio_out` も孤児です。

ネット上でよく見る `dtoverlay=pwm-pi5` は**使えません**。`rpi-6.12.y` の
`overlays/Makefile` に無く（あるのは `pwm` / `pwm-2chan` / `pwm-gpio` /
`pwm-gpio-fan` / `pwm-ir-tx` / `pwm-pio` / `pwm1`）、書いても無視されます。

`provision.sh` は `tools/image/udev/99-daifuku-raspicat.rules` を
`/etc/udev/rules.d/` へ入れます。コンテナは `user: "1000:1000"` で走り、補助
グループを引き継がないので、`/sys/class/pwm` 配下と `/dev/gpiochip*` と
`/dev/i2c-1` の所有者を 1000:1000 にしています。**ルールが入るのは起動後なので、
`/sys/class/pwm` は再起動するまで root のままです**（udev はデバイスの add でしか
発火しない）。プロビジョニング後の再起動はグループ反映だけの話ではありません。

ロボット LAN にルータが無い構成では、`--gateway none` と `--wifi-ssid` が要ります
（[`tools/image/README.md`](../../tools/image/README.md#2-カードを作る)）。あわせて、
**Pi 5 は RTC のバックアップ電池が無いと過去の時刻で起動します**。NTP が効く前に
apt が走ると全リポジトリが `Release file ... is not valid yet` で拒否されるので、
疎通が戻ったら `timedatectl set-ntp true` で時刻を合わせてから
`sudo bash tools/image/provision.sh` をやり直してください。

### 2. 起動する

```bash
docker compose -f docker/raspberrypi/compose.yaml \
               -f docker/raspberrypi/compose.original.yaml up -d
```

`compose.original.yaml` が raspicat サービスに `driver:=original` を渡し、`/sys/class/pwm` と
`/sys/devices` を rw で足します（カーネルの PWM サブシステムにキャラクタデバイスの
API が無いので sysfs を通すしかなく、Docker は既定で `/sys` を read-only で見せる
ため）。

コンテナを使わずホストで直接動かすこともできます。ネイティブの ROS 2 Humble は
Ubuntu 22.04 前提で Pi 5 では使えないので、その場合も ROS 側はコンテナのままです。

### 3. 確認する

```bash
ros2 lifecycle get /raspicat_driver              # active になっていること
ros2 topic hz /odom
ros2 service call /motor_power std_srvs/srv/SetBool '{data: true}'
ros2 topic pub --times 20 /cmd_vel geometry_msgs/msg/Twist \
  '{linear: {x: 0.05}, angular: {z: 0.0}}'
```

エンコーダが効いているかは、**モータ OFF のまま回転を指令して `odom` が動かない
こと**で見ます。ノードのログは「カウンタが読めた」ことまでしか保証しません。

## 実機で確かめること

**まだ走らせていません。** チップの同定だけは 2026-08-04 に Pi 5 Model B Rev 1.1
（8GB・Ubuntu 24.04.4・6.8.0-1047-raspi）で確かめました。モータを回す側は未確認です。
ピン・チャネル・アドレス・デバイスパスはすべてパラメータに出してあるので、
`config/robot/raspicat_driver.yaml` を直せばコードは触らずに済みます。

**現時点で PWM は出せません。** `clk_pwm0` のレートが 0 のままで、`period` の書き込みが
EINVAL になります（上の「1. SD カードを作る」を参照）。ここが解けるまで、モータ経路の
検証はどれも始められません。

同定と権限まわりは確認済み（どれも黙って失敗するので、ノードを立てる前に見ること）:

| 項目 | 実測 |
| --- | --- |
| PWM のオーバレイ | `pwm-2chan.dtbo` は実在（`pwm-pi5.dtbo` は無い）。ただし**存在することと PWM が出ることは別**で、このオーバレイはクロックを設定しない |
| PWM の出どころ | `pwmchip0` → `.../1f00098000.pwm`（RP1・npwm=4）。`pwmchip_match` の `98000.pwm` は `find_pwmchip` が部分一致で拾う。`pwmchip1` は SoC 側の `107d517a80.pwm` |
| gpiochip | `/dev/gpiochip4` が `pinctrl-rp1`（54 本）。`gpiochip0`〜`3` は `gpio-brcmstb@...` |
| 所有者 | `provision.sh` を流した直後は `/sys/class/pwm` が root のまま。**再起動で `ubuntu:ubuntu` になる**。またチャネルの export は子デバイスの `add` ではなく `pwmchip` への `change` で飛ぶので、udev ルールは両方を見る必要がある |
| AppArmor | Docker 既定の `docker-default` は `/sys/fs` 以外の `/sys/**` への書き込みを拒否する。所有者が合っていても export が EACCES になり、監査ログにも残らない。`compose.original.yaml` の `security_opt: apparmor=unconfined` で外してある |

未確認（PWM が出るようになってから）:

| 項目 | 見かた | 直す場所 |
| --- | --- | --- |
| 方向の極性 | 前進を指令して両輪が同じ向きに回るか | `direction_*_forward_level` |
| ステップ周波数と実速度 | 0.1 m/s を指令して実測。`wheel_diameter` は 200 mm 実測値 | `pulses_per_revolution` |
| I2C ボーレート | RP1 の I2C は DesignWare 系でタイミング生成が Pi 4 と違う。62.5 kHz が効くか | `config.txt` |
| **電源** | 制御基板は 40 ピンヘッダ経由で Pi に 5V を供給する。Pi 5 の電流で足りるか | — |
| ステッピングのトルク | PWM のクロック源が変わるので、同じ周波数で同じ回転になるか | — |

## 関連

- [`src/daifuku_stack/config/README.md`](../../src/daifuku_stack/config/README.md) — 設定値の由来
- [`tools/image/README.md`](../../tools/image/README.md) — SD カードの作成
- [`docker/raspberrypi/README.md`](../../docker/raspberrypi/README.md) — コンテナ構成
- [Raspberry Pi 4 で動かす](raspberry-pi-4.md) — 既定の構成（rtmouse）
