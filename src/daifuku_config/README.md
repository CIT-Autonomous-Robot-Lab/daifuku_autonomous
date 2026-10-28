# 設定

`daifuku_config` は設定の実体を持ちます。合成と監視の実装は
`daifuku_config_manager`、地図は `daifuku_stack` にあります。
起動・停止・再ビルドの判断は[日常操作](../../docs/usage/operations.md#設定変更を反映する)、
自律移動の手順は[自律移動](../../docs/usage/navigation.md)を参照してください。

## 読む側と変更の単位

| 設定 | 読む側 |
| --- | --- |
| `bringup/robot/` | 常駐する駆動ドライバ、twist_mux、ゲームパッド |
| `bringup/sensors/` | 常駐する LiDAR ドライバ、IMU 前処理、EKF |
| `stack/nav2/*.yaml` と `stack/vi_planner.yaml` | navigation の `params_file` へ合成 |
| `stack/localization/` | 選択した自己位置推定器 |
| `stack/lifecycle_bond.yaml` | navigation の lifecycle 管理 |
| `stack/mapping/` | mapping の SLAM Toolbox |
| `stack/sensors/` | navigation/mapping が立てる `/scan` 生成段 |
| `overrides/*.yaml` | 場所ごとの地図・スキャン・推定器・VI の調整 |
| `site` | 現在の場所名 1 語 |

機体は場所を知りません。場所や `stack/` の設定を変えたら navigation/mapping を
立て直し、`bringup/` を変えたら常駐する機体側も読み直します。
設定変更で `config_sentinel` が launch を終了させる点、新規ファイルにはビルドが
必要な点を含め、反映の判断表は上記の日常操作にまとめています。

自律移動は standalone `vi_planner` が担います。`nav2/` にあるのは共通部品の設定で、
Nav2 の map server・velocity smoother・lifecycle 管理と、選択時の AMCL は引き続き使います。
巡回の失敗時動作・待ち時間は VI の `stop_on_failure`、`goal_retry_limit`、
`waypoint_pause_sec` で設定します。

## 合成と上書き

`stack/nav2/*.yaml` をファイル名順に読み、`stack/vi_planner.yaml` と合わせて
`params_file` を作ります。断片はノード単位で分け、同じノード名を 2 ファイルに
置きません。キーが重ならなくても起動時に止まります。断片間の深いマージはしません。
VI の断片を合成から外すと値が渡らず、override の行き先も失われます。

上書きの優先順位は次の順で、後勝ちです。

1. 各 launch の土台。navigation は上記の合成結果、`params_file:=` 指定時はそのファイル。
2. `overrides:=<名前>`。カンマ区切りで複数指定でき、列挙順に重ねます。
3. `extra_params_file:=<パス>`。一時的な上書き用です。

override はパッケージ名、ノード名、`ros__parameters` の形で書き、
同じノードを持つ設定へ深くマージします。

```yaml
site:
  map:
    navigation: 19f/map_19f.yaml
    localization: 19f/map_19f.yaml

daifuku_stack:
  elevation_filter:
    ros__parameters:
      min_elevation_deg: 0.0
  vi_planner:
    ros__parameters:
      waypoint_prefetch: false
```

- 各 launch は `daifuku_bringup:` / `daifuku_stack:` の自分の部分木だけを読みます。
  知らないパッケージ名と、そのパッケージの設定に存在しないノード名は起動エラーです。
  既知だがその launch が使わないノードの節は適用されません
  （mapping に渡した `emcl2:` など）。
- `params_file:=` で土台を替えても、上書き先として認めるノード名は断片から決まります。
- `MID360_config.json` は ROS パラメータ形式ではないため上書き対象外です。
- 既存の場所別 override はキーの集合を揃えています。断片同値の行も、
  場所ごとの差を比較するため残します。新しい override へ土台全体を複製する必要はありません。
- `SetParameter` / `SetParametersFromFile` よりノード個別の `parameters=` が後勝ちです。
  このため override はファイルそのものをマージします。`lifecycle_bond.yaml` の注入を
  効かせるには、各断片へ同じ `bond_timeout` を追加しません。

起動ログの `params:` は土台・上書き先・生成した一時ファイルを示します。
効かないときはこのログ、ノード名、`ros__parameters` の段、ノードが宣言しているキーかを
確認します。未宣言のキーは ROS 2 が黙って無視し得ます。

## 場所と地図

場所の変更は `tools/site.sh <名前>`、または起動中なら
`ros2 param set /site_manager site <名前>` で行います。停止中は `site` の場所名を
書き換えます。反映には navigation/mapping の再起動が必要で、機体の再起動は不要です。
`OVERRIDES` 環境変数が残っているとファイルより優先されます。

`overrides:=` は場所の既定を置き換えます。上書き無しは空文字ではなく
`overrides:=none` です。このとき、または `site.map` が無いときは `map:=` が必須で、
既定の場所の地図へは落としません。VTC の生成地図も `overrides:=none` を明示します。

`site.map` のパスは `daifuku_stack` の `maps/` からの相対パスか絶対パスです。

| 役 | launch 引数 | 用途 |
| --- | --- | --- |
| `navigation` | `map` | `/map`。VI の計画用。進入禁止区域を塗った地図を使える |
| `localization` | `map_loc` | `/map_loc`。実測のままの自己位置推定用 |
| `pointcloud`（任意） | `map_pcd` | `localization:=emcl3d` の PCD 地図 |

`site.map` を書くなら 2D の 2 役は同じ地図でも両方明記します。
明示した `map:=` / `map_loc:=` は、その役の宣言と同じファイルでなければ起動できません。
場所別宣言が無く `map:=` だけを渡した場合、`map_loc` はその地図を使います。
emcl2 系は別々の 2D 地図を使えます。AMCL と VI 内蔵推定は同一地図に限られます。
`map_server_loc` に設定節を足しません。地図パスは launch が直接渡し、
キー名で書き換える `RewrittenYaml` が両方を同じ地図にするのを避けます。
emcl3d は `/map_loc` を読まないため 2D 地図が別でも起動でき、
自己位置推定用の PCD が解決できなければ起動を止めます。

`tsudanuma` と `tsudanuma_mugimaru` は原点・解像度の違う別地図で、
ウェイポイントを共用できません。後者の計画地図は順路から生成した 8.5 m の回廊なので、
順路を変えたら `tools/maps/corridor_map.py` で再生成します。
全点の自由セル・連結性・上下方向を検算してください。点が自由セルでも回廊が壁で
分断されることがあり、この順路の半径下限は 8.0 m です。PGM の行 0 は y 最大側です。
既知の壁内 waypoint や地図閾値は
[地図の説明](../daifuku_stack/maps/tsudanuma_mugimaru/README.md)と
`tools/maps/verify_{map_thresholds,corridor_orientation,waypoints}.py` で確認します。

## コメントと設定の所有者

キー右に `# 既定 <値>: <説明>` を 1 行で書き、既定から変える理由を添えます。
「既定」はノード実装の宣言時の値、「断片」は override の重ね先の値です。
上流のサンプル設定をノード既定とは呼びません。出典は各 YAML の冒頭に置き、
値を変えたらコメントと要約も更新します。未設定キーの一覧も同じ書式で残します。

`follow`、`standalone`、`publish_tf` は launch が渡すため YAML には書きません。
`localization:=vi` は内蔵の `localizer`、それ以外は `localizer: external` と組にします。
内蔵推定では `pose_topic` が手動シードとなるため、launch が `initialpose` へ向けます。
外部推定の `mcl_pose` を入れ続けると毎回初期化されます。
名前空間を使う場合は RViz の `/initialpose` と相対名のシード入力が一致するかも確認します。
内蔵推定の `active_reloc` は密ソルバ専用で、外部推定用の場所別設定へ戻しません。

## 駆動と停止の理由

- 標準の自前ドライバは車輪径 0.200 m、トレッド 0.350 m、
  `pulses_per_revolution: 1073`（odom）と `steps_per_revolution: 447`（速度指令）を
  分けています。基板の指令とエンコーダは別単位で、径を歪めて補うと旋回を壊します。
  odom の較正は巡航帯に合わせており、全速度に一致する値ではありません。
  詳細と配線の未検証点は[ドライバ](../raspicat_driver/README.md)を参照してください。
- 自前実装の `odom_hz: 50` は I2C 負荷を抑える値です。カウンタが連続失敗すると
  `cmd_vel` 積分へ落ちるため、モータ OFF の回転指令で `/wheel/odom` が動かないか確認します。
  `publish_tf` は EKF 使用の有無から launch が決めます。
- `buzzer_pwm_channel: -1` は意図的です。GPIO19 と右モータが PWM チャネルを共有するため、
  ハード PWM にするとブザー操作で車輪が回り得ます。Pi 4 の競合指定も拒否します。
- 公式ドライバの `use_pulse_counters: false` はカウンタ I2C の固着でノードが D 状態に
  なり、再起動以外で復旧できない個体への回避です。代償は指令積分の幻の odom です。
  走行終了時に必ずゼロ指令を届けます。「Testing counters」のログは read 成功の証拠ではありません。
  公式実装は指令換算の 400 が固定なので、正しい径でも指令速度と実速度は一致しません。
- `bringup/robot/raspicat.yaml` は上流設定の完全コピーです。include の外から差分を
  注入してもノード個別パラメータに負けるため、この形を取っています。

### `twist_mux.yaml` の配線と優先度

速度指令は VI → `/cmd_vel_nav` → velocity smoother → `/cmd_vel` →
twist_mux → `/cmd_vel_mux` → ドライバです。smoother 無効時は VI が `/cmd_vel` へ直接出します。
手動は `/cmd_vel_teleop` で、優先度は自律 100、手動 10、自律の有効期限は 0.5 秒です。
したがって `control.sh stop` は自律走行中の停止手段ではありません。
ゲームパッドは teleop 開始時にゴールを取り消しますが、切替直後の待ちは残ります。
確実に止めるにはモータ電源を切ります。

twist_mux や発行側が止まっても、自前ドライバは最後の指令を最大 60 秒保持します。
teleop 終了時はゼロを送ってください。仲裁を外すときはドライバ入力と手動出力を
両方 `/cmd_vel` に合わせます。配信元の無い `locks` は期限切れで閉じ続けるため追加しません。

## スキャン・IMU・自己位置推定

- Mid-360 のジャイロバイアスは EKF が推定しないため、起動時に静止して測ります。
  `imu_throttle` は Python 前処理の受信負荷を抑えます。`bias_samples` の時間長は
  間引き後レートで決まります。前処理・throttle が止まると、EKF は IMU 無しでも回り得ます。
- 仰角フィルタの下限は `lidar_z + 距離 × tan(min_elevation_deg)` です。
  `max_height` と `range_max` を組で確認します。仰角 5 度なら 70 m 先の下限は約 6.4 m で、
  上限がそれより低いと帯が消えます。仰角 0 度は搭載高の水平帯で、坂の床を除けません。
- `emcl2.open_space_threshold` は未宣言で効きません。`laser_min_range` /
  `laser_max_range` は毎スキャンの範囲で上書きされるため、`mid360_scan.yaml` を変更します。
  ROS 2 の EKF は `odom0_nodelay` / `imu0_nodelay` を使いません。
  `sensor_reset: false` はノード宣言値に合わせたもので、上流 README の表とは異なります。
- emcl2 の `odom_freq: 20` は TF の更新頻度も兼ねます。10 Hz のスキャンを
  同じ観測の重複チェック無しで 2 回処理するため、周期を変えると重み付けも変わります。
- emcl2 の運動モデルを小さくしすぎると粒子の多様性が失われ、ずれから戻れません。
  現在の調整値は実機未検証です。カウンタ、粒子群、`ALPHA` / `RESET`、TF 遅延を確認します。
  リサンプリング後の重み保持と観測・odom の時刻差は外部実装の問題で、設定だけでは直りません。
- `laser_likelihood_max_dist: 1.0` は地図の小さなずれを許す一方、貫通検知も鈍らせます。
  幅 2 m 未満の通路では零尤度セルが無くなり、`ALPHA: 1.000` に張り付いて
  検知が止まり得ます。その場合は幅を狭めて再確認します。
  場所別の `alpha_threshold` と `expansion_radius_orientation` も対症療法なので、
  地図を取り直したら既定へ戻して評価します。広げた尤度幅も含め、走行性能は未検証です。
- `bond_timeout: 60.0` は Pi 4 高負荷時の形成待ちを許容するためで、
  心拍停止の検知も遅くなります。非コンポジション起動も discovery/bond の失敗を避ける選択です。

## VI の行動と追従

`action_names`、`action_forward_m`、`action_rotation_deg` は同じ長さにします。
前進量と回転量は 1 手の変位であると同時に、速度指令の値です。
1 手のコストは距離に依存しないので、同じ前進速度の直進と旋回前進を揃え、
据え切りを対応する旋回前進より安くしないよう角度を揃えます。
`follow_controller: greedy` を使ってください。DWA/MPPI は速度・角速度の組を保たず、
意図した旋回半径より小さい指令を選び得ます。

行動を変えるときは次も確認します。

- velocity smoother の上限で黙ってクリップされないこと。土台の前進上限は 0.4 m/s で、
  VI の前進 0.5 を下回ります。場所別 override が上限を変えるので、実効値で確認します。
  前進と回転が別々にクリップされると旋回半径も変わります。
- 加減速の上限で基板が飽和しないこと。指令への遅れや直進の曲がりは実機で確認が必要です。
- 着地セルだけを見る遷移では 1 手より薄い壁を跳び越え得ること。
  膨張だけで安全が保証されるわけではありません。
- ゴール半径と 1 手の距離の釣合い。0.3 m 半径に 0.5 m の手では直径との差は 0.1 m です。
  ゴール半径の拡大は実距離の許容差を緩める変更です。
- 追従パッチ内に遷移が収まること。起動時の `action_forward_m is too large` を無視しません。

### 地図ごとの解像度とメモリ

| 場所 | 元の解像度 | `map_scale` | 内部解像度 | ソルバ | 場 1 本の目安 |
| --- | --- | --- | --- | --- | --- |
| `19f` | 0.05 m | 2 | 0.10 m | 密 | 約 655 MB |
| `tsudanuma` | 0.05 m | 5 | 0.25 m | compact | sink 約 648 MB |
| `tsudanuma_mugimaru` | 0.10 m | 2 | 0.20 m | compact | sink 約 711 MB |

メモリ値は場の目安で、プロセス全体のピークではありません。
`map_scale` は VI 内部だけに効き、配信地図や外部推定器の解像度は変えません。
大きくすれば状態数は減りますが、1 手が跨ぐセル数も減って反復が増えるため、
solve の高速化は保証されません。計画用の自由空間を絞るほうが効く場合があります。

津田沼は `map_scale: 5` と `downsample_policy: optimistic` が組です。
保守的縮小では通路が潰れ、波が広がりません。楽観側も壁を削るため実際に通れるか確認します。
`tsudanuma_mugimaru` は保守的縮小のままで、scale 3 へ上げません。
未観測の少ない地図では `unknown_as_obstacle` は進入範囲の制限になりません。
津田沼の `vi_threads: 3` は追従・推定器に 1 コアを残すためです。
狭い通路で `safety_radius_penalty` を大きくすると隣接状態の値の揺れが 1 手の進捗を超え、
貪欲な経路生成が `LoopDetected` になり得るため、場所別に小さくしています。

compact の `compact_ram_limit_mb: 4096` は sink を RAM に置く閾値で、プロセス全体の上限では
ありません。`compact_sink_dir` を指定するとそちらが優先されます。自動退避先の `/tmp` は
コンテナの SD 書き込み層なので、追従周期に響きます。4 GB 機では閾値を 2048 以下へ見直すか、
速い実ディスクを明示します。密はディスク退避せず `MemAvailable` で起動可否を判定します。

`waypoint_prefetch` は 19F と mugimaru で有効です。場が 2 本になるため、
**4 GB 機では無効にしてください**。メモリ判定は 1 本単位で、合計 OOM は防げません。
無効化のために `overrides:=none` を使うと必要な自己位置調整まで失うので、
その場所のキーを `false` にします。1 点目は必ず solve を待ち、次点も走行中に解き終わった
場合だけ待ちが消えます。compact の先読みスレッドは mugimaru で 2、
Pi 5 の 4 コアで 3 以上にはしません。現在の回廊・行動での実機時間と安定性は未検証です。

`early_start` は compact の値バンドが地図全体を覆うと短縮になりません。
打ち切り場から外れると再 solve の間は停止します。`cut short` / `truncated` と
`dropped the truncated value function` を確認します。

### 追従窓と全域伝播

窓は capsule、前後の基準半径 2.5 m、横半径 2.0 m、伸縮上限 1.25 です。
前後半軸の和を 5 m に保ち、前方は最大 3 m、後方も最低 2 m を確保します。
楕円では前縁の横被覆が落ちるため capsule を選んでいます。形は凸を保ちます。

- `refine_budget_ms: 40` で掃き切れない場合、次 tick も地図 x 最小側から再開し、
  x 最大側が更新されません。19F の窓は約 99,000 状態で、Pi 4 の速度による
  見積もりでは約 4 割しか掃けません。予算か窓を見直す必要があり、実時間は未測定です。
- 津田沼は 1 tick の移動量が変形の不感帯以下で、窓が静止形のままになり得ます。
- compact の追従パッチは伸縮上限込みの外接半径で決まり、面積に比例して増えます。
  起動ログの `follow patch` で実寸を確認します。

#### 効いているか確かめる

`global_sweep` はスキャンが変えたコストを全域へ伝播します。密はセル、compact は
タイル単位の能動集合で、変化が及ぶ範囲を更新します。無効化すると広域計画が
塞がった通路を指し続けます。実機の収束時間は未測定で、
`global propagation running` / `settled` のログで確認します。

追従と伝播は同じ Mutex を使い、追従が 3 tick 続けて取れないと停止します。
`global_sweep_duty` は 25 のままとし、コアが空いている理由だけで増やしません。
走行中の `/cmd_vel_mux` の周期も確認します。
`value_publish_interval_ms` は正なら定期配信、0 なら収束時のみ、負なら表示を無効化します。
大きい地図の描画もロックを握るため、周期的な停止ではまず配信を減らして切り分けます。
`cost_drawing_threshold` は全域と窓で共通の色スケールで、経路には影響しません。

窓外の `local_penalty` はそのゴールの間残ります。通れない場所を覚える反面、
スキャンの誤検知も残るので、消すときはゴールを取り直します。
通路の一部だけを塞いでも迂回できれば遠方の値はほぼ変わりません。
表示が変わらないだけで伝播の故障と判断しないでください。
