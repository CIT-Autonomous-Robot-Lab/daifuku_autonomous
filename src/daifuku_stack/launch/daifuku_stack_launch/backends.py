"""localization / planner バックエンドの選択と、その起動前チェック。

navigation.launch.py が扱う 4 つの選択肢:

  localization  : amcl / emcl2
  planner       : navfn (nav2 標準) / vi (価値反復)
  local_planner : auto / nav2 / vi
  nav2          : false (既定) / true / auto

planner:=vi では planner_server の代わりに vi_global_planner パッケージの
navigation_launch.py を include する。その中で local_planner:=vi なら
vi_planner 1 ノードが compute_path_to_pose と follow_path の両方を提供し、
local_planner:=nav2 なら vi_global_planner + controller_server が立つ (排他)。

nav2:=false (**既定**) はそこからさらに進んで、**Nav2 のノードを 1 つも
立てない**。vi_planner が standalone モードで
navigate_to_pose と follow_waypoints も提供するので、bt_navigator /
behavior_server / waypoint_follower / smoother_server と
lifecycle_manager_navigation が丸ごと不要になる。地図を配る map_server と
自己位置の emcl2 はそのまま (これらは navigation ではなく localization 側)。
"""

from ament_index_python.packages import PackageNotFoundError, get_package_prefix
from launch.substitutions import PythonExpression

from . import value


def resolve_local_planner(planner, local_planner):
    """local_planner:=auto を実際のバックエンド名へ解決する substitution を作る。

    auto (既定) はグローバルプランナに連動する: planner:=vi なら vi
    (= vi_planner 1 ノードが両アクションを提供)、それ以外は nav2
    (vi_global_planner + controller_server)。
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


def validate_localization(context, *args, **kwargs):
    """localization:= の値と、emcl2 を選んだ場合のパッケージの有無を見る。"""
    selected = value(context, "localization")
    if selected not in ("amcl", "emcl", "emcl2"):
        raise RuntimeError(
            f"Unsupported localization: {selected}\n"
            "Use localization:=amcl or localization:=emcl2."
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
    return []


def validate_planner(context, *args, **kwargs):
    """planner:= の値と、vi を選んだ場合のパッケージの有無を見る。"""
    selected = value(context, "planner")
    if selected not in ("vi", "navfn"):
        raise RuntimeError(
            f"Unsupported planner: {selected}\n"
            "Use planner:=vi (value iteration, vi_global_planner) or planner:=navfn."
        )
    if selected == "vi":
        try:
            get_package_prefix("vi_global_planner")
        except PackageNotFoundError as exc:
            raise RuntimeError(
                "vi_global_planner package is not available.\n"
                "Import value_iteration3 (vcs import src < autonomous_bot.repos) and "
                "build it (colcon build --packages-select vi_global_planner) before launching "
                "with planner:=vi, or fall back to planner:=navfn."
            ) from exc
    return []


# かつてここには ``_validate_vi_solver`` があり、**compact ソルバ +
# global_sweep** を「フィードバックが黙って効かない組み合わせ」として弾いて
# いた。2026-08-04 に compact も同じフィードバックを持つようになった
# (sink への書き戻し + タイル修復) ので、その検査は消してある。
#
# メモリの上限判定もここではやらない。地図の実寸はノードしか知らないので、
# ``dense_limit_mb`` としてノード側にある (超えたら起動時にエラーで止まる)。


def validate_local_planner(context, *args, effective_local_planner, **kwargs):
    """local_planner:= の値と、vi に解決される場合の前提を見る。

    Args:
        effective_local_planner: resolve_local_planner() が返した substitution。
            auto の解決規則をここで書き直さないよう、呼び出し側から受け取る。
    """
    selected = value(context, "local_planner")
    if selected not in ("auto", "nav2", "vi"):
        raise RuntimeError(
            f"Unsupported local_planner: {selected}\n"
            "Use local_planner:=auto (follow the global planner), "
            "local_planner:=nav2 (vi_global_planner + controller_server) or "
            "local_planner:=vi (vi_planner: one node, both actions)."
        )
    if selected == "vi" and value(context, "planner") != "vi":
        # local_planner:=vi は vi 版 navigation_launch.py 経由でのみ効く
        # (planner:=navfn の標準 navigation_launch.py は local_planner を
        # 知らない)。
        raise RuntimeError(
            "local_planner:=vi requires planner:=vi (it is wired through "
            "vi_global_planner's navigation_launch.py)."
        )

    if effective_local_planner.perform(context) != "vi":
        return []

    try:
        get_package_prefix("vi_planner")
    except PackageNotFoundError as exc:
        raise RuntimeError(
            "vi_planner package is not available (local planner defaults to "
            "vi when planner:=vi).\n"
            "Import value_iteration3 (vcs import src < autonomous_bot.repos) and "
            "build it (colcon build --packages-select vi_planner), or "
            "fall back to local_planner:=nav2."
        ) from exc
    return []
