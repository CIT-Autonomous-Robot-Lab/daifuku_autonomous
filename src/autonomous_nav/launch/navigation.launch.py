import os

from ament_index_python.packages import get_package_share_directory
from ament_index_python.packages import get_package_prefix
from ament_index_python.packages import PackageNotFoundError
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    GroupAction,
    IncludeLaunchDescription,
    OpaqueFunction,
    SetEnvironmentVariable,
)
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node
from launch_ros.actions import PushRosNamespace
from launch_ros.descriptions import ParameterFile
from nav2_common.launch import RewrittenYaml


def generate_launch_description():
    pkg_share = get_package_share_directory("autonomous_nav")
    nav2_share = get_package_share_directory("nav2_bringup")

    default_params = os.path.join(pkg_share, "config", "nav2_params.yaml")
    default_emcl2_params = os.path.join(pkg_share, "config", "emcl2_params.yaml")
    default_rviz_config = os.path.join(pkg_share, "rviz", "nav2_default.rviz")
    default_map = os.path.join(pkg_share, "maps", "map.yaml")
    bringup_launch = os.path.join(nav2_share, "launch", "bringup_launch.py")
    navigation_launch = os.path.join(nav2_share, "launch", "navigation_launch.py")

    namespace = LaunchConfiguration("namespace")
    use_namespace = LaunchConfiguration("use_namespace")
    map_yaml = LaunchConfiguration("map")
    params_file = LaunchConfiguration("params_file")
    emcl2_params_file = LaunchConfiguration("emcl2_params_file")
    rviz_config = LaunchConfiguration("rviz_config")
    use_sim_time = LaunchConfiguration("use_sim_time")
    autostart = LaunchConfiguration("autostart")
    use_composition = LaunchConfiguration("use_composition")
    use_respawn = LaunchConfiguration("use_respawn")
    log_level = LaunchConfiguration("log_level")
    use_rviz = LaunchConfiguration("use_rviz")
    localization = LaunchConfiguration("localization")
    emcl2_package = LaunchConfiguration("emcl2_package")
    emcl2_executable = LaunchConfiguration("emcl2_executable")
    emcl2_node_name = LaunchConfiguration("emcl2_node_name")

    use_amcl = PythonExpression(["'", localization, "' == 'amcl'"])
    use_emcl2 = PythonExpression(["'", localization, "' in ['emcl', 'emcl2']"])

    remappings = [("/tf", "tf"), ("/tf_static", "tf_static")]
    emcl2_remappings = remappings + [
        ("particlecloud", "particle_cloud"),
        ("global_localization", "reinitialize_global_localization"),
    ]

    configured_map_server_params = ParameterFile(
        RewrittenYaml(
            source_file=params_file,
            root_key=namespace,
            param_rewrites={
                "use_sim_time": use_sim_time,
                "yaml_filename": map_yaml,
            },
            convert_types=True,
        ),
        allow_substs=True,
    )

    configured_nav2_params = ParameterFile(
        RewrittenYaml(
            source_file=params_file,
            root_key=namespace,
            param_rewrites={
                "use_sim_time": use_sim_time,
                "autostart": autostart,
            },
            convert_types=True,
        ),
        allow_substs=True,
    )

    def validate_map_file(context, *args, **kwargs):
        map_path = map_yaml.perform(context)
        if not os.path.isfile(map_path):
            raise RuntimeError(
                f"Map YAML file does not exist: {map_path}\n"
                "Pass a real map path, for example: "
                "map:=$PWD/src/autonomous_nav/maps/map.yaml"
            )
        return []

    def validate_localization(context, *args, **kwargs):
        selected = localization.perform(context)
        if selected not in ("amcl", "emcl", "emcl2"):
            raise RuntimeError(
                f"Unsupported localization: {selected}\n"
                "Use localization:=amcl or localization:=emcl2."
            )

        if selected in ("emcl", "emcl2"):
            package_name = emcl2_package.perform(context)
            try:
                get_package_prefix(package_name)
            except PackageNotFoundError as exc:
                raise RuntimeError(
                    f"EMCL2 package is not available: {package_name}\n"
                    "Clone/build the emcl2_ros2 repository in this workspace or source an "
                    "underlay that provides it before launching with localization:=emcl2."
                ) from exc
        return []

    return LaunchDescription([
        SetEnvironmentVariable("RCUTILS_LOGGING_BUFFERED_STREAM", "1"),

        DeclareLaunchArgument("namespace", default_value=""),
        DeclareLaunchArgument("use_namespace", default_value="false"),
        DeclareLaunchArgument(
            "map",
            default_value=default_map,
            description="Full path to the map yaml file.",
        ),
        DeclareLaunchArgument("params_file", default_value=default_params),
        DeclareLaunchArgument("emcl2_params_file", default_value=default_emcl2_params),
        DeclareLaunchArgument("rviz_config", default_value=default_rviz_config),
        DeclareLaunchArgument("use_sim_time", default_value="false"),
        DeclareLaunchArgument("autostart", default_value="true"),
        DeclareLaunchArgument("use_composition", default_value="True"),
        DeclareLaunchArgument("use_respawn", default_value="False"),
        DeclareLaunchArgument("log_level", default_value="info"),
        DeclareLaunchArgument("use_rviz", default_value="true"),
        DeclareLaunchArgument(
            "localization",
            default_value="emcl2",
            description="Localization backend: amcl or emcl2.",
        ),
        DeclareLaunchArgument("emcl2_package", default_value="emcl2"),
        DeclareLaunchArgument("emcl2_executable", default_value="emcl2_node"),
        DeclareLaunchArgument("emcl2_node_name", default_value="emcl2"),

        OpaqueFunction(function=validate_map_file),
        OpaqueFunction(function=validate_localization),

        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(bringup_launch),
            condition=IfCondition(use_amcl),
            launch_arguments={
                "namespace": namespace,
                "use_namespace": use_namespace,
                "slam": "False",
                "map": map_yaml,
                "use_sim_time": use_sim_time,
                "params_file": params_file,
                "autostart": autostart,
                "use_composition": use_composition,
                "use_respawn": use_respawn,
                "log_level": log_level,
            }.items(),
        ),

        GroupAction(
            condition=IfCondition(use_emcl2),
            actions=[
                PushRosNamespace(
                    condition=IfCondition(use_namespace),
                    namespace=namespace,
                ),
                Node(
                    condition=IfCondition(use_composition),
                    name="nav2_container",
                    package="rclcpp_components",
                    executable="component_container_isolated",
                    parameters=[configured_nav2_params, {"autostart": autostart}],
                    arguments=["--ros-args", "--log-level", log_level],
                    remappings=remappings,
                    output="screen",
                ),
                Node(
                    package="nav2_map_server",
                    executable="map_server",
                    name="map_server",
                    output="screen",
                    respawn=use_respawn,
                    respawn_delay=2.0,
                    parameters=[configured_map_server_params],
                    arguments=["--ros-args", "--log-level", log_level],
                    remappings=remappings,
                ),
                Node(
                    package=emcl2_package,
                    executable=emcl2_executable,
                    name=emcl2_node_name,
                    output="screen",
                    respawn=use_respawn,
                    respawn_delay=2.0,
                    parameters=[
                        emcl2_params_file,
                        {"use_sim_time": use_sim_time},
                    ],
                    arguments=["--ros-args", "--log-level", log_level],
                    remappings=emcl2_remappings,
                ),
                Node(
                    package="nav2_lifecycle_manager",
                    executable="lifecycle_manager",
                    name="lifecycle_manager_map_server",
                    output="screen",
                    arguments=["--ros-args", "--log-level", log_level],
                    parameters=[
                        {"use_sim_time": use_sim_time},
                        {"autostart": autostart},
                        {"node_names": ["map_server"]},
                    ],
                ),
                IncludeLaunchDescription(
                    PythonLaunchDescriptionSource(navigation_launch),
                    launch_arguments={
                        "namespace": namespace,
                        "use_sim_time": use_sim_time,
                        "params_file": params_file,
                        "autostart": autostart,
                        "use_composition": use_composition,
                        "use_respawn": use_respawn,
                        "container_name": "nav2_container",
                        "log_level": log_level,
                    }.items(),
                ),
            ],
        ),

        Node(
            condition=IfCondition(use_rviz),
            package="rviz2",
            executable="rviz2",
            name="rviz2",
            namespace=namespace,
            output="screen",
            arguments=["-d", rviz_config],
            parameters=[{"use_sim_time": use_sim_time}],
        ),
    ])
