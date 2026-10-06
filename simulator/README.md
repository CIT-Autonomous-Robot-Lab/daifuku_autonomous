# simulator — usim VTC 地図作成・自律移動ハーネス

usim の Gazebo Classic で VTC ワールドと Raspicat を動かし、
daifuku の SLAM で地図を作ってから、その地図で emcl2 + VI standalone の自律移動を確認する。
シミュレータ・センサ・Gazebo 用の資産加工は usim が持ち、ここには daifuku 側の実行と判定だけを置く。

## 起動

開発時は、このリポジトリと `usim` を同じ親ディレクトリに置く。
`pyproject.toml` は `../../usim` と `../../usim/packages/gazebo` を明示的な依存元にしている。
別の配置ではこの 2 つの uv source を変更する。

リポジトリルートから:

```bash
uv sync --project simulator --locked --inexact
USIM_ROOT=/path/to/usim uv run --project simulator --locked daifuku-vtc \
    --engine podman --base-image localhost/daifuku-autonomous:humble-amd64
uv run --project simulator --locked daifuku-vtc --help
```

`--inexact` は既存 venv の追加パッケージを保全する。
完全に最小の環境が必要なら、空の `UV_PROJECT_ENVIRONMENT` を指定して `uv sync --project simulator --locked` を使う。

- `USIM_ROOT` または `--usim-root` は必須。ワールドとイメージの build context に使い、
  Python の import 先にはしない。既定のワールドは `$USIM_ROOT/assets/vtc/world.sdf`。
- Podman または Docker が必要。stack イメージはリポジトリルートを build context として毎回再構築する。
  `--no-build` で既存イメージを再利用できる。
- ベースイメージには `docker/raspberrypi/Dockerfile` の ROS 2 Rust underlay
  (`/opt/ros2_rust_ws`、rclrs、nav2_msgs / tf2_msgs の Rust bindings) が必要。
  `docker/dev/Dockerfile` のイメージは使えない。
- DDS は実行ごとの専用 bridge、ROS_DOMAIN_ID 87、UDPv4 が既定。
  `--network host` は明示指定のみ。駆動ドライバ・Livox・URG・EKF は立てない。

## 実行と判定

prepare → mapping → alignment → navigation を直列に実行する。
Gazebo のワールドと odom は mapping から navigation まで同じセッションを保つ。
RSP 用 URDF はそのまま使い、Gazebo 用コピーの plugin 除去は usim に任せる。

mapping は `map_saver_cli --free 0.15` で保存する。
navigation は `localization:=emcl2 planner:=vi local_planner:=vi nav2:=false`、
両段とも `use_sim_time:=true lidar:=2d lidar_driver:=false overrides:=none config_watch:=off`。
実機の設定値は変更しない。

**PASS は NavigateToPose の SUCCEEDED と、独立した world 真値での
ゴール誤差 <= `--tolerance`（既定 0.35 m）の両方が成立したときだけ。**
結果は `simulator/runs/vtc/vtc-<時刻>/result.json`。
既存の `--run-dir` は UUID 子ディレクトリを作って保全する。
終了コードは 0 PASS / 1 FAIL / 2 入力不正 / 3 BLOCKED。
後始末は自分のコンテナと bridge だけを対象にする。

## 録画

`--record-video` のときだけ Gazebo のカメラを有効にする。動画化にはローカルの ffmpeg が必要。

```bash
USIM_ROOT=/path/to/usim uv run --project simulator --locked daifuku-vtc \
    --engine podman --record-video --run-dir simulator/runs/vtc/new-video
uv run --project simulator --locked daifuku-vtc-video \
    simulator/runs/vtc/new-video/navigation/recording.jsonl \
    simulator/runs/vtc/new-video/navigation.mp4 --fps 10 \
    --transition simulator/runs/vtc/new-video/transition.json \
    --result simulator/runs/vtc/new-video/result.json
```

## 検証

```bash
uv run --project simulator --locked --with pytest python -m pytest -q simulator/tests/vtc
bash -n simulator/container/usim_vtc/stack.sh
```

単体テストの成功は実 VTC の PASS とは別。
ホスト側は `src/daifuku_sim/vtc/`、ROS Humble 用 helper と設定は `container/usim_vtc/` に置く。
ホスト venv と ROS の Python ABI が違うので、ROS helper をホスト package に混ぜない。

地図生成・地図と順路の検算は [`tools/maps/`](../tools/maps/) の独立スクリプト。
旧 Pi4 ハーネスの実測記録は [`docs/usage/pi4_sim_history.md`](../docs/usage/pi4_sim_history.md) に残す。
