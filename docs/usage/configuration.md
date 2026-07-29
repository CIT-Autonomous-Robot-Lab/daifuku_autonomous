# 設定リファレンス

## 主なファイル

| ファイル | 内容 |
|---|---|
| `config/nav2_params.yaml` | Nav2、価値反復プランナ、コストマップ、速度、ゴール判定 |
| `config/tsudanuma_overrides.yaml` | 広域地図`map_tsudanuma.yaml`用の追加設定（`extra_params_file`で重ねる） |
| `behavior_trees/*.xml` | `planner:=vi`用のビヘイビアツリー（起動時に自動で選択） |
| `config/emcl2_params.yaml` | EMCL2のフレーム、初期姿勢、粒子数、オドメトリモデル |
| `config/lifecycle_bond_params.yaml` | ライフサイクルマネージャのbondタイムアウト |
| `config/slam_toolbox_params.yaml` | SLAM Toolboxのmapping設定 |
| `config/scan_filter.yaml` | LiDARの角度フィルタ |
| `config/MID360_config.json` | Mid-360とホストのIP |
| `config/mid360_scan.yaml` | 3D点群から2D LaserScanへの変換 |
| `config/mid360_ekf.yaml` | Mid-360 IMUと車輪オドメトリの融合 |
| `rviz/mapping.rviz` | 地図作成用RViz |
| `rviz/nav2_default.rviz` | 自律移動用RViz |
| `maps/*.yaml`, `maps/*.pgm` | 保存済み地図 |

すべて`src/autonomous_nav/`以下にあります。

## navigation.launch.py

主な起動引数:

| 引数 | 既定値 | 説明 |
|---|---|---|
| `map` | パッケージ内`maps/map.yaml` | 使用する地図YAMLのフルパス |
| `params_file` | `config/nav2_params.yaml` | Nav2パラメータ |
| `extra_params_file` | 空（無効） | `params_file`へ重ねる追加パラメータ。地図固有の設定用（例`config/tsudanuma_overrides.yaml`） |
| `emcl2_params_file` | `config/emcl2_params.yaml` | EMCL2パラメータ |
| `localization` | `emcl2` | `emcl2` / `emcl` / `amcl` |
| `planner` | `vi` | `vi` / `navfn` |
| `local_planner` | `auto` | `auto` / `vi` / `nav2` |
| `lidar` | `2d` | `2d` / `mid360` |
| `use_rviz` | `true` | RVizを起動するか |
| `use_sim_time` | `false` | シミュレーション時刻を使うか |
| `use_composition` | `False` | Nav2ノードを1プロセスへ合成するか（Pi 4では既定の分離を推奨） |
| `scan_filter_enabled` | `true` | 角度フィルタを使うか |
| `use_mid360_imu` | `true` | Mid-360のIMU融合を使うか |
| `publish_lidar_tf` | `false` | 暫定的なセンサーTFを配信するか |
| `wheel_odom_topic` | `/wheel/odom` | EKFへ渡す車輪オドメトリ |

このほか`namespace`、`use_namespace`、`autostart`、`use_respawn`、`log_level`、各種LiDAR搭載姿勢引数があります。全件は次で確認できます。

```bash
ros2 launch autonomous_nav navigation.launch.py --show-args
```

## mapping.launch.py

主な引数は`slam_params_file`、`rviz_config`、`use_sim_time`、`use_rviz`、`lidar`、スキャンフィルタ、Mid-360関連設定です。

```bash
ros2 launch autonomous_nav mapping.launch.py --show-args
```

## Raspberry Pi 4向けの調整値

実機での負荷試験を受けて、既定値を次のように下げています。PCなど余裕のある環境で
動かす場合は戻して構いません。

| 項目 | 値 | 理由 |
|---|---|---|
| `controller_server.controller_frequency` | `10.0` | 20 HzではPi 4のCPUが飽和し、bond心拍の途絶を招いた。最高速0.2 m/s級の車体には10 Hzで十分 |
| `planner_server.expected_planner_frequency` | `1.0` | 実測7.6 HzしかでずWARNが続いた。BTの再計画周期は1 Hzのため |
| `lifecycle_manager_*.bond_timeout` | `60.0` | 非合成起動では8プロセスが同時に立ち上がりloadが10〜19まで上がるため、既定の4秒ではbond形成が間に合わない |
| `use_composition` | `False` | 合成起動時のディスカバリ不能とbond途絶を回避する |

`planner_server`は`planner:=navfn`のときだけ起動します。

## 自己位置推定の暫定設定

`config/emcl2_params.yaml`のリセット関連は、現在の地図に合わせた**暫定値**です。

| パラメータ | 値 | 従来値 |
|---|---|---|
| `alpha_threshold` | `0.2` | `0.5` |
| `expansion_radius_orientation` | `0.05` | `0.2` |
| `sensor_reset` | `false` | `true` |

有効ビームの28%が地図上の壁を貫通しており、非貫通率（alpha）が0.0〜0.4に張り付く
状態でした。閾値0.5のままでは膨張リセットとセンサーリセットが毎スキャン発動し、
推定姿勢がその場で回転してしまいます。根本原因は地図と実環境の不整合のため、地図を
取り直したあとは既定寄りの値へ戻してください。

## ロボット固有の調整

実機に合わせて、少なくとも次を確認してください。

- footprintとinflation radius
- 最大並進・旋回速度、加速度
- DWBまたは価値反復ローカルプランナの制御パラメータ
- EMCL2 / AMCLのオドメトリノイズモデル
- LiDAR搭載位置と除外角度
- ゴール許容誤差

`vi_global_planner`のソルバ、スレッド数、キャッシュ許容差、経路補間間隔と、`vi_local_planner`の制御周期、局所反復時間などは`nav2_params.yaml`内の各セクションで設定します。
