# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 概要

Raspberry Pi Cat を ROS 2 Humble / Nav2 で自律移動させる colcon ワークスペース。
全体像は [`README.md`](README.md) と [`docs/usage/architecture.md`](docs/usage/architecture.md)。

## このファイルに書くこと

**コードを読んでもわからないことだけ**を書きます。複数のファイルにまたがる制約、実機や
実行時にしか現れない失敗、意図があってその値にしてある既定値の 3 つです。ファイル構成・
パッケージの中身・launch 引数の既定値・依存の一覧のように、`ls` や `--show-args` や当該
ファイルを 1 つ開けば確かめられることは**書きません**（二重に持つと古くなって嘘になる）。

追記するときは、その 1 行が失敗（何が壊れるか）を述べているかを見てください。述べて
いないなら、それは実装かドキュメントの側の仕事です。

## リポジトリの範囲

`src/` の下で自前なのは `daifuku_stack` と `raspicat_driver` と `daifuku_rqt` と
`daifuku_waypoint_manager` だけで、
残りは `autonomous_bot.repos` からの `vcs import` です。`vcs import` で入るものを直しても本
リポジトリのコミットには入らないので、上流を直す必要があれば向こうで作業してください。

罠が 2 つあります。`raspicat_ros` / `raspicat_description` / `raspimouse2` は
`.gitignore` に**書かれていない**ので、Linux・Docker のチェックアウトでは untracked で
現れます。それでも**コミット対象ではありません**。もう 1 つ、`src/value_iteration3`
（`vi_planner` / `vi_global_planner` の実装）は**独自の `CLAUDE.md` を持つ**ので、中を
触る前にそちらを読むこと。

## ビルド

開発ホストは Windows ですが、ビルドは Docker（`docker/raspberrypi/`）か Ubuntu 22.04
ネイティブで行います。

```bash
# Docker（up のたびにコンテナ内で colcon build する。初回は Pi 4 で 1〜2 時間）
docker compose -f docker/raspberrypi/compose.yaml build
docker compose -f docker/raspberrypi/compose.yaml up -d
BUILD_JOBS=1 docker compose -f docker/raspberrypi/compose.yaml up -d   # 低メモリ時

# ネイティブ（Ubuntu 22.04 / ROS 2 Humble）
bash tools/setup/setup_native.sh              # --jobs 1 / --no-livox / --no-vi
```

何を変えたかで、やり直す範囲が変わります（詳細は
[`docs/usage/operations.md`](docs/usage/operations.md#設定変更を反映する)）。

| 変えたもの | やること |
| --- | --- |
| `daifuku_stack` / `raspicat_driver` の Python・launch・config・地図など | 何もしない。ノード再起動だけで反映される（`--symlink-install` のため）。ただし `raspicat_driver` の `setup.py` に `entry_points` を足したときはビルドが要る |
| C++ / Rust のコード、`CMakeLists.txt`、外部パッケージのソース | `docker compose up`（差分ビルド） |
| apt 依存、`Dockerfile`、`package.xml`、`docker/` 配下のスクリプト | `docker compose build` からやり直す |

**ファイルを新しく足したとき・動かしたときだけは例外**です。`install/` の symlink は
ビルド時に張られるので、一度 `up`（ネイティブなら `colcon build`）を通してください。
名前を変えたり移したりしたときは、それに加えて**古い symlink が `install/` に残ります**。
両方あるように見えて新しいほうしか更新されないので、紛らわしければ `install/` を消して
からビルドしてください。

## テストと起動

自動テストは実質ありません。`colcon test` で走るのは lint だけで、独自テストを持つ
パッケージはありません。挙動の確認は実機か `simulator/` のハーネスで行います。例外は
`map-to-usd` の出力検算で、これだけは単体で回せます。

```bash
cd simulator && uv run python tests/verify_usda.py <map.yaml> <world.usda> free
```

```bash
ros2 launch daifuku_stack navigation.launch.py   # 自律移動（引数は --show-args）
ros2 launch daifuku_stack mapping.launch.py      # 地図作成（SLAM Toolbox）
```

Docker 越しに叩く形は
[`docs/usage/README.md`](docs/usage/README.md#コマンドの読み替え)、実運用の手順は
[`docs/usage/navigation.md`](docs/usage/navigation.md)。

## ファイルをまたぐ約束ごと

理由と実測は各ドキュメント側にあります。ここは「知らずに壊す」ものだけ。

- `config/nav2/*.yaml` はファイル名順に 1 つの `params_file` へ束ねられる。ノード単位で
  分けるのが前提で、**同じノード名が 2 つの断片にあると起動時にエラーで止まる**。
  キーが重なっていなくても止まるので、1 つのノードの設定を 2 ファイルに割れない。
  断片どうしは深くマージしない（深いマージが効くのは `overrides` を重ねるときだけ）。
- `overrides` の既定は **`map_19f`** で、**置き換え**（追加ではない）。`map:=` を
  変えたら `overrides:=` も必ず変える。重ねないときは `overrides:=none`（空文字は
  `ros2 launch` が弾く）。4 つの launch すべてが同じ既定で受ける。
- **`overrides/*.yaml` の行き先はノード名だけで決まる。** 同じノード名を宣言して
  いる設定ファイル（`config/` の下のどれか）に重なるので、`emcl2:` も
  `slam_toolbox:` も `raspicat_driver:` も 1 つの override に書ける。どの設定
  ファイルにも無いノード名は**起動時にエラーで止まる**（綴り違いが黙って消えると
  探せないため）。ノード名を持たない `sensors/MID360_config.json` だけは上書き
  できない。
- `vi_planner`（`local_planner:=auto|vi`）と `vi_global_planner`（`local_planner:=nav2`）は
  **排他**。両方立てると `compute_path_to_pose` にサーバが 2 つ載る。
- **`twist_mux:=true`（既定）だと、機体が動くのは `/cmd_vel` ではなく
  `/cmd_vel_mux`。** 人が出す指令は `/cmd_vel_teleop`（優先度 100）へ。`/cmd_vel`
  （優先度 10）は自律側の出力で、そちらへ投げると自律走行中は取り合いになる（誰も
  出していなければ届く）。どちらでもないトピックへ投げると**エラーは出ず、ただ機体が
  動かない**。優先度は非常停止ではない（出しているあいだ + 0.5 秒だけ勝つ）ので、
  止めるのはモータ電源。
- **`joy:=true`（既定）の teleop は「押している間」ではなくモード。** START 3 秒で
  入れているあいだ `joy_teleop` はスティックが中立でもゼロを出し続けるので、
  **そのあいだ自律側の `/cmd_vel` は twist_mux を通らない**（出しっぱなしにしないと
  手を離した 0.5 秒後に自律が勝って走り出す）。入るときに `/follow_waypoints` と
  `/navigate_to_pose` のゴールを取り消すのも同じ事情で、優先度で押さえるだけでは
  teleop を切った瞬間に元のゴールが再開する。`twist_mux:=false` だと
  `/cmd_vel_teleop` を誰も購読しないので、**エラーも出さずに効かない**。
- **Pi 5 では rtmouse が動かない。** `robot_bringup.launch.py` の `driver:=` は既定が
  公式実装の `raspimouse`（`/dev/rt*` が要る）なので、Pi 5 でそのままだと configure で
  落ちる。自前実装の `original`（`src/raspicat_driver`）を指定すること。
- **rtmouse と `driver:=original` は排他。** rtmouse はレジスタを `ioremap` するので
  カーネルは衝突を検出しない（両方が GPIO 16/6/5 を持つと車輪が逆に回り得る）。
  Pi 4 で自前実装を使うなら rtmouse を載せないこと（`create_image.py --no-rtmouse`。
  ノード側も起動時に拒否する）。`config.txt` のオーバレイもこれで変わる
  （[`docs/setup/raspberry-pi-4.md`](docs/setup/raspberry-pi-4.md)）。
- `use_composition` の既定 `False` は意図的（Pi 4 でディスカバリ不能 + bond 心拍途絶）。
  `config/lifecycle_bond.yaml` の `bond_timeout: 60.0` も同じ事情。
- **`daifuku_rqt` と `daifuku_waypoint_manager` は Pi では建てない。** 実機イメージ
  (`ros:humble-ros-base`) に rqt と RViz が無いため。両方の `build-workspace.sh` が
  `--packages-select` で名前を並べているので、Pi 側の一覧に足すと**ビルドが通らなく
  なる**。
- **`planner:=vi`（既定）では `navigate_through_poses` が常に失敗する。** VI 系は
  `compute_path_to_pose` しか持たないので、`navigation.launch.py` が through_poses の
  木を `behavior_trees/nav_through_poses_stub.xml`（`AlwaysFailure`）へ差し替える。
  ここへ投げるものは**即座に ABORTED になり、ログにも何も出ない**。複数点を回すなら
  `nav2_waypoint_follower` の `/follow_waypoints` を使うこと（`daifuku_waypoint_manager`
  はそちら。両プランナ経路で lifecycle 管理下に立っている）。
- **`vi_planner` の先読み（`waypoint_prefetch`）は `/waypoints` が来ないと何も
  しない。** 次のウェイポイントを走行中に解いておく機能だが、「次」を知る手立ては
  順路そのものを latch する `nav_msgs/Path` だけで、これを出すのは
  `daifuku_waypoint_manager` のパネルと `joy_teleop`（START+BACK での巡回開始）の
  **2 か所しかない**。どちらも通らない経路（`/follow_waypoints` へ直接投げる、
  単発ゴール）では**エラーも警告も出ないまま先読みだけが起きない**。実機ではパネルが
  載らないので `joy_teleop` だけが出どころだが、そちらは **`waypoints_file` が空だと
  巡回そのものを断る**（2026-08-04 に既定の順路を廃止。それまでは津田沼の 73 点に
  フォールバックしていて、`map_19f` で立てると全点が地図の外に出た）。トピック名は
  パネルの `kWaypointPathTopic`、`joy_teleop` の publisher、`vi_planner` の
  `waypoint_topic` の 3 か所にあり、1 つだけ変えても同じことになる（パネルだけが
  絶対名なので、`namespace:=` を付けた構成でも噛み合わない）。**既定は `false`**
  （2026-08-04 に一度 `true` へ反転したが、同日の実機で走行中の固まりが出たため
  容疑者の 1 つとして戻した。ノード側の宣言も `false`）— 価値関数が同時に 2 つ
  生きるので、**密ソルバではメモリが 2 倍要る**。compact でも同梱の 2 地図は sink が
  RAM なので（2026-08-04 に津田沼の `compact_sink_dir` を外した）、そのまま 2 倍が
  匿名メモリに乗る（津田沼 648MB×2 = 1.3GB、19F 95MB×2）。**Pi 4 (4GB) では
  `true` にしないこと。**
- **`vi_planner` の `early_start` は compact では効かない地図がある。** ゴールまで
  方策が繋がった時点で solve を打ち切る機能だが、compact（同梱の既定 solver）の確定は
  値バンド単位でしか進まず、0.1 m/cell・`safety_radius_penalty: 30` で 1 バンドが
  約 500 ステップ = 150m 相当。**地図の値域が丸ごと 1 バンドに収まると波 2 つで解き
  終わって打ち切る隙が無く、エラーも警告も出ないまま何も短くならない**（建物 1 フロア
  程度はこちら側の見込み。効くのは津田沼のような広域地図と、密ソルバ）。効いたかは
  ログの `cut short` / `truncated` で見る。もう 1 つ、打ち切った場は**経路の外が
  未確定**なので、機体が経路から外れて方策が引けなくなると捨てて解き直す
  （`dropped the truncated value function`）。そのとき機体は**走行中に止まったまま**
  フルの solve を待つ（津田沼で 87 秒）ので、打ち切らなかったときより待ちは長い。
- **recovery の `spin` だけは `velocity_smoother` を通らない。** 上流 nav2 の
  `navigation_launch.py` は `behavior_server` に `cmd_vel` → `cmd_vel_nav` を張るが、
  `vi_global_planner` 側の複製（`local_planner:=vi` で使うほう）は張っていないので、
  **`spin` / `backup` は生の値で直接 `/cmd_vel` → twist_mux → 車輪へ届く**。結果、
  `velocity_smoother` が落ちていても回転だけは効く。ここで **RViz の「Navigation 2」
  パネルの `Reset` を押すと悪化する**：停止は逆順なので `velocity_smoother` が先に
  落ち、`waypoint_follower` の停止で（走りっぱなしのコールバックを待って）固まり、
  `behavior_server` だけが active で残る。**自律走行は死んだまま回転だけが止まらない**
  状態になり、`lifecycle_manager_navigation` は `is_active` にも応答しなくなる。
  抜けるには launch を立て直すしかない。
- **`navigation.rviz` の `2D Goal Pose` は `/goal_pose` を出さない。**
  `daifuku_waypoint_manager` へ waypoint を渡すため `/waypoint_pose` に付け替えて
  ある。単発ゴールは `Nav2 Goal` (`nav2_rviz_plugins/GoalTool`) のほうを使う。
  間違えても**エラーは出ず、ただ機体が動かない**（パネルに点が増えるだけ）。
- **teleop を出すものは自分で 0 を出して止める。** 自前実装（`driver:=original`）の
  `cmd_vel_timeout` は既定 60 秒で、指令が途切れてもその間は**走り続ける**。公式実装
  （既定の `driver:=raspimouse`）にはこのキーが無く、止まるかどうかは**未確認**。
- TF は区間ごとに所有者を 1 つだけにする（`map→odom` は emcl2/amcl、
  `odom→base_footprint` は本体ドライバ（raspimouse / raspicat_driver）または EKF、
  リンク間は robot_state_publisher）。
  二重に出すと**自己位置だけが静かに壊れる**。

## 設定ファイル (`src/daifuku_stack/config/**/*.yaml`) のコメント

- **1 行でまとめる。** キーの右に `# 既定 <ノード既定値>: <説明>` の形で書き、既存の行と
  同じ書式・同じ語彙にそろえる。キーの上に段落を積まない。実測値は 1 行に収まる範囲で
  入れてよいが、導出や背景は `config/README.md` / `docs/` / 実装 (例:
  `vi_planner/src/core/mod.rs` 冒頭) に置いて参照で済ませる。
- 「既定」= 各ノードの `main.rs` などが持つ宣言時の値、`overrides/` での「断片」=
  重ねる先の設定ファイル（そのノードを宣言している `config/nav2/*.yaml` や
  `config/localization/emcl2.yaml` など）の値。値を変えたら `既定 同左` や
  `# 断片 <値>:`、ファイル冒頭の
  「変えてあるのは○○だけ」といった要約も同じ変更で追随させる。

## 触る前に読むもの

| 触るもの | 先に読む |
| --- | --- |
| `config/` の yaml の値 | [`src/daifuku_stack/config/README.md`](src/daifuku_stack/config/README.md)（合成・override の仕組みと、各値の由来） |
| `launch/` | [`docs/usage/architecture.md`](docs/usage/architecture.md#launchファイルの構成) |
| `simulator/`（Isaac 版 / pi4_sim 版） | [`simulator/docs/pi4_sim.md`](simulator/docs/pi4_sim.md) を先に、次に [`simulator/README.md`](simulator/README.md) |
| `docker/` | [`docker/README.md`](docker/README.md)（実機用と開発用の 2 環境） |
| `src/raspicat_driver/` / `tools/image/udev/` | [`src/raspicat_driver/README.md`](src/raspicat_driver/README.md)、次に [`docs/setup/raspberry-pi-4.md`](docs/setup/raspberry-pi-4.md) と [`raspberry-pi-5.md`](docs/setup/raspberry-pi-5.md)（未検証の項目付き） |
| `src/daifuku_rqt/` | [`src/daifuku_rqt/README.md`](src/daifuku_rqt/README.md)、次に [`docs/usage/control-panel.md`](docs/usage/control-panel.md) |
| `src/daifuku_waypoint_manager/` / `daifuku_stack/waypoints/` | [`src/daifuku_waypoint_manager/README.md`](src/daifuku_waypoint_manager/README.md) |
| `src/value_iteration3/` | 同ディレクトリの `CLAUDE.md` |
| 実機の症状を追う | [`docs/usage/troubleshooting.md`](docs/usage/troubleshooting.md) |

ドキュメントは日本語で書かれています。追記も日本語でそろえてください。
