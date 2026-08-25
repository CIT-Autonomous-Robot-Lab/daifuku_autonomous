# GUI付き開発コンテナ

`docker/dev/`はRaspberry Pi Cat公式のPC側環境をDockerで再現します。Ubuntu 22.04、ROS 2 Humble Desktop Full、RViz、`CIT-Autonomous-Robot-Lab/raspicat2`のPC用ワークスペース（確認済みコミット`500a20c`）を含みます。

Raspberry Pi本体のOS、GPIOカーネルドライバ、モータードライバはコンテナ化しません。

## 推奨の起動方法

専用Ethernet NICを確認してから実行してください。起動スクリプトは、コンテナを立ち上げる
前にホスト側へ固定IP`192.168.1.1/24`を設定し、ICS/DHCPを解除します。

Linux:

```bash
sudo apt install network-manager
export RASPICAT_ETHERNET_IF=enp3s0
bash docker/dev/tools/linux/up.sh
```

Windows PowerShell:

```powershell
.\docker\dev\tools\windows\up.ps1
```

WSL2のシェルからも次を実行できます。実行すると管理者権限の確認画面が開き、Windows側に
固定IPが設定されます。

```bash
bash docker/dev/tools/linux/up.sh
```

ネットワークの詳細と戻し方は[ROS 2ネットワーク](network.md#専用ethernetで機体を接続する)を参照してください。

## コンテナを使う

```bash
bash docker/dev/tools/linux/shell.sh
# PowerShell: .\docker\dev\tools\windows\shell.ps1
build-autonomous
source install/setup.bash
ros2 pkg list | grep raspicat
ros2 topic list
```

このリポジトリ全体は`/workspaces/daifuku_autonomous`、公式PC環境は`/opt/raspicat2`にあります。
開発ワークスペースは`RelWithDebInfo`と`--symlink-install`でビルドされます。
`build`、`install`、`log`はDockerボリュームへ保存され、ソースはホストと共有されます。

実機接続時は既定で`ROS_DOMAIN_ID=90`、`FASTDDS_BUILTIN_TRANSPORTS=UDPv4`です。
ナビゲーションを起動する前に、次のコマンドでデータが届いているか確認します。

```bash
ros2 topic echo --once /odom
ros2 run tf2_ros tf2_echo odom base_footprint
ros2 topic hz /scan
```

NavFn構成のデバッグ起動例:

```bash
ros2 launch daifuku_stack navigation.launch.py \
  planner:=navfn use_rviz:=true
```

`use_rviz`の既定は`false`なので、この環境で表示するなら明示します。**LiDARの引数は
ありません**——`navigation.launch.py`はセンサーを1つも立てず、`/scan`と`/odom`の消費者に
徹します。上の確認コマンドで`/scan`が来るのは実機の`raspicat`サービスが出しているから
なので、実機につながずにこの環境だけでセンサーを立てたいときは
`ros2 launch daifuku_bringup lidar_bringup.launch.py`を別に叩いてください
（`robot_bringup.launch.py`のほうは本体ドライバも立てるのでPCでは上がりません）。

キーボード操作の例:

```bash
ros2 launch raspicat_bringup teleop.launch.py teleop:=key
```

人が出す指令の宛先は`/cmd_vel_teleop`（優先度10）です（`control.sh teleop`と
[操作パネル](../usage/control-panel.md)はそちらへ出しています）。優先度は自律側の
`/cmd_vel`（100）のほうが上なので、**自律走行中は手動が通りません**（先にゴールを
取り消すこと）。**この上流launchがどこへ出すかは確認していません。** `/cmd_vel`へ
出しているなら、`twist_mux:=true`（既定）ではそれは自律側の入口なので、自律走行中に
開くと取り合いになります。`ros2 topic info /cmd_vel_teleop`で購読者と配信者を見て
確かめてください。

## GUI

- WSLg: WSLシェルの`DISPLAY`と`WAYLAND_DISPLAY`を確認
- Windows X Server: `windows/up.ps1`がVcXsrv Display `:400`を起動し、
  `DISPLAY=host.docker.internal:400.0`を指定
- Linux X11: 起動前に`xhost +si:localuser:root`、終了後に`xhost -si:localuser:root`を実行

## Dockerだけを手動起動する

固定IPを設定済みの場合のみ使用します。

```bash
docker compose -f docker/dev/compose.yaml up -d --build
```

LinuxでGUIソケットを渡す場合:

```bash
docker compose \
  -f docker/dev/compose.yaml \
  -f docker/dev/compose.linux.yaml \
  up -d --build
```

この方法では固定IPは設定されません。通常は`linux/up.sh`または`windows/up.ps1`を利用してください。
