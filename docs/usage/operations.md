# 日常操作と確認

## 起動状態を確認する

```bash
ros2 node list
ros2 topic list
ros2 topic hz /scan_raw
ros2 topic hz /scan
ros2 topic hz /odom
ros2 run tf2_ros tf2_echo odom base_footprint
```

Mid-360 + IMUでは追加で確認します。

```bash
ros2 topic hz /livox/lidar
ros2 topic hz /livox/imu
ros2 topic hz /imu/mid360
ros2 topic hz /wheel/odom
```

## Dockerでコマンドを実行する

```bash
docker compose -f docker/raspberrypi/compose.yaml exec ros2 \
  /ros_entrypoint.sh ros2 topic list
docker compose -f docker/raspberrypi/compose.yaml exec ros2 \
  /ros_entrypoint.sh bash
```

補助スクリプトから対話シェルを開くこともできます。コンテナが停止していれば自動的に
起動します。

```bash
bash docker/raspberrypi/tools/shell.sh
```

## control.shで操作する

`docker/raspberrypi/tools/control.sh`は、モーター電源、遠隔操作、状態確認をまとめたスクリプト
です。こちらもコンテナを自動起動します。

```bash
bash docker/raspberrypi/tools/control.sh help
```

| サブコマンド | 動作 |
|---|---|
| `motor on` | モーター電源を入れる |
| `motor off` | `/cmd_vel`へ停止指令を送ってからモーター電源を切る |
| `stop` | `/cmd_vel`へ停止指令を1回送る |
| `teleop keyboard` | キーボードで操作する（Ctrl-Cで終了） |
| `teleop joystick` | ジョイスティックで操作する（Ctrl-Cで終了） |
| `status` | コンテナ、ROSノード、モーターサービスを確認する |
| `nodes` / `topics` / `services` | それぞれの一覧を表示する |
| `ros ARGS...` | 任意の`ros2`コマンドを実行する |
| `logs [ARGS...]` | コンテナのログを表示する（例: `logs -f`） |

動作は環境変数で変更できます。

| 環境変数 | 既定値 | 用途 |
|---|---|---|
| `CONTROL_SERVICE` | `ros2` | Composeサービス名 |
| `MOTOR_SERVICE` | `/motor_power` | モーター電源サービス |
| `CMD_VEL_TOPIC` | `/cmd_vel` | 速度指令トピック |
| `ROS_TIMEOUT` | `10` | ROS操作のタイムアウト秒数 |
| `TELEOP_LINEAR_SPEED` | `0.2` | キーボード操作の並進速度 m/s |
| `TELEOP_ANGULAR_SPEED` | `1.0` | キーボード操作の旋回速度 rad/s |
| `JOYSTICK_ID` | `0` | joyデバイスID |
| `JOYSTICK_CONFIG` | `xbox` | `teleop_twist_joy`の設定名 |

たとえば遠隔操作の速度を落とす場合:

```bash
TELEOP_LINEAR_SPEED=0.1 bash docker/raspberrypi/tools/control.sh teleop keyboard
```

`motor off`は停止指令を送ってから電源を切ります。停止指令の送信に失敗しても警告を
出したうえで電源OFFへ進みます。作業を終えるときは`motor off`を実行してください。

## 設定変更を反映する

`src/autonomous_nav`配下のlaunch、config、maps、rvizはDockerコンテナへマウントされるため、通常はイメージの再ビルドが不要です。起動中のノードを再起動して反映します。

次を変更した場合はイメージを再ビルドします。

- `docker/raspberrypi/Dockerfile`
- 依存パッケージ
- `autonomous_bot.repos`
- CMakeやビルド対象
- 外部パッケージのソース

```bash
docker compose -f docker/raspberrypi/compose.yaml down
docker compose -f docker/raspberrypi/compose.yaml build
docker compose -f docker/raspberrypi/compose.yaml up -d
```

## ログを見る

```bash
docker compose -f docker/raspberrypi/compose.yaml logs
docker compose -f docker/raspberrypi/compose.yaml logs -f ros2
bash docker/raspberrypi/tools/control.sh logs -f
```

コンテナは`HOME=/tmp`で動くため、ROS 2のログファイルは`/tmp/ros/log`に出力されます。
コンテナを作り直すと消えるので、残したいログはホストへ取り出してください。

```bash
docker compose -f docker/raspberrypi/compose.yaml exec ros2 ls /tmp/ros/log
docker compose -f docker/raspberrypi/compose.yaml cp ros2:/tmp/ros/log ./ros_log
```

## 終了する

走行を伴う作業のあとは、コンテナを止める前にモーター電源を切ります。

```bash
bash docker/raspberrypi/tools/control.sh motor off
docker compose -f docker/raspberrypi/compose.yaml down
```

ネイティブ環境ではlaunchを実行したターミナルで`Ctrl+C`を押します。停止後も機体側ドライバが動いている場合があるため、必要に応じて機体側も安全に停止してください。
