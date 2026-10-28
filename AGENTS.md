# エージェント向け指針

Raspberry Pi Cat を ROS 2 Humble と standalone VI で自律移動させるワークスペースです。
指針はこのファイルにまとめます。説明・追記は日本語で、コードから分かる構成や既定値、
実験の時系列ではなく、壊してはいけない制約と設定の理由を残してください。

## 作業の入口

| 触るもの | 先に読む |
| --- | --- |
| 全体構成・launch | [README](README.md)、[構成](docs/usage/architecture.md) |
| 設定値・override | [設定の説明](src/daifuku_config/README.md)、合成の実装 `src/daifuku_config/daifuku_config/{overlay,params}.py` |
| ビルド・設定の反映 | [日常操作](docs/usage/operations.md#設定変更を反映する) |
| 実機・LiDAR | [Pi 4](docs/setup/raspberry-pi-4.md)、[Pi 5](docs/setup/raspberry-pi-5.md)、[LiDAR](docs/setup/lidar.md) |
| Docker | [Docker](docker/README.md) |
| シミュレータ | [VTC ハーネス](simulator/README.md) |
| 駆動ドライバ | [raspicat_driver](src/raspicat_driver/README.md) |
| 操作パネル | [daifuku_rqt](src/daifuku_rqt/README.md)、[操作](docs/usage/control-panel.md) |
| ウェイポイント | [daifuku_waypoint_manager](src/daifuku_waypoint_manager/README.md) |
| `src/value_iteration3/` | そのディレクトリの `AGENTS.md` |
| 実機の不調 | [切り分け](docs/usage/troubleshooting.md) |

## 所有範囲とビルド

- 自前は `daifuku_bringup`、`daifuku_robot`、`daifuku_perception`、
  `daifuku_localization`、`daifuku_navigation`、`daifuku_mapping`、`daifuku_maps`、
  `daifuku_config`、`raspicat_driver`、`daifuku_rqt`、
  `daifuku_waypoint_manager` です。残りは `daifuku_autonomous.repos` から取得する
  外部リポジトリで、変更は上流側で扱います。`raspicat_ros`、`raspicat_description`、
  `raspimouse2` は untracked でも本リポジトリへ追加しません。
- 機能パッケージの分担は次のとおりです。
  - `daifuku_robot`: 駆動・LiDAR ドライバ、EKF、IMU 前処理、teleop
  - `daifuku_perception`: `scan_pipeline`、仰角フィルタ、スタンプ打ち直し
  - `daifuku_localization`: 自己位置推定器と leveling
  - `daifuku_navigation`: VI の計画・追従、velocity smoother、計画用 map server
  - `daifuku_mapping`: SLAM（他の機能を起動せず、bringup から include する部品）
  - `daifuku_maps`: 地図（`maps/`）と順路（`waypoints/`）
  - `daifuku_bringup`: 公開の合成 launch、RViz 設定（`rviz/`）、`system_monitor`
  機能パッケージどうしを公開入口として使わず、合成は `daifuku_bringup` に寄せます。
  設定の実体と合成規則（Python は `src/daifuku_config/daifuku_config/`）は
  `daifuku_config` に置き、そこから機能パッケージの実装を import しません。
- Windows は編集ホストです。ビルド・ROS の確認は Docker または Ubuntu 22.04 で行います。
  再ビルドと再起動の判断表は[日常操作](docs/usage/operations.md#設定変更を反映する)に
  一本化します。Pi の再起動では `workspace-build` が走らず、コンパイル済みコードは更新されません。
- 新規・移動したファイルは symlink の作成にビルドが必要です。移動・改名後は古い
  `install/` の symlink が残り、パッケージ移動では `build/<pkg>/CMakeCache.txt` にも
  旧パスが残ります。該当パッケージの生成物を整理してからビルドしてください。
- 自前 Python ノードは CMake が実行可能なラッパーを生成します。Windows の
  ソース実行ビットに依存させません。ラッパーは share の実装を実行するので、
  `--symlink-install` では Python の編集も再ビルドなしで反映されます。
  起動名を追加したら CMake の生成対象と CI のインストール確認を揃えます。
- Pi の ROS base イメージでは `daifuku_rqt` と `daifuku_waypoint_manager` を
  `SKIP_PACKAGES` から外しません。rqt/RViz が無くビルドできないためです。
  `daifuku_bringup`、`daifuku_robot`、`daifuku_config` は実機でも必須です。

## 設定と起動の契約

- 設定の合成・場所別の値の理由は[設定の説明](src/daifuku_config/README.md)に置きます。
  `navigation/nav2/*.yaml` と `navigation/vi_planner.yaml` はノード単位で分割し、
  同じノードを複数断片に定義しません。断片間は深くマージせず、override だけを深くマージします。
- 公開の navigation 入口は場所ごとに固定です（`daifuku_bringup` の
  `navigation.<場所>.launch.py`）。どの場所を走るかは launch 名で決まり、
  `OVERRIDES` も `site` ファイルも変えられません。`daifuku_navigation` の
  `navigation.launch.py` は合成から使う部品で、公開入口ではありません。
- 場所の override は `src/daifuku_config/sites/<場所>/overrides.yaml` です。
  最上位キーは機能パッケージ名（`daifuku_robot:`、`daifuku_perception:`、
  `daifuku_localization:`、`daifuku_navigation:`、`daifuku_mapping:` など）で、旧 stack の節はありません。
- `site` は場所名 1 語だけです。地図は名前から推測せず、override の
  `site.map.navigation` と `site.map.localization` から解決します。同じ地図でも両方明記します。
  `/map` は計画用、`/map_loc` は自己位置推定用です。明示した地図と override の不一致、
  計画用地図の未指定を既定地図へのフォールバックで隠しません。
- mapping の `overrides:=<場所>` は既定 `none` です。VTC の公開入口
  `navigation.vtc.launch.py` は外部の地図を明示し、`overrides` は `none` です。
  実機の場所別調整を混ぜません。
- `site` ファイルと `site_manager` は**次に立てる場所**の任意の選択だけを持ちます。
  `tools/site.sh` は次の場所を選んで対応する起動コマンドを表示するだけで、
  走っている launch を止めたり立て直したりしません。機体は場所を知りません。
- `config_sentinel` は top-level launch ごとにちょうど 1 つ置き、include される
  launch に見張りを重ねません。見張る範囲は navigation が選んだ場所の
  navigation・localization・perception と使用中プロファイルの明示ファイル、
  mapping が mapping・perception、機体が robot だけです。
  固定された場所の設定が変われば、設定どおり静止を待って終了します。
  関係しない場所選択（`site` の書き換え）では終了しません。
  `SENTINEL_RESTART_CODE` は非ゼロを保ち、通常のノード終了を設定変更と取り違えさせません。
  リンク切れの設定は監視対象から外れるため、起動ログで確認して生成物を整理します。
- 設定変更は sentinel が launch を終了させます。機体は Compose が上げ直しますが、
  手動起動した navigation/mapping は終了したままです。保存前の地図も失い得るので、
  長時間の地図作成では `config_watch:=warn` を使います。
- 自律移動は standalone `vi_planner` だけです。Nav2 の map server・lifecycle・
  velocity smoother と AMCL は共通部品として使います。
  `follow`、`standalone`、`publish_tf` は launch の所有とし、設定へ書きません。
  同じ名前のアクションサーバや TF の出し手を増やさないためです。
- 外部推定器を使うときは VI の `localizer: external`、`localization:=vi` のときは
  内蔵推定器を選びます。内蔵推定器の `pose_topic` は連続入力ではなく手動シードです。
  `mcl_pose` へ向けると毎メッセージで推定を初期化します。
- TF の各区間の所有者は 1 つにします。`map→odom` は選んだ推定器、
  `odom→base_footprint` は駆動ドライバまたは EKF、リンク間は robot_state_publisher です。

## 実機で守ること

- `twist_mux:=true` ではドライバ入力は `/cmd_vel_mux`、自律入力は `/cmd_vel`
  （優先度 100）、手動入力は `/cmd_vel_teleop`（10）です。手動側のゼロ指令や
  `control.sh stop` は自律側が 0.5 秒黙るまで通りません。確実な停止はモータ電源で行います。
- ゲームパッドの teleop は START 長押しで入るモードで、ボタンを離しても続きます。
  入るときに `/follow_waypoints` と `/navigate_to_pose` を取り消しますが、
  自律側の有効期限までは手動が通りません。`twist_mux:=false` では
  `/cmd_vel_teleop` はドライバへ届きません。
- teleop の発行側は終了時にゼロを送ります。自前ドライバは指令断でも
  `cmd_vel_timeout`（60 秒）まで最後の指令を保持します。公式ドライバの指令断時の停止は未確認です。
- Pi 5 は `driver:=original` を使います。rtmouse は動きません。Pi 4 でも
  rtmouse と自前ドライバは排他です。カーネルが GPIO/PWM の競合を検出できず、
  車輪が逆転し得るため、競合検出を回避して走らせません。
- LiDAR ドライバと EKF は常駐する `robot_bringup`、`/scan` の生成は
  navigation/mapping が持ちます。両 launch の `lidar` / `lidar_driver` を揃えます。
  navigation と mapping は TF と `/scan` が重複するため同時起動しません。
  VTC は usim が `/odom` を出すので駆動・EKF を立てず、`lidar:=2d lidar_driver:=false` を渡します。
- `use_mid360_imu` は駆動と EKF を同じ launch で切り替えます。IMU 使用時は
  ドライバが `/wheel/odom`、EKF が `/odom` と TF を出します。
  `lidar:=2d` と `use_mid360_imu:=true` は併用しません。
- Mid-360 の起動時は機体を静止させます。`prepare_mid360_imu` は静止区間で
  ジャイロバイアスを測り、加速度の g を m/s² へ変換します。
  手前の `imu_throttle` を外すと Pi 4 の負荷が増え、停止すると EKF から IMU が消えます。
  レートを変えるときは `bias_samples` が定める静止窓の長さも確認します。
- `IncludeLaunchDescription` へ渡す引数は `GroupAction` で隔離します。
  `lidar_frame` が後続 include へ漏れると、IMU が固定名 `livox_frame` を使う一方で
  TF だけ別名になり、EKF が IMU を捨てます。`/mcl_pose` が出ても `map→odom` は出ない、
  という故障になり得ます。
- 仰角と高さ帯と最大距離は組で変えます。下限
  `lidar_z + 距離 × tan(min_elevation_deg)` が `max_height` を超えると、
  遠方の点群が無言で消えます。emcl2 の距離上限は `/scan` の値で上書きされます。
- `use_composition: false` と `bond_timeout: 60.0` は Pi 4 の
  discovery/bond 形成失敗への対策です。タイムアウト延長は故障検知も遅らせます。
- 駆動ドライバの `finalized` は機体 launch 全体を終了させ、LiDAR/EKF も再起動させます。
  一方、Mid-360 不在で子ノードだけが落ちても launch は残り得ます。
  コンテナの稼働表示だけでなく `/livox/lidar` を確認します。

## VI・地図・表示

- 複数点巡回は `/follow_waypoints` を使います。`navigate_through_poses` はありません。
  ゲームパッド巡回には場所に合う `waypoints_file` が必須です。
- `follow_controller` は `greedy` を使います。DWA/MPPI は行動の速度・角速度の組を
  外接矩形へ崩すため、意図した旋回半径を守れません。
- 行動配列の長さを揃え、前進量・旋回量・smoother 上限を一緒に確認します。
  同じ前進速度の旋回行動は直進と同じ手数単価を保ち、据え切りの角度を対応する
  旋回前進より大きくしません。着地セルだけの判定では、1 手より薄い壁を跳び越え得ます。
- 4 GB 機では `waypoint_prefetch` を無効にします。既定の場所 `19f` も先読みを
  有効にしており、場が 2 本になります。メモリ判定は 1 本単位なので合計 OOM を防げません。
  compact の RAM 閾値はプロセス全体の上限ではありません。詳細は[設定の説明](src/daifuku_config/README.md)。
- `global_sweep_duty` は不用意に上げません。追従と同じ Mutex を握るため、
  コアが空いていても追従が止まります。可視化・先読み・窓の拡大も同じ観点で確認します。
  `early_start` の打ち切り場から外れると、再 solve の間は停止します。
- `tsudanuma_mugimaru` の計画地図は順路から生成した回廊です。順路変更時は地図も再生成し、
  自由セル・連結性・画像の上下方向を検算します。PGM の行 0 は地図の y 最大側です。
  `tools/maps/verify_{map_thresholds,corridor_orientation,waypoints}.py` を使い、
  地図固有の既知事項は[地図の説明](src/daifuku_maps/maps/tsudanuma_mugimaru/README.md)に従います。
- RViz に `Navigation 2` パネルを足しません。同じ `/waypoints` へ MarkerArray を出し、
  自前パネルの Path と型が衝突して設定読み込みが途中で止まります。
  `/waypoints` の表示は Path、点の表示は `/waypoint_markers` です。
  `2D Goal Pose` は `/waypoint_pose` への点追加、単発移動は `Nav2 Goal` を使います。

## Docker と通信

- Compose はリポジトリルートから使います。`COMPOSE_FILE` は
  `compose.common.yaml`、ドライバ別ファイルの順です。逆順は placeholder が勝ちます。
  common 単体で機体を起動せず、ドライバ側から `include` に戻しません
  （サービス上書きが衝突します）。全入口の project 名を揃え、ビルド用ボリュームを共有します。
- ハードウェア無しは `compose.none.yaml` を使い、機体を profile の外へ出しません。
  `workspace-build` に restart を設定すると正常終了でもビルドを繰り返します。
- `.env` はルートと `docker/raspberrypi/` の両方が読まれ、同じキーは後者が勝ちます。
  `COMPOSE_FILE` はルートで指定します。
- Fast DDS の whitelist には読むホスト自身のロボット LAN アドレスが必要です。
  無いと UDP が張られず、無言で他ホストが見えなくなります。
  環境を変えた切り分けには、古い環境を持つ daemon を避けて `ros2 topic list --no-daemon` を使います。

## テストと設定コメント

- `colcon test` の成功と実走行の成功は別です。挙動は実機または VTC で確認し、
  ROS・エンジン不要の VTC 単体テストだけを走行 PASS としません。
- `site_manager` / `config_sentinel` の launch テストは実機ノードと隔離します。
  `ROS_DOMAIN_ID=77` と `ROS_LOCALHOST_ONLY=1` を外しません。
  テストは `daifuku_config` パッケージ内に置きます。
- lint は個別指定を保ち、`ament_lint_common` や pep257 を戻しません。
  移植 C++ の書式と日本語 docstring に合わないためです。`ament_python` では
  `test_depend` だけでなく実行する `test/test_*.py` が必要です。
  CMake パッケージの Python には `ament_cmake_flake8` を使います。
- 新規 `.py` / `.cpp` / `.hpp` の Apache-2.0 ヘッダはファイル先頭のコメント塊に置きます。
- YAML のコメントはキー右の 1 行で `# 既定 <ノード既定値>: <説明>`。
  宣言時の値を既定とし、override の「断片」は重ね先の値です。
  値とコメント・冒頭の要約を同じ変更で揃え、長い理由は[設定の説明](src/daifuku_config/README.md)へ置きます。
