# Nav2 Waypoint Manager

RViz2のパネルプラグインでWaypointを作成・並べ替え・YAML保存し、Nav2の
`/navigate_through_poses` (`nav2_msgs/action/NavigateThroughPoses`) に送信します。

## Build

```bash
rosdep install --from-paths src --ignore-src -r -y
colcon build --packages-select nav2_waypoint_manager
source install/setup.bash
```

## RViz2で使う

1. Nav2（`nav2_waypoint_follower` を含む）を起動します。
2. RViz2で **Panels → Add New Panel** を選び、
   `nav2_waypoint_manager/WaypointManagerPanel` を追加します。
3. X/Y/Z/Yawを入力して **Add Waypoint** を押すか、RVizのツールバーから
   **2D Goal Pose** を選び、地図上でクリックして向きたい方向へドラッグします。
   クリック位置とドラッグ方向がWaypointの位置・向きとして追加されます。
4. RViz2に `MarkerArray` 表示を追加し、topicを `/waypoint_markers` に設定します。
5. **Start** で巡回を開始し、必要なら **Cancel** で停止します。

YAMLを読込むと、既存リストを置換するか追加するか選べます。追加は同じ
`frame_id` のファイルに限られます。保存は一時ファイルからの原子的な置換を
使うため、保存途中で既存のYAMLを壊しません。

`2D Goal Pose` のtopicは `/waypoint_pose` に設定してください。このパネルは同topicを
購読します。`Publish Point` の標準topic `/clicked_point` も引き続き購読し、その場合は
パネルのYaw値を向きとして使います。
RVizのFixed FrameとWaypointの座標系を一致させてください。既にWaypointがある場合、
異なる `frame_id` のクリックは誤操作防止のため追加されません。
