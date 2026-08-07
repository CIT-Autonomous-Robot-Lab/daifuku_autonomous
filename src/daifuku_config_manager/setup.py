import glob

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
        # 今どこで走らせるか。**tools/site.sh が書き換えるので glob にしない** —
        # 名前を決め打ちにしておけば、ビルド時の glob の結果に依らず必ず入る。
        ("share/" + package_name + "/config", ["config/site"]),
        # overrides はここから両パッケージへ配る。--symlink-install では symlink に
        # なるので、値を直しても再ビルドは要らない (launch / config と同じ扱い)。
        # **ファイルを新しく足したときだけは 1 度ビルドを通すこと** — glob は
        # ビルド時にしか展開されない。
        (
            "share/" + package_name + "/config/overrides",
            glob.glob("config/overrides/*.yaml"),
        ),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Keita Sekiguchi / nop",
    maintainer_email="noplab90@gmail.com",
    description="設定ファイルの合成規則と、場所ごとの調整 (overrides) を持つ共有パッケージ",
    license="Apache-2.0",
    tests_require=["pytest"],
    # 立てるノードは無い。launch から import されるだけ。
)
