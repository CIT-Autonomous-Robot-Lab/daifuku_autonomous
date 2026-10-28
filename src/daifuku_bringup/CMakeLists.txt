cmake_minimum_required(VERSION 3.19)
project(daifuku_bringup)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)
ament_python_install_package(daifuku_bringup_launch
  PACKAGE_DIR launch/daifuku_bringup_launch)

install(DIRECTORY launch src rviz
  DESTINATION share/${PROJECT_NAME}
  PATTERN "__pycache__" EXCLUDE
)

set(NODE_SCRIPT system_monitor.py)
configure_file(scripts/node.in "${NODE_SCRIPT}" @ONLY)
file(CHMOD "${CMAKE_CURRENT_BINARY_DIR}/${NODE_SCRIPT}"
  PERMISSIONS OWNER_READ OWNER_WRITE OWNER_EXECUTE GROUP_READ GROUP_EXECUTE
  WORLD_READ WORLD_EXECUTE)
install(PROGRAMS "${CMAKE_CURRENT_BINARY_DIR}/${NODE_SCRIPT}"
  DESTINATION lib/${PROJECT_NAME})

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  ament_lint_auto_find_test_dependencies()
  find_package(ament_cmake_pytest REQUIRED)
  ament_add_pytest_test(composition test/test_composition.py)
  ament_add_pytest_test(profiles test/test_profiles.py)
  find_package(launch_testing_ament_cmake REQUIRED)
  add_launch_test(test/test_python_nodes_launch.py TIMEOUT 120
    ENV ROS_DOMAIN_ID=77 ROS_LOCALHOST_ONLY=1)
endif()

ament_package()
