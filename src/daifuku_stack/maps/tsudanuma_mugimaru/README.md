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

**どの overrides からも参照されていないので、いまは `tools/site.sh` で選べない。**
使うには `src/daifuku_config/overrides/<名前>.yaml` を足して `site: map:` を書く。
**この地図こそ 2 枚に分ける値打ちがある** — 上流も `localization/` と `navigation/` で
別の地図を読んでいる（`vi2_exp.launch.py`）。

```yaml
site:
  map:
    navigation:   tsudanuma_mugimaru/navigation/tsudanuma-challenge_nav.yaml
    localization: tsudanuma_mugimaru/localization/tsudanuma-challenge_loc.yaml
```

**2 枚を別にできるのは `localization:=emcl2` のときだけ**（`amcl` と
`localization:=vi` は起動時に落ちる。[自律移動](../../../../docs/usage/navigation.md#地図は2枚)）。
VI の `solver` と `map_scale` は、この大きさだと密では載らないので
`../../../daifuku_config/overrides/map_tsudanuma.yaml` を写して始めるのが早い。

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
