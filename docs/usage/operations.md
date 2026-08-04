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

## tmuxで作業する

SSHで機体につないで作業する場合は、tmuxの中でlaunchを起動してください。SSHが切れても
ノードは動き続け、つなぎ直して`attach`すれば同じ画面に戻れます。ターミナルを何枚も
開かなくても、launch・遠隔操作・状態確認を1つの接続で切り替えられます。

`tools/image/`で作ったSDカードにはtmuxが入っています。入っていない場合は導入します。

```bash
sudo apt install -y tmux
```

セッションの操作:

```bash
tmux new-session -s nav      # 新しいセッションを作る
tmux ls                      # セッションの一覧
tmux attach -t nav           # つなぎ直す
tmux kill-session -t nav     # セッションごと終了する（中のノードも止まる）
```

セッション内では、`Ctrl-b`を押して離してから次のキーを押します。

| キー | 動作 |
|---|---|
| `Ctrl-b` `c` | 窓を新しく開く |
| `Ctrl-b` `0`〜`9` | 番号で窓を切り替える |
| `Ctrl-b` `n` / `p` | 次／前の窓へ移動する |
| `Ctrl-b` `d` | デタッチする（ノードは動いたまま） |
| `Ctrl-b` `[` | 画面をさかのぼる（`q`で戻る） |

デタッチしてもノードは動き続けます。機体を止めるときは、アタッチしてlaunchの窓で
`Ctrl-C`を押すか、別の窓から`control.sh stop`を送ってください。

地図作成と自律移動それぞれの窓構成は、[地図作成](mapping.md#tmuxで一式を起動する)と
[自律移動](navigation.md#tmuxで一式を起動する)にまとめています。

## control.shで操作する

`docker/raspberrypi/tools/control.sh`は、モーター電源、遠隔操作、状態確認をまとめたスクリプト
です。こちらもコンテナを自動起動します。

```bash
bash docker/raspberrypi/tools/control.sh help
```

| サブコマンド | 動作 |
|---|---|
| `motor on` | モーター電源を入れる |
| `motor off` | `CMD_VEL_TOPIC`へ停止指令を送ってからモーター電源を切る |
| `stop` | `CMD_VEL_TOPIC`へ停止指令を1回送る |
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
| `CMD_VEL_TOPIC` | `/cmd_vel_teleop` | 速度指令トピック。仲裁（`twist_mux`）の優先度が高い側。`twist_mux:=false`で起動したなら`/cmd_vel` |
| `ROS_TIMEOUT` | `10` | ROS操作のタイムアウト秒数 |
| `TELEOP_LINEAR_SPEED` | `0.2` | キーボード操作の並進速度 m/s |
| `TELEOP_ANGULAR_SPEED` | `1.0` | キーボード操作の旋回速度 rad/s |
| `JOYSTICK_ID` | `0` | joyデバイスID |
| `JOYSTICK_CONFIG` | `xbox` | `teleop_twist_joy`の設定名 |

たとえば遠隔操作の速度を落とす場合:

```bash
TELEOP_LINEAR_SPEED=0.1 bash docker/raspberrypi/tools/control.sh teleop keyboard
```

`motor off`は停止指令を送ってから電源を切ります。停止指令の送信に失敗した場合も、
警告を出したうえで電源を切ります。作業を終えるときは`motor off`を実行してください。

## 設定変更を反映する

`docker/raspberrypi/`のイメージが持つのはaptの依存とツールチェーンだけです。ワークスペースの
ソースはイメージに含めず、composeが`src/`をマウントして渡します。ビルドは`up`のたびに
コンテナの中の`colcon build`が行います。変更したものによって、どこからやり直すかが3通りに
分かれます。

| 変更したもの | やること |
|---|---|
| `src/daifuku_stack`配下のlaunch、config、behavior_trees、maps、rviz、src | 何もしない。`--symlink-install`なのでノードを再起動するだけで反映される |
| `src/raspicat_driver`のPython | 同上。ただし`setup.py`の`entry_points`を増やしたときはビルドが要る |
| C++やRustのコード、`CMakeLists.txt`、外部パッケージのソース | `docker compose up`で差分ビルドする |
| aptの依存、`Dockerfile`、`package.xml`の依存、`docker/`配下のスクリプト | `docker compose build`からやり直す |

aptパッケージを足したときにイメージを焼き直すのは、`rosdep`をイメージのビルド時にしか
回さないからです（`build-workspace.sh`が`up`のたびに`rosdep`を回すと、ネットワークが要る
うえにaptの状態が毎回変わり、この切り分け自体が崩れます）。最後の行のコマンドは次のとおりです。

```bash
docker compose -f docker/raspberrypi/compose.yaml down
docker compose -f docker/raspberrypi/compose.yaml build
docker compose -f docker/raspberrypi/compose.yaml up -d
```

`autonomous_bot.repos`だけは、この3通りのどれにもきれいには収まりません。外部パッケージの取得は`up`のときの
`vcs import --skip-existing`なので、リポジトリを新しく足しただけなら`up`で入ります。
ただしそれが新しいapt依存を連れてくる場合や、`build-workspace.sh`の`--packages-select`へ
追加が要る場合は`build`からやり直してください。既にある`src/`のリビジョン変更は
`--skip-existing`で素通りするので、`src/`側で直接チェックアウトを合わせます
（[`docker/raspberrypi/README.md`](../../docker/raspberrypi/README.md)に詳しくあります）。

1行目が効くのは既にあるファイルを直したときだけです。launchやconfigでも、ファイルを
新しく足したときは`docker compose up`が要ります。`install/`のsymlinkはビルド時に
張られるためです。

名前を変えたり別のディレクトリへ移したりしたときは、それに加えて**古いsymlinkが
`install/`に残ります**。両方あるように見えて新しいほうしか更新されないので、紛らわしければ
`install/`のボリュームごと捨ててから建て直してください。ボリューム名は`docker volume ls`で
確認します（`build`と`log`は残して構いません）。

```bash
docker compose -f docker/raspberrypi/compose.yaml down
docker volume ls | grep autonomous-install
docker volume rm <上で出た名前>
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
