# pi4_sim — Raspberry Pi 4 (4GB) 相当環境のローカル再現ハーネス

実機 (Raspberry Pi Cat / 192.168.1.50) が落ちている間に、「ゴールを送ると
Aborted になる (`plan=0`)」を手元の Podman で切り分けるための一式。

パスは `simulator/` からの相対。Isaac 版ハーネス (`README.md`) とコンテナ側の
置き場・コンテナ内パス (`/opt/sim`)・`probe.py` を共有する。

| ファイル | 役割 |
|---|---|
| `scripts/run_pi4_sim.ps1` | Pi4 相当に絞ったコンテナを作り、1 ケース実行する (Windows 側) |
| `scripts/run_matrix.ps1` | ケース一式を順に回して `PROBE_SUMMARY` を集める |
| `container/run_case.sh` | コンテナ内で nav2 + 疑似ロボットを起動しゴールを1回投げる |
| `container/fake_robot.py` | 差動二輪 + 2D LiDAR の疑似ロボット (地図をレイキャスト) |
| `container/probe.py` | `/plan` `/cmd_vel_nav` `/cmd_vel` の本数、初回 plan までの時間、各ノードの RSS と cgroup メモリを取る診断プローブ。**Isaac 版と共有** |
| `container/fastdds_local.xml` | 実機プロファイルのローカル版 (SHM + ループバック UDP) |
| `src/daifuku_sim/downsample_map.py` | 占有格子地図の整数倍ダウンサンプル (障害物優先)。ホストでは `uv run downsample-map`、コンテナ内では `run_case.sh` が `MAP_SCALE` 指定時に直接呼ぶ |

```powershell
cd simulator\scripts

# 実機と同じ設定 (地図 free_thresh 0.25 / planner vi。solver はリポジトリの
# config のまま = 2026-08-04 以降 frontier2d_sparse_compact。VI_SOLVER で上書き可)
.\run_pi4_sim.ps1 -Case baseline

# 制限なし (全速) との対照
.\run_pi4_sim.ps1 -Case nolimits -Container pi4sim_full -NoLimits

# 地図しきい値だけ直した場合
.\run_pi4_sim.ps1 -Case fixed_map -CaseEnv @{ MAP_FREE_THRESH = "0.15" }
```

## 再現方式と、その限界

Pi4 の aarch64 を QEMU で命令エミュレーションはしない。減速率が読めないうえ、
rclrs/DDS のアトミック周りで別の問題を持ち込む (Dockerfile 自体が
arm64-under-QEMU のビルド回避策を抱えている)。代わりに cgroup で絞る:

| 項目 | 実機 Pi4 4GB | ハーネス |
|---|---|---|
| CPU | Cortex-A72 ×4 @1.5GHz | `--cpuset-cpus 0-3` + `cpu.max` で合計スループットを絞る (既定 quota 6000 / period 10000 = 0.6 コア) |
| メモリ | 4GB, スワップ無し | `--memory 3g --memory-swap 3g` (OS + コンテナ外 ROS ノードの取り分 ~1GB を引いた値) |
| period | — | 既定 100ms ではなく 10ms。スロットリング1回あたりの停止を短くする |

測れないもの:

- **単一スレッドのレイテンシは実機より速い。** cgroup quota は合計スループット
  しか絞らないので、ディスカバリ・bond・コールバック遅延といった直列パスは
  楽観的に出る。逆に、多スレッドの VI ソルバとメモリ制約はよく再現できる。
- **コンテナ外 (実機ホスト側) の負荷は含まない。** raspicat ドライバ,
  robot_state_publisher (実測でコア 60%), livox, pointcloud_to_laserscan,
  restamp, filter chain。その分は quota を「Pi4 4 コア分」ではなく
  「nav2 が実際に取れた分」に絞ることで代用する。
- **emcl2 の alpha 崩壊は原理的に再現できない。** あれは「地図と実環境の
  不一致 (有効ビームの28%が地図の壁を貫通)」が原因で、同じ地図をレイキャスト
  する疑似 LiDAR では起きない。odom ドリフトと測距ノイズ、地図に無い障害物は
  入れてあるが、地図そのものの誤りは別問題。

CPU quota のキャリブレーションは、実機で取れている数少ない実測値
「navfn の planner ループが 20Hz 設定に対し実測 7.6Hz」を的にする:

```powershell
.\run_pi4_sim.ps1 -Case calib -CaseEnv @{ PLANNER="navfn"; PLANNER_EXPECTED_FREQ="20" }
# run_case.sh が nav.log から "current loop rate is X Hz" を拾って出す
```

このとき **地図の free_thresh は実機のまま (上書きしない)** こと。7.6Hz は
518k セルが free の地図で測った値で、しきい値を直すと navfn の問題規模が
1/4 になり、まったく違う quota に合わせてしまう。

## 実機に触らずに判明したこと (2026-07-25)

### 1. 地図の 74.66% は「未観測」だが、free として扱われている

`map_19f.pgm` の画素分布:

| 画素値 | 割合 | 意味 |
|---|---|---|
| 205 | 74.66% | map_saver の「未観測」 |
| 254 | 23.11% | 観測済み free |
| 0 | 1.73% | 障害物 |

`map_19f.yaml` の `free_thresh: 0.25` に対し 205 は p=(255-205)/255=**0.196** なので
`p < free_thresh` が成立し、**未観測セルが全部 free に化けている**
(ROS の既定は 0.196 で、この値なら未観測のまま)。結果:

- VI が解く free セルが 518,808 → 実際に観測済みなのは 124,645 (4.2倍の水増し)
- `unknown_as_obstacle: true` も `track_unknown_space: true` も効かない
  (未観測セルが存在しないことになるため)
- nav2 が未観測領域を通る経路を平気で引く

### 2. VI の状態数と、それを決めるパラメータの制約

`915x577 x theta_cell_num 60 = 31,677,300 状態`。`vi_reference` の `State` は
56 B/state なので **密配列だけで 1.65 GiB**。しかも:

- `theta_cell_num` は実行時に減らせない。`vi_global_planner` の `validate()` が
  `theta_cell_num != N_THETA` を弾く (`N_THETA` は `vi_core` のコンパイル時定数)。
  減らすには `vi_core` を作り直してイメージを再ビルドする必要がある。
  → **2026-08-04 に解消** (value_iteration3 の `ec2579d`)。`vi_core` の
  `params` ごと捨てたのでこの照合は無くなり、`theta_cell_num` も行動集合も launch から
  渡せる (360 を割り切ること)。
- `vi_global_planner` と `vi_local_planner` は**別プロセスで同じ全地図の価値関数を
  それぞれ解く**。つまり所要メモリは 2 倍。→ **2026-07-29 に解消**: 両アクションを
  1 ノード・1 価値関数で提供する `vi_planner` に統合し、`local_planner:=vi` では
  solve もメモリも 1 本になった (以下の 2 倍という記述は当時の 2 プロセス構成の話)。

実測 (手元 Ryzen 7 8840U, `VI_THREADS=4`, `bench_map --solver frontier2d_sparse`):

| 条件 | 収束時間 | ピーク RSS(*) |
|---|---|---|
| 実機と同じ (free_thresh 0.25) | 12.3 s | 3.79 GB |
| free_thresh 0.196 | 3.0 s | 3.44 GB |
| free_thresh 0.196 + 解像度 0.10m (scale 2) | 0.45 s | 867 MB |

(*) Windows の PeakWorkingSet64 で、Linux の RSS とは一致しない。密配列の
1.65 GiB が確実な下限で、ピークはその 2 倍前後。Linux 実測は `probe.py` の
`memory.current` サンプリングで取る。

**しきい値の修正は「時間」にしか効かない** (4倍速) — 状態配列は free 率に
関係なく全格子ぶん確保されるため。**メモリを削れるのは格子そのものを小さく
する方 (解像度 or theta) だけ**。

Pi4 は 4GB でスワップ無し。プランナ 1 プロセスのピークだけで載らず、そこへ
当時は vi_local_planner がもう 1 つ同じものを積んでいた (現在は `vi_planner` に
統合され 1 本)。`plan=0` はこれで十分説明がつく
(OOM kill かスラッシングかは実機の `dmesg` / `swapon --show` で確定させる。
実機で load 21.9 のままスタックが応答していたのはスラッシング寄りの挙動)。

### 3. `solver: frontier2d_sparse_compact` はこの用途では効かない → 2026-07-29 に解消

**2026-07-25 時点**: `bench_map` の compact 実行は 956 MB で収まるが、あれは
`solve_compact_mapped` (states を確保せずマップから直接解く mapped 経路) を
呼んでいる。ROS ノードが通るのは `solvers::solve` → `solve_compact` の
**slice 経路**で、こちらは `vi.states` (1.65 GiB) を確保したうえに RamSink を
足す。パラメータを変えるだけではメモリは減らない。

**2026-07-29 に実装**: `vi_global_planner` に mapped 経路を追加した
(value_iteration3 側の変更、下の「広域地図 (map_tsudanuma) 対応」節)。
`solver: frontier2d_sparse_compact` を選ぶと `solve_compact_mapped` を直接呼び、
`states` を一切確保しない。ロールアウトは確定出力 (sink) を方策ビューとして
読む (`vi_reference::planner::CompactPolicy`)。この時点では統合ノードの `vi_planner` が
compact 非対応 (追従ループが `states` に書き戻すため) だったので、広域地図では
`local_planner:=nav2` = `vi_global_planner` + `controller_server` に限られた。

**2026-08-01 に解消**: `vi_planner` も compact を扱う。追従はロボット近傍のパッチ
だけを sink から起こして回し、狭域 → 広域の伝播 (`global_sweep`) は sink のタイル修復
になる (2026-08-04)。広域地図は `local_planner:=vi` でも `nav2` でも通る。詳細は
[`config/README.md`](../../src/daifuku_stack/config/README.md) の
「`map_tsudanuma` で `planner:=vi` を使うときの制約」。

### 4. 地図を切り詰めても効かない

観測済みセルの外接矩形は 881x570 で、元の 915x577 の 95.1%。観測領域が
地図全体に散らばっているため、クロップでは状態数が 5% しか減らない。

なお start (-1.27,-0.63) と goal (4.28,-2.92) は**どちらも観測済み free で、
4近傍で連結している** (120,753 セルの同一成分)。つまり `free_thresh` を
直して未観測を障害物扱いにしても、このゴールは到達可能なまま。

## シムで再現したこと (2026-07-25)

### A. `planner:=vi` は綺麗なプロセステーブルからは bringup できない

コンテナを作り直した状態で `planner:=vi`（リポジトリ既定）を上げると、
bt_navigator が `on_configure` で 2 段階に落ちる:

1. `"compute_path_through_poses" action server not available after waiting for 1.00s`
   → `Error loading XML file: navigate_through_poses_w_replanning_and_recovery.xml`
2. (1 を回避すると) `"global_costmap/clear_entirely_global_costmap" service server
   not available` → `Error loading XML file:
   navigate_to_pose_w_replanning_and_recovery.xml`

どちらも **planner_server が提供するもの**で、`vi_global_planner` は
`compute_path_to_pose` しか置き換えない。nav2 1.1.20 (Humble) の bt_navigator は
navigate_to_pose / navigate_through_poses の両ナビゲータをハードコードで持ち、
Iron 以降の `navigators` パラメータが無いので後者を無効化できない。
`local_planner:=vi` では controller_server も無いので
`local_costmap/clear_entirely_local_costmap` も無い。

→ **実機で bringup が成功していたということは、planner_server (と恐らく
controller_server) を提供する何かが同時に生きていた**ことを意味する。
既知の「docker exec 残骸」か、あるいは実際には `planner:=navfn` で
起動していたかのどちらか。前者なら、生き残った planner_server も
`compute_path_to_pose` を提供するため vi_global_planner と衝突し、
どちらにゴールが行くかは不定になる。

回避策として `src/daifuku_stack/behavior_trees/navigate_to_pose_vi.xml` /
`nav_through_poses_stub.xml` を置いた (コストマップクリアを外し、リカバリは
behavior_server の Spin/Wait/BackUp のみ)。2026-07-29 に `navigation.launch.py` が
`planner:=vi` のとき自動でこの 2 本を選ぶようにしたので、手作業の差し替えは不要。

### B. `plan=0` は「プランナが失敗した証拠」ではなかった

`vi_global_planner` は **`/plan` を publish しない**。Path は action の Result で
返すだけで、`/plan` を出しているのは nav2 の planner_server。つまり
`planner:=vi` では `/plan` に publisher が存在せず、実機プローブの `plan=0` は
正常時でもそうなる (統合後の `vi_planner` も同じ)。実際に見るべきは
`cmd_vel_nav=0` と、VI の `value_function` の有無。

### C. 本命: vi_global_planner が OOM で SIGKILL される

Pi4 相当 (4 コア / quota 0.6 コア / メモリ 3GB・スワップ無し) でゴールを1回
投げた結果:

```
mem_mb: 448 -> 801 -> 1338 -> 1889 -> 2181 -> 3071 (= memory.max) -> 405
cgroup memory.events: max 2987, oom 6, oom_kill 1
[ERROR] vi_global_planner: process has died [exit code -9, ...]   # SIGKILL
```

カーネル側の記録:

```
Memory cgroup out of memory: Killed process (vi_global_plann)
  total-vm:7602460kB  anon-rss:2732376kB  file-rss:9936kB
```

**anon-RSS 2.73 GB / 仮想 7.6 GB。** プローブのサンプリングでも
`vi_global_planner` の RSS ピークは 2592 MB だった (他のノードは全部合わせても
200 MB 程度)。Pi4 の 4GB からカーネルと OS、コンテナ外の ROS ノードを引いた
残りには入らない。

`vi_global_planner: plan (-0.46, -0.94) -> (4.28, -2.92)` のログを出した直後に
価値関数の確保でメモリを食い尽くし、カーネルに殺されている。`use_respawn` は
False なので復活せず、以後 `compute_path_to_pose` のサーバが居なくなり、BT は
リカバリを回して最終的に ABORTED になる。これが実機の
`cmd_vel_nav=0 / cmd_vel=49 / ABORTED` と同じ形。

### D. CPU 飢餓は「ゴール受理 ack のタイムアウト」として現れる

OOM に至る前の実行では、BT の全アクションが即座に失敗していた:

```
[WARN] Timed out while waiting for action server to acknowledge goal request for compute_path_to_pose
[WARN] ... for spin / wait / backup
[WARN] BehaviorTreeEngine: Behavior Tree tick rate 100.00 was exceeded!
```

`bt_navigator` の `default_server_timeout` は **20 ms** (nav2 既定, 当時は
`config/nav2/bt_navigator.yaml` 相当にもその値が入っていた)。cgroup の統計で `nr_throttled=31904`,
`throttled_usec=546s` という状態では、rclrs のアクションサーバが 20 ms 以内に
ack を返せずゴールが即失敗する。`default_server_timeout: 500` に上げると
この段階は通過し、次の OOM まで進んだ。

## 対策の検証結果 (Pi4 相当: 4 コア / quota 0.6 コア / メモリ 3GB・スワップ無し)

同じスタート (-1.27,-0.63) からゴール (4.28,-2.92) へ 1 回ずつ。

| ケース | 地図 | 狭域 | `default_server_timeout` | 結果 | ピーク mem | vi_global RSS |
|---|---|---|---|---|---|---|
| baseline | 0.05m / 実機しきい値 | vi | 20 ms (既定) | ABORTED 1.0s (ack タイムアウト) | 503 MB | 62 MB (solve 前に失敗) |
| bt_timeout | 0.05m / 実機しきい値 | vi | 500 ms | **OOM kill** → TIMEOUT | 3011 MB (上限) | **2593 MB** |
| fullres_dwb | 0.05m / free_thresh 0.15 | nav2 | 500 ms | **OOM kill** → TIMEOUT | 3071 MB (上限) | (kill) |
| map10cm | 0.10m / free_thresh 0.15 | vi | 500 ms | ABORTED 60s (リカバリ全滅) | 1853 MB | 825 MB |
| map10cm_dwb_t20 | 0.10m / free_thresh 0.15 | nav2 | 20 ms (既定) | ABORTED 0.2s (ack タイムアウト) | 833 MB | — |
| **map10cm_dwb** | **0.10m / free_thresh 0.15** | **nav2** | **500 ms** | **SUCCEEDED 47.6s / リカバリ 0** | **1421 MB** | **831 MB** |
| calib_navfn (planner:=navfn) | 0.05m / 実機しきい値 | nav2 | 20 ms (既定) | ABORTED 0.5s (ack タイムアウト, `/plan` は 1 本出た) | 852 MB | — |
| **calib_navfn2 (planner:=navfn)** | **0.05m / 実機しきい値** | **nav2** | **500 ms** | **SUCCEEDED 24.2s / リカバリ 0, `/plan` 21 本** | **557 MB** | — |

**資源だけを外した対照** (cgroup 制限なし・16 コア・メモリ無制限。
`planner:=vi` / `local_planner:=nav2` / `free_thresh 0.15` / ack 20ms は
fullres_dwb と同じで、違いは制限の有無だけ):

| ケース | 地図 | 狭域 | `default_server_timeout` | 結果 | ピーク mem |
|---|---|---|---|---|---|
| nolimits_fullres | 0.05m / free_thresh 0.15 | nav2 | 20 ms (既定) | **SUCCEEDED 23.3s / リカバリ 0** | **3425 MB** |

fullres_dwb (Pi4 相当) は OOM で死に、同じ構成が制限なしなら 23 秒で走破する。
ピークは **3425 MB** で Pi4 相当の 3GB 上限を超えており、これが死因。
ack タイムアウト 20 ms も CPU が足りていれば通る。つまり **設定の問題ではなく
資源の問題**で、メモリと CPU が独立に効いている、というのがこの対照の主張。
「この構成なら問題ない」という主張ではない。

### CPU quota のキャリブレーション

calib_navfn2 で planner_server が出した実測値は **11.19 Hz** (navfn 1 回あたり
89 ms)。実機 Pi4 の同じ指標が **7.6 Hz** (131 ms) なので、**quota 6000 の
ハーネスは実機より約 1.5 倍速い**。実機に厳密に合わせるなら quota は
6000 × 7.6/11.19 ≒ **4000** 前後。したがって上の表は実機より甘い条件での
結果であり、実機ではこれ以上に厳しくなることはあっても緩くはならない。

読み取れること:

1. **地図の解像度を落とすのは必須。** `local_planner:=nav2` にして VI プロセスを
   1 つに減らしても、0.05 m のままでは `vi_global_planner` が OOM で殺される
   (fullres_dwb)。状態数は面積 × θ なので、0.10 m 化で 1/4 になるのが効く。
2. **`default_server_timeout: 20` ms は CPU 飢餓下で致命的。** 他が全部同じでも
   20 ms のままだとゴールは 0.2 秒で ABORTED になる (map10cm_dwb_t20)。
   これは「BT がアクションサーバのゴール受理 ack を待つ時間」で、
   `nr_throttled` が 10 万回級の状態では rclrs も nav2 も間に合わない。
3. **`local_planner:=vi` は現状リカバリが機能しない。** behavior_server は
   `local_costmap/costmap_raw` を購読するが、controller_server を起動しないと
   誰も publish しないため `Costmap is not available` で spin / backup が全部
   失敗する (map10cm)。VI 狭域を使うなら behavior_server にコストマップを
   与える仕組みが別途要る。
4. **素の nav2 (`planner:=navfn` + DWB) は、実機の地図のまま Pi4 相当で走破する**
   (calib_navfn2: 24.2 秒、ピーク 557 MB)。VI を使わないなら地図もしきい値も
   変えなくてよく、必要なのは ack タイムアウトの引き上げだけ。
5. 成功ケースの自己位置は真値と一致した (mcl (4.12,-2.76) / truth (4.13,-2.75))。
   走行しながら emcl2 が収束している。

### 実機の障害がこの連鎖で説明できるか

実機プローブの実測は `plan=0 / cmd_vel_nav=0 / cmd_vel=49 / ABORTED` だった。

- navfn なら planner_server が `/plan` を publish する。シムでは ack タイムアウトで
  0.5 秒で落ちたケースでも `/plan` は 1 本出ている (calib_navfn)。
  **実機の plan=0 は navfn 起動と整合しない。**
- `planner:=vi` なら `/plan` に publisher が居ないので plan=0 は常に成立する。
- cmd_vel=49 はリカバリ (spin/backup) が実際に動いた証拠で、behavior_server が
  コストマップを取れていた = controller_server が居た、ということになる。

よって実機は **`planner:=vi` + controller_server あり** (明示的な
`local_planner:=nav2` か、残骸の planner_server / controller_server) で動いていた
可能性が高い。ただしこれは状況証拠で、確定には実機で
`ros2 node list` / `pgrep -af planner_server` / `dmesg | grep -i oom` /
`swapon --show` を取る必要がある。

## 実機に反映するときの提案 (実機が復帰してから適用すること)

Pi には触れていないので、以下は**提案**であって適用済みではない。唯一
`config/nav2/bt_navigator.yaml` の `default_server_timeout` だけリポジトリに入れた
(下記)。

### `planner:=vi` を Pi4 で動かすのに必要な 5 点 (全部必要)

シムで走破した構成 (`map10cm_dwb` / `verify_repo`) は次の 5 点をすべて
満たしている。1 つでも欠けると上の表のどれかの失敗になる。

1. 地図 0.10 m/cell — 欠けると OOM。`maps/map_10cm.*` として同梱していたが
   削除したので、必要なら `map_19f` から作り直すこと（`free_thresh 0.15` 込み）:

   ```bash
   uv run --project simulator downsample-map \
       src/daifuku_stack/maps/map_19f.yaml /tmp/map_10cm.yaml \
       --scale 2 --free-thresh 0.15
   ```

   `run_case.sh` は `MAP_SCALE=2 MAP_FREE_THRESH=0.15` を渡せば実行時に
   同じものを生成するので、ハーネス経由なら手で作る必要はない。
2. その地図の `free_thresh: 0.15` — (1) に同梱。下の「注意」参照
3. `local_planner:=nav2` — 欠けるとリカバリが `Costmap is not available` で全滅
4. `bt_navigator.default_server_timeout: 500` — 欠けると 0.2 秒で ABORTED
5. **VI 用 BT XML 2 本** — 欠けると bt_navigator が on_configure で落ちて
   そもそも bringup できない:

```yaml
bt_navigator:
  ros__parameters:
    default_nav_to_pose_bt_xml: "<share>/daifuku_stack/behavior_trees/navigate_to_pose_vi.xml"
    default_nav_through_poses_bt_xml: "<share>/daifuku_stack/behavior_trees/nav_through_poses_stub.xml"
```

`verify_repo` が検証したのは「リポジトリに入れた `default_server_timeout: 500`
+ ハーネス側が注入した BT XML」であって、リポジトリだけの状態ではない。
BT XML はファイルを追加しただけで launch からは参照していない
(navfn 経路に影響させないため)。

| 対策 | 効果 | 副作用・注意 |
|---|---|---|
| `bt_navigator.default_server_timeout: 20 → 500` | CPU 飢餓時に全アクションが 0.2 秒で失敗するのを防ぐ。VI/navfn どちらでも効く | ゴール受理待ちが最大 0.5 秒。**これだけリポジトリに適用済み** |
| `local_planner:=nav2` | VI プロセスが 1 つになりメモリ半減。behavior_server のリカバリが機能する | 狭域が DWB に戻る |
| 地図を 0.10 m/cell に作り直す (`downsample_map.py --scale 2 --free-thresh 0.15`) | 状態数 1/4 = メモリ 1/4。VI の OOM が止まる | **`free_thresh 0.15` が同梱されている** (未観測 205 を unknown 扱いにしないと縮めた意味が薄い)。つまりこの地図を選ぶことは下の「注意」を受け入れること。以前は `maps/map_10cm.*` として同梱していたが削除した。`run_case.sh` に `MAP_SCALE=2 MAP_FREE_THRESH=0.15` を渡すか、上の 1. のコマンドで作り直す |
| VI 用 BT XML (`behavior_trees/navigate_to_pose_vi.xml` / `nav_through_poses_stub.xml`) | `planner:=vi` で bt_navigator が configure できるようになる | リカバリからコストマップクリアが消える。**`navigation.launch.py` が `planner:=vi` のとき自動で選ぶようにした** |
| `vi_global_planner` を `solve_compact_mapped` 経路に | `states` (56B/state) を確保しなくなる | **2026-07-29 に実装** (下の「広域地図対応」節)。ただし常駐ブロックは残る |
| VI をやめて `planner:=navfn` | 実機の地図のまま Pi4 相当で走破する (実測 24 秒 / 557 MB)。必要なのは timeout 変更だけ | VI 研究の目的から外れる |

### `free_thresh` を下げるときの注意

0.15 にすると **地図の 74.66% が「未観測」になり、`unknown_as_obstacle: true` の
VI では通行不能**、nav2 のコストマップでは NO_INFORMATION になる。今回
連結性を確認したのは実機プローブと同じ 1 組 (start (-1.27,-0.63) →
goal (4.28,-2.92)) だけで、これは観測済み領域内の 120,753 セル成分に両端が
入っていた。**それ以外のゴール、特に普段使っている目的地が未観測領域に
あると、全部到達不能になる。** 適用前に対象ゴールが 254 (観測済み) の
領域にあるか確認すること。

本筋の対処は地図の取り直しで、これは emcl2 の alpha 崩壊
(有効ビームの 28% が地図の壁を貫通) と同じ根に繋がっている。

### ダウンサンプルは通路を細らせる

`downsample_map.py` は 2x2 ブロックに障害物が 1 セルでもあれば障害物にする
(保守的プーリング)。0.10 m/cell では通路が片側最大 0.1 m 削られる計算で、
実測の footprint (420x450mm、外接円 0.408 m) / `inflation_radius: 0.55` と
組み合わせると狭い戸口が塞がりうる。この検証当時のコストマップは
`robot_radius: 0.22` (nav2 の既定値のまま) だったので、現在のほうが条件は厳しい。今回検証したのは実機プローブと同じ 1 経路だけ
(VI ロールアウトで 192 点・`reached_goal=true`、シムでも走破)。
**普段使う経路が縮小後も通れるかは、適用前に個別に確認すること。**

`free_thresh` を下げる場合、0.196 ちょうどではなく **0.15** にすること。
205 の p は 0.19607843… で、0.196 との比較は浮動小数の境界に乗っており、
実装によって free 側に転ぶ危険がある。

`theta_cell_num` を 60 から下げられればメモリは線形に落ちる。当時は `vi_core` の
`N_THETA` とのコンパイル時一致チェックがあってイメージの再ビルドが要ったが、
2026-08-04 の `ec2579d` でその照合は無くなり、いまは launch から渡せる
(360 を割り切ること)。

## 広域地図 (map_tsudanuma) 対応 (2026-07-29)

`maps/map_tsudanuma.yaml` は 5888x4000 @0.05m (294.4m x 200m)。占有 0.75% /
自由 31.1% / **未観測 68.2%**。VI の状態数は 5888*4000*60 = **14.1 億**で、
`State` 56B/state の密配列だけで 79GB。密ソルバ (`frontier2d_sparse`、ノードの
既定値) では起動と同時に死ぬ。リポジトリの config は 2026-08-04 に compact を
既定にしたので、いまは上書きなしでもここには落ちない。

### 実装したもの (value_iteration3 側)

| 変更 | 内容 |
|---|---|
| `vi_reference::bridge::downsample_occupancy` | 障害物優先の整数倍ダウンサンプル |
| `vi_reference::planner::PolicyView` | ロールアウトが読む最小ビューの抽象 (密/compact 共通) |
| `vi_reference::planner::CompactPolicy` | `CompactSink` を方策ビューとして読む実装 |
| `solve_compact_mapped(..., cancel)` | バンド内ラウンド境界で観測する中断フラグを追加 |
| `vi_global_planner` の compact 経路 | `solver: frontier2d_sparse_compact` で `solve_compact_mapped` を直接呼ぶ |
| `vi_global_planner/src/sink.rs` | 確定出力をディスク mmap に置く `MmapSink` |
| パラメータ `map_scale` / `compact_sink_dir` / `compact_ram_limit_mb` | 上記の制御 |

未検証: **進行中の compact solve を新しいゴールでプリエンプトする経路**。
`cancel` フラグの単体テスト (事前に立てた状態で `Cancelled` を返す) はあるが、
数十秒の solve の途中で立てるケースは通していない。BT は
ComputePathToPose を 1 本しか同時に持たないので、受け入れテストでも通らない。

`CompactPolicy` の終端判定は「`value == 0` がゴール圏」。`solve_compact_mapped` は
`states` を作らないので `final_state` フラグが無いが、sink の規約から一意に決まる
(津田沼 157,118,520 状態で実測確認: `value == 0` は 28 状態のみ・全て action=-1、
到達 41,879,880 状態のうち action<0 はその 28 だけ)。
密経路との経路一致は `vi_reference` と `vi_global_planner` のテストで固定した。

### 実測 (ローカル・16 コア・cgroup 制限なし)

start (53.07,-21.62,90°) → goal (44.08,-5.12,0°)、測地距離 25.5m。

| ケース | 結果 | 備考 |
|---|---|---|
| `planner:=navfn local_planner:=nav2` | **SUCCEEDED 61.9s** / リカバリ 0 | 地図は 0.05m のまま。cgroup ピーク 1.5GB |
| `planner:=vi local_planner:=nav2` + `overrides:=map_tsudanuma` | **SUCCEEDED 110.5s / リカバリ 0** | 初回 solve 41.6s (1670 iters、576 姿勢)。以降のリプランはキャッシュヒットで 0.00s。到達時の自己位置誤差 0.02m。別ランでは solve 25s・112.0s・リカバリ 1 |

**メモリ (これが Pi4 での本題)**: `vi_global_planner` のピーク RSS 3.98GB の内訳は
**anon 2.16GB + file 1.81GB**。file 側は sink の mmap (逼迫時に回収できる
ページキャッシュ) なので、ディスク退避は効いている。残る anon 2.16GB は compact の
**ブロックストア (値バンドの常駐分)** で、`resident_blocks_peak=126/168`・
`freed_blocks=0` — つまりこの地図では退避がほとんど発動しない (到達領域が地図全体に
広がるので、行ブロックが「自分±halo まで全 final」になるのが遅い)。
`--compact-band` を 10s/2s と絞っても `resident_cols_peak` は 66k→53k→32k と減るが
`resident_blocks_peak` は 126/168 のまま、`freed_blocks` は 0 のまま。
**バンド幅では下がらない。**下げるなら `map_scale` を上げる (常駐は概ね
`nx × 常駐行数 × nθ` に比例) しかない。

したがって **Pi4 4GB でこの設定が通るとは言えない** (anon 2.16GB + 他ノード)。
ローカル無制限では通る、というのがここで確認できた範囲。

再現コマンド (コンテナ内):

```bash
CASE=tsuda_vi MAP_NAME=map_tsudanuma PLANNER=vi LOCAL_PLANNER=nav2 \
OVERRIDES=map_tsudanuma \
START_X=53.07 START_Y=-21.62 START_YAW_DEG=90 \
GOAL_X=44.08 GOAL_Y=-5.12 GOAL_YAW_DEG=0 SETTLE=120 TIMEOUT=900 \
bash /opt/sim/run_case.sh
```

`bench_map` 単体 (scale 3 = 0.15m/cell, 1963x1334x60 = 157,118,520 状態):

| safety_penalty | 収束 | 時間 | sink |
|---|---|---|---|
| 30 s/cell (既定) | Y (1754 iters) | 27.8s | 1.89GB (ディスク mmap) |
| 1 s/cell | Y (1670 iters) | 17.0s | 同上 |

### 罠 1: ペナルティが大きすぎると貪欲ロールアウトが固着する

`safety_radius_penalty` は **秒/セル** (`State::from_occupancy` が
`margin_penalty * PROB_BASE` を足す)。既定の 30 は「1 手 = 1 秒」に対して 30 倍。
津田沼は通路が細く 0.15m セルではほぼ全ての自由セルが `safety_radius` 0.2m 以内に
入るため、価値関数が移動時間ではなくペナルティ積算で決まる。VI の遷移は
サブセルサンプリング付きの確率モデルなので、隣接状態でペナルティの重みが変わり、
**価値が局所的に ±3 秒ゆらぐ**。1 手の進捗 1 秒より揺らぎが大きいと、ノイズ無しの
決定論ロールアウトは降下できない。

実測 (penalty 30): 82 手まで V 593→528 と順調に降りたあと、セル (997,620) で
θ=186° (V=528.50, 方策=left) と θ=210° (V=530.94, 方策=right) の 2-cycle に入り
`LoopDetected`。**そのポーズからのノイズ無し 1 手先は 6 アクション全てが V を
増やす** (529.67〜530.94) ので、単純な価値降下フォールバックでも抜けられない。
penalty を 1 にすると同じ地図・同じゴールで V 120→0 と単調に降り、104 姿勢で到達。

実機の閉ループ (本家 `ViNode::decision`) では運動ノイズが厳密な 2-cycle を壊すので
顕在化しにくい。開ループで経路を作る `rollout_path` 特有の失敗。
`vi_global_planner` は `LoopDetected` のときこの原因をヒントとしてログに出す。

揺らぎはペナルティにほぼ比例するので、10 秒未満なら 1 手 1 秒の進捗を下回るはず。
壁に寄りすぎるようなら 5 程度まで上げてから他をいじること (penalty 1 の実測は
リカバリ 0 なので、この経路では寄りすぎていない)。

### 罠 2: パラメータ上書きは SetParametersFromFile では効かない

launch_ros は global params (SetParameter / SetParametersFromFile) を先に、
ノード個別の `parameters=[...]` を後に渡す。ROS は後勝ちなので、
**`params_file` に既にあるキーは上書きできない**。`bond_timeout` が効くのは
`config/nav2/*.yaml` のどこにも無いキーだからで、`solver` や `map_scale` は効かない
(実測: overlay を書いても `map_scale=1` のまま起動した)。
`navigation.launch.py` は `daifuku_stack_launch/params.py` の `compose` で YAML の
段階でマージし、`params_file` 自体を作っている (`overrides:=` / `extra_params_file:=`
の両方がこの経路)。BT XML の 2 キーは `config/nav2/*.yaml` に無いので `SetParameter` で
足りる (`planner:=vi` の bringup はこれで通る)。

### 罠 3: `bt_navigator` の `wait_for_service_timeout` (既定 1000ms)

`planner:=vi` では `vi_global_planner` が `/map` を受け取ってから
`compute_path_to_pose` を作る。23.5MB の地図では bt_navigator の on_configure に
間に合わず、`"compute_path_to_pose" action server not available after waiting for
1.00s` → `Error loading XML file` → `Failed to bring up all requested nodes` で
bringup が全滅した (BT の差し替え自体は成功していたのに、その次で落ちる)。
`config/nav2/bt_navigator.yaml` で 60000ms にした。起動時にしか効かない待ち。

### 罠 4: `/initialpose` の 1 発だけでは取りこぼす

emcl2 は地図とスキャンが揃うまで MCL を初期化せず、`/initialpose` の購読は
volatile。23.5MB の地図受信が遅い津田沼では初回の 1 発が捨てられ、emcl2 が
初期姿勢 (0,0) のまま走り続けた (自己位置 (-0.43,-1.03) / 真値 (53.36,-21.60)、
当然プランは失敗)。`fake_robot.py` は自己位置が近傍に来るまで再送するようにした。

## 津田沼を Pi4 相当で走らせた (2026-07-31)

2026-07-29 に「Pi4 4GB でこの設定が通るとは言えない」と書いたまま残していた点を、
実際に cgroup を絞って確かめた。条件は他のケースと同じ (4 コア可視 /
quota 6000・period 10000 = 合計 0.6 コア / メモリ 3GB・スワップ無し)。
start (53.07,-21.62,90°) → goal (44.08,-5.12,0°)、`planner:=vi local_planner:=nav2`
`overrides:=map_tsudanuma` (map_scale 3 / compact / sink はコンテナの overlayfs
= 実ディスク。`findmnt -no FSTYPE /tmp` が `overlayfs` であることは確認済み)。

**結論: 解けるし、走破もする。ただし初回 solve に 45 分かかり、メモリは 3GB の
上限に貼り付いたまま。同じ地図・同じゴールを navfn なら 111 秒・1.3GB で走る。**

計 5 走行:

| # | 構成 | 制限 | 結果 | ピーク mem |
|---|---|---|---|---|
| 1 | vi + nav2 | Pi4 相当 | solve 3243.92s。**`SUCCEEDED` は偽陽性**で不動 (TF 凍結) | 3072 MB (上限) |
| 2 | navfn + nav2 | 制限なし | **SUCCEEDED 61.6 s** / リカバリ 0 | 1288 MB |
| 3 | navfn + nav2 | Pi4 相当 | **SUCCEEDED 111.5 s** / リカバリ 0 / 到達誤差 0.22 m | 1311 MB |
| 4 | vi + nav2 | Pi4 相当 | 再走行。**TF 凍結は再現せず** (確認後 goal 側で打ち切り) | — |
| 5 | vi + nav2 | Pi4 相当 | **SUCCEEDED 2794.1 s (46.6 分)** / リカバリ 0 / 到達誤差 0.24 m | 3072 MB (上限) |

**1 と 5 は同じ構成で、1 の失敗は再現しない。** したがって 1 の不動は
フレークとして扱うこと (経緯は 2〜3 節)。**Pi4 相当で津田沼を VI で走破できる**
というのが 5 の主張で、この節の結論はそちら。

### 1. 解ける。初回 solve は 45〜54 分。メモリは上限に貼り付いたまま生き残る

| 指標 | 走行 1 | 走行 5 (走破した方) |
|---|---|---|
| 初回 VI solve | **3243.92 s (54 分)** / 1844 iters / 576 姿勢 | **2706.05 s (45 分)** / 1844 iters / 576 姿勢 |
| リプラン | 0.00 s (`solved_now=false`) | 0.00〜0.06 s。**姿勢数が 576→366 と単調に減る** = 実際に前進している |
| OOM kill | **無し** (`oom_kill 0`) | **無し** (`oom_kill 0`) |
| `memory.events max` | 19316 | 17759 |
| cgroup ピーク | **3072 MB = 上限ちょうど** | **3072 MB = 上限ちょうど** |
| `vi_global_planner` ピーク RSS | 2461 MB (**anon 2148 MB** + file 1815 MB) | 2485 MB (**anon 2148 MB** + file 1815 MB) |
| 回収の激しさ | `pgmajfault 147` / `workingset_refault_file 11951` (≒47 MB) | — |
| sink | 1.89 GB (ディスク mmap) | 同左 |
| CPU スロットル | `nr_throttled 314510` | `nr_throttled 274088` |

`anon 2148.4 MB` は 2 走行で**完全に一致**した (再現性がある数字)。
ピーク RSS が anon+file の合計にならないのは、両者のピーク時刻が違うため。

無制限ローカル (16 コア) の 41.6 s に対して **78 倍**。
`nr_throttled` が示すとおり CPU は常時絞られており、これは quota どおりの結果。

メモリについては 2026-07-29 の観測と整合する: **anon は 2148 MB まで伸びる**
(当時の 2.16 GB とほぼ同じ)。3GB に収まったのは sink の 1.8 GB が
**回収可能なページキャッシュ**だったからで、`memory.events max` が 19316 回
立っているのは「上限に達して回収が走った」回数。つまり余裕があって通ったのではなく、
**上限に貼り付きながら回収でしのいで通った**。`pgmajfault` が 147 と小さいので
スラッシングには至っていない (ディスクに逃がした設計が効いている)。

実機 Pi4 では、ここからさらにカーネルとコンテナ外の ROS ノード
(raspicat ドライバ / robot_state_publisher / livox / pointcloud_to_laserscan /
restamp / filter chain) の取り分が引かれる。**anon 2.15 GB は回収できない**ので、
この余白は実機には無い。加えて quota 6000 は既存のキャリブレーション
(navfn 11.19Hz vs 実機 7.6Hz) で**実機より約 1.5 倍速い**から、実機の solve は
**80 分以上**と見るのが妥当。運用に耐える数字ではない。

### 2. 走行 1 の SUCCEEDED は偽陽性だった (再現しない)

**この節は走行 1 だけの話で、走行 5 では起きていない。**それでも残すのは、
`PROBE_SUMMARY` の `SUCCEEDED` を無条件に信じてはいけない実例だから。

走行 1 の `PROBE_SUMMARY` は `"result": "SUCCEEDED"`, `elapsed_s 3246.8`,
`number_of_recoveries 0`, `distance_remaining 0.0` を返した。**が、走っていない。**

**`result` は走破の判定に使えない。**見るべきは次の 2 つで、走破した走行 5 と
並べると差は明らかになる:

| 指標 | 走行 1 (偽陽性) | 走行 5 (本物) |
|---|---|---|
| `cmd_vel_nav` | **2 本** (`cmd_vel` 12 本) | **877 本** (`cmd_vel` 1719 本) |
| `ground_truth` | **(53.07, -21.62)** = スタートのまま | **(44.32, -5.11)** ≒ goal (44.08, -5.12) |

ゴールは測地 25.5 m 先なので、走行 1 はロボットが 1 mm も動いていない。

ログの該当箇所:

```
[bt_navigator]: Begin navigating from current location (0.00, 0.00) to (44.08, -5.12)
...
[controller_server]: Received a goal, begin computing control effort.
[controller_server]: Unable to transform robot pose into global plan's frame
[controller_server]: Reached the goal!          <- パス受領の 0.2 秒後
[bt_navigator]: Goal succeeded
```

`Begin navigating from current location (0.00, 0.00)` が出ている時点で
bt_navigator は自己位置を取れていない。goal_checker は変換できないまま
「到達」と判定し、BT は成功を返した。**VI の経路は正しかった** —
`vi_global_planner: plan (53.07, -21.62) -> (44.08, -5.12)` と自前の pose topic から
正しい start で解いており、576 姿勢の経路を返している。壊れているのは TF 側。

### 3. 走行 1 の原因: emcl2 の map→odom TF が固まる (solve より前に起きている)

19853 本の Extrapolation Error は**すべて同じ要求時刻** `1785494439.480997` を指す。
`tf_help` は `Transform time: 1785494439s 480996809ns` と出しており、これは
**map→odom の TF が その時刻で凍結した**ことを意味する。

重要なのは**時刻**で、凍結は **1785494439.48 = ゴール送信 (…494.89) の 55 秒前**、
つまり **VI が解き始める前**に起きている。最初の Extrapolation Error は
ゴール送信のわずか 0.16 秒後に出ている。**54 分の solve が原因ではない。**

`src/emcl2_ros2/src/emcl2_node.cpp` を読むと機構が分かる:

- `publishOdomFrame()` は map→odom を **`scan_time_stamp_`** (最後に受けたスキャンの
  スタンプ) で打つ。
- その中の `tf_->transform(...)` が投げると、`catch` は **`RCLCPP_DEBUG` を出して
  黙って return する**。既定のログレベルでは**何も表示されない**。
- `loop()` は `odom_freq` (20Hz) のタイマで回り続け、キャッシュ済みスキャンで
  `ALPHA` を出し続ける。**ノードは生きているように見える。**

したがって **/scan が emcl2 に届かなくなると `scan_time_stamp_` が凍り、
やがて TF バッファ (10 s) から外れて `transform` が投げ始め、以降 map→odom は
永久に更新されない**。`ALPHA: 1.000000` が出続けるのは、止まったロボットが
自分の古いスキャンと完全一致するため。**移動量ゲートの類は存在しない**ので、
`SETTLE` を延ばしたこと自体が原因ではない (ソースを読んで確認した)。

odom→base_footprint 側は無実: `fake_robot.py` の `step()` は odom TF と
`/sim/ground_truth` を同じ呼び出しで出しており、その ground_truth が
3246 秒で 77704 本 (≒24Hz) 届いているので、odom TF は最後まで健全だった。

なお 2026-07-29 の実機初走行で ABORTED になった際も
「emcl2 の map→odom 遅延」を疑っている。**同じ形がシムでも出た**ことになる。

**ただし同条件の再走行 (走行 4・5) では凍結しなかった** — `Begin navigating from
current location (53.07, -21.62)` と正しい自己位置が出て、Extrapolation Error は 0 本。
つまりこれは **0.6 コアで VI を回すことの決定的な帰結ではなく、間欠的に踏む不具合**。
ただし踏むと `SUCCEEDED` を返して黙って死ぬので、たちは悪い。
`publishOdomFrame()` の `catch` が `RCLCPP_DEBUG` なのが発見を遅らせる原因なので、
ここを `RCLCPP_WARN_THROTTLE` に上げるのが妥当な対処 (未実施)。

### 4. 対照: 資源制限を外すと同じ地図・同じゴールで普通に走る

TF 凍結が「Pi4 相当の資源」のせいなのか、ハーネスや環境の問題なのかを
切り分けるため、**cgroup 制限だけを外した** navfn 対照を取った
(`-Container pi4sim_full -NoLimits -DomainId 92`, 16 コア・メモリ無制限)。

| | navfn 制限なし (走行 2) | navfn Pi4 相当 (走行 3) | vi Pi4 相当・偽陽性 (走行 1) |
|---|---|---|---|
| `Begin navigating from current location` | **(53.07, -21.62)** | **(53.07, -21.62)** | **(0.00, 0.00)** |
| Extrapolation Error | 0 本 | 0 本 | 19853 本 |
| 結果 | **SUCCEEDED 61.6 s** / `/plan` 50 本 / `cmd_vel_nav` 614 本 | **SUCCEEDED 111.5 s** / `/plan` 46 本 / `cmd_vel_nav` 1038 本 | 不動 |
| 到達位置 | truth (44.30, -5.00) | truth (44.30, -5.00) | スタートから動かず |
| リカバリ | 0 | 0 | 0 (回るところまで行っていない) |
| ピーク mem | 1288 MB | 1311 MB | 3072 MB (上限) |

**navfn は制限の有無にかかわらず走破する** (61.6 s → 111.5 s、約 1.8 倍に鈍るだけ)。
資源制限そのものが TF を壊すわけではない、というのがこの対照の主張。
走行 1 の (0.00, 0.00) は走行 4/5 で再現しなかったので、資源の帰結ではなく間欠不具合。

### 5. 途中で踏んだハーネス側の問題 (結果の解釈に必要)

Pi4 相当の navfn 対照は 2 回失敗しており、どちらも**ハーネス由来**で
「津田沼が Pi4 で走れない証拠」ではない。混同しないこと。

| 症状 | 実際の原因 |
|---|---|
| `NO_ACTION_SERVER`。`lifecycle_manager` が `map_server/get_state` を待ち続ける | 前ケース (VI) を殺した残骸。`/dev/shm` に `fastrtps_*` が 69 個残っており、新しい参加者の探索が通らない。**コンテナを作り直す (`-Recreate`) と 40 秒で active になった**。なお同時に居た PID 123/129 は**ゾンビ**で、これは無害 (コンテナの PID 1 が `sleep infinity` で刈り取らないだけ) |
| `GOAL_REJECTED`。`Invalid frame ID "map" ... frame does not exist` | `fake_robot.py` の `/initialpose` 再送上限 (既定 8 回 x 5 秒 = 40 秒) を撃ち切った。低 CPU + 広域地図では emcl2 が最初のループを回すまで 100 秒以上かかる (実測: 最後の再送から 67 秒後にようやく最初の `ALPHA`)。受理されないまま諦めるので **map フレームが一度も生えない** |

後者のために `run_case.sh` に `INITIALPOSE_MAX_TRIES` / `INITIALPOSE_DELAY` を
足した (`fake_robot.py` 側は元からパラメータを持っていた)。

### この節の読み方

- **メモリの問い (2026-07-29 の宿題) には答えが出た**: 3GB で OOM はしない。
  ただし上限ちょうどに貼り付き、回収 (`memory.events max` 約 1.8 万回) でしのいでいる。
  **anon だけで 2148 MB** は 2 走行で一致した再現性のある数字で、ここは回収できない。
  実機 Pi4 では、ここからカーネルとコンテナ外ノードの取り分が引かれる。**余白は無い。**
- **走破はする**: Pi4 相当で 2794 秒 (46.6 分)、リカバリ 0、到達誤差 0.24 m。
  内訳はほぼ初回 solve (45 分) で、以降のリプランはキャッシュヒットで 0.0x 秒。
- **速度が実用上の失格点**: quota 6000 は既存キャリブレーションで実機より約 1.5 倍速い
  ので、**実機の初回 solve は 70 分前後**と見るべき。ゴール 1 つにこれは使えない。
- **地図のせいではない**: 同じ地図・同じゴールを navfn + DWB は Pi4 相当で
  **111.5 秒 / 1311 MB** で走破する (走行 3)。津田沼が Pi4 に載らないのではなく、
  **この地図規模の VI が載らない**。
- 詰めるなら `map_scale` (常駐は概ね `nx × 常駐行数 × nθ` に比例)。
  `--compact-band` が効かないという 2026-07-29 の観測は**無制限下 (`freed_blocks=0`)
  のもの**で、上限に貼り付いて回収が約 1.8 万回走る今回の条件では測り直していない。
  効かないと決めつけずに、必要なら再測すること。

## ハーネスを触るときの落とし穴 (実際に踏んだもの)

- **`pgrep -f` / `pkill -f` の自己マッチ。** `podman exec bash -lc "pgrep -f
  'probe.py --goal-x'"` はその exec 自身のコマンドラインにマッチするので
  永久に非空を返す。待ちループが 20 分空回りした。`pgrep -f '[p]robe.py'` の
  ブラケット記法を使う。
- **前ケースの残骸がグラフを汚す。** `laser_filters` の
  `scan_to_scan_filter_chain` は名前が `nav2_` で始まらないため素朴な pkill を
  すり抜けて溜まり、bt_navigator が前回の古いゴールを掴んで
  「85 秒前のスタンプで Extrapolation Error」を出した (プローブ側のバグだと
  思って一度追いかけた)。`run_case.sh` の `cleanup_ros()` が
  `/opt/ros/humble/lib/` と `/opt/ros_ws/install/lib/` のパスで一括して落とす。
- **`set -u` は ROS の setup.bash と両立しない** (`AMENT_TRACE_SETUP_FILES:
  unbound variable`)。
- **podman の既定接続は Hyper-V マシン (4GB 固定・拡張に管理者権限が要る) で、
  Windows パスを bind mount できない** (`/mnt/c` が無く statfs が I/O error)。
  20GB 取れる WSL マシン (`-c podman-machine-default-root`) を使い、
  ファイルは `podman cp` で流し込む。
- **podman build は `--format docker` が要る。** OCI フォーマットでは
  Dockerfile の `SHELL ["/bin/bash", ...]` が無視され、`RUN source ...` が
  `/bin/sh: source: not found` で落ちる。
