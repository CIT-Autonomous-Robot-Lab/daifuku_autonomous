"""daifuku_stack の launch ファイルが共有する部品。

launch/ の直下に置いてあるので、CMakeLists.txt の
`install(DIRECTORY launch ...)` でそのまま share/daifuku_stack/launch/ へ入る。
colcon の --symlink-install では src/ への symlink になるため、ここを直しても
再ビルドは要らない (launch / config と同じ扱い)。

各 launch ファイルは先頭で launch ディレクトリを sys.path へ入れてから
import する。パッケージ名を長めにしてあるのは、sys.path が他パッケージの
launch ディレクトリと混ざったときにモジュール名が衝突しないようにするため。

  params.py    nav2 / emcl2 のパラメータ合成 (navigation)
  backends.py  localization / planner バックエンドの選択と検証 (navigation)
  lidar.py     LiDAR 構成の共通引数と include (navigation / mapping / lidar_bringup)
"""

from launch.substitutions import LaunchConfiguration


def value(context, name):
    """起動引数を文字列として取り出す (OpaqueFunction の中で使う)。"""
    return LaunchConfiguration(name).perform(context)


def is_true(context, name):
    """起動引数を真偽値として取り出す。"true"/"True" のどちらでも受ける。"""
    return value(context, name).lower() == "true"
