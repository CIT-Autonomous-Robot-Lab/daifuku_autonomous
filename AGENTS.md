# AGENTS.md

このリポジトリで作業するエージェント（Claude Code / Codex）向けの指針です。**指針の
実体はこのファイルだけ**で、`CLAUDE.md` は取り込むだけの入口です（2 つに分けていた
2026-08-08 まで、AGENTS.md が config 統合前で止まったままパッケージ数もパスも嘘に
なっていました）。

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

自前は 7 つです。`src/` の下が `daifuku_bringup` と `daifuku_stack` と
`daifuku_config` と `daifuku_config_manager` と `raspicat_driver` と
`daifuku_rqt` と `daifuku_waypoint_manager` で、残りは
`daifuku_autonomous.repos` からの `vcs import` です。

自前パッケージの役割分担は次のとおりで、**`daifuku_bringup` と `daifuku_stack` は
互いに依存しません**（どちらも `daifuku_config_manager` と `daifuku_config` にだけ
依存する）。

| パッケージ | 持つもの |
| --- | --- |
| `daifuku_bringup` | 機体。駆動ドライバ・URDF・cmd_vel の仲裁・ゲームパッド・**LiDAR**・**EKF**。`docker compose up` で常駐する |
| `daifuku_stack` | 自律移動。Nav2 / emcl2 / VI の launch、地図、ウェイポイント、RViz |
| `daifuku_config_manager` | 設定の合成規則（`params.py`）と、`site_manager` / `config_sentinel`（設定が書き変わったことを見つける役。**どちらも他を import しない**）。**設定の実体は持たない** |
| `daifuku_config` | 設定の実体だけ。`bringup/` と `stack/` と `overrides/` と `site` |

`vcs import` で入るものを直しても本リポジトリのコミットには入らないので、上流を
直す必要があれば向こうで作業してください。

罠が 2 つあります。`raspicat_ros` / `raspicat_description` / `raspimouse2` は
`.gitignore` に**書かれていない**ので、Linux・Docker のチェックアウトでは untracked で
現れます。それでも**コミット対象ではありません**。もう 1 つ、`src/value_iteration3`
（`vi_planner` の実装）は**独自の `AGENTS.md` を持つ**ので、中を
触る前にそちらを読むこと。

## ビルド

開発ホストは Windows ですが、ビルドは Docker（`docker/raspberrypi/`）か Ubuntu 22.04
ネイティブで行います。

```bash
# Docker（up のたびにコンテナ内で colcon build する。初回は Pi 4 で 1〜2 時間）
# **リポジトリルートから叩くこと**。compose ファイルは .env の COMPOSE_FILE で選ぶ
docker compose build
docker compose up -d
BUILD_JOBS=1 docker compose up -d   # 低メモリ時

# ネイティブ（Ubuntu 22.04 / ROS 2 Humble）
bash tools/setup/setup_native.sh              # --jobs 1 / --no-livox / --no-vi
```

何を変えたかで、やり直す範囲が変わります（詳細は
[`docs/usage/operations.md`](docs/usage/operations.md#設定変更を反映する)）。

| 変えたもの | やること |
| --- | --- |
| `daifuku_bringup` / `daifuku_stack` / `daifuku_config_manager` / `raspicat_driver` の Python・launch・地図など | 何もしない。ノード再起動だけで反映される（`--symlink-install` のため）。ただし `raspicat_driver` の `setup.py` に `entry_points` を足したときはビルドが要る |
| `src/daifuku_config/` の値（`bringup/` `stack/` `overrides/` のどれでも） | 同上（ビルドは要らない）。ただし**走らせたまま直すと `config_sentinel` がその launch を落とす**。機体は `restart: unless-stopped` が上げ直すが、**`navigation` と `mapping` は人が立てたものなので終わったまま**になる（mapping では作りかけの地図が消える）。長丁場は `config_watch:=warn` |
| C++ / Rust のコード、`CMakeLists.txt`、外部パッケージのソース | `docker compose up`（差分ビルド） |
| apt 依存、`Dockerfile`、`package.xml`、`docker/` 配下のスクリプト | `docker compose build` からやり直す |

**ファイルを新しく足したとき・動かしたときだけは例外**です。`install/` の symlink は
ビルド時に張られるので、一度 `up`（ネイティブなら `colcon build`）を通してください。
名前を変えたり移したりしたときは、それに加えて**古い symlink が `install/` に残ります**。
両方あるように見えて新しいほうしか更新されないので、紛らわしければ `install/` を消して
からビルドしてください。

`launch` の `Node(executable=...)` で立てる Python は、**git 側で実行ビットを立てて
おくこと**（`git update-index --chmod=+x <path>`）。`install(PROGRAMS)` は本来
実行ビットを付けて入れますが、`--symlink-install` だと `install/` はソースへの
symlink になるので、効くのは**ソース側の権限**です。Windows のチェックアウトは
`core.fileMode=false` なので `chmod` しても記録されず、**Linux で初めて
`Permission denied` になります**。`import` されるだけのもの（`joy_buttons.py`）は
立てなくてよい。

## テストと起動

自動テストはありません。`colcon test` で走るのは lint だけです（`raspicat_driver` の
`test/test_control.py` が唯一の例外でしたが、2026-08-09 に PI 補正ごと消しました）。
挙動の確認は実機か `simulator/` のハーネスで行います。単体で回せるのは
`simulator/tests/` の 2 つ（`map-to-usd` の出力検算と、地図の `free_thresh` の検算）
だけです。

**実機で通すぶんは `tools/checklist/` にあります。** `colcon test` からは走りません
（人に聞く項も機体が動く項もあるため）。使いかたと番号の意味は `checkall.sh` の冒頭に
あるので**ここには写しません**。段の 01 は静的検査で、このファイルが述べている約束ごと
（ヘッダの位置・lint の顔ぶれ・見張りの立て方・順路のトピック名）をそのまま突き
合わせます。**ここを直したら 0103 も直すこと。**

lint は詰め合わせ（`ament_lint_common`）を使わず、自前 7 パッケージが**同じものを
名指し**しています。走るのは 7 つ全部で copyright、Python を持つ 5 つで flake8、
`ament_cmake` の 4 つで lint_cmake と xmllint（`daifuku_waypoint_manager` と
`daifuku_config` は Python が無いので flake8 が無い）。踏むのは 4 つ:

- **`.py` / `.cpp` / `.hpp` を足したら Apache-2.0 のヘッダが要る**（`# Copyright 2026
  Keita Sekiguchi / nop` + `ament_copyright` のテンプレート逐語）。**置くのは
  ファイルの先頭**で、`ament_copyright` は最初のコメント塊しか見ないので、`.hpp` の
  `#ifndef` や launch 冒頭の説明コメントの下に置くと**見つからない**。逆に
  `CMakeLists.txt` は見られない（拡張子で選ぶので `.cmake` だけ）し、`package.xml` の
  隣の `setup.py` も除外される。
- **`ament_lint_common` を戻さないこと。** C++ の書式 lint（uncrustify / cpplint）が
  `daifuku_waypoint_manager` の移植コードで落ち、pep257 は「要約は 1 行目」と `。`
  で終わる日本語 docstring を全部弾く（2026-08-07 の実測で 330 件）。同じ理由で
  `ament_cmake_pep257` / `ament_pep257` も入れない。
- **`ament_python` の 3 つは `test/test_*.py` が実体。** `test_depend` に足すだけでは
  何も走らない（2026-08-07 まで `daifuku_rqt` が宣言だけで `test/` を持たず、lint が
  一度も走っていなかった）。
- **`daifuku_bringup` / `daifuku_stack` の flake8 は launch と `src/` の Python に
  効く。** `ament_cmake` のパッケージだが中身は Python なので、`ament_cmake_flake8`
  のほうを入れてある（`ament_flake8` は `ament_python` 用で、CMake からは走らない）。

```bash
cd simulator && uv run python tests/verify_usda.py <map.yaml> <world.usda> free
cd simulator && uv run python tests/verify_map_thresholds.py ../src/daifuku_stack/maps/*.yaml
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

- `src/daifuku_config/stack/nav2/*.yaml` と `src/daifuku_config/stack/vi_planner.yaml` はファイル名順に 1 つの
  `params_file` へ束ねられる（`vi_planner` は Nav2 のノードではないので `nav2/` の外に
  居るが、**合成には入れる** — 上流の `navigation_launch.py` は `params_file` を 1 つしか
  受け取らず、`overrides` が重なれるノード名もこの合成結果で決まるため）。ノード単位で
  分けるのが前提で、**同じノード名が 2 つの断片にあると起動時にエラーで止まる**。
  キーが重なっていなくても止まるので、1 つのノードの設定を 2 ファイルに割れない。
  断片どうしは深くマージしない（深いマージが効くのは `overrides` を重ねるときだけ）。
- **場所は 1 つの値で決まる — `src/daifuku_config/site` の 1 行。**
  すべての launch が `overrides` の既定をここから取り、`navigation.launch.py` は
  `map` の既定もそこから導く。**導き方は「同じ名前の地図」ではなく、その overrides
  自身が書いている `site: map:`**（2026-08-07 に改めた。`nav2_params.declared_map` /
  `resolve_map`）。だから overrides の名前と地図のファイル名は揃っていなくてよい。
  `map:=` を明示したときに `site: map:` と別のファイルを指していると**起動時に
  エラーで止まる**（別の場所の帯と emcl2 の調整を載せたまま走るのを防ぐため。承知で
  やるなら `overrides:=none` を添える）。**地図が決まらないとき（`overrides:=none`、
  または `site: map:` の無い overrides）は `map:=` が必須で、既定の地図へは落とさずに
  止まる** — 別の場所にいるのに 19F の地図で自己位置を推定し始めるほうが危ないため。
  `site:` は 1 段目に書ける予約節（`RESERVED_SECTIONS`）で、パッケージ名の段には
  並べない。`overrides` は
  **置き換え**（追加ではない）で、重ねないときは `overrides:=none`（空文字は
  `ros2 launch` が弾く）。**切り替えは `tools/site.sh <名前>`。** LiDAR の帯を読むのは
  `daifuku_bringup`（= 常駐している raspicat サービス）で**起動時にしか読まない**ので、
  素手でファイルを直したときは `docker compose restart raspicat` が要る（スクリプトは
  そこまでやる）。`.env` の `OVERRIDES` は 2026-08-07 に廃止した — 環境変数はコンテナ
  生成時に焼かれるので作り直しが要り、かつ「仕立てるときに 1 度決める」値と混ざって
  忘れやすかった。**環境変数 `OVERRIDES` 自体はファイルより強いまま残してある**が、
  compose はもう渡さない（`simulator/` が 1 回きりの構成を渡す口）。
- **`site_manager` を立てるのは `robot_bringup` の 1 か所だけ。** 2 つ立てると同じ
  `src/daifuku_config/site` を 2 つのノードが書きに行く。機体は常駐しているので、人が navigation を
  立てていないあいだも `ros2 param set /site_manager site <名前>` が通る。対になる
  `config_sentinel` は逆に**各 top-level launch が 1 つずつ**立てる（`sentinel_actions`
  を `include` される側でも呼ぶと、1 つの launch 木に見張りが 3 つ立ってそれぞれが
  勝手に落としにかかる）。指紋を取るのは `config_root` の下の yaml **全部**だが、
  **リンク切れは飛ばす**（設定を別のパッケージへ移すと `install/` に古い symlink が
  残り、読みにいくと launch ごと落ちる。2026-08-07 の実機で `daifuku_stack` の share に
  当時の `config/robot/joy_teleop.yaml` が居た。**設定を丸ごと `src/daifuku_config/` へ出した
  ときも同じことが起きる**ので、あの移行のあとは一度 `install/` を作り直すこと）。飛ばしたものは起動時のログに出るので、
  `find <install> -xtype l -delete` で掃除すること — **そのファイルは見張りの対象にも
  入っていない**。落とす合図の `SENTINEL_RESTART_CODE` を **0 にしないこと** —
  `OnProcessExit` → `EmitEvent(Shutdown)` が 0 で発火すると、ノードがバグで落ちただけでも
  機体が上がり直し、`restart: unless-stopped` と組んで止まらなくなる。
- **`overrides/*.yaml` の行き先はパッケージ名とノード名で決まる。** 1 段目が
  `daifuku_bringup:` か `daifuku_stack:` で、各 launch は**自分のパッケージ名の
  部分木しか読まない**。2 段目がノード名で、同じノード名を宣言している設定ファイル
  （そのパッケージの `src/daifuku_config/` の下のどれか）に重なる。落ちるのは 2 通り:
  **知らないパッケージ名**（`params.py` の `KNOWN_PACKAGES`。誰も読まない部分木に
  なるため）と、**そのパッケージのどの設定ファイルにも無いノード名**。どちらも
  綴り違いが黙って消えるのを防ぐため。ノード名を持たない
  `sensors/MID360_config.json` だけは上書きできない。
- **`overrides/*.yaml` の実体は `daifuku_config` にある。** 地図ごとの調整は
  LiDAR の帯（機体側）と emcl2 / VI（自律移動側）にまたがるので、どちらかに置くと
  他方がそちらへ依存してしまう。1 地図 = 1 ファイルのまま、葉のパッケージに置いて
  ある。
- **VI のノードは `vi_planner` 1 つだけ**（`local_planner` はその立ち方を選ぶ。
  `auto|vi` は両アクション、`nav2` は `follow: false` で広域だけ担わせて追従を
  `controller_server` に渡す）。**2026-08-08 の上流の整理まで後者は
  `vi_global_planner` という別パッケージ・別ノードで、両方立てると
  `compute_path_to_pose` にサーバが 2 つ載る、というのがここの罠だった**。消えたので、
  設定（`src/daifuku_config/stack/vi_planner.yaml` と `overrides/`）も検査も名前を
  `vi_planner` に一本化してある。**`follow` を `src/daifuku_config/` に書かないこと。**
  `local_planner` を見て渡すのは vi 版 `navigation_launch.py` で、あちらは
  `parameters` の後ろに置くので設定より強い（書いても効かない）。**危ないのは
  `nav2:=false`（既定）のほう** — こちらは `navigation.launch.py` が `vi_planner` を
  直に立てていて `follow` を渡さないので、`follow: false` と書いてあると
  **`follow_path` のサーバが立たないまま standalone が上がり、エラーも警告も出ない
  まま機体が追従しない**。`standalone` と対で、どちらも渡すのは launch だけ。
- **`vi_planner` は 2026-08-09 の上流の更新から自己位置推定も持つ（VIOLA）。使うかは
  launch の `localization:=vi`、どれを使うかは `config` の `localizer` で、この
  2 つは別の場所にある。** 噛み合わなければ起動時に止まる（`backends.
  validate_localization`）——どちらの向きも、通せば**エラーも警告も出ないまま自己
  位置だけが壊れる**ため。`localizer` が `external`（既定）でないのに `emcl2` を
  立てると、`map→odom` の出し手が 2 つになるうえ、`pose_topic` の意味が「自己位置の
  連続入力」ではなく**手動シード**に変わって `mcl_pose` の 20Hz で belief が張り
  直され、素通しと変わらなくなる。逆に `localization:=vi` なのに `external` のままだと、
  **誰も `map→odom` を出さないまま起動する**。`localization:=vi` は
  `planner:=vi` と `nav2:=false`（既定）が要る——`nav2:=true` の navigation は上流の
  `navigation_launch.py` 経由で、あちらに `publish_tf` を渡す口が無い。**その
  `publish_tf` は `standalone` / `follow` と同じ launch 専用のキーで、`src/daifuku_config/` に
  書かないこと**（`localizer` は逆に `src/daifuku_config/` にしか無い）。`localization:=vi` では
  `pose_topic` が `initialpose` になり、emcl2 は立たず `map_server` だけが残る。
  **2026-08-09 から既定の `map_19f` が `localizer: "belief"` を上書きしているので、
  この地図では `localization:=vi` のほうが要る** — 引数を足さずに（＝既定の
  `localization:=emcl2` で）立てると上の 1 つめに当たって起動時に止まる。`active_reloc`
  と組なので `solver` も密へ戻してあり（compact = アウトオブコアは単一ゴール専用で、
  そのままだと同じく起動時に止まる）、`map_scale: 2` と `waypoint_prefetch` の
  1.31GB がそこにぶら下がる。**津田沼は密だと 3.17GB で OOM なのでこの節を持てない。**
  **この地図で立つのは `localization:=vi planner:=vi nav2:=false` の 1 通りだけになる** —
  `planner:=navfn` / `local_planner:=nav2` / `nav2:=true` はどれも起動時に落ちる
  （前 2 つは `nav2:=auto|true` を要求し、それが `localization:=vi` と噛み合わない。
  `emcl2` へ落とせば今度は `localizer` の側で落ちる）。逃げ道は `overrides:=none` だけで、
  そちらは emcl2 の対症療法ごと落ちる。
- **`nav2` の既定は `false` で、そのとき Nav2 の navigation は BT ごと
  立たない。** `planner:=navfn` / `local_planner:=nav2` へ落とすときは
  **`nav2:=auto` を足さないと起動時にエラーで止まる**（`navigate_to_pose` を出す
  ものが居なくなるため。黙って Nav2 を立て直しはしない）。`simulator/` の
  ハーネスが `NAV2=auto` を既定にしているのはこれが理由。
  `nav2:=false` では `vi_planner` が `standalone` モードで
  `navigate_to_pose` と `follow_waypoints` も出すので、`bt_navigator` /
  `behavior_server` / `waypoint_follower` / `smoother_server` が要らなくなる。
  **`lifecycle_manager_navigation` は名前のまま残る**が、管理下は
  `velocity_smoother` 1 つだけになる（`velocity_smoother:=false` にすると
  lifecycle ノードが navigation 側から消える）。アクション型は `nav2_msgs` のままなので
  RViz も各パネルも配線は変わらない。**`standalone` を `src/daifuku_config/` に書かないこと** —
  真のまま Nav2 構成で起動すると `navigate_to_pose` のサーバが `bt_navigator` と 2 つに
  なり、クライアントは先に見つけたほうへ繋ぐ（**どちらに繋がったかはログにも
  `ros2 action list` にも出ない**）。渡すのは launch だけ。
- **`nav2:=false` では `src/daifuku_config/stack/nav2/{bt_navigator,controller_server,costmaps}.yaml`
  と `behavior_trees/` が丸ごと読まれず、`behaviors.yaml` も `velocity_smoother` の
  節だけになる**（そのノードだけは `nav2:=false` でも立つ）。合成には入る（ファイル名順に
  束ねる規則はそのまま）が、宛先のノードが立たないので**エラーも警告も出ないまま
  無視される**。`behaviors.yaml` の残り 3 ノード（`smoother_server` /
  `behavior_server` / `waypoint_follower`）がそれで、同名の設定を移した先は
  `src/daifuku_config/stack/vi_planner.yaml`（`stop_on_failure` / `waypoint_pause_sec`）。
  あちらを直しても効かない。BT の `RecoveryNode number_of_retries` に当たるものは
  同ファイルの `goal_retry_limit` で、こちらは名前が違う。
- **`twist_mux:=true`（既定）だと、機体が動くのは `/cmd_vel` ではなく
  `/cmd_vel_mux`。** 人が出す指令は `/cmd_vel_teleop`（優先度 10）へ。`/cmd_vel`
  （優先度 100）は自律側の出力で、**自律側のほうが勝つ**。どちらでもないトピックへ
  投げると**エラーは出ず、ただ機体が動かない**。**自律走行中は手動が通らない**ので、
  `control.sh teleop` と `control.sh stop`（ゴールを取り消さない）は自律側が
  `timeout`（0.5 秒）ぶん黙るまで効かず、**そのときもエラーは出ない**。優先度は
  非常停止ではないので、止めるのはモータ電源。
- **`joy:=true`（既定）の teleop は「押している間」ではなくモード。** START を
  `hold_seconds`（既定 2 秒）長押しして入れたあとは、ボタンを離しても切るまで続く。
  **パッドが自律側に勝てるのは優先度ではなく、入るときに `/follow_waypoints` と
  `/navigate_to_pose` のゴールを取り消して自律側を黙らせるから**（取り消さない
  `control.sh teleop` との違いはここ）。それでも自律側は最後の指令から 0.5 秒は
  勝つので、入った直後に効かない間がある。そのあいだ `joy_teleop` はスティックが
  中立でもゼロを出し続けるが、これは**ドライバの `cmd_vel_timeout`（既定 60 秒）に
  対する保険**で、自律側を押さえる働きは無い（twist_mux は勝っているトピックが
  メッセージを受けたときしか中継しない）。`twist_mux:=false` だと
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
  `src/daifuku_config/stack/lifecycle_bond.yaml` の `bond_timeout: 60.0` も同じ事情。
- **`daifuku_rqt` と `daifuku_waypoint_manager` は Pi では建てない。** 実機イメージ
  (`ros:humble-ros-base`) に rqt と RViz が無いため。両方の `build-workspace.sh` が
  `--packages-select` で名前を並べているので、Pi 側の一覧に足すと**ビルドが通らなく
  なる**。逆に `daifuku_bringup` と `daifuku_config_manager` と `daifuku_config` は**両方の
  一覧に要る**（実機はこの 3 つが無いと機体が上がらない）。一覧は 3 か所
  ——`docker/raspberrypi/scripts/build-workspace.sh`、`docker/dev/tools/build-workspace.sh`、
  `tools/setup/setup_native_base.sh`。
- **`docker/raspberrypi/` に `compose.yaml` は無い。** 入口は本体ドライバ別の
  `compose.rt.yaml`（公式実装 + rtmouse。Pi 4 専用）と `compose.original.yaml`
  （自前実装。既定、Pi 5 では必須）の 2 つで、どちらも `compose.common.yaml` を
  `include:` する。選ぶのは**リポジトリルートの `.env`** の `COMPOSE_FILE`
  （`.gitignore` 済み。`.env.example` から作る。`provision.sh` は機種を見て自動で
  作る）。ここに 3 つ罠がある。**(1)** Compose が `.env` を読むのは**カレント
  ディレクトリ**なので、リポジトリルート以外から `docker compose` を叩くと
  `no configuration file provided` で止まる。**(2)** 入口 2 つは `name:
  daifuku-autonomous` をわざと揃えてある。違えるとドライバを替えた瞬間に
  ビルドキャッシュの名前付きボリュームが別物になり、**1〜2 時間かけて建て直しに
  なる**（`include:` された側の `name:` は無視されるので、入口の側に要る）。
  **(3)** `compose.common.yaml` を単体で `-f` に渡すと `raspicat` が exit 1 で
  落ちる（どちらのドライバか決まらないまま起動しないための placeholder）。
  ドライバに依存しない `ros2` サービスだけを触る `tools/control.sh` と
  `tools/shell.sh` は、意図してこちらを直接渡している。
- **`.env` は 2 つ読まれ、値は合成される。** リポジトリルートのものと、
  `docker/raspberrypi/.env`（`provision.sh` が `ROS_DOMAIN_ID` と `BUILD_JOBS` を
  書いて生成する）の両方。**同じキーが両方にあると `docker/raspberrypi/.env` が
  勝つ**（Compose の project directory は `COMPOSE_FILE` の 1 つめがあるディレクトリ）。
  逆に `COMPOSE_FILE` はルート側でしか効かない。実機で「ルートの `.env` を直したのに
  効かない」はこれ。
- **`ros2` と `raspicat` は `restart: unless-stopped` なので Pi の再起動で戻るが、
  そのとき `workspace-build` は走らない。** デーモンが上げ直すときは `depends_on`
  が効かず、各コンテナが独立に上がるため。`install/` が名前付きボリュームに残るので
  それで動くが、**C++ / Rust を直した分は再起動しても反映されない**（`docker compose
  up -d` を人手で通すこと）。**LiDAR と EKF も `raspicat` サービスに入ったので、この
  性質はセンサ側にも及ぶ。**
- **ドライバが `finalized` まで落ちると launch ごと終了する**
  （`robot_bringup.launch.py` の `register_shutting_down_transition`）。LiDAR と EKF が
  同じ launch に居るので、**駆動の障害はセンサも道連れにし、`restart: unless-stopped`
  で全部が上がり直す**。踏むのは Pi 5 で `driver:=raspimouse` を選んだときのような
  設定の取り違えで、そこは直せば直る。
- **Mid-360 が LAN に居ないまま boot すると、コンテナは正常に上がったように見える。**
  `ros2 launch` は子ノードが死んでも終了しないので、`/livox/lidar` が来ないまま
  `restart: unless-stopped` の出番も無い。センサが「人が navigation を立てるとき」
  ではなく「boot 時」に上がるようになった副作用。**未検証**。`workspace-build` 側に `restart` を足してはいけない
  （正常終了でも上げ直すので、ビルドが終わるたびに次が始まる）。
- **`planner:=vi`（既定）では `navigate_through_poses` が使えない。** VI 系は
  `compute_path_to_pose` しか持たないので、`nav2:=true` では `navigation.launch.py` が
  through_poses の木を `behavior_trees/nav_through_poses_stub.xml`（`AlwaysFailure`）へ
  差し替え、**即座に ABORTED になってログにも何も出ない**。`nav2:=false` では
  `bt_navigator` 自体が立たないので**サーバがそもそも居ない**（クライアント側が
  「サーバがいません」で止まるぶん、こちらのほうが分かる）。複数点を回すなら
  `/follow_waypoints` を使うこと（`daifuku_waypoint_manager` はそちら。出すのは
  `nav2:=true` なら `nav2_waypoint_follower`、`nav2:=false` なら `vi_planner` 自身で、
  クライアントから見た型と名前は同じ）。
- **`nav2:=true` では、`vi_planner` の先読み（`waypoint_prefetch`）は `/waypoints` が
  来ないと何もしない。** 次のウェイポイントを走行中に解いておく機能だが、そちらの
  構成で「次」を知る手立ては順路そのものを latch する `nav_msgs/Path` だけで、これを
  出すのは `daifuku_waypoint_manager` のパネルと `joy_teleop`（START+BACK での巡回
  開始）の**2 か所しかない**。どちらも通らない経路（`/follow_waypoints` へ直接投げる、
  単発ゴール）では**エラーも警告も出ないまま先読みだけが起きない**。実機ではパネルが
  載らないので `joy_teleop` だけが出どころだが、そちらは **`waypoints_file` が空だと
  巡回そのものを断る**（2026-08-04 に既定の順路を廃止。それまでは津田沼の 73 点に
  フォールバックしていて、`map_19f` で立てると全点が地図の外に出た）。トピック名は
  パネルの `kWaypointPathTopic`、`joy_teleop` の publisher、`vi_planner` の
  `waypoint_topic` の 3 か所にあり、1 つだけ変えても同じことになる（パネルだけが
  絶対名なので、`namespace:=` を付けた構成でも噛み合わない）。**`nav2:=false` では
  この穴は無い** — `follow_waypoints` を `vi_planner` 自身が受けるので、順路はゴールと
  同じ経路で入る（トピックはもう 1 つの入口として残る）。**ノード側の宣言と
  `src/daifuku_config/stack/vi_planner.yaml` は `false` で、同梱の overrides で `true` へ
  上書きしているのは `map_19f` だけ**（津田沼は 2026-08-07 に `true` にしたあと
  2026-08-08 に `false` へ戻した。走行中の固まりの切り分けで、消える待ちは 19F が
  29 秒、津田沼が 87 秒）。価値関数が同時に 2 つ生きるので、**密ソルバでは
  メモリが 2 倍要る**。compact でも同梱の 2 地図は sink が RAM なので（2026-08-04 に
  津田沼の `compact_sink_dir` を外した）、そのまま 2 倍が匿名メモリに乗る
  （**19F は 2026-08-09 に密へ戻したので 655MB×2 = 1.31GB**。compact の頃は 95MB×2。
  津田沼は戻せば 648MB×2 = 1.3GB）。**Pi 4 (4GB) では `true` に
  しないこと。ただし既定の `map_19f` が `true` なので、引数を何も
  足さずに立てると Pi 4 でもこれが効く。** 外すには使う地図の
  `overrides/*.yaml` の `waypoint_prefetch` を消すか `false` と書くしかない（キー 1 つだけ外す launch 引数は無い。
  津田沼は後者で、断片と同値の `false` を切り分けの目印として明示してある。
  `overrides:=none` にすると emcl2 の 2 つの対症療法ごと落ちて、19F では自己位置が
  その場で回り出す（3 つめの `sensor_reset` は 2026-08-09 に断片ごと `false` に
  なったので、両 overrides から消えた）。2026-08-04 に一度**断片**で `true` へ反転したときは同日の実機で
  走行中の固まりが出て、容疑者の 1 つとして戻した（切り分けは未了）ので、
  再発したらまずここを疑う。
- **`vi_planner` の `early_start` は compact では効かない地図がある。** ゴールまで
  方策が繋がった時点で solve を打ち切る機能だが、compact（断片の solver。2026-08-09 に
  `map_19f` だけ密へ戻したので、compact なのは津田沼だけ）の確定は
  値バンド単位でしか進まず、そのバンド幅は「4 × 1 手の最大移動セル数（`action_forward_m`
  ÷ 解像度）× 最大ペナルティ（`safety_radius_penalty`）」（`couple_margin`）。**地図の値域が
  丸ごと 1 バンドに収まると波 2 つで解き終わって打ち切る隙が無く、エラーも警告も出ないまま
  何も短くならない**（建物 1 フロア程度はこちら側の見込み。効くのは津田沼のような広域地図と、
  密ソルバ）。距離への換算は [`docs/usage/navigation.md`](docs/usage/navigation.md)（19F で
  600 ステップ ≒ 300m）だが、**あれは式に値を入れただけで実測ではない**。前進量は
  バンド幅と 1 ステップの距離の両方に効くので、値を変えたら掛け直すのではなく式から出し直す
  こと。効いたかはログの `cut short` / `truncated` で見る。もう 1 つ、打ち切った場は**経路の外が
  未確定**なので、機体が経路から外れて方策が引けなくなると捨てて解き直す
  （`dropped the truncated value function`）。そのとき機体は**走行中に止まったまま**
  フルの solve を待つ（津田沼で 87 秒）ので、打ち切らなかったときより待ちは長い。
- **以下 2 つは `nav2:=true` のときの話。`nav2:=false` では `behavior_server` も
  `waypoint_follower` も立たないので起こらない**（`lifecycle_manager_navigation` は
  残るが、管理下が `velocity_smoother` 1 つなので停止順で固まる相手が居ない。
  代わりに投げ直しは `vi_planner` の `goal_retry_limit` と、その合間の 3 秒（2026-08-09 に
  ノード内の固定値になった。それまでは `goal_retry_settle_sec`）。
- **RViz の「Navigation 2」パネルは `navigation.rviz` から外してある**（下の
  `/waypoints` の項）。以下は自分で足したときの話。**`Reset` を押すと停止順で
  固まる。** 停止は逆順なので
  `velocity_smoother` が先に落ち、`waypoint_follower` の停止で（走りっぱなしの
  コールバックを待って）固まり、`behavior_server` だけが active で残る。
  `lifecycle_manager_navigation` は `is_active` にも応答しなくなり、抜けるには launch を
  立て直すしかない。**ここはかつてもっと悪く**、VI 版の `navigation_launch.py` が
  `behavior_server` に `cmd_vel` → `cmd_vel_nav` を張っていなかったので `spin` /
  `backup` だけが生の値で直接 `/cmd_vel` → twist_mux → 車輪へ届き、**自律走行は死んだまま
  回転だけが止まらない**状態になった。**2026-08-08 に上流（`6e803f7`）が張ったので、
  いまは `spin` も `velocity_smoother` を通る** —— 裏を返すと、
  `velocity_smoother` が落ちているときは以前と違って**回転も効かない**。**未検証**。
- **`navigation.rviz` に `nav2_rviz_plugins/Navigation 2` パネルを足さないこと。**
  あちらは `/waypoints` へ `visualization_msgs/MarkerArray` を、
  `daifuku_waypoint_manager/WaypointManagerPanel` は同じ名前へ
  `nav_msgs/Path` を出す。RViz は 1 プロセス = 1 参加者なので、後から立った
  ほうが `create_publisher() called for existing topic name rt/waypoints with
  incompatible type` で落ち、**そこで設定の読み込みが止まる** —— 自前パネルも
  `/waypoints` を見る表示も出ないまま、他の表示だけが揃って上がるので、
  **気づく手掛かりはログの 1 行だけ**（2026-08-19 に WSL の実測）。
  同じ理由で `/waypoints` を見る表示は
  `Path` でなければならない（`MarkerArray` にすると購読側で同じ衝突が起きる）。
  点そのものは `/waypoint_markers` の `Waypoint Manager` が出す。
  ツールの `Nav2 Goal`（`nav2_rviz_plugins/GoalTool`）はトピックを持たないので
  そのままでよい。
- **`navigation.rviz` の `2D Goal Pose` は `/goal_pose` を出さない。**
  `daifuku_waypoint_manager` へ waypoint を渡すため `/waypoint_pose` に付け替えて
  ある。単発ゴールは `Nav2 Goal` (`nav2_rviz_plugins/GoalTool`) のほうを使う。
  間違えても**エラーは出ず、ただ機体が動かない**（パネルに点が増えるだけ）。
- **teleop を出すものは自分で 0 を出して止める。** 自前実装（`driver:=original`）の
  `cmd_vel_timeout` は既定 60 秒で、指令が途切れてもその間は**走り続ける**。公式実装
  （既定の `driver:=raspimouse`）にはこのキーが無く、止まるかどうかは**未確認**。
- **emcl2 は 1 枚のスキャンを `odom_freq ÷ /scan の周期` 回だけ食う。** 実際に
  呼ばれる `ExpResetMcl2::sensorUpdate` には**「同じスキャンなら抜ける」ガードが
  無い**（`Mcl::sensorUpdate` のほうにはある）ので、`loop()` は毎回そのときの
  スキャンで重み付けとリサンプリングをやり直す。既定は `odom_freq` 20 Hz に対し
  `/scan` が `mid360_publish_freq` の 10 Hz なので**2 回**。同じ観測を独立な証拠と
  して二度数える形で、**エラーも警告も出ないまま自己位置がスキャン寄りに硬くなる**
  だけなので気付けない。どちらかを変えるときは倍率が変わることを承知で。
- **`elevation_filter`（既定 `true`）の `min_elevation_deg` と
  `pointcloud_to_laserscan` の `max_height` / `range_max` は組で決まる。** 仰角
  フィルタは `pointcloud_to_laserscan` の手前に入り、切り出しの下限を
  `lidar_z + 距離 × tan(min_elevation_deg)` へ変える（勾配のある床を落とすため。
  高さで切る限り、相対傾斜 α の床は `min_height/tan α` の先で必ず入ってくる）。
  下限が距離とともに上がるので、**`max_height` をその下に置くと帯が潰れ、
  `range_max` を伸ばしてもエラーも警告も出ないまま手前で何も入らなくなる**
  （5 度なら 70m 先の実効下限は 6.40m）。地図ごとの角度は `overrides/` 側。
  設定は `src/daifuku_config/bringup/sensors/` で、**変えたら `docker compose up -d`**
  （読むのは常駐している raspicat サービス）。
  `range_max` の既定 70.0 はセンサの測距上限だが、**そこまで使うのは `emcl2` だけ**
  （costmap は `obstacle_max_range: 2.5`、SLAM は `max_laser_range: 10.0` で頭打ち）。
  **`map_tsudanuma` は 2026-08-08 に `min_elevation_deg` を 5.0 から断片と同じ 0.0 へ
  戻したので、いまこの地図では仰角フィルタが実質素通し**（0.0 度 = 搭載高の水平面 =
  断片の `min_height: 0.275` と同じ切り方）。帯は全距離で 0.275〜4.00m の高さ帯に
  なっていて、`elevation_filter:=false` にしても**帯は変わらない**。組で決まるのは
  5.0 へ戻したときの話で、そのときは `max_height`（同日に 8.30 → 5.00 → 4.00 と
  下げた）が帯の届く距離をそのまま決める。**未検証**。
- **センサを立てるのは `robot_bringup.launch.py` だけ。** LiDAR（`/scan`）も EKF
  （`/odom`・`odom→base_footprint`）もそちらが `include` していて、**`docker compose up`
  で常駐している**。`navigation.launch.py` / `mapping.launch.py` は消費者に徹し、
  センサの引数を 1 つも持たない。手元で単独に立てるなら先に
  `ros2 launch daifuku_bringup robot_bringup.launch.py` を通すこと（`/scan` が
  来ないと emcl2 も costmap も動かない）。`simulator/` は駆動ドライバが要らないので、
  `nav_container.sh` / `run_case.sh` が `lidar_bringup.launch.py` と
  `odom_fusion.launch.py` を直接立てている。
- **`use_mid360_imu` は 1 つの launch に閉じている。** `robot_bringup.launch.py` が
  ドライバと EKF（`odom_fusion.launch.py`）の両方を立てるので、**片方だけ切り替わる
  状態は作れない**。`true`（既定）では `odom→base_footprint` と `/odom` の所有者が EKF
  になり、ドライバは `/wheel/odom` を出すだけになる（自前実装は `publish_tf: false`、
  公式実装は `publish_tf` が無いのでノードの `/tf` を捨て先へ remap）。既定値は環境
  変数 `USE_MID360_IMU` から取る（`daifuku_config_manager.env_bool_default`。読めない
  綴りは黙って既定に落とさず起動時に落とす）。**2026-08-07 より前は EKF が
  `lidar_bringup` 側に居て、2 つの launch へ同じ値を渡さないとエラーも警告も出ないまま
  自己位置が壊れた**（2026-08-05 の実機）。同じ launch に入れたのでその穴は無い。
- **`lidar:=2d` と `use_mid360_imu:=true` は同時に指定できない**（`robot_bringup` の
  `validate` が起動時に落とす）。URG に IMU は無いので、通すと EKF が `imu0` を一度も
  受け取らないまま車輪だけで回り、**融合しているつもりで融合していない**状態になる。
- **Mid-360 のジャイロは電源投入時バイアスが大きい**（この個体は z 軸
  +0.013960 rad/s = +0.800 deg/s = 48 deg/min。2026-08-05 実測）。
  `robot_localization` はセンサのバイアスを推定しないので、`prepare_mid360_imu` が
  起動後の静止区間から測って引く。**そのため起動時は機体を静止させておくこと**
  （動いていると測れないまま補正なしで通り、ログに `still moving` が出るだけ）。
  同じノードが加速度を g から m/s² へ直している（Livox が出すのは g）。
- **`prepare_mid360_imu` の前に `topic_tools throttle`（`imu_throttle`）が挟まって
  いて、これを外すと Pi 4 で 1 コアの 24% が戻ってくる。** rclpy はメッセージ 1 通
  ごとに約 1.2ms 払うので、Mid-360 の 200 Hz（ドライバ側に間引く口は無い。
  `publish_freq` は点群専用）を Python に直に食わせるとそれだけで 21 ポイント——
  自前の計算は 1.7 ポイントしかない（2026-08-09 実測。C++ を挟んで 24% → 10%）。
  踏むのは 2 つ。**この 1 段が落ちると `/imu/mid360` ごと止まり、EKF は `imu0` が
  一度も来ないまま車輪だけで回る**（`robot_localization` は黙るだけなので、エラーも
  警告も出ない）。もう 1 つ、**`bias_samples` は間引き後のレート前提**なので、
  throttle のレートだけ変えると静止窓の長さが掛け算で動く（50 指定 = 実測 44 Hz に
  対して 100 サンプル = 2.3 秒）。**レートを触ったら `tools/checklist/
  section-0501-imu-odom.sh` の hz 判定も動く。**
- TF は区間ごとに所有者を 1 つだけにする（`map→odom` は emcl2/amcl、
  `odom→base_footprint` は本体ドライバ（raspimouse / raspicat_driver）または EKF、
  リンク間は robot_state_publisher）。
  二重に出すと**自己位置だけが静かに壊れる**。
- **`IncludeLaunchDescription` の `launch_arguments` は親と同じ文脈に積まれる。**
  囲まないと**後ろに並ぶ include まで巻き添えになり**、向こうの
  `DeclareLaunchArgument` は既定を入れられない（宣言済みの値が勝つ）。
  `robot_bringup.launch.py` は上流の robot_state_publisher へ `lidar_frame` を
  渡すが、これは `lidar_bringup.launch.py` が Mid-360 用に宣言している名前と
  同じなので、**`GroupAction` で囲むこと**（引数名を分けても、上流へ渡すときの
  名前は変えられないので止まらない）。漏れると Livox ドライバの `frame_id` と
  `base_footprint→*` の静的 TF が `lidar_link` になる一方、**IMU だけは
  `livox_ros_driver2` が `frame_id` を無視して `livox_frame` をべた書きする**
  （`lddc.cpp` の `Lddc::PublishImuData`）ので、EKF が引く TF が消える。
  `robot_localization` は `transform_timeout`（0.1 秒）ぶん毎回ブロックするだけで
  **エラーも警告も出さない**ため、IMU が丸ごと捨てられたまま 30 Hz が 5 Hz に落ち、
  `odom→base_footprint` が 0.5 秒古くなる。すると emcl2 の `publishOdomFrame` は
  スキャンの時刻（`restamp_scan` が `now()` で打ち直すので**系内で最も新しい**）で
  `base_footprint→odom` をタイムアウト 0 で引いて必ず extrapolation で落ち、
  それを `RCLCPP_DEBUG` で握り潰すので、**`/mcl_pose` は 20 Hz で出続けるのに
  `map→odom` だけが 1 度も出ない**（RViz は Fixed Frame が `map` なので全部消える。
  2026-08-07 の実機）。

## 設定ファイル (`src/daifuku_config/**/*.yaml`) のコメント

- **1 行でまとめる。** キーの右に `# 既定 <ノード既定値>: <説明>` の形で書き、既存の行と
  同じ書式・同じ語彙にそろえる。キーの上に段落を積まない。実測値は 1 行に収まる範囲で
  入れてよいが、導出や背景は `src/daifuku_config/README.md` / `docs/` / 実装 (例:
  `vi_rs/vi_planner/src/core/mod.rs` 冒頭) に置いて参照で済ませる。
- 「既定」= 各ノードの `main.rs`（`vi_planner` は `src/node/params.rs`）などが持つ
  宣言時の値、`overrides/` での「断片」=
  重ねる先の設定ファイル（そのノードを宣言している `src/daifuku_config/stack/nav2/*.yaml` や
  `src/daifuku_config/stack/vi_planner.yaml`、
  `src/daifuku_config/stack/localization/emcl2.yaml` など）の値。値を変えたら `既定 同左` や
  `# 断片 <値>:`、ファイル冒頭の
  「変えてあるのは○○だけ」といった要約も同じ変更で追随させる。

## 触る前に読むもの

| 触るもの | 先に読む |
| --- | --- |
| `src/daifuku_config/` の yaml の値 | [`src/daifuku_config/README.md`](src/daifuku_config/README.md)（合成・override の仕組みと、各値の由来。機体側の値もここにまとまっている） |
| `overrides/` / 設定の合成そのもの | `src/daifuku_config_manager/src/daifuku_config_manager/params.py` の冒頭 |
| `launch/` | [`docs/usage/architecture.md`](docs/usage/architecture.md#launchファイルの構成) |
| `simulator/`（Isaac 版 / pi4_sim 版） | [`simulator/docs/pi4_sim.md`](simulator/docs/pi4_sim.md) を先に、次に [`simulator/README.md`](simulator/README.md) |
| `docker/` | [`docker/README.md`](docker/README.md)（実機用と開発用の 2 環境） |
| `src/daifuku_bringup/`（LiDAR・EKF・駆動の launch） | [`docs/setup/lidar.md`](docs/setup/lidar.md)、次に [`docs/usage/architecture.md`](docs/usage/architecture.md#launchファイルの構成) |
| `src/raspicat_driver/` / `tools/image/udev/` | [`src/raspicat_driver/README.md`](src/raspicat_driver/README.md)、次に [`docs/setup/raspberry-pi-4.md`](docs/setup/raspberry-pi-4.md) と [`raspberry-pi-5.md`](docs/setup/raspberry-pi-5.md)（未検証の項目付き） |
| `src/daifuku_rqt/` | [`src/daifuku_rqt/README.md`](src/daifuku_rqt/README.md)、次に [`docs/usage/control-panel.md`](docs/usage/control-panel.md) |
| `src/daifuku_waypoint_manager/` / `daifuku_stack/waypoints/` | [`src/daifuku_waypoint_manager/README.md`](src/daifuku_waypoint_manager/README.md) |
| `src/value_iteration3/` | 同ディレクトリの `AGENTS.md` |
| 実機の症状を追う | [`docs/usage/troubleshooting.md`](docs/usage/troubleshooting.md) |

ドキュメントは日本語で書かれています。追記も日本語でそろえてください。
