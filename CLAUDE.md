# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 概要

Raspberry Pi Cat を ROS 2 Humble / Nav2 で自律移動させる colcon ワークスペース。
全体像は [`README.md`](README.md) と [`docs/usage/architecture.md`](docs/usage/architecture.md)。

## このリポジトリのものと、そうでないもの

`src/` の下で**このリポジトリが持つのは `src/autonomous_nav` と `src/raspicat_driver`
の 2 つだけ**です。

| ディレクトリ | 出どころ |
| --- | --- |
| `src/autonomous_nav` | 本リポジトリ（launch・config・地図・RViz・Python ノード 2 本。C++ ノードは無い） |
| `src/raspicat_driver` | 本リポジトリ（本体ドライバの自前実装。Pi 4 / Pi 5 の両対応、ament_python） |
| `src/value_iteration3` | `autonomous_bot.repos` からの `vcs import`（`.gitignore` 済み） |
| `src/emcl2_ros2` | 同上 |
| `src/livox_ros_driver2` | 同上 |

`autonomous_bot.repos` はさらに `raspicat_ros` / `raspicat_description` /
`raspimouse2` も持ちます（Linux・Docker のチェックアウトには入る。この 3 つは
`.gitignore` に無いので、untracked で現れても**コミット対象ではありません**）。

`vcs import` で入るものを直しても、このリポジトリのコミットには入りません。上流を
直す必要がある場合は、そのリポジトリ側で作業してください。とくに
`src/value_iteration3`（`vi_planner` / `vi_global_planner` の実装）は**独自の
`CLAUDE.md` を持つ**ので、中を触る前にそちらを読むこと。

## ビルドと反映

開発ホストは Windows ですが、ビルドは Docker（`docker/raspberrypi/`）か Ubuntu 22.04
ネイティブで行います。`colcon build` は `--symlink-install` 付きで、
`--packages-select` で対象を絞っています（実際の一覧は
`docker/raspberrypi/scripts/build-workspace.sh` と `tools/setup/setup_native_*.sh` を
見ること）。CMake 側と Rust 側（`rclrs` のオーバーレイが要る）で 2 回に分かれます。

```bash
# Docker（up のたびにコンテナ内で colcon build する。初回は Pi4 で 1〜2 時間）
docker compose -f docker/raspberrypi/compose.yaml build
docker compose -f docker/raspberrypi/compose.yaml up -d
BUILD_JOBS=1 docker compose -f docker/raspberrypi/compose.yaml up -d   # 低メモリ時

# ネイティブ（Ubuntu 22.04 / ROS 2 Humble）
bash tools/setup/setup_native.sh              # --jobs 1 / --no-livox / --no-vi
```

何を変えたかで、やり直す範囲が 3 通りに分かれます（詳細は
[`docs/usage/operations.md`](docs/usage/operations.md#設定変更を反映する)）。

| 変えたもの | やること |
| --- | --- |
| `autonomous_nav` の launch・config・behavior_trees・maps・rviz | 何もしない。ノード再起動だけで反映される |
| `raspicat_driver` の Python | 同上（`--symlink-install` なので）。ただし `setup.py` の `entry_points` を増やしたときはビルドが要る |
| C++ / Rust のコード、`CMakeLists.txt`、外部パッケージのソース | `docker compose up`（差分ビルド） |
| apt 依存、`Dockerfile`、`package.xml`、`docker/` 配下のスクリプト | `docker compose build` からやり直す |

**ファイルを新しく足したときだけは例外**です。`install/` の symlink はビルド時に
張られるので、launch や config を追加したら一度 `up`（ネイティブなら `colcon build`）を
通してください。

## テスト

自動テストはほとんどありません。あるのは次の 2 つだけです。

- `colcon test`: `autonomous_nav` は `ament_lint_auto` / `ament_lint_common`、
  `raspicat_driver` は `ament_flake8` のみ（どちらも独自テストは無い）
- `cd simulator && uv run python tests/verify_usda.py <map.yaml> <world.usda> free`:
  `map-to-usd` の出力を地図に焼き戻して一致を検算する

挙動の確認は実機か `simulator/` のハーネスで行います。

## 起動

```bash
ros2 launch autonomous_nav navigation.launch.py   # 自律移動（引数は --show-args）
ros2 launch autonomous_nav mapping.launch.py      # 地図作成（SLAM Toolbox）
```

主な引数の既定は `localization:=emcl2` / `planner:=vi` / `local_planner:=auto` /
`lidar:=mid360` / `use_rviz:=false` / `use_sim_time:=false`。Docker 越しに叩く形は
[`docs/usage/README.md`](docs/usage/README.md#コマンドの読み替え)、実運用の手順は
[`docs/usage/navigation.md`](docs/usage/navigation.md)。

## ファイルをまたぐ約束ごと

理由と実測は各ドキュメント側にあります。ここは「知らずに壊す」ものだけ。

- `config/nav2/*.yaml` はファイル名順に**深くマージ**されて 1 つの `params_file` に
  なる。同じノードの同じキーが 2 つの断片にあると**起動時にエラーで止まる**。
- `overrides` の既定は **`map_19f`** で、**置き換え**（追加ではない）。`map:=` を
  変えたら `overrides:=` も必ず変える。重ねないときは `overrides:=none`（空文字は
  `ros2 launch` が弾く）。
- **emcl2 は `params_file` を通らない。** `navigation.launch.py` が
  `localization/emcl2.yaml` に override の `emcl2:` セクションだけを重ねて
  `emcl2_params_file` を作る。この経路が無いと `emcl2:` は**警告も出ずに無視される**。
- `vi_planner`（`local_planner:=auto|vi`）と `vi_global_planner`（`local_planner:=nav2`）は
  **排他**。両方立てると `compute_path_to_pose` にサーバが 2 つ載る。
- 本体ドライバは `robot_bringup.launch.py` の `driver:=` で選ぶ。既定 `raspimouse` は
  公式実装（rtmouse の `/dev/rt*` が要る。Pi 4 のみ）、`original` は自前実装
  （`src/raspicat_driver`。PWM・gpiochip・I2C を直接叩く。Pi 4 / Pi 5）。**Pi 5 では
  rtmouse が動かない**ので `driver:=raspimouse` のままだと configure で落ちる。
- **rtmouse と `driver:=original` は排他。** rtmouse はレジスタを `ioremap` するので
  カーネルは衝突を検出しない（両方が GPIO 16/6/5 を持つと車輪が逆に回り得る）。
  Pi 4 で自前実装を使うなら rtmouse を載せないこと（`create_image.py --no-rtmouse`。
  ノード側も起動時に拒否する）。`config.txt` のオーバレイもこれで変わる
  （[`docs/setup/raspberry-pi-4.md`](docs/setup/raspberry-pi-4.md)）。
- `use_composition` の既定 `False` は意図的（Pi4 でディスカバリ不能 + bond 心拍途絶）。
  `config/lifecycle_bond.yaml` の `bond_timeout: 60.0` も同じ事情。
- TF は区間ごとに所有者を 1 つだけにする（`map→odom` は emcl2/amcl、
  `odom→base_footprint` は本体ドライバ（raspimouse / raspicat_driver）または EKF、
  リンク間は robot_state_publisher）。
  二重に出すと**自己位置だけが静かに壊れる**。

## 設定ファイル (`src/autonomous_nav/config/**/*.yaml`) のコメント

- **1 行でまとめる。** キーの右に `# 既定 <ノード既定値>: <説明>` の形で書き、既存の行と
  同じ書式・同じ語彙にそろえる。キーの上に段落を積まない。実測値は 1 行に収まる範囲で
  入れてよいが、導出や背景は `config/README.md` / `docs/` / 実装 (例:
  `vi_planner/src/core.rs` 冒頭) に置いて参照で済ませる。
- 「既定」= 各ノードの `main.rs` などが持つ宣言時の値、`overrides/` での「断片」=
  `config/nav2/*.yaml` の値。値を変えたら `既定 同左` や `# 断片 <値>:`、ファイル冒頭の
  「変えてあるのは○○だけ」といった要約も同じ変更で追随させる。

## 触る前に読むもの

| 触るもの | 先に読む |
| --- | --- |
| `config/` の yaml の値 | [`src/autonomous_nav/config/README.md`](src/autonomous_nav/config/README.md)（合成・override の仕組みと、各値の由来） |
| `launch/` | [`docs/usage/architecture.md`](docs/usage/architecture.md#launchファイルの構成) |
| `simulator/`（Isaac 版 / pi4_sim 版） | [`simulator/docs/pi4_sim.md`](simulator/docs/pi4_sim.md) を先に、次に [`simulator/README.md`](simulator/README.md) |
| `docker/` | [`docker/README.md`](docker/README.md)（実機用と開発用の 2 環境） |
| `src/raspicat_driver/` / `tools/image/udev/` | [`src/raspicat_driver/README.md`](src/raspicat_driver/README.md)、次に [`docs/setup/raspberry-pi-4.md`](docs/setup/raspberry-pi-4.md) と [`raspberry-pi-5.md`](docs/setup/raspberry-pi-5.md)（未検証の項目付き） |
| `src/value_iteration3/` | 同ディレクトリの `CLAUDE.md` |
| 実機の症状を追う | [`docs/usage/troubleshooting.md`](docs/usage/troubleshooting.md) |

ドキュメントは日本語で書かれています。追記も日本語でそろえてください。
