cmake_minimum_required(VERSION 3.19)
project(daifuku_localization)

find_package(ament_cmake REQUIRED)
install(DIRECTORY launch src DESTINATION share/${PROJECT_NAME}
  PATTERN "__pycache__" EXCLUDE)
set(NODE_SCRIPT localization_gravity.py)
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
  ament_add_pytest_test(localization_launch test/test_localization_launch.py)
endif()

ament_package()
