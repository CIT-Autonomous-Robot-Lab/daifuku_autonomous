#!/usr/bin/env python3
# Copyright 2026 Keita Sekiguchi / nop
#
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

"""src/daifuku_config を、起動時と同じ規則で検査する (ROS 不要)。

tools/checklist/section-0102-config.sh から呼ぶ。params.overlay.audit_tree が
1 段目のパッケージ名、ノード名の行き先、nav2 断片の重複を見る。
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_overlay():
    # パッケージの __init__.py は launch を import するので、ROS が無い
    # 開発ホストでは overlay.py をファイルとして読む。
    path = ROOT / "src" / "daifuku_config_manager" / "src" / "daifuku_config_manager" / "overlay.py"
    spec = importlib.util.spec_from_file_location("daifuku_overlay", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit_tree = _load_overlay().audit_tree


def main(argv):
    config_dir = argv[1] if len(argv) > 1 else str(ROOT / "src" / "daifuku_config")
    problems = audit_tree(config_dir)
    if problems:
        print("\n".join(problems))
        return 1
    print("1 段目・ノード名・断片の重複は起動時と同じ規則で通る")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
