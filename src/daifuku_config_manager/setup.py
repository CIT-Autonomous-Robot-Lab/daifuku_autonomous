from setuptools import setup

package_name = "daifuku_config_manager"

setup(
    name=package_name,
    version="0.1.0",
    # 実装は src/ の下 (raspicat_driver と同じ配置)。data_files はパッケージの外なので、
    # この付け替えの影響を受けない。
    package_dir={"": "src"},
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        # **設定の実体はここには無い。** リポジトリルートの config/
        # (daifuku_config パッケージ) に置いてある。こちらはその合成規則
        # (params.py) とノード 2 つだけを持つ。
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Keita Sekiguchi / nop",
    maintainer_email="noplab90@gmail.com",
    description="設定ファイルの合成規則と、場所ごとの調整 (overrides) を持つ共有パッケージ",
    license="Apache-2.0",
    tests_require=["pytest"],
    # **ここを足したときはビルドが要る** (entry_points はビルド時にしか展開されない。
    # --symlink-install でも同じ)。
    #
    #   site_manager    今どこかを ROS から読み書きできるようにする。機体側
    #                   (robot_bringup.launch.py) が 1 つだけ立てる
    #   config_sentinel 起動時に読んだ設定が書き変わっていないか見張る。
    #                   top-level の launch がそれぞれ 1 つ立てる
    entry_points={
        "console_scripts": [
            "site_manager = daifuku_config_manager.site_manager:main",
            "config_sentinel = daifuku_config_manager.config_sentinel:main",
        ],
    },
)
