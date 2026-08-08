cmake_minimum_required(VERSION 3.8)
project(daifuku_stack)

find_package(ament_cmake REQUIRED)

# src も share へ入れるが、**起動には要らない** (立てるものは下の
# install(PROGRAMS) で lib/ に入り、launch はそちらを executable= で指す)。
# 実機で中身を読む・単体で叩くための控えなので、消しても起動は壊れない。
# launch/daifuku_stack_launch/ を import すると launch/ の下に __pycache__ が
# できる。install には要らないので落とす。
install(DIRECTORY behavior_trees launch maps rviz src waypoints
  DESTINATION share/${PROJECT_NAME}
  PATTERN "__pycache__" EXCLUDE
)

install(PROGRAMS
  src/system_monitor.py
  DESTINATION lib/${PROJECT_NAME}
)

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  ament_lint_auto_find_test_dependencies()
endif()

ament_package()
