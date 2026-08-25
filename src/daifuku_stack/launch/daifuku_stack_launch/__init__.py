# Copyright 2026 Keita Sekiguchi / nop
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""daifuku_stack の launch ファイルだけが使う部品。

launch/ の直下に置いてあるので、CMakeLists.txt の
`install(DIRECTORY launch ...)` でそのまま share/daifuku_stack/launch/ へ入る。
colcon の --symlink-install では src/ への symlink になるため、ここを直しても
再ビルドは要らない (launch / config と同じ扱い)。

各 launch ファイルは先頭で launch ディレクトリを sys.path へ入れてから
import する。パッケージ名を長めにしてあるのは、sys.path が他パッケージの
launch ディレクトリと混ざったときにモジュール名が衝突しないようにするため。

**パッケージをまたいで共有するものはここには無い。** 設定の合成 (params) と
launch 引数の小道具 (value / is_true / env_bool_default / env_default) は
daifuku_config_manager にあり、機体側 (daifuku_bringup) と共有している。

  backends.py     localization / planner バックエンドの選択と検証
  nav2_params.py  src/daifuku_config/stack/nav2/*.yaml + vi_planner.yaml の合成と、
                  overrides からの map:= の決定
  scan.py         点群 (または生スキャン) を /scan に変える段の引数と include
"""
