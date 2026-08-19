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

# 機体の起動。**docker compose up で立つのはこれ**（raspicat サービス）。
#
# 「機体としてのロボット」を丸ごと持つ: 駆動ドライバ、URDF/TF、cmd_vel の仲裁、
# ゲームパッド、**LiDAR 一式**、**オドメトリ融合**。ナビゲーション
# (navigation.launch.py / mapping.launch.py) はここが出すトピックの消費者に徹し、
# センサは一切立てない。
#
#   include lidar_bringup.launch.py   LiDAR -> /scan
#   include odom_fusion.launch.py     車輪 + IMU -> /odom, odom -> base_footprint
#
# LiDAR と EKF をここに置いてあるのは、navigation を立て直すたびに EKF が再起動して
# /odom が原点へ飛ぶのを避けるため。**ドライバが finalized まで落ちるとこの launch
# ごと終了する**ので、駆動の障害はセンサも道連れにする (下の
# register_shutting_down_transition。詳細は AGENTS.md)。
#
# 上流 raspicat_ros の raspicat.launch.py 相当だが、raspimouse ノードを自前で
# 立てている (上流の parameters= に勝てないため。src/daifuku_config/bringup/robot/raspicat.yaml
# のコメント)。
#
# 自前化のついでに urg_node 関連は落としてある (LiDAR はどちらの構成でも
# lidar_bringup.launch.py が扱う)。robot_state_publisher / joint_state_publisher は
# パラメータ競合が無いので上流の launch をそのまま include する。
#
# driver:= で公式実装と自前実装のどちらを立てるか選ぶ。どちらも同じ契約 (cmd_vel を
# 購読し、odom と odom -> base_footprint TF を出し、motor_power サービスを持つ
# lifecycle ノード) を満たすので、この launch から下だけが入れ替わる。
#
#   raspimouse (既定) … 公式実装。rtmouse がホストで insmod されている前提
#                       (/dev/rt* が要る)ので Pi 4 のみ。
#   original          … 自前実装 (raspicat_driver)。PWM・gpiochip・I2C をユーザ空間
#                       から直接叩く。Pi 5 は rtmouse が動かないのでこちらしか選べない。
#
# rtmouse と original の排他、twist_mux:= (既定 true) の仲裁と joy:= (既定 true) の
# 行き先、use_mid360_imu:= (既定 true) が odom の担当を EKF へ譲ることと、その EKF を
# 立てるのもこの launch (odom_fusion.launch.py を include する) であることは、
# どれも他のファイルと組で決まるので AGENTS.md「ファイルをまたぐ約束ごと」に集約して
# ある。操作は docs/usage/joystick.md と src/joy_teleop.py、Pi ごとの注意は
# docs/setup/raspberry-pi-4.md と raspberry-pi-5.md。

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

# 共通部品はこの launch ディレクトリの直下 (daifuku_bringup_launch/) にある。
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

    # overrides を重ねる。行き先はノード名で決まるので、driver:= で選ばなかった
    # ほうの節 (raspimouse: / raspicat_driver:) は自動的に外れる。
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

    # 仲裁を挟むときだけ、ドライバの購読先を仲裁の出力へ振り替える。両ドライバとも
    # 相対名の "cmd_vel" で購読している (raspimouse_component.cpp / raspicat_driver の
    # node.py) ので、この remap で両方に効く。
    mux_actions, mux_logs = _twist_mux(context)
    remappings = [("cmd_vel", MUXED_CMD_VEL)] if mux_actions else []

    # use_mid360_imu:=true では odom -> base_footprint の所有者が EKF に移る。
    # ドライバは車輪の生値を wheel_odom_topic へ出すだけの立場になるので、
    # トピックを振り替えたうえで TF を止める。**止め方がドライバで違う**:
    # 自前実装には publish_tf があるが、公式実装 (raspimouse) には無いので
    # ノードの /tf を捨て先へ remap する (あちらが出す TF はこれだけ)。
    #
    # EKF を立てるのは同じ launch が include する odom_fusion.launch.py で、同じ
    # use_mid360_imu を見る。**片方だけ true という状態は作れない。**
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

    # 今どこで走らせるかを ROS から読み書きできるようにする。**立てるのはここ
    # 1 つだけ** — navigation 側にも置くと、2 つのノードが同じ src/daifuku_config/site を
    # 書きに行く。機体は docker compose で常駐しているので、人が navigation を
    # 立てていないあいだも `ros2 param set /site_manager site <名前>` が通る。
    site_manager = Node(
        package="daifuku_config_manager",
        executable="site_manager",
        name="site_manager",
        output="screen",
    )

    # 起動後に設定が書き変わったら言い、追随してよければこの launch を落とす。
    # **落ちたあと上げ直すのは compose の restart: unless-stopped**（この launch は
    # raspicat サービスの PID 1）。ドライバが finalized で落ちたときと同じ経路で、
    # LiDAR も EKF も一緒に上がり直す。
    sentinel = params.sentinel_actions(
        context, package="daifuku_bringup",
        config_root=params.config_root("daifuku_bringup"),
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

    # URDF / TF ツリー。上流のものをそのまま使う (競合するパラメータが無い)。
    #
    # **GroupAction で囲むこと。** ここで渡す上流の引数名 `lidar_frame` は
    # lidar_bringup.launch.py が Mid-360 用に宣言している名前とぶつかっていて、
    # 素で並べると向こうの既定 (livox_frame) が入らず、IMU の TF が消える。
    # 何がどう壊れるかは AGENTS.md (IncludeLaunchDescription の項)。
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

    # 車輪オドメトリと Mid-360 IMU の融合。**ドライバと同じ launch に置くのが要点**で、
    # use_mid360_imu 1 つで「ドライバが TF を止める」と「EKF が TF を出す」が同時に
    # 切り替わる。use_mid360_imu:=false なら向こうで何も立たない。
    odom_fusion_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(pkg_share, "launch", "odom_fusion.launch.py")
        ),
        launch_arguments=[
            (name, LaunchConfiguration(name))
            for name in ("use_mid360_imu", "wheel_odom_topic", "use_sim_time",
                         "overrides", "extra_params_file")
        ],
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
            # 既定は環境変数から取る。Compose が .env の 1 行を raspicat と ros2 の
            # 両サービスへ配るので、2 つの launch を人手で揃えなくてよくなる。
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
        # LiDAR 構成の引数 (lidar / lidar_driver / scan_filter_* / mid360_* /
        # publish_lidar_tf / lidar_x..yaw / urg_*)。daifuku_bringup_launch/lidar.py。
        *lidar_common.declare_shared_args(),
        DeclareLaunchArgument("use_sim_time", default_value="false"),
        # URDF が持つ 2D LiDAR のリンク名 (上流 raspicat_description)。
        # **lidar_bringup.launch.py の lidar_frame (Mid-360 の livox_frame) とは
        # 別物。** ここで名前を分けているのは人が読むためだけで、漏れを止めて
        # いるのは上の GroupAction のほう (上流へ渡すときの名前は lidar_frame の
        # ままなので、囲まないと名前を分けても同じことが起きる)。
        DeclareLaunchArgument("urdf_lidar_frame", default_value="lidar_link"),
        DeclareLaunchArgument("use_joint_state_publisher", default_value="True"),

        OpaqueFunction(function=validate),

        robot_state_publisher_launch,
        odom_fusion_launch,
        lidar_common.include_lidar_bringup(pkg_share),
        OpaqueFunction(function=launch_setup),
    ])
