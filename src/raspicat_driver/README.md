# raspicat_driver

Raspberry Pi Cat の本体ドライバの**自前実装**。Pi 4 と Pi 5 の両方に対応し、
モータ経路（ステップクロック・方向・モータ電源・パルスカウンタ）をユーザ空間から
直接扱います。カーネルモジュール（rtmouse）は使いません。

`robot_bringup.launch.py` の `driver:=original` で立ちます。**リポジトリの標準はこちら**で、
Docker の入口（`.env` の `COMPOSE_FILE`）も `compose.original.yaml` を指しています。
`driver:=` という引数そのものの既定値だけは `raspimouse`（公式実装）のままなので、
`robot_bringup.launch.py` を手で叩くときは明示してください。

```bash
ros2 launch daifuku_bringup robot_bringup.launch.py driver:=original
ros2 launch daifuku_bringup robot_bringup.launch.py driver:=original model:=pi4
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
| モータ電源の状態 | サービスのみ（出さない） | `/motor_power_state`（`std_msgs/Bool`、latch） |
| カーネルモジュール | 要 rtmouse | 不要 |

上に見せる契約は公式実装を**含みます**。`cmd_vel` と `/leds`（`raspimouse_msgs/Leds`）と
`/buzzer`（`std_msgs/Int16`、値は Hz・0 で停止）を購読し、`odom` と
`odom -> base_footprint` TF と `/switches`（`raspimouse_msgs/Switches`、true が押下）を
出し、`motor_power` サービスを持つ lifecycle ノードなので、Nav2・EKF・emcl2 の設定は
変わりません。

足してあるのは `/motor_power_state`（`std_msgs/Bool`、latch）1 つだけです。公式実装は
`motor_power` を受けても**どこにも出さない**ので、電源が入っているかを見せたい側
（`joy_teleop` の LED1）は自分が投げた要求を数えるしかなく、`control.sh motor` や RViz の
パネルから変えられると 1 回ぶんずれていました。**購読する側は「無くても動く」ように
書くこと** — `driver:=raspimouse` では誰も出しません。latch なので**ドライバが落ちても
最後の値は残ります**（生きているかは別に見ること）。**実機未確認。**

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
| `node.py` | ROS に見える面。lifecycle・`cmd_vel`・`odom`・TF・`motor_power`（+ `/motor_power_state`）・`/leds`・`/buzzer`・`/switches`・オドメトリの積分。レジスタもチップ名も出てこない |
| `control.py` | `control_mode: closed` の車輪ごとの PI 補正。ROS もハードも触らないので、rclpy の無いホストでも `test/test_control.py` で回せる |
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

パラメータは `config/bringup/robot/raspicat_driver.yaml`（全キーを既定値の
まま列挙。値の由来は [`config/README.md`](../../config/README.md)）。

ホスト側に要るものは 2 つです。どちらも `tools/image/` が入れます。

- `config.txt` の `dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4`
  （`create_image.py`。rtmouse を入れない構成のときだけ入る）
- `tools/image/udev/99-daifuku-raspicat.rules`（`provision.sh`。コンテナは
  uid/gid 1000 で走り補助グループを持たないので、`/sys/class/pwm`・`/dev/gpiochip*`・
  `/dev/i2c-*` の所有者を渡す）

コンテナから動かすときは `docker/raspberrypi/compose.original.yaml` を重ねます
（`/sys/class/pwm` と `/sys/devices` を rw で渡し、`driver:=original` を付ける）。

## 開ループと閉ループ

`control_mode` で選びます。**既定は `closed`** で、`odom` の周期（`odom_hz`）で
読んでいるエンコーダの値をそのまま使って、指令との差を PI で積んだぶんを周波数に
足します。読む相手は同じカウンタなので、専用のタイマも I2C トランザクションも
増えません。指令が来た瞬間に前置きの周波数を書く点は `open` と同じで、補正はその
上に乗るだけです（`_push_command`）。

`open` は上流（`raspimouse`）とまったく同じで、`cmd_vel` を車輪角速度に割って
ステップ周波数を書いて終わりです。エンコーダは `odom` にしか出てきません。

**脱調の対策にはなりません。** パルスの符号は直前に書いた方向線から借りているので、
脱調した車輪も「前進した」カウントを返します。ループはそれを遅れと読んで周波数を
上げ、脱調を悪化させます。効くのはすべりと負荷による定常的な不足で、そのために
`wheel_correction_limit`（既定 2.0 rad/s）を小さく保ってあります。補正が指令と
逆向きに車輪を回すことはありません（`trimmed_speed`）。**荷重をかけて実速度が
落ちるようなら脱調なので、`control_mode` を `open` へ戻してください。**

I2C が落ちて `cmd_vel` 積分に降りているあいだは補正を 0 に戻して前置きだけで走り、
`motor_power` を切ったときと watchdog が働いたときは目標も積分も 0 にします（そうし
ないと、止めた 20 ms 後の周期が止める前の目標を書き戻します）。

ゲイン 3 つ（`wheel_kp` / `wheel_ki` / `wheel_correction_limit`）は毎周期読み直す
ので、走らせたまま `ros2 param set /raspicat_driver wheel_ki 2.0` で詰められます。
`control_mode` だけは `configure` 時に固定です。既定値と `wheel_kp: 0.0` の理由は
[`config/README.md`](../../config/README.md)。

### ゲインは実機で測ってあります（2026-08-08）

物差しは **Mid-360 が見る前方の壁までの距離**です。前後に 1 m の直線で、`ki` を
変えながら後進の脚を刻み、指令・エンコーダ・壁の 3 つの距離を同じ区間で比べました。
エンコーダはいま測っている対象そのものなので、位置決めも安全の仕切りも壁のほうで
やっています（`odom` で仕切ると測定誤差がそのまま余裕を食う。実際に一度、`odom` で
1.35 m と思っていた場所が床の上では 1.52 m あり、後ろの壁に当てました）。

| `wheel_ki` | 5 秒後の定常誤差 | 速度のばらつき |
| --- | --- | --- |
| 0（= `open`） | +22% | 32%（カウンタの粗さ = 下限） |
| 0.5 | +5% | 32% |
| 1.0 | +0.7% | 32% |
| **2.0** | **+0.4%** | **34%** |
| 3.0 | +0.04% | 37% |
| 4.0 | +0.1% | 45% |
| 8.0 | +15%（発振） | 79% |

`ki` は 3 と 4 のあいだで崩れはじめるので、既定はその半分の **2.0** にしてあります。
**この表は `steps_per_revolution: 570` のときの測定です。** 447 へ直すとループ利得が
2 割落ちる（開ループの `measured / cmd` が 1.23 → 1.00 になる）ので、崩れる境目は
`ki` 8 から 10 前後へ**上がり**ます。余裕は増えるほうなので詰め直しは要りません。
時定数は 1/(`ki` × 利得) なので 0.41 → 0.50 秒になります。`kp` は 0.1 では何も変わらず、0.3 でばらつきが 1.4 倍に
なって定常値も暴れました。**カウンタは `odom_hz` の 3 周期に 1 度、12 パルス前後を
まとめて返す**ので、1 周期の量子化は 0.85 rad/s あります。比例項はこれを毎周期
そのまま周波数へ出すので、`wheel_kp` は 0.0 のままにしてください。

## I2C が詰まっても固着しません

rtmouse は I2C が 1 回タイムアウトするとカーネルの mutex を握ったままになり、
`/dev/rtcounter_*` を読む者が全員 D 状態で固着してリブートでしか復旧しません
（`config/README.md`）。

ここではユーザ空間の `ioctl` がエラーを返して戻るだけです。カウンタの読み出しは
専用のコールバックグループで走り、連続 `counter_error_limit` 回失敗すると `cmd_vel`
の積分に落ち、`counter_retry_period` ごとに再試行して、応答が戻れば自動でエンコーダに
復帰します。詰まっているあいだも `cmd_vel` → モータの経路は生きています。

ただし無害ではありません。I2C が 1 回タイムアウトすると、その周期の `odom` と TF が
バスのタイムアウト（1 秒前後）だけ遅れます。実機で I2C エラーが出るようなら
`counter_error_limit` を 1〜2 に下げてください。

## 実機での確認

2026-08-04 に Pi 5 + Raspberry Pi Cat の HAT で、車輪を浮かせたまま `cmd_vel` →
モータ → エンコーダの一巡を確かめました。**2026-08-08 に床へ降ろして走らせ**、閉
ループのゲインを詰めて（上の表）、`pulses_per_revolution` と `steps_per_revolution`
を Mid-360 の壁で検算しました。Pi 4 では未確認です。残っているものは機種ごとに
[`docs/setup/raspberry-pi-4.md`](../../docs/setup/raspberry-pi-4.md) と
[`docs/setup/raspberry-pi-5.md`](../../docs/setup/raspberry-pi-5.md) の表にあります。

脱調は起きませんでした（0.10〜0.40 m/s、平坦な床、無積載）。閉ループで実速度が
落ちる兆候も無く、`ki` を上げたときに崩れるのは発振であって脱調ではありません。
**荷重をかけた状態は未確認**のままです。

### 較正（2026-08-08 に床の上で測り直し）

車輪を浮かせて取った較正は、床の上では合いませんでした。前方の壁を物差しに、
**実速度 0.13〜0.51 m/s** で 20 脚を測った結果です。

- **エンコーダは実距離より少なく数えます**（実速度 0.13 m/s で −7.7%、0.51 m/s で
  −3.0%）。`pulses_per_revolution` を **1118 → 1073** にしました。**その割合が速度で
  変わる**ので、1 つの値では全速度に合いません（下記）。1073 は巡航帯 0.20〜0.40 m/s に
  合わせた値で、そこでは +0.4%、0.13 m/s では **odom が 4% 少なく**出ます。
  **向きに注意** — `pulses_per_revolution` を下げると odom は**大きく**なります。
- **指令より 27% 余計に走っていました**（`cmd / 実距離` = 0.785。実速度 0.19 m/s 以上
  では速度によらない、きれいな定数）。`steps_per_revolution` を **570 → 447** に
  しました。指令 0.10 m/s だけ 0.808 と高いのは動き出しの損なので、そこには合わせて
  いません。**`cmd_vel` の意味が変わる変更です** — 直す前は 0.4 m/s の指令で 0.51 m/s
  出ていたので、Nav2 の速度まわり（`controller_server.yaml` の `max_vel_x` と
  `velocity_smoother` の加減速）は実機で見直してください。

**この表は `1118 / 570` で測った値です。** 較正を入れた後は指令 = 実速度になるので、
同じ**指令**値では再現しません（横軸は実速度で読むこと）。

| 実速度 [m/s] | 0.13 | 0.19 | 0.26 | 0.32 | 0.38 | 0.51 |
| --- | --- | --- | --- | --- | --- | --- |
| 当時の指令 [m/s] | 0.10 | 0.15 | 0.20 | 0.25 | 0.30 | 0.40 |
| `odom / 実距離` | 0.923 | 0.943 | 0.950 | 0.966 | 0.968 | 0.970 |

**`odom = 0.985 × 実距離 − 0.0077 × 秒` が 6 点すべてに 0.5% で乗ります**
（= `0.985 − 0.0077/実速度`）— 距離ではなく**時間**に比例した取りこぼしで、50 Hz なら
1 周期あたり 0.27 カウントにあたります。`(raw - last) & 0xFFFF` の計算自体は整数で
取りこぼしませんから、疑うのは**読み出しの側**です。

**確かめ方**は `odom_hz` を 25 へ半減して測り直すこと。**いまの `1073 / 447` の構成で
言うと**、実速度 0.13 m/s の `odom / 実距離` は 0.962 で、読み出しごとの取りこぼしなら
半減して **0.995** へ動き、物理現象なら 0.962 のままです。**取りこぼしを直せたなら
`pulses_per_revolution` は 1101** で、そのとき全速度で合います（いまの 1073 は
取りこぼしごと巡航帯に合わせ込んだ値なので、直したら入れ直すこと）。

**閉ループはこのうち片方を隠します。** `closed` はエンコーダが指令どおりになる
ように回すので、実速度は `steps_per_revolution` ではなく
`pulses_per_revolution` の狂いのぶんだけずれます（測定でも `cmd / 実距離` が
`open` の 0.785 から `ki=2` で 0.878 へ寄りました）。速度を正しくしたいなら
直すのは**エンコーダ側**です。

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
