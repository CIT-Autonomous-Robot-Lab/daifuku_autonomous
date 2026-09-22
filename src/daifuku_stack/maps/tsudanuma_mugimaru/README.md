# tsudanuma_mugimaru

[CIT-Autonomous-Robot-Lab/mugimaru_bringup](https://github.com/CIT-Autonomous-Robot-Lab/mugimaru_bringup)
の `map/tsudanuma/` から持ってきたもの（2026-08-25 取得。`20f43fd`）。上流の
ディレクトリ構成（`localization/` と `navigation/`）は変えていない——**どちらにも
`map_tsudanuma.yaml` があるので、平らにすると衝突する**。上流にはこのほかに
`map_tsudanuma_mcl_6` と `resize.py` があるが、どの launch からも参照されて
いないので落としていない。

隣の `../tsudanuma/` とは**別の地図**。原点が違う（あちらは `[-100, -100]`、
こちらは `[-145.141138, -103.344482]`）ので、ウェイポイントも overrides も
共用できない。

選ぶのは `src/daifuku_config/overrides/tsudanuma_mugimaru.yaml`（`tools/site.sh
tsudanuma_mugimaru`）。**同梱の overrides で唯一、地図を 2 枚に分けている** — 上流も
`localization/` と `navigation/` で別の地図を読んでいる（`vi2_exp.launch.py`）。

```yaml
site:
  map:
    navigation:   tsudanuma_mugimaru/navigation/tsudanuma-challenge_nav.yaml
    localization: tsudanuma_mugimaru/localization/tsudanuma-challenge_loc.yaml
```

**2 枚を別にできるのは `localization:=emcl2` のときだけ**（`amcl` と
`localization:=vi` は起動時に落ちる。[自律移動](../../../../docs/usage/navigation.md#地図は2枚)）。
VI の値は隣の `tsudanuma.yaml` から写して内部解像度ぶんだけ計算し直してある
（`map_scale: 2` で 0.20m/cell = 5925 万状態、compact）。**まだ実機で通していない。**

**2026-09-02 に navigation 側をもう 1 枚足した**（`tsudanuma-challenge_nav_corridor.yaml`。
`site: map: navigation:` はいまこちらを指す）。`nav3_9` から**順路の回りだけを残して
自由空間を削った**もので、solve が半分になる（下の「3 枚目」）。

## 2 枚は何が違うか

上流がどの pgm を読んでいるかは yaml の `image:` にある。`loc` 系の本命は
`tsudanuma-challenge_loc3.pgm`、`nav` 系は `tsudanuma-challenge_nav3_9.pgm`（どちらも
2500×1581 @0.1m、原点も同じ）。**この 2 枚は同じ地図で、違いは壁の描き足しだけ**
（2026-08-25 に実測）:

* `loc3` の壁の **91%** が `nav3_9` の壁の ±1px に入る（±3px なら 100%）。ずらさずに
  重なるので、同じ原点のまま両方読める。
* 逆に `nav3_9` にしかない壁が **85,317 画素 = 853 m²**。これが手で描き足した
  「入ってほしくない場所」で、11,326 個のかたまりに散っている（最大 122 m²、
  上位 10 個で全体の 48%）。**自己位置推定にこれを使うと、LiDAR が見つけられない
  壁が 853 m² ぶん増える**——分ける理由がこれ。

系譜はきれいな一本道ではない。**`loc3` は `nav2` から壁を削ったもの**（±3px で
100% 含まれる）で、`nav2` 以降の 9 世代が GIMP で壁を足し引きした跡
（`nav3_9` までに正味 +21,498 画素）。一方 **`loc` / `loc2` は別系統**で、`nav` 系
とも `loc3` とも 3 割しか重ならない（別の SLAM か、別の加工）。接尾辞なしの
`tsudanuma-challenge_{loc,nav}.pgm` は 3140×2425 で、`loc2` も `nav2` も
そこから (x=515, y=351) を切り出している。`map_tsudanuma.*`（37MB、0.05m/セル）は
上流では `vi2_exp_astar_jetson.launch.py` だけが読む別系統。

`tsudanuma-challenge_{nav,loc}.yaml` はどちらも `free_thresh: 0.196` ではなく `0.2` で、
未観測画素 205（p=0.196）が nav で 28 セル・loc で 25 セルぶん free/occupied に化ける
（`simulator/tests/verify_map_thresholds.py` が NG を出す）。上流の値のままに
してあるので、使うなら 0.15 へ下げること。

## 3 枚目 — 順路の回りだけを残した navigation の地図

`tsudanuma-challenge_nav_corridor.pgm` / `.yaml`（2026-09-02 に生成、2026-09-03 に作り直し）。`nav3_9` の
壁はそのままに、**順路から 8.5m 以内を残して残りの自由空間を占有へ倒した**もの。
`site: map: navigation:` はこちらを指しています。

```
uv run corridor-map \
    src/daifuku_stack/maps/tsudanuma_mugimaru/navigation/tsudanuma-challenge_nav.yaml \
    src/daifuku_stack/maps/tsudanuma_mugimaru/navigation/tsudanuma-challenge_nav_corridor.yaml \
    --waypoints src/daifuku_stack/waypoints/waypoints_tsudanuma_mugimaru_v1.0.yaml \
                src/daifuku_stack/waypoints/waypoints_tsudanuma_mugimaru_v1.1.yaml \
    --radius 8.5
```

**なぜ要るか。** `vi_planner` の solve 時間を決めているのは状態数ではなく**解くべき
自由空間の広さ**です。同条件のフル solve は隣の `tsudanuma` が 11.83 秒に対して
この地図が 27.95 秒で、状態数はほぼ同じ（5654 万 / 5932 万）。差はあちらが 68.2%
未観測＝障害物扱いで解かなくてよいのに対し、こちらは未観測 0.05% でほぼ全域を
解いていたことです。`map_scale` を上げても状態数の減りを反復の増加が相殺して
速くなりません（実測は
[`src/daifuku_config/README.md`](../../../daifuku_config/README.md)）。

| | `nav3_9` | `nav_corridor` (r=8.5m) |
| --- | --- | --- |
| 自由セル | 3,680,134 (93.1%) = 36,801 m² | **1,814,401 (45.9%) = 18,144 m²** |
| フル solve（`early_start: false`、4 スレッド） | 27.95 s | 27.95 → 13.07 s（**要再測**） |
| 先読み（1 スレッド） | 54.29 s | 51.01 s（**要再測**） |
| 先読み（2 スレッド） | 37.50 s | 32.44 s（**要再測**） |
| `early_start` の打ち切り | 13.56 s | 15.86 s（**要再測**） |

**`nav_corridor` 列の秒数は 2026-09-03 に作り直す前の地図で測ったものです**（下の
「上下反転していた」）。作り直しで回廊は 2 つ動きました——**置き場所**（上下反転を
直した）と**太さ**（連結性のため r=5.0 → 8.5m）です。自由空間は 30.6% →
**45.9%** に増えたので、**フル solve の短縮はここに書いてある 2.1 倍より小さくなる
はず**です。秒数は測り直すまで当てになりません。`nav3_9` 側は加工前の地図なので
影響を受けません。

**効くのはフル solve だけです。** `early_start` の打ち切りはもともとゴールまで
繋がった時点で止める＝経路の周りしか解いていないので変わりません。先読みは
**1 スレッド固定**（`waypoint_prefetch_threads` 既定 1）なので並列の恩恵を受けません。
**対で 2 に上げてあります** — 32.44 秒 = 走行 89.85 秒の 1/2.8 で、実機の solve が
本ホストの 2.8 倍まで遅くても次の点に間に合う目安がこれです（この比も同じく要再測）。

**この地図は順路から導かれます。順路を変えたら作り直してください。** 新しい点が
回廊の外に出ると、その点は占有セルに乗るので**ゴールが出ないだけ**で、地図が古い
とは誰も言いません。生成ツールは書き出す前に全点が自由セルに落ちるか確かめ、
この加工で失われた点があれば**書かずに落とします**。

**半径 8.5m の下限を決めているのは連結性です。** 回廊は順路の点どうしを結ぶ
**直線**の周りに引かれますが、実際に走れる道は建物を回り込むので、細いと回廊が
壁で分断されます。分断されても点は自由セルのままなので、**エラーも警告も出ない
まま経路が引けない**——生成ツールは書き出す前にここも確かめ、加工前の地図で
繋がっていた点どうしが切れたら落とします（実測の下限は 8.0m。8.5m はその余裕）。
連結さえ保てば狭いほど速くなりますが、機体が回廊から出ると start が占有セルに
なって方策が引けないので、横ずれの実測がないうちも下げないこと（2026-08-20 の
「左それ」）。

**1 点だけ、順路の点が壁の中にあります**（この加工とは無関係で、`nav3_9` の
時点でそう）。生成時に WARN で出ます。

| 点 | `nav3_9` | `loc3` |
| --- | --- | --- |
| (90.79, 35.29) — v1.1[5] | 占有 | 自由 |

実測の `loc3` では自由なので、手描きの壁が塞いでいます。ゴールとして与えると
**エラーは出ずにただ失敗**します。

## 上下反転していた

2026-09-03 に作り直しました。**2026-09-02 に生成した `nav_corridor` は、回廊が
上下逆に載っていました。**
`corridor_map.py` が画像の行 0 を世界の y=origin_y として組んでいた一方、
`map_server` は行 0 を y の**最大**として読む（`msg.data[MAP_IDX(w, height - y
- 1, x)]`）ためです。回廊の形も面積もそれらしいままなので地図としては
もっともらしく、**順路 66 点のうち 22 点が壁の中**という形でしか現れません。
生成時の検算が同じ添字で書かれていたので、そちらも通っていました。

かつてここに「壁の中の 3 点」の表がありましたが、あれもその添字で測ったもので
**まるごとこの不具合の産物**でした（3 点とも実際には自由セルです。本当に壁の中に
あるのは上の 1 点だけ）。反転を直すのと対で
`simulator/tests/verify_corridor_orientation.py` を置いてあります。

直した回廊を正しい向きで見て初めて、**回廊が 4 つに分断されていた**ことも
分かりました（半径 5m では建物を回り込む区間で切れる）。点が自由セルかどうかだけ
見ても気づけないので、生成ツールに連結性の検算を足し、半径を 8.5m にしてあります。
