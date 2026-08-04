# ゲームパッドで操作する

`robot_bringup.launch.py`が`joy:=true`（既定）で`joy_node`と`joy_teleop`を立てます。
手で走らせるのと、保存したウェイポイントの巡回を始めるのを、どちらもコントローラだけで
行えます。ノートPCもRVizも要りません。

想定しているのは**XInput互換**のゲームパッドです。

```text
パッド ─ /joy ─ joy_teleop ─ /cmd_vel_teleop（優先度 100）─┐
                     ├─ FollowWaypoints ─ nav2_waypoint_follower
                     └─ /buzzer ─→ ドライバ（音）
                                                           ├→ twist_mux → /cmd_vel_mux → ドライバ
自律側 ───────────────────── /cmd_vel（優先度 10）─────────┘
```

## 操作

| 操作 | 結果 | 音 |
| --- | --- | --- |
| STARTを3秒長押し | teleopの入/切を切り替える | ピロリ↑（入） / ピロリ↓（切） |
| STARTとBACKを同時に3秒 | teleopを切り、保存したウェイポイントの巡回を始める | ピピピ |
| 左スティック上下 | 前進・後退（0.15〜0.35 m/s） | |
| 左スティック左右 | 左右旋回（0.3〜1.0 rad/s） | |
| RBを押しながら | ブースト（上限が0.5 m/s・1.5 rad/s に上がる） | |

**XInput（Xモード）で使ってください。** モード切り替えを持つ機種をDirectInput側にすると、
ボタンと軸の番号が総入れ替えになります。左スティックと十字キーを入れ替えるモードを持つ
機種もあるので、そちらも切っておいてください。番号が合わないときは**エラーが出ず、ただ
効きません**。`ros2 topic echo /joy`で押しながら確かめ、
`config/robot/joy_teleop.yaml`の`button_*` / `axis_*`を直します。

速度は不感帯（スティックの傾き0.15）を出た**すぐ外側で下限値に飛び**、そこから上限まで
線形です。下限未満はステップ周波数が低すぎて機体が唸るだけで進まないため、そこへ写しても
操作の分解能になりません。不感帯を`joy_teleop`側だけに持たせてある（`joy_node`の
`deadzone`を0にしてある）のは、両方で切ると軸の値が引き伸ばされて届き、実際の不感帯が
この値とずれるためです。

## モードは音で分かります

長押しは3秒経つまで何も起きず、切り替わった先はスティックを倒すまで見分けが付きません。
手元にノートPCが無ければログも見えないので、切り替わった時点で`/buzzer`
（`std_msgs/Int16`、値はHz・0で停止）へ短い旋律を出します。

| 音 | いつ |
| --- | --- |
| ピロリ↑（2音上がり） | teleopが入った |
| ピロリ↓（2音下がり） | teleopが切れた |
| ピピピ（同じ高さ3回） | 巡回を始めた |
| ピロリロ↑（3音上がり） | 巡回を**1点も外さずに**走り切った |
| ブッブー↓（低い2音） | **押しても効かなかった**・1点でも外した・巡回が異常終了した |

上がりが「入った・始まった」、下がりが「切れた・断られた」です。効かなかったときにも鳴らす
のは、そこが**今まで完全に無音だった**ためで、「3秒押したのに何も起きない」が一番分かり
にくいからです（巡回中にもう一度押した、`navigation.launch.py`が立っていない、YAMLが
読めない、ゴールを拒否された、のどれか。理由はログに出ます）。

**1点でも外したら下がりになります。** `nav2_waypoint_follower`は`stop_on_failure: false`
なので、[地図と経路を取り違えて全点が地図の外に出ても「成功」で返ってきます](#巡回を始める)。
上がりが鳴らなければ、そこを疑ってください（外した数はログに出ます）。

teleopへ移って巡回を**取り消した**ときは鳴りません。取り消しの結果が返るのは切り替えの音を
出した直後なので、鳴らすとその音を潰してしまいます。

鳴らしているのは本体ドライバで、自前実装（`driver:=original`）も公式実装（既定の
`driver:=raspimouse`）も同じトピック・同じ型で受けます。**鳴らなくても走行には何の影響も
ありません。** うるさければ`config/robot/joy_teleop.yaml`の`buzzer: false`で止まります。

## teleopは「モード」で、押している間だけではありません

STARTで入れているあいだ、`joy_teleop`はスティックが中立でも**ゼロ速度を出し続けます**。
`twist_mux`の優先度は非常停止ではなく、勝つのはpublishしているあいだと`timeout`
（0.5秒）だけだからです。出しっぱなしにしないと、スティックから手を離した0.5秒後に自律側
の`/cmd_vel`が通って機体が勝手に走り出します。

裏返すと、**teleopが入っているあいだ自律走行はできません**。ゴールを投げても
`/cmd_vel_mux`には出ません。自律に戻すにはSTARTをもう一度3秒押します。

同じ理由で、teleopに入るときは走行中のゴールを**取り消します**（`/follow_waypoints`と
`/navigate_to_pose`の両方に空の`CancelGoal`を出すので、RVizのパネルや`Nav2 Goal`から
走り出したものも止まります）。優先度で押さえているだけでは、teleopを切った瞬間に元の
ゴールが再開してしまいます。

取り消しは1回ではなく2秒（`cancel_window`）のあいだ繰り返します。
`nav2_waypoint_follower`は`stop_on_failure: false`なので、`navigate_to_pose`側が先に
取り消されると「1点失敗した」と見なして**次の点へ新しいゴールを出す**ためです。1回だけ
だと、それが取り消されずに残ります。

teleopを切ったあとは1秒（`stop_tail`）だけゼロを出してから黙ります。黙るだけだと本体
ドライバは最後に受けた速度を保持し続けるためです。自前実装（`driver:=original`）は
`cmd_vel_timeout`の60秒で止まりますが、公式実装（既定の`driver:=raspimouse`）は
このキーを持たず、いつ止まるかは**未確認**です。

## 巡回を始める

STARTとBACKを同時に3秒押すと、`waypoints_file`のYAMLを読んで
[`nav2_waypoint_follower`](navigation.md)の`/follow_waypoints`へゴールを投げます。既定は
`share/daifuku_stack/waypoints/waypoints_tsudanuma.yaml`です。押すたびに読み直すので、
`daifuku_waypoint_manager`パネルで保存し直したものがそのまま反映されます（再起動は不要）。

**地図と対で選んでください。** `map_19f`で津田沼の経路を投げると全点が地図の外に出ます。
それでも`stop_on_failure: false`なので、1点ずつ失敗しながら最後まで進みます。

`navigation.launch.py`が立っていないと押しても始まりません（ログに
`follow_waypoints action server is not available`が出ます）。走行中にもう一度押しても
何も起きません。取り消すにはSTARTを3秒押してteleopへ移ります。

ゴールを投げる直前に、順路そのものを`/waypoints`（`nav_msgs/Path`、latch）へも出します。
**見せるためのものではなく**、`vi_planner`の先読み（`waypoint_prefetch`、
[navigation.md](navigation.md#次の点を走行中に解いておくwaypoint_prefetch)）が「いま
向かっている点の次はどこか」を知る唯一の手立てです。同じものをRVizの
`daifuku_waypoint_manager`パネルも出しますが、**実機のイメージにパネルは入っていない**
ので、機体だけで巡回するときはこちらが出どころになります。起動時にYAMLを読めた時点でも
一度出します。

## 確かめかた

```bash
ros2 topic echo /joy                       # ボタンと軸の番号
ros2 topic echo /joy_teleop/enabled        # teleopが入っているか（1秒ごとに出ています）
ros2 topic echo /buzzer                    # モードの音（Hz、0で停止。音の変わり目だけ出ます）
ros2 topic hz /cmd_vel_teleop              # 出ているか
ros2 topic echo /cmd_vel_mux               # 仲裁を抜けてドライバへ届いているか
```

| 症状 | 見るところ |
| --- | --- |
| 何も反応しない | `ros2 topic hz /joy`。出ていなければ`joy_node`のログ（デバイスが見えているか）と、コンテナへの`/dev/input`の受け渡し |
| ボタンだけ効かない | XInput側になっているか、十字キーとスティックを入れ替えるモードが切れているか。`ros2 topic echo /joy`で番号を確かめる |
| `/cmd_vel_teleop`は出るのに動かない | `twist_mux:=false`で立てていないか（誰も購読しません）。モータ電源（`control.sh motor on`） |
| スティックを一定角度で保持すると止まる | `joy_node`の`autorepeat_rate`が0になっていないか。0だと状態が変わったときしか`/joy`が出ず、`joy_timeout`に引っかかります |
| 手を離していないのに止まる | 無線が切れています（電池・受信機）。`joy_timeout`（0.5秒）でゼロに落とす仕様です |
| 自律走行が始まらない | teleopが入ったままではありませんか（`/joy_teleop/enabled`） |
| 切り替わるのに音が鳴らない | `ros2 topic echo /buzzer`。出ていれば機体側（ドライバが`activate`されているか、`use_buzzer`、起動ログの`peripherals: ... buzzer=`）。出ていなければ`joy_teleop`の`buzzer`が`false` |

## `control.sh teleop joystick`とは別物です

[日常操作と確認](operations.md#controlshで操作する)の`control.sh teleop joystick`は、
`teleop_twist_joy`のlaunchを別に立てるものです。そちらは自前の`joy_node`を持つので、
`joy:=true`（既定）のまま実行すると**`joy_node`が2つ**になり、`/joy`にも
`/cmd_vel_teleop`にもpublisherが2つ載ります。長押しでモードを切り替える`joy_teleop`と
デッドマン方式の`teleop_twist_joy`が同じトピックへ同時に書くので、どちらが出した
指令なのか区別が付きません。パッドで走らせるならこのページの操作を使い、
`control.sh`のほうはキーボード（`teleop keyboard`）に留めてください。両方を試すなら
`joy:=false`で立ててから`control.sh`へ渡します。

## 使わないとき

```bash
ros2 launch daifuku_stack robot_bringup.launch.py joy:=false
```

とはいえ、**挿していなくても他は動きます**。`joy_teleop`は`/joy`が一度も来なければ何も
publishしないので、自律走行の邪魔をしません（`start_enabled: true`にしたときだけは別で、
受信断としてゼロを出し続ける＝自律側を塞ぎます）。`joy_node`はデバイスが無いときの挙動を
実機で確かめていないため`respawn`を付けてあります。ホットプラグに対応していれば無害で、
対応していなければ5秒ごとに開き直して、挿したときに拾います。

途中で挿したときに拾えているかは次で見ます。

```bash
ros2 topic hz /joy      # 挿してから数秒待つ。出れば拾えている
```

設定値は[`config/robot/joy_teleop.yaml`](../../src/daifuku_stack/config/robot/joy_teleop.yaml)、
実装は[`src/joy_teleop.py`](../../src/daifuku_stack/src/joy_teleop.py)にあります。
