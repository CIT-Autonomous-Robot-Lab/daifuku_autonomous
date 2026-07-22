# 設定リファレンス

## 主なファイル

| ファイル | 内容 |
|---|---|
| `config/nav2_params.yaml` | Nav2、価値反復プランナ、コストマップ、速度、ゴール判定 |
| `config/emcl2_params.yaml` | EMCL2のフレーム、初期姿勢、粒子数、オドメトリモデル |
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
| `emcl2_params_file` | `config/emcl2_params.yaml` | EMCL2パラメータ |
| `localization` | `emcl2` | `emcl2` / `emcl` / `amcl` |
| `planner` | `vi` | `vi` / `navfn` |
| `local_planner` | `auto` | `auto` / `vi` / `nav2` |
| `lidar` | `2d` | `2d` / `mid360` |
| `use_rviz` | `true` | RVizを起動するか |
| `use_sim_time` | `false` | シミュレーション時刻を使うか |
| `use_composition` | `True` | Nav2ノードをコンポーネント化するか |
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

## ロボット固有の調整

実機に合わせて、少なくとも次を確認してください。

- footprintとinflation radius
- 最大並進・旋回速度、加速度
- DWBまたは価値反復ローカルプランナの制御パラメータ
- EMCL2 / AMCLのオドメトリノイズモデル
- LiDAR搭載位置と除外角度
- ゴール許容誤差

`vi_global_planner`のソルバ、スレッド数、キャッシュ許容差、経路補間間隔と、`vi_local_planner`の制御周期、局所反復時間などは`nav2_params.yaml`内の各セクションで設定します。
