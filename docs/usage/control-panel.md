# 操作パネル（rqt）

PC 側の rqt に、ゴール送信・ドライバ操作・teleop・CPU 表示をまとめたパネルを出します。
実装は [`src/daifuku_rqt/`](../../src/daifuku_rqt/README.md)、CPU を集める側は
`daifuku_stack` の `system_monitor` です。

```
Pi (ros2 コンテナ)                                PC (dev コンテナ / ネイティブ)
  system_monitor ──── /diagnostics ──┐
  vi_planner ────   navigate_to_pose ├─[DDS, 同じ ROS_DOMAIN_ID]─> rqt 操作パネル
   (nav2:=true なら bt_navigator)    │
  raspicat_driver ── /motor_power ───┘                                   │
                                                     /cmd_vel_teleop <───┘
  自律側 /cmd_vel ──┐
                    ├─> twist_mux ─> /cmd_vel_mux ─> raspicat_driver
  /cmd_vel_teleop ──┘  (teleop 100 / 自律 10)
```

## 準備

パネルは PC 側だけで動きます。実機イメージには rqt が入っていないので、実機側でビルドする
必要はありません。

```bash
# PC 側（dev コンテナ or ネイティブ）。新規パッケージなので初回はビルドが要る
colcon build --symlink-install --packages-select daifuku_rqt
source install/setup.bash
```

Pi 側は `system_monitor` が新しいファイルなので、一度だけワークスペースのビルドを通します。
イメージの再作成は要りません（`Dockerfile` の `COPY src/daifuku_stack` は rosdep に apt 依存を
解かせるためだけのもので、同じ `RUN` の中で捨てています。実行時のソースは `src/` の
マウントから来るので、パッケージ名が変わってもイメージは有効なままです）。

```bash
docker compose -f docker/raspberrypi/compose.yaml up -d
```

ただし、`autonomous_nav` という名前だった頃にビルドした Pi では、名前付きボリュームの
`build/` と `install/` に古い成果物が残ります。`--merge-install` なので新しい名前の下に
上書きされず、`install/setup.bash` が両方を読んで**リンク切れのパッケージが 1 つ増えた
状態**になります。一度だけ落としてください。

```bash
docker compose -f docker/raspberrypi/compose.yaml run --rm workspace-build bash -c '
  cd /opt/ros_ws
  rm -rf build/autonomous_nav install/lib/autonomous_nav install/share/autonomous_nav \
         install/share/ament_index/resource_index/packages/autonomous_nav \
         install/share/colcon-core/packages/autonomous_nav'
```

## 起動

```bash
rqt --standalone daifuku_rqt
```

Windows + Podman の dev コンテナからは、X サーバ（VcXsrv）の起動込みで切り離し起動できます
（RViz の `rviz.ps1` と同じ形です）。

```powershell
.\docker\dev\tools\windows\rqt.ps1
```

`rqt` を素で開いて **Plugins > Raspicat > Raspicat Control Panel** から足しても同じです。
そちらだと他のプラグインと並べて perspective に保存できます。CPU の推移をグラフで見たい
ときは `rqt_plot` で `/system_monitor/cpu_percent` を開いてください
（`rqt_plot` は描画バックエンドに `python3-pyqtgraph` か `python3-matplotlib` が要ります）。

`/diagnostics` は `rqt_runtime_monitor` でも読めます。こちらは `desktop-full` に入っていない
ので `apt install ros-humble-rqt-runtime-monitor` が要ります。

## パネルの中身

**稼働状況** — CPU 全体の使用率、コア別、loadavg、SoC 温度、監視対象プロセスの CPU。
`system_monitor` が止まっていると「system_monitor を待っています」のまま動きません。

プロセス別の内訳は **`ros2` コンテナの PID 名前空間の中しか見えません**。
`docker/raspberrypi/compose.yaml` は `pid: host` を付けていないので、別コンテナで動く本体
ドライバとホストの rtmouse は出てきません。見たい場合は `ros2` サービスに `pid: host` を
足してください（compose の変更なので再起動だけで済みます）。全体の CPU・loadavg・温度の
ほうは `/proc/stat` が名前空間化されないため、コンテナの中から読んでもホストの値です。

**ゴール** — `navigate_to_pose` へ x / y / yaw を投げます。`map` フレーム基準です。
中断はアクションのキャンセルで、パネルが `/cmd_vel` に割り込むわけではありません。

**ドライバ** — ノード名（`raspicat_driver` / `raspimouse`）を選び、configure / activate /
deactivate と、モータ ON/OFF。状態は 2 秒ごとに `get_state` で取り直します。
`motor_power` はドライバが configure されたあとにしか現れないので、それ以前は
「サービスがいません」と出ます（固まりはしません）。

**teleop** — ボタンを押している間だけ `/cmd_vel_teleop` を 10 Hz で出します。矢印キーでも
操作できますが、チェックを入れたうえでパネルに入力フォーカスがあるときだけ効きます
（スピンボックスに入っている間は矢印キーは値の増減に使われます）。

出す先が `/cmd_vel` でないのは、機体の手前に `twist_mux` が入っているためです
（`robot_bringup.launch.py` の `twist_mux:=true` が既定）。優先度は teleop 100 / 自律 10 で、
**publish している間と 0.5 秒だけ**こちらが勝ちます。指を離せば自律側に戻ります。

`twist_mux:=false` で立てた機体では `/cmd_vel_teleop` を誰も購読しません。パネルは正常に
見えて**機体だけが動かない**ので、そのときは宛先を `/cmd_vel` に戻してください。宛先は
`src/daifuku_rqt/src/daifuku_rqt/control_panel.py` の `TELEOP_CMD_VEL_TOPIC` という
**モジュール先頭の定数**です（ROSパラメータでも環境変数でもないので、直したらパネルを
開き直します）。

## 停止の設計と、残る危険

仲裁は**「出している間だけ勝つ」もので、非常停止ではありません。**
`twist_mux` は勝っている入力を 1 対 1 で中継するだけで、途切れてもゼロを出しません。
止めるのはドライバの仕事のままです。自前実装（`driver:=original`）の
`config/robot/raspicat_driver.yaml` は `cmd_vel_timeout` = **60 秒**なので、指令が
途切れてからモータが止まるまで 1 分あります。公式実装（既定の `driver:=raspimouse`）は
このキーを持たず、途切れたときに止まるかどうかは**未確認**です。いずれにせよ teleop の
停止をドライバ側に任せられないので、パネルは次のすべてで 0 の `Twist` を自分で出します。

- ボタン・キーを離したとき
- 5 秒押しっぱなしになったとき（押し続けていても止めます）
- パネルが隠れた／ウィンドウが非アクティブになったとき
- プラグインを閉じたとき

**パネルのプロセスごと落ちた場合はこの経路がどれも通りません。** その場合は最後の指令の
まま少なくとも 60 秒動き続けます。確実に止めるのはモータ電源（パネルの「モータ OFF」、
または `control.sh motor off`）です。`driver:=original` で teleop を常用するなら
`overrides/` で `cmd_vel_timeout` を 1 秒程度に下げることも検討してください。

止めたあとパネルが黙るのも意図的です。`twist_mux` はメッセージを受けている間 teleop を
勝たせ続けるので、ゼロを流し続けると**開いているだけで自律走行を塞いだまま**になります。
停止時は 3 回だけゼロを出して黙ります。

**ゴールが走っている間は teleop を無効化**しています。仲裁上は勝てますが、勝てるのは
押している間と 0.5 秒だけで、離せばゴールの途中から自律側が動き出します。0.5 秒刻みで
プランナと取り合うくらいなら操作させないほうがよい、という判断です。手動に戻すには
「中断」を押してください。

## 動かないとき

| 症状 | 見るところ |
| --- | --- |
| プラグイン一覧に出てこない | `install/share/daifuku_rqt/plugin.xml` があるか。新規パッケージなので初回ビルドが要る |
| 稼働状況が空のまま | Pi 側で `ros2 topic echo /diagnostics`。`use_system_monitor:=false` にしていないか |
| プロセス別だけ空 | PID 名前空間（上記）。`ros2` コンテナに `pid: host` を足す |
| ゴールが「サーバがいません」 | `ros2 action list` に `navigate_to_pose` があるか。出すのは既定（`nav2:=false`）では `vi_planner`、`nav2:=true` では `bt_navigator`（後者は active かも見る。前者は lifecycle ノードではないので、居れば動いている） |
| モータが「サービスがいません」 | ドライバが configure 済みか。ノード名の選択が `driver:=` と合っているか |
| teleop でロボットが動かない | ゴールが走っていないか（走行中は無効）。`ros2 topic hz /cmd_vel_teleop` で出ているなら、`twist_mux` が立っているか（`twist_mux:=false` なら誰も購読しない）と `/cmd_vel_mux` を確認 |
