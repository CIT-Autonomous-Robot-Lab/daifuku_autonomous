# GUI付き開発コンテナ

`docker_dev/`はRaspberry Pi Cat公式のPC側環境をDockerで再現します。Ubuntu 22.04、ROS 2 Humble Desktop Full、RViz、`CIT-Autonomous-Robot-Lab/raspicat2`のPC用ワークスペース（確認済みコミット`500a20c`）を含みます。

Raspberry Pi本体のOS、GPIOカーネルドライバ、モータードライバはコンテナ化しません。

## 推奨の起動方法

専用Ethernet NICを確認してから実行してください。起動ラッパーはコンテナより先に
ホスト側へ固定IP`192.168.1.3/24`を設定し、ICS/DHCPを解除します。

Linux:

```bash
sudo apt install network-manager
export RASPICAT_ETHERNET_IF=enp3s0
bash docker_dev/tools/up.sh
```

Windows PowerShell:

```powershell
.\docker_dev\tools\up.ps1
```

WSL2シェルからは次を実行できます。管理者権限の確認画面が開き、Windows側の固定IPが
設定されます。

```bash
bash docker_dev/tools/up.sh
```

ネットワークの詳細と戻し方は[ROS 2ネットワーク](network.md#専用ethernetで機体を接続する)を参照してください。

## コンテナを使う

```bash
bash docker_dev/tools/shell.sh
# PowerShell: .\docker_dev\tools\shell.ps1
build-autonomous
source install/setup.bash
ros2 pkg list | grep raspicat
ros2 topic list
```

このリポジトリ全体は`/workspaces/daifuku_autonomous`、公式PC環境は`/opt/raspicat2`にあります。
開発ワークスペースは`RelWithDebInfo`と`--symlink-install`でビルドされます。
`build`、`install`、`log`はDockerボリュームへ保存され、ソースはホストと共有されます。

実機接続時は既定で`ROS_DOMAIN_ID=90`、`FASTDDS_BUILTIN_TRANSPORTS=UDPv4`です。
次のコマンドで、ナビゲーションを起動する前にデータ到達を確認します。

```bash
ros2 topic echo --once /odom
ros2 run tf2_ros tf2_echo odom base_footprint
ros2 topic hz /scan
```

NavFn構成のデバッグ起動例:

```bash
ros2 launch autonomous_nav navigation.launch.py \
  planner:=navfn lidar:=mid360 use_mid360_imu:=false \
  publish_lidar_tf:=true lidar_z:=0.30 use_rviz:=true
```

キーボード操作の例:

```bash
ros2 launch raspicat_bringup teleop.launch.py teleop:=key
```

## GUI

- WSLg: WSLシェルの`DISPLAY`と`WAYLAND_DISPLAY`を確認
- Windows X Server: `up.ps1`がVcXsrv Display `:400`を起動し、
  `DISPLAY=host.docker.internal:400.0`を指定
- Linux X11: 起動前に`xhost +si:localuser:root`、終了後に`xhost -si:localuser:root`を実行

## Dockerだけを手動起動する

固定IPを設定済みの場合のみ使用します。

```bash
docker compose -f docker_dev/compose.yaml up -d --build
```

LinuxでGUIソケットを渡す場合:

```bash
docker compose \
  -f docker_dev/compose.yaml \
  -f docker_dev/compose.linux.yaml \
  up -d --build
```

この方法では固定IPは設定されません。通常は`up.sh`または`up.ps1`を利用してください。
