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

このリポジトリの開発機には NVIDIA GPU が無く (AMD Radeon 780M)、Isaac Sim は
起動できない。**どこまで実測で確かめたかを明示しておく**:

| ファイル | 状態 |
|---|---|
| `src/daifuku_sim/map_to_usd.py` | **実測検証済み**。`map_19f` / `map_tsudanuma` で生成し、出力 USD の Wall プリムを地図グリッドに焼き直して元の占有と**完全一致** (欠落0・余剰0) を確認。検算は `tests/verify_usda.py` (手元で再実行できる) |
| `src/daifuku_sim/rtf_gate.py` | **実測検証済み**。合成 RTF ログで PASS / FAIL(3) / WARN の分岐を確認 |
| `pyproject.toml` / `uv.lock` | **実測検証済み**。`uv lock` / `uv sync` / `uv run map-to-usd` / `uv run rtf-gate` の成功を確認 |
| extra `isaac` (pip 版 Isaac Sim 6.0.1) | **解決のみ検証済み**。`uv lock` が 166 パッケージで解決し、`uv sync --extra isaac --dry-run` が `isaacsim-*` 22 個を含む 144 個を入れると出すことを確認。lock には linux (x86_64 / aarch64) と win_amd64 の wheel が入っている。**実際にインストールして起動したことは無い** (GPU 必須) |
| `src/daifuku_sim/src/daifuku_config/*.json` | 生成ロジックは検証済みだが、**Isaac の RTX LiDAR プロファイルのスキーマとの適合は未検証**。6.0 でプロファイル探索パスの機構が変わったかは NVIDIA のドキュメントに記載が無く、確認できていない |
| `src/daifuku_sim/isaac_raspicat.py` | **未検証** (GPU 必須)。5.x 向けに書き、6.0 の変更 2 点 (RTX LiDAR API / TransformTree の分割) への分岐を後から入れた。**分岐の条件は実行時判定だが、6.0 側の分岐が通ることは一度も確かめていない** |
| `scripts/run_isaac_case.sh` / `container/nav_container.sh` | 構文チェックのみ。**未実行** |
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
| 取得量 | 配布物次第 | 新規 **144 パッケージ** (lock 上の総サイズ 22.9 GiB) |

```bash
# pip 版を使う場合
cd simulator
uv sync --extra isaac                       # 144 パッケージ。Python 3.12 必須
ISAAC_RUNTIME=pip bash scripts/run_isaac_case.sh baseline
```

pip 版には `isaacsim-ros2` (OmniGraph の ROS 2 ブリッジ) と `isaacsim-sensor`
(RTX LiDAR) を含む `isaacsim-*` 22 個が入るので、このハーネスが使う機能は揃う。
wheel は linux (x86_64 / aarch64) と win_amd64 の 3 つが lock に入っている。

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

`isaac_raspicat.py` は元々 5.x 向けに書いたが、6.0 で当たる変更が 2 つあった。
どちらも**バージョン番号ではなく「実行時に何が在るか」で分岐**させてある。
版数で分岐すると、互換 shim が外れた版で静かに壊れる。

| 変更 | 対応 |
|---|---|
| `isaacsim.sensors.rtx.LidarRtx` → `isaacsim.sensors.experimental.rtx.Lidar.create()`。引数も `config_file_name` → `config` に改名 | `_make_rtx_lidar()` が import できたほうを使い、引数名は `inspect.signature` で詰める |
| `ROS2PublishTransformTree` がプリムを自分で解決するのをやめ、`IsaacComputeTransformTree` の出力を受け取る形に | ノードレジストリに `IsaacComputeTransformTree` が在ればグラフを 2 段に組む |

後者は `PUBLISH_LINK_TF=isaac` のときしか通らない (既定は `rsp`)。
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
#   uv run --project simulator --extra isaac --no-sync python \
#       simulator/src/daifuku_sim/isaac_raspicat.py --world ... (以下同じ)

# (b) 別端末でトピックを確認 (ROS_DOMAIN_ID を揃えること)
ros2 topic hz /scan_raw
ros2 topic hz /odom
ros2 topic pub -r 5 /cmd_vel geometry_msgs/msg/Twist '{linear: {x: 0.1}}'   # 動くか
```

`/scan_raw` が出なければ LiDAR プロファイルの問題が濃厚。`src/daifuku_config/*.json` の
スキーマが手元の Isaac と合わない場合に備えて逃げ道を用意してある:

```bash
$ISAACSIM/python.sh simulator/src/daifuku_sim/isaac_raspicat.py ... --lidar-profile Example_Rotary
```

同梱プロファイルで `/scan_raw` が出るなら原因は JSON のスキーマなので、
手元の Isaac が持つ `Example_Rotary.json` と `src/daifuku_config/raspicat_2d_lidar.json` を
diff して合わせる。

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
| `LOCALIZATION` | `emcl2` | `emcl2` / `amcl` |
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
- **MID360 の非繰り返し走査は近似。** `src/daifuku_config/livox_mid360.json` は公称 FOV
  (水平 360° / 垂直 −7〜+52°) を 40 本の等間隔ラインで覆う回転式に置き換えてある
  (288,000 pts/s、実機の公称は 200,000 pts/s)。「時間をかけると隙間が埋まる」という
  MID360 固有の性質は再現されない。`pointcloud_to_laserscan` で 0.30–0.50 m を
  切り出す用途には十分だが、点群そのものを使うアルゴリズムの評価には向かない。

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
| `src/daifuku_sim/src/daifuku_config/raspicat_2d_lidar.json` | — | RTX LiDAR プロファイル (360°/0.5°/10Hz/0.1–30 m) |
| `src/daifuku_sim/src/daifuku_config/livox_mid360.json` | — | 同 (MID360 の近似。40 ライン) |
| `scripts/run_isaac_case.sh` | `bash` (ホスト) | Isaac 版オーケストレータ (world 生成 → Isaac → nav2 コンテナ → RTF 判定) |
| `scripts/run_pi4_sim.ps1` | `powershell` (ホスト) | pi4_sim 版オーケストレータ。詳細は `docs/pi4_sim.md` |
| `scripts/run_matrix.ps1` | `powershell` (ホスト) | pi4_sim 版をケース一式まとめて回して `PROBE_SUMMARY` を集める |
| `container/nav_container.sh` | `bash` (コンテナ内) | nav2 を起動しゴールを 1 回投げる。`run_isaac_case.sh` が送り込む |
| `container/run_case.sh` | `bash` (コンテナ内) | 同上 + `fake_robot.py` の起動。`run_pi4_sim.ps1` が送り込む |
| `container/fake_robot.py` | `python3` (コンテナ内) | 差動二輪 + 2D LiDAR の疑似ロボット (地図をレイキャスト) |
| `container/probe.py` | `python3` (コンテナ内) | ゴール投入と `/plan` `/cmd_vel` の計数、RSS / cgroup メモリのサンプリング。**両ハーネスで共有** |
| `container/fastdds_local.xml` | — | 実機 DDS プロファイルのローカル版 (SHM + ループバック UDP) |
| `tests/verify_usda.py` | `uv run python tests/...` | 生成 USD を地図グリッドに焼き戻して一致を検算 (主に y 反転の検出) |
| `tests/verify_map_thresholds.py` | `uv run python tests/...` | 地図の `free_thresh` が未観測画素 205 を free に落としていないかを検算。`map_saver_cli` の既定 0.25 だと落ちる |
