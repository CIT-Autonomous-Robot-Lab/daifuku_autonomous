cmake_minimum_required(VERSION 3.8)
project(daifuku_waypoint_manager)

if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 17)
endif()

find_package(ament_cmake REQUIRED)
find_package(action_msgs REQUIRED)
find_package(emcl3d REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(nav2_msgs REQUIRED)
find_package(nav_msgs REQUIRED)
find_package(pluginlib REQUIRED)
find_package(rclcpp REQUIRED)
find_package(rclcpp_action REQUIRED)
find_package(rviz_common REQUIRED)
find_package(tf2 REQUIRED)
find_package(tf2_ros REQUIRED)
find_package(visualization_msgs REQUIRED)
find_package(yaml-cpp REQUIRED)

find_package(Qt5 REQUIRED COMPONENTS Widgets)
# Panel は Q_OBJECT を持つので moc が要る。切ると vtable 未定義でリンクに失敗する。
set(CMAKE_AUTOMOC ON)

add_library(${PROJECT_NAME} SHARED
  include/daifuku_waypoint_manager/waypoint_manager_panel.hpp
  include/daifuku_waypoint_manager/initial_pose_preset_panel.hpp
  src/waypoint_manager_panel.cpp
  src/initial_pose_preset_panel.cpp
  src/global_estimate_pose_tool.cpp
)

target_include_directories(${PROJECT_NAME} PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>
)

ament_target_dependencies(${PROJECT_NAME}
  action_msgs
  emcl3d
  geometry_msgs
  nav2_msgs
  nav_msgs
  pluginlib
  rclcpp
  rclcpp_action
  rviz_common
  tf2
  tf2_ros
  visualization_msgs
)
target_link_libraries(${PROJECT_NAME} yaml-cpp Qt5::Widgets)

pluginlib_export_plugin_description_file(rviz_common plugin_description.xml)

install(TARGETS ${PROJECT_NAME}
  ARCHIVE DESTINATION lib
  LIBRARY DESTINATION lib
  RUNTIME DESTINATION bin
)
install(DIRECTORY include/ DESTINATION include)
install(DIRECTORY config/ DESTINATION share/${PROJECT_NAME}/config)
install(FILES plugin_description.xml DESTINATION share/${PROJECT_NAME})

if(BUILD_TESTING)
  find_package(ament_cmake_gtest REQUIRED)
  ament_add_gtest(test_global_estimate_pose_tool test/test_global_estimate_pose_tool.cpp)
  ament_target_dependencies(test_global_estimate_pose_tool emcl3d pluginlib rclcpp_action rviz_common)
  target_link_libraries(test_global_estimate_pose_tool Qt5::Widgets)
  set_tests_properties(test_global_estimate_pose_tool PROPERTIES
    ENVIRONMENT "QT_QPA_PLATFORM=offscreen;ROS_DOMAIN_ID=77;ROS_LOCALHOST_ONLY=1")
  find_package(ament_lint_auto REQUIRED)
  ament_lint_auto_find_test_dependencies()
endif()

ament_package()
