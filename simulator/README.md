# simulator — nav2 スタックを Pi4 相当の速度でシミュレータ上で回す

**ハーネスは 2 つあり、nav2 側 (コンテナ・cgroup・キャリブレーション) は共通で、
ロボットとセンサをどこから供給するかだけが違う。**

| | ロボット / センサ | 入口 | ドキュメント |
|---|---|---|---|
| **Isaac 版** | Isaac Sim (RTX GPU、ホスト側プロセス) | `scripts/run_isaac_case.sh` | このファイル |
| **pi4_sim 版** | `container/fake_robot.py` (地図をレイキャストする疑似ロボット) | `scripts/run_pi4_sim.ps1` | [`docs/pi4_sim.md`](docs/pi4_sim.md) |

pi4_sim 版のほうが先にあり、Pi4 相当の cgroup 値・キャリブレーション
(navfn 20Hz 設定に対し実機 7.6Hz)・既定のスタート/ゴール座標・`container/probe.py`
はそこから引き継いでいる。**実機で観測された事象の実測記録は `docs/pi4_sim.md`
にまとまっている**ので、どちらを使う場合でもあちらを先に読むこと。

以下はこのうち Isaac 版の話。`rt-net/raspicat_sim` (Gazebo) の Isaac Sim 版だが
**移植ではなく作り直し**で、動かすナビゲーションスタックは
`rt-net/raspicat_slam_navigation` ではなく **本リポジトリの `daifuku_stack`**
(emcl2 + value_iteration3)。

Raspberry Pi 4 の遅さは pi4_sim ハーネスと同じく cgroup の CPU quota で再現する。
Isaac Sim は `fake_robot.py` (地図をレイキャストする疑似ロボット) を置き換えるだけで、
nav2 側の構成・制約・キャリブレーションはそのまま引き継ぐ。

```
  ┌─────────────────────────── 同一ホスト (RTX GPU) ────────────────────────────┐
  │                                                                             │
  │  Isaac Sim (制限なし・GPU)              Pi4 相当コンテナ (cgroup で減速)     │
  │  ┌───────────────────────┐              ┌────────────────────────────────┐  │
  │  │ world.usda (地図押出し) │              │ nav2 / emcl2 / value_iteration3│  │
  │  │ raspicat (URDF→USD)    │  /scan_raw   │ map_server                     │  │
  │  │ RTX LiDAR              │─ /livox/* ──▶│ laser_filters                  │  │
  │  │ 差動駆動 + odom        │  /odom       │ (mid360 時) p2l + ekf          │  │
  │  │                        │◀── /cmd_vel ─│ bt_navigator                   │  │
  │  └───────────────────────┘              └────────────────────────────────┘  │
  │        cpu: 無制限                          cpu.max = 6000/10000 (0.6コア)    │
  │        gpu: RTX                             mem     = 3g, swap なし          │
  └─────────────────────────────────────────────────────────────────────────────┘
```

**Isaac と nav2 コンテナは必ず同じマシンに置くこと。** Isaac をクラウド、nav2 を
手元に分けると DDS がネットワーク越しになり、測りたい「Pi4 の遅さ」にネットワーク
遅延が混ざって分離できなくなる。

## 動作確認の状態

**どこまで実測で確かめたかを明示しておく**。2026-08-20 に RTX 4060 Ti 16GB /
Windows 11 の機体で pip 版 (6.0.1) を初めて実際に動かし、**Isaac と nav2 を DDS で
繋いだ通しまで到達した**（`.wslconfig` の `networkingMode=mirrored` と
`container/make_fastdds_mirrored.sh` が要る。下の「Isaac と nav2 を繋ぐ」）。
**ただしゴールには届いていない** —— 同梱 LiDAR プロファイルの走査諸元が実機と
違うため、VIOLA の自己位置が 2 分ほどで見失う（下の表）。それ以前の記述は
GPU の無い開発機 (AMD Radeon 780M) で書いたもの。

| ファイル | 状態 |
|---|---|
| `src/daifuku_sim/map_to_usd.py` | **実測検証済み**。`map_19f` / `map_tsudanuma` で生成し、出力 USD の Wall プリムを地図グリッドに焼き直して元の占有と**完全一致** (欠落0・余剰0) を確認。検算は `tests/verify_usda.py` (手元で再実行できる) |
| `src/daifuku_sim/rtf_gate.py` | **実測検証済み**。合成 RTF ログで PASS / FAIL(3) / WARN の分岐を確認 |
| `pyproject.toml` / `uv.lock` | **実測検証済み**。`uv lock` / `uv sync` / `uv run map-to-usd` / `uv run rtf-gate` の成功を確認 |
| extra `isaac` (pip 版 Isaac Sim 6.0.1) | **実測検証済み** (2026-08-20)。`uv sync --extra isaac` で 147 パッケージが入り、Kit が起動することを確認 (venv 実測 2.2GB + extscache)。**`all` だけでは起動しない**ことが分かったので `all,extscache` に直した (下の「Isaac Sim 6.0 で変わったところ」) |
| `src/daifuku_sim/isaac_raspicat.py` | **実測検証済み** (2026-08-20、6.0.1 / `--lidar 2d` / `--headless`)。world 読み込み → URDF 取り込み → RTX LiDAR → ROS 2 グラフ → `/scan_raw` `/odom` `/tf` の発行、**および `/cmd_vel` を受けて実際に走ること**まで通り（手投げの `linear.x=0.2` で 20 秒 1.8m 前進、`--rtf-report` は `rtf-gate` を PASS）。同日に articulation root と車輪ドライブ damping の 2 件を直した（上の「`/cmd_vel` が届いているのに車輪が回らない失敗が 2 つある」）。**未検証は `--lidar mid360` と `--publish-link-tf isaac` の 2 経路** |
| `scripts/run_pi4_sim.ps1` / `container/run_case.sh` / `container/fake_robot.py` / `container/probe.py` | **実測検証済み** (2026-08-20、Windows + podman/WSL、amd64)。`LOCALIZATION=vi` で `PROBE_SUMMARY {"result": "SUCCEEDED", "first_plan_s": 6.8, "elapsed_s": 19.2}`、`vi_planner` peak RSS 820MB / cgroup peak 1.08GB (上限 3GB、OOM 0)、throttle 24.1s / 2457 回。プロセス死なし |
| `scripts/run_isaac_case.sh` / `container/nav_container.sh` | **通しで実行した** (2026-08-20、Windows + WSL の podman。`run_isaac_case.sh` 自体は Linux 向けのままなので、コンテナ側は同じ env を並べて手で叩いた)。Isaac の `/scan_raw` `/odom` `/tf` → nav2 → `/cmd_vel` → Isaac の往復が成立する。**`LOCALIZATION=emcl2 OVERRIDES=none` なら自己位置が収束して自走する** —— 種で 0.06m に収まり静止中は 0.10m を保ち、走り出して `distance_remaining` 6.0 → 2.1 まで詰めたところで誤差 1.6m まで開いて機体が止まる (`result: TIMEOUT`、`first_plan_s` 57.1)。**既定の `map_19f` overrides (VIOLA / `localizer: belief`) では一度も収束しない** —— 真値と一致する種を撒いても初回推定が 1.74m ずれ、単調に 5.4m まで離れて `no robot pose for too long` で ABORTED になる。同日にタイムスタンプの実バグを 1 件直した (下の「Isaac のタイムスタンプ」)。`vi_planner` peak RSS 1.02GB / cgroup peak 1.41GB (上限 3GB、OOM 0) |
| `lidar_bringup.launch.py` / `robot_bringup.launch.py` / `navigation.launch.py` の変更 | 構文チェックのみ。既定値は現行のままで実機の挙動は変えていない |

最初に動かすときは、下の「立ち上げ順序」に従って**段階的に**確認すること。

> **`.python-version` を変えたら round-trip を必ず回し直すこと。**
> このファイルは isaacsim の都合 (6.0.1 = cp312) だけで決まっているのに、
> `map_to_usd` / `rtf_gate` もその Python で動く。将来 isaacsim が cp313 を要求して
> ここを上げると `uv lock` が numpy / Pillow も引き直すが、**その差は
> `map_to_usd` の出力を見ても分からない**。上の「実測検証済み」を維持する条件は:
>
> ```bash
> uv run --no-sync map-to-usd ../src/daifuku_stack/maps/map_19f.yaml -o /tmp/w.usda
> uv run --no-sync python tests/verify_usda.py \
>     ../src/daifuku_stack/maps/map_19f.yaml /tmp/w.usda free    # -> MATCH
> ```

## 構成

`simulator/` は **uv プロジェクト**である。ただし uv パッケージ (`src/daifuku_sim/`)
に入るのは**ホスト側で走るものだけ**で、コンテナの中で走るものは `container/` に置く。
両者は Python が違う (下記)。

```
simulator/
├── pyproject.toml / uv.lock      # 依存を固定 (isaacsim は extra `isaac`)
├── .python-version               # 3.12 — isaacsim 6.0.1 が cp312 のみのため
├── src/daifuku_sim/              # ホスト側 (uv / Python 3.12)
│   ├── map_to_usd.py             # -> uv run map-to-usd
│   ├── rtf_gate.py               # -> uv run rtf-gate
│   ├── downsample_map.py         # -> uv run downsample-map (コンテナ内でも走る。後述)
│   ├── isaac_raspicat.py         # -> python.sh か uv run --extra isaac python
│   └── src/daifuku_config/*.json            # RTX LiDAR プロファイル
├── container/                    # コンテナ内 (ROS 2 Humble / Python 3.10 / rclpy)
│   ├── nav_container.sh          #   Isaac 版のコンテナ側
│   ├── run_case.sh               #   pi4_sim 版のコンテナ側 (fake_robot もここが起動)
│   ├── fake_robot.py             #   疑似ロボット (pi4_sim 版のみ)
│   ├── probe.py                  #   ゴール投入と計数 (**両版で共有**)
│   └── fastdds_local.xml         #   実機プロファイルのローカル版
├── scripts/                      # ホスト側オーケストレータ
│   ├── run_isaac_case.sh         #   Isaac 版 (Linux / RTX)
│   ├── run_pi4_sim.ps1           #   pi4_sim 版 (Windows / podman)
│   └── run_matrix.ps1            #   pi4_sim 版をケース一式まとめて回す
├── docs/pi4_sim.md               # pi4_sim 版のドキュメントと実測記録
├── tests/verify_usda.py          # 生成 USD を地図に焼き戻して検算
└── tests/verify_map_thresholds.py # 地図の未観測が free に化けていないかを検算
```

> **`container/` を `src/daifuku_sim/` に入れないこと。** ROS 2 Humble は
> Python 3.10、この venv は 3.12 で、拡張モジュールの ABI が合わない
> (「どちらの経路でも `rclpy` は使えない」の節を参照)。`probe.py` / `fake_robot.py` を
> パッケージに入れると、**その venv では絶対に import できないモジュール**が
> `daifuku_sim` に並ぶことになる。
>
> 例外は `downsample_map.py` で、これは numpy / PyYAML しか要らないので両方から
> 使う。ホストからは `uv run downsample-map`、コンテナ内では `run_case.sh` が
> `MAP_SCALE` 指定時に `/opt/sim/downsample_map.py` を直接叩く。オーケストレータが
> **この 1 ファイルだけを別途配っている**ので、**パッケージ内 import を足さないこと**。

コンテナ内での置き場は両版とも `/opt/sim` で共通。`run_isaac_case.sh` と
`run_pi4_sim.ps1` がそれぞれ `container/` の必要なものをそこへ `podman cp` する
(bind mount しない理由は「つまずきやすいところ」を参照)。

```bash
cd simulator && uv sync          # 初回だけ (uv run は自動で同期するので必須ではない)
uv run map-to-usd --help
uv run rtf-gate --help

# 生成した world.usda が元の地図と一致しているか検算する
uv run map-to-usd ../src/daifuku_stack/maps/map_19f.yaml -o /tmp/world.usda
uv run python tests/verify_usda.py ../src/daifuku_stack/maps/map_19f.yaml /tmp/world.usda free
# -> walls parsed: 3373 / missing 0 / extra 0 / MATCH
```

### Isaac Sim 本体をどう用意するか

Isaac Sim の入れ方は 2 通りあり、**どちらでも同じ `isaac_raspicat.py` が動く**。
`run_isaac_case.sh` は `ISAAC_RUNTIME` で切り替える (既定 `binary`)。

| | `binary` (既定) | `pip` |
|---|---|---|
| 入手 | NVIDIA 配布のバイナリ | `uv sync --extra isaac` |
| バージョン | 手元次第 | **6.0.1** (`uv.lock` で固定) |
| 動かす Python | Kit 同梱 (この venv は**見ない**) | この venv (3.12) |
| 起動 | `$ISAACSIM/python.sh <path>` | `uv run --extra isaac --no-sync python <path>` |
| 取得量 | 配布物次第 | 新規 **147 パッケージ**。実測で venv 2.2GB + extscache (`kit` 単体で 5.5 GiB。3 つの取得に 20 分) |

```bash
# pip 版を使う場合
cd simulator
uv sync --extra isaac                       # 147 パッケージ。Python 3.12 必須
ISAAC_RUNTIME=pip bash scripts/run_isaac_case.sh baseline
```

pip 版には `isaacsim-ros2` (OmniGraph の ROS 2 ブリッジ) と `isaacsim-sensor`
(RTX LiDAR) を含む `isaacsim-*` 22 個に加え、Kit の拡張本体を運ぶ
`isaacsim-extscache-*` 3 個が入る。wheel は linux (x86_64 / aarch64) と
win_amd64 の 3 つが lock に入っている。

> **`extscache` を extras から外さないこと。** `all` に入るのは `isaacsim-*` の
> 22 個だけで、Kit の拡張 493 個は `extscache` が運ぶ。外すと
> `isaacsim.exp.base.kit` が要求する 4 つの schema 拡張が**どこにも無く**、
> Kit が起動 2 秒で `Failed to resolve extension dependencies` で終わる。
> experience を替えても全部 `isaacsim.exp.base` を継承するので逃げられない。

> **初回起動は EULA の同意を対話で聞かれる。** 標準入力が無いと
> `Unable to bootstrap inner kit kernel: EOF when reading a line` で止まる。
> 一度だけ端末から起動して `Yes` と答えるか、`OMNI_KIT_ACCEPT_EULA=YES` を
> 立てること。

> **`--no-sync` を省かないこと。** `uv run --extra isaac` は**その場で同期を始める**
> ので、「入っているか確認するだけ」のつもりの 1 行が 20 GiB 超のダウンロードに
> なる (実際に踏んだ)。`run_isaac_case.sh` は確認にも起動にも `--no-sync` を付けて
> あり、入っていなければダウンロードせずに終了コード 2 で止まる。

> **`.python-version` と Python マーカは対で機能する。**
> `isaacsim` 6.0.1 の wheel は **cp312 のみ**で `Requires-Python: ==3.12.*`。
> 素直に `isaac = ["isaacsim[all]==6.0.1.0"]` と書くと、このプロジェクトの
> `requires-python = ">=3.10"` と衝突して `uv lock` が **extra を使わない人の分も
> 含めて**失敗する (`No solution found ... requires Python ==3.12.*`)。
> そのため `; python_full_version ~= '3.12.0'` を付け、venv 側は
> `.python-version` で 3.12 に固定してある。
> **`.python-version` を消すとマーカが外れ、`uv sync --extra isaac` は
> 何も入れないまま成功する。** 沈黙して壊れるので、片方だけ触らないこと。

> **`prerelease = "explicit"` にしてある理由。**
> `isaacsim-core` が `tinyobjloader==2.0.0rc13` のようにプレリリースを `==` で
> 指しているため、既定の `disallow` では解決できない。`allow` にすると
> **numpy などが将来の再 lock でプレリリースに化け得る**ので使わず、
> 受け入れる 3 つ (`tinyobjloader` / `cuda-bindings` / `pyopengl`) を
> `[project.optional-dependencies]` に名指ししてある。

### Isaac Sim 6.0 で変わったところ

`isaac_raspicat.py` は元々 5.x 向けに書いた。**2026-08-20 に 6.0.1 で実際に
動かして、当たる変更が 6 つあることが分かった** (それ以前は 2 つと書いていたが、
どちらも机上で、うち 1 つは見立てが違っていた)。バージョン番号ではなく
**「実行時に何が在るか」で分岐**させる方針はそのまま。版数で分岐すると、
互換 shim が外れた版で静かに壊れる。

| # | 変更 | 対応 |
|---|---|---|
| 1 | **`isaacsim[all]` に Kit の拡張本体が入らない。** `isaacsim.exp.base.kit` が要求する `isaacsim.anim.robot.schema` / `isaacsim.replicator.agent.schema` / `omni.metropolis.schema` / `omni.behavior.tree.schema` がどこにも無く、Kit が起動 2 秒で `Failed to resolve extension dependencies` で終わる。experience を替えても全部 `isaacsim.exp.base` を継承するので逃げられない | `pyproject.toml` の extra を `isaacsim[all,extscache]` に。`extscache` の 3 パッケージ (kit / kit-sdk / physics) が `isaacsim/extscache/` の 493 拡張を運ぶ。**kit 単体で 5.5 GiB** |
| 2 | **pip 版の ROS 2 ブリッジは内蔵 Humble を使う。** 起動前に `ROS_DISTRO` / `RMW_IMPLEMENTATION` と `isaacsim.ros2.core/humble/lib` の探索パスが要り、無いと `ROS2 Bridge startup failed` で**ブリッジだけが死ぬ** (Kit は上がるのでグラフや DDS の問題に見える) | `run_isaac_case.sh` の `pip` 分岐が立てる。手で起動するときは自分で立てること (下の「立ち上げ順序」) |
| 3 | **URDF importer の C++ インタフェース `_urdf` が廃止。** `ImportConfig()` / `acquire_urdf_interface()` / `import_robot()` が無い | `import_urdf()` が `URDFImporter` / `URDFImporterConfig` を import できたらそちらへ。6.0 は「USD ファイルに落として参照する」形なので、生成先を URDF の隣にしないこと (`package://` の解決が URDF の置き場基準のため) |
| 4 | **RTX LiDAR がプロファイル JSON を読まなくなった。** `isaacsim.sensors.rtx.LidarRtx` (`config_file_name`) ごと消え、残る `isaacsim.sensors.experimental.rtx.Lidar.create` は `SUPPORTED_LIDAR_CONFIGS` の名前かセンサ資産の USD しか受けない。実体は `get_assets_root_path()` から引かれる。`/app/sensors/nv/lidar/profileBaseFolder` は**参照されない** | `_BUNDLED_PROFILE` (2d=`Example_Rotary_2D` / mid360=`Example_Solid_State`) を使い、**走査諸元が実機と違うことを警告する**。JSON を渡す経路 (`configs/*.json` と `--lidar-config`) は 2026-08-20 に削除した。別のものを選ぶのは `--lidar-profile` |
| 5 | **OmniGraph のノード型名の名前空間がノード単位で散った。** `OnPlaybackTick` は `omni.graph.action`、`IsaacCreateRenderProduct` は `isaacsim.core.nodes`、`IsaacReadIMU` は `isaacsim.sensors.physics`。`ROS2*` と残りの `Isaac*` は据え置き | `node_type()` の解決を**グループ単位からノード名ごと**に変え、`og.ObjectLookup.node_type()` で実在を照合してから使う。グループの先頭を決め打ちにすると、そのグループの他のノードが道連れで壊れる |
| 6 | `ROS2PublishTransformTree` がプリムを自分で解決するのをやめ、`IsaacComputeTransformTree` の出力を受け取る形に | ノードレジストリに `IsaacComputeTransformTree` が在ればグラフを 2 段に組む。**ここだけは未検証** (`PUBLISH_LINK_TF=isaac` の経路) |

> **ノード型が在るかを `og.get_node_type()` で判定しないこと。** あれは
> 未登録でも例外を投げずにオブジェクトを返すので、常に「在る」になる。
> 判定に使えるのは `og.ObjectLookup.node_type()` のほう (`og.Controller.edit`
> が内部で使うのもこちら)。なお `og.GraphRegistry().get_node_types()` は
> omni.graph 1.142 に**存在しない** — 以前ここで名前空間を照合していたが、
> 例外を握り潰して常に空集合を返していたので、実質ハードコードだった。

> **LiDAR の取り付け位置は作成後に USD の xform へ入れる。** 6.0 の
> `Lidar.create` は複数プリムを一度に作る API で、姿勢の引数が複数形
> (`translations` / `orientations`) なうえ `orientations` は**ワールド系**、
> `translations` は**ローカル系**という混在。原点に置いたままでも 2m の壁を
> 撃つ限りもっともらしいスキャンが返るので、**帯だけがずれても気づけない**。

> **`/cmd_vel` が届いているのに車輪が回らない失敗が 2 つある。** どちらも
> エラーも警告も出ず、`/scan_raw` も `/odom` も `/tf` も正常に出続けるので、
> 症状は「nav2 がゴールに近づかない」だけになり、原因がプランナや
> ローカライザに見える (2026-08-20 に両方踏んだ)。
> **(1) `IsaacArticulationController` に渡すのは articulation root の prim。**
> 6.0 の URDF Importer は root を `<inertial>` を持つ最初のリンク
> (`<robot_prim>/Geometry/base_footprint/base_link`) に置くので、`--robot-prim` の
> Xform を渡すと掴めない。`articulation_root()` が探して渡す。
> **(2) 車輪ジョイントの角度ドライブは damping を入れないと力を出さない。**
> URDF に `<dynamics damping=...>` が無いと Importer は damping=0 / stiffness=0 に
> するので、速度目標を与えても 1 N も出ない。`set_wheel_drives()` が
> `--wheel-drive-damping` (既定 1e4) を入れる。stiffness は 0 のまま
> (0 でないと位置追従になって車輪が原点へ戻ろうとする)。キャスタは触らない。
> 切り分けは手で `/cmd_vel` を投げて `/odom` の `position.x` が動くかを見るのが早い。

6 番は `PUBLISH_LINK_TF=isaac` のときしか通らない (既定は `rsp`)。
起動時に `[isaac_raspicat] rtx lidar api -> ...` と
`[isaac_raspicat] link TF: ...` をログに出すので、**どちらの経路を選んだかは
1 行目付近で確認できる**。ここが期待と違っていたら、以降の症状を追う前に見ること。

> **どちらの経路でも `rclpy` は使えない。** ROS 2 Humble は Python 3.10、
> Isaac 6.0 は 3.12 で拡張モジュールの ABI が合わない。Isaac プロセス内の
> `import rclpy` は成立しないので、ROS 2 とのやりとりは OmniGraph の
> ROS 2 ブリッジで行う。これは pip 版にしても変わらない (むしろ 5.0 の 3.11 より
> 差が開いた)。

`src/daifuku_config/*.json` は `__file__` 基準で解決するので、インストール形態に関係なく
見つかる。

### Isaac のタイムスタンプ

**Isaac が打つ stamp と nav2 の時計は、`use_sim_time` の両側で合わせること。**
`isaac_raspicat.py` は `--use-sim-time` を付けたときだけシム時間で刻印し、既定
(付けない = ハーネスの既定) では `IsaacReadSystemTime` に切り替える。`/clock` を
出すのも `--use-sim-time` のときだけなので、購読側が合わせられる先もそのときだけ
シム時間になる。

ここを取り違えると**エラーも警告も出ないまま自己位置だけが壊れる**。2026-08-20 に
踏んだときの実測は、`/scan_raw` の stamp が 767 秒 (Isaac の起動からの秒) に対して
コンテナのウォールクロックが 1787197524 で、全メッセージが 56 年前扱い。TF の引きが
軒並み extrapolation で落ち、`scan_to_scan_filter_chain` が `/scan` を出さなくなり、
VIOLA が真値から 2.6m ずれたまま経路を引いて機体が壁へ向かった。**`/scan_raw` `/odom`
`/tf` は全部出続けている**ので、症状はプランナかローカライザの不調に見える。

見分けかたは stamp をウォールクロックと突き合わせるだけ:

```bash
date +%s
ros2 topic echo --once --field header /scan_raw     # sec が桁違いに小さければこれ
```

**RTX LiDAR の helper だけは `timeStamp` 入力を持たず自分で打つ**ので、
`ROS2RtxLidarHelper.inputs:useSystemTime` を別に立てている。ここだけ漏らすと
`/odom` と `/tf` はウォールクロック、`/scan_raw` だけシム時間という**いちばん
分かりにくい形**になる。

### Isaac を立て直さずにケースを回さないこと

`nav_container.sh` は `/initialpose` に `START_X` / `START_Y` / `START_YAW`
(= Isaac のスポーン姿勢) を撒く。**Isaac を立て直さずに 2 回目を回すと、ロボットは
前回の走行の終了位置に残っているのに種はスポーン姿勢のまま**なので、自己位置が
最初からずれた測定になる。エラーは出ない。

Isaac の odom はスポーン姿勢が原点なので、`nav_container.sh` は起動時に odom が
原点付近にあるかを見て、離れていれば exit 5 で止まる (承知の上なら
`ALLOW_STALE_POSE=1`)。`run_isaac_case.sh` から回すぶんには毎回 Isaac を起動するので
当たらない。

## 必要なもの

- [uv](https://docs.astral.sh/uv/) (ホスト側。すべての Python はこれ経由で動かす)
- Isaac Sim **6.0.1** — 配布バイナリ、または `uv sync --extra isaac`
  (4.x / 5.x でも動くよう名前空間とノード構成の解決は入れてあるが未検証。
  pip 版で版を下げると必要な Python も変わる: 5.0 = 3.11 / 6.0 = 3.12)
- ROS 2 Humble (nav2 側。コンテナイメージ `daifuku-autonomous:humble-amd64`)
- podman または docker
- NVIDIA RTX GPU (RT コアが要る。Isaac Sim の最低要件は RTX 4080 16GB)

ブリッジは Isaac 側に自前の DDS を持つので、nav2 は別プロセス・別コンテナのまま
でよい。それがこのハーネスが Pi4 の減速をコンテナ側だけに掛けられる理由でもある。

### コンテナイメージ `daifuku-autonomous:humble-amd64` の作り方

**`docker/raspberrypi/Dockerfile` を建てただけでは足りない。** あの Dockerfile が
作るのは `daifuku-autonomous:humble` — ワークスペースが**空**の実行イメージで、
compose が `src/` をマウントし `build/install/log` を名前付きボリュームで受けて、
`up` のたびにコンテナ内で `build-workspace` を回す前提になっている。一方この
ハーネスは、素のコンテナを 1 つ立てて `podman cp` で流し込むだけなので、
**イメージの中に `/opt/ros_ws/install` が要る**。無いと
`"/opt/ros_ws/install/share/daifuku_stack/" could not be found` で止まる。

リポジトリルートで:

```bash
C="podman -c podman-machine-default-root"     # 接続名は環境次第

$C build -f docker/raspberrypi/Dockerfile -t daifuku-autonomous:humble-amd64 .
$C run -d --name wsbuild --user root daifuku-autonomous:humble-amd64 sleep infinity
$C cp src/. wsbuild:/opt/ros_ws/src/
$C cp daifuku_autonomous.repos wsbuild:/opt/ros_ws/daifuku_autonomous.repos

# **ワークスペースのルートで import すること。** repos.yaml のパスが src/… なので、
# src/ の中で叩くと src/src/… になる。
$C exec wsbuild bash -lc 'cd /opt/ros_ws && source /opt/ros/humble/setup.bash     && vcs import . < daifuku_autonomous.repos'

# **ROS を自分で source すること。** podman exec はエントリポイントを通らないので、
# 素で叩くと ament_cmake が見つからず全パッケージが落ちる。
$C exec -e BUILD_JOBS=12 wsbuild bash -lc     'source /opt/ros/humble/setup.bash && cd /opt/ros_ws && build-workspace'

$C exec wsbuild bash -lc 'chown -R 1000:1000 /opt/ros_ws'
$C commit wsbuild daifuku-autonomous:humble-amd64 && $C rm -f wsbuild
```

> **Windows のチェックアウトから流し込むときは、実行ビットと CRLF を直すこと。**
> `core.fileMode=false` なので作業ツリーに実行ビットが無く、`podman cp` は
> それをそのまま持ち込む。`--symlink-install` の `install/lib/<pkg>/x.py` は
> `src/` への symlink なので、**効くのはソース側の権限**。落ちかたが 2 段階で、
> どちらも原因から遠く見える:
>
> * 実行ビットが無い → `executable 'system_monitor.py' not found on the
>   libexec directory` (symlink は在るのに「無い」と言われる)
> * シェバンが CRLF → 起動して **exit code 127** (`python3
` を探しに行く)
>
> ```bash
> LIST=$(git ls-files -s | awk '$1=="100755"{print $4}' | grep '^src/' | sed 's|^src/||')
> $C exec wsbuild bash -lc "cd /opt/ros_ws/src && for f in $LIST; do >     sed -i 's/
$//' \"$f\"; chmod +x \"$f\"; done"
> ```

## 手順

### 1. ワールド USD を作る

Gazebo 版のワールド (`empty.world` / `iscas_museum.world` / `turtlebot3_house.world`)
はいずれも数百バイトしかなく、実体は外部 Gazebo モデル DB への `include` 参照でしか
ない。USD 化しようとすると元モデルの調達から始まる。

代わりに**このリポジトリが既に持っている地図を押し出して**ワールドにする。

```bash
uv run --project simulator map-to-usd \
    src/daifuku_stack/maps/map_19f.yaml -o /tmp/world.usda
```

利点は依存が無いことだけではない。地図とシミュレータ環境が**定義上ずれない**。
実機で起きた emcl2 の alpha 崩壊は「有効ビームの 28% が地図の壁を貫通する」= 地図と
環境の不一致が原因だったので、地図から環境を作れば、その不一致は**意図的に入れた
ときだけ**再現される。

主なオプション:

| オプション | 意味 |
|---|---|
| `--unknown free\|wall` | 未観測セルの扱い。`fake_robot.py` の `unknown_as_obstacle` と同義 (既定 `free`) |
| `--wall-height` | 壁の高さ [m] (既定 2.0) |
| `--max-prims` | 矩形数の上限 (既定 50000) |

実測した規模感:

| 地図 | セル | 占有セル | 矩形 | USD |
|---|---|---|---|---|
| `map_19f` | 915×577 | 9,146 | 3,373 | 1.6 MiB |
| `map_tsudanuma` | 5888×4000 | 176,107 | 67,939 | **上限超過** |

津田沼地図は先に粗くする:

```bash
uv run --project simulator downsample-map \
    src/daifuku_stack/maps/map_tsudanuma.yaml /tmp/ts4.yaml --scale 4
uv run --project simulator map-to-usd /tmp/ts4.yaml -o /tmp/ts4.usda
# 1472x1000 @0.2m -> 12,011 矩形 / 5.8 MiB
```

> **`--unknown wall` が効かないように見えたら、それは地図側の問題**
> 2026-08-09 まで `map_19f.yaml` は `free_thresh: 0.25` で、map_saver の未観測画素 205 は
> p=0.196 なので **free 側に落ちて未観測と判定されなかった**。実測では `--unknown wall`
> を付けても占有セルは 9,146 → 9,147 と 1 セルしか増えなかった。いまは `0.15` なので
> 413,191 セル (78.26%) が壁になる。自分で用意した地図で同じ症状が出たら、そちらの
> `free_thresh` を疑うこと（[`docs/pi4_sim.md`](docs/pi4_sim.md#free_thresh-を下げるときの注意)）。

### 2. ロボット USD を作る

`raspicat_description` の URDF を使う。**Gazebo プラグインを外して**展開すること。
差動駆動・LiDAR・IMU は `isaac_raspicat.py` が OmniGraph で作り直すので、URDF 側に
`<gazebo>` タグが残っていると「どちらが効いているのか」が分からなくなる。

```bash
# 素の URDF を吐く
xacro $(ros2 pkg prefix raspicat_description)/share/raspicat_description/urdf/raspicat.urdf.xacro \
    gazebo_plugin:=false camera_gazebo_plugin:=false imu_gazebo_plugin:=false \
    > /tmp/raspicat_plain.urdf
```

`isaac_raspicat.py` に `--urdf /tmp/raspicat_plain.urdf` を渡せばその場で取り込む。
毎回変換したくなければ Isaac の URDF Importer で一度 USD にして `--robot` に渡す。

**`<collision>` と `<inertial>` を落とした URDF を渡さないこと。** 手で書き写した
リンク定義や、visual だけを抜いたものを渡すと、**ロボットは床をすり抜けて落ち続ける**。
Isaac は起動もするしトピックも全部出るので（`/scan_raw` も `/odom` も `/tf` も
出続ける）、症状は「nav2 がゴールに近づかない」だけになり、原因が
プランナやローカライザに見える。見分けかたは `/odom` の `position.z` —
落ちていれば秒単位で大きな負の値になる（2026-08-20 に踏んだときは
`z = -2027133`）。`xacro` を通したものは 5 リンクぶんの `<collision>` と
`<inertial>` を持つ（`grep -c '<collision'` が 5）。

### 3. 立ち上げ順序 (最初はここを段階的に)

いきなり `run_isaac_case.sh` を叩かず、まず Isaac 単体を GUI 付きで上げて
トピックが出ているか確認する。

```bash
export ISAACSIM=$HOME/isaacsim

# (a) Isaac 単体。GUI ありでロボットが地図の中に立っているか目で見る
$ISAACSIM/python.sh simulator/src/daifuku_sim/isaac_raspicat.py \
    --world /tmp/world.usda --urdf /tmp/raspicat_plain.urdf \
    --lidar 2d -x -1.27 -y -0.63
# pip 版なら (--no-sync を落とさないこと。落とすとこの場で同期が始まる):
#   pip 版は内蔵 ROS 2 を使うので、先にこれを見せる。無いと ROS2 Bridge
#   だけが死ぬ (Kit は上がるので原因がグラフや DDS に見える)。
#   run_isaac_case.sh の pip 分岐は自分でやるので、手起動のときだけ要る:
#     export ROS_DISTRO=humble RMW_IMPLEMENTATION=rmw_fastrtps_cpp
#     export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:<venv>/lib/site-packages/isaacsim/exts/isaacsim.ros2.core/humble/lib
#     (Windows は LD_LIBRARY_PATH ではなく PATH)
#   uv run --project simulator --extra isaac --no-sync python \
#       simulator/src/daifuku_sim/isaac_raspicat.py --world ... (以下同じ)

# (b) 別端末でトピックを確認 (ROS_DOMAIN_ID を揃えること)
ros2 topic hz /scan_raw
ros2 topic hz /odom
ros2 topic pub -r 5 /cmd_vel geometry_msgs/msg/Twist '{linear: {x: 0.1}}'   # 動くか
```

`/scan_raw` が出なければ LiDAR プロファイルの問題が濃厚。既定は同梱プロファイル
(2d=`Example_Rotary_2D` / mid360=`Example_Solid_State`)。別のものを選ぶには:

```bash
$ISAACSIM/python.sh simulator/src/daifuku_sim/isaac_raspicat.py ... --lidar-profile Example_Rotary
```

**6.0 の `--lidar-profile` が受け付けるのは登録済みの USD アセット名だけ**
(`Lidar.create` が `get_assets_root_path()` の下から引く)。同梱 JSON を置く場所へ
自分の JSON を並べても `Lidar config ... not found` で落ちる。実機に寄せるなら
`Lidar.create(usd_path=...)` へ渡す USD を起こす経路になる。**一覧にある実在機種
(`RPLIDAR_S2E` など) へ差し替えるのも駄目** —— あちらはアセットの中に OmniLidar が
入れ子で入っているので、`IsaacCreateRenderProduct` が掴めず
`Render product ... not attached to RTX Lidar` を出し続けて **`/scan_raw` ごと消える**
(2026-08-20 実測)。

### 4. Pi4 相当で 1 ケース通す

```bash
export ISAACSIM=$HOME/isaacsim
export ROBOT_URDF=/tmp/raspicat_plain.urdf

bash simulator/scripts/run_isaac_case.sh baseline

# MID360 構成
LIDAR=mid360 bash simulator/scripts/run_isaac_case.sh mid360_run

# 制限なしとの対照
NO_LIMITS=1 CONTAINER=isaacsim_full bash simulator/scripts/run_isaac_case.sh nolimits
```

主な環境変数 (既定値は実機の現行設定に一致):

| 変数 | 既定 | 意味 |
|---|---|---|
| `ISAAC_RUNTIME` | `binary` | `binary` (`$ISAACSIM/python.sh`) / `pip` (`uv --extra isaac`) |
| `LIDAR` | `2d` | `2d` / `mid360` |
| `MAP_NAME` | `map_19f` | `src/daifuku_stack/maps/<name>.yaml`。launch と `nav_container.sh` の既定と揃えてある |
| `PLANNER` | `vi` | `vi` / `navfn` |
| `LOCAL_PLANNER` | `auto` | `auto` / `nav2` / `vi` |
| `NAV2` | `auto` | `auto` / `true` / `false`。**ここだけ launch の既定（`false`）と違う** — 下記 |
| `LOCALIZATION` | `vi` | `vi` / `emcl2` / `amcl`。**launch の既定 (`emcl2`) と違う** — 既定の `map_19f` は overrides が `vi_planner` の `localizer` を `belief` にしているので、`emcl2` だと推定器が 2 つになって起動時に止まる。`map_tsudanuma` では逆に `emcl2` へ戻すこと |
| `QUOTA` / `PERIOD` | `6000` / `10000` | cgroup の cpu.max (0.6 コア) |
| `MEMORY` | `3g` | Pi4 4GB から OS + コンテナ外ノード分を引いた値 |
| `USE_SIM_TIME` | `false` | 下記「RTF」を読むこと |
| `MIN_RTF` | `0.95` | RTF ゲートのしきい値 |
| `ROBOT_URDF` / `ROBOT_USD` | — | どちらか必須。URDF なら Isaac と rsp の両方に同じものを使う |
| `PUBLISH_LINK_TF` | `rsp` | リンク間 TF の所有者 (`rsp` / `isaac`)。下記「TF の所有者」 |
| `WORLD_MAP_YAML` | `MAP_NAME` と同じ | **意図的に**ワールドと地図をずらすとき用 |
| `PLANNER_EXPECTED_FREQ` | — | キャリブレーション用 (下記) |

**`NAV2` だけは launch の既定を引き継いでいない。** `navigation.launch.py` の `nav2:=` は
既定 `false`（Nav2 の navigation ノードを立てず、`vi_planner` が `navigate_to_pose` も
出す）だが、そのまま渡すと `PLANNER=navfn` と `LOCAL_PLANNER=nav2` のケースが
**起動時にエラーで止まる**（`navigate_to_pose` を出すものが居なくなるため、launch が
わざと弾く）。ハーネスは条件を振るのが仕事なのでプランナに追従する `auto` を既定に
してある。**したがって `PLANNER=vi` のケースは BT 抜きで測ることになる** — `bt_navigator`
込みで測りたいとき、および `docs/pi4_sim.md` の過去の記録と条件をそろえたいときは
`NAV2=true` を明示すること。

### Isaac と nav2 を繋ぐ (Windows + WSL)

**このハーネスは本来 Linux ホスト前提**で、Isaac も nav2 コンテナも同じカーネルの
上に居ることを当てにしている（`--network host --ipc host` で DDS がそのまま通る）。
Windows で回すと Isaac は Windows のプロセス、コンテナは WSL2 の VM の中なので、
そのままでは**エラーも出ないまま一度もトピックが見えない**。以下は 2026-08-20 に
Windows 11 + podman machine (WSL2) で通したときの手順。

**1. `%USERPROFILE%\.wslconfig` でネットワーク名前空間を共有する。**

```ini
[wsl2]
networkingMode=mirrored
firewall=false          ; DDS のディスカバリが Hyper-V ファイアウォールに黙って落とされる
dnsTunneling=true

[experimental]
hostAddressLoopback=true ; Linux -> Windows の 127.0.0.1
```

（実測で通したのはこの 4 つを入れた状態。`firewall` と `hostAddressLoopback` の
どちらが効いているかまでは切り分けていない。）

`wsl --shutdown` してから `podman machine start`。効いていれば、コンテナの中から
Windows 側の NIC（Tailscale や LAN のアドレス）がそのまま見える。

罠が 1 つ。**`podman machine start` が `machine is not listening on ssh port` で
落ちることがある。** mirrored では Windows と Linux でポート空間が共有されるので、
前回の `win-sshproxy.exe` が握ったままだと machine 内の sshd が
`Bind to port <n> failed: Address already in use` で上がれない。残っている
`win-sshproxy.exe` を落としてから start し直す。

**2. Fast DDS のプロファイルを両側に噛ませる。** mirrored にしただけでは繋がらない。

```bash
bash simulator/container/make_fastdds_mirrored.sh 91 > /tmp/fastdds_mirrored.xml
export FASTRTPS_DEFAULT_PROFILES_FILE=/tmp/fastdds_mirrored.xml   # Isaac 側
```

`run_isaac_case.sh` はこの変数が立っていれば**同じファイルをコンテナへも配って**
nav2 側にも渡す（立っていなければ何もしない = Linux ホストでは従来どおり）。
理由 3 つは生成器の冒頭にある。要点だけ:

* **SHM を入れると discovery が黙って止まる。** Windows 側の Isaac は DDS の
  ポートを 1 つも開かないまま上がり、コンテナ側は同じコンテナ内の 2 ノードすら
  見つけられない。
* **マルチキャストが一度も届かない。** ユニキャストのピアをポートまで書いて
  並べる（ポートを書かない locator は participant 0〜4 にしか広がらず、nav2 には
  足りない）。ポートがドメイン依存なので静的な XML にできない。
* **UDP の `maxMessageSize` を 1400 に絞る。** 既定のままだと `LaserScan` が IP
  フラグメントになって Windows → WSL の境界で全部落ちる。`/odom` や `/tf` は
  70Hz で流れるので、**「繋がっているのに `/scan_raw` だけ来ない」**という形で
  失敗する。

**3. 測定値としての限界。** SHM を落とした結果、**コンテナ内のノード同士も UDP に
なる**ので、pi4_sim ハーネス（`container/fastdds_local.xml`、SHM 併用）とは通信
経路が違う。Pi4 の再現性を見るのが目的ならあちらを使うこと。ここで測れるのは
「Isaac の環境と繋いだときに nav2 が何をするか」であって、DDS の経路まで含めた
実機再現ではない。

**4. `ros2` のデーモンを立てておくこと**（`nav_container.sh` / `run_case.sh` は
やる）。ros2cli は毎回 127.0.0.1 のデーモンへ繋ぎに行き、居なければ普通は
すぐ諦めるが、mirrored だと**握られたまま 2 分待つ**。`timeout 5 ros2 topic list`
のような呼び出しが全部空を返し、Isaac が正しく喋っていても「トピックが見えない」
で止まる。

**pi4_sim ハーネス (`run_pi4_sim.ps1`) はこの設定に巻き込まれない。** あちらの
コンテナは `--network host` を使わず自前のネットワーク名前空間に居るので、
マルチキャストも SHM もそのまま通る（`.wslconfig` を mirrored にした状態で
`SUCCEEDED` / 19.2 秒を再確認した）。

なお `run_isaac_case.sh` 自体は Linux 向けのまま。Git Bash から叩くと MSYS が
`/opt/...` を `C:/Program Files/Git/opt/...` に化かすので（`--odom-topic /odom` が
`/C:/Program Files/Git/odom` になる）、Windows では `MSYS_NO_PATHCONV=1` を置いて
手で並べるか、WSL 側の shell から回すこと。

## RTF — このハーネスの成立条件

**RTF はログ項目ではなく合否条件**である。理由:

Pi4 相当への減速は cgroup の CPU quota で行うが、quota は**実時間**基準である。
一方 `USE_SIM_TIME=true` にすると nav2 の締め切り・タイマ・TF の期限は**シム時間**
基準になる。ここで RTF (シム時間の進み ÷ 実時間の進み) が 1.0 を割ると:

```
    1 シム秒 = 1/RTF 実秒   →   nav2 は 1 シム秒あたり 1/RTF 倍の CPU 時間を得る
```

つまり **RTF 0.5 の実行は「Pi4 が実際の 2 倍速い」という結果を出す**。
「重い地図でも間に合った」という結論がそのまま嘘になる。

そのため:

- **既定は `USE_SIM_TIME=false`** (Isaac をウォールクロックで自由走行させる)。
  nav2 の時計と cgroup quota が同じ実時間基準になるので、構造的に嘘がつけない。
  `navigation.launch.py` の `use_sim_time` 既定も `false` で、pi4_sim ハーネスも
  この経路を使っている。
- `USE_SIM_TIME=true` は再現性と引き換えに RTF の監視が必須になる。
  `rtf_gate.py` が RTF 不足の実行を **終了コード 3 = 計測無効**として弾く。
  `USE_SIM_TIME=false` のときは警告に留める (減速率そのものは歪まないため)。

RTF が足りないときの対処: `RENDER_DT` を大きくする / `HEADLESS=1` で回す /
`downsample_map.py` で地図を粗くする / `USE_SIM_TIME=false` にする。

## launch 側の変更点

実機の挙動を変えないよう、追加した引数の既定値は現行のままにしてある。

- `lidar_bringup.launch.py` / `robot_bringup.launch.py` に **`lidar_driver`** (既定 `true`) を追加
  (センサーを立てるのは機体側なので、`navigation.launch.py` にこの引数は無い)。
  `false` にすると:
  - `livox_ros_driver2` を起動しない (Isaac が `/livox/lidar` を PointCloud2 で直接出す。
    実機ドライバも `xfer_format: 0` = PointCloud2 なので**同型**で、下流は一切変わらない)
  - `restamp_scan.py` を起動しない。あれは MID360 の**デバイス時計が PTP 同期されず
    毎分数秒ドリフトする**ことへの対処であって、シムには存在しない問題である。とくに
    `use_sim_time:=true` では「受信時刻で押し直す」動作がシム時間と噛み合わず有害になる
  - `pointcloud_to_laserscan` の出力を `/scan_mid360_prestamp` ではなく直接
    `/scan_raw` に出す
  - `lidar:=2d` では `urg_node` (raspicat の URG) を起動しない。`/scan_raw` は
    Isaac が LaserScan で直接出す
  - `MID360_config.json` の存在チェックを飛ばす (driver 専用のファイルなので)

## TF の所有者

TF ツリーは区間ごとに**所有者を 1 つだけ**にする。二重に出すと同じ transform が
別ソースから流れ、tf2 がどちらを採るかで**自己位置だけが静かに壊れる**
(トピックは全部出ているように見える)。

| 区間 | 所有者 |
|---|---|
| `map → odom` | emcl2 (または amcl) |
| `odom → base_footprint` | Isaac (`lidar:=2d`) / **ekf_node** (`lidar:=mid360` + EKF) |
| `base_footprint → base_link → lidar_link …` | **robot_state_publisher** (既定) |

リンク間 TF の既定の所有者を `robot_state_publisher` にしてあるのは実機と同じ配置
だから (`robot_bringup.launch.py` も rsp を上げている)。`isaac_raspicat.py` の
`--publish-link-tf` は既定 `false` で、Isaac はリンク間 TF を出さない。

`container/nav_container.sh` は URDF から `robot_state_publisher` を起動し、**nav2 を上げる前に
`base_footprint → lidar_link` (mid360 なら `livox_frame`) が実際に引けることを確認**
してから進む (引けなければ終了コード 6)。ここを確認せずに nav2 を上げると
`laser_filters` と emcl2 が原因の分からない沈黙で失敗する。

Isaac 側にリンク間 TF を出させたい場合は両方を切り替える:

```bash
PUBLISH_LINK_TF=isaac bash simulator/scripts/run_isaac_case.sh mycase   # nav 側は rsp を起動しない
# かつ isaac_raspicat.py に --publish-link-tf true を渡す
```

`robot_state_publisher` には **Isaac が読み込んだのと同じ URDF** を使わせる。
`ROBOT_URDF` を指定していれば `run_isaac_case.sh` がコンテナへコピーする。
別々に生成すると、リンクのオフセットが食い違っても誰も気づけない。

なお `import_urdf` は `merge_fixed_joints = False` にしてある。畳むと `base_link` や
`lidar_mount_link` のプリムが消えて `--lidar-prim-path` の取り付け先が失われる。
LiDAR の高さが 2 cm ずれても 2 m の壁を撃つ限りもっともらしいスキャンが返るので、
間違いに気づけない。

## odom のトピックを lidar モードから推測しないこと

launch 側の配線が構成によって変わる。`run_isaac_case.sh` は明示的に渡している。

| 構成 | Isaac が出すトピック | `odom→base_footprint` の TF |
|---|---|---|
| `lidar:=2d` | `/odom` | Isaac が出す |
| `lidar:=mid360` + EKF | `/wheel/odom` | **ekf_node が出す** (Isaac は出さない) |

MID360 構成では `robot_localization` の `ekf_node` が `/wheel/odom` と `/imu/mid360` を
融合して `odometry/filtered` → `/odom` に remap している。**実機ではこれを立てるのは
`daifuku_bringup` の `robot_bringup.launch.py`** (docker compose up で常駐) だが、シムは
駆動ドライバが要らないので `nav_container.sh` が `odom_fusion.launch.py` を直接立てて
いる。launch の既定は `use_mid360_imu:=true` (環境変数 `USE_MID360_IMU` で切り替わる)
だが、コンテナに環境変数が無い環境でも変わらないよう `lidar:=mid360` のときだけ
明示している。**渡し先を `navigation.launch.py` に戻さないこと** — あちらはもう
`use_mid360_imu` を宣言していないので、黙って EKF が立たなくなる。そうなると
`odom → base_footprint` を誰も出さない (逆に Isaac 側にも出させると二重になる) が、
どちらも **「なんとなく動いて見えるのに自己位置だけ壊れる」** 形で失敗する。

同じ理由で `nav_container.sh` と `run_case.sh` は `lidar_bringup.launch.py` も直接
立てている (`/scan` の出どころ)。**`navigation.launch.py` はセンサーを 1 つも
立てない。**

## 再現できないもの

`simulator/docs/pi4_sim.md` の限界は Isaac にしても**ほぼそのまま残る**。
Isaac が良くするのはセンサの現実感であって、CPU タイミングの忠実性ではない。

- **単一スレッドのレイテンシは実機より速い。** cgroup quota は合計スループットしか
  絞らないので、ディスカバリ・bond・コールバック遅延といった直列パスは楽観的に出る。
  これは Isaac に置き換えても変わらない。
- **コンテナ外 (実機ホスト側) の負荷は含まない。** raspicat ドライバ,
  robot_state_publisher (実測でコア 60%), livox ドライバなど。その分は quota を
  「Pi4 4 コア分」ではなく「nav2 が実際に取れた分」に絞ることで代用する。
- **emcl2 の alpha 崩壊は既定では再現されない。** あれは地図と実環境の不一致が原因で、
  地図から生成したワールドでは定義上一致してしまう。**意図的にずらす**なら
  `WORLD_MAP_YAML` にワールド用の別地図を指定する (`map_server` は `MAP_NAME` の
  ままにする)。指定すると `run_isaac_case.sh` が「意図的な不一致の注入である」旨を
  警告として出す。うっかり食い違わせた実行を「一致しているつもり」で読むのが
  いちばん危険なので、そこだけは黙って通さないようにしてある。
- **odom が真値になる。** `IsaacComputeOdometry` は物理の真値をそのまま出すので
  odom はドリフトしない。`fake_robot.py` は意図的にドリフトさせている
  (`odom_fw_scale 1.02` / `odom_rot_scale 0.99` + 移動量比例ノイズ) ので、
  **emcl2 に補正すべきものが無くなり `map→odom` が育たない**。この 1 点だけは
  pi4_sim ハーネスのほうが実機に近い。emcl2 の収束や `map→odom` の挙動そのものを
  見たい場合は pi4_sim 側で見ること。Isaac 側が優れるのは環境の幾何とセンサの
  現実感であって、オドメトリの誤差モデルではない。
- **MID360 の非繰り返し走査は再現していない。** `--lidar mid360` は同梱の
  `Example_Solid_State` で代用していて (上の「Isaac Sim 6.0 で変わったところ」)、
  走査諸元は実機と違ううえ**この経路は未検証**。「時間をかけると隙間が埋まる」という
  MID360 固有の性質は出ない。点群そのものを使うアルゴリズムの評価には向かない。

## つまずきやすいところ

**壁の高さをセンサに合わせて薄くしない。** `mid360_scan.yaml` は `base_footprint`
基準で 0.30–0.50 m を切り出し、2D LiDAR は URDF 上 0.14 m あたりに来る。どちらかに
合わせるともう片方が空スキャンになり、「自己位置推定が壊れた」ように見える。
既定の 2.0 m は両方まとめて覆うための値。

**`/scan_raw` は出るのに nav2 が繋がらない。** `ROS_DOMAIN_ID` の不一致か、
コンテナが `--network host --ipc host` で起動していないか。`--ipc host` が無いと
Fast DDS の共有メモリトランスポートが通らず、**ディスカバリだけ成功してデータが
流れない**という分かりにくい失敗をする。`container/nav_container.sh` は起動前に
`/scan_raw` と `/odom` の存在を 30 秒待って、無ければ見えているトピック一覧を
出して終了コード 4 で止まる。

**プランナの周波数でキャリブレーションする。** 実機で取れている数少ない実測値
「navfn の planner ループが 20Hz 設定に対し実測 7.6Hz」が的。

```bash
PLANNER=navfn PLANNER_EXPECTED_FREQ=20 bash simulator/scripts/run_isaac_case.sh calib
# nav_container.sh が nav.log から "current loop rate is X Hz" を拾って出す
```

`planner_server` は**達成できない周波数を設定したときだけ**実測値を WARN に出す。
`PLANNER_EXPECTED_FREQ` はそのための注入で、`container/nav_container.sh` が
`extra_params_file` 経路の overlay に載せる (`simulator/container/run_case.sh` と同じ方式)。
同じ経路で `VI_SOLVER` / `VI_MAP_SCALE` / `VI_COMPACT_SINK_DIR` /
`VI_PUBLISH_VF` / `BT_SERVER_TIMEOUT` も渡せる。

このとき地図の `free_thresh` は `MAP_FREE_THRESH=0.25` で戻すこと。7.6Hz は 518k セルが
free の地図で測った値で、しきい値を直すと navfn の問題規模が 1/4 になり、まったく
違う quota に合わせてしまう（同梱の `map_19f.yaml` は 2026-08-09 に 0.15 = 105k セルへ
直した）。

## ファイル

パスは `simulator/` からの相対。

| ファイル | 実行方法 | 役割 |
|---|---|---|
| `src/daifuku_sim/map_to_usd.py` | `uv run map-to-usd` | 占有格子地図 → ワールド USD (`pxr` 不要の手書き `.usda`) |
| `src/daifuku_sim/rtf_gate.py` | `uv run rtf-gate` | RTF レポートを読んで実行の成立/不成立を判定 |
| `src/daifuku_sim/downsample_map.py` | `uv run downsample-map` / コンテナ内で `python3` | 占有格子地図の整数倍ダウンサンプル (障害物優先)。**両ハーネスとホストで共有** |
| `src/daifuku_sim/isaac_raspicat.py` | `$ISAACSIM/python.sh <path>` | Isaac Sim standalone。ロボット読込 + OmniGraph ROS 2 ブリッジ + RTF 計測 |
| `scripts/run_isaac_case.sh` | `bash` (ホスト) | Isaac 版オーケストレータ (world 生成 → Isaac → nav2 コンテナ → RTF 判定) |
| `scripts/run_pi4_sim.ps1` | `powershell` (ホスト) | pi4_sim 版オーケストレータ。詳細は `docs/pi4_sim.md` |
| `scripts/run_matrix.ps1` | `powershell` (ホスト) | pi4_sim 版をケース一式まとめて回して `PROBE_SUMMARY` を集める |
| `container/nav_container.sh` | `bash` (コンテナ内) | nav2 を起動しゴールを 1 回投げる。`run_isaac_case.sh` が送り込む |
| `container/run_case.sh` | `bash` (コンテナ内) | 同上 + `fake_robot.py` の起動。`run_pi4_sim.ps1` が送り込む |
| `container/fake_robot.py` | `python3` (コンテナ内) | 差動二輪 + 2D LiDAR の疑似ロボット (地図をレイキャスト) |
| `container/probe.py` | `python3` (コンテナ内) | ゴール投入と `/plan` `/cmd_vel` の計数、RSS / cgroup メモリのサンプリング。**両ハーネスで共有** |
| `container/fastdds_local.xml` | — | 実機 DDS プロファイルのローカル版 (SHM + ループバック UDP) |
| `container/make_fastdds_mirrored.sh` | `bash` (ホスト) | Windows + WSL (`networkingMode=mirrored`) で Isaac とコンテナを繋ぐ DDS プロファイルを吐く。**両側に同じものを渡す**。上の「Isaac と nav2 を繋ぐ」 |
| `tests/verify_usda.py` | `uv run python tests/...` | 生成 USD を地図グリッドに焼き戻して一致を検算 (主に y 反転の検出) |
| `tests/verify_map_thresholds.py` | `uv run python tests/...` | 地図の `free_thresh` が未観測画素 205 を free に落としていないかを検算。`map_saver_cli` の既定 0.25 だと落ちる |
