# 設定ファイルを `daifuku_config_manager` へ集める

**この文書は移行が終わったら消す。** 手順書であって、仕様ではない。残る説明は
`config/README.md` と `CLAUDE.md` に書く。

## なぜ

いま設定は 3 パッケージに散っている（`daifuku_bringup/config/`、
`daifuku_stack/config/`、`daifuku_config_manager/config/overrides/`）。値を 1 つ直す
たびに「これは機体側か自律側か」を判断するところから始まり、断片とそれを上書きする
overrides が別の木にあるので、両方を並べて読めない。

ファイルの数（20）は減らない。減るのは**置き場の判断**と、断片と overrides を
突き合わせるときの往復。

## 移す先

```
src/daifuku_config_manager/config/
  README.md                    ← daifuku_stack/config/README.md
  site
  overrides/*.yaml             （そのまま）
  bringup/robot/*.yaml         ← daifuku_bringup/config/robot/
  bringup/sensors/*            ← daifuku_bringup/config/sensors/（MID360_config.json 含む）
  stack/lifecycle_bond.yaml    ← daifuku_stack/config/
  stack/localization/emcl2.yaml
  stack/mapping/slam_toolbox.yaml
  stack/nav2/*.yaml
```

`bringup/` と `stack/` の段は**残す**。これが `config_root`（= `config_sentinel` が
指紋を取る範囲、`params._reject_unknown_nodes` がノード名を探す範囲）の単位なので、
潰すと粒度が変わる。逆に言うと、この段さえ残れば挙動は今と 1 ビットも変わらない。

`stack/nav2/` も畳まない。`navigation.launch.py` はそのディレクトリの yaml を
**全部**ファイル名順に束ねるので、`emcl2.yaml` や `lifecycle_bond.yaml` が同じ階層に
来ると同じノード名の重複で起動時に落ちる。

overrides の 1 段目（`daifuku_bringup:` / `daifuku_stack:`）は変えない。`params.compose`
は `package` と `config_root` を**別々の引数で**受けるので、置き場が動いても部分木の
選び方は無関係。

## 変えるコード

`config_root` などのパスを組み立てているのは 6 ファイル 13 行だけ。`pkg_share` を
`get_package_share_directory("daifuku_config_manager")` に替え、`"config"` の下に
`"bringup"` / `"stack"` を 1 段挟む。

| ファイル | 行 | いま |
| --- | --- | --- |
| `daifuku_bringup/launch/daifuku_bringup_launch/lidar.py` | 46 | `config/sensors` |
| `daifuku_bringup/launch/lidar_bringup.launch.py` | 58, 59 | `config/sensors`, `config` |
| `daifuku_bringup/launch/odom_fusion.launch.py` | 70, 71 | 同上 |
| `daifuku_bringup/launch/robot_bringup.launch.py` | 153, 158, 354 | `config/robot/<name>`, `config` ×2 |
| `daifuku_stack/launch/mapping.launch.py` | 46, 47 | `config`, `config/mapping/slam_toolbox.yaml` |
| `daifuku_stack/launch/navigation.launch.py` | 85, 86, 90, 91 | `config/nav2`, `config`, `config/lifecycle_bond.yaml`, `config/localization/emcl2.yaml` |

依存は足さなくてよい。両パッケージとも `package.xml` に
`<exec_depend>daifuku_config_manager</exec_depend>` を持っている（share の解決は
実行時なので exec_depend で正しい）。

### `CMakeLists.txt`（両方）

`install(DIRECTORY config ...)` から `config` を**落とす**。これは同じコミットで
必ずやること。残すと**新旧どちらの share にも設定が実在する**状態になり、
`params._config_files` は古いほうを生きたファイルとして読む（リンク切れではないので
飛ばされない）。指紋に古い複製が入り続けるので、2026-08-07 のリンク切れより悪い。

### `daifuku_config_manager/setup.py`

`data_files` はサブディレクトリを再帰しない。ディレクトリごとに 1 エントリ要るので、
`os.walk` で回す。

```python
def _config_data_files():
    """config/ の下を丸ごと data_files に。**site だけは決め打ちの側に残す。**"""
    out = []
    for root, _, files in os.walk("config"):
        names = [os.path.join(root, f) for f in files if f != "site"]
        if names:
            out.append(("share/" + package_name + "/" + root.replace(os.sep, "/"), names))
    return out
```

「新しくファイルを足したときだけビルドが要る」性質は変わらない。CMake の
`install(DIRECTORY)` も `--symlink-install` ではファイル単位に張るので、いまも同じ。

## 変えるドキュメントとヘルプ文字列

diff の大半はこちら。3 種類に分けて、扱いも分ける。

1. **`--show-args` に出るヘルプ文字列**（挙動に近い。取りこぼすと嘘の案内になる）
   `robot_bringup.launch.py` の 433 / 448 / 482、`navigation.launch.py` の 525。
   `lidar.py` の 95 / 135 が指す `config/urg_*.param.yaml` は**上流
   `raspicat_bringup` のもの**なので触らない。
2. **コード中のコメント・docstring**
   `robot_bringup.launch.py:144`、`nav2_params.py` の 17 / 39 / 61-62 / 100、
   `daifuku_stack_launch/__init__.py:30`、`navigation.launch.py:206` / 617、
   `elevation_filter.py:63`、`control_panel.py:76`、`simulator/` の 4 か所
   （`fake_robot.py`、`nav_container.sh`、`run_case.sh`、`isaac_raspicat.py`、
   `map_to_usd.py`）。
3. **`docs/` と各 README**（19 ファイル）。`src/daifuku_stack/config/README.md` が
   `daifuku_config_manager/config/README.md` へ動くので、そこへのリンクも全部。

`CLAUDE.md` は**同じコミットで**直す。放置すると指示そのものが嘘になる。効くのは
少なくとも次の 3 か所。

- 「触る前に読むもの」の表（`src/daifuku_stack/config/README.md` を指している行）
- 「設定ファイル (`src/daifuku_{bringup,stack}/config/**/*.yaml` と …) のコメント」の
  見出し — パスがそのまま文字列で入っている
- 「リポジトリの範囲」の表の `daifuku_config_manager` の行（「設定の合成規則と、
  場所ごとの調整」→ 設定の実体そのものを持つようになる）

## 新しく増える罠

**`config_root` は必ず `bringup/` か `stack/` の段を指すこと。** 親の `config/` を
指すと `_reject_unknown_nodes` が両パッケージ分のノード名を認めるようになり、
`daifuku_bringup:` の下に nav2 のノード名を書いても**通ってしまう**（誰も読まない
部分木になるのを止めるための検査が効かなくなる）。黙って広がるので、コメントでは
なく `params.compose` に assert を 1 行入れて塞ぐ。

```python
if os.path.basename(config_root) not in ("bringup", "stack"):
    raise RuntimeError(f"config_root must be the per-package level, got {config_root}")
```

## 手順

1. **コミット 1**: `git mv` ＋ 上のコード 13 行 ＋ `CMakeLists.txt` ×2 ＋ `setup.py`
   ＋ ヘルプ文字列。**分けない** — 途中のコミットは起動しないので、二分探索で
   踏むと原因を取り違える。
2. **コミット 2**: コメント・`docs/`・README・`CLAUDE.md`。
3. **コミット 3**: この文書を消す。

## 検証（Windows ホストでは走らない。dev コンテナか実機で）

**先に `rm -rf install build`。** `git mv` で移すと古い symlink の指す先がソース側から
消えるので、リンク切れとしては検出できる（`_config_files` が飛ばす）が、飛ばした
ものは**指紋の対象からも外れる**ので設定が 1 つ消えたことに気付けない。順序も保証
できないので、掃除ではなく作り直す。

```bash
rm -rf install build && docker compose up -d
ros2 launch daifuku_stack navigation.launch.py --show-args   # params_dir などの既定が新しい場所か
ros2 launch daifuku_bringup robot_bringup.launch.py          # 上がるか、config_sentinel が指紋を取れるか
find install -xtype l                                        # 空であること
colcon test --packages-select daifuku_bringup daifuku_stack daifuku_config_manager
```

検査が生きていることも見る。`overrides/map_19f.yaml` の `daifuku_bringup:` の下に
でたらめなノード名を 1 行足して、**起動時に落ちる**こと（移行前と同じメッセージで）。

## やらないこと

- 設定を ROS トピックで配る（前段の議論のとおり。ファイルシステムを共有しない相手が
  出てきたら、そのとき）
- `bringup/` `stack/` を潰して 1 つにする（指紋の粒度が変わる）
- ファイルを統合して数を減らす（`nav2/*.yaml` の 1 ノード 1 ファイルは、
  「同じノード名が 2 つの断片にあると落ちる」制約とセット）
