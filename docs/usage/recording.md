# 走行を記録して再生する

自律走行が破綻したとき、その場で`ros2 topic echo`を並べても間に合いません。走行中の
トピックを`ros2 bag`に録っておき、あとから別の環境で再生して突き合わせます。

この機体では落とし穴が4つあります。**点群を録らない**、**隠しトピックを明示する**、
**bagの置き場**、**再生時のQoSと`--clock`**。順に説明します。

## 何を録るか

**`-a`（全トピック）は使いません。** `/livox/lidar`はPointCloud2が10 Hzで流れるので、
Piの空き（1.5GB程度）を数分で埋めます。SDへの書き込みはビルドでも律速になっている
ので、記録そのものが走行を乱します。

**代わりに`/scan`を録ります。** `emcl2`もcostmapも食っているのは`/scan`のほうなので、
自己位置と経路の破綻は点群が無くても追えます。

| トピック | 何が分かる |
|---|---|
| `/rosout` | **全ノードのログ**。データと時刻が揃うので、まずこれを読む |
| `/scan` | `emcl2`とcostmapが見ていたもの。スキャンが壁を貫通していないか |
| `/tf` `/tf_static` | `map→odom`（`emcl2`）と`odom→base_footprint`（EKF）の飛び |
| `/odom` `/wheel/odom` | EKFの出力と車輪だけの出力。差が開くならIMU融合を疑う |
| `/map` | 地図。latchなので1回だけ乗る（22.5MB） |
| `/mcl_pose` `/particle_cloud` | `emcl2`の推定姿勢とパーティクル。膨張リセットの様子 |
| `/cmd_vel` `/cmd_vel_nav` `/cmd_vel_teleop` `/cmd_vel_mux` | **4本とも**。仲裁の取り合いは既知の破綻要因なので、どれが勝っていたかは並べないと分からない。`/cmd_vel_nav`は`velocity_smoother`を挟んだときだけ居る（`velocity_smoother:=false`では`vi_planner`が`/cmd_vel`へ直接出す） |
| `/plan` `/value_function` `/local_window_value` | 価値反復が引いた経路と価値関数 |
| `/waypoints` `/waypoint_pose` | 順路（latch）と単発ゴール |
| `*/_action/status` | ゴールがABORTEDになった経緯。**ここにしか出ない** |
| `/diagnostics` | ノードの自己申告 |

構成によって居るトピックが変わります。既定の`nav2:=false` / `planner:=vi`では
`bt_navigator`もcostmapも立たないので、**Nav2前提の一覧をそのまま書くと、その分は
黙って何も録れません**。録る前に実際の一覧を見てください。

```bash
bash docker/raspberrypi/tools/shell.sh   # コンテナへ入る
ros2 topic list
```

LiDAR側のスタンプを疑うときは`/scan_raw`と`/scan_mid360_prestamp`
（[トラブルシューティング](troubleshooting.md#mid-360のスキャンが古すぎると拒否される)）、
IMU融合を疑うときは`/livox/imu`と`/imu/mid360`も足します。どれも軽いトピックです。

## 記録する

走行の前に、コンテナの中で起動します（tmuxの窓を1枚使うと扱いやすい）。

```bash
mkdir -p /tmp/bags
ros2 bag record -o /tmp/bags/run1 --include-hidden-topics \
  --max-bag-size 200000000 --max-cache-size 100000000 \
  /rosout /diagnostics \
  /scan /tf /tf_static /odom /wheel/odom /map \
  /mcl_pose /particle_cloud \
  /cmd_vel /cmd_vel_nav /cmd_vel_teleop /cmd_vel_mux \
  /plan /value_function /local_window_value \
  /waypoints /waypoint_pose \
  /compute_path_to_pose/_action/status \
  /follow_waypoints/_action/status
```

- **`--include-hidden-topics`が無いと`*/_action/status`は名前で指定しても録れません。**
  隠しトピック（`_`で始まる要素を含む名前）は、明示しても黙って外れます。ゴールが
  中断した理由だけが抜けたbagになるので、`ros2 bag info`のTopic informationにも
  現れません（Humbleで実測。フラグ無し58件＝`/chatter`のみ、有り116件＝両方）。
- **居ないトピックを並べてもエラーになりません。** `record`は自動探索を続けて待つだけ
  なので、綴り違いや構成違いは`ros2 bag info`で数えるまで気付けません。
- `--max-bag-size`（バイト）でファイルを分割します。長く走らせるときは
  `--max-bag-duration 300`（秒）でも切れます。
- Piに余裕があれば`--compression-mode file --compression-format zstd`。走行中のCPUは
  そのぶん食います。
- **止めるのは`Ctrl-C`を1回だけ。** 強制終了すると`metadata.yaml`が書かれず、そのままでは
  読めません（`ros2 bag reindex /tmp/bags/run1`で作り直せます）。`ros2`コマンドを
  何度も中断すると、そのノードだけDDSから見えなくなることもあります
  （[トラブルシューティング](troubleshooting.md#ros2コマンドを何度か中断したあとそのノードだけ届かなくなる)）。

`docker compose exec`から直接叩く形なら次のとおりです。

```bash
docker compose exec ros2 /ros_entrypoint.sh \
  ros2 bag record -o /tmp/bags/run1 --include-hidden-topics /rosout /scan /tf /tf_static
```

### 破綻した瞬間だけ残す

`--snapshot-mode`を付けると、記録は`--max-cache-size`のバッファに溜まるだけになり、
サービスを叩いた時点で溜まっているぶんだけがディスクへ落ちます。長く走らせても
SDを食わないので、空きが乏しいときはこちら。**バッファを超えた古いぶんは捨てられます。**

```bash
ros2 bag record --snapshot-mode -o /tmp/bags/snap --include-hidden-topics \
  --max-cache-size 200000000 /rosout /scan ...
ros2 service call /rosbag2_recorder/snapshot rosbag2_interfaces/srv/Snapshot
```

## bagを取り出す

`compose.common.yaml`がマウントしているのは`src/`と名前付きボリュームだけで、
**bagを書けるホストのパスがありません**。`/tmp`はコンテナを作り直す（`down`、recreate）と
消えるので、録ったらその場で出します。

```bash
docker compose cp ros2:/tmp/bags/run1 ./run1
ros2 bag info run1
```

`info`で**`/map`と`/tf_static`のcountが1以上**あることを確認してください。0なら記録側で
QoSが噛み合っておらず、そのbagからはTFツリーも地図も組み直せません。

`/opt/ros_ws/src/`の下へ直接書けば持ち出しは要りませんが、**gitの作業ツリーに落ちます**。
常用するなら`compose.common.yaml`の`ros2`サービスへ1行足すほうが素直です。

```yaml
      - ../../bags:/opt/ros_ws/bags
```

## 再生する

**実機の隣で流さないでください。** `raspicat`サービスは`restart: unless-stopped`で常に
`/tf`・`/odom`・`/scan`を出しています。同じドメインへbagを流すと`odom→base_footprint`が
二重配信になり、TFの区間ごとに所有者を1つにするという約束が破れて
**自己位置だけが静かに壊れます**。想定している流れは
[`docker/dev/README.md`](../../docker/dev/README.md)にあるとおり「Piで録って開発環境で再生」です。
Piで再生するなら先に`docker compose stop raspicat`するか、`ROS_DOMAIN_ID`を分けてください。

再生には**QoSの上書きが要ります**。`/map`と`/tf_static`はtransient_local（latch）ですが、
`ros2 bag play`は既定でvolatileとして流すので、**あとから起動したRVizは地図も静的TFも
一生受け取りません**（エラーは出ず、ただ何も映らない）。

```yaml
# qos_override.yaml
/map:
  durability: transient_local
  reliability: reliable
  history: keep_last
  depth: 1
/tf_static:
  durability: transient_local
  reliability: reliable
  history: keep_last
  depth: 100
```

`/waypoints`や`/value_function`もlatchなので、それらを見たいときは同じ形で足します。

```bash
ros2 bag play run1 --clock --qos-profile-overrides-path qos_override.yaml
```

**`--clock`も要ります。** bagの時刻は過去なので、これを出さないとTFもRVizも「古すぎる」で
全部捨てます。受け手には`use_sim_time:=true`を渡してください。

```bash
ros2 run rviz2 rviz2 \
  -d $(ros2 pkg prefix daifuku_stack)/share/daifuku_stack/rviz/navigation.rviz \
  --ros-args -p use_sim_time:=true
```

## 破綻箇所を絞る

```bash
ros2 bag play run1 --topics /rosout --clock          # まずログだけ通しで読む
ros2 bag play run1 -r 0.2 --start-offset 120 \
  --clock --qos-profile-overrides-path qos_override.yaml
ros2 bag play run1 -p --clock ...                    # 一時停止で始める
```

`/rosout`を通しで読んで時刻を掴み、`--start-offset`でそこへ飛び、`/tf`と`/cmd_vel*`と
action statusを突き合わせる、という順が早く済みます。一時停止と1メッセージ送りは
サービスからも叩けます。

```bash
ros2 service call /rosbag2_player/toggle_paused rosbag2_interfaces/srv/TogglePaused
ros2 service call /rosbag2_player/play_next rosbag2_interfaces/srv/PlayNext
```

症状別の読み方は[トラブルシューティング](troubleshooting.md)にそろっています。

## ノードを再実行する

録った`/scan`と`/odom`から`emcl2`や`vi_planner`を立て直して追うこともできます。その場合、
**bagの`/tf`には`map→odom`が入っている**ので（`navigation.launch.py`の`/tf`→`tf`は
相対名へのremapなので、`namespace:=`を付けていない既定の構成では`/tf`に出ます）、
そのまま流すと立て直した`emcl2`と二重になります。`--topics`で`/tf`を外すか、
`--remap /tf:=/tf_bag`で別の名前へ逃がしてください。

```bash
ros2 bag play run1 --clock --qos-profile-overrides-path qos_override.yaml \
  --topics /scan /odom /tf_static /map
```

まずは記録の再生だけで見えることが多いので、こちらは必要になってからで構いません。
