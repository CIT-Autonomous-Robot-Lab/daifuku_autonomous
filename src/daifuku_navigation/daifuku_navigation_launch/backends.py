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

"""VI の TF 所有と、選択した外部推定器の起動条件を検査する。"""

from ament_index_python.packages import PackageNotFoundError, get_package_prefix

from daifuku_config import value
from daifuku_config.params import load


def config_localizer(context):
    """合成後の params_file が vi_planner に渡す `localizer` の値。

    **compose の後に呼ぶこと** (params_file は overrides を重ねた一時ファイルに
    差し替わっている)。書かれていなければノードの既定と同じ "external"。
    """
    body = load(value(context, "params_file")).get("vi_planner") or {}
    got = (body.get("ros__parameters") or {}).get("localizer")
    return str(got).strip() if got else "external"


def validate_localization(context, *args, **kwargs):
    """localization:= の値と、選んだ推定器を立てられる前提を見る。"""
    selected = value(context, "localization")
    if selected not in ("amcl", "emcl", "emcl2", "emcl3d", "vi"):
        raise RuntimeError(
            f"Unsupported localization: {selected}\n"
            "Use localization:=amcl, localization:=emcl2, localization:=emcl3d "
            "(6DoF MCL on the PCD map) or localization:=vi "
            "(vi_planner's own estimator; no emcl2)."
        )

    if selected == "emcl3d":
        try:
            get_package_prefix("emcl3d")
        except PackageNotFoundError as exc:
            raise RuntimeError(
                "EMCL3D package is not available: emcl3d\n"
                "Import it (vcs import src < daifuku_autonomous.repos) and build it "
                "(colcon build --packages-select emcl3d) before launching with "
                "localization:=emcl3d."
            ) from exc

    if selected in ("emcl", "emcl2"):
        package_name = value(context, "emcl2_package")
        try:
            get_package_prefix(package_name)
        except PackageNotFoundError as exc:
            raise RuntimeError(
                f"EMCL2 package is not available: {package_name}\n"
                "Clone/build the emcl2_ros2 repository in this workspace or source an "
                "underlay that provides it before launching with localization:=emcl2."
            ) from exc

    # 内蔵推定器 (VIOLA) を使うかどうかは launch 引数、**どれを使うかは config**。
    # 2 か所に分かれるので、噛み合っていなければここで止める。黙って通すと
    # どちらの向きも**エラーも警告も出ないまま自己位置だけが壊れる**:
    #   config が内蔵 + localization:=emcl2 -> map->odom を emcl2 と 2 人が出し、
    #     さらに pose_topic (mcl_pose) が「連続入力」ではなく手動シードとして
    #     読まれて 20Hz で belief が張り直される。
    #   config が external + localization:=vi -> 誰も map->odom を出さないまま
    #     起動し、RViz からは Fixed Frame ごと全部消える。
    localizer = config_localizer(context)
    if selected != "vi":
        if localizer != "external":
            raise RuntimeError(
                f"config sets vi_planner's localizer to {localizer!r}, but "
                f"localization:={selected} brings up its own estimator.\n"
                "Both would own map->odom, and vi_planner would treat pose_topic as a "
                "manual seed instead of a pose input — neither logs anything.\n"
                "Set localizer: \"external\" in src/daifuku_config/navigation/vi_planner.yaml "
                "(or the overrides file that changed it), or launch with "
                "localization:=vi to use it."
            )
        return []

    if localizer == "external":
        raise RuntimeError(
            "localization:=vi asks vi_planner to localize, but config leaves "
            "localizer: \"external\" (= consume someone else's pose).\n"
            "Pick an estimator in src/daifuku_config/navigation/vi_planner.yaml (or an overrides "
            "file): \"adaptive\" (windowed multi-resolution; recovers from kidnapping "
            "and starts unseeded), \"grid\", \"belief\" or \"viterbi\".\n"
            "Leaving it external here would launch with nothing publishing map->odom."
        )
    return []


def validate_vi_package(context, *args, **kwargs):
    """Require the standalone navigation provider before starting nodes."""
    try:
        get_package_prefix("vi_planner")
    except PackageNotFoundError as exc:
        raise RuntimeError(
            "vi_planner package is not available.\n"
            "Import value_iteration3 (vcs import src < daifuku_autonomous.repos) and "
            "build it (colcon build --packages-select vi_planner) before launching."
        ) from exc
    return []
