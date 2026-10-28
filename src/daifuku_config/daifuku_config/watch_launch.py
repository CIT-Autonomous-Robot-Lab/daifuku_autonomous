# Copyright 2026 Keita Sekiguchi / nop
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""top-level composition に一つだけ置く設定監視の launch action。"""

from launch.actions import DeclareLaunchArgument, EmitEvent, LogInfo, RegisterEventHandler
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown

from . import value
from .monitoring import config_digest, selected_roots
from .overlay import config_files
from .paths import SENTINEL_RESTART_CODE


def follows_site(context):
    """明示した構成は、次回用の site 選択には追随しない。"""
    return False


def _on_sentinel_exit(event, context):
    """指定した非ゼロの終了コードだけを launch の終了へつなぐ。"""
    if event.returncode == SENTINEL_RESTART_CODE:
        return [
            LogInfo(msg="config_sentinel: 設定が変わったので launch を終了します。"),
            EmitEvent(event=Shutdown(reason="configuration changed")),
        ]
    if event.returncode in (0, -2, -15):
        return []
    return [LogInfo(msg=(
        f"config_sentinel が rc={event.returncode} で終了しました。"
        "以降、設定の書き換えは検出されません。"
    ))]


def declare_watch_arg():
    """設定ファイルの変更時の動作を宣言する。"""
    return DeclareLaunchArgument(
        "config_watch", default_value="shutdown",
        description="shutdown = 検査と静止確認後に終了。warn = 警告のみ。off = 監視なし。",
    )


def sentinel_actions(context, *args, package, config_root, packages=None, files=None,
                     action=None, node_name=None, watch_site=False, **kwargs):
    """選んだ機能の根と入力ファイルだけを、一つの sentinel で監視する。

    files には合成で引数を差し替える前の入力パスを渡す。
    watch_site は場所告知の購読だけを制御し、固定 override の監視は止めない。
    """
    from launch_ros.actions import Node

    if action is None:
        action = value(context, "config_watch").strip().lower()
    if action == "off":
        return []
    if action not in ("shutdown", "warn"):
        raise RuntimeError(f"config_watch:={action} は未対応です (shutdown / warn / off)。")
    marker = "__daifuku_config_sentinel"
    if context.launch_configurations.get(marker):
        raise RuntimeError("top-level composition に config_sentinel は一つだけ置いてください")
    selected = list(selected_roots(package, config_root, packages))
    inputs = list(dict.fromkeys([
        *(files or []),
        *[path.strip() for path in value(context, "extra_params_file").split(",")
          if path.strip()],
    ]))
    site = value(context, "overrides").strip()
    digest = config_digest(site, package, config_root, packages=selected, files=inputs)
    stale = [path for root in selected_roots(package, config_root, selected).values()
             for path in config_files(root)[1]]
    logs = []
    if stale:
        logs.append(LogInfo(msg="config_sentinel: リンク切れの設定を除外しました: "
                            + ", ".join(stale)))
    sentinel = Node(
        package="daifuku_config",
        executable="config_sentinel",
        name=node_name or f"config_sentinel_{package.replace('daifuku_', '')}",
        output="screen",
        parameters=[{
            "package": package,
            "config_root": config_root,
            "packages": selected,
            **({"files": inputs} if inputs else {}),
            "site": site,
            "watch_site": watch_site,
            "follow": follows_site(context),
            "digest": digest,
            "action": action,
        }],
    )
    context.launch_configurations[marker] = "true"
    return logs + [
        sentinel,
        RegisterEventHandler(OnProcessExit(
            target_action=sentinel, on_exit=_on_sentinel_exit,
        )),
    ]
