# 設定ファイルをリポジトリルートの `config/` へ集める

**この文書は移行が終わったら消す。** 手順書であって、仕様ではない。残る説明は
`config/README.md` と `CLAUDE.md` に書く。

## なぜ

いま設定は 3 パッケージに散っている（`daifuku_bringup/config/`、
`daifuku_stack/config/`、`daifuku_config_manager/config/overrides/`）。値を 1 つ直す
たびに「これは機体側か自律側か」の判断から始まり、断片とそれを上書きする overrides が
別の木にあるので並べて読めない。

ファイルの数（20）は減らない。減るのは**置き場の判断**と、断片と overrides を
突き合わせるときの往復。

## 移す先

`src/` と並ぶ `config/` に置く。**設定は 6 つの自前パッケージのどれのものでもない**
（1 地図 = 1 ファイルの overrides が機体側と自律側にまたがるのと同じ理由が、断片にも
そのまま効く）ので、`src/` の下のどこに置いても嘘が残る。

```
config/                        ← 新しい ament_cmake パッケージ daifuku_config
  package.xml  CMakeLists.txt
  README.md                    ← src/daifuku_stack/config/README.md
  site                         ← src/daifuku_config_manager/config/site
  overrides/*.yaml             ← src/daifuku_config_manager/config/overrides/
  bringup/robot/*.yaml         ← src/daifuku_bringup/config/robot/
  bringup/sensors/*            ← src/daifuku_bringup/config/sensors/（MID360_config.json 含む）
  stack/lifecycle_bond.yaml    ← src/daifuku_stack/config/
  stack/localization/emcl2.yaml
  stack/mapping/slam_toolbox.yaml
  stack/nav2/*.yaml
```

`daifuku_config_manager` には**コードだけが残る**（`params.py` と、ノード 2 つ =
`site_manager` / `config_sentinel`）。役割の説明はむしろ今の名前どおりになる。

`bringup/` と `stack/` の段は**残す**。これが `config_root`（= `config_sentinel` が
指紋を取る範囲、`params._reject_unknown_nodes` がノード名を探す範囲）の単位なので、
潰すと粒度が変わる。この段さえ残れば挙動は今と 1 ビットも変わらない。

`stack/nav2/` も畳まない。`navigation.launch.py` はそのディレクトリの yaml を
**全部**ファイル名順に束ねるので、`emcl2.yaml` や `lifecycle_bond.yaml` が同じ階層に
来ると同じノード名の重複で起動時に落ちる。

overrides の 1 段目（`daifuku_bringup:` / `daifuku_stack:`）は変えない。`params.compose`
は `package` と `config_root` を**別々の引数で**受けるので、置き場が動いても部分木の
選び方は無関係。

### なぜ新しいパッケージにするのか

launch が設定を見つける手段は `get_package_share_directory` しかない。ルートの
`config/` を素のディレクトリのまま `daifuku_config_manager/setup.py` の `data_files`
から `../../config/...` で拾うこともできるが、パッケージの外へ相対パスで手を伸ばす形は
`--symlink-install` の挙動が読みにくい。`package.xml` と 5 行の `CMakeLists.txt` を
足して普通のパッケージにするほうが、仕組みとしては退屈で確実。

```cmake
cmake_minimum_required(VERSION 3.8)
project(daifuku_config)
find_package(ament_cmake REQUIRED)
install(DIRECTORY bringup stack overrides DESTINATION share/${PROJECT_NAME})
install(FILES site README.md DESTINATION share/${PROJECT_NAME})
ament_package()
```

lint は他の `ament_cmake` パッケージと同じものを名指しする（copyright / lint_cmake /
xmllint。`.py` も `.cpp` も持たないので copyright は見る対象が無いが、揃えておく）。

## 変えるコード

### `params.py` — パスを組むのを 1 か所にする

いまは launch 6 ファイルが `os.path.join(pkg_share, "config")` を各自で組み立て、
`site_manager.py:89` も同じ形を持っている。**ここを `params` に寄せる。**

```python
CONFIG_DIRS = {"daifuku_bringup": "bringup", "daifuku_stack": "stack"}
KNOWN_PACKAGES = tuple(CONFIG_DIRS)   # overrides の 1 段目に書ける名前

def config_root(package):
    """このパッケージの設定の根。**パスを組むのはここだけ。**"""
    return os.path.join(get_package_share_directory("daifuku_config"), CONFIG_DIRS[package])
```

これで「`config_root` に親の `config/` を渡すと、`_reject_unknown_nodes` が両パッケージ
分のノード名を認めてしまう（`daifuku_bringup:` の下に nav2 のノード名を書いても通る）」
という穴が、そもそも作れなくなる。呼び元が組み立てられないため。

同じファイルの 87 行目（`config/site`）と 172 行目（`config/overrides`）の
`get_package_share_directory("daifuku_config_manager")` も `daifuku_config` に替える。
700 行目の `package="daifuku_config_manager"` は `config_sentinel` の**実行ファイルの
出どころ**なので**そのまま**。

### launch 6 ファイル 13 行

`config_root` を組んでいた行は `params.config_root("daifuku_bringup")` に置き換え。
残りは個別のファイル指定。

| ファイル | 行 | いま |
| --- | --- | --- |
| `daifuku_bringup/launch/daifuku_bringup_launch/lidar.py` | 46 | `config/sensors` |
| `daifuku_bringup/launch/lidar_bringup.launch.py` | 58, 59 | `config/sensors`, `config` |
| `daifuku_bringup/launch/odom_fusion.launch.py` | 70, 71 | 同上 |
| `daifuku_bringup/launch/robot_bringup.launch.py` | 153, 158, 354 | `config/robot/<name>`, `config` ×2 |
| `daifuku_stack/launch/mapping.launch.py` | 46, 47 | `config`, `config/mapping/slam_toolbox.yaml` |
| `daifuku_stack/launch/navigation.launch.py` | 85, 86, 90, 91 | `config/nav2`, `config`, `config/lifecycle_bond.yaml`, `config/localization/emcl2.yaml` |

`package.xml` の `<exec_depend>daifuku_config_manager</exec_depend>` の隣に
`<exec_depend>daifuku_config</exec_depend>` を足す（share の解決は実行時なので
exec_depend で正しい）。

### `CMakeLists.txt`（`daifuku_bringup` / `daifuku_stack`）

`install(DIRECTORY config ...)` から `config` を**落とす**。これは同じコミットで必ず
やること。残すと**新旧どちらの share にも設定が実在する**状態になり、
`params._config_files` は古いほうを生きたファイルとして読む（リンク切れではないので
飛ばされない）。指紋に古い複製が入り続けるので、2026-08-07 のリンク切れより悪い。

### `daifuku_config_manager/setup.py`

`data_files` から `config/site` と `config/overrides` の 2 エントリを消す。あとは
コードだけのパッケージになる。

## 変えるビルドとマウント

**Pi の compose は `src/` しかマウントしていない**（`compose.common.yaml` の
`../../src:/opt/ros_ws/src`）。ルートに置く以上、ここが唯一の実作業。

- `compose.common.yaml` の `workspace-build` / `ros2` / `raspicat` の 3 サービスに
  `- ../../config:/opt/ros_ws/config` を足す。**`:ro` にしないこと** —
  `site_manager` が `config/site` を書く。
- 開発コンテナはリポジトリ全体（`../..`）を、ネイティブはリポジトリルートを
  ワークスペースにしているので、**どちらも変更不要**。colcon はベースパスの下を
  再帰で探すので `config/package.xml` は勝手に見つかる。
- `--packages-select` の 3 か所に `daifuku_config` を足す。**Pi 側と開発側の両方に
  要る**（`daifuku_config_manager` と同じ扱い。実機はこれが無いと機体が上がらない）。
  `docker/raspberrypi/scripts/build-workspace.sh`、
  `docker/dev/tools/build-workspace.sh`、`tools/setup/setup_native_base.sh`。

## 変えるドキュメントとヘルプ文字列

diff の大半はこちら。3 種類に分けて、扱いも分ける。

1. **`--show-args` に出るヘルプ文字列**（挙動に近い。取りこぼすと嘘の案内になる）
   `robot_bringup.launch.py` の 433 / 448 / 482、`navigation.launch.py` の 525。
   `lidar.py` の 95 / 135 が指す `config/urg_*.param.yaml` は**上流
   `raspicat_bringup` のもの**なので触らない。
2. **コード中のコメント・docstring**
   `robot_bringup.launch.py:144`、`nav2_params.py` の 17 / 39 / 61-62 / 100、
   `daifuku_stack_launch/__init__.py:30`、`navigation.launch.py:206` / 617、
   `elevation_filter.py:63`、`control_panel.py:76`、`simulator/` の
   `fake_robot.py` / `nav_container.sh` / `run_case.sh` / `isaac_raspicat.py` /
   `map_to_usd.py`。
3. **`docs/` と各 README**（19 ファイル）。`src/daifuku_stack/config/README.md` が
   `config/README.md` へ動くので、そこへのリンクも全部。

`CLAUDE.md` は**同じコミットで**直す。放置すると指示そのものが嘘になる。効くのは
少なくとも次の 5 か所。

- 「リポジトリの範囲」— 自前は 6 つ → **7 つ**。表に `daifuku_config` の行を足し、
  `daifuku_config_manager` の行から設定の実体を外す
- 「触る前に読むもの」の表（`src/daifuku_stack/config/README.md` を指している行）
- 「設定ファイル (`src/daifuku_{bringup,stack}/config/**/*.yaml` と …) のコメント」の
  見出し — パスがそのまま文字列で入っている
- 「ビルド」の表の「何もしないでよいもの」の並び（`setup.py` の `glob` の話は
  overrides が CMake 側へ移るので消える。代わりに**ファイルを新しく足したら 1 度
  ビルド**の一般則だけが残る）
- パッケージ一覧が 3 か所にある、という段落（`daifuku_config` も並ぶ）

## 手順

1. **コミット 1**: `git mv` ＋ `config/{package.xml,CMakeLists.txt}` ＋ `params.py` の
   `config_root()` ＋ launch 13 行 ＋ `package.xml` ×2 ＋ `CMakeLists.txt` ×2 ＋
   `setup.py` ＋ compose の 3 マウント ＋ ビルド一覧 3 か所 ＋ ヘルプ文字列。
   **分けない** — 途中のコミットは起動しないので、二分探索で踏むと原因を取り違える。
2. **コミット 2**: コメント・`docs/`・README・`CLAUDE.md`。
3. **コミット 3**: この文書を消す。

## 検証（Windows ホストでは走らない。dev コンテナか実機で）

**先に `rm -rf install build`。** `git mv` で移すと古い symlink の指す先がソース側から
消えるので、リンク切れとしては検出できる（`_config_files` が飛ばす）が、飛ばしたものは
**指紋の対象からも外れる**ので設定が 1 つ消えたことに気付けない。順序も保証できない
ので、掃除ではなく作り直す。

```bash
rm -rf install build && docker compose up -d
ros2 launch daifuku_stack navigation.launch.py --show-args   # params_dir などの既定が新しい場所か
ros2 launch daifuku_bringup robot_bringup.launch.py          # 上がるか、config_sentinel が指紋を取れるか
find install -xtype l                                        # 空であること
tools/site.sh map_tsudanuma && tools/site.sh map_19f         # site の読み書きが通るか
colcon test --packages-select daifuku_bringup daifuku_stack daifuku_config_manager daifuku_config
```

検査が生きていることも見る。`config/overrides/map_19f.yaml` の `daifuku_bringup:` の下に
でたらめなノード名を 1 行足して、**起動時に落ちる**こと（移行前と同じメッセージで）。

## やらないこと

- 設定を ROS トピックで配る（前段の議論のとおり。ファイルシステムを共有しない相手が
  出てきたら、そのとき）
- `bringup/` `stack/` を潰して 1 つにする（指紋の粒度が変わる）
- ファイルを統合して数を減らす（`nav2/*.yaml` の 1 ノード 1 ファイルは、
  「同じノード名が 2 つの断片にあると落ちる」制約とセット）
