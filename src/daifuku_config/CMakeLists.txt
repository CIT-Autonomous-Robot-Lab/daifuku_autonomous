cmake_minimum_required(VERSION 3.19)
project(daifuku_config)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)

ament_python_install_package(${PROJECT_NAME})

# CMake の data install を保ち、--symlink-install で設定編集を即座に届ける。
install(DIRECTORY robot perception localization navigation mapping sites
  DESTINATION share/${PROJECT_NAME}
)
install(FILES site README.md
  DESTINATION share/${PROJECT_NAME}
)

# Windows 上のソース実行ビットに依存させない。symlink の先も実行可能にする。
foreach(ENTRY_MODULE site_manager config_sentinel)
  configure_file(scripts/node.in ${ENTRY_MODULE} @ONLY)
  file(CHMOD "${CMAKE_CURRENT_BINARY_DIR}/${ENTRY_MODULE}"
    PERMISSIONS OWNER_READ OWNER_WRITE OWNER_EXECUTE GROUP_READ GROUP_EXECUTE
    WORLD_READ WORLD_EXECUTE)
  install(PROGRAMS "${CMAKE_CURRENT_BINARY_DIR}/${ENTRY_MODULE}"
    DESTINATION lib/${PROJECT_NAME})
endforeach()

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  ament_lint_auto_find_test_dependencies()
  find_package(ament_cmake_pytest REQUIRED)
  ament_add_pytest_test(overlay test/test_overlay.py)
  ament_add_pytest_test(monitoring test/test_monitoring.py)
  ament_add_pytest_test(sentinel test/test_sentinel.py)
  find_package(launch_testing_ament_cmake REQUIRED)
  add_launch_test(test/test_config_sentinel_launch.py TIMEOUT 60
    ENV ROS_DOMAIN_ID=77 ROS_LOCALHOST_ONLY=1)
  add_launch_test(test/test_site_manager_launch.py TIMEOUT 120
    ENV ROS_DOMAIN_ID=77 ROS_LOCALHOST_ONLY=1)
endif()

ament_package()
