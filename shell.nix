{ system ? builtins.currentSystem, gui ? true, vulkan ? false }:

let
  # NixOSの設定とは独立して、ROSと対応するnixpkgsを固定する。
  rosOverlay = builtins.fetchTarball {
    url = "https://github.com/lopsided98/nix-ros-overlay/archive/971fa9ca86c79a4997e7c6b2f9df0cb754e7256a.tar.gz";
    sha256 = "sha256-LxoRIHc9WMcpcGpfmj1sYbwuBa8HjHenE+3N2iUgKTg=";
  };
  nixpkgs = builtins.fetchTarball {
    url = "https://github.com/NixOS/nixpkgs/archive/7a0f122f5090cf4c2ade2a13a0e229d4e19ba71f.tar.gz";
    sha256 = "sha256-ZoxIApko70jCdbH3l20HWXOBaT2HZd87orzd2yJ9dVE=";
  };
  pkgs = import nixpkgs {
    inherit system;
    overlays = [
      (import (rosOverlay + "/overlay.nix"))
      (final: prev: {
        # pytest全体は下げず、Humbleの収集フックだけを現在のPath APIへ合わせる。
        rosPackages = prev.rosPackages // {
          humble = prev.rosPackages.humble.overrideScope (_: rosPrev: {
            launch-testing = rosPrev.launch-testing.overrideAttrs (old: {
              postPatch = (old.postPatch or "") + ''
                substituteInPlace launch_testing/pytest/hooks.py \
                  --replace-fail 'def pytest_pycollect_makemodule(path, parent):' \
                  'def pytest_pycollect_makemodule(module_path, parent):
                    path = module_path'
              '';
            });
          });
        };
        # GraphBLASのJITはCを使うため、Fortranラッパーを実行時の依存へ残さない。
        suitesparse = prev.suitesparse.overrideAttrs (old: {
          cmakeFlags = old.cmakeFlags ++ [
            "-DCMAKE_C_COMPILER=${final.stdenv.cc}/bin/cc"
          ];
        });
        # PCLは点群の入出力・フィルター・近傍検索に使うため、描画用のVTKとQtを外す。
        pclWithQt5 = prev.pclWithQt5.overrideAttrs (old: {
          nativeBuildInputs = final.lib.subtractLists [
            final.qt5.wrapQtAppsHook
          ] old.nativeBuildInputs;
          buildInputs = final.lib.subtractLists [
            final.qt5.qtbase final.libxt
          ] old.buildInputs;
          propagatedBuildInputs = final.lib.subtractLists [
            final.vtkWithQt5
          ] old.propagatedBuildInputs;
          cmakeFlags = old.cmakeFlags ++ [
            "-DWITH_VTK=OFF"
            "-DWITH_QT=NO"
            "-DWITH_OPENGL=OFF"
            "-DWITH_GLEW=OFF"
            "-DBUILD_visualization=OFF"
            "-DBUILD_apps=OFF"
            "-DBUILD_tools=OFF"
            "-DBUILD_examples=OFF"
            "-DBUILD_features=OFF"
            "-DBUILD_keypoints=OFF"
            "-DBUILD_outofcore=OFF"
            "-DBUILD_people=OFF"
            "-DBUILD_recognition=OFF"
            "-DBUILD_registration=OFF"
            "-DBUILD_segmentation=OFF"
            "-DBUILD_stereo=OFF"
            "-DBUILD_surface=OFF"
            "-DBUILD_tracking=OFF"
          ];
        });
      })
    ];
  };
  # ホストとDockerで同じSDKを使い、Livoxドライバのリンク先をそろえる。
  livoxSdk2 = pkgs.stdenv.mkDerivation {
    pname = "livox-sdk2";
    version = "1.3.1";
    src = builtins.fetchTarball {
      url = "https://github.com/Livox-SDK/Livox-SDK2/archive/refs/tags/v1.3.1.tar.gz";
      sha256 = "0vzpqd89pn3r8fajcp72ccys0dqff6x6c7irvrfvav2p5f3s7kaw";
    };
    nativeBuildInputs = [ pkgs.cmake ];
    cmakeFlags = [ "-DCMAKE_POLICY_VERSION_MINIMUM=3.5" ];
    postPatch = ''
      substituteInPlace sdk_core/logger_handler/file_manager.h \
        --replace-fail '#include <string>' '#include <stdint.h>
      #include <string>'
    '';
  };
  # rclrsはRust依存としてコンパイルするため、固定ソースをamentの索引へ載せる。
  ros2Rust = builtins.fetchTarball {
    url = "https://github.com/ros2-rust/ros2_rust/archive/2c6b92671a65a426aec9c3468c069a551329ca1b.tar.gz";
    sha256 = "1iazg3zpjqgpamz5ip03p5fgk5gp4znnwqh0h6f34xsz269xz0ix";
  };
  rclrsSource = pkgs.runCommand "rclrs-source-0.7.0" {
    passthru.rosPackage = true;
  } ''
    mkdir -p "$out/share/rclrs" "$out/share/ament_index/resource_index/packages"
    ln -s ${ros2Rust}/rclrs "$out/share/rclrs/rust"
    ln -s ${ros2Rust}/rclrs/package.xml "$out/share/rclrs/package.xml"
    touch "$out/share/ament_index/resource_index/packages/rclrs"
  '';
  # bindgenと描画が使う共有ABIを保ち、LLVM/Clangの静的アーカイブを持ち込まない。
  llvm = pkgs.llvmPackages.libclang.libllvm;
  llvmRuntime = pkgs.runCommand "${llvm.pname}-${llvm.version}-lib" {} ''
    mkdir -p "$out/lib"
    cp -a ${llvm.lib}/lib/libLLVM.so* "$out/lib/"
  '';
  libclangRuntime = (pkgs.replaceDirectDependencies {
    drv = pkgs.llvmPackages.libclang.lib;
    replacements = [{
      oldDependency = llvm.lib;
      newDependency = llvmRuntime;
    }];
  }).overrideAttrs (old: {
    buildCommand = old.buildCommand + ''
      find "$out/lib" -maxdepth 1 -type f -name '*.a' -delete
    '';
  });
  withSharedLibraries = drv: pkgs.replaceDependencies {
    inherit drv;
    replacements = [
      { oldDependency = llvm.lib; newDependency = llvmRuntime; }
      { oldDependency = pkgs.llvmPackages.libclang.lib; newDependency = libclangRuntime; }
    ];
  };
  # Linux用のビルドにWasm/BPFやrustc内部の開発用情報は持ち込まない。
  minimalRustc = (pkgs.rustc-unwrapped.override {
    enableRustcDev = false;
  }).overrideAttrs (old: {
    configureFlags = builtins.filter
      (flag: !(pkgs.lib.hasPrefix "--target=" flag))
      old.configureFlags
      ++ [ "--target=${pkgs.stdenv.targetPlatform.rust.rustcTargetSpec}" ];
  });
  rustc = pkgs.rustc.override {
    rustc-unwrapped = (pkgs.replaceDirectDependencies {
      drv = minimalRustc;
      replacements = [{ oldDependency = llvm.lib; newDependency = llvmRuntime; }];
    }) // {
      inherit (minimalRustc)
        pname version src llvm llvmPackages targetPlatforms
        targetPlatformsWithHostTools badTargetPlatforms meta tests;
    };
  };
in
pkgs.mkShell ({
  packages = [
    pkgs.bashInteractive
    pkgs.cacert
    pkgs.coreutils
    pkgs.curl
    pkgs.findutils
    pkgs.gawk
    pkgs.git
    pkgs.gnugrep
    pkgs.gnused
    pkgs.iproute2
    pkgs.iputils
    pkgs.procps
    pkgs.util-linux
    pkgs.vcs2l
    livoxSdk2
    pkgs.apr
    pkgs.aprutil
    pkgs.cmake
    # Cargoのラッパーも同じRustを参照させ、旧コンパイラを残さない。
    (pkgs.replaceDirectDependencies {
      drv = pkgs.cargo;
      replacements = [{ oldDependency = pkgs.rustc; newDependency = rustc; }];
    })
    rustc
    pkgs.cargo-ament-build
    pkgs.pkg-config
    libclangRuntime
    (withSharedLibraries (pkgs.rosPackages.humble.buildEnv {
      paths = with pkgs.rosPackages.humble; [
        (pkgs.colcon.withExtensions (with pkgs.python3Packages; [
          colcon-cargo
          colcon-ros-cargo
        ]))
        rclrsSource
        ament-cmake-auto
        ament-cmake-copyright
        ament-cmake-core
        ament-cmake-flake8
        ament-cmake-lint-cmake
        ament-cmake-pytest
        ament-cmake-xmllint
        ament-copyright
        ament-flake8
        ament-lint-auto
        ament-lint-cmake
        ament-xmllint
        launch-testing-ament-cmake
        python-cmake-module
        ros-base
        diagnostic-updater
        grid-map-msgs
        joint-state-publisher
        joy
        laser-filters
        nav2-amcl
        nav2-common
        nav2-lifecycle-manager
        nav2-map-server
        nav2-msgs
        nav2-velocity-smoother
        pcl-conversions
        pointcloud-to-laserscan
        realsense2-camera
        robot-localization
        robot-state-publisher
        rosbag2
        slam-toolbox
        teleop-twist-joy
        teleop-twist-keyboard
        topic-tools
        twist-mux
        urdf
        urg-node
        xacro
      ] ++ pkgs.lib.optionals gui (with pkgs.rosPackages.humble; [
        pkgs.python3Packages.pyqt5
        nav2-rviz-plugins
        rqt-gui-py
        rviz2
        rviz-default-plugins
      ]);
      # colcon-ros-cargoは従来のrust_packages索引からメッセージクレートを探す。
      postBuild = ''
        mkdir -p "$out/share/ament_index/resource_index/rust_packages"
        for manifest in "$out"/share/*/rust/Cargo.toml; do
          package_dir="''${manifest%/rust/Cargo.toml}"
          touch "$out/share/ament_index/resource_index/rust_packages/''${package_dir##*/}"
        done
      '';
    }))
  ] ++ pkgs.lib.optionals gui [
    pkgs.arp-scan
    pkgs.bash-completion
    pkgs.gdb
    (withSharedLibraries pkgs.mesa)
    pkgs.nano
    pkgs.openssh
    pkgs.tmux
    pkgs.xterm
  ] ++ pkgs.lib.optionals vulkan [
    pkgs.glslang
    (withSharedLibraries pkgs.mesa)
    pkgs.vulkan-headers
    pkgs.vulkan-loader
    pkgs.vulkan-tools
  ];

  ROS_DOMAIN_ID = "90";
  ROS_LOCALHOST_ONLY = "0";
  LIBCLANG_PATH = "${libclangRuntime}/lib";

  shellHook = ''
    # ビルド済みの自前プラグイン・ノードを現在のNix環境へ重ねる。
    if [[ -f "${toString ./.}/install/local_setup.bash" ]]; then
      source "${toString ./.}/install/local_setup.bash"
    fi

    # RVizのOGRE描画はGLXを使うため、QtもXWaylandに揃える。
    export QT_QPA_PLATFORM=xcb

    # 認証ファイル名はログインごとに変わる。既存の有効な指定を優先する。
    if [[ -n "''${DISPLAY:-}" && ! -r "''${XAUTHORITY:-}" ]]; then
      _ros_xauth="$(systemctl --user show-environment 2>/dev/null | sed -n 's/^XAUTHORITY=//p')"
      if [[ -n "$_ros_xauth" && -r "$_ros_xauth" && -O "$_ros_xauth" ]]; then
        export XAUTHORITY="$_ros_xauth"
      elif [[ -n "''${XDG_RUNTIME_DIR:-}" ]]; then
        _ros_xauth_files=()
        for _ros_xauth in "$XDG_RUNTIME_DIR"/xauth_*; do
          if [[ -f "$_ros_xauth" && -r "$_ros_xauth" && -O "$_ros_xauth" ]]; then
            _ros_xauth_files+=("$_ros_xauth")
          fi
        done
        # 複数セッションがある場合は推測せず、呼び出し元からの指定を要求する。
        if [[ "''${#_ros_xauth_files[@]}" -eq 1 ]]; then
          export XAUTHORITY="''${_ros_xauth_files[0]}"
        fi
      fi
      if [[ ! -r "''${XAUTHORITY:-}" ]]; then
        echo 'ROS: XAUTHORITYを取得できません。Plasmaのターミナルで開き直してください。' >&2
      fi
      unset _ros_xauth _ros_xauth_files
    fi
  '';
} // pkgs.lib.optionalAttrs gui {
  # 空のコンテナでも英数字・日本語を表示できるフォント設定を共有する。
  FONTCONFIG_FILE = pkgs.makeFontsConf {
    fontDirectories = [ pkgs.dejavu_fonts pkgs.noto-fonts-cjk-sans ];
  };
})
