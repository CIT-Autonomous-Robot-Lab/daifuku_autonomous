cmake_minimum_required(VERSION 3.8)
project(daifuku_mapping)

find_package(ament_cmake REQUIRED)

install(DIRECTORY launch DESTINATION share/${PROJECT_NAME})

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  ament_lint_auto_find_test_dependencies()
  find_package(ament_cmake_pytest REQUIRED)
  ament_add_pytest_test(test_launch test/test_launch.py
    ENV ROS_DOMAIN_ID=77 ROS_LOCALHOST_ONLY=1
  )
endif()

ament_package()
