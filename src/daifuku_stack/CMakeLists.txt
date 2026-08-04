cmake_minimum_required(VERSION 3.8)
project(daifuku_stack)

find_package(ament_cmake REQUIRED)

# src も share へ入れる: lidar_bringup.launch.py が
# share/daifuku_stack/src/restamp_scan.py を ExecuteProcess で直接叩く
# (lidar:=mid360 のときのみ)。
# launch/daifuku_stack_launch/ を import すると launch/ の下に __pycache__ が
# できる。install には要らないので落とす。
install(DIRECTORY behavior_trees config launch maps rviz src waypoints
  DESTINATION share/${PROJECT_NAME}
  PATTERN "__pycache__" EXCLUDE
)

install(PROGRAMS
  src/prepare_mid360_imu.py
  src/system_monitor.py
  DESTINATION lib/${PROJECT_NAME}
)

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  ament_lint_auto_find_test_dependencies()
endif()

ament_package()
