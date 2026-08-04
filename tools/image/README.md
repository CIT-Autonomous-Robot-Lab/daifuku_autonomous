# SDカードイメージの作成とセットアップ

`tools/image/`は、Raspberry Pi 4 / 5用のSDカードを一から用意するためのスクリプトです。
ベースイメージの取得からカードへの書き込み、初回起動時のホスト側セットアップまでを
まとめて扱います。

すでに動いているマシンへROS 2環境を足したいだけなら[`tools/setup/`](../setup/)を
使ってください。こちらは「まだOSが入っていないカード」が対象です。

## ファイル構成

| ファイル | 実行場所 | 用途 |
|---|---|---|
| `create_image.py` | 手元のPC（Windows / Linux / macOS） | ベースイメージの取得・検証・書き込みと、ブートパーティションへの設定注入 |
| `provision.sh` | Raspberry Pi | 初回起動時のホスト側セットアップ。単体でも実行できる |

`create_image.py`はPython 3.8以上の標準ライブラリだけで動きます。追加のパッケージは
不要です。`provision.sh`は`create_image.py`がcloud-initへ埋め込むので、Piへ手で
コピーする必要はありません。

## 何を用意するのか

ナビゲーション一式は`docker/raspberrypi/`のイメージに入っています。ここで面倒を
見るのは、コンテナの中に入れられないものだけです。

- Ubuntu Server（arm64）とcloud-initによる初期設定（ユーザー、SSH鍵、固定IP）
- Docker本体とcomposeプラグイン
- rtmouseカーネルモジュール（コンテナからは`insmod`できない）
- DDS向けのカーネルパラメータと、Fast DDSプロファイルの指定
- スワップ（価値反復プランナがPi 4の4GBに収まらないことがある）
- 時刻同期、ユーザーの所属グループ、本リポジトリの取得

## 使い方

### 1. 書き込み先を確認する

```bash
python3 tools/image/create_image.py devices
```

```
書き込み先の候補:
  [0] \\.\PhysicalDrive0   953.9 GiB  KINGSTON OM8PGP41024Q-A0  (内蔵, システムディスク)
  [2] \\.\PhysicalDrive2    59.5 GiB  Generic MassStorageClass  (リムーバブル)
```

### 2. カードを作る

管理者（Windows）またはroot（Linux / macOS）で実行します。

```bash
sudo python3 tools/image/create_image.py all \
  --model pi4 \
  --device 2 \
  --ssh-key ~/.ssh/id_ed25519.pub \
  --hostname raspicat \
  --ip 192.168.1.50 \
  --gateway 192.168.1.1
```

Windowsでは管理者権限のPowerShellから実行します。

```powershell
python tools\image\create_image.py all --model pi4 --device 2 --ssh-key $HOME\.ssh\id_ed25519.pub
```

> **Windowsでは書き込む前にカードのボリュームを外します。**マウント中のボリュームに
> 属するセクタへの生書きをWindowsが拒否するためで、`flash`はドライブレターごとに
> ロックとディスマウントを行い、書き終わるまでハンドルを開いたままにします（閉じると
> 再マウントされて途中から弾かれる）。エクスプローラやウイルス対策ソフトがカードを
> 開いているとロックに失敗しますが、それだけなら警告のうえ続行します。ディスマウント
> まで失敗した場合は書けないので、カードを開いているウィンドウを閉じてやり直して
> ください。

`all`は次を順に行います。

1. `fetch` — Ubuntuのcdimageから`SHA256SUMS`を読み、最新のポイントリリースを
   ダウンロードしてSHA256を検証し、展開する（キャッシュ済みなら再利用）
2. `flash` — カードへ生書きする
3. `configure` — ブートパーティションへ`user-data`、`network-config`、`meta-data`を
   書き、`config.txt`と`cmdline.txt`を更新する

機種ごとの前提と起動後の確認手順は[Raspberry Pi 4で動かす](../../docs/setup/raspberry-pi-4.md)と
[Raspberry Pi 5で動かす](../../docs/setup/raspberry-pi-5.md)にあります。

> 初回起動のプロビジョニングはapt、git、Dockerの取得でインターネットへ出ます。
> 固定IPだけを書いてデフォルトルートが無いと、起動はするのに中身が空のPiが
> できあがります。`--gateway`を省略した場合は`--ip`と同じサブネットの`.1`を
> 仮定するので、ルータのアドレスが違うときは必ず指定してください。ロボットLANが
> 閉じている場合は`--wifi-ssid`で保守用の経路を用意するか、あとから
> `provision.sh`を手で実行します。
>
> **ロボットLANにルータが無いなら`--gateway none`も要ります。**`--wifi-ssid`を足す
> だけでは直りません。eth0の静的デフォルトルートはwlan0のDHCPルート（メトリック600）
> より優先されるので、存在しない`.1`宛のまま残ると外へ出られず、`provision.sh`は
> 「ネットワークの疎通を待つ」で足踏みしてapt・git・Dockerが全部失敗します。

### 3. Piを起動する

カードをPiに挿して電源を入れると、cloud-initが`provision.sh`を実行します。10〜20分
ほどかかります。

```bash
ssh ubuntu@192.168.1.50
sudo tail -f /var/log/daifuku-provision.log
```

終わったら再起動して（グループとカーネルモジュールの反映）、Dockerイメージを
ビルドします。Pi 4では数時間かかるので、時間があるときに回してください。

```bash
sudo reboot
# 再ログイン後
cd ~/daifuku_autonomous
BUILD_JOBS=1 docker compose -f docker/raspberrypi/compose.yaml build
docker compose -f docker/raspberrypi/compose.yaml up -d
```

`--build-on-first-boot`を付けておくと初回起動のプロビジョニングで最後まで
ビルドしますが、その間Piは張り付きます。

## 主なオプション

| オプション | 既定値 | 用途 |
|---|---|---|
| `--model {pi4,pi5}` | `pi4` | 対象機種。ベースイメージとrtmouseの扱いが変わる |
| `--release` | pi4は`22.04`、pi5は`24.04` | Ubuntuのリリース |
| `--device` | （必須） | 書き込み先。`devices`のIDを渡す |
| `--hostname` | `raspicat` | ホスト名 |
| `--user` | `ubuntu` | 作成するユーザー。uidは1000固定 |
| `--ssh-key` | なし | SSH公開鍵。ファイルパスでも鍵そのものでもよい。複数指定可 |
| `--password` | なし | パスワード。指定するとパスワード認証も有効になる |
| `--ip` | `192.168.1.50` | eth0の固定IP。`dhcp`でDHCP |
| `--gateway` / `--dns` | `--ip`と同一サブネットの`.1` | デフォルトゲートウェイとDNS。`none`で置かない |
| `--wifi-ssid` / `--wifi-password` | なし | 保守用wlan0 |
| `--ros-domain-id` | `90` | `ROS_DOMAIN_ID` |
| `--build-jobs` | pi4は`1`、pi5は`2` | `docker compose build`の並列数 |
| `--swap-mb` | pi4は`2048`、pi5は`0` | スワップファイル。`0`で作らない |
| `--no-rtmouse` / `--with-rtmouse` | pi4のみ有効 | rtmouseカーネルモジュールの導入 |
| `--repo-url` / `--repo-ref` | `origin` / 手元のブランチ | Pi側で`git clone`するリポジトリ |
| `--no-repo-archive` | 無効 | リポジトリのスナップショットを同梱しない |
| `--build-on-first-boot` | 無効 | 初回起動でDockerイメージまでビルドする |
| `--dry-run` | 無効 | 書き込まずに動作だけ確認する |
| `--force` / `--max-size-gb` | `128` | 安全確認の緩和 |

## 個別のサブコマンド

`all`が途中で止まったときや、Raspberry Pi Imagerなど他のツールで焼いたカードへ
設定だけ入れたいときに使います。

```bash
# ベースイメージだけ取っておく
python3 tools/image/create_image.py fetch --model pi5

# 書き込みだけやり直す
sudo python3 tools/image/create_image.py flash --device 2 --model pi4

# 既に焼いてあるカードへ設定を入れ直す（Windowsならドライブレター）
python3 tools/image/create_image.py configure --model pi4 --boot-dir E:\ \
  --ssh-key ~/.ssh/id_ed25519.pub
```

`configure`は`--boot-dir`を省略するとブートパーティション（`config.txt`と
`cmdline.txt`があるFATパーティション）を自動で探します。書き込み直後にOSが
パーティションを認識していないと見つからないので、その場合はカードを挿し直してから
`--boot-dir`付きで実行してください。

`configure`は何度実行しても同じ結果になります。`config.txt`はマーカーで囲んだ
ブロックだけを置き換え、`cmdline.txt`は不足しているトークンだけを足します。

## リポジトリの持ち込み方

本リポジトリは非公開なので、Piから素の`git clone`はできません（認証を求められて
失敗します）。そのため`configure`は、リポジトリのスナップショットを
`daifuku-repo.tar.gz`（約1.6MB）としてブートパーティションへ同梱します。

`provision.sh`はまず`git clone`を試し、失敗したらこのスナップショットを展開します。
どちらの経路でも同じリビジョンになるよう、`--repo-ref`は両方に使われます。省略した
場合は手元でチェックアウト中のブランチです。スナップショットに入るのは**コミット
済みの内容だけ**で、未コミットの変更があるときは警告を出します。

- 認証情報をPiに置いてある、あるいはリポジトリを公開している場合は、そのまま
  `git clone`が成功し、gitの履歴付きのワークスペースになります
- 失敗した場合はスナップショットの展開になります。**gitの履歴は付きません。**
  あとからgitの履歴が欲しくなったら、SSH鍵などを用意して手で入れ替えてください

同梱したくない場合は`--no-repo-archive`を付けます。

## 安全のための確認

生書きは相手を間違えると復旧できません。`flash`は次を確認してから書き込みます。

- 管理者 / root権限があること
- 対象がシステムディスクでないこと
- 対象がリムーバブルとして認識されていること
- 容量が`--max-size-gb`（既定128GB）以下であること
- イメージが対象の容量に収まること
- `yes`と入力しての確認（`-y`で省略）

SDカードリーダーによっては内蔵ディスク扱いになります。相手が確実な場合だけ
`--force`を付けてください。

## Raspberry Pi 5について

- **Ubuntu 22.04はPi 5で起動しません。** Canonicalはjammyへのバックポートを
  予定していません。`--model pi5`の既定はUbuntu 24.04です。`--model pi5
  --release 22.04`はエラーにします。
- **rtmouseカーネルモジュールはPi 5に対応していません。** rt-net/RaspberryPiMouse
  が公式にサポートするのはPi 4 Bまでです（BCM2711のレジスタを`ioremap`しますが、
  Pi 5ではGPIO/PWMがRP1側にあります）。`--model pi5`では既定で導入を行いません。
  代わりに`raspicat_driver`パッケージがモーターとエンコーダをユーザー空間から
  直接扱います（`driver:=original`）。`--model pi5`では`config.txt`にPWMオーバレイを
  書きます。手順は[Raspberry Pi 5で動かす](../../docs/setup/raspberry-pi-5.md)。

## 本体ドライバとconfig.txt

`config.txt`に入るオーバレイは、機種ではなく**rtmouseを入れるか**で決まります。

| | rtmouseあり（`--model pi4`の既定） | rtmouseなし（`--no-rtmouse` / `--model pi5`） |
| --- | --- | --- |
| 本体ドライバ | 公式実装 `driver:=raspimouse` | 自前実装 `driver:=original` |
| `config.txt` | `anyspi`（A/D用）。PWMはrtmouseが直書き | `dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4` |

`udev/99-daifuku-raspicat.rules`（PWM・gpiochip・i2c-devの所有者を1000:1000にする）は
`provision.sh`が機種によらず導入します。公式実装では使われないだけで害はありません。

**Pi 5ではもう1枚オーバレイが要ります。** `pwm-2chan`はピンを切り替えるだけで
クロックを設定せず、RP1の`clk_pwm0`は親の決まらないレート0のままになるので、
そのままでは`period`の書き込みがEINVALで弾かれます。`overlays/daifuku-pwm-clk.dts`が
親を`xosc`に名指しします。`dtc`が開発ホストにあるとは限らないので、コンパイルと
`config.txt`への`dtoverlay=`行の追記はどちらも`provision.sh`が機体で行います
（[`docs/setup/raspberry-pi-5.md`](../../docs/setup/raspberry-pi-5.md)）。

ROS 2 Humbleのネイティブ環境（[`tools/setup/`](../setup/)）はUbuntu 22.04が
前提なので、Pi 5では使えません。Pi 5ではDockerを使ってください。

## provision.shを単体で使う

すでに動いている機体へ後から適用したり、失敗した箇所をやり直したりできます。

```bash
sudo bash tools/image/provision.sh
```

設定は`/etc/daifuku/provision.env`から読み、環境変数で上書きできます。

```bash
sudo DAIFUKU_SWAP_MB=4096 DAIFUKU_WITH_RTMOUSE=0 bash tools/image/provision.sh
```

各手順は失敗しても止まらず、最後に未了の一覧を出します。ログは
`/var/log/daifuku-provision.log`に残ります。

## 生成されるもの

ブートパーティション（Pi起動後は`/boot/firmware/`）

| ファイル | 内容 |
|---|---|
| `user-data` | cloud-config。ユーザー、SSH鍵、`provision.sh`本体（base64）、`runcmd` |
| `network-config` | netplan形式のネットワーク設定 |
| `meta-data` | `instance-id`と`local-hostname` |
| `config.txt` | `dtparam=i2c_arm` / `spi` と`i2c_baudrate=62500`。rtmouseありはA/D用の`anyspi`オーバレイ、rtmouseなし（`--no-rtmouse` / `--model pi5`）は`dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4` |
| `cmdline.txt` | `cgroup_enable=memory cgroup_memory=1` |
| `daifuku-repo.tar.gz` | 手元のリポジトリのスナップショット（`--no-repo-archive`で省略） |

Pi側

| パス | 内容 |
|---|---|
| `/etc/daifuku/provision.env` | `provision.sh`が読む設定値 |
| `/etc/sysctl.d/60-ros2-dds.conf` | UDP受信バッファ16MB |
| `/etc/sysctl.d/61-daifuku-swappiness.conf` | `vm.swappiness=10` |
| `~/daifuku_autonomous` | 本リポジトリ |
| `~/daifuku_autonomous/docker/raspberrypi/.env` | `ROS_DOMAIN_ID`と`BUILD_JOBS` |
| `~/.bashrc` | `ROS_DOMAIN_ID`、`FASTRTPS_DEFAULT_PROFILES_FILE`などのブロック |
| `/var/log/daifuku-provision.log` | プロビジョニングのログ |

`config.txt`の`i2c_baudrate=62500`と`~/.bashrc`のFast DDSプロファイル指定には
理由があります。前者はrtmouseのパルスカウンタ（I2C 0x10 / 0x11）のタイムアウト
対策、後者はホストとコンテナでプロファイルが食い違うと片側だけが共有メモリを使い、
通信が静かに止まるためです。詳細は
[`docker/raspberrypi/README.md`](../../docker/raspberrypi/README.md)を参照してください。

`--ip`を既定値から変えた場合、`provision.sh`は
`docker/raspberrypi/fastdds_udp_whitelist.xml`のIPも書き換えます。このXMLには
ロボットLANのIPが直接書いてあるためです。書き換えは`git diff`に出ます。
