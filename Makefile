# Makefile内のコマンド実行にBashを使用
SHELL := /usr/bin/bash

# `make`実行時にsetupターゲットを既定で実行
.DEFAULT_GOAL := setup

# /opt/ros配下から最新のROS 2ディストリビューションを自動検出
# 例: /opt/ros/humble が存在する場合は humble
ROS_DISTRO ?= $(shell ls -1 /opt/ros 2>/dev/null | sort -V | tail -n 1)

# ROS 2環境のセットアップスクリプト
ROS_SETUP := /opt/ros/$(ROS_DISTRO)/setup.bash

# ビルド対象のROS 2パッケージ
PACKAGES := autonomous_slam autonomous_nav emcl2 nav2_waypoint_manager

# setupという名前のファイルに影響されないための疑似ターゲット指定
.PHONY: setup

setup:
# ROS 2ディストリビューションの検出確認
	@test -n "$(ROS_DISTRO)" || { \
		echo "ROS 2 was not found under /opt/ros."; \
		exit 1; \
	}

# ROS 2セットアップスクリプトの存在確認
	@test -f "$(ROS_SETUP)" || { \
		echo "ROS setup file not found: $(ROS_SETUP)"; \
		exit 1; \
	}

# APTパッケージ一覧の更新
	sudo apt-get update

# ビルドツール、依存関係管理ツール、Nav2関連パッケージの導入
	sudo apt-get install -y \
		python3-colcon-common-extensions \
		python3-rosdep \
		python3-vcstool \
		ros-$(ROS_DISTRO)-nav2-bringup \
		ros-$(ROS_DISTRO)-slam-toolbox \
		ros-$(ROS_DISTRO)-turtlebot3-gazebo \
		ros-$(ROS_DISTRO)-turtlebot3-teleop

# rosdep updateを妨げる古いDebian用設定の削除
	@if [ -f /etc/ros/rosdep/sources.list.d/10-debian.list ] \
		&& grep -Fqx 'yaml file:///usr/share/python3-rosdep2/debian.yaml debian' \
			/etc/ros/rosdep/sources.list.d/10-debian.list; then \
		sudo rm /etc/ros/rosdep/sources.list.d/10-debian.list; \
	fi

# rosdep未初期化時のみ初期化
	@if [ ! -f /etc/ros/rosdep/sources.list.d/20-default.list ]; then \
		sudo rosdep init; \
	fi

# rosdep依存関係データベースの更新
	rosdep update

# autonomous_bot.reposに記載された外部リポジトリの取得
# .repos側にsrc/を含むため、取得先ディレクトリの指定なし
# 既に存在するリポジトリは取得対象外
	vcs import --skip-existing < autonomous_bot.repos

# src配下のROS 2パッケージに必要な依存関係の導入
# ソース内に存在するパッケージは導入対象外
	source "$(ROS_SETUP)" && \
		rosdep install --from-paths src --ignore-src -r -y

# 指定したROS 2パッケージのビルド
# シンボリックリンク形式でのインストール
# CMakeキャッシュを削除した状態での再構成
# emcl2の既知のヘッダ配置警告のみ非表示
	source "$(ROS_SETUP)" && \
		set -o pipefail; \
		colcon build \
			--symlink-install \
			--cmake-clean-cache \
			--packages-select $(PACKAGES) \
			2>&1 | sed \
				-e '/^--- stderr: emcl2$$/d' \
				-e '/headers install destination is set to `include` by ament_auto_package/d' \
				-e '/recommended to install `include\/emcl2` instead/d' \
				-e '/default behavior of ament_auto_package from ROS 2 Kilted Kaiju/d' \
				-e '/USE_SCOPED_HEADER_INSTALL_DIR option/d' \
				-e '/^[[:space:]]*1 package had stderr output: emcl2$$/d'
