# ROS 2ネットワーク

Raspberry Pi Catとナビゲーション環境はDDSで直接通信します。コンテナまたはPCから機体へIP到達できることに加え、DDSの探索通信が通る必要があります。

## 共通設定

両側で`ROS_DOMAIN_ID`を同じ値にします。`docker/raspberrypi/compose.common.yaml`と
`docker/dev/compose.yaml`の既定はどちらも`90`です。

```bash
export ROS_DOMAIN_ID=90
export ROS_LOCALHOST_ONLY=0
```

Raspberry Pi上のネイティブROSノードとDocker内のFast DDSを確実に接続するため、
両Composeとも`network_mode: host`を使用します。トランスポートの設定は環境ごとに
異なります。

| 環境 | Fast DDSの設定 |
|---|---|
| `docker/raspberrypi/` | `FASTRTPS_DEFAULT_PROFILES_FILE`でXMLプロファイルを指定し、UDPを特定インターフェースへ限定したうえでSHMを併用（[下記](#raspberry-pi本体でのdds設定)） |
| `docker/dev/` | `FASTDDS_BUILTIN_TRANSPORTS=UDPv4`。別ホストであるPi上のノードを同一ホストの相手と誤認させないため、SHMを使わない |

次も確認してください。

- PCと機体が同じネットワーク、または相互にルーティング可能
- ファイアウォールがDDSのUDPとマルチキャストを許可
- 両側で互換性のあるRMW実装を使用
- VPNや複数NICがDDS探索を妨げていない

## Raspberry Pi本体でのDDS設定

Pi本体でネイティブのROS 2ノードを動かし、同じPi上の`docker/raspberrypi/`コンテナと通信する構成
では、ホストとコンテナで同じFast DDSプロファイル`docker/raspberrypi/fastdds_udp_whitelist.xml`
を使います。コンテナ側は`compose.common.yaml`がマウントと環境変数を設定するため、追加の
作業はホスト側だけです。

```bash
# Pi本体の ~/.bashrc へ追記
export FASTRTPS_DEFAULT_PROFILES_FILE=/path/to/daifuku_autonomous/docker/raspberrypi/fastdds_udp_whitelist.xml
```

このプロファイルは2つの実測問題に対処しています。

- **UDPインターフェースの限定**: 限定しないと、各参加者はwlan0側（別セグメント）の
  ロケータまで広告します。相手から到達できないロケータへ送信し続けるぶん
  UDPバッファが逼迫し、高負荷時にはノードが現れたり消えたりしました。whitelistでは、
  ループバックとロボットLANのアドレスだけを広告します。
- **同一ホスト通信のSHM化**: ナビゲーションスタック、LiDARパイプライン、機体
  ドライバで約20個の参加者をUDPのみで動かすと、購読者ごとの`sendmsg`でカーネルが
  飽和し（Pi 4でsys 57%、load 24）、TFのタイムスタンプが20秒以上遅れてゴールが
  中断しました。同一ホスト内はSHMへ切り替えています。

そのため、次の3点が前提になります。

- `docker/raspberrypi/compose.common.yaml`の`ipc: host`（`/dev/shm`をホストと共有する）
- `docker/raspberrypi/compose.common.yaml`の`user: "1000:1000"`。Fast DDSはSHMセグメントを0644で作るため、
  ホスト側ROSプロセスとuidを揃えないと互いのポートを開けません
- whitelist内の`192.168.1.50`は、Piの固定IPをそのまま書いたものです。ロボットLANの
  アドレスが異なる場合はXMLを書き換えてください

`docker/dev/`は別ホスト（PC）で動くため、SHMは使わず`FASTDDS_BUILTIN_TRANSPORTS=UDPv4`
のままです。

## Docker Desktop

`docker/raspberrypi/compose.common.yaml`と`docker/dev/compose.yaml`は`network_mode: host`を使います。Docker Desktop 4.34以降で、Settings > Resources > Networkの「Enable host networking」を有効にしてください。

WindowsファイアウォールではDocker Desktop、WSL、ROS 2で使用するネットワークの通信を許可します。

Docker Desktopのhost networkingはL4実装で、コンテナからWindowsホストの物理NICへ
直接bindできません。このためpingやユニキャストUDPが通っても、Raspberry Pi側との
DDSマルチキャスト探索が成立しない場合があります。ライブDDS接続が必要な開発では、
ネイティブUbuntuのDocker Engine、またはmirrored networkingのWSL2内で直接動かす
Docker Engineを推奨します。Docker Desktop環境でも、rosbagを再生してのデバッグはできます。

WSL2からGUI付き開発コンテナを使う場合は、Windows 11 22H2以降のmirrored networkingを推奨します。`%UserProfile%\.wslconfig`の例:

```ini
[wsl2]
networkingMode=mirrored
firewall=true
```

変更後は`wsl --shutdown`を実行し、Docker Desktopを再起動します。mirroredが失敗する
環境では[WSL2から直接つなぐ](#wsl2から直接つなぐ)のbridgedを使います。

## 専用Ethernetで機体を接続する

`docker/dev/`には、専用NICを固定IPで設定する補助スクリプトがあります。DHCP/NATは
使用しません。社内LANなど別用途のNICを選ばないでください。

| 機器 | 固定IP |
|---|---|
| Windows / Linuxホスト（このセグメントのゲートウェイ） | `192.168.1.1/24` |
| Podman Hyper-V VM | `192.168.1.2/24` |
| WSL2（bridged、[下記](#wsl2から直接つなぐ)） | `192.168.1.3/24` |
| Raspberry Pi Cat | `192.168.1.50/24` |
| Livox Mid-360 | `192.168.1.108/24` |

Piの`/etc/netplan/99-livox.yaml`では、`eth0`に`dhcp4: false`と
`192.168.1.50/24`を明示します。

Linux:

```bash
export RASPICAT_ETHERNET_IF=enp3s0
bash docker/dev/tools/linux/up.sh
```

Linux側はNetworkManagerプロファイル`raspicat-docker-dev`を作り、
`192.168.1.1/24`を設定します。終了後に戻す場合:

```bash
bash docker/dev/tools/linux/network.sh down "$RASPICAT_ETHERNET_IF"
```

Windows PowerShell:

```powershell
.\docker\dev\tools\windows\up.ps1
# 自動判定できない場合
.\docker\dev\tools\windows\up.ps1 -EthernetAlias "vEthernet (RasPiCat External)"
```

Windows用スクリプトは既存のICS共有を解除し、旧`OpenDHCPServer`サービスがあれば
停止・無効化して、ホストへ`192.168.1.1/24`を設定します。
固定IPを解除する場合は管理者PowerShellで実行します。

```powershell
.\docker\dev\tools\windows\network.ps1 -Mode Disable `
  -EthernetAlias "vEthernet (RasPiCat External)"
```

通常の接続先は`ssh ubuntu@192.168.1.50`です。

## WSL2から直接つなぐ

WSL2の中でROS 2ノード（RVizなど）を動かして機体のトピックを見る場合、既定のNATでは
接続できません。WSLの`eth0`は`172.31.x.x`で、Windowsホストが`192.168.1.1/24`を持つため
**pingとsshはSNATで通ります**。しかしDDSの参加者は自分のロケータとして`172.31.x.x`を
広告しますが、機体のeth0には既定経路が無いので返せません（ホストの`.1`はWSLと
Windows側のためのゲートウェイで、機体はそちらを向いていません）。`ROS_STATIC_PEERS`や初期
ピアを両側に書いても解決しません。**エラーは出ず、トピックだけが見えない**形になります。

[上記](#docker-desktop)のmirrored networkingが使えればそれで解決しますが、ホストに
よっては失敗します。**失敗するとNATではなく`None`にフォールバックする**ため、NICが
1枚も無い状態になります（`lo`に`10.255.255.254/32`だけが付き、`/etc/resolv.conf`も
生成されない）。`wslinfo --networking-mode`で確認できます。

```
CreateInstance/CreateVm/ConfigureNetworking/0x8007054f
Failed to configure network (networkingMode Mirrored), falling back to networkingMode None.
```

この場合はHyper-Vの外部スイッチへ直結します。`vmSwitch`には`Get-VMSwitch`の
`SwitchType`が`External`のものを指定します。

```ini
[wsl2]
networkingMode=bridged
vmSwitch=RasPiCat External
dhcp=false
```

このセグメントにはDHCPもゲートウェイも無いため、アドレスはWSL側で静的に設定します。
`/etc/wsl.conf`の`[boot] command`から次のようなスクリプトを呼びます。NATへ戻したときに
影響しないよう、`bridged`のときだけ動かします。

```sh
#!/bin/sh
[ "$(wslinfo --networking-mode 2>/dev/null)" = "bridged" ] || exit 0
ip link set eth0 up
ip -4 -o addr show dev eth0 | grep -q ' inet ' || ip addr replace 192.168.1.3/24 dev eth0
# 既定経路はWindowsホストへ向ける（下の「bridgedのまま外に出る」）
ip route replace default via 192.168.1.1 dev eth0
```

機体側の`fastdds_udp_whitelist.xml`はループバックとロボットLANのロケータだけを広告
するため、WSLがこのセグメントにいる構成と噛み合います。mirroredの場合はWi-FiやVPNの
ロケータまで広告することになります。

注意点が3つあります。

- **LANケーブルを抜いて持ち歩くときはNATに戻します。** 外部スイッチの上流が消えると
  bridgedのWSLは無通信になります。逆に挿さっているあいだは、次節のとおりホストが
  中継するので戻す必要はありません。
- **`.wslconfig`は全ディストロ共通**です。適用には`wsl --shutdown`が必要で、
  `wsl --terminate <distro>`ではVMが動き続けるため`networkingMode`は変わりません
  （`[boot] command`の再実行には使えます）。
- **`.wslconfig`はVMが起動するたびに読まれます。** アイドルタイムアウトや最後の
  ディストロの終了でVMが落ちれば、`wsl --shutdown`を明示しなくても次の起動で反映
  されます。編集した時点で意図しないタイミングの切り替わりが起こり得ます。

### bridgedのまま外に出る

このセグメントには本物のゲートウェイが無いので、素のbridgedではWSLから外部ネット
ワークへ出られません（aptやgitのたびにNATへ戻す、という運用になります）。**Windows
ホストをこのセグメントのゲートウェイに仕立てると、戻さずに済みます。** 管理者権限の
PowerShellで3つ、いずれも再起動後も残ります。

```powershell
Set-NetIPInterface -InterfaceAlias 'vEthernet (RasPiCat External)' -AddressFamily IPv4 -Forwarding Enabled
Set-NetIPInterface -InterfaceAlias 'Wi-Fi' -AddressFamily IPv4 -Forwarding Enabled
New-NetNat -Name RobotLanNAT -InternalIPInterfaceAddressPrefix 192.168.1.0/24
```

`Wi-Fi`は外向きのインタフェース名に読み替えます。WSL側は上のスクリプトが既定経路を
`192.168.1.1`へ向けます。ロボットLAN内の通信はNATを通らないので、DDSには影響しません。

**DNSは別に塞ぐ必要があります。** bridgedではWSLが`/mnt/wsl/resolv.conf`を生成しない
ため`/etc/resolv.conf`がリンク切れのままになり、**`ping 8.8.8.8`は通るのに名前解決だけが
落ちます**（`curl: (6) Could not resolve host`）。`/etc/wsl.conf`に
`[network] generateResolvConf=false`を足し、`/etc/resolv.conf`を実ファイルで置きます。
ホストのDNSを写すとWi-Fiを移ったときに古いまま残るので、公開リゾルバを書いておくのが
確実です。**この2つはディストロごと**です（eth0と既定経路はVM共通なので、他の
ディストロは経路だけ通って名前解決だけが落ちます）。企業ネットワーク越しだと
外向きの53番が塞がれていることがあり、そのときも同じ症状になります。

```
nameserver 1.1.1.1
nameserver 8.8.8.8
```

つながったら、RVizは`tools/rviz.sh`（引数なしで`navigation.rviz`、`mapping`で地図作成用）
で立てます。機体側のスタックはPiのDockerが持っているので、WSLで建てるのはRVizの
パネルプラグイン（`daifuku_waypoint_manager`）1つだけです——初回だけ2分ほどかかり、
`~/.cache/daifuku_rviz_ws`に入ります（`ROS_DOMAIN_ID`は未設定なら90）。

## 接続を確認する

```bash
ros2 topic list
ros2 node list
ros2 topic echo /odom --once
```

Docker環境では:

```bash
docker compose exec ros2 \
  /ros_entrypoint.sh ros2 topic list
```

トピックが見えない場合は[トラブルシューティング](../usage/troubleshooting.md)を参照してください。
