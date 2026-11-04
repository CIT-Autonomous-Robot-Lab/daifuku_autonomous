# tsudanuma_3d

`tsudanuma_mugimaru`の計画用回廊地図と走行設定を使い、自己位置推定を
EMCL3Dに切り替える場所です。PCD本体はGitに含めません。

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
  --output src/daifuku_maps/maps/tsudanuma_3d/map_tsudanuma.pcd
```

既にワークスペースの隣に取得済みのファイルがある場合は、次のように配置できます。

```bash
cp ../map_tsudanuma.pcd src/daifuku_maps/maps/tsudanuma_3d/
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
  src/daifuku_maps/maps/tsudanuma_3d/map_tsudanuma.pcd \
  src/daifuku_maps/maps/tsudanuma_3d/map_tsudanuma_aligned.pcd
```

入力は残し、出力が既に存在するときは上書きしません。
launchは変換後の`map_tsudanuma_aligned.pcd`を使います。
計画地図と順路の座標は`tsudanuma_mugimaru`から変更しません。

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
ros2 launch daifuku_bringup navigation.tsudanuma_3d.launch.py
```

`localization`の既定は`emcl3d`です。PCDは自己位置推定、
2D回廊地図はNavigationの`/map`に使います。
RVizの`2D Pose Estimate`で初期位置・向きを指定します。
実機とPCの時計を同期し、実機では`use_sim_time:=false`を使います。
