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
使うには `src/daifuku_config/overrides/<名前>.yaml` を足して `site: map:` に
`tsudanuma_mugimaru/navigation/map_tsudanuma.yaml` のように書くこと。VI の
`solver` と `map_scale` は、この大きさ（37MB の pgm）だと密では載らないので
`../../../daifuku_config/overrides/map_tsudanuma.yaml` を写して始めるのが早い。

`navigation/tsudanuma-challenge_nav.yaml` は `free_thresh: 0.196` ではなく
`0.2` で、未観測画素 205（p=0.196）が 28 セルぶん free/occupied に化ける
（`simulator/tests/verify_map_thresholds.py` が NG を出す）。上流の値のままに
してあるので、使うなら 0.15 へ下げること。指す pgm は `tsudanuma-challenge_nav3_9.pgm`
で、同じ系列の `nav2` / `nav3` / `nav3_2..8` には yaml が無い（上流でも作業途中の
差し替え候補として置かれている）。
