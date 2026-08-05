"""daifuku_stack の launch ファイルが共有する部品。

launch/ の直下に置いてあるので、CMakeLists.txt の
`install(DIRECTORY launch ...)` でそのまま share/daifuku_stack/launch/ へ入る。
colcon の --symlink-install では src/ への symlink になるため、ここを直しても
再ビルドは要らない (launch / config と同じ扱い)。

各 launch ファイルは先頭で launch ディレクトリを sys.path へ入れてから
import する。パッケージ名を長めにしてあるのは、sys.path が他パッケージの
launch ディレクトリと混ざったときにモジュール名が衝突しないようにするため。

  params.py    nav2 / emcl2 のパラメータ合成 (navigation)
  backends.py  localization / planner バックエンドの選択と検証 (navigation)
  lidar.py     LiDAR 構成の共通引数と include (navigation / mapping / lidar_bringup)
"""

import os

from launch.substitutions import LaunchConfiguration


# 環境変数を真偽値として読むときに受ける綴り。Compose の .env に人が書くので、
# docker-compose 自身や systemd と同じくらいの幅を受けておく。
_TRUE_WORDS = ("true", "1", "yes", "on")
_FALSE_WORDS = ("false", "0", "no", "off")


def env_bool_default(name, fallback):
    """環境変数を launch 引数の既定値 ("true" / "false") に変える。

    Compose の .env から複数の launch へ同じ値を配るために使う。**知らない
    綴りは弾く**: 既定へ落とすと、`USE_MID360_IMU=1` のつもりが false のまま
    走って、エラーも警告も出ないまま構成が変わる。

    Args:
        name: 環境変数名。未設定または空なら fallback をそのまま返す。
        fallback: 環境変数が無いときの既定値 ("true" / "false")。

    Returns:
        正規化した "true" か "false"。
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return fallback
    text = raw.strip().lower()
    if text in _TRUE_WORDS:
        return "true"
    if text in _FALSE_WORDS:
        return "false"
    raise RuntimeError(
        "%s=%r は真偽値として読めません。%s のいずれかを書いてください。"
        % (name, raw, " / ".join(_TRUE_WORDS + _FALSE_WORDS))
    )


def value(context, name):
    """起動引数を文字列として取り出す (OpaqueFunction の中で使う)。"""
    return LaunchConfiguration(name).perform(context)


def is_true(context, name):
    """起動引数を真偽値として取り出す。"true"/"True" のどちらでも受ける。"""
    return value(context, name).lower() == "true"
