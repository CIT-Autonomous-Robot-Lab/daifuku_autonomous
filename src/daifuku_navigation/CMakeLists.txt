cmake_minimum_required(VERSION 3.19)
project(daifuku_navigation)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)
ament_python_install_package(daifuku_navigation_launch)
install(DIRECTORY launch src DESTINATION share/${PROJECT_NAME}
  PATTERN "__pycache__" EXCLUDE)
set(NODE_SCRIPT tf_pose.py)
configure_file(scripts/node.in "${NODE_SCRIPT}" @ONLY)
file(CHMOD "${CMAKE_CURRENT_BINARY_DIR}/${NODE_SCRIPT}"
  PERMISSIONS OWNER_READ OWNER_WRITE OWNER_EXECUTE GROUP_READ GROUP_EXECUTE
  WORLD_READ WORLD_EXECUTE)
install(PROGRAMS "${CMAKE_CURRENT_BINARY_DIR}/${NODE_SCRIPT}"
  DESTINATION lib/${PROJECT_NAME})

if(BUILD_TESTING)
  set(ament_cmake_flake8_CONFIG_FILE
    "${CMAKE_CURRENT_SOURCE_DIR}/../../.flake8")
  find_package(ament_lint_auto REQUIRED)
  ament_lint_auto_find_test_dependencies()
  find_package(ament_cmake_pytest REQUIRED)
  ament_add_pytest_test(navigation_launch test/test_navigation_launch.py)
endif()

ament_package()
