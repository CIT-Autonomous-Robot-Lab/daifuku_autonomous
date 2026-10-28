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

# raspicat サービスで常駐する機体の起動。LiDAR と EKF もここに置き、
# navigation の再起動で /odom の原点や LiDAR の初期化を巻き込まない。
# ドライバが finalized になるとセンサを含む launch 全体が終了する。
# 場所依存の /scan 生成は daifuku_stack/scan_pipeline.launch.py が担う。
#
# 上流の parameters= に勝てないため駆動ノードは直接立て、URDF は上流を include する。
# raspimouse は rtmouse 入りの Pi 4 専用。Pi 5 は original を使うこと。
# rtmouse と original の排他など、ファイル間の制約は AGENTS.md を参照。

import os
import sys

from ament_index_python.packages import get_package_share_directory

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.actions import EmitEvent
from launch.actions import GroupAction
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

_LAUNCH_DIR = os.path.dirname(os.path.realpath(__file__))
if _LAUNCH_DIR not in sys.path:
    sys.path.insert(0, _LAUNCH_DIR)

from daifuku_config_manager import env_bool_default, is_true, params, value  # noqa: E402
from daifuku_bringup_launch import lidar as lidar_common  # noqa: E402


def validate(context, *args, **kwargs):
    """構成の組み合わせを起動前に見る (OpaqueFunction)。

    lidar:=2d に IMU は無い。それでも use_mid360_imu:=true のままだと、ドライバは
    車輪オドメトリを /wheel/odom へ移して TF を止めるのに、EKF は imu0 が一度も
    来ないまま車輪だけで回り続ける。**エラーも警告も出ないまま「IMU 融合している
    つもりで融合していない」状態**になるので、ここで落とす。
    """
    if value(context, "lidar") == "2d" and is_true(context, "use_mid360_imu"):
        raise RuntimeError(
            "lidar:=2d と use_mid360_imu:=true は同時に指定できません。\n"
            "2D LiDAR (URG) に IMU は無いので、EKF は車輪オドメトリだけで回り、"
            "融合しているつもりで融合していない状態になります。\n"
            "use_mid360_imu:=false を渡すか (Compose なら .env の "
            "USE_MID360_IMU=false)、lidar:=mid360 にしてください。"
        )
    return []


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


def _params_file(context, argument, default_name):
    """launch 引数が指すパラメータファイルを解決し、overrides を重ねる。

    空なら src/daifuku_config/bringup/robot/<default_name> に落とす。実在しないものをそのまま
    params.compose_path へ渡すと FileNotFoundError の traceback になり、**どの
    引数が悪いのか出ない**ので、ここで引数名を添えて落とす。

    Returns:
        (パス, ログの並び)。
    """
    config_root = params.config_root("daifuku_bringup")
    path = LaunchConfiguration(argument).perform(context)
    if not path:
        path = os.path.join(config_root, "robot", default_name)
    if not os.path.isfile(path):
        raise RuntimeError(f"{argument} does not exist: {path}")
    return params.compose_path(
        context, path, name=argument, package="daifuku_bringup",
        config_root=config_root,
    )


def _twist_mux(context):
    """cmd_vel の仲裁ノードを組み立てる (twist_mux:=false なら何も作らない)。

    自律走行 (/cmd_vel、優先度 100) と遠隔操作 (/cmd_vel_teleop、優先度 10) を
    1 本に束ね、MUXED_CMD_VEL へ出す。**「出している間だけ勝つ」仲裁**であって
    非常停止ではない。自律側のほうが上なので、**自律走行中は遠隔操作が通らない**
    (通るのは自律側が timeout (0.5 s) のあいだ黙ったとき)。手動へ渡すには
    ゴールを取り消すこと。確実に止めるのは今までどおりモータ電源
    (motor_power サービス / control.sh motor off)。

    Returns:
        (action の並び, ログの並び)。twist_mux:=false なら両方とも空。
    """
    if not is_true(context, "twist_mux"):
        return [], []

    params_file, logs = _params_file(
        context,
        "twist_mux_params_file", "twist_mux.yaml",
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


def _joy_teleop(context):
    """ゲームパッドのドライバと、その入力をモードに変えるノードを組み立てる。

    joy_node は /joy を出すだけ。速度の写像と、START 長押しでの teleop 切り替え・
    START+BACK でのウェイポイント巡回開始は src/joy_teleop.py が持つ。上流の
    teleop_twist_joy を使っていないのはそのため (あちらは押している間だけ速度を
    出すデッドマンしかできない)。

    出す先は /cmd_vel_teleop なので、**twist_mux:=false だと誰も購読しない**。

    ゲームパッドを挿していなくても他は動く。joy_teleop は /joy が一度も来なければ
    何も publish しないので、自律走行の邪魔をしない (start_enabled: true にした
    ときだけは別で、そちらは受信断としてゼロを出し続ける = 自律側を塞ぐ)。
    joy_node のほうはデバイスが無いときの挙動を実機で確かめていないので、
    respawn を付けてある。ホットプラグに対応していれば無害で、対応して
    いなければ 5 秒ごとに開き直して挿したときに拾う。

    Returns:
        (action の並び, ログの並び)。joy:=false なら両方とも空。
    """
    if not is_true(context, "joy"):
        return [], []

    params_file, logs = _params_file(
        context,
        "joy_teleop_params_file", "joy_teleop.yaml",
    )

    return [
        Node(
            package="joy",
            executable="joy_node",
            name="joy_node",
            output="screen",
            parameters=[params_file],
            respawn=True,
            respawn_delay=5.0,
        ),
        Node(
            package="daifuku_bringup",
            executable="joy_teleop.py",
            name="joy_teleop",
            output="screen",
            parameters=[params_file],
        ),
    ], logs


def launch_setup(context, *args, **kwargs):
    """driver:= を解決してから 1 つだけノードを立てる。

    lifecycle のイベントハンドラは対象のノードオブジェクトを直接参照するので、
    IfCondition で 2 つ並べるとハンドラも二重になる。ここで 1 つに決める。
    """

    driver = LaunchConfiguration("driver").perform(context)
    if driver not in DRIVERS:
        raise RuntimeError(
            "driver:=%s は未対応です。%s のいずれかを指定してください。"
            % (driver, " / ".join(sorted(DRIVERS)))
        )
    node_name, package, executable, default_params_name = DRIVERS[driver]

    # 選んだドライバのノード名に対応する overrides だけを重ねる。
    params_file, override_logs = _params_file(
        context, "params_file", default_params_name
    )

    parameters = [params_file]
    # model:= は自前実装にしか無いパラメータ。raspimouse に渡すと未宣言で落ちる。
    model = LaunchConfiguration("model").perform(context)
    if model:
        if driver != "original":
            raise RuntimeError("model:= は driver:=original のときだけ指定できます。")
        parameters.append({"model": model})

    # 両ドライバとも相対名 cmd_vel を購読するので、仲裁の出力へ remap できる。
    mux_actions, mux_logs = _twist_mux(context)
    remappings = [("cmd_vel", MUXED_CMD_VEL)] if mux_actions else []

    # EKF に odom と TF を任せるため、車輪の生値を分けてドライバの TF を止める。
    # raspimouse には publish_tf が無いので /tf を捨て先へ remap する。
    # EKF の起動も同じ use_mid360_imu で切り替える。
    if is_true(context, "use_mid360_imu"):
        remappings.append(
            ("odom", LaunchConfiguration("wheel_odom_topic").perform(context))
        )
        if driver == "original":
            parameters.append({"publish_tf": False})
        else:
            remappings.append(("/tf", "/wheel/tf_unused"))

    joy_actions, joy_logs = _joy_teleop(context)

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

    # site の書き手が競合しないよう、site_manager は常駐するここだけで立てる。
    site_manager = Node(
        package="daifuku_config_manager",
        executable="site_manager",
        name="site_manager",
        output="screen",
    )

    # 設定変更で終了した後は Compose が LiDAR・EKF ごと再起動する。
    # 場所依存の設定は無いので site は見ず、場所変更による不要な機体再起動を防ぐ。
    sentinel = params.sentinel_actions(
        context, package="daifuku_bringup",
        config_root=params.config_root("daifuku_bringup"),
        watch_site=False,
    )

    return override_logs + mux_logs + joy_logs + mux_actions + joy_actions + [
        driver_node,
        register_activating_transition,
        register_shutting_down_transition,
        emit_configuring_event,
        site_manager,
    ] + sentinel


def generate_launch_description():
    pkg_share = get_package_share_directory("daifuku_bringup")
    bringup_launch_dir = os.path.join(
        get_package_share_directory("raspicat_bringup"), "launch"
    )

    urdf_lidar_frame = LaunchConfiguration("urdf_lidar_frame")
    use_joint_state_publisher = LaunchConfiguration("use_joint_state_publisher")

    # lidar_frame が後続の LiDAR include へ漏れると livox_frame が上書きされ、
    # IMU の TF が消えるため GroupAction で隔離する（詳細は AGENTS.md）。
    robot_state_publisher_launch = GroupAction([
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                os.path.join(bringup_launch_dir, "robot_state_publisher.launch.py")
            ),
            launch_arguments={
                "lidar_frame": urdf_lidar_frame,
                "use_joint_state_publisher": use_joint_state_publisher,
            }.items(),
        ),
    ])

    # 子の base_frame / odom_topic / mid360_ekf_params_file が後続の
    # LiDAR include の既定を上書きしないよう、GroupAction で隔離する。
    odom_fusion_launch = GroupAction([
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                os.path.join(pkg_share, "launch", "odom_fusion.launch.py")
            ),
            launch_arguments=[
                (name, LaunchConfiguration(name))
                for name in ("use_mid360_imu", "wheel_odom_topic", "use_sim_time",
                             "overrides", "extra_params_file")
            ],
        ),
    ])

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
                        "src/daifuku_config/bringup/robot/raspicat.yaml か "
                        "raspicat_driver.yaml を使う。",
        ),
        DeclareLaunchArgument(
            "twist_mux",
            default_value="true",
            description="cmd_vel の仲裁 (twist_mux) を挟むか。true ならドライバが "
                        f"購読するのは /cmd_vel ではなく /{MUXED_CMD_VEL} になり、"
                        "遠隔操作は /cmd_vel_teleop へ出す (自律側の /cmd_vel の "
                        "ほうが優先度が高いので、自律走行中は通らない)。"
                        "false にすると仲裁なしで全員が /cmd_vel へ "
                        "書く従来の配線に戻る。",
        ),
        DeclareLaunchArgument(
            "twist_mux_params_file",
            default_value="",
            description="twist_mux のパラメータファイル。空なら "
                        "src/daifuku_config/bringup/robot/twist_mux.yaml を使う。",
        ),
        DeclareLaunchArgument(
            "joy",
            default_value="true",
            description="ゲームパッド (XInput 互換) での手動走行を立てるか。true なら "
                        "joy_node と joy_teleop が上がり、START 2 秒長押しで "
                        "/cmd_vel_teleop への出力を入/切、BACK 単体の 2 秒長押しで "
                        "モータ電源の入/切、START+BACK 同時 2 秒で "
                        "保存したウェイポイントの巡回を始める。挿していなくても "
                        "他のノードは動く (joy_teleop は /joy が来なければ何も "
                        "publish しない。joy_node は respawn 付き)。",
        ),
        DeclareLaunchArgument(
            "use_mid360_imu",
            default_value=env_bool_default("USE_MID360_IMU", "true"),
            description="Mid-360 の IMU 融合に合わせた配線にするか。true にすると "
                        "ドライバは車輪オドメトリを wheel_odom_topic へ出し、"
                        "odom -> base_footprint TF を出さなくなる (この launch が "
                        "include する odom_fusion.launch.py の EKF が両方を出す)。"
                        "既定は環境変数 USE_MID360_IMU。",
        ),
        DeclareLaunchArgument(
            "wheel_odom_topic",
            default_value="/wheel/odom",
            description="use_mid360_imu:=true のときにドライバが車輪オドメトリを "
                        "出すトピック (EKF の入力)。",
        ),
        DeclareLaunchArgument(
            "joy_teleop_params_file",
            default_value="",
            description="ゲームパッドのパラメータファイル (joy_node と joy_teleop の "
                        "両方に渡る)。空なら src/daifuku_config/bringup/robot/joy_teleop.yaml を使う。",
        ),
        *params.declare_args(),
        params.declare_watch_arg(),
        *lidar_common.declare_shared_args(),
        DeclareLaunchArgument("use_sim_time", default_value="false"),
        # URDF の 2D LiDAR リンク。Mid-360 の lidar_frame とは別物。
        DeclareLaunchArgument("urdf_lidar_frame", default_value="lidar_link"),
        DeclareLaunchArgument("use_joint_state_publisher", default_value="True"),

        OpaqueFunction(function=validate),

        robot_state_publisher_launch,
        odom_fusion_launch,
        lidar_common.include_lidar_bringup(pkg_share),
        OpaqueFunction(function=launch_setup),
    ])
