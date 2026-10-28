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

"""ファイルの不正な選択値を拒否しても、次の正常な選択を処理できる。"""

from daifuku_config import params
from daifuku_config.site_manager import validate


def test_invalid_file_selection_does_not_break_next_valid_selection(tmp_path):
    # Given: a next-site file containing a path rather than a site name.
    site_file = tmp_path / "site"
    site_file.write_text("../other\n", encoding="utf-8")
    # When: the same parse/validation boundary as the file watcher is used.
    ok, reason = validate(params.read_site_file(site_file))
    # Then: invalid input is a result, not an exception.
    assert ok is False
    assert reason
    valid = params.available_overrides()[0]
    site_file.write_text(valid + "\n", encoding="utf-8")
    assert validate(params.read_site_file(site_file)) == (True, "")
