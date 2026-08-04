# Raspberry Pi Cat 本体ドライバの起動。
#
# 上流 raspicat_ros の raspicat.launch.py 相当だが、raspimouse ノードを自前で
# 立てている。上流は parameters= に raspicat/config/raspicat.param.yaml を直接
# 渡しており、launch_ros のパラメータ優先順位 (ノード自身の parameters= が
# グローバルの SetParametersFromFile に勝つ) の都合で、include して差分を重ねる
# 方式では use_pulse_counters を上書きできないため。詳細は
# config/robot/raspicat.yaml のコメント。
#
# 自前化のついでに urg_node 関連は落としてある (本機の LiDAR は MID360 で、
# lidar_bringup.launch.py が扱う)。robot_state_publisher / joint_state_publisher は
# パラメータ競合が無いので上流の launch をそのまま include する。
#
# driver:= で公式実装と自前実装のどちらを立てるか選ぶ。どちらも同じ契約 (cmd_vel を
# 購読し、odom と odom -> base_footprint TF を出し、motor_power サービスを持つ
# lifecycle ノード) を満たすので、この launch から下だけが入れ替わる。
#
#   raspimouse (既定) … 公式実装。raspimouse2 の raspimouse ノードで、rtmouse
#                       カーネルモジュールがホストで insmod されている前提
#                       (/dev/rt* が必要)。Pi 4 のみ。
#   original          … 自前実装。raspicat_driver パッケージが PWM (sysfs)・gpiochip・
#                       I2C をユーザ空間から直接叩く。Pi 4 / Pi 5 の両方に対応し、
#                       機種は model:=auto がハード側で判定する。Pi 5 は rtmouse が
#                       動かない (BCM2711 のレジスタを ioremap するが GPIO/PWM は RP1
#                       側にある) ので、こちらしか選べない。
#
# Pi 4 で original を選ぶときは rtmouse を載せないこと。両方が GPIO 16/6/5 と PWM を
# 奪い合い、カーネルは止めてくれない (ノード側が起動時に拒否する)。詳細は
# docs/setup/raspberry-pi-4.md と raspberry-pi-5.md。
#
# twist_mux:= (既定 true) はドライバの手前に cmd_vel の仲裁を挟む。自律走行
# (/cmd_vel) と遠隔操作 (/cmd_vel_teleop) の両方が同じトピックへ書いていたのを、
# 優先度付きで 1 本に束ねる。ドライバが購読するのは /cmd_vel ではなく
# /cmd_vel_mux になる (両ドライバとも相対名 "cmd_vel" なので remap で効く)。
# nav2 側の配線は変えていない。velocity_smoother の出力はそのまま /cmd_vel で、
# それが仲裁の入力の 1 つになる。

import os
import sys

from ament_index_python.packages import get_package_share_directory

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.actions import EmitEvent
from launch.actions import IncludeLaunchDescription
from launch.actions import OpaqueFunction
from launch.actions import RegisterEventHandler
from launch.events import matches_action
from launch.events import Shutdown
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration

from launch_ros.actions import LifecycleNode
from launch_ros.actions import Node
from launch_ros.event_handlers import OnStateTransition
from launch_ros.events import lifecycle

from lifecycle_msgs.msg import Transition

# 共通部品はこの launch ディレクトリの直下 (daifuku_stack_launch/) にある。
_LAUNCH_DIR = os.path.dirname(os.path.realpath(__file__))
if _LAUNCH_DIR not in sys.path:
    sys.path.insert(0, _LAUNCH_DIR)

from daifuku_stack_launch import is_true, params  # noqa: E402


# twist_mux を挟むときにドライバが購読するトピック。仲裁の入力 (/cmd_vel と
# /cmd_vel_teleop) と区別するため、出力だけ別名にしてある。
MUXED_CMD_VEL = "cmd_vel_mux"


# driver:= の値ごとの (ノード名, パッケージ, 実行ファイル, 既定パラメータファイル)。
DRIVERS = {
    "raspimouse": ("raspimouse", "raspimouse", "raspimouse", "raspicat.yaml"),
    "original": (
        "raspicat_driver",
        "raspicat_driver",
        "raspicat_driver",
        "raspicat_driver.yaml",
    ),
}


def _twist_mux(context, pkg_share, overrides_dir):
    """cmd_vel の仲裁ノードを組み立てる (twist_mux:=false なら何も作らない)。

    自律走行 (/cmd_vel) と遠隔操作 (/cmd_vel_teleop) を優先度で 1 本に束ね、
    MUXED_CMD_VEL へ出す。**「出している間だけ勝つ」仲裁**であって非常停止では
    ない。teleop が勝つのは publish している間と timeout (0.5 s) のあいだだけで、
    途切れれば自律側に戻る。確実に止めるのは今までどおりモータ電源
    (motor_power サービス / control.sh motor off)。

    Returns:
        (action の並び, ログの並び)。twist_mux:=false なら両方とも空。
    """
    if not is_true(context, "twist_mux"):
        return [], []

    params_file = LaunchConfiguration("twist_mux_params_file").perform(context)
    if not params_file:
        params_file = os.path.join(pkg_share, "config", "robot", "twist_mux.yaml")
    if not os.path.isfile(params_file):
        raise RuntimeError(f"twist_mux_params_file does not exist: {params_file}")

    params_file, logs = params.compose_path(
        context, params_file,
        name="twist_mux_params_file",
        overrides_dir=overrides_dir,
    )

    return [
        Node(
            package="twist_mux",
            executable="twist_mux",
            name="twist_mux",
            output="screen",
            parameters=[params_file],
            remappings=[("cmd_vel_out", MUXED_CMD_VEL)],
        ),
    ], logs


def launch_setup(context, *args, **kwargs):
    """driver:= を解決してから 1 つだけノードを立てる。

    lifecycle のイベントハンドラは対象のノードオブジェクトを直接参照するので、
    IfCondition で 2 つ並べるとハンドラも二重になる。ここで 1 つに決める。
    """
    pkg_share = get_package_share_directory("daifuku_stack")
    overrides_dir = os.path.join(pkg_share, "config", "overrides")

    driver = LaunchConfiguration("driver").perform(context)
    if driver not in DRIVERS:
        raise RuntimeError(
            "driver:=%s は未対応です。%s のいずれかを指定してください。"
            % (driver, " / ".join(sorted(DRIVERS)))
        )
    node_name, package, executable, default_params_name = DRIVERS[driver]

    params_file = LaunchConfiguration("params_file").perform(context)
    if not params_file:
        params_file = os.path.join(pkg_share, "config", "robot", default_params_name)

    # overrides を重ねる。行き先はノード名で決まるので、driver:= で選ばなかった
    # ほうの節 (raspimouse: / raspicat_driver:) は自動的に外れる。
    params_file, override_logs = params.compose_path(
        context, params_file,
        name="params_file",
        overrides_dir=overrides_dir,
    )

    parameters = [params_file]
    # model:= は自前実装にしか無いパラメータ。raspimouse に渡すと未宣言で落ちる。
    model = LaunchConfiguration("model").perform(context)
    if model:
        if driver != "original":
            raise RuntimeError("model:= は driver:=original のときだけ指定できます。")
        parameters.append({"model": model})

    # 仲裁を挟むときだけ、ドライバの購読先を仲裁の出力へ振り替える。両ドライバとも
    # 相対名の "cmd_vel" で購読している (raspimouse_component.cpp / raspicat_driver の
    # node.py) ので、この remap で両方に効く。
    mux_actions, mux_logs = _twist_mux(context, pkg_share, overrides_dir)
    remappings = [("cmd_vel", MUXED_CMD_VEL)] if mux_actions else []

    driver_node = LifecycleNode(
        namespace="",
        name=node_name,
        package=package,
        executable=executable,
        output="screen",
        parameters=parameters,
        remappings=remappings,
    )

    # 起動 -> configure -> (inactive になったら) activate。
    # finalized まで落ちたら launch ごと終了する。上流 raspicat.launch.py と同じ。
    emit_configuring_event = EmitEvent(
        event=lifecycle.ChangeState(
            lifecycle_node_matcher=matches_action(driver_node),
            transition_id=Transition.TRANSITION_CONFIGURE,
        )
    )
    register_activating_transition = RegisterEventHandler(
        OnStateTransition(
            target_lifecycle_node=driver_node,
            goal_state="inactive",
            entities=[
                EmitEvent(
                    event=lifecycle.ChangeState(
                        lifecycle_node_matcher=matches_action(driver_node),
                        transition_id=Transition.TRANSITION_ACTIVATE,
                    )
                )
            ],
        )
    )
    register_shutting_down_transition = RegisterEventHandler(
        OnStateTransition(
            target_lifecycle_node=driver_node,
            goal_state="finalized",
            entities=[EmitEvent(event=Shutdown())],
        )
    )

    return override_logs + mux_logs + mux_actions + [
        driver_node,
        register_activating_transition,
        register_shutting_down_transition,
        emit_configuring_event,
    ]


def generate_launch_description():
    bringup_launch_dir = os.path.join(
        get_package_share_directory("raspicat_bringup"), "launch"
    )

    lidar_frame = LaunchConfiguration("lidar_frame")
    use_joint_state_publisher = LaunchConfiguration("use_joint_state_publisher")

    # URDF / TF ツリー。上流のものをそのまま使う (競合するパラメータが無い)。
    robot_state_publisher_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(bringup_launch_dir, "robot_state_publisher.launch.py")
        ),
        launch_arguments={
            "lidar_frame": lidar_frame,
            "use_joint_state_publisher": use_joint_state_publisher,
        }.items(),
    )

    return LaunchDescription([
        DeclareLaunchArgument(
            "driver",
            default_value="raspimouse",
            description="本体ドライバ: raspimouse (公式実装 / rtmouse / Pi 4 のみ) または "
                        "original (自前実装 / Pi 4・Pi 5)。",
        ),
        DeclareLaunchArgument(
            "model",
            default_value="",
            description="driver:=original のときの機種: pi4 / pi5 / auto。空なら "
                        "raspicat_driver.yaml の model (既定 auto) に従う。",
        ),
        DeclareLaunchArgument(
            "params_file",
            default_value="",
            description="ドライバのパラメータファイル。空なら driver:= に応じて "
                        "config/robot/raspicat.yaml か raspicat_driver.yaml を使う。",
        ),
        DeclareLaunchArgument(
            "twist_mux",
            default_value="true",
            description="cmd_vel の仲裁 (twist_mux) を挟むか。true ならドライバが "
                        f"購読するのは /cmd_vel ではなく /{MUXED_CMD_VEL} になり、"
                        "遠隔操作は /cmd_vel_teleop へ出す (自律側の /cmd_vel より "
                        "優先度が高い)。false にすると仲裁なしで全員が /cmd_vel へ "
                        "書く従来の配線に戻る。",
        ),
        DeclareLaunchArgument(
            "twist_mux_params_file",
            default_value="",
            description="twist_mux のパラメータファイル。空なら "
                        "config/robot/twist_mux.yaml を使う。",
        ),
        *params.declare_args(
            os.path.join(
                get_package_share_directory("daifuku_stack"), "config", "overrides"
            )
        ),
        DeclareLaunchArgument("lidar_frame", default_value="lidar_link"),
        DeclareLaunchArgument("use_joint_state_publisher", default_value="True"),
        robot_state_publisher_launch,
        OpaqueFunction(function=launch_setup),
    ])
