# tsukuba2026 - GLIM 実測点群からの地図

`~/Downloads/glim01.ply` と `glim01.pcd` を読み取り専用で変換した。
両方とも同じ 2,914,091 点の XYZI データなので、重複加算していない。
PCD の宣言点数の後にある 3,904 バイトのゼロ埋めは点として扱わない。
ファイル全体と有効点列の SHA256 は `conversion.json` に記録している。
元の PCD・PLY は変更していない。

## 地図と座標

- 0.1 m/セル、3029 x 2777 セル、302.9 m x 277.7 m。
- 原点は `[-76.4, -177.9, 0.0]`。点群の XY を回転・平行移動せず保持した。
- PGM の上端が地図の Y 最大。PNG は対応する PGM と画素単位で同じ。
- 0 が占有、205 が未観測、254 が自由。未観測を自由へ読み替えない
  `free_thresh: 0.196` を使う。
- `navigation.pgm` は推定地面から高さ 0.275..2.5 m の障害物を投影。
- `localization.pgm` は高さ 0.275..5.5 m の側面・構造物を投影。
- 同名の YAML が map_server 用メタデータ、PNG がフォト等での表示用。

地面は、Z <= 1.0 m の点から 0.5 m 格子内の下位 10% を取り、3 点以上ある
格子を支持点として最近傍補間と 5 x 5 格子の opening で推定した。
その地面から 0.15 m 以内に観測点があるセルを自由空間の根拠とし、
半径 0.2 m の closing で小さな観測の隙間だけを補った。障害物が優先する。

観測軌跡や各ビームの発射位置はないため、射線による自由空間更新ではない。
建物内部や大きな未観測部分を白く塗り潰してはいない。地面分類と通行可能性は
現地で確認が必要で、全コースの経路連結や実走行成功を示す地図ではない。

**2025 の VTC 地図・ウェイポイントとは別座標系。** 2025 の順路をコピーしない。
2026 の経路は別途、この GLIM 座標系で作成する。

## 再生成

リポジトリルートで実行する。Python の依存は `tools/maps/pyproject.toml` にある。

```bash
uv run --project tools/maps python tools/maps/cloud_to_map.py \
  "$HOME/Downloads/glim01.ply" --equivalent "$HOME/Downloads/glim01.pcd" \
  --output src/daifuku_stack/maps/tsukuba2026 \
  --resolution 0.1 --ground-ceiling 1.0
```

## 独立した PGM ノイズ除去

点群の再変換とは別に `tools/maps/denoise_pgm.py` を実行する。
今回の地図では、8 近傍で連結した占有成分が **8 セル以下**のときだけ、
該当セルを 205（未観測）へ戻す。スクリプトは指定値未満を除くため、
`--min-component-cells 9` を使う（スクリプト単体の既定値は 3）。
自由空間や既存の未観測セルは変えず、元画像は上書きしない。
実物の細い物体かノイズかを判別する処理ではないため、閾値を大きくするほど
小さな構造物も自己位置推定用の特徴から外れる。

```bash
uv run --project tools/maps python tools/maps/denoise_pgm.py \
  src/daifuku_stack/maps/tsukuba2026/navigation.pgm \
  src/daifuku_stack/maps/tsukuba2026/navigation_denoised.pgm \
  --min-component-cells 9
uv run --project tools/maps python tools/maps/denoise_pgm.py \
  src/daifuku_stack/maps/tsukuba2026/localization.pgm \
  src/daifuku_stack/maps/tsukuba2026/localization_denoised.pgm \
  --min-component-cells 9
```

出力の PNG は処理済み PGM と同じ画素。JSON に除去セル数と入出力 SHA256 を残す。
自己位置側は `localization_denoised.yaml` を選ぶ。計画側は次の地面補間も行う。
原画像と処理済み画像の解像度・原点・大きさは同じ。

## ナビ用だけの地面補間

点のあるセルだけを白にした原地図では、白は 5.01% で、保守的な 2 x 2 縮小後は
3.74% だった。小さな地面観測の欠けで通路が切れるため、利用者の指定により
ナビ用だけ、半径 0.5 m の closing で局所的な穴を補間した。
新たに白にするのは元の白セルから **0.3 m 以内の未観測セルだけ**。
黒セルは変えず、元の白セルも減らさず、外側全体を膨らませる dilation はしない。
自己位置用の地図にはこの補間を適用しない。

```bash
uv run --project tools/maps python tools/maps/fill_ground_free.py \
  src/daifuku_stack/maps/tsukuba2026/navigation_denoised.pgm \
  src/daifuku_stack/maps/tsukuba2026/navigation_connected.pgm \
  --resolution 0.1 --closing-radius 0.5 --support-radius 0.3
```

補間した 174,174 セルを含む `navigation_connected.pgm` とその YAML を
`overrides/tsukuba2026.yaml` の計画側に選ぶ。地図の白は 7.08%、
縮小後は 6.07%。縮小後の最大 4 近傍連結領域は約 1,338 m² から約 3,482 m² に増えた。
これはラスタ上の連結性であり、VI の経路成功や車体の走行試験の結果ではない。
新たな観測や射線推定ではなく、既存の地面観測に近い範囲での補間である。

GLIM 原点 (0, 0) の 2 x 2 区画には観測した黒セルがあるため、その区画は
保守的縮小後も通行不可。障害物を消して原点を白くすることはしていない。
実際のロボットの初期位置は、この点群の原点と同一とは限らない。

## 実機の起動

2025 の分離スキャン構成を参考にした専用入口を使う。
既定は実機時刻、RViz オフ、emcl2 + VI standalone、compact + greedy。
計画側の高さ上限は新地図に合わせて 2.5 m、自己位置側は 5.5 m。
計画内部は `map_scale: 2` の 0.2 m/セルとし、全域地図を二重保持しないよう
先読みは無効。経路の成功・パラメータの現地適合は未確認。

依存と新規ファイルを反映するため、実機で再ビルドする。
既存の navigation / mapping は終了し、robot_bringup のドライバ・EKF は動かしておく。

```bash
docker compose build
docker compose up -d
docker compose exec ros2 /ros_entrypoint.sh \
  ros2 launch daifuku_stack navigation_tsukuba2026.launch.py
```

`/livox/lidar`、`/odom`、`odom -> base_footprint` と LiDAR の TF が入力。
実機では TF 投影前に両方の点群を受信時刻へ補正する。ドライバはこの入口では立てない。
`src/daifuku_config/site` は自動で変更しない。
初期位置は GLIM の地図座標で `/initialpose` に与える。表示が必要なときだけ
`use_rviz:=true` を添え、2D Pose Estimate で現在位置を指定する。

時刻補正なしで点群バッグ等を使う場合:

```bash
ros2 launch daifuku_stack navigation_tsukuba2026.launch.py use_sim_time:=true
```

この場合は `/clock` と同じ座標系・時刻系の入力 TF が必要。
`localization_gravity` は odom の roll/pitch だけから作る。既定の実機 EKF が
`two_d_mode: true` の場合は傾きが 0 で、別途 IMU 姿勢推定を行うものではない。
