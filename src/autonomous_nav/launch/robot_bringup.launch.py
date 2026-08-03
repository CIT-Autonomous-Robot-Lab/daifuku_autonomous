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
# driver:= でドライバを選ぶ。どちらも同じ契約 (cmd_vel を購読し、odom と
# odom -> base_footprint TF を出し、motor_power サービスを持つ lifecycle ノード) を
# 満たすので、この launch から下だけが入れ替わる。
#
#   raspimouse (既定) … Pi 4 用。rtmouse カーネルモジュールがホストで insmod されて
#                       いる前提 (/dev/rt* が必要)。
#   pi5               … Pi 5 用。rtmouse は Pi 5 で動かない (BCM2711 のレジスタを
#                       ioremap するが、Pi 5 では GPIO/PWM が RP1 側にある) ので、
#                       scripts/raspicat_pi5_driver.py が RP1 の PWM と gpiochip と
#                       I2C をユーザ空間から直接叩く。詳細は
#                       docs/setup/raspberry-pi-5.md。

import os

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
from launch_ros.event_handlers import OnStateTransition
from launch_ros.events import lifecycle

from lifecycle_msgs.msg import Transition


# driver:= の値ごとの (ノード名, パッケージ, 実行ファイル, 既定パラメータファイル)。
DRIVERS = {
    "raspimouse": ("raspimouse", "raspimouse", "raspimouse", "raspicat.yaml"),
    "pi5": (
        "raspicat_pi5_driver",
        "autonomous_nav",
        "raspicat_pi5_driver.py",
        "raspicat_pi5.yaml",
    ),
}


def launch_setup(context, *args, **kwargs):
    """driver:= を解決してから 1 つだけノードを立てる。

    lifecycle のイベントハンドラは対象のノードオブジェクトを直接参照するので、
    IfCondition で 2 つ並べるとハンドラも二重になる。ここで 1 つに決める。
    """
    pkg_share = get_package_share_directory("autonomous_nav")

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

    driver_node = LifecycleNode(
        namespace="",
        name=node_name,
        package=package,
        executable=executable,
        output="screen",
        parameters=[params_file],
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

    return [
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
            description="本体ドライバ: raspimouse (Pi 4 / rtmouse) または pi5 (Pi 5 / RP1)。",
        ),
        DeclareLaunchArgument(
            "params_file",
            default_value="",
            description="ドライバのパラメータファイル。空なら driver:= に応じて "
                        "config/robot/raspicat.yaml か raspicat_pi5.yaml を使う。",
        ),
        DeclareLaunchArgument("lidar_frame", default_value="lidar_link"),
        DeclareLaunchArgument("use_joint_state_publisher", default_value="True"),
        robot_state_publisher_launch,
        OpaqueFunction(function=launch_setup),
    ])
