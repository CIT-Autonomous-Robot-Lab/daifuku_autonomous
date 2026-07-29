# Raspberry Pi Cat development container

公式ドキュメントのPC側環境（Ubuntu 22.04、ROS 2 Humble、`raspicat-pc.repos`）を
Dockerで再現する開発環境です。ロボット用EthernetはDHCPを使用せず、
`192.168.1.0/24`の固定IPで構成します。

## 構成

- ROS 2 Humble Desktop Full
- `CIT-Autonomous-Robot-Lab/raspicat2` のPC用ワークスペース
  （再現性のため確認済みコミット`500a20c`に固定）
- `raspicat_ros`、`raspicat_description`、`raspicat_slam_navigation`、
  `raspicat_speak2`、`raspicat_setup_scripts`
- RVizなどのGUI（Linux X11、WSLg、またはWindows X Server）
- ROS 2通信用のhost network

Raspberry Pi本体のOS、GPIOカーネルドライバ、モータードライバはコンテナ化しません。
それらは実機Raspberry Piへ導入し、PC側コンテナとはEthernet上のROS 2で通信します。

## tools/の構成

`tools/windows/`はWindowsホスト（PowerShell、Podman Hyper-V）、`tools/linux/`は
Linux/WSLホスト（bash）で実行します。`tools/build-workspace.sh`だけはコンテナ内で
動くビルドスクリプトで、イメージへ`build-autonomous`として組み込まれます。

| スクリプト | 用途 | 管理者権限 |
|---|---|---|
| `windows/up.ps1` / `linux/up.sh` | 固定IP設定、X Server起動、Compose起動までの一括実行 | あり（内部で昇格） |
| `windows/shell.ps1` / `linux/shell.sh` | 起動済みコンテナへ入る | なし |
| `windows/network.ps1` / `linux/network.sh` | ホストの固定IPだけを設定・解除 | あり |
| `windows/rviz.ps1` | コンテナ内のRVizをWindows画面へ表示 | なし |
| `windows/pi-ros.ps1` | SSH経由で実機の`ros2`コマンドを実行 | なし |
| `windows/podman-network.ps1` | Podman VMを実機Ethernetへ接続（初回のみ） | あり |
| `windows/common.ps1` | 上記が読み込む共通処理（単体では実行しない） | — |

WSLから`tools/linux/up.sh`を実行した場合のみ、Windows側の固定IP設定のために
`tools/windows/network.ps1`を管理者権限で呼び出します。

## 固定IP

| 機器 | アドレス |
|---|---|
| Windows / Linuxホスト | `192.168.1.3/24` |
| Podman Hyper-V VM | `192.168.1.2/24` |
| Raspberry Pi Cat | `192.168.1.50/24` |
| Livox Mid-360 | `192.168.1.108/24` |

Raspberry Pi Catにつながる専用Ethernet NICを確認してください。起動処理はそのNICの
既存ネットワーク設定を切り替えます。社内LANなどにつながったNICを選択しないでください。

### Raspberry Pi側

Piの`/etc/netplan/99-livox.yaml`を次の内容にします。複数のNetplanファイルはマージされる
ため、cloud-init側に`dhcp4: true`があっても、優先度の高いこのファイルで明示的に
`false`へ上書きします。

```yaml
network:
  version: 2
  renderer: networkd
  ethernets:
    eth0:
      dhcp4: false
      addresses:
        - 192.168.1.50/24
      optional: true
```

```bash
sudo chmod 600 /etc/netplan/99-livox.yaml
sudo netplan generate
sudo netplan apply
ip -brief address show eth0
```

Wi-Fiをインターネット用のデフォルト経路、有線をPi/Livox専用ネットワークとして使用します。

## Linux（Ubuntu）

ホストにDocker Engine、Docker Compose v2、NetworkManagerが必要です。

```bash
sudo apt install network-manager
export RASPICAT_ETHERNET_IF=enp3s0  # NICが1個だけなら省略可
bash docker_dev/tools/linux/up.sh
```

`linux/up.sh`は次の順序で処理します。

1. `raspicat-docker-dev`というNetworkManagerプロファイルを作成
2. ホストを`192.168.1.3/24`に設定（DHCP/NATなし）
3. Dockerイメージをビルドしてコンテナを起動

```bash
ssh ubuntu@192.168.1.50
```

終了後にネットワーク設定も戻す場合:

```bash
bash docker_dev/tools/linux/network.sh down "$RASPICAT_ETHERNET_IF"
```

## Windows + Podman Hyper-V

Windows PowerShellから起動します。`windows/up.ps1`はPodman Hyper-V APIを明示的に使用し、
固定IP設定とVcXsrv Display `:400`の起動後にComposeを実行します。

```powershell
# アダプター名は Get-NetAdapter で確認
.\docker_dev\tools\windows\up.ps1
# 自動判定できない場合
.\docker_dev\tools\windows\up.ps1 -EthernetAlias "vEthernet (RasPiCat External)"
```

管理者権限の確認画面が開き、ICS/DHCP共有を解除してWindows側へ
`192.168.1.3/24`を設定します。旧環境の`OpenDHCPServer`サービスが存在する場合は、
ファイルを削除せずサービスを停止・無効化します。Piは常に`192.168.1.50`です。

実機はWindows側から直接確認できます。

```powershell
ping 192.168.1.50
ssh ubuntu@192.168.1.50
```

固定IPを解除する場合は管理者PowerShellで実行します。

```powershell
.\docker_dev\tools\windows\network.ps1 -Mode Disable `
  -EthernetAlias "vEthernet (RasPiCat External)"
```

`windows/network.ps1 -Mode Static`は競合防止のため既存のICS共有を解除します。

### Docker Desktop / WSL2

Docker Desktopでhost networkingを有効にしてください。WSL2から使う場合は、Windows
11 22H2以降のmirrored networkingも推奨します。`%UserProfile%\.wslconfig`の例:

```ini
[wsl2]
networkingMode=mirrored
firewall=true
```

設定変更後は`wsl --shutdown`を実行し、Docker Desktopを再起動します。
WSL2シェルから起動する場合:

```bash
bash docker_dev/tools/linux/up.sh
```

同じ制約はROS 2 DDSのマルチキャスト探索にも影響します。Docker Desktopのhost
networkingはLinux VM内のL4接続であり、コンテナからWindowsの物理NICへ直接bind
できません。実機のトピックが見えない場合、ライブDDSデバッグには次のいずれかを
使用してください。

- ネイティブUbuntu + Docker Engineで`network_mode: host`を使う
- mirrored networkingのWSL2内へDocker Engineを直接導入し、コンテナから実機側NICが
  見えることを確認して使う
- Piでrosbagを記録し、Docker Desktop側のこの環境で再生してローカルデバッグする

Windows + Docker Desktopでも、ビルド、GDB、RViz、rosbag再生には利用できます。

## コンテナの利用

```bash
bash docker_dev/tools/linux/shell.sh
# PowerShellの場合
.\docker_dev\tools\windows\shell.ps1
```

初回と、C++ソースまたは依存関係を変更した後は、コンテナ内で開発用ワークスペースを
ビルドします。`RelWithDebInfo`と`--symlink-install`を使うため、デバッガ用シンボルを
保持し、launch/configの変更は再ビルドなしで反映されます。

```bash
build-autonomous
source install/setup.bash
ros2 pkg list | grep raspicat
ros2 pkg prefix autonomous_nav
ros2 topic list
```

ワークスペース全体はコンテナの`/workspaces/daifuku_autonomous`へマウントされます。
公式PC環境は`/opt/raspicat2`です。
このリポジトリの`build`、`install`、`log`はDockerボリュームに保存されます。

実機と同じ`ROS_DOMAIN_ID=90`、Fast DDSのUDPv4通信が既定です。実機との通信確認:

```bash
ros2 topic echo --once /odom
ros2 run tf2_ros tf2_echo odom base_footprint
ros2 topic hz /scan
```

Podman Hyper-V VMを実機と同じEthernetへ直接接続すると、Windows画面のRVizから実機の
DDSトピックをライブ表示できます。管理者PowerShellで一度だけ次を実行してください。
既存のPodman NAT用`vsock0`は残したまま、外部NIC `raspi0`へ
`192.168.1.2/24`を設定します。

```powershell
.\docker_dev\tools\windows\podman-network.ps1
```

`compose.yaml`は`network_mode: host`なので、コンテナを起動し直せばPiと同一セグメントで
DDSマルチキャストを使用します。

```powershell
$env:DOCKER_HOST = "npipe:////./pipe/podman-hyperv"
Remove-Item Env:DOCKER_CONTEXT -ErrorAction SilentlyContinue
docker compose -f docker_dev/compose.yaml up -d
# VcXsrvの起動も含めてRVizをバックグラウンド起動
.\docker_dev\tools\windows\rviz.ps1

# 設定変更後などにRVizを再起動
.\docker_dev\tools\windows\rviz.ps1 -Restart
```

起動ログは次のコマンドで確認できます。

```powershell
podman exec daifuku-raspicat-dev tail -n 50 /tmp/rviz.log
```

SSH経由で実機の状態だけを確認するときは、PowerShellスクリプトも使用できます。
Windows側でDDSを起動せず、固定IP`192.168.1.50`へSSHして`ros2`を実行します。

```powershell
# トピック一覧
.\docker_dev\tools\windows\pi-ros.ps1

# ノード一覧、詳細、1メッセージ、周波数
.\docker_dev\tools\windows\pi-ros.ps1 -Action Nodes
.\docker_dev\tools\windows\pi-ros.ps1 -Action Info -Topic /odom
.\docker_dev\tools\windows\pi-ros.ps1 -Action Echo -Topic /odom
.\docker_dev\tools\windows\pi-ros.ps1 -Action Hz -Topic /scan

# 実機が別アドレスの場合
.\docker_dev\tools\windows\pi-ros.ps1 -PiAddress 192.168.1.60

# ドメインIDやログインユーザーを変える場合
.\docker_dev\tools\windows\pi-ros.ps1 -DomainId 10 -User ubuntu
```

固定アドレスへ直接SSHするため、Windows側でのDDS探索は行いません。BatchModeで接続
するので、公開鍵によるSSHログインを設定しておいてください。

現在のROS 2 Humbleで動作確認済みのNavFn構成をデバッグ起動する例:

```bash
ros2 launch autonomous_nav navigation.launch.py \
  planner:=navfn lidar:=mid360 use_mid360_imu:=false \
  publish_lidar_tf:=true lidar_z:=0.30 use_rviz:=true
```

`lidar_z`は実際の取付高さへ変更してください。モーター電源は、この起動確認だけでは
有効にしません。

`docker_dev/`は`autonomous_nav`をcolconでビルドするため、
`share/autonomous_nav/scripts/`がインストールされず、Mid-360のスタンプ打ち直しが
起動しません（`/scan_raw`が配信されません）。詳細と対処は
[LiDARとオドメトリ](../docs/setup/lidar.md#タイムスタンプの打ち直し)を参照して
ください。

ネイティブノードをGDBで起動する場合は、例えば次のようにします。

```bash
gdb --args install/emcl2/lib/emcl2/emcl2_node \
  --ros-args --params-file src/autonomous_nav/config/localization/emcl2.yaml
```

キーボード操作の例:

```bash
ros2 launch raspicat_bringup teleop.launch.py teleop:=key
```

GUIが表示されない場合:

- WSLg: WSLシェルの`DISPLAY`と`WAYLAND_DISPLAY`が設定されているか確認
- Windows X Server: `windows/up.ps1`はVcXsrv Display `:400`を使用し、
  `DISPLAY=host.docker.internal:400.0`を設定
- Linux X11: 起動前に`xhost +si:localuser:root`、終了後に
  `xhost -si:localuser:root`を実行

## 手動でDockerだけ起動する場合

固定IPを別途設定済みなら、Composeを直接使用できます。

```bash
docker compose -f docker_dev/compose.yaml up -d --build
```

LinuxではGUIソケット用のオーバーライドも指定します。

```bash
docker compose -f docker_dev/compose.yaml -f docker_dev/compose.linux.yaml up -d --build
```

ただし、この方法では固定IP設定は実行されません。通常は`linux/up.sh`または`windows/up.ps1`を使って
ください。
