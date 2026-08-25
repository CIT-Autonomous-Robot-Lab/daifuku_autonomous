# tsudanuma_mugimaru

[CIT-Autonomous-Robot-Lab/mugimaru_bringup](https://github.com/CIT-Autonomous-Robot-Lab/mugimaru_bringup)
の `map/tsudanuma/` から持ってきたもの（2026-08-25 取得。`20f43fd`）。上流の
ディレクトリ構成（`localization/` と `navigation/`）は変えていない——**どちらにも
`map_tsudanuma.yaml` があるので、平らにすると衝突する**。上流にはこのほかに
`map_tsudanuma_mcl_6` / `tsudanuma-challenge_loc*` / `resize.py` があるが、
落としてから消した。

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

**この `localization:` の側はいまここに無い。** 上流の対は
`tsudanuma-challenge_loc.yaml` + `tsudanuma-challenge_loc3.pgm` だが、落としたあとに
消してある（上の段落）。そのまま使うなら取り直すこと。

**2 枚を別にできるのは `localization:=emcl2` のときだけ**（`amcl` と
`localization:=vi` は起動時に落ちる。[自律移動](../../../../docs/usage/navigation.md#地図は2枚)）。
VI の `solver` と `map_scale` は、この大きさだと密では載らないので
`../../../daifuku_config/overrides/map_tsudanuma.yaml` を写して始めるのが早い。

上流がどの pgm を読んでいるかは yaml の `image:` にある。`nav` 系の本命は
`tsudanuma-challenge_nav3_9.pgm`、`loc` 系は `tsudanuma-challenge_loc3.pgm`（どちらも
2500×1581 @0.1m）。**接尾辞なしの `tsudanuma-challenge_nav.pgm` は元の 3140×2425 で、
`nav2` はそこから (x=515, y=351) を切り出したもの**。`nav2` 以降は GIMP で壁を描き
足したり消したりした編集の世代で、`nav3_9` までに正味 +21,498 画素（約 215m²）を
塞いでいる。`map_tsudanuma.*`（37MB、0.05m/セル）は上流では
`vi2_exp_astar_jetson.launch.py` だけが読む別系統。

`navigation/tsudanuma-challenge_nav.yaml` は `free_thresh: 0.196` ではなく
`0.2` で、未観測画素 205（p=0.196）が 28 セルぶん free/occupied に化ける
（`simulator/tests/verify_map_thresholds.py` が NG を出す）。上流の値のままに
してあるので、使うなら 0.15 へ下げること。
