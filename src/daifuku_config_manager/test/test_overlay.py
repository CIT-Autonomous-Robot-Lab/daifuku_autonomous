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

"""overrides の合成規則。launch も DDS も立てない。"""

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path


def _load_overlay():
    try:
        from daifuku_config_manager import overlay as module
        return module
    except ImportError:
        path = (
            Path(__file__).resolve().parents[1] / "src" / "daifuku_config_manager" / "overlay.py"
        )
        spec = importlib.util.spec_from_file_location("daifuku_overlay", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


_overlay_mod = _load_overlay()
audit_tree = _overlay_mod.audit_tree
check_sections = _overlay_mod.check_sections
config_files = _overlay_mod.config_files
fragment_owners = _overlay_mod.fragment_owners
load = _overlay_mod.load
overlay = _overlay_mod.overlay
reject_unknown_nodes = _overlay_mod.reject_unknown_nodes
select = _overlay_mod.select


class OverlayTest(unittest.TestCase):

    def test_overlay_deep_merges_dicts_and_replaces_lists(self):
        base = {"vi_planner": {"ros__parameters": {
            "map_scale": 1,
            "action_forward_m": [0.5, 0.0],
        }}}
        extra = {"vi_planner": {"ros__parameters": {
            "map_scale": 2,
            "action_forward_m": [0.8],
        }}}
        overlay(base, extra)
        params = base["vi_planner"]["ros__parameters"]
        self.assertEqual(params["map_scale"], 2)
        self.assertEqual(params["action_forward_m"], [0.8])

    def test_check_sections_rejects_unknown_package(self):
        with self.assertRaises(RuntimeError) as caught:
            check_sections({"daifuku_stak": {}}, "overrides:19f")
        self.assertIn("知らないパッケージ名", str(caught.exception))

    def test_check_sections_accepts_site_and_known_packages(self):
        check_sections(
            {"site": {"map": {}}, "daifuku_stack": {"emcl2": {}}},
            "ok",
        )

    def test_select_returns_subtree(self):
        body = {"daifuku_stack": {"emcl2": {"ros__parameters": {"x": 1}}}}
        self.assertEqual(select(body, "t", "daifuku_stack")["emcl2"]["ros__parameters"]["x"], 1)
        self.assertEqual(select(body, "t", "daifuku_bringup"), {})

    def test_reject_unknown_nodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            declared = Path(tmp) / "emcl2.yaml"
            declared.write_text("emcl2:\n  ros__parameters: {}\n", encoding="utf-8")
            reject_unknown_nodes([("ov", {"emcl2": {}})], set(), tmp)
            with self.assertRaises(RuntimeError) as caught:
                reject_unknown_nodes([("ov", {"emcl3": {}})], set(), tmp)
            self.assertIn("emcl3", str(caught.exception))

    def test_config_files_skips_unreadable(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "ok.yaml"
            real.write_text("n: {}\n", encoding="utf-8")
            found, stale = config_files(tmp)
            self.assertEqual([os.path.basename(p) for p in found], ["ok.yaml"])
            self.assertEqual(stale, [])

    def test_fragment_owners_detects_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.yaml"
            b = Path(tmp) / "b.yaml"
            a.write_text("same:\n  ros__parameters: {}\n", encoding="utf-8")
            b.write_text("same:\n  ros__parameters: {}\n", encoding="utf-8")
            with self.assertRaises(RuntimeError) as caught:
                fragment_owners([str(a), str(b)])
            self.assertIn("same", str(caught.exception))

    def test_audit_tree_accepts_repo_config(self):
        root = Path(__file__).resolve().parents[3] / "src" / "daifuku_config"
        if not root.is_dir():
            self.skipTest("src/daifuku_config が無い")
        problems = audit_tree(str(root))
        self.assertEqual(problems, [], "\n".join(problems))

    def test_load_empty_is_dict(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
            handle.write("")
            path = handle.name
        try:
            self.assertEqual(load(path), {})
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
