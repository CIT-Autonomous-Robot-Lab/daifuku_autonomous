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

"""選んだ構成だけが変更・検査の対象になる。ROS 不要。"""

from pathlib import Path

from daifuku_config import monitoring, paths
import pytest
import yaml


@pytest.fixture
def tree(tmp_path, monkeypatch):
    for name, subdir in paths.CONFIG_DIRS.items():
        directory = tmp_path / subdir
        directory.mkdir()
        (directory / "params.yaml").write_text(
            yaml.safe_dump({name: {"ros__parameters": {"value": 1}}}), encoding="utf-8",
        )
    for site in ("selected", "other"):
        directory = tmp_path / "sites" / site
        directory.mkdir(parents=True)
        (directory / "overrides.yaml").write_text(yaml.safe_dump({
            "site": {"map": {"navigation": "a.yaml", "localization": "a.yaml"}},
            **{name: {name: {"ros__parameters": {"value": 2}}}
               for name in paths.CONFIG_DIRS},
        }), encoding="utf-8")
    monkeypatch.setattr(paths, "_share", lambda: str(tmp_path))
    return tmp_path


def scope(*packages, files=()):
    package = packages[-1]
    return {
        "site": "selected", "package": package, "config_root": paths.config_root(package),
        "packages": packages, "files": [str(path) for path in files],
    }


def change(path, body):
    path.write_text(yaml.safe_dump(body), encoding="utf-8")


@pytest.mark.parametrize("unselected", ["mapping", "robot"])
def test_navigation_ignores_unselected_function_roots(tree, unselected):
    selected = scope("daifuku_perception", "daifuku_localization", "daifuku_navigation")
    before = monitoring.config_digest(**selected)
    change(tree / unselected / "params.yaml", {"changed": {"value": 9}})
    assert monitoring.config_digest(**selected) == before


@pytest.mark.parametrize("selected", ["perception", "localization", "navigation"])
def test_navigation_notices_each_selected_function(tree, selected):
    watched = scope("daifuku_perception", "daifuku_localization", "daifuku_navigation")
    before = monitoring.config_digest(**watched)
    change(tree / selected / "params.yaml", {"changed": {"value": 9}})
    assert monitoring.config_digest(**watched) != before


def test_robot_ignores_site_selection_and_other_sites(tree):
    watched = scope("daifuku_robot")
    watched["site"] = "none"
    before = monitoring.config_digest(**watched)
    (tree / "site").write_text("other\n", encoding="utf-8")
    (tree / "sites" / "other" / "overrides.yaml").write_text("broken: [", encoding="utf-8")
    assert monitoring.config_digest(**watched) == before
    assert monitoring.precheck(**watched) == (True, "")


def test_mapping_ignores_navigation_and_localization_overlays(tree):
    watched = scope("daifuku_perception", "daifuku_mapping")
    before = monitoring.config_digest(**watched)
    overlay = tree / "sites" / "selected" / "overrides.yaml"
    body = yaml.safe_load(overlay.read_text(encoding="utf-8"))
    body["daifuku_navigation"]["daifuku_navigation"]["ros__parameters"]["value"] = 44
    body["daifuku_localization"] = {"unknown_node": {}}
    change(overlay, body)
    assert monitoring.config_digest(**watched) == before
    assert monitoring.precheck(**watched) == (True, "")


def test_selected_overlay_and_metadata_changes_are_detected(tree):
    watched = scope("daifuku_perception", "daifuku_mapping")
    before = monitoring.config_digest(**watched)
    overlay = tree / "sites" / "selected" / "overrides.yaml"
    body = yaml.safe_load(overlay.read_text(encoding="utf-8"))
    body["site"]["map"]["navigation"] = "changed.yaml"
    change(overlay, body)
    assert monitoring.config_digest(**watched) != before


def test_fixed_site_overlay_changes_are_detected(tree):
    watched = scope("daifuku_perception", "daifuku_mapping")
    before = monitoring.config_digest(**watched)
    overlay = tree / "sites" / "selected" / "overrides.yaml"
    body = yaml.safe_load(overlay.read_text(encoding="utf-8"))
    body["daifuku_perception"]["daifuku_perception"]["ros__parameters"]["value"] = 23
    change(overlay, body)
    assert monitoring.config_digest(**watched) != before


def test_selected_profile_ignores_other_sites(tree):
    watched = scope("daifuku_perception", "daifuku_localization", "daifuku_navigation")
    before = monitoring.config_digest(**watched)
    (tree / "sites" / "other" / "overrides.yaml").write_text("broken: [", encoding="utf-8")
    assert monitoring.config_digest(**watched) == before
    assert monitoring.precheck(**watched) == (True, "")


def test_robot_json_configuration_changes_are_detected(tree):
    path = tree / "robot" / "livox.json"
    path.write_text('{"lidar_configs": [{"ip": "192.168.1.1"}]}', encoding="utf-8")
    watched = scope("daifuku_robot")
    before = monitoring.config_digest(**watched)
    path.write_text('{"lidar_configs": [{"ip": "192.168.1.2"}]}', encoding="utf-8")
    assert monitoring.config_digest(**watched) != before


@pytest.mark.parametrize("filename", [
    "sites/selected/navigation_scan.yaml",
    "sites/selected/localization_scan.yaml",
    "custom.yaml",
])
def test_explicit_inputs_are_fingerprinted_and_prechecked(tree, filename):
    path = tree / filename
    change(path, {"scan": {"ros__parameters": {"bands": [1, 2, 3]}}})
    watched = scope("daifuku_navigation", files=[path])
    before = monitoring.config_digest(**watched)
    change(path, {"scan": {"ros__parameters": {"bands": [3, 2, 1]}}})
    assert monitoring.config_digest(**watched) != before
    path.write_text("scan: [", encoding="utf-8")
    assert monitoring.precheck(**watched)[0] is False


def test_extra_overlay_only_watches_selected_subtrees(tree):
    extra = tree / "extra.yaml"
    change(extra, {"daifuku_navigation": {
        "daifuku_navigation": {"ros__parameters": {"value": 3}},
    }})
    watched = scope("daifuku_navigation", files=[extra])
    before = monitoring.config_digest(**watched)
    change(extra, {"daifuku_navigation": {"unknown_node": {}}})
    assert monitoring.config_digest(**watched) != before
    assert monitoring.precheck(**watched)[0] is False


def test_missing_selected_file_prevents_restart(tree):
    watched = scope("daifuku_navigation", files=[tree / "missing.yaml"])
    assert monitoring.precheck(**watched)[0] is False


def test_comments_do_not_restart_selected_configuration(tree):
    watched = scope("daifuku_navigation")
    before = monitoring.config_digest(**watched)
    path = tree / "navigation" / "params.yaml"
    path.write_text("# comment\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    assert monitoring.config_digest(**watched) == before


def test_site_names_do_not_escape_sites_directory(tree):
    with pytest.raises(RuntimeError):
        paths.overrides_path("../other")


def test_available_overrides_requires_site_override_file(tree):
    (tree / "sites" / "incomplete").mkdir()
    assert paths.available_overrides() == ["other", "selected"]


def test_repository_audit_checks_relocated_overlays():
    from daifuku_config.overlay import audit_tree

    assert audit_tree(str(Path(__file__).resolve().parents[1])) == []
