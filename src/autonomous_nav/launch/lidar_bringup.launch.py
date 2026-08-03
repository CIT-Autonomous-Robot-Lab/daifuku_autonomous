import os

from ament_index_python.packages import get_package_share_directory
from ament_index_python.packages import PackageNotFoundError
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    ExecuteProcess,
    OpaqueFunction,
    SetLaunchConfiguration,
)
from launch.conditions import IfCondition, UnlessCondition
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    pkg_share = get_package_share_directory("autonomous_nav")

    lidar = LaunchConfiguration("lidar")
    use_sim_time = LaunchConfiguration("use_sim_time")
    scan_filter_enabled = LaunchConfiguration("scan_filter_enabled")
    scan_filter_params_file = LaunchConfiguration("scan_filter_params_file")
    mid360_config = LaunchConfiguration("mid360_config")
    mid360_scan_params_file = LaunchConfiguration("mid360_scan_params_file")
    use_mid360_imu = LaunchConfiguration("use_mid360_imu")
    mid360_ekf_params_file = LaunchConfiguration("mid360_ekf_params_file")
    wheel_odom_topic = LaunchConfiguration("wheel_odom_topic")
    odom_topic = LaunchConfiguration("odom_topic")
    publish_lidar_tf = LaunchConfiguration("publish_lidar_tf")
    lidar_frame = LaunchConfiguration("lidar_frame")
    base_frame = LaunchConfiguration("base_frame")
    urg_interface = LaunchConfiguration("urg_interface")
    urg_params_file = LaunchConfiguration("urg_params_file")

    lidar_driver = LaunchConfiguration("lidar_driver")

    # lidar_driver:=false は「LiDAR の生データを外部が出す」構成。
    # シミュレータ (simulator) は /livox/lidar を PointCloud2 で直接出すので
    # livox_ros_driver2 を立てない。実機の driver は xfer_format:=0 = PointCloud2 な
    # ので、ドライバの出力とシムの出力は同型で、下流 (pointcloud_to_laserscan 以降)
    # は一切変わらない。
    #
    # restamp_scan.py も同時に外す。あれは MID360 の**デバイス時計が PTP 同期されず
    # 毎分数秒ドリフトする**ことへの対処であって、シムには存在しない問題である。
    # とくに use_sim_time:=true では「受信時刻で押し直す」動作がシム時間と噛み合わず
    # 積極的に有害になる。
    is_mid360 = PythonExpression(["'", lidar, "' == 'mid360'"])
    use_livox_driver = PythonExpression([
        "'", lidar, "' == 'mid360' and '", lidar_driver, "'.lower() == 'true'",
    ])
    # lidar:=2d は raspicat の URG (Hokuyo) ドライバを立てる。lidar_driver:=false
    # (シミュレータ) では /scan_raw を外部が出すので起動しない。
    use_urg_driver = PythonExpression([
        "'", lidar, "' == '2d' and '", lidar_driver, "'.lower() == 'true'",
    ])
    use_mid360_ekf = PythonExpression([
        "'", lidar, "' == 'mid360' and '", use_mid360_imu,
        "'.lower() == 'true'",
    ])
    publish_mid360_tf = PythonExpression([
        "'", lidar, "' == 'mid360' and '", publish_lidar_tf,
        "'.lower() == 'true'",
    ])

    def validate(context, *args, **kwargs):
        selected = lidar.perform(context)
        if selected not in ("2d", "mid360"):
            raise RuntimeError(
                f"Unsupported lidar: {selected}. Use lidar:=2d or lidar:=mid360."
            )

        actions = []
        files = []
        if scan_filter_enabled.perform(context).lower() == "true":
            files.append(
                ("scan_filter_params_file", scan_filter_params_file.perform(context))
            )
        if selected == "2d" and lidar_driver.perform(context).lower() == "true":
            # 既定のパラメータは上流 raspicat_bringup のもの
            # (config/urg_<urg_interface>.param.yaml)。raspicat_bringup が
            # 無い環境でも lidar:=mid360 は起動できるよう、share ディレクトリの
            # 解決はここ (2d を選んだときだけ) まで遅らせる。
            path = urg_params_file.perform(context)
            if not path:
                interface = urg_interface.perform(context)
                if interface not in ("serial", "ethernet"):
                    raise RuntimeError(
                        f"Unsupported urg_interface: {interface}. "
                        "Use urg_interface:=serial or urg_interface:=ethernet."
                    )
                try:
                    urg_share = get_package_share_directory("raspicat_bringup")
                except PackageNotFoundError as exc:
                    raise RuntimeError(
                        "raspicat_bringup package is not available, so the default "
                        "URG parameters cannot be located.\n"
                        "Import raspicat_ros (vcs import src < autonomous_bot.repos) "
                        "and build it, or pass urg_params_file:=<path> explicitly."
                    ) from exc
                path = os.path.join(
                    urg_share, "config", f"urg_{interface}.param.yaml"
                )
                actions.append(SetLaunchConfiguration("urg_params_file", path))
            files.append(("urg_params_file", path))
        if selected == "mid360":
            files.append(
                ("mid360_scan_params_file", mid360_scan_params_file.perform(context))
            )
            # MID360_config.json は livox_ros_driver2 のためだけのもの。
            # lidar_driver:=false (シム) では driver を立てないので要求しない。
            if lidar_driver.perform(context).lower() == "true":
                files.append(("mid360_config", mid360_config.perform(context)))
            if use_mid360_imu.perform(context).lower() == "true":
                files.append(
                    ("mid360_ekf_params_file", mid360_ekf_params_file.perform(context))
                )
        for label, path in files:
            if not os.path.isfile(path):
                raise RuntimeError(f"{label} does not exist: {path}")
        return actions

    return LaunchDescription([
        DeclareLaunchArgument(
            "lidar",
            default_value="mid360",
            description="LiDAR backend: mid360 (既定。本機の構成) または "
                        "2d (raspicat の URG を起動する)。",
        ),
        DeclareLaunchArgument("use_sim_time", default_value="false"),
        DeclareLaunchArgument(
            "lidar_driver",
            default_value="true",
            description="LiDAR の実機ドライバを起動するか。lidar:=mid360 では "
                        "livox_ros_driver2 と restamp_scan.py、lidar:=2d では "
                        "urg_node が対象。false にすると /livox/lidar と "
                        "/livox/imu (mid360) あるいは /scan_raw (2d) を外部 "
                        "(シミュレータ) が出す前提になる。",
        ),
        DeclareLaunchArgument("scan_raw_topic", default_value="/scan_raw"),
        DeclareLaunchArgument("scan_topic", default_value="/scan"),
        DeclareLaunchArgument("scan_filter_enabled", default_value="true"),
        DeclareLaunchArgument(
            "scan_filter_params_file",
            default_value=os.path.join(pkg_share, "config", "sensors", "scan_filter.yaml"),
        ),
        DeclareLaunchArgument(
            "mid360_config",
            default_value=os.path.join(pkg_share, "config", "sensors", "MID360_config.json"),
        ),
        DeclareLaunchArgument(
            "mid360_scan_params_file",
            default_value=os.path.join(pkg_share, "config", "sensors", "mid360_scan.yaml"),
        ),
        DeclareLaunchArgument("mid360_publish_freq", default_value="10.0"),
        DeclareLaunchArgument("lidar_frame", default_value="livox_frame"),
        DeclareLaunchArgument("base_frame", default_value="base_footprint"),
        # 既定 true だが、実際に TF を出すのは lidar:=mid360 のときだけ
        # (下の publish_mid360_tf)。URDF は base_footprint -> lidar_link
        # (2D LiDAR のフレーム) しか配信しておらず、Mid-360 の livox_frame は
        # 誰も出さないため、mid360 構成では既定で必要になる。URDF 側から
        # livox_frame を配信するようにしたら false にすること (二重配信は不可)。
        DeclareLaunchArgument("publish_lidar_tf", default_value="true"),
        DeclareLaunchArgument("lidar_x", default_value="0.0"),
        DeclareLaunchArgument("lidar_y", default_value="0.0"),
        # 実測 275 mm (2026-08-03)。base_footprint (接地面) から MID360 までの
        # 高さ。publish_lidar_tf:=true のときだけ使う。
        DeclareLaunchArgument("lidar_z", default_value="0.275"),
        DeclareLaunchArgument("lidar_roll", default_value="0.0"),
        DeclareLaunchArgument("lidar_pitch", default_value="0.0"),
        DeclareLaunchArgument("lidar_yaw", default_value="0.0"),
        DeclareLaunchArgument("use_mid360_imu", default_value="true"),
        DeclareLaunchArgument(
            "mid360_ekf_params_file",
            default_value=os.path.join(pkg_share, "config", "sensors", "mid360_ekf.yaml"),
        ),
        DeclareLaunchArgument("wheel_odom_topic", default_value="/wheel/odom"),
        DeclareLaunchArgument("odom_topic", default_value="/odom"),
        DeclareLaunchArgument(
            "urg_interface",
            default_value="serial",
            description="lidar:=2d のときの URG の接続方式: serial または "
                        "ethernet。既定のパラメータファイル名を決める。",
        ),
        DeclareLaunchArgument(
            "urg_params_file",
            default_value="",
            description="URG のパラメータファイル。空なら raspicat_bringup の "
                        "config/urg_<urg_interface>.param.yaml を使う。",
        ),

        OpaqueFunction(function=validate),

        # lidar:=2d の LiDAR ドライバ。上流 raspicat_bringup/launch/urg.launch.py と
        # 同じ urg_node_driver + config/urg_<urg_interface>.param.yaml だが、
        # include ではノードに remapping を足せないため自前で立てている。
        # urg_node は `scan` に出すので、ここでこのファイルの入力 (`scan_raw_topic`)
        # へ寄せる。ノード名はパラメータ YAML のキー (urg_node) に合わせること。
        Node(
            condition=IfCondition(use_urg_driver),
            package="urg_node",
            executable="urg_node_driver",
            name="urg_node",
            output="screen",
            parameters=[urg_params_file, {"use_sim_time": use_sim_time}],
            remappings=[("scan", LaunchConfiguration("scan_raw_topic"))],
        ),

        Node(
            condition=IfCondition(use_livox_driver),
            package="livox_ros_driver2",
            executable="livox_ros_driver2_node",
            name="livox_lidar_publisher",
            output="screen",
            parameters=[{
                "xfer_format": 0,
                "multi_topic": 0,
                "data_src": 0,
                "publish_freq": ParameterValue(
                    LaunchConfiguration("mid360_publish_freq"), value_type=float
                ),
                "output_data_type": 0,
                "frame_id": lidar_frame,
                "user_config_path": mid360_config,
                "cmdline_input_bd_code": "livox0000000001",
            }],
        ),

        Node(
            condition=IfCondition(publish_mid360_tf),
            package="tf2_ros",
            executable="static_transform_publisher",
            name="mid360_static_transform",
            output="screen",
            arguments=[
                "--x", LaunchConfiguration("lidar_x"),
                "--y", LaunchConfiguration("lidar_y"),
                "--z", LaunchConfiguration("lidar_z"),
                "--roll", LaunchConfiguration("lidar_roll"),
                "--pitch", LaunchConfiguration("lidar_pitch"),
                "--yaw", LaunchConfiguration("lidar_yaw"),
                "--frame-id", base_frame,
                "--child-frame-id", lidar_frame,
            ],
        ),

        Node(
            condition=IfCondition(is_mid360),
            package="pointcloud_to_laserscan",
            executable="pointcloud_to_laserscan_node",
            name="pointcloud_to_laserscan",
            output="screen",
            parameters=[
                mid360_scan_params_file,
                {"use_sim_time": use_sim_time, "target_frame": base_frame},
            ],
            remappings=[
                ("cloud_in", "/livox/lidar"),
                # The MID360 device clock is not PTP-synced and drifts by
                # seconds per minute against the system clock, so the raw
                # scan is restamped with the receive time below before it
                # reaches the filter chain and the rest of the stack.
                #
                # lidar_driver:=false (シム) にはそのドリフトが無いので restamp を
                # 挟まず、ここから直接 scan_raw_topic に出す。
                ("scan", PythonExpression([
                    "'/scan_mid360_prestamp' if '", lidar_driver,
                    "'.lower() == 'true' else '", LaunchConfiguration("scan_raw_topic"), "'",
                ])),
            ],
        ),

        ExecuteProcess(
            condition=IfCondition(use_livox_driver),
            cmd=[
                "python3",
                os.path.join(pkg_share, "scripts", "restamp_scan.py"),
                "/scan_mid360_prestamp",
                LaunchConfiguration("scan_raw_topic"),
            ],
            output="screen",
        ),

        Node(
            condition=IfCondition(scan_filter_enabled),
            package="laser_filters",
            executable="scan_to_scan_filter_chain",
            name="scan_to_scan_filter_chain",
            output="screen",
            parameters=[scan_filter_params_file, {"use_sim_time": use_sim_time}],
            remappings=[
                ("scan", LaunchConfiguration("scan_raw_topic")),
                ("scan_filtered", LaunchConfiguration("scan_topic")),
            ],
        ),
        Node(
            condition=UnlessCondition(scan_filter_enabled),
            package="topic_tools",
            executable="relay",
            name="unfiltered_scan_relay",
            output="screen",
            arguments=[
                LaunchConfiguration("scan_raw_topic"),
                LaunchConfiguration("scan_topic"),
            ],
        ),

        Node(
            condition=IfCondition(use_mid360_ekf),
            package="autonomous_nav",
            executable="prepare_mid360_imu.py",
            name="prepare_mid360_imu",
            output="screen",
            parameters=[{"use_sim_time": use_sim_time}],
            remappings=[("imu_in", "/livox/imu"), ("imu_out", "/imu/mid360")],
        ),
        Node(
            condition=IfCondition(use_mid360_ekf),
            package="robot_localization",
            executable="ekf_node",
            name="ekf_filter_node",
            output="screen",
            parameters=[
                mid360_ekf_params_file,
                {
                    "use_sim_time": use_sim_time,
                    "odom0": wheel_odom_topic,
                    "imu0": "/imu/mid360",
                    "base_link_frame": base_frame,
                },
            ],
            remappings=[("odometry/filtered", odom_topic)],
        ),
    ])
