# simulator — daifuku シミュレーションシナリオ

usim の Gazebo Classic で VTC ワールドと Raspicat を動かし、
daifuku の SLAM で地図を作ってから、その地図で emcl2 + VI standalone の自律移動を確認する。
シミュレータ・センサ・Gazebo 実行は usim が持ち、ここには daifuku 側の実行・判定と選択した地図の準備を置く。

## 起動

Python 依存の `usim` と `usim-gazebo` は、`pyproject.toml` で
`https://github.com/NOPLAB/usim.git` のコミット
`a7d97a9542f8939e6e7b80e8c901ab9eff229148` に固定している。
`--usim-root` と `USIM_ROOT` は不要。インストール済み usim の Git コミット情報から、
ワールドと build context 用の checkout を `.venv/share/daifuku-vtc/<コミット>/` に
初回だけ自動取得する。実際の配置は実行中の仮想環境に従う。
既定の地図には、簡略Blender版ではなく、以前復元した完全版VTCの地図データを同梱する。
建物・樹木・低木・ポールなど1039配置、地形64タイルと追加構造物を元のメートル座標で
保持する。初回のみ固定BlenderイメージでCPU変換し、同じモデルを共有して配置する。
衝突形状は元のフラグを引き継ぎ、葉を衝突形状へ追加しない。両面の葉は裏面も明示して変換する。
地形は近傍約0.4 m、遠景0.8 mのLOD、
色画像はタイルごとに512ピクセルとし、変換コンテナのメモリ上限は3 GiB。
完成ワールドは `assets/vtc-complete-v1/` に保存し、旧簡略版キャッシュと区別する。
地図全体がMITという意味ではない。出典・保持範囲は同梱データと生成資産の
`NOTICE`・`LICENSE-MIT`・`LICENSE-APACHE2` に残す。
入力の再パッケージ化には `uv run simulator/tools/package_vtc_complete.py --source <復元済み完全版> --out <archive>` を使う。
Python の import 先は uv がインストールしたパッケージのままとし、uv 内部のキャッシュ
配置や隣の usim checkout には依存しない。

リポジトリルートから:

```bash
uv sync --project simulator --locked --inexact
uv run --project simulator --locked daifuku-sim --help
uv run --project simulator --locked daifuku-sim vtc run
uv run --project simulator --locked daifuku-sim vtc run --help
```

`python -m daifuku_sim` も同じ入口を使う。サブコマンドなしではヘルプだけを表示する。
通常起動では Gazebo GUI を表示する。GUI が不要な実行環境では `vtc run --headless` を指定する。
Windows + Docker Desktop では、`DISPLAY` が未設定なら WSLg への表示接続を自動作成し、
シミュレータ終了時に片付ける。ポートはホストへ公開しない。
それ以外の環境では X11 の `DISPLAY` と、コンテナから表示先への接続が必要。
旧 `daifuku-vtc` と `daifuku-vtc-video` は、それぞれ `daifuku-sim vtc run` と
`daifuku-sim vtc video` の別名として残す。

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

mapping は `ros2 launch daifuku_bringup mapping.launch.py` で立て、`map_saver_cli --free 0.15` で保存する。
navigation は公開入口 `ros2 launch daifuku_bringup navigation.vtc.launch.py` を使う。
生成した地図を外から明示して渡し、`overrides` は `none` で実機の場所別調整を混ぜない。
推定は `localization:=emcl2`、計画と追従は standalone VI。
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
uv run --project simulator --locked daifuku-sim vtc run \
    --record-video --run-dir simulator/runs/vtc/new-video
uv run --project simulator --locked daifuku-sim vtc video \
    simulator/runs/vtc/new-video/navigation/recording.jsonl \
    simulator/runs/vtc/new-video/navigation.mp4 --fps 10 \
    --transition simulator/runs/vtc/new-video/transition.json \
    --result simulator/runs/vtc/new-video/result.json
```

## 検証

```bash
uv run --project simulator --locked --with pytest python -m pytest -q simulator/tests
bash -n docker/simulator/stack.sh
```

単体テストの成功は実 VTC の PASS とは別。
ホスト側は `simulator/src/daifuku_sim/vtc/`、ROS Humble 用 helper と設定は `docker/simulator/` に置く。
ホスト venv と ROS の Python ABI が違うので、ROS helper をホスト package に混ぜない。

## 構成と拡張

- `daifuku_sim/cli.py` がシナリオのコマンドを登録する。`__main__.py` は同じ入口を呼ぶ。
- `daifuku_sim/runtime.py` はコンテナエンジンと所有する子プロセスの管理だけを持ち、
  VTC や usim の設定を import しない。
- `vtc/cli.py` が VTC 専用引数と入力検証、`vtc/workflow.py` が実行順序・判定・
  結果保存・後始末、`vtc/phases.py` が各実行段を持つ。
- VTC の Gazebo 設定と起動コマンドは `vtc/runtime.py`、
  資産取得は `vtc/assets.py`、共有データ契約は `vtc/contract.py` に置く。
  Docker 内の ROS helper には同じ契約を `vtc_contract.py` としてコピーする。

別の実行手順・判定を追加するときは `vtc/` と並ぶシナリオを作り、
その `cli.py` で引数と handler を登録して、トップレベルの `build_parser()` から呼ぶ。
共通部からシナリオを参照せず、シナリオから共通の実行基盤を使う。
ワールド・センサ・シミュレータバックエンドの実装は引き続き usim が持つ。

地図生成・地図と順路の検算は [`tools/maps/`](../tools/maps/) の独立スクリプト。
