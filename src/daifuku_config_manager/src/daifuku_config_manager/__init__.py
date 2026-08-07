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

"""daifuku の launch ファイルが共有する部品。

**葉のパッケージ。** daifuku_bringup (機体とセンサ) と daifuku_stack (自律移動) の
両方がこれに依存し、その 2 つは互いに依存しない。ここから向こう 2 つを import して
はいけない — した瞬間に循環する。

ここに置くのは「どちらの側にも属さないもの」だけ。パッケージ固有の launch 部品
(daifuku_bringup の lidar.py、daifuku_stack の backends.py) は各パッケージの
launch/ の下に残してある。

  params.py           設定ファイルへの overrides の合成
  site_manager.py     今どこか (config/site) を ROS から読み書きできるようにするノード
  config_sentinel.py  起動時に読んだ設定が書き変わっていないかを見張るノード

**ノードを 2 つ持つが、葉であることは変わらない。** どちらも向こう 2 つの
パッケージを import せず、見るのは overrides と、呼び元から渡された config_root
だけ (パスの解決は ament の索引を引くだけで、依存にはならない)。
"""

import os

from launch.substitutions import LaunchConfiguration


# 環境変数を真偽値として読むときに受ける綴り。Compose の .env に人が書くので、
# docker-compose 自身や systemd と同じくらいの幅を受けておく。
_TRUE_WORDS = ("true", "1", "yes", "on")
_FALSE_WORDS = ("false", "0", "no", "off")


def env_bool_default(name, fallback):
    """環境変数を launch 引数の既定値 ("true" / "false") に変える。

    Compose の .env から launch へ値を配るために使う。**知らない綴りは弾く**:
    既定へ落とすと、`USE_MID360_IMU=1` のつもりが false のまま走って、エラーも
    警告も出ないまま構成が変わる。

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


def env_default(name, fallback):
    """環境変数を launch 引数の既定値 (文字列) にする。

    env_bool_default の文字列版。綴りの検査ができないので、**値そのものの妥当性は
    受け手が見る** (overrides なら名前が実在するかを params が見る)。

    Args:
        name: 環境変数名。未設定・空白のみなら fallback を返す。
        fallback: 環境変数が無いときの既定値。

    Returns:
        前後の空白を落とした文字列。
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return fallback
    return raw.strip()


def value(context, name):
    """起動引数を文字列として取り出す (OpaqueFunction の中で使う)。"""
    return LaunchConfiguration(name).perform(context)


def is_true(context, name):
    """起動引数を真偽値として取り出す。"true"/"True" のどちらでも受ける。"""
    return value(context, name).lower() == "true"
