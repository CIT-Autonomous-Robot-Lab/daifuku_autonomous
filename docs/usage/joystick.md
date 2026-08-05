# ゲームパッドで操作する

`robot_bringup.launch.py`が`joy:=true`（既定）で`joy_node`と`joy_teleop`を立てます。
手で走らせるのと、保存したウェイポイントの巡回を始めるのを、どちらもコントローラだけで
行えます。ノートPCもRVizも要りません。

想定しているのは**XInput互換**のゲームパッドです。

```text
パッド ─ /joy ─ joy_teleop ─ /cmd_vel_teleop（優先度 100）─┐
                     ├─ FollowWaypoints ─ vi_planner（nav2:=true なら nav2_waypoint_follower）
                     └─ /buzzer ─→ ドライバ（音）
                                                           ├→ twist_mux → /cmd_vel_mux → ドライバ
自律側 ───────────────────── /cmd_vel（優先度 10）─────────┘
```

## 操作

| 操作 | 結果 | 音 |
| --- | --- | --- |
| STARTを2秒長押し | teleopの入/切を切り替える | ピロリ↑（入） / ピロリ↓（切） |
| BACKを2秒長押しして**離す** | モータ電源の入/切を切り替える（[下記](#backでモータ電源を切る)） | ピロリピロリ↑（入） / ↓（切） |
| STARTとBACKを同時に2秒 | teleopを切り、保存したウェイポイントの巡回を始める | ピピピ |
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

長押しは2秒経つまで何も起きず、切り替わった先はスティックを倒すまで見分けが付きません。
手元にノートPCが無ければログも見えないので、切り替わった時点で`/buzzer`
（`std_msgs/Int16`、値はHz・0で停止）へ短い旋律を出します。

| 音 | いつ |
| --- | --- |
| ピロリ↑（2音上がり） | teleopが入った |
| ピロリ↓（2音下がり） | teleopが切れた |
| ピロリピロリ↑（上がりを2回） | モータ電源が入った |
| ピロリピロリ↓（下がりを2回） | モータ電源が切れた |
| ピピピ（短く3回） | 巡回を始めた |
| ピロリロ↑（3音上がり） | 巡回を**1点も外さずに**走り切った |
| ブッブー（低く長く2回） | **押しても効かなかった**・1点でも外した・巡回が異常終了した |

**音はすべて1175〜2093 Hzに収めてあります。** 実機で600〜2800 Hzを掃引したところ、周囲の
雑音の中で聞き取りやすかったのが1200〜2100 Hzだったためです（低い音は圧電ブザーでは鳴りに
くく、3 kHzより上は耳に刺さります）。**高さでは区別していません。** 動くほうは向き
（上がり／下がり）で、同じ高さを繰り返すほうはリズム（短く3回／低く長く2回）で分かれます。

モータ電源だけは**同じ向きを2回**繰り返します。teleopの入/切と向きが同じなのに意味の重さが
違う（切るほうは非常停止として使います）ので、長さで格を分けています。

上がりが「入った・始まった」、下がりが「切れた・断られた」です。効かなかったときにも鳴らす
のは、そこが**今まで完全に無音だった**ためで、「2秒押したのに何も起きない」が一番分かり
にくいからです（巡回中にもう一度押した、`navigation.launch.py`が立っていない、YAMLが
読めない、ゴールを拒否された、のどれか。理由はログに出ます）。

**1点でも外したら下がりになります。** `/follow_waypoints`を受けるほう（既定の
`nav2:=false`では`vi_planner`、`nav2:=true`では`nav2_waypoint_follower`）は
どちらも`stop_on_failure: false`なので、[地図と経路を取り違えて全点が地図の外に出ても
「成功」で返ってきます](#巡回を始める)。
上がりが鳴らなければ、そこを疑ってください（外した数はログに出ます）。

teleopへ移って巡回を**取り消した**ときは鳴りません。取り消しの結果が返るのは切り替えの音を
出した直後なので、鳴らすとその音を潰してしまいます。

鳴らしているのは本体ドライバで、自前実装（`driver:=original`）も公式実装
（`driver:=raspimouse`）も同じトピック・同じ型で受けます。**鳴らなくても走行には何の影響も
ありません。** うるさければ`config/robot/joy_teleop.yaml`の`buzzer: false`で止まります。

## teleopは「モード」で、押している間だけではありません

STARTで入れているあいだ、`joy_teleop`はスティックが中立でも**ゼロ速度を出し続けます**。
`twist_mux`の優先度は非常停止ではなく、勝つのはpublishしているあいだと`timeout`
（0.5秒）だけだからです。出しっぱなしにしないと、スティックから手を離した0.5秒後に自律側
の`/cmd_vel`が通って機体が勝手に走り出します。

裏返すと、**teleopが入っているあいだ自律走行はできません**。ゴールを投げても
`/cmd_vel_mux`には出ません。自律に戻すにはSTARTをもう一度2秒押します。

同じ理由で、teleopに入るときは走行中のゴールを**取り消します**（`/follow_waypoints`と
`/navigate_to_pose`の両方に空の`CancelGoal`を出すので、RVizのパネルや`Nav2 Goal`から
走り出したものも止まります）。優先度で押さえているだけでは、teleopを切った瞬間に元の
ゴールが再開してしまいます。

取り消しは1回ではなく2秒（`cancel_window`）のあいだ繰り返します。
`nav2_waypoint_follower`（`nav2:=true`）は`stop_on_failure: false`なので、
`navigate_to_pose`側が先に取り消されると「1点失敗した」と見なして**次の点へ新しい
ゴールを出す**ためです。1回だけだと、それが取り消されずに残ります。

既定の`nav2:=false`ではこの穴はありません。巡回は`vi_planner`の中のループで、
`navigate_to_pose`のゴールを1点ごとに出し直してはいないので、`/follow_waypoints`の
取り消し1回で巡回そのものが止まります（取り消しは「1点失敗」ではなく巡回の中断として
扱われる）。繰り返しても害はないので、`cancel_window`はそのままにしてあります。

teleopを切ったあとは1秒（`stop_tail`）だけゼロを出してから黙ります。黙るだけだと本体
ドライバは最後に受けた速度を保持し続けるためです。自前実装（`driver:=original`）は
`cmd_vel_timeout`の60秒で止まりますが、公式実装（`driver:=raspimouse`）は
このキーを持たず、いつ止まるかは**未確認**です。

## BACKでモータ電源を切る

BACKだけを2秒以上押して**離す**と、本体ドライバの`motor_power`サービス
（`std_srvs/SetBool`）を叩いて電源を入/切します。`control.sh motor off`と同じもので、これを
パッドに出したものです。

**これが実質の非常停止です。** `twist_mux`の優先度は非常停止ではありません（勝つのは
publishしているあいだと0.5秒だけ）。teleopに入っても自律側を押さえるだけで、走っている
機体を確実に止めるのはモータ電源のほうです。

**離した時点で決まります。** 押している2秒の途中でSTARTを足せば巡回（同時押し）のほうに
なり、モータ電源は動きません。同時押しをやりかけてSTARTだけ先に離したときも、BACKを握った
ままでは何も起きません（一度離してから押し直してください）。押しっぱなしにしても2秒ごとに
入/切を繰り返すことはありません。

**いま入っているかは`joy_teleop`にも分かりません。** ドライバが電源の状態を出さないので、
自分が投げた要求だけを数えています。起点は`motor_power_start_state`（既定`false`）で、
ドライバの`initial_motor_power`と合わせてください。`control.sh motor`やRVizのパネルから
外して変えると**1回ぶんずれます**。音が期待と逆だったら、もう一度押せば揃います。

ドライバが立っていない（`activate`されていない）ときはブッブーが鳴り、ログに
`motor_power service is not available`が出ます。

## 巡回を始める

STARTとBACKを同時に2秒押すと、`waypoints_file`のYAMLを読んで
[`/follow_waypoints`](navigation.md#waypointを並べて巡回する)へゴールを投げます
（受けるのは既定の`nav2:=false`では`vi_planner`、`nav2:=true`では
`nav2_waypoint_follower`。型も名前も同じなのでパッド側の配線は変わりません）。押すたびに
読み直すので、`daifuku_waypoint_manager`パネルで保存し直したものがそのまま反映されます
（再起動は不要）。

**`waypoints_file`に既定はありません。** 設定していなければブッブーが鳴って始まらず、ログに
`waypoints_file is not set` が出ます（起動時にも同じ警告が1度出ます）。順路は地図と対でしか
意味を持たないので、既定の1つを忍ばせると別の地図で立てたときに黙って噛み合わないものを
走らせてしまいます。

**地図と対で選んでください。** `map_19f`で津田沼の経路を投げると全点が地図の外に出ます。
それでも`stop_on_failure: false`なので、1点ずつ失敗しながら最後まで進みます。外から
見える形は構成で変わります。

- 既定の`nav2:=false`では、`vi_planner`が1点ごとに`goal_retry_settle_sec`（3秒）
  止まったまま`goal_retry_limit`（3回）投げ直して次へ移ります。**その場で止まったまま
  何も起きない機体**に見えます。
- `nav2:=true`では**その場で左に回り続けます** — 経路が引けないので
  `navigate_to_pose`が失敗し、nav2のrecoveryが`spin`（+1.57 rad = 反時計回り、
  `max_rotational_vel` 1.0 rad/s）を点の数だけ繰り返すためです。
  [troubleshooting.md](troubleshooting.md#その場で左に回り続ける)も参照。

どちらも上がりの音は鳴らないので、まずそこで気づけます。

`navigation.launch.py`が立っていないと押しても始まりません（ログに
`follow_waypoints action server is not available`が出ます）。走行中にもう一度押しても
何も起きません。取り消すにはSTARTを2秒押してteleopへ移ります。

ゴールを投げる直前に、順路そのものを`/waypoints`（`nav_msgs/Path`、latch）へも出します。
**見せるためのものではなく**、`vi_planner`の先読み（`waypoint_prefetch`、
[navigation.md](navigation.md#次の点を走行中に解いておくwaypoint_prefetch)）へ「いま
向かっている点の次はどこか」を渡すためのものです。同じものをRVizの
`daifuku_waypoint_manager`パネルも出しますが、**実機のイメージにパネルは入っていない**
ので、機体だけで巡回するときはこちらが出どころになります。起動時にYAMLを読めた時点でも
一度出します。

**これが要るのは`nav2:=true`のときだけです。** 既定の`nav2:=false`では
`/follow_waypoints`を`vi_planner`自身が受けるので、順路はゴールと同じ経路で届きます。
`nav2:=true`のときにかぎり、**`waypoints_file`が空だと`/waypoints`は誰も出さないので、
先読みはエラーも警告も出さないまま働きません**（ただしそのときはSTART+BACK自体が
断られるので、巡回そのものが始まりません）。

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
| `/cmd_vel_teleop`は出るのに動かない | `twist_mux:=false`で立てていないか（誰も購読しません）。モータ電源（BACKを2秒長押しして離すか、`control.sh motor on`） |
| スティックを一定角度で保持すると止まる | `joy_node`の`autorepeat_rate`が0になっていないか。0だと状態が変わったときしか`/joy`が出ず、`joy_timeout`に引っかかります |
| 手を離していないのに止まる | 無線が切れています（電池・受信機）。`joy_timeout`（0.5秒）でゼロに落とす仕様です |
| 自律走行が始まらない | teleopが入ったままではありませんか（`/joy_teleop/enabled`） |
| BACKを長押ししてもモータ電源が変わらない | `ros2 service list \| grep motor_power`。押している途中でSTARTに触れていないか（巡回のほうになります）。音が期待と逆なら[数えがずれています](#backでモータ電源を切る) |
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
