cmake_minimum_required(VERSION 3.8)
project(autonomous_nav)

find_package(ament_cmake REQUIRED)

# scripts も share へ入れる: lidar_bringup.launch.py が
# share/autonomous_nav/scripts/restamp_scan.py を ExecuteProcess で直接叩く
# (lidar:=mid360 のときのみ)。
install(DIRECTORY behavior_trees config launch maps rviz scripts
  DESTINATION share/${PROJECT_NAME}
)

install(PROGRAMS
  scripts/prepare_mid360_imu.py
  DESTINATION lib/${PROJECT_NAME}
)

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  ament_lint_auto_find_test_dependencies()
endif()

ament_package()
