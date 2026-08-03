# raspicat_driver

Raspberry Pi Cat の本体ドライバの**自前実装**。Pi 4 と Pi 5 の両方に対応し、
モータ経路（ステップクロック・方向・モータ電源・パルスカウンタ）をユーザ空間から
直接扱います。カーネルモジュール（rtmouse）は使いません。

`robot_bringup.launch.py` の `driver:=original` で立ちます。既定は公式実装
（`driver:=raspimouse`）のままです。

```bash
ros2 launch autonomous_nav robot_bringup.launch.py driver:=original
ros2 launch autonomous_nav robot_bringup.launch.py driver:=original model:=pi4
```

## 公式実装との関係

| | `driver:=raspimouse`（公式） | `driver:=original`（ここ） |
| --- | --- | --- |
| ノード | `raspimouse`（raspimouse2） | `raspicat_driver` |
| ステップクロック | rtmouse が SoC の PWM レジスタを直書き | `/sys/class/pwm`（GPIO12 → ch0、GPIO13 → ch1） |
| 方向・モータ電源 | rtmouse が GPSET/GPCLR を直書き | `/dev/gpiochip*` のキャラクタデバイス（GPIO16 / 6 / 5） |
| パルスカウンタ | rtmouse が `/dev/rtcounter_*` を出す | `/dev/i2c-1` の 0x10 / 0x11 を直読み |
| 対応機種 | Pi 4 のみ | Pi 4 / Pi 5 |
| LED・ブザー・スイッチ・測距センサ | あり | **なし** |
| カーネルモジュール | 要 rtmouse | 不要 |

上に見せる契約は同じです。`cmd_vel` を購読し、`odom` と `odom -> base_footprint`
TF を出し、`motor_power` サービスを持つ lifecycle ノードなので、Nav2・EKF・emcl2 の
設定は変わりません。

LED・ブザー・スイッチ・測距センサを持たないのは、このワークスペースの中に
`/leds`・`/buzzer`・`/switches`・`/light_sensors` を使うものが無いためです。

**Pi 4 では両者を同時に動かせません。** rtmouse は GPIO と PWM のレジスタを
`ioremap()` して直接書くので、カーネルの pinctrl には何も見えず、衝突は検出され
ません。両方が GPIO 16/6/5 を持てば、モータ電源が入った状態で車輪が逆に回り得ます。
そのため Pi 4 のバックエンドは rtmouse が載っていると **configure を拒否します**
（`allow_rtmouse: true` で上書きできますが、通常は上のとおり壊れます）。

## 中身

| ファイル | 何を持つか |
| --- | --- |
| `node.py` | ROS に見える面。lifecycle・`cmd_vel`・`odom`・TF・`motor_power`・オドメトリの積分。レジスタもチップ名も出てこない |
| `backend.py` | 両機種で共通の手順（GPIO → PWM → I2C の順で掴む）と、機種の判定 |
| `pi4.py` | BCM2711 の同定（`pinctrl-bcm2835` / `fe20c000.pwm`）と rtmouse の排除 |
| `pi5.py` | RP1 の同定（`pinctrl-rp1` / `98000.pwm`） |
| `gpio.py` | gpiochip キャラクタデバイス（v1 uAPI）。libgpiod は使わない |
| `pwm.py` | `/sys/class/pwm` 経由のハードウェア PWM |
| `i2c.py` | パルスカウンタ（`I2C_RDWR` の write+read 結合転送） |

機種差は**チップの同定だけ**です。ピン番号・PWM チャネル・I2C アドレスは制御基板
側の性質なので両機種で同じ、`model: auto` は device-tree（`/proc/device-tree/`）と
`/proc/cpuinfo` から SoC を見て決めます。それでも全部パラメータに出してあるので、
実機で違っていればコードを触らずに直せます。

外部の Python 依存はありません（libgpiod も smbus2 も使わない）。コンテナのイメージに
どちらも入っておらず、足すと `docker compose build` からやり直しになるためです。

## パラメータと前提

パラメータは `src/autonomous_nav/config/robot/raspicat_driver.yaml`（全キーを既定値の
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
（`src/autonomous_nav/config/README.md`）。

ここではユーザ空間の `ioctl` がエラーを返して戻るだけです。カウンタの読み出しは
専用のコールバックグループで走り、連続 `counter_error_limit` 回失敗すると `cmd_vel`
の積分に落ち、`counter_retry_period` ごとに再試行して、応答が戻れば自動でエンコーダに
復帰します。詰まっているあいだも `cmd_vel` → モータの経路は生きています。

ただし無害ではありません。I2C が 1 回タイムアウトすると、その周期の `odom` と TF が
バスのタイムアウト（1 秒前後）だけ遅れます。実機で I2C エラーが出るようなら
`counter_error_limit` を 1〜2 に下げてください。

## 実機での確認

**まだハードウェアで走らせていません。** 最初のベンチで見るべきものは機種ごとに
[`docs/setup/raspberry-pi-4.md`](../../docs/setup/raspberry-pi-4.md) と
[`docs/setup/raspberry-pi-5.md`](../../docs/setup/raspberry-pi-5.md) の表にあります。
