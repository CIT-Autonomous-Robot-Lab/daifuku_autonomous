# ROS 2ネットワーク

Raspberry Pi Catとナビゲーション環境はDDSで直接通信します。コンテナまたはPCから機体へIP到達できることに加え、DDSの探索通信が通る必要があります。

## 共通設定

両側で`ROS_DOMAIN_ID`を同じ値にします。`docker/compose.yaml`と
`docker_dev/compose.yaml`の既定はどちらも`90`です。

```bash
export ROS_DOMAIN_ID=90
export ROS_LOCALHOST_ONLY=0
```

Raspberry Pi上のネイティブROSノードとDocker内のFast DDSを確実に接続するため、
両Composeでは`network_mode: host`を使用し、コンテナ側の
`FASTDDS_BUILTIN_TRANSPORTS`を`UDPv4`に固定します。

次も確認してください。

- PCと機体が同じネットワーク、または相互にルーティング可能
- ファイアウォールがDDSのUDPとマルチキャストを許可
- 両側で互換性のあるRMW実装を使用
- VPNや複数NICがDDS探索を妨げていない

## Docker Desktop

`docker/compose.yaml`と`docker_dev/compose.yaml`は`network_mode: host`を使います。Docker Desktop 4.34以降で、Settings > Resources > Networkの「Enable host networking」を有効にしてください。

WindowsファイアウォールではDocker Desktop、WSL、ROS 2で使用するネットワークの通信を許可します。

Docker Desktopのhost networkingはL4実装で、コンテナからWindowsホストの物理NICへ
直接bindできません。このためpingやユニキャストUDPが通っても、Raspberry Pi側との
DDSマルチキャスト探索が成立しない場合があります。ライブDDS接続が必要な開発では、
ネイティブUbuntuのDocker Engine、またはmirrored networkingのWSL2内で直接動かす
Docker Engineを推奨します。Docker Desktop環境はrosbag再生によるデバッグにも利用できます。

WSL2からGUI付き開発コンテナを使う場合は、Windows 11 22H2以降のmirrored networkingを推奨します。`%UserProfile%\.wslconfig`の例:

```ini
[wsl2]
networkingMode=mirrored
firewall=true
```

変更後は`wsl --shutdown`を実行し、Docker Desktopを再起動します。

## 専用Ethernetで機体を接続する

`docker_dev/`には、専用NICを固定IPで設定する補助スクリプトがあります。DHCP/NATは
使用しません。社内LANなど別用途のNICを選ばないでください。

| 機器 | 固定IP |
|---|---|
| Windows / Linuxホスト | `192.168.1.3/24` |
| Podman Hyper-V VM | `192.168.1.2/24` |
| Raspberry Pi Cat | `192.168.1.50/24` |
| Livox Mid-360 | `192.168.1.108/24` |

Piの`/etc/netplan/99-livox.yaml`では、`eth0`に`dhcp4: false`と
`192.168.1.50/24`を明示します。

Linux:

```bash
export RASPICAT_ETHERNET_IF=enp3s0
bash docker_dev/tools/up.sh
```

Linux側はNetworkManagerプロファイル`raspicat-docker-dev`を作り、
`192.168.1.3/24`を設定します。終了後に戻す場合:

```bash
bash docker_dev/tools/network-linux.sh down "$RASPICAT_ETHERNET_IF"
```

Windows PowerShell:

```powershell
.\docker_dev\tools\up.ps1
# 自動判定できない場合
.\docker_dev\tools\up.ps1 -EthernetAlias "vEthernet (RasPiCat External)"
```

Windows用スクリプトは既存のICS共有を解除し、旧`OpenDHCPServer`サービスがあれば
停止・無効化して、ホストへ`192.168.1.3/24`を設定します。
固定IPを解除する場合は管理者PowerShellで実行します。

```powershell
.\docker_dev\tools\network-windows.ps1 -Mode Disable `
  -EthernetAlias "vEthernet (RasPiCat External)"
```

通常の接続先は`ssh ubuntu@192.168.1.50`です。

## 接続を確認する

```bash
ros2 topic list
ros2 node list
ros2 topic echo /odom --once
```

Docker環境では:

```bash
docker compose -f docker/compose.yaml exec ros2 \
  /ros_entrypoint.sh ros2 topic list
```

トピックが見えない場合は[トラブルシューティング](../usage/troubleshooting.md)を参照してください。
