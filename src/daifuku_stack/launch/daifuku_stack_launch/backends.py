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

"""localization / planner バックエンドの選択と、その起動前チェック。

navigation.launch.py が扱う 4 つの選択肢:

  localization  : amcl / emcl2 / vi
  planner       : navfn (nav2 標準) / vi (価値反復)
  local_planner : auto / nav2 / vi
  nav2          : false (既定) / true / auto

planner:=vi では planner_server の代わりに vi_planner パッケージの
navigation_launch.py を include する。**立つ VI のノードはどちらの
local_planner でも vi_planner 1 つ**で、local_planner:=vi なら
compute_path_to_pose と follow_path の両方を、local_planner:=nav2 なら
follow: false で compute_path_to_pose だけを提供し、追従は controller_server
(DWB) が持つ。**2026-08-08 の上流の整理まで後者は vi_global_planner という
別パッケージだった**（消えたので、設定も検査もノード名 vi_planner に一本化して
ある）。

nav2:=false (**既定**) はそこからさらに進んで、**Nav2 の navigation を
BT ごと立てない**。vi_planner が standalone モードで
navigate_to_pose と follow_waypoints も提供するので、bt_navigator /
behavior_server / waypoint_follower / smoother_server が丸ごと不要になる。
残るのは velocity_smoother:=true (既定) のときの velocity_smoother と、
それを起こすためだけの lifecycle_manager_navigation (管理下 1 ノード) だけ。
地図を配る map_server と自己位置の emcl2 はそのまま (これらは navigation では
なく localization 側)。

**localization:=vi** はその emcl2 も外し、自己位置推定まで vi_planner に持たせる
(上流が 2026-08-09 に足した VIOLA)。map_server は残る — 地図はどちらの推定器も
/map から読む。どの推定器を使うかは launch ではなく **config の `localizer`** が
持ち、ここはその値と launch 引数が噛み合っているかを見て、決まったものを launch
設定 `localizer` へ置く (validate_localization)。config が何も選んでいなければ
`DEFAULT_VI_LOCALIZER`。
"""

from ament_index_python.packages import PackageNotFoundError, get_package_prefix
from launch.actions import LogInfo, SetLaunchConfiguration
from launch.substitutions import PythonExpression

from daifuku_config_manager import value
from daifuku_config_manager.params import load

# localization:=vi で config が何も選んでいない (= localizer が既定の external の
# まま) ときに使う推定器。全地図に belief を持つ和積で、**未シードでも最初の
# スキャンから free 一様で立ち上がる** (窓つきの grid は種が要る)。姉妹の viterbi は
# 同じ場を min-plus で回す変種だが 1 observe 183ms で追従の 40ms 予算を超える。
DEFAULT_VI_LOCALIZER = "belief"


def resolve_local_planner(planner, local_planner):
    """local_planner:=auto を実際のバックエンド名へ解決する substitution を作る。

    auto (既定) はグローバルプランナに連動する: planner:=vi なら vi
    (= vi_planner 1 ノードが両アクションを提供)、それ以外は nav2
    (vi_planner を follow: false で立てて controller_server が追従)。
    """
    return PythonExpression([
        "'", local_planner, "' if '", local_planner, "' != 'auto' else "
        "('vi' if '", planner, "' == 'vi' else 'nav2')"
    ])


def resolve_nav2(nav2, planner, effective_local_planner):
    """nav2:=auto を "true" / "false" へ解決する substitution を作る。

    **既定は auto ではなく false**。素で起動したら Nav2 は立ちません。

    auto は「VI が 1 ノードで全部やれるなら Nav2 は立てない」で、planner:=vi かつ
    狭域も vi に解決されるときだけ false になります。**planner:=navfn へ落とすときは
    これが要ります** — 既定のままだと下の validate_nav2 が弾くので。

    Args:
        effective_local_planner: resolve_local_planner() が返した substitution。
    """
    return PythonExpression([
        "'", nav2, "' if '", nav2, "' != 'auto' else ",
        "('false' if ('", planner, "' == 'vi' and '", effective_local_planner,
        "' == 'vi') else 'true')",
    ])


def validate_nav2(context, *args, effective_nav2, effective_local_planner, **kwargs):
    """nav2:= の値と、false にできる前提を見る。

    nav2:=false は vi_planner の standalone モードそのものなので、
    vi_planner が立たない構成では成り立たない。ここで弾かないと、
    navigate_to_pose のサーバが**どこにも居ない**まま起動が通ってしまい、
    症状は「ゴールを投げても何も起きない」になる (RViz もパネルも
    「サーバがいません」としか言わない)。
    """
    selected = value(context, "nav2")
    if selected not in ("auto", "true", "false"):
        raise RuntimeError(
            f"Unsupported nav2: {selected}\n"
            "Use nav2:=false (default; vi_planner standalone), nav2:=true "
            "(bt_navigator + behavior_server + waypoint_follower) or nav2:=auto "
            "(false when the VI planner can serve everything, true otherwise)."
        )
    if effective_nav2.perform(context) != "false":
        return []

    if effective_local_planner.perform(context) != "vi":
        # 既定のまま planner:=navfn / local_planner:=nav2 を選んだときにここへ来る。
        # 黙って nav2 を立て直すことはしない: 「Nav2 は立てないつもりだったのに
        # 立っていた」は起動ログを 1 行ずつ読むまで気付けないので、要求どおりに
        # できないなら止める。
        how = "planner:=navfn" if value(context, "planner") != "vi" else "local_planner:=nav2"
        raise RuntimeError(
            f"nav2:=false (the default) cannot be honored with {how}.\n"
            "navigate_to_pose / follow_waypoints are served by vi_planner itself in "
            "that mode, and vi_planner is not launched here — nothing would serve "
            "them.\n"
            f"Add nav2:=auto to let it follow the planner (recommended when you are "
            f"falling back to {how}), or nav2:=true to ask for the Nav2 nodes "
            "explicitly."
        )
    return []


def config_localizer(context):
    """合成後の params_file が vi_planner に渡す `localizer` の値。

    **compose の後に呼ぶこと** (params_file は overrides を重ねた一時ファイルに
    差し替わっている)。書かれていなければノードの既定と同じ "external"。
    """
    body = load(value(context, "params_file")).get("vi_planner") or {}
    got = (body.get("ros__parameters") or {}).get("localizer")
    return str(got).strip() if got else "external"


def validate_localization(context, *args, effective_nav2=None, **kwargs):
    """localization:= の値と、選んだ推定器を立てられる前提を見る。

    決まった推定器を launch 設定 `localizer` へ置く。**これは launch 引数では
    なく解決結果**で、vi_planner を立てる Node がここから読む。config の値を
    そのまま流すのが基本で、localization:=vi のときだけ「config が何も選んで
    いない」を DEFAULT_VI_LOCALIZER で埋める。
    """
    selected = value(context, "localization")
    if selected not in ("amcl", "emcl", "emcl2", "vi"):
        raise RuntimeError(
            f"Unsupported localization: {selected}\n"
            "Use localization:=amcl, localization:=emcl2 or localization:=vi "
            "(vi_planner's own estimator; no emcl2)."
        )

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
    # **エラーも警告も出ないまま自己位置だけが壊れる** —— config が内蔵なのに
    # localization:=emcl2 で立てると、map->odom を emcl2 と 2 人が出し、さらに
    # pose_topic (mcl_pose) が「連続入力」ではなく手動シードとして読まれて 20Hz で
    # belief が張り直される。
    localizer = config_localizer(context)
    if selected != "vi":
        if localizer != "external":
            raise RuntimeError(
                f"config sets vi_planner's localizer to {localizer!r}, but "
                f"localization:={selected} brings up its own estimator.\n"
                "Both would own map->odom, and vi_planner would treat pose_topic as a "
                "manual seed instead of a pose input — neither logs anything.\n"
                "Set localizer: \"external\" back in config/stack/nav2/vi_planner.yaml "
                "(or the overrides file that changed it), or launch with "
                "localization:=vi to use it."
            )
        return [SetLaunchConfiguration("localizer", "external")]

    if value(context, "planner") != "vi":
        raise RuntimeError(
            "localization:=vi needs planner:=vi — the estimator lives in vi_planner, "
            "and planner:=navfn does not launch it."
        )
    if effective_nav2 is not None and effective_nav2.perform(context) != "false":
        # nav2:=true の navigation は vi 版 navigation_launch.py 経由で立つが、
        # あちらは publish_tf を渡す口を持たない (params_file 頼み)。config に
        # 書かせると localization:=emcl2 のときに二重配信へ戻るので、この組は
        # 通さない。
        raise RuntimeError(
            "localization:=vi requires nav2:=false (the default).\n"
            "With the Nav2 stack up, vi_planner is launched through vi_planner's "
            "navigation_launch.py, which has no way to hand it publish_tf — nobody "
            "would publish map->odom."
        )
    if localizer != "external":
        return [SetLaunchConfiguration("localizer", localizer)]

    # config が何も選んでいない (既定のまま)。ここで落とさず既定の推定器を使う。
    # **external のまま立てるのだけはしない** —— それは「誰かの推定を読む」設定
    # なので、emcl2 を止めた構成では誰も map->odom を出さないまま起動し、RViz
    # からは Fixed Frame ごと全部消える。
    return [
        LogInfo(
            msg=f"localizer: {DEFAULT_VI_LOCALIZER} "
                "(config は external のまま = 既定。config/stack/nav2/vi_planner.yaml "
                "か overrides で grid / adaptive / viterbi にも変えられる)"
        ),
        SetLaunchConfiguration("localizer", DEFAULT_VI_LOCALIZER),
    ]


def validate_planner(context, *args, **kwargs):
    """planner:= の値と、vi を選んだ場合のパッケージの有無を見る。"""
    selected = value(context, "planner")
    if selected not in ("vi", "navfn"):
        raise RuntimeError(
            f"Unsupported planner: {selected}\n"
            "Use planner:=vi (value iteration, vi_planner) or planner:=navfn."
        )
    if selected == "vi":
        try:
            get_package_prefix("vi_planner")
        except PackageNotFoundError as exc:
            raise RuntimeError(
                "vi_planner package is not available.\n"
                "Import value_iteration3 (vcs import src < daifuku_autonomous.repos) and "
                "build it (colcon build --packages-select vi_planner) before launching "
                "with planner:=vi, or fall back to planner:=navfn."
            ) from exc
    return []


# かつてここには ``_validate_vi_solver`` があり、**compact ソルバ +
# global_sweep** を「フィードバックが黙って効かない組み合わせ」として弾いて
# いた。2026-08-04 に compact も同じフィードバックを持つようになった
# (sink への書き戻し + タイル修復) ので、その検査は消してある。
#
# メモリの上限判定もここではやらない。地図の実寸はノードしか知らないので、
# ノード側が /proc/meminfo の MemAvailable と突き合わせる (超えたら起動時に
# エラーで止まり、半分を超えたら警告)。2026-08-09 の上流の整理まではキー
# ``dense_limit_mb`` だった。


def validate_local_planner(context, *args, **kwargs):
    """local_planner:= の値と、vi を選んだ場合の前提を見る。"""
    selected = value(context, "local_planner")
    if selected not in ("auto", "nav2", "vi"):
        raise RuntimeError(
            f"Unsupported local_planner: {selected}\n"
            "Use local_planner:=auto (follow the global planner), "
            "local_planner:=nav2 (vi_planner with follow: false + controller_server) or "
            "local_planner:=vi (vi_planner: one node, both actions)."
        )
    if selected == "vi" and value(context, "planner") != "vi":
        # local_planner:=vi は vi 版 navigation_launch.py 経由でのみ効く
        # (planner:=navfn の標準 navigation_launch.py は local_planner を
        # 知らない)。
        raise RuntimeError(
            "local_planner:=vi requires planner:=vi (it is wired through "
            "vi_planner's navigation_launch.py)."
        )

    # パッケージの有無はここでは見ない。**どちらの local_planner でも立つのは
    # vi_planner 1 つ**になり、それを要求するのは planner:=vi の側なので、
    # validate_planner の検査で尽きている (上の分岐で planner:=vi は保証済み)。
    return []
