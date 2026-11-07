# tsukuba2026

`C:\Users\nop\Downloads\ナビゲーション用ファイル`で提供された地図を使う。
元画像・編集画像の画素と座標は変更しない。XCFは編集用なので配置しない。
PGMの2枚はGit LFSで管理する。取得時はGit LFSを導入して`git lfs pull`を実行する。

| 用途 | ファイル |
| --- | --- |
| 計画用 `/map` | `tsukuba2026_modified.yaml` / `tsukuba2026_modified.pgm` |
| 2D自己位置推定用 `/map_loc` | `tsukuba2026.yaml` / `tsukuba2026.pgm` |
| EMCL3D自己位置推定用 | `tsukuba2026.pcd` |
| 順路 | `../../waypoints/waypoints_tsukuba2026.yaml` |

PGMは14466 × 8604セル、解像度0.05 m、原点は`[-239.888, -81.802, 0.0]`。
VIでは`map_scale: 5`で内部解像度0.25 mを使う。
順路は提供された`waypoints.yaml`の24点をそのまま配置する。
以前のGLIM地図・市役所参照経路とは座標が異なるため共用しない。

PCDは`C:\Users\nop\Downloads\tsukuba2026.pcd`から取得し、このディレクトリへ
`tsukuba2026.pcd`として配置する。PCDはGitに含めない。
SHA256は`7f9c38e7a16efedd7fea258b886cc0086e46a5b9e90bf85f9d949c34d2740cb1`。
2D地図とPCDの座標整合と、機体での走行は未確認。

地図を配置した後、UbuntuまたはDockerで`daifuku_maps`を再ビルドする。

```bash
ros2 launch daifuku_bringup navigation.tsukuba2026.launch.py
```

自己位置推定の既定はEMCL3D。元の2D地図でEMCL2を使う場合は
`localization:=emcl2`を指定する。
順路ファイルは`daifuku_maps`の`waypoints/waypoints_tsukuba2026.yaml`を明示して読み込む。
