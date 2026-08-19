cmake_minimum_required(VERSION 3.8)
project(daifuku_config)

find_package(ament_cmake REQUIRED)

# 設定の実体しか持たないパッケージ。**bringup/ と stack/ の段を潰さないこと** —
# ここが config_sentinel の指紋を取る単位 (params.config_root) で、1 つにまとめると
# 片方を直しただけで両方の launch が落ちる。
install(DIRECTORY bringup stack overrides
  DESTINATION share/${PROJECT_NAME}
)
install(FILES site README.md
  DESTINATION share/${PROJECT_NAME}
)

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  ament_lint_auto_find_test_dependencies()
endif()

ament_package()
