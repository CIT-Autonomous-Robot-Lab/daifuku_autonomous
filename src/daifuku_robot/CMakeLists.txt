cmake_minimum_required(VERSION 3.19)
project(daifuku_robot)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)
find_package(rclcpp REQUIRED)
find_package(sensor_msgs REQUIRED)

add_executable(imu_throttle src/imu_throttle.cpp)
target_compile_features(imu_throttle PRIVATE cxx_std_17)
ament_target_dependencies(imu_throttle rclcpp sensor_msgs)
install(TARGETS imu_throttle DESTINATION lib/${PROJECT_NAME})

ament_python_install_package(daifuku_robot_launch
  PACKAGE_DIR launch/daifuku_robot_launch
)
install(DIRECTORY launch src
  DESTINATION share/${PROJECT_NAME}
  PATTERN "__pycache__" EXCLUDE
)
foreach(NODE_SCRIPT prepare_mid360_imu.py joy_teleop.py)
  configure_file(scripts/node.in "${NODE_SCRIPT}" @ONLY)
  file(CHMOD "${CMAKE_CURRENT_BINARY_DIR}/${NODE_SCRIPT}"
    PERMISSIONS OWNER_READ OWNER_WRITE OWNER_EXECUTE GROUP_READ GROUP_EXECUTE
    WORLD_READ WORLD_EXECUTE)
  install(PROGRAMS "${CMAKE_CURRENT_BINARY_DIR}/${NODE_SCRIPT}"
    DESTINATION lib/${PROJECT_NAME})
endforeach()
install(FILES
  src/joy_buttons.py
  src/joy_waypoints.py
  src/joy_motor.py
  src/joy_patrol.py
  DESTINATION lib/${PROJECT_NAME}
)

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  ament_lint_auto_find_test_dependencies()
  find_package(launch_testing_ament_cmake REQUIRED)
  add_launch_test(test/test_imu_throttle_launch.py TIMEOUT 60
    ENV ROS_DOMAIN_ID=77 ROS_LOCALHOST_ONLY=1)
endif()

ament_package()
