cmake_minimum_required(VERSION 3.8)
project(autonomous_nav)

find_package(ament_cmake REQUIRED)

# src も share へ入れる: lidar_bringup.launch.py が
# share/autonomous_nav/src/restamp_scan.py を ExecuteProcess で直接叩く
# (lidar:=mid360 のときのみ)。
# launch/autonomous_nav_launch/ を import すると launch/ の下に __pycache__ が
# できる。install には要らないので落とす。
install(DIRECTORY behavior_trees config launch maps rviz src
  DESTINATION share/${PROJECT_NAME}
  PATTERN "__pycache__" EXCLUDE
)

install(PROGRAMS
  src/prepare_mid360_imu.py
  DESTINATION lib/${PROJECT_NAME}
)

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  ament_lint_auto_find_test_dependencies()
endif()

ament_package()
