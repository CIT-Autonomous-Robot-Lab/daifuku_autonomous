cmake_minimum_required(VERSION 3.19)
project(daifuku_perception)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)

ament_python_install_package(daifuku_perception_launch
  PACKAGE_DIR launch/daifuku_perception_launch
)
install(DIRECTORY launch src
  DESTINATION share/${PROJECT_NAME}
  PATTERN "__pycache__" EXCLUDE
)
foreach(NODE_SCRIPT elevation_filter.py restamp_scan.py)
  configure_file(scripts/node.in "${NODE_SCRIPT}" @ONLY)
  file(CHMOD "${CMAKE_CURRENT_BINARY_DIR}/${NODE_SCRIPT}"
    PERMISSIONS OWNER_READ OWNER_WRITE OWNER_EXECUTE GROUP_READ GROUP_EXECUTE
    WORLD_READ WORLD_EXECUTE)
  install(PROGRAMS "${CMAKE_CURRENT_BINARY_DIR}/${NODE_SCRIPT}"
    DESTINATION lib/${PROJECT_NAME})
endforeach()

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  ament_lint_auto_find_test_dependencies()
  find_package(ament_cmake_pytest REQUIRED)
  ament_add_pytest_test(test_elevation_filter test/test_elevation_filter.py
    ENV ROS_DOMAIN_ID=77 ROS_LOCALHOST_ONLY=1
  )
endif()

ament_package()
