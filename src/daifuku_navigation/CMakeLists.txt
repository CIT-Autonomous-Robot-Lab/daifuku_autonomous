cmake_minimum_required(VERSION 3.8)
project(daifuku_navigation)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)
ament_python_install_package(daifuku_navigation_launch)
install(DIRECTORY launch DESTINATION share/${PROJECT_NAME}
  PATTERN "__pycache__" EXCLUDE)

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  ament_lint_auto_find_test_dependencies()
  find_package(ament_cmake_pytest REQUIRED)
  ament_add_pytest_test(navigation_launch test/test_navigation_launch.py)
endif()

ament_package()
