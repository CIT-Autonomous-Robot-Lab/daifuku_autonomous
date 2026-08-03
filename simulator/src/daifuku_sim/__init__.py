"""autonomous_nav スタックをシミュレータ上で回すためのハーネス (ホスト側)。

- `map_to_usd`     : ROS の占有格子地図 -> Isaac に読ませるワールド USD
- `rtf_gate`       : RTF レポートから「その実行が Pi4 の速度計測として成立しているか」を判定
- `downsample_map` : 占有格子地図の整数倍ダウンサンプル (障害物優先)

`isaac_raspicat` はこのパッケージに同居しているが **import してはいけない**。
モジュール読み込み時に Isaac の `SimulationApp` を起動する (それ以降でしか
`omni.*` / `isaacsim.*` を import できないという Kit の制約による)。
起動は `$ISAACSIM/python.sh <path>/isaac_raspicat.py` で行う。

コンテナの**中**で走るもの (`probe.py` / `fake_robot.py` / `*_case.sh`) はここには
入れない。あちらは ROS 2 Humble の Python 3.10 + rclpy で動き、この venv (3.12) では
`import rclpy` が成立しない。置き場は `simulator/container/`。
"""

__all__ = ["downsample_map", "map_to_usd", "rtf_gate"]
