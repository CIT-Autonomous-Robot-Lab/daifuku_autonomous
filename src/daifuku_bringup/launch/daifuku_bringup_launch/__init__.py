"""daifuku_bringup の launch ファイルだけが使う部品。

launch/ の直下に置いてあるので、CMakeLists.txt の
`install(DIRECTORY launch ...)` でそのまま share/daifuku_bringup/launch/ へ入る。
colcon の --symlink-install では src/ への symlink になるため、ここを直しても
再ビルドは要らない (launch / config と同じ扱い)。

各 launch ファイルは先頭で launch ディレクトリを sys.path へ入れてから
import する。パッケージ名を長めにしてあるのは、sys.path が他パッケージの
launch ディレクトリと混ざったときにモジュール名が衝突しないようにするため。

**パッケージをまたいで共有するものはここには無い。** 設定の合成 (params) と
launch 引数の小道具 (value / is_true / env_bool_default / env_default) は
daifuku_config_manager にあり、自律移動側 (daifuku_stack) と共有している。

  lidar.py  LiDAR 構成の共通引数と include
"""
