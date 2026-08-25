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

一通り順番に確かめるなら`tools/checklist/`を使います。段階順（静的検査から
ナビゲーションまで）に並んでいて、機体が動く項は必ず事前に確認を求めます。

```bash
tools/checklist/checkall.sh --list          # 何が走るかだけ見る
tools/checklist/checkall.sh                 # 機体は動かさない範囲を全部
tools/checklist/checkall.sh --only 0401     # LiDARだけ
```

`--armed`（機体が動く項も走らせる）を含む使いかたは`checkall.sh`の冒頭にあります。

## Dockerでコマンドを実行する

```bash
docker compose exec ros2 \
  /ros_entrypoint.sh ros2 topic list
docker compose exec ros2 \
  /ros_entrypoint.sh bash
```

補助スクリプトから対話シェルを開くこともできます。コンテナが停止していれば自動的に
起動します。

```bash
bash tools/shell.sh
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

`tools/control.sh`は、モーター電源、遠隔操作、状態確認をまとめたスクリプト
です。こちらもコンテナを自動起動します。

```bash
bash tools/control.sh help
```

| サブコマンド | 動作 |
|---|---|
| `motor on` | モーター電源を入れる |
| `motor off` | `CMD_VEL_TOPIC`へ停止指令を送ってからモーター電源を切る |
| `stop` | `CMD_VEL_TOPIC`へ停止指令を1回送る |
| `teleop keyboard` | キーボードで操作する（Ctrl-Cで終了） |
| `teleop joystick` | `teleop_twist_joy`で操作する（Ctrl-Cで終了）。`joy:=true`（既定）と併用しない（下記） |
| `status` | コンテナ、ROSノード、モーターサービスを確認する |
| `nodes` / `topics` / `services` | それぞれの一覧を表示する |
| `ros ARGS...` | 任意の`ros2`コマンドを実行する |
| `logs [ARGS...]` | コンテナのログを表示する（例: `logs -f`） |

動作は環境変数で変更できます。

| 環境変数 | 既定値 | 用途 |
|---|---|---|
| `CONTROL_SERVICE` | `ros2` | Composeサービス名 |
| `MOTOR_SERVICE` | `/motor_power` | モーター電源サービス |
| `CMD_VEL_TOPIC` | `/cmd_vel_teleop` | 速度指令トピック。仲裁（`twist_mux`）の手動側の入口で、**優先度は自律側のほうが上**（自律走行中は`teleop`も`stop`も効かない。先にゴールを取り消すこと）。`twist_mux:=false`で起動したなら`/cmd_vel` |
| `ROS_TIMEOUT` | `10` | ROS操作のタイムアウト秒数 |
| `TELEOP_LINEAR_SPEED` | `0.2` | キーボード操作の並進速度 m/s |
| `TELEOP_ANGULAR_SPEED` | `1.0` | キーボード操作の旋回速度 rad/s |
| `JOYSTICK_ID` | `0` | joyデバイスID |
| `JOYSTICK_CONFIG` | `xbox` | `teleop_twist_joy`の設定名 |

たとえば遠隔操作の速度を落とす場合:

```bash
TELEOP_LINEAR_SPEED=0.1 bash tools/control.sh teleop keyboard
```

`motor off`は停止指令を送ってから電源を切ります。停止指令の送信に失敗した場合も、
警告を出したうえで電源を切ります。作業を終えるときは`motor off`を実行してください。

同じことは**ゲームパッドのBACKを2秒長押しして離す**操作でもできます
（[ゲームパッドで操作する](joystick.md#backでモータ電源を切る)）。端末に戻れない場所で
止めたいときはそちら。標準の自前実装（`driver:=original`）は電源の実状態を
`/motor_power_state`へ出すので、`control.sh`と混ぜても`joy_teleop`が追随します。
**公式実装（`driver:=raspimouse`）はこれを出さない**ので、そのときだけ`joy_teleop`は
自分が投げた要求を数えることになり、外から変えると入/切が1回ぶんずれます。

`teleop joystick`は`teleop_twist_joy`のlaunchを別に立てるもので、自前の`joy_node`を
持ちます。`robot_bringup.launch.py`の`joy:=true`（既定）と重ねると`joy_node`が2つ、
`/joy`と`/cmd_vel_teleop`のpublisherも2つになります。パッドで走らせるなら
[ゲームパッドで操作する](joystick.md)のほうを使ってください。

## 設定変更を反映する

`docker/raspberrypi/`のイメージが持つのはaptの依存とツールチェーンだけです。ワークスペースの
ソースはイメージに含めず、composeが`src/`をマウントして渡します。ビルドは`up`のたびに
コンテナの中の`colcon build`が行います。変更したものによって、どこからやり直すかが3通りに
分かれます。

| 変更したもの | やること |
|---|---|
| `src/daifuku_stack`配下のlaunch、behavior_trees、maps、rviz、src | 何もしない。`--symlink-install`なのでノードを再起動するだけで反映される |
| `src/daifuku_config/stack/`の**値**（`nav2/`・`localization/`・`mapping/`・`lifecycle_bond.yaml`） | 同じく再ビルドは不要。ただし**走らせたまま直すと`config_sentinel`がそのlaunchを終了します**（下） |
| `src/daifuku_config/bringup/`（LiDAR・EKF・ドライバ）と`src/daifuku_config/overrides/`の**値** | 再ビルドは不要だが、読むのは常駐している`raspicat`サービスなので立て直しが要る。**`config_sentinel`が変化に気づいて自分で上がり直します**（機体が止まっていて、その設定でも立つときだけ。`config_watch:=warn`で止められる）。コメントだけの変更には反応しません |
| `src/daifuku_bringup`配下のlaunchと`src/`のPython | 再ビルドは不要だが、**`config_sentinel`は気づきません**（見張っているのは`src/daifuku_config/`の`*.yaml`だけ）。`docker compose up -d`で自分で立て直す |
| `src/daifuku_config/overrides/`に**ファイルを新しく足した** | 一度ビルドを通す（`install/`のsymlinkはビルド時にしか張られないので、足しただけでは`overrides:=`の一覧に出てこない）。そのあと`docker compose up -d` |
| `src/daifuku_config/site`（走らせる場所） | **`tools/site.sh <名前>`**。書き換えと`raspicat`の立て直しを両方やる（下） |
| `src/raspicat_driver`のPython | 何もしない。ただし`setup.py`の`entry_points`を増やしたときはビルドが要る |
| C++やRustのコード、`CMakeLists.txt`、外部パッケージのソース | `docker compose up`で差分ビルドする |
| aptの依存、`Dockerfile`、`package.xml`の依存、`docker/`配下のスクリプト | `docker compose build`からやり直す |

aptパッケージを足したときにイメージを焼き直すのは、`rosdep`をイメージのビルド時にしか
回さないからです（`build-workspace.sh`が`up`のたびに`rosdep`を回すと、ネットワークが要る
うえにaptの状態が毎回変わり、この切り分け自体が崩れます）。最後の行のコマンドは次のとおりです。

```bash
docker compose down
docker compose build
docker compose up -d
```

`daifuku_autonomous.repos`だけは、この3通りのどれにもきれいには収まりません。外部パッケージの取得は`up`のときの
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
docker compose down
docker volume ls | grep autonomous-install
docker volume rm <上で出た名前>
docker compose up -d
```

### 走らせたまま設定を直したとき

top-levelのlaunchはそれぞれ`config_sentinel`を1つ立てていて、**自分が起動時に読んだ
設定が書き変わると、まずログに出したうえで自分のlaunchを終了します。** 落ちたあと誰が
上げ直すかは、そのlaunchを誰が立てたかで変わります。

- **機体**（`raspicat`サービス）は`restart: unless-stopped`なので、そのまま新しい設定で
  上がり直します。上の表の`src/daifuku_config/bringup/`の行がこれです
- **`navigation`と`mapping`は人が立てたものなので、上げ直す人が居ません。** 終わったまま
  です。とくに`mapping`では、**そこまで作った地図が消えます**（SLAM Toolboxは終了時に
  保存しません）

見張っているのは自分のパッケージの`src/daifuku_config/`の`*.yaml`（`overrides/`は除く）と、重ねて
いる`overrides`のうち自分の部分木です。**そのlaunchが実際に読まないファイルも入ります**
——`navigation`を走らせたまま`src/daifuku_config/stack/mapping/slam_toolbox.yaml`を直しても落ちます
（取りこぼして黙るより、余分に反応するほうを選んでいます）。**値を正規化してから指紋を
取るので、コメントや並び順の変更には反応しません。**

落とさせたくないときは、そのlaunchへ`config_watch:=warn`（言うだけ）か`off`（見張りごと
立てない）を渡します。長丁場の地図作成のように、途中で落ちる代償が大きいときはこちらを
使ってください。

```bash
ros2 launch daifuku_stack mapping.launch.py config_watch:=warn
```

なお`config_watch:=shutdown`（既定）でも、落ちるのは**追随してよい構成で・その設定でも
立つことを確かめ・機体が止まっている**ときだけです。`overrides:=`を明示して立てたlaunchは
そもそも追随しません。

## 走らせる場所を切り替える

19Fと津田沼のように場所が変わると、**LiDARの帯（仰角と高さ）・`emcl2`と価値反復の調整・
地図**が3つとも変わります。これは1つの話なので、人が動かす値も1つだけです——
`src/daifuku_config/site`の1行です。

```bash
tools/site.sh                 # 今の値と、選べる名前
tools/site.sh map_tsudanuma   # 切り替えて、機体が上がり直すところまで面倒を見る
tools/site.sh map_19f --file-only    # ファイルを書くだけ（ROSにもDockerにも触らない）
tools/site.sh map_19f --no-restart   # ファイル経由に落ちたとき、機体は立て直さない
```

`--file-only`は開発ホスト向けで、`--no-restart`が効くのは`site_manager`へ届かず
ファイル経由になったときだけです（ROS経由で通った場合、立て直すかどうかを決めるのは
機体側の`config_sentinel`なので、こちらからは止められません）。

名前は`src/daifuku_config/overrides/<名前>.yaml`を指します。**地図はそのファイル自身が`site:`節で
宣言します**（`site: map: <フォルダ>/<ファイル名>`。`daifuku_stack`の`maps/`からの相対パス）ので、
overridesの名前と地図のファイル名は揃っていなくて構いません。切り替えたあとは
`navigation`を立て直すだけで、`map:=`も`overrides:=`も渡す必要はありません（どちらも
この1行から来ます）。**立て直したときは機体を静止させておいてください**——Mid-360の
ジャイロの電源投入時バイアスをそこで測ります。

**機体側は起動時にしかこの値を読みません。** ファイルを直しただけでは、LiDARの帯だけが
前の場所のまま走ることになります。これを塞いでいるのが常駐している2つのノードです。

- **`site_manager`**（`robot_bringup`が立てる。機体側に1つだけ）——`src/daifuku_config/site`の
  読み書きと告知。`ros2 param set /site_manager site <名前>`でも切り替えられ、**書く前に
  両パッケージについて検査する**ので、綴り違いや壊れた`overrides`はファイルに残りません。
  素手で直したときのために2秒ごとに読み直し、いまの値を`/daifuku/site`へlatchします
- **`config_sentinel`**（各launchが1つずつ立てる）——自分が起動時に読んだ設定と場所が
  書き変わっていないかを見張ります。変化を見つけたらまずログに出し、**追随してよい構成で・
  その設定でも立つことを確かめ・機体が止まっていれば**、自分のlaunchを終了します。機体
  （`raspicat`サービス）は`restart: unless-stopped`で上がり直し、人が立てた`navigation`は
  そのまま終わります

```bash
ros2 param get /site_manager site        # 今どこか
ros2 param set /site_manager site map_19f
ros2 topic echo /daifuku/site            # 流れている値
```

`tools/site.sh`は機体が上がっていれば`site_manager`へ渡し、居なければファイルを直接書きます。
見張りを止めたいときは各launchに`config_watch:=warn`（言うだけ）か`off`（見張りごと
立てない）を渡します。**走行中は落としません**——上がり直した先で`prepare_mid360_imu`が
ジャイロのバイアスを測れず（静止区間が要る）、補正なしのまま走り出すためです。

`map:=`を明示することもできますが、`overrides`の`site: map:`と別のファイルを指していると
**起動時にエラーで止まります**。地図だけ差し替えて調整を置き忘れると、別の場所の帯と
`emcl2`のリセット閾値を載せたまま走ることになるためです。承知のうえでやるなら
`overrides:=none`を添えてください（そのときは`map:=`が要ります）。

**この1行に入っていない場所依存が1つあります**——`joy_teleop`の`waypoints_file`
（パッド巡回の順路）です。既定は空で、空のあいだはSTART+BACKが巡回を断ります。絶対パスな
ので場所ごとの`overrides`には書きにくく、いまは手で渡す扱いのままです
（`src/daifuku_config/bringup/robot/joy_teleop.yaml`）。

> かつてはリポジトリルートの`.env`の`OVERRIDES`でした（2026-08-07まで）。`.env`は
> `COMPOSE_FILE`や`INPUT_GID`のように「機体を仕立てるときに1度決める」値の置き場で、
> 運ぶたびに変わるものを混ぜると忘れます。加えて環境変数はコンテナ生成時に焼かれるため、
> 変えるのに`docker compose up -d`（作り直し）が要りました。ファイルなら`restart`で足ります。

## ログを見る

```bash
docker compose logs
docker compose logs -f ros2
bash tools/control.sh logs -f
```

コンテナは`HOME=/tmp`で動くため、ROS 2のログファイルは`/tmp/ros/log`に出力されます。
コンテナを作り直すと消えるので、残したいログはホストへ取り出してください。

```bash
docker compose exec ros2 ls /tmp/ros/log
docker compose cp ros2:/tmp/ros/log ./ros_log
```

走行が破綻した原因を後から追うなら、ログだけでなくトピックごと録っておきます
（[走行を記録して再生する](recording.md)）。

## 終了する

走行を伴う作業のあとは、コンテナを止める前にモーター電源を切ります。

```bash
bash tools/control.sh motor off
docker compose down
```

ネイティブ環境ではlaunchを実行したターミナルで`Ctrl+C`を押します。停止後も機体側ドライバが動いている場合があるため、必要に応じて機体側も安全に停止してください。
