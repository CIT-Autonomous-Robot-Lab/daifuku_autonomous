修正版です。

# Nav2 Waypoint Manager

RViz2上でWaypointの作成・並べ替え・保存・読込みを行うパネルプラグイン

作成したWaypointを、Nav2の`/navigate_through_poses`アクション
（`nav2_msgs/action/NavigateThroughPoses`）へ送信可能

## Build

```bash
rosdep install --from-paths src --ignore-src -r -y
colcon build --packages-select nav2_waypoint_manager
source install/setup.bash
```

## 使い方

1. Nav2を起動
2. RViz2の**Panels → Add New Panel**から
   `nav2_waypoint_manager/WaypointManagerPanel`を追加
3. RViz2の**2D Goal Pose**を選択し、地図上でクリック・ドラッグ

   * クリック位置をWaypointの座標として使用
   * ドラッグ方向をWaypointの向きとして使用
4. RViz2に`MarkerArray`表示を追加し、topicを`/waypoint_markers`に設定
5. **Start**で登録したWaypointへの巡回を開始
6. **Cancel**で実行中の巡回を停止

## Waypointの保存・読込み

WaypointはYAML形式で保存・読込み可能

YAML読込み時は、以下のいずれかを選択

* 現在のWaypointリストを置換
* 現在のWaypointリストへ追加

追加読込みは、既存Waypointと同一の`frame_id`を持つファイルのみ対象

保存時は一時ファイルへ書き込んだ後に既存ファイルを置換し、保存途中のYAML破損を防止

## RViz2の設定

### 2D Goal Pose

`2D Goal Pose`のtopicを以下に設定

```text
/waypoint_pose
```

本パネルは`/waypoint_pose`を購読し、受信したPoseをWaypointとして追加

### Publish Point

RViz2標準の`Publish Point`が送信する`/clicked_point`にも対応

`Publish Point`からWaypointを追加した場合、向きにはパネル上で設定したYaw値を使用

## 注意事項

RViz2のFixed FrameとWaypointの`frame_id`を一致させる必要あり

Waypointがすでに登録されている状態で異なる`frame_id`のPoseを受信した場合、誤操作防止のため追加対象外
