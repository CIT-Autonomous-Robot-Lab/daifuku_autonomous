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
# rtmouse カーネルモジュールはホストで insmod されている前提 (/dev/rt* が必要)。

import os

from ament_index_python.packages import get_package_share_directory

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.actions import EmitEvent
from launch.actions import IncludeLaunchDescription
from launch.actions import RegisterEventHandler
from launch.events import matches_action
from launch.events import Shutdown
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration

from launch_ros.actions import LifecycleNode
from launch_ros.event_handlers import OnStateTransition
from launch_ros.events import lifecycle

from lifecycle_msgs.msg import Transition


def generate_launch_description():
    pkg_share = get_package_share_directory("autonomous_nav")
    bringup_launch_dir = os.path.join(
        get_package_share_directory("raspicat_bringup"), "launch"
    )

    default_params = os.path.join(pkg_share, "config", "robot", "raspicat.yaml")

    params_file = LaunchConfiguration("params_file")
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

    mouse_node = LifecycleNode(
        namespace="",
        name="raspimouse",
        package="raspimouse",
        executable="raspimouse",
        output="screen",
        parameters=[params_file],
    )

    # 起動 -> configure -> (inactive になったら) activate。
    # finalized まで落ちたら launch ごと終了する。上流 raspicat.launch.py と同じ。
    emit_configuring_event = EmitEvent(
        event=lifecycle.ChangeState(
            lifecycle_node_matcher=matches_action(mouse_node),
            transition_id=Transition.TRANSITION_CONFIGURE,
        )
    )
    register_activating_transition = RegisterEventHandler(
        OnStateTransition(
            target_lifecycle_node=mouse_node,
            goal_state="inactive",
            entities=[
                EmitEvent(
                    event=lifecycle.ChangeState(
                        lifecycle_node_matcher=matches_action(mouse_node),
                        transition_id=Transition.TRANSITION_ACTIVATE,
                    )
                )
            ],
        )
    )
    register_shutting_down_transition = RegisterEventHandler(
        OnStateTransition(
            target_lifecycle_node=mouse_node,
            goal_state="finalized",
            entities=[EmitEvent(event=Shutdown())],
        )
    )

    return LaunchDescription([
        DeclareLaunchArgument(
            "params_file",
            default_value=default_params,
            description="raspimouse のパラメータファイル。"
                        "既定は config/robot/raspicat.yaml (use_pulse_counters: true)。",
        ),
        DeclareLaunchArgument("lidar_frame", default_value="lidar_link"),
        DeclareLaunchArgument("use_joint_state_publisher", default_value="True"),
        robot_state_publisher_launch,
        mouse_node,
        register_activating_transition,
        register_shutting_down_transition,
        emit_configuring_event,
    ])
