# simulator — daifuku シミュレーションシナリオ

usim の Gazebo Classic で VTC ワールドと Raspicat を動かし、
daifuku の SLAM で地図を作ってから、その地図で emcl2 + VI standalone の自律移動を確認する。
シミュレータ・センサ・Gazebo 実行は usim が持ち、ここには daifuku 側の実行・判定と選択した地図の準備を置く。

## 起動

19fの実測PCDから生成したワールドは`worlds/19f/world.sdf`に置く。
元のXYZを10cmボクセルの表面メッシュへ変換し、描画と衝突で共用する。
疎な床点群を補う支持平面を`z=0`に追加しており、補完内容と点群のSHA256は
`worlds/19f/manifest.json`に記録する。点群の穴・ノイズは残る。
VTCとは別のワールドで、ロボットや自律走行を自動起動しない。

リポジトリルートからワールドだけを表示する:

```bash
uv run --project simulator --locked daifuku-sim 19f
```

`vtc`・`tsukuba2025`以外の名前は`worlds/<名前>/world.sdf`から自動検出する。
同名のディレクトリがあっても組み込みシナリオを優先する。
Linuxではインストール済みGazebo Classicを、WindowsではWSLの`Ubuntu-22.04`を使う。
Windowsのディストリビューションは`--wsl-distro`または`DAIFUKU_WSL_DISTRO`で指定できる。
GUI不要なら`19f --headless`を指定する。終了はGUIを閉じるかCtrl+C。

再生成はリポジトリルートで次を実行する。PCDはGit管理外なので別途配置し、
既存ディレクトリを上書きしないよう、新しい出力先を指定する。

```bash
uv run --project tools/maps python tools/maps/pcd_to_world.py \
    src/daifuku_maps/maps/19f/map_19f.pcd --output simulator/runs/19f-new
```

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

## つくばチャレンジ2025の全コース

`daifuku-sim tsukuba2025` は、小範囲SLAM試験とは独立したシナリオ。
現在の30地点と17領域の計画地図を使い、物理シミュレーションとemcl2を止めずに、
領域ごとの地図読み込みとVIの再起動を行う。自己位置推定へ真値を連続入力しない。
各地点のアクション進行と独立したシミュレータのworld姿勢による到達誤差を照合する。
`--end-index` による部分診断は `PARTIAL_PASS` であり、完走の `PASS` にはしない。
目標間の経路長が最大約164.8mとなるため、到達期限の既定値は1800秒。
独立した実位置の到達誤差上限0.6mは変更しない。

Fast DDSのサービス応答は、応答先readerの発見通知を最大5秒待つ。
クライアント側でサーバーを発見しても、サーバー側の応答先発見は同時とは限らず、
既定の100msでは区間アクションの受付応答が送信前に失われるため。
`docker/simulator/fastdds_udp.xml`のサービスpublisherに適用し、既に発見済みなら直ちに送る。
固定の起動待ちや要求の再送は加えず、物理到達判定も緩和しない。

ワールドは、以前の2025用の地形・歩道補正を合成したUSDからCPUで生成する。
簡略VTCや補正前の完全版を、2025の物理コースとして使わない。

```bash
uv run simulator/tools/package_tsukuba2025.py \
    --out simulator/runs/tsukuba2025-world
```

このツールの既定入力は、手元に保全した
`runs/vtc/isaac-course-20261016/course-2025/frontage-model-v3/runtime-world.usda`。
別の配置では `--stage`、`--course`、`--index` を明示する。
順路の75 m以内に関係するメッシュを元の三角形のまま保持する。
この選択線は`course.json`の`world_route`で保持し、30地点の疎な目標点列とは分離する。
センサー到達距離70 mと5 mの余裕を含み、地形を平坦化・間引きしない。
`transform.json` は元地図とGazeboの剛体変換、実地形から求めた開始地面高、
元コースのSHA256を記録する。地図と選択された30地点の座標は変更しない。
間引き前の密な経路・全区間と必須折返しは、30点化しても保持する。
高密度の衝突メッシュを含むため、小範囲試験よりメモリ・ディスクを多く使う。
このシナリオの起動期限は900秒。`--startup-timeout` でGazeboの生成待ちと
ROS入力待ちをまとめて変更できる。生成されたCOLLADAの材質色は、
Gazebo Classicが扱えるメッシュ単位の平均色とし、衝突形状は変更しない。

Mid-360は `/livox/lidar` の `sensor_msgs/PointCloud2`。
水平360度、垂直-7..52度、0.1..70 m、720×28方向、10 Hzの実レイキャストを使い、
XYZと強度を出す。実機の反射率依存距離・非反復走査・測距ノイズまでは再現しない。
`livox_frame` は機体基準 `[0.144, 0, 0.275]` mに取り付け、
公開の `navigation.tsukuba2025.launch.py` の二つの点群投影系へ接続する。

3D点群対応のusimを開発中にローカルで検証する場合、通常の固定Git依存とは
区別して、変更した両パッケージを検証用venvへインストールする。
Windowsのリポジトリルートでの例:

```bash
uv pip install --python simulator/.venv/Scripts/python.exe --no-deps \
    -e ../usim -e ../usim/packages/gazebo
uv run --project simulator --no-sync daifuku-sim tsukuba2025 \
    --usim-root ../usim --keep-open
```

Windows + Docker Desktop でこのシナリオが WSLg 接続を自動作成する場合は、
`--wslg-gpu` を usim へ渡し、`/dev/dxg` と WSL の描画ライブラリを使う。
複数 GPU がある場合は、起動元の環境変数
`MESA_D3D12_DEFAULT_ADAPTER_NAME` で使用するアダプタ名を指定できる。
たとえば `NVIDIA` は GeForce を選び、内蔵 GPU や CPU 描画への意図しない切替を避ける。
大規模なコースでは GUI が黒いままの場合も走行本体の生存と描画ログを分けて確認し、
`ogre.log` の `GL_RENDERER` で実際の描画装置を確認する。

`--no-sync` はこの開発版を固定Git版へ戻さず実行する指定。
`--keep-open` は成功後も同じ物理セッションを到達位置に表示し続け、
`result.json` を先に保存する。ヘッドレス試験では付けず `--headless` を使う。
結果・元コース・各地点の到達誤差・軌跡・領域別ログは実行ディレクトリへ保存する。

### GazeboのGPU 3D点群

`--lidar-sensor-type gpu_ray` は既存のCPU `ray` の代わりに、GPU描画による
実距離をROS PointCloud2へ出す。形状・衝突・1 ms物理刻み・720×28方向・
10 Hz・距離範囲・取付位置は変えない。専用のイメージを先に構築する。
usimのGPU用イメージには、Gazeboの出力画素境界による1行・1列の測距ラベルずれを
直すシェーダーが必要。修正前のイメージを再利用すると、角度設定が同じでも
点群の形状がずれるため、Pythonだけの再読込みではなくGPU用レイヤーを再構築する。
既存の追従器は壁時計周期なので、出力ワールドは1倍速を上限として
1 msの物理刻みをすべて進める。上限を外すと1手の実行時間がモデルとずれる。

```bash
(cd ../usim && docker build -f packages/gazebo/Dockerfile -t usim-gazebo:gpu-lidar .)
docker build -f docker/simulator/Dockerfile.emcl \
    -t daifuku-usim-vtc-stack:emcl-freshness ../emcl2_ros2
MESA_D3D12_DEFAULT_ADAPTER_NAME=NVIDIA \
uv run --project simulator --no-sync daifuku-sim tsukuba2025 \
    --usim-root ../usim --usim-image usim-gazebo:gpu-lidar \
    --stack-image daifuku-usim-vtc-stack:emcl-freshness \
    --lidar-sensor-type gpu_ray --headless --no-build --goal-timeout 1800
```

`Dockerfile.emcl` は既存ROSイメージを土台に、隣のローカルEMCLチェックアウトだけを
再構築する。今回のEMCL修正は、取得時刻のナノ秒を含むスキャン識別、同一観測の
再処理防止、再サンプル後の等重み化と初期共分散の反映。
実クラスの6回帰テストもイメージ構築時に行う。
EMCL側の未コミット変更を使うため、上流を取得し直す通常のROSイメージ構築とは分ける。
コースドライバは既知の名目開始位置だけをシードに使い、方位の標準偏差を20度とする。
初期化後に10スキャン以上を観測し、位置分散0.01 m²以下・方位分散0.005 rad²以下へ
収束してからモータを有効にする。物理真値は従来どおり独立評価にだけ使う。

Windows + Docker Desktopでは、ヘッドレスでもWSLgのGPU接続を自動作成する。
GUIは起動しない。Gazeboのセンサー同期を使い、実際の受信時刻から点群周期と
実時間比を計測する。Humble 2.0.3のプラグイン名は
`libgazebo_ros_velodyne_laser.so` で、ROS 1のGPU専用ライブラリ名とは異なる。
出力にはXYZ・強度に加えてringとtimeが含まれ、欠測方向はNaNとして保持される。
モデル生成完了とセンサー準備完了は別なので、実際の点群受信で準備を判定する。

### WindowsネイティブIsaac SimとRTX LiDAR

`--backend isaacsim` はWindowsネイティブIsaac Sim 6.1と、同じDocker ROS構成を使う。
既定の `--backend gazebo` は変えない。Isaac Simをインストールし、EULAに同意した
Python 3.12環境の実行ファイルを `ISAAC_PYTHON` に設定して実行する。
開発中の変更を使う場合は、固定Git版へ戻さずローカルのパッケージを導入する。

```bash
uv pip install --python "$ISAAC_PYTHON" --no-deps \
    -e simulator -e ../usim -e ../usim/packages/isaacsim -e ../usim/packages/gazebo
"$ISAAC_PYTHON" -m daifuku_sim tsukuba2025 \
    --backend isaacsim --usim-root ../usim --headless --ros-domain-id 93
```

`--world` は同じ出力済みコースパッケージの `world.sdf` を指す。
Isaacは隣の `manifest.json` から元の合成USDと常駐インデックスを読み、
変換時のレイヤー・インデックスのSHA256を照合する。元USDも保全しておく。
80 m以内の元形状を有効にし、70 mの測距範囲を保つ。メッシュを間引かない。
720×28方向、10 Hz、1 ms物理刻みで、RTXのGPUレイ計算によるXYZ・強度を使う。
回転走査の水平角は半開区間であり、Gazebo/PhysX版の末尾の重複方向とは異なる。

Windowsとの通信はTCP 55100を使う。Docker内は標準の `LARGE_DATA` 構成で、
発見にUDP、大きい地図や点群の転送にTCP・共有メモリを使う。
Docker Desktopのホストアドレスは起動ごとに調べ、専用のプロファイルを保存する。
自己位置推定の初期値と未駆動時の位置検証は、Gazeboの原点ではなく元コースの
開始座標を使う。Isaacのodomは元座標系なので、実行時の `transform.json` は恒等変換、
元の変換は `source-transform.json` に保全する。

プランナの到達セルは、対角の2点とも到達半径内に入る必要がある。
0.2 m格子で終端セルが消えないよう、到達セル半径は0.3 mを保つ。
独立した真値による0.6 mの到達判定は緩めない。設定値の調整やセンサー単体の
速度測定だけでは完走とせず、同じ連続実走で設定された全30地点をすべて確認する。

実センサーの独立検証:

```bash
RUN_GAZEBO_MID360=1 uv run --project simulator --no-sync --with pytest \
    python -m pytest -q -s simulator/tests/test_mid360_native.py
```

通常の単体テストでは、このDocker/Gazebo依存検査だけを明示的に対象外とする。

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
