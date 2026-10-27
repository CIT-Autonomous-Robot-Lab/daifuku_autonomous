# simulator — usim VTC 地図作成・自律移動ハーネス

usim の Gazebo Classic で VTC ワールドと Raspicat を動かし、
daifuku の SLAM で地図を作ってから、その地図で emcl2 + VI standalone の自律移動を確認する。
シミュレータ・センサ・Gazebo 用の資産加工は usim が持ち、ここには daifuku 側の実行と判定だけを置く。

## 起動

Python 依存の `usim` と `usim-gazebo` は、`pyproject.toml` で
`https://github.com/NOPLAB/usim.git` のコミット
`a7d97a9542f8939e6e7b80e8c901ab9eff229148` に固定している。
`--usim-root` と `USIM_ROOT` は不要。インストール済み usim の Git コミット情報から、
ワールドと build context 用の checkout を `.venv/share/daifuku-vtc/<コミット>/` に
初回だけ自動取得する。実際の配置は実行中の仮想環境に従う。
VTC ワールドは Git に含まれない生成物なので、初回起動時に usim の既存ツールで
固定版の上流 FBX をサイズ・SHA256 検証付きで取得し、選択した Podman / Docker 上の
固定 Blender イメージで CPU 変換する。以後は同じコミットの生成済み資産を再利用する。
Python の import 先は uv がインストールしたパッケージのままとし、uv 内部のキャッシュ
配置や隣の usim checkout には依存しない。

リポジトリルートから:

```bash
uv sync --project simulator --locked --inexact
uv run --project simulator --locked daifuku-vtc
uv run --project simulator --locked daifuku-vtc --help
```

`--inexact` は既存 venv の追加パッケージを保全する。
完全に最小の環境が必要なら、空の `UV_PROJECT_ENVIRONMENT` を指定して `uv sync --project simulator --locked` を使う。

- Git と起動済みの Docker が必要。既定のコンテナエンジンは Docker。
  Podman を使う場合は `--engine podman` または `VTC_ENGINE=podman` を明示する。
  初回の資産準備にはネットワーク接続も必要。
- `USIM_ROOT` または `--usim-root` は、既存 checkout を使う場合だけ指定する。
  その場合は従来どおり `<checkout>/assets/vtc/world.sdf` を使い、自動生成しない。
  `--world` で独自の SDF を指定した場合も VTC ワールドの自動生成を省く。
- Podman または Docker が必要。stack イメージはリポジトリルートを build context として毎回再構築する。
  `--no-build` で既存イメージを再利用できる。
- ベースイメージには `docker/raspberrypi/Dockerfile` の ROS 2 Rust underlay
  (`/opt/ros2_rust_ws`、rclrs、nav2_msgs / tf2_msgs の Rust bindings) が必要。
  更新後の `docker/dev/Dockerfile` も同じ underlay を構築する。
- DDS は実行ごとの専用 bridge、ROS_DOMAIN_ID 87、UDPv4 が既定。
  `--network host` は明示指定のみ。駆動ドライバ・Livox・URG・EKF は立てない。

## 実行と判定

資産準備 → prepare → mapping → alignment → navigation を直列に実行する。
Gazebo のワールドと odom は mapping から navigation まで同じセッションを保つ。
RSP 用 URDF はそのまま使い、Gazebo 用コピーの plugin 除去は usim に任せる。

mapping は `map_saver_cli --free 0.15` で保存する。
navigation は `localization:=emcl2` と standalone VI を使い、
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
uv run --project simulator --locked daifuku-vtc \
    --record-video --run-dir simulator/runs/vtc/new-video
uv run --project simulator --locked daifuku-vtc-video \
    simulator/runs/vtc/new-video/navigation/recording.jsonl \
    simulator/runs/vtc/new-video/navigation.mp4 --fps 10 \
    --transition simulator/runs/vtc/new-video/transition.json \
    --result simulator/runs/vtc/new-video/result.json
```

## 検証

```bash
uv run --project simulator --locked --with pytest python -m pytest -q simulator/tests/vtc
bash -n docker/simulator/stack.sh
```

単体テストの成功は実 VTC の PASS とは別。
ホスト側は `simulator/src/daifuku_sim/vtc/`、ROS Humble 用 helper と設定は `docker/simulator/` に置く。
ホスト venv と ROS の Python ABI が違うので、ROS helper をホスト package に混ぜない。

地図生成・地図と順路の検算は [`tools/maps/`](../tools/maps/) の独立スクリプト。
