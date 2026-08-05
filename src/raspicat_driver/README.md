# raspicat_driver

Raspberry Pi Cat の本体ドライバの**自前実装**。Pi 4 と Pi 5 の両方に対応し、
モータ経路（ステップクロック・方向・モータ電源・パルスカウンタ）をユーザ空間から
直接扱います。カーネルモジュール（rtmouse）は使いません。

`robot_bringup.launch.py` の `driver:=original` で立ちます。**リポジトリの標準はこちら**で、
Docker の入口（`.env` の `COMPOSE_FILE`）も `compose.original.yaml` を指しています。
`driver:=` という引数そのものの既定値だけは `raspimouse`（公式実装）のままなので、
`robot_bringup.launch.py` を手で叩くときは明示してください。

```bash
ros2 launch daifuku_stack robot_bringup.launch.py driver:=original
ros2 launch daifuku_stack robot_bringup.launch.py driver:=original model:=pi4
```

## 公式実装との関係

| | `driver:=raspimouse`（公式） | `driver:=original`（ここ） |
| --- | --- | --- |
| ノード | `raspimouse`（raspimouse2） | `raspicat_driver` |
| ステップクロック | rtmouse が SoC の PWM レジスタを直書き | `/sys/class/pwm`（GPIO12 → ch0、GPIO13 → ch1） |
| 方向・モータ電源 | rtmouse が GPSET/GPCLR を直書き | `/dev/gpiochip*` のキャラクタデバイス（GPIO16 / 6 / 5） |
| パルスカウンタ | rtmouse が `/dev/rtcounter_*` を出す | `/dev/i2c-1` の 0x10 / 0x11 を直読み |
| 対応機種 | Pi 4 のみ | Pi 4 / Pi 5 |
| LED・スイッチ | あり | あり（`/dev/gpiochip*` を直接） |
| ブザー | あり（PWM レジスタ直書き） | あり（既定はソフト生成。下記） |
| 測距センサ | あり | **なし** |
| カーネルモジュール | 要 rtmouse | 不要 |

上に見せる契約は同じです。`cmd_vel` と `/leds`（`raspimouse_msgs/Leds`）と
`/buzzer`（`std_msgs/Int16`、値は Hz・0 で停止）を購読し、`odom` と
`odom -> base_footprint` TF と `/switches`（`raspimouse_msgs/Switches`、true が押下）を
出し、`motor_power` サービスを持つ lifecycle ノードなので、Nav2・EKF・emcl2 の設定は
変わりません。

**測距センサだけは持ちません。** GPIO ではなく基板の SPI 側 AD にぶら下がっていて、
このワークスペースの中に `/light_sensors` を読むものが無く、100 Hz で読むと rtmouse
側がカーネル oops を起こす（[`troubleshooting.md`](../../docs/usage/troubleshooting.md)）
ためです。

LED・ブザー・スイッチは**モータ経路と違って必須ではありません**。ピンを掴めなければ
その旨を 1 行出して走行だけ続けます（`configure` は成功します）。起動ログの
`peripherals: leds=... switches=... buzzer=...` が結果です。

### ブザーが PWM ではなくソフト生成なのは

ブザーは GPIO19 で、**Pi 4 ではこれが右モータのステップクロック（GPIO13）と同じ PWM
チャネル**です（BCM2711 の PWM0 ch1。GPIO19 は同じチャネルへの ALT5 経路）。rtmouse は
GPFSEL を `ioremap` していて、鳴らす瞬間だけ GPIO19 を ALT5 に、止めたら OUTPUT に
**mux し直す**のでこれで成立しています。sysfs PWM も gpiochip キャラクタデバイスも
ピンの alt 機能を変えられないので、自前実装は同じ手が使えません。両方のピンを PWM に
mux したままにすると、**鳴らすたびに右車輪がステップします**。

そのため既定（`buzzer_pwm_channel: -1`）はスレッドで GPIO19 を叩く方式です。オーバレイも
再起動も要らず両機種で鳴りますが、スケジューラのゆらぎのぶん**音程がわずかに揺れます**
（Windows でのベンチでは半周期が最悪 6 倍に伸びる瞬間があった）。鳴らしているあいだ
1 コアの 2〜3 割を使います（周波数が高いほど多い。`buzzer_max_frequency` が上限）。

**Pi 5 なら本物の PWM にできます。** RP1 の PWM ブロックは 4 チャネルあり（実機で
`npwm=4`）、`pinctrl-rp1.c` では GPIO18/19 も `pwm0` に繋がるので、モータの ch0・ch1 と
別のチャネルが空いている可能性があります。やることは 2 つです。

1. `config.txt` でピン 19 を PWM へ mux する。`create_image.py` が書くのは
   `dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4`（モータの 2 本）だけなので、
   **今は自分で足して再起動する**ことになります。
2. `buzzer_pwm_channel` にそのチャネル番号を入れる。番号は**実機で当てる**しかありま
   せん（外しても鳴らないだけで、車輪は動きません。モータと同じ番号を書いた場合は
   `configure` がその旨を出して失敗します）。

固まったら `create_image.py` 側にも入れてください。

Pi 4 では `buzzer_pwm_channel` を 0 以上にすると `configure` を拒否します（チャネルが
2 本しかなく、どちらもステップクロックのため）。

**Pi 4 では両者を同時に動かせません。** rtmouse は GPIO と PWM のレジスタを
`ioremap()` して直接書くので、カーネルの pinctrl には何も見えず、衝突は検出され
ません。両方が GPIO 16/6/5 を持てば、モータ電源が入った状態で車輪が逆に回り得ます。
そのため Pi 4 のバックエンドは rtmouse が載っていると **configure を拒否します**
（`allow_rtmouse: true` で上書きできますが、通常は上のとおり壊れます）。

## 中身

実装は `src/raspicat_driver/` の下です（`setup.py` の `package_dir={"": "src"}`）。

| ファイル | 何を持つか |
| --- | --- |
| `node.py` | ROS に見える面。lifecycle・`cmd_vel`・`odom`・TF・`motor_power`・`/leds`・`/buzzer`・`/switches`・オドメトリの積分。レジスタもチップ名も出てこない |
| `backend.py` | 両機種で共通の手順（GPIO → PWM → I2C → 周辺の順で掴む）と、機種の判定 |
| `pi4.py` | BCM2711 の同定（`pinctrl-bcm2835` / `fe20c000.pwm`）と rtmouse の排除、ブザーへの PWM 割り当ての拒否 |
| `pi5.py` | RP1 の同定（`pinctrl-rp1` / `98000.pwm`） |
| `gpio.py` | gpiochip キャラクタデバイス（v1 uAPI）。出力（方向・モータ電源・LED）と入力（スイッチ）。libgpiod は使わない |
| `pwm.py` | `/sys/class/pwm` 経由のハードウェア PWM。ステップクロックと、チャネルが空いていればブザーも |
| `buzzer.py` | PWM が使えないときのブザー（スレッドで GPIO19 を叩く） |
| `i2c.py` | パルスカウンタ（`I2C_RDWR` の write+read 結合転送） |

機種差は**チップの同定だけ**です。ピン番号・PWM チャネル・I2C アドレスは制御基板
側の性質なので両機種で同じ、`model: auto` は device-tree（`/proc/device-tree/`）と
`/proc/cpuinfo` から SoC を見て決めます。それでも全部パラメータに出してあるので、
実機で違っていればコードを触らずに直せます。

外部の Python 依存はありません（libgpiod も smbus2 も使わない）。コンテナのイメージに
どちらも入っておらず、足すと `docker compose build` からやり直しになるためです。

## パラメータと前提

パラメータは `src/daifuku_stack/config/robot/raspicat_driver.yaml`（全キーを既定値の
まま列挙。値の由来は同ディレクトリの `README.md`）。

ホスト側に要るものは 2 つです。どちらも `tools/image/` が入れます。

- `config.txt` の `dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4`
  （`create_image.py`。rtmouse を入れない構成のときだけ入る）
- `tools/image/udev/99-daifuku-raspicat.rules`（`provision.sh`。コンテナは
  uid/gid 1000 で走り補助グループを持たないので、`/sys/class/pwm`・`/dev/gpiochip*`・
  `/dev/i2c-*` の所有者を渡す）

コンテナから動かすときは `docker/raspberrypi/compose.original.yaml` を重ねます
（`/sys/class/pwm` と `/sys/devices` を rw で渡し、`driver:=original` を付ける）。

## I2C が詰まっても固着しません

rtmouse は I2C が 1 回タイムアウトするとカーネルの mutex を握ったままになり、
`/dev/rtcounter_*` を読む者が全員 D 状態で固着してリブートでしか復旧しません
（`src/daifuku_stack/config/README.md`）。

ここではユーザ空間の `ioctl` がエラーを返して戻るだけです。カウンタの読み出しは
専用のコールバックグループで走り、連続 `counter_error_limit` 回失敗すると `cmd_vel`
の積分に落ち、`counter_retry_period` ごとに再試行して、応答が戻れば自動でエンコーダに
復帰します。詰まっているあいだも `cmd_vel` → モータの経路は生きています。

ただし無害ではありません。I2C が 1 回タイムアウトすると、その周期の `odom` と TF が
バスのタイムアウト（1 秒前後）だけ遅れます。実機で I2C エラーが出るようなら
`counter_error_limit` を 1〜2 に下げてください。

## 実機での確認

**まだ走行させていません。** 2026-08-04 に Pi 5 + Raspberry Pi Cat の HAT で、車輪を
浮かせたまま `cmd_vel` → モータ → エンコーダの一巡を確かめ、`pulses_per_revolution`
と `steps_per_revolution` の較正まで済ませました。Pi 4 では未確認です。残っている
ものは機種ごとに [`docs/setup/raspberry-pi-4.md`](../../docs/setup/raspberry-pi-4.md)
と [`docs/setup/raspberry-pi-5.md`](../../docs/setup/raspberry-pi-5.md) の表にあります。

**LED・ブザー・スイッチも実機では未確認です。** ピン番号は rtmouse の `rtmouse.h` から
写したもの（LED 25/24/23/18、SW 20/26/21、ブザー 19）で、机上で確かめたのはソフト生成の
波形だけです（スタブを噛ませて 100 / 440 / 2000 Hz のエッジ数を数えた）。実機では次の
順で見てください。

```bash
ros2 topic pub --once /leds raspimouse_msgs/msg/Leds "{led0: true, led1: false, led2: false, led3: false}"
ros2 topic echo /switches      # 押していないとき全部 false、押すと該当が true
ros2 topic pub --once /buzzer std_msgs/msg/Int16 "{data: 440}"
ros2 topic pub --once /buzzer std_msgs/msg/Int16 "{data: 0}"
```

- LED が 1 つずれる → `gpio_leds` の並びが逆（`[18, 23, 24, 25]`）。
- スイッチが押していないのに true → 内部プルアップが効いていない。起動ログに
  「the pin controller refused an internal pull-up」が出ていないか見る。
- ブザーが鳴らない → まず `peripherals: ... buzzer=` の行。`software on GPIO19` なのに
  無音ならピン番号かハードの側。`pwm channel N` なら番号が違う（Pi 5 でオーバレイを
  入れた場合）。

ブザーだけは `joy:=true`（既定）ならゲームパッドからも鳴ります。START を 2 秒押して
teleop を入れると `joy_teleop` が `/buzzer` へ旋律を出すので
（[joystick.md](../../docs/usage/joystick.md#モードは音で分かります)）、`topic pub` より
手早く確かめられます。
