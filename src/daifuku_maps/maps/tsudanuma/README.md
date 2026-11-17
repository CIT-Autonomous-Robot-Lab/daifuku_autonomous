# tsudanuma

計画用の2D回廊地図と、EMCL3Dの自己位置推定用PCDを使います。
PCD本体はGitに含めません。設定は`src/daifuku_config/sites/tsudanuma/overrides.yaml`です。

## 2D地図と順路

2D地図は[CIT-Autonomous-Robot-Lab/mugimaru_bringup](https://github.com/CIT-Autonomous-Robot-Lab/mugimaru_bringup)
の`map/tsudanuma/`から取得した地図に基づきます（取得時のコミット`20f43fd`）。
原点は`[-68.946, -101.928, 0]`、解像度は0.1 m、画像は2500×1581セルです。

| ファイル | 用途 |
| --- | --- |
| `navigation/tsudanuma-challenge_nav_corridor.yaml` | `/map`。順路の周囲8.5 mを残した計画用地図 |
| `navigation/tsudanuma-challenge_nav.yaml` | 回廊の生成元。手描きの進入禁止壁を含む |
| `localization/tsudanuma-challenge_loc.yaml` | 実測の2D地図。PCDの座標合わせに使った壁面 |

`waypoints/waypoints_tsudanuma_v1.0.yaml`と`v1.1.yaml`はこの座標系の順路です。
原点`[-100, -100]`・解像度0.05 mの旧地図用の順路とは共用できません。
改名前の順路を外部に保存している場合も、ファイル名だけで判断しないでください。

回廊は順路から生成するため、順路変更時はリポジトリのルートで再生成します。
両方の版を使うので、生成時には両方を渡します。

```bash
uv run tools/maps/corridor_map.py \
  src/daifuku_maps/maps/tsudanuma/navigation/tsudanuma-challenge_nav.yaml \
  src/daifuku_maps/maps/tsudanuma/navigation/tsudanuma-challenge_nav_corridor.yaml \
  --waypoints src/daifuku_maps/waypoints/waypoints_tsudanuma_v1.0.yaml \
              src/daifuku_maps/waypoints/waypoints_tsudanuma_v1.1.yaml \
  --radius 8.5
```

全点の自由セル・連結性・上下方向を検算してください。PGMの行0は地図のy最大側です。
この順路では半径8.0 m未満で回廊が壁に分断されるため、余裕を含めて8.5 mにしています。
`tools/maps/verify_{map_thresholds,corridor_orientation,waypoints}.py`も使います。

既存の地図・順路には次の制約があります。今回の改名では値や座標を変えていません。

- v1.1の点`[5]`（90.79, 35.29）は計画用の手描き壁の中にあります。実測地図では自由ですが、
  計画用地図でゴールとして使うと失敗します。
- `free_thresh: 0.2`は上流の値です。未観測画素205が自由セルへ分類されるため、
  閾値の検査は問題を報告します。
- `map_scale: 2`で内部解像度は0.20 mです。3へ上げると狭い通路が失われます。
- 先読みは場を2本保持します。8 GB機が前提で、4 GB機では`waypoint_prefetch`を無効にします。

## 点群地図の取得

配布元は[Tsukuba Challenge Datasets](https://github.com/tsukubachallenge/tc-datasets)
の「Tsudanuma 2020, Chiba Institute of Technology, Map Data」です。
[PCDのダウンロード](https://drive.google.com/file/d/19HeoDbAmwfxLW40NhZFPQGvRZKuip62B/view?usp=sharing)
から取得し、`map_tsudanuma.pcd`という名前でこのディレクトリへ配置してください。
提供者は千葉工業大学、SLAM方式はLIO-SAM、ライセンスはCC BY 4.0です。
コマンドで取得する場合はリポジトリのルートで実行します。

```bash
curl --fail --location \
  'https://drive.google.com/uc?export=download&id=19HeoDbAmwfxLW40NhZFPQGvRZKuip62B' \
  --output src/daifuku_maps/maps/tsudanuma/map_tsudanuma.pcd
```

既にワークスペースの隣に取得済みのファイルがある場合は、次のように配置できます。

```bash
cp ../map_tsudanuma.pcd src/daifuku_maps/maps/tsudanuma/
```

PCDはASCII形式のXYZ・intensity、13,611,640点の地図を使用します。
使用したファイルのSHA256は
`19fd7f0cf35e3d8a72f089e48081f572ff21d0a8fd92fcaf916f1bce8993a486`です。
配布一覧は13,583,284点・法線付きと記載していますが、今回の手元ファイルとは
点数・フィールドが異なります。取得後にハッシュを照合し、違う版に座標補正を流用しないでください。
Google Driveが確認画面を返す場合はブラウザからダウンロードしてください。
`.gitignore`の`*.pcd`により管理対象から除外します。
取得後に`daifuku_maps`と追加したlaunch・設定をビルドしてください。
PCDが無い場合は起動前の地図検査で停止します。

## 座標の整合

取得したPCDをそのまま`map`として使用しません。リポジトリのルートで
次の変換を行ってからビルドします（Python 3、NumPyが必要です）。

```bash
python3 tools/maps/align_tsudanuma_pcd.py \
  src/daifuku_maps/maps/tsudanuma/map_tsudanuma.pcd \
  src/daifuku_maps/maps/tsudanuma/map_tsudanuma_aligned.pcd
```

入力は残し、出力が既に存在するときは上書きしません。
launchは変換後の`map_tsudanuma_aligned.pcd`を使います。
計画地図と順路の座標は変更しません。

PCDから既存の2D地図へ適用する変換は次の平面剛体変換です。
zはそのまま残します。高さの絶対原点は2D壁面からは決定できません。

```text
x_map = cos(yaw) * x_pcd - sin(yaw) * y_pcd - 33.85123089
y_map = sin(yaw) * x_pcd + cos(yaw) * y_pcd - 32.99809369
yaw = 2.87006675 rad
```

PCDのXYを0.3 mセルへ集約し、セル内の高さ幅が2 mを超える鉛直面を抽出して、
実測地図`tsudanuma-challenge_loc3.pgm`の壁面を照合しました。
原点は実ファイルの`[-68.946, -101.928, 0]`、解像度は0.1 mを使っています。
壁面から抽出点への距離を3 mで打ち切った平均は、未補正の1.998 mから0.315 mへ減少しました。
この値は地図間の照合指標であり、実機の自己位置推定誤差ではありません。
別の版のPCDに同じ数値を適用しないよう、変換ツールは入力ハッシュを検査します。

## 起動

機体側のMid-360・オドメトリを起動した環境で実行します。

```bash
ros2 launch daifuku_bringup navigation.tsudanuma.launch.py
```

`localization`の既定は`emcl3d`です。PCDは自己位置推定、
2D回廊地図はNavigationの`/map`に使います。
RVizの`2D Pose Estimate`で初期位置・向きを指定します。
実機とPCの時計を同期し、実機では`use_sim_time:=false`を使います。
