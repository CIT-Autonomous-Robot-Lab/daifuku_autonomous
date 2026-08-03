from setuptools import setup

package_name = "raspicat_driver"

setup(
    name=package_name,
    version="0.1.0",
    # 実装は src/ の下。data_files はパッケージの外なので、この付け替えの
    # 影響を受けない (resource/ と package.xml はリポジトリ側の相対パスのまま)。
    package_dir={"": "src"},
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="autonomous_bot maintainer",
    maintainer_email="user@example.com",
    description="Raspberry Pi Cat の本体ドライバ (Pi 4 / Pi 5 のユーザ空間実装)",
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={
        # robot_bringup.launch.py の executable= と同じ名前でなければならない。
        "console_scripts": [
            "raspicat_driver = raspicat_driver.node:main",
        ],
    },
)
