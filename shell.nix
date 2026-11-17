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
in
pkgs.mkShell {
  packages = [
    (pkgs.rosPackages.humble.buildEnv {
      paths = with pkgs.rosPackages.humble; [
        pkgs.colcon
        ament-cmake-core
        python-cmake-module
        desktop
        rviz2
        rviz-default-plugins
      ];
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
