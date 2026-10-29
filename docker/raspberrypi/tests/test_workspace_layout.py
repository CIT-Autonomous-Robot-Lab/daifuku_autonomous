# Copyright 2026 nop
#
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

"""永続ボリュームでの構成変更と差分ビルドを検査する。"""

import os
from pathlib import Path
import subprocess
from typing import Final

import pytest


SCRIPT: Final = Path(__file__).resolve().parents[2] / 'common/workspace-layout.sh'
ENTRYPOINT: Final = Path(__file__).resolve().parents[2] / 'common/entrypoint.sh'


def layout(workspace: Path, action: str, packages: str = '') -> None:
    _ = subprocess.run(
        ['bash', str(SCRIPT), action], cwd=workspace,
        input=packages, text=True, check=True, timeout=30,
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    for package in ('changing', 'stable'):
        source = tmp_path / 'src' / package
        source.mkdir(parents=True)
        _ = (source / 'node.py').write_text('print(1)\n')
        _ = (source / 'CMakeLists.txt').write_text('project(fixture)\n')
        (tmp_path / 'build' / package).mkdir(parents=True)
        _ = (tmp_path / 'build' / package / 'cached').write_text('compiled')
    (tmp_path / 'build' / '.cargo').mkdir()
    _ = (tmp_path / 'build' / '.cargo' / 'download').write_text('crate')
    (tmp_path / 'install').mkdir()
    _ = (tmp_path / 'install' / 'old').write_text('old install')
    return tmp_path


def package_list(workspace: Path) -> str:
    return ''.join(
        f'{path.name}\t{path.relative_to(workspace)}\t(ros.ament_cmake)\n'
        for path in sorted((workspace / 'src').iterdir()) if path.is_dir()
    )


def baseline(workspace: Path) -> None:
    layout(workspace, 'prepare', package_list(workspace))
    layout(workspace, 'commit')
    for package in ('changing', 'stable'):
        (workspace / 'build' / package).mkdir(exist_ok=True)
        _ = (workspace / 'build' / package / 'cached').write_text('compiled')
    _ = (workspace / 'install' / 'old').write_text('old install')


def test_existing_volumes_are_migrated_without_removing_mounts_or_cargo(workspace: Path) -> None:
    # Given: 記録のない旧ボリューム。
    install = workspace / 'install'
    inode = install.stat().st_ino
    # When: 初めて構成を確認する。
    layout(workspace, 'prepare', package_list(workspace))
    # Then: マウントポイントと取得キャッシュだけは維持する。
    assert install.stat().st_ino == inode
    assert not list(install.iterdir())
    assert not (workspace / 'build' / 'changing').exists()
    assert (workspace / 'build' / '.cargo' / 'download').read_text() == 'crate'


def test_content_edits_and_generated_caches_keep_incremental_outputs(workspace: Path) -> None:
    # Given: 成功済みの構成。ファイル内容だけを編集し、実行時キャッシュを作る。
    baseline(workspace)
    _ = (workspace / 'src' / 'changing' / 'node.py').write_text('print(2)\n')
    cache = workspace / 'src' / 'changing' / '__pycache__'
    cache.mkdir()
    _ = (cache / 'node.pyc').write_bytes(b'cache')
    # When: 次のビルドの準備をする。
    layout(workspace, 'prepare', package_list(workspace))
    # Then: install と両パッケージのビルドキャッシュが残る。
    assert (workspace / 'install' / 'old').exists()
    for package in ('changing', 'stable'):
        assert (workspace / 'build' / package / 'cached').exists()


@pytest.mark.parametrize('change', ['move', 'rename', 'remove', 'file', 'metadata', 'add'])
def test_layout_changes_clean_generated_outputs(workspace: Path, change: str) -> None:
    # Given: 成功済み構成にパッケージ・ファイル構成の変更を加える。
    baseline(workspace)
    source = workspace / 'src' / 'changing'
    if change == 'move':
        container = workspace / 'src' / 'nested'
        container.mkdir()
        _ = source.rename(container / 'changing')
        packages = (
            'changing\tsrc/nested/changing\t(ros.ament_cmake)\n'
            'stable\tsrc/stable\t(ros.ament_cmake)\n'
        )
    else:
        if change == 'rename':
            _ = source.rename(source.with_name('renamed'))
        elif change == 'remove':
            (source / 'node.py').unlink()
            (source / 'CMakeLists.txt').unlink()
            source.rmdir()
        elif change == 'file':
            _ = (source / 'node.py').rename(source / 'renamed.py')
        elif change == 'metadata':
            _ = (source / 'CMakeLists.txt').write_text('install(DIRECTORY config DESTINATION share)\n')
        elif change == 'add':
            (workspace / 'src' / 'added').mkdir()
        packages = package_list(workspace)
    # When: 次のビルドの準備をする。
    layout(workspace, 'prepare', packages)
    # Then: 生成物を作り直し、取得済みクレートは残す。
    assert not (workspace / 'install' / 'old').exists()
    assert not (workspace / 'build' / 'stable' / 'cached').exists()
    assert (workspace / 'build' / '.cargo' / 'download').exists()
    assert not (workspace / 'build' / 'changing' / 'cached').exists()


def test_failed_build_does_not_accept_pending_layout(workspace: Path) -> None:
    # Given: 構成変更後のビルドが失敗し、確定を呼んでいない。
    baseline(workspace)
    _ = (workspace / 'src' / 'changing' / 'node.py').rename(
        workspace / 'src' / 'changing' / 'new.py')
    layout(workspace, 'prepare', package_list(workspace))
    _ = (workspace / 'install' / 'partial').write_text('failed build')
    # When: 同じ構成でもう一度ビルドする。
    layout(workspace, 'prepare', package_list(workspace))
    # Then: 失敗した途中成果物を残さない。
    assert not (workspace / 'install' / 'partial').exists()
    assert not (workspace / 'build' / 'stable' / 'cached').exists()


@pytest.mark.parametrize('merged', [True, False])
def test_real_colcon_recovers_moved_package_and_removes_stale_install(
        tmp_path: Path, merged: bool) -> None:
    # Given: 実際の merged symlink install を作成する。
    source = tmp_path / 'src' / 'sample'
    source.mkdir(parents=True)
    _ = (source / 'package.xml').write_text(
        '<package format="3"><name>sample</name><version>0.0.0</version>' +
        '<description>fixture</description><maintainer email="test@example.com">test</maintainer>' +
        '<license>Apache-2.0</license><buildtool_depend>ament_cmake</buildtool_depend>' +
        '<export><build_type>ament_cmake</build_type></export></package>')
    _ = (source / 'CMakeLists.txt').write_text(
        'cmake_minimum_required(VERSION 3.16)\nproject(sample)\n' +
        'find_package(ament_cmake REQUIRED)\n' +
        'install(DIRECTORY config DESTINATION share/${PROJECT_NAME})\nament_package()\n')
    (source / 'config').mkdir()
    _ = (source / 'config' / 'old.yaml').write_text('value: 1\n')

    def packages() -> str:
        return subprocess.run(
            ['colcon', 'list', '--base-paths', 'src'], cwd=tmp_path,
            check=True, capture_output=True, text=True, timeout=30,
        ).stdout

    command = ['colcon', 'build', *(['--merge-install'] if merged else []), '--symlink-install',
               '--cmake-args', '-DBUILD_TESTING=OFF']
    layout(tmp_path, 'prepare', packages())
    _ = subprocess.run(command, cwd=tmp_path, check=True, timeout=120)
    layout(tmp_path, 'commit')
    _ = source.rename(source.with_name('relocated'))
    source = source.with_name('relocated')
    _ = (source / 'config' / 'old.yaml').rename(source / 'config' / 'new.yaml')
    # 旧キャッシュのままでは、実際に CMake のソースパス不一致で失敗する。
    broken = subprocess.run(command, cwd=tmp_path, capture_output=True, timeout=120)
    assert broken.returncode != 0
    # When: ボリュームを削除せず自動整理してから再ビルドする。
    layout(tmp_path, 'prepare', packages())
    _ = subprocess.run(command, cwd=tmp_path, check=True, timeout=120)
    layout(tmp_path, 'commit')
    # Then: 新しいファイルだけが利用でき、古い名前は残らない。
    prefix = tmp_path / 'install' if merged else tmp_path / 'install' / 'sample'
    installed = prefix / 'share' / 'sample' / 'config'
    assert (installed / 'new.yaml').read_text() == 'value: 1\n'
    assert not (installed / 'old.yaml').exists()
    # 同じボリュームのままパッケージを廃止したときも登録を残さない。
    (source / 'COLCON_IGNORE').touch()
    layout(tmp_path, 'prepare', packages())
    assert not (prefix / 'share' / 'ament_index' /
                'resource_index' / 'packages' / 'sample').exists()


def test_builder_entrypoint_does_not_source_old_workspace(tmp_path: Path) -> None:
    # Given: 古い overlay は source すると失敗する。
    install = tmp_path / 'install'
    install.mkdir()
    _ = (install / 'setup.bash').write_text('exit 91\n')
    executable = tmp_path / 'build-workspace'
    _ = executable.write_text('#!/bin/sh\nexit 0\n')
    executable.chmod(0o755)
    env = dict(os.environ, ROS_WS=str(tmp_path), DAIFUKU_NO_REAP='1',
               PATH=f'{tmp_path}:{os.environ["PATH"]}')
    # When: workspace-build の entrypoint から起動する。
    result = subprocess.run(
        ['bash', str(ENTRYPOINT), 'build-workspace'], env=env, timeout=30)
    # Then: 古い overlay に阻まれずビルドコマンドに到達する。
    assert result.returncode == 0


def test_builder_orders_cmake_rust_and_launch_packages(tmp_path: Path) -> None:
    # Given: plain cargo のライブラリと CMake → ament_cargo → launch の依存。
    library = tmp_path / 'src' / 'vi_lib'
    (library / 'src').mkdir(parents=True)
    _ = (library / 'Cargo.toml').write_text(
        '[package]\nname = "vi_lib"\nversion = "0.0.0"\nedition = "2021"\n')
    _ = (library / 'src' / 'lib.rs').write_text('pub fn value() -> u8 { 1 }\n')
    for name, dependency in (
            ('daifuku_config', ''),
            ('vi_planner', 'daifuku_config'),
            ('daifuku_bringup', 'vi_planner')):
        source = tmp_path / 'src' / name
        source.mkdir(parents=True)
        package_type = 'ament_cargo' if name == 'vi_planner' else 'ament_cmake'
        depends = f'<depend>{dependency}</depend>' if dependency else ''
        _ = (source / 'package.xml').write_text(
            f'<package format="3"><name>{name}</name><version>0.0.0</version>' +
            '<description>fixture</description>' +
            '<maintainer email="test@example.com">test</maintainer><license>Apache-2.0</license>' +
            f'<buildtool_depend>{package_type}</buildtool_depend>{depends}' +
            f'<export><build_type>{package_type}</build_type></export></package>')
        if name == 'vi_planner':
            _ = (source / 'Cargo.toml').write_text(
                '[package]\nname = "vi_planner"\nversion = "0.0.0"\nedition = "2021"\n' +
                '[dependencies]\nvi_lib = { path = "../vi_lib" }\n')
            (source / 'src').mkdir()
            _ = (source / 'src' / 'main.rs').write_text(
                'fn main() { assert_eq!(vi_lib::value(), 1); }\n')
        else:
            _ = (source / 'CMakeLists.txt').write_text(
                f'cmake_minimum_required(VERSION 3.16)\nproject({name})\n' +
                'find_package(ament_cmake REQUIRED)\nament_package()\n')
    builder = SCRIPT.parents[1] / 'raspberrypi' / 'scripts' / 'build-workspace.sh'
    env = dict(os.environ, ROS_WS=str(tmp_path), BUILD_JOBS='2', CARGO_NET_OFFLINE='true')
    # When: 実際のビルド入口を通す。
    result = subprocess.run(
        ['bash', str(builder)], env=env, capture_output=True, text=True, timeout=180)
    # Then: Rust を飛ばして依存パッケージの package.sh が欠落することがない。
    assert result.returncode == 0, result.stdout + result.stderr
    for name in ('daifuku_config', 'vi_planner', 'daifuku_bringup'):
        assert (tmp_path / 'install' / 'share' / name / 'package.sh').is_file()
    assert (tmp_path / 'build' / 'vi_lib' / 'release' / 'libvi_lib.rlib').is_file()
