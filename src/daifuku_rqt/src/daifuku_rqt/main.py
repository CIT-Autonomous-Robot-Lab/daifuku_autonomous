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

"""Standalone entry point so the panel can be opened without the rqt shell.

`rqt --standalone daifuku_rqt` does the same thing; this exists so
`ros2 run daifuku_rqt daifuku_rqt` also works.
"""

import sys

from rqt_gui.main import Main


def main():
    return Main().main(
        sys.argv, standalone="daifuku_rqt.control_panel.ControlPanel"
    )


if __name__ == "__main__":
    sys.exit(main())
