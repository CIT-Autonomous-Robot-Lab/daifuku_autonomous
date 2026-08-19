# daifuku_rqt

Raspberry Pi Cat の操作パネル（rqt プラグイン）。ゴール送信、ドライバのライフサイクルと
モータ、teleop、CPU 表示を 1 つのウィジェットにまとめたものです。

**PC 側だけで動きます。** 実機の `docker/raspberrypi/` イメージには rqt が入っていないため、
`docker/raspberrypi/scripts/build-workspace.sh` の `--packages-select` にこのパッケージは
入れていません（あちらは名前で選ぶので、書かなければ建ちません）。ビルドされるのは
`docker/dev/` とネイティブ環境です。

## 起動

```bash
rqt --standalone daifuku_rqt          # 単独ウィンドウ
ros2 run daifuku_rqt daifuku_rqt   # 同じもの
rqt                                          # Plugins > Raspicat > Raspicat Control Panel
```

`rqt` から開いた場合、ゴール座標とドライバ名は perspective に保存されます。

## つながる先

| パネルの操作 | 相手 |
| --- | --- |
| 稼働状況 | `/diagnostics`（`daifuku_stack` の `system_monitor` が出す） |
| ゴール送信・中断 | `navigate_to_pose` アクション（既定の `nav2:=false` では `vi_planner`、`nav2:=true` では `bt_navigator`。型も名前も同じ） |
| configure / activate / deactivate | `/<ドライバ名>/change_state`、状態表示は `/get_state` を 2 秒ごと |
| モータ ON/OFF | `/motor_power`（`std_srvs/SetBool`） |
| teleop | `/cmd_vel_teleop` を 10 Hz で publish（`twist_mux` の優先度 10 側。**自律側の `/cmd_vel`（100）のほうが上なので、自律走行中は通らない** — 先に「中断」でゴールを止めること） |

`motor_power` が**ノード名の下ではなく `/motor_power`** にいるのは、ドライバが相対名で
`create_service` しているためです（相対名は名前空間に対して解決され、ノード名は入りません）。
ライフサイクルのサービスだけはノード名の下にいます。

**モータ電源に状態表示はありません。** ライフサイクルは `/get_state` で 2 秒ごとに
取り直しますが、電源のほうは「ON」「OFF」の 2 ボタンだけで、いま入っているかは
パネルのどこにも出ません。自前実装（`driver:=original`）は実状態を
`/motor_power_state`（`std_msgs/Bool`、latch）へ出しますが、**パネルはまだ購読して
いません**（`joy_teleop` の LED1 はそれを見ています）。確かめるなら
`ros2 topic echo /motor_power_state --qos-durability transient_local` のほうです。

## 止まり方（ここが一番重要）

自前実装（`driver:=original`）の `configs/bringup/robot/raspicat_driver.yaml` は
`cmd_vel_timeout` が **60 秒**です。つまり「`cmd_vel` が途切れたから止まる」までに
1 分かかります。公式実装（`driver:=raspimouse`）にはこのキーが**そもそも
ありません**（`raspicat.yaml` が並べている 14 個で全部）。指令が途切れたときに
上流のノードが止めるのかどうかは**未確認**です。少なくとも 60 秒は見ておいてください。
どちらにせよ teleop の停止をドライバ側のタイムアウトに任せられないので、パネルが
自分で 0 の `Twist` を出します。

出す条件は次のとおりです。

- ボタンを離したとき・矢印キーを離したとき
- 5 秒（`HOLD_LIMIT`）押しっぱなしになったとき（押し続けている最中でも止めます）
- パネルが隠れたとき、ウィンドウが非アクティブになったとき
- プラグインを閉じるとき（3 回まとめて出します）

それでも、**パネルのプロセスごと落ちた場合は 60 秒動き続けます。** `driver:=original` で
teleop を常用するなら `cmd_vel_timeout` を 1 秒程度へ下げることを検討してください
（既定 60.0 は上流の値です）。確実に止めるのはどちらのドライバでもモータ電源です。

## teleop とナビゲーションの排他

出す先は `/cmd_vel` ではなく **`/cmd_vel_teleop`** です。`/cmd_vel` は自律側
（nav2 のコントローラと `vi_planner`）の出力で、機体の手前には `twist_mux` が入って
います（`robot_bringup.launch.py` の `twist_mux:=true` が既定）。優先度は**自律 100 /
teleop 10** なので、**こちらが通るのは自律側が黙っているあいだだけです**。配線は
[`configs/README.md`](../../configs/README.md#twist_muxyaml-の配線と優先度)。

だから**ゴールが走っている間は teleop グループを無効化**しています。無効化しなくても
仲裁で負けて機体は動かないのですが、それでは「パネルが壊れた」と見分けが付きません。
手動に戻すには「中断」を押してください。

`twist_mux:=false` で立てた機体では、`/cmd_vel_teleop` を誰も購読しません。
パネルは何事もなく動いているように見えて**機体だけが動かない**ので、そのときは宛先を
`/cmd_vel` に戻してください。宛先は `src/daifuku_rqt/src/daifuku_rqt/control_panel.py` の
`TELEOP_CMD_VEL_TOPIC` という**モジュール先頭の定数**です（ROSパラメータでも環境変数でも
ないので、直したらパネルを開き直します）。

## 実装のきまり

- ROS のコールバックは rqt のスピンスレッドから来ます。ウィジェットを直接触ると
  「たまに落ちる」形で壊れるので、コールバックは Qt シグナルを emit するだけにし、
  スロット側でウィジェットを触ります。
- サービスとアクションはすべて非同期（`call_async` + `add_done_callback`）です。スロットの
  中で `spin_until_future_complete` を呼ぶと GUI が固まります。
- done-callback から例外が漏れるとスピンスレッドごと死に、パネル中の購読が全部止まります。
  `_result_or_none()` で握って UI に出す形にしてあります。
