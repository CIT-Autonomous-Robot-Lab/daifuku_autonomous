# Raspberry Pi Cat development container

公式ドキュメントのPC側環境（Ubuntu 22.04、ROS 2 Humble、`raspicat-pc.repos`）を
Dockerで再現する開発環境です。起動ラッパーはコンテナより先にホスト側のDHCP/NATを
有効化します。

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

## 配線前の注意

DHCPを開始する前に、Raspberry Pi Catにつながる専用Ethernet NICを確認してください。
起動処理はそのNICの既存ネットワーク設定を切り替えます。社内LANなどにつながったNICを
選択しないでください。

## Linux（Ubuntu）

ホストにDocker Engine、Docker Compose v2、NetworkManagerが必要です。

```bash
sudo apt install network-manager
export RASPICAT_ETHERNET_IF=enp3s0  # NICが1個だけなら省略可
bash docker_dev/tools/up.sh
```

`up.sh`は次の順序で処理します。

1. `raspicat-docker-dev`というNetworkManagerプロファイルを作成
2. ホストを`10.42.0.1/24`に設定し、DHCPとインターネット共有（NAT）を開始
3. Dockerイメージをビルドしてコンテナを起動

実機へ払い出されたIPは次のように確認できます。

```bash
sudo arp-scan --localnet --interface "$RASPICAT_ETHERNET_IF"
ssh ubuntu@10.42.0.x
```

終了後にネットワーク設定も戻す場合:

```bash
bash docker_dev/tools/network-linux.sh down "$RASPICAT_ETHERNET_IF"
```

## Windows + Docker Desktop / WSL2

Docker Desktopでhost networkingを有効にしてください。WSL2から使う場合は、Windows
11 22H2以降のmirrored networkingも推奨します。`%UserProfile%\.wslconfig`の例:

```ini
[wsl2]
networkingMode=mirrored
firewall=true
```

設定変更後は`wsl --shutdown`を実行し、Docker Desktopを再起動します。

Windows PowerShellから起動する場合:

```powershell
# アダプター名は Get-NetAdapter で確認
.\docker_dev\tools\up.ps1 -EthernetAlias "Ethernet" -InternetAlias "Wi-Fi"
```

WSL2シェルから起動する場合:

```bash
bash docker_dev/tools/up.sh
```

管理者権限の確認画面が開き、Windows Internet Connection Sharing (ICS) を設定します。
ICSがWindows側でDHCP/NATを提供します。通常、Windows側は`192.168.137.1/24`、実機は
`192.168.137.x`です。Dockerコンテナ内にDHCPサーバーを置かないのは、Docker Desktopの
Linux VMからWindowsの物理Ethernet NICへ直接DHCPブロードキャストを出せないためです。

同じ制約はROS 2 DDSのマルチキャスト探索にも影響します。Docker Desktopのhost
networkingはLinux VM内のL4接続であり、コンテナからWindowsの物理NICへ直接bind
できません。実機のトピックが見えない場合、ライブDDSデバッグには次のいずれかを
使用してください。

- ネイティブUbuntu + Docker Engineで`network_mode: host`を使う
- mirrored networkingのWSL2内へDocker Engineを直接導入し、コンテナから実機側NICが
  見えることを確認して使う
- Piでrosbagを記録し、Docker Desktop側のこの環境で再生してローカルデバッグする

Windows + Docker Desktopでも、ビルド、GDB、RViz、rosbag再生には利用できます。

払い出し後の実機はWindows側で確認します。

```powershell
Get-NetNeighbor -InterfaceAlias "Ethernet" -AddressFamily IPv4 |
  Where-Object State -ne Unreachable
ssh ubuntu@192.168.137.x
```

ICSを解除する場合は管理者PowerShellで実行します。

```powershell
.\docker_dev\tools\network-windows.ps1 -Mode Disable -EthernetAlias "Ethernet"
```

注意: ICSを有効にすると、このスクリプトは競合を避けるため既存のICS共有を解除します。

## コンテナの利用

```bash
bash docker_dev/tools/shell.sh
# PowerShellの場合
.\docker_dev\tools\shell.ps1
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

現在のROS 2 Humbleで動作確認済みのNavFn構成をデバッグ起動する例:

```bash
ros2 launch autonomous_nav navigation.launch.py \
  planner:=navfn lidar:=mid360 use_mid360_imu:=false \
  publish_lidar_tf:=true lidar_z:=0.30 use_rviz:=true
```

`lidar_z`は実際の取付高さへ変更してください。モーター電源は、この起動確認だけでは
有効にしません。

ネイティブノードをGDBで起動する場合は、例えば次のようにします。

```bash
gdb --args install/emcl2/lib/emcl2/emcl2_node \
  --ros-args --params-file src/autonomous_nav/config/emcl2_params.yaml
```

キーボード操作の例:

```bash
ros2 launch raspicat_bringup teleop.launch.py teleop:=key
```

GUIが表示されない場合:

- WSLg: WSLシェルの`DISPLAY`と`WAYLAND_DISPLAY`が設定されているか確認
- Windows X Server: X Serverを起動し、必要なら`DISPLAY=host.docker.internal:0.0`を指定
- Linux X11: 起動前に`xhost +si:localuser:root`、終了後に
  `xhost -si:localuser:root`を実行

## 手動でDockerだけ起動する場合

DHCP/NATを別途設定済みなら、Composeを直接使用できます。

```bash
docker compose -f docker_dev/compose.yaml up -d --build
```

LinuxではGUIソケット用のオーバーライドも指定します。

```bash
docker compose -f docker_dev/compose.yaml -f docker_dev/compose.linux.yaml up -d --build
```

ただし、この方法ではDHCP設定は実行されません。通常は`up.sh`または`up.ps1`を使って
ください。
