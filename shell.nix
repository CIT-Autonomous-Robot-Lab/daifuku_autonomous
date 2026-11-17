{ system ? builtins.currentSystem }:

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
    overlays = [ (import (rosOverlay + "/overlay.nix")) ];
  };
  # Dockerと同じSDKを使い、Livoxドライバがリンクする共有ライブラリを用意する。
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
in
pkgs.mkShell {
  packages = [
    pkgs.vcs2l
    livoxSdk2
    pkgs.cargo
    pkgs.rustc
    pkgs.cargo-ament-build
    pkgs.pkg-config
    pkgs.llvmPackages.libclang
    (pkgs.rosPackages.humble.buildEnv {
      paths = with pkgs.rosPackages.humble; [
        (pkgs.colcon.withExtensions (with pkgs.python3Packages; [
          colcon-cargo
          colcon-ros-cargo
        ]))
        rclrsSource
        ament-cmake-core
        python-cmake-module
        nav2-msgs
        desktop
        rviz2
        rviz-default-plugins
      ];
      # colcon-ros-cargoは従来のrust_packages索引からメッセージクレートを探す。
      postBuild = ''
        mkdir -p "$out/share/ament_index/resource_index/rust_packages"
        for manifest in "$out"/share/*/rust/Cargo.toml; do
          package_dir="''${manifest%/rust/Cargo.toml}"
          touch "$out/share/ament_index/resource_index/rust_packages/''${package_dir##*/}"
        done
      '';
    })
  ];

  shellHook = ''
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
}
