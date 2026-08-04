"""localization / planner バックエンドの選択と、その起動前チェック。

navigation.launch.py が扱う 3 つの選択肢:

  localization  : amcl / emcl2
  planner       : navfn (nav2 標準) / vi (価値反復)
  local_planner : auto / nav2 / vi

planner:=vi では planner_server の代わりに vi_global_planner パッケージの
navigation_launch.py を include する。その中で local_planner:=vi なら
vi_planner 1 ノードが compute_path_to_pose と follow_path の両方を提供し、
local_planner:=nav2 なら vi_global_planner + controller_server が立つ (排他)。
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


def _validate_vi_solver(context):
    """local_planner:=vi の設定に、静かに効かなくなる組み合わせが無いか見る。

    見るのは 1 つだけ、**compact ソルバ + global_sweep** の組み合わせ。

    vi_planner は 1 ノードが compute_path_to_pose と follow_path の両方を
    提供する (vi_global_planner は起動しない)。密ソルバではその 2 つが同じ
    ``states`` を共有するので、追従がスキャンから書いた local_penalty を
    ``global_sweep`` が全域へ掃き広げれば、広域の経路も塞がった通路を避ける
    ようになる。compact にはこの共有場が無い (``states`` を作らず、追従は
    sink から起こしたパッチの上で回り、それは置き直しのたびに捨てられる) ので、
    同じ設定でも**フィードバックだけが黙って効かなくなる**。

    メモリの上限判定はここではやらない。地図の実寸はノードしか知らないので、
    ``dense_limit_mb`` としてノード側に移した (超えたら起動時にエラーで止まる)。
    かつてここには「map_scale > 1 なら密ソルバを禁止」という代理判定があったが、
    前提が逆になった: map_scale は密を**載せるための**手段で、19F を 2 で解いた
    密の実測は 655 MB しかない。
    """
    import yaml

    # compose が差し替えた後の合成結果を読む。
    with open(value(context, "params_file")) as f:
        merged = yaml.safe_load(f) or {}
    vp = merged.get("vi_planner", {}).get("ros__parameters", {})
    solver = str(vp.get("solver", ""))
    if solver.endswith("_compact") and vp.get("global_sweep", True):
        raise RuntimeError(
            f"local_planner:=vi with vi_planner.solver={solver} and "
            "global_sweep enabled.\n"
            "The out-of-core (compact) solver never builds the shared `states` "
            "array, so the local planner's scan penalties have no way to reach "
            "the global value function: the robot would avoid obstacles locally "
            "while compute_path_to_pose keeps returning a path through them.\n"
            'Either use a dense solver ("frontier2d_sparse" — 19F at map_scale 2 '
            "measures 655 MB, see src/daifuku_stack/config/README.md), or set "
            "vi_planner.global_sweep: false to accept the old behaviour."
        )


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

    _validate_vi_solver(context)
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
