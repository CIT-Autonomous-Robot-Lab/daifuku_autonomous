{ nixosConfig ? builtins.getEnv "HOME" + "/nixos-config" }:

let
  configuration = builtins.getFlake (toString nixosConfig);
  rosShell = configuration.devShells.${builtins.currentSystem}.ros2-humble;
in
rosShell.overrideAttrs (old: {
  shellHook = (old.shellHook or "") + ''
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
})
