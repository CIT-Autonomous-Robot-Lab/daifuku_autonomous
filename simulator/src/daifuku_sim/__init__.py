"""autonomous_nav スタックを Isaac Sim 上で回すためのハーネス。

- `map_to_usd`  : ROS の占有格子地図 -> Isaac に読ませるワールド USD
- `rtf_gate`    : RTF レポートから「その実行が Pi4 の速度計測として成立しているか」を判定

`isaac_raspicat` はこのパッケージに同居しているが **import してはいけない**。
モジュール読み込み時に Isaac の `SimulationApp` を起動する (それ以降でしか
`omni.*` / `isaacsim.*` を import できないという Kit の制約による)。
起動は `$ISAACSIM/python.sh <path>/isaac_raspicat.py` で行う。
"""

__all__ = ["map_to_usd", "rtf_gate"]
