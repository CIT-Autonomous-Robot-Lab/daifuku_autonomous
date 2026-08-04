from setuptools import setup

package_name = "daifuku_rqt"

setup(
    name=package_name,
    version="0.1.0",
    # 実装は src/ の下。plugin.xml の <library path="src"> と対になっている。
    package_dir={"": "src"},
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        # rqt は package.xml の <rqt_gui plugin="${prefix}/plugin.xml"/> を辿るので、
        # この 2 つが share/ に入っていないとプラグイン一覧に出てこない。
        ("share/" + package_name, ["package.xml", "plugin.xml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Keita Sekiguchi / nop",
    maintainer_email="noplab90@gmail.com",
    description="Raspberry Pi Cat の操作パネル (rqt プラグイン)",
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "daifuku_rqt = daifuku_rqt.main:main",
        ],
    },
)
