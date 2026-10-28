cmake_minimum_required(VERSION 3.19)
project(daifuku_robot)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)

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
endif()

ament_package()
