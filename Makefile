# Makefile内のコマンド実行にBashを使用
SHELL := /usr/bin/bash
.SHELLFLAGS := -o pipefail -c

# /opt/ros配下から最新のROS 2ディストリビューションを自動検出
# 例: /opt/ros/humble が存在する場合は humble
ROS_DISTRO ?= $(shell ls -1 /opt/ros 2>/dev/null | sort -V | tail -n 1)
ROS_SETUP := /opt/ros/$(ROS_DISTRO)/setup.bash
WORKSPACE_SETUP := install/setup.bash

# ビルド対象のROS 2パッケージ
PACKAGES := autonomous_slam autonomous_nav emcl2 nav2_waypoint_manager

# make devで使用する起動パラメータ
MAP ?= $(CURDIR)/src/autonomous_nav/maps/map_tsudanuma.yaml
USE_SIM_TIME ?= false
LOCALIZATION ?= emcl2
USE_RVIZ ?= true
TURTLEBOT3_MODEL ?= burger

.DEFAULT_GOAL := help

.PHONY: help check-ros setup deps build rebuild dev sim slam teleop test clean

help:
	@echo "Usage: make <target> [VARIABLE=value]"
	@echo
	@echo "Targets:"
	@echo "  setup    Install system and ROS dependencies, then build the workspace"
	@echo "  deps     Install ROS package dependencies with rosdep"
	@echo "  build    Build workspace packages with symlink install"
	@echo "  rebuild  Reconfigure and build workspace packages from a clean CMake cache"
	@echo "  dev      Build and launch Nav2"
	@echo "  sim      Launch the TurtleBot3 Gazebo world"
	@echo "  slam     Launch SLAM Toolbox and RViz"
	@echo "  teleop   Launch TurtleBot3 keyboard teleoperation"
	@echo "  test     Run package tests"
	@echo "  clean    Remove build, install, and log directories"
	@echo
	@echo "Variables for dev/slam: MAP, USE_SIM_TIME, LOCALIZATION, USE_RVIZ"
	@echo "Example: make dev USE_SIM_TIME=true MAP=$(CURDIR)/src/autonomous_nav/maps/turtlebot3.yaml"

check-ros:
	@test -n "$(ROS_DISTRO)" || { echo "ROS 2 was not found under /opt/ros."; exit 1; }
	@test -f "$(ROS_SETUP)" || { echo "ROS setup file not found: $(ROS_SETUP)"; exit 1; }

setup: check-ros
	sudo apt-get update
	sudo apt-get install -y \
		python3-colcon-common-extensions \
		python3-rosdep \
		python3-vcstool \
		ros-$(ROS_DISTRO)-nav2-bringup \
		ros-$(ROS_DISTRO)-slam-toolbox \
		ros-$(ROS_DISTRO)-turtlebot3-gazebo \
		ros-$(ROS_DISTRO)-turtlebot3-teleop
	@if [ -f /etc/ros/rosdep/sources.list.d/10-debian.list ] \
		&& grep -Fqx 'yaml file:///usr/share/python3-rosdep2/debian.yaml debian' /etc/ros/rosdep/sources.list.d/10-debian.list; then \
		sudo rm /etc/ros/rosdep/sources.list.d/10-debian.list; \
	fi
	@if [ ! -f /etc/ros/rosdep/sources.list.d/20-default.list ]; then sudo rosdep init; fi
	rosdep update
	vcs import --skip-existing < autonomous_bot.repos
	@$(MAKE) deps build

deps: check-ros
	source "$(ROS_SETUP)" && rosdep install --from-paths src --ignore-src -r -y

build: check-ros
	source "$(ROS_SETUP)" && colcon build --symlink-install --packages-select $(PACKAGES)

rebuild: check-ros
	source "$(ROS_SETUP)" && colcon build --symlink-install --cmake-clean-cache --packages-select $(PACKAGES)

dev: build
	source "$(ROS_SETUP)" && source "$(WORKSPACE_SETUP)" && \
		ros2 launch autonomous_nav navigation.launch.py \
		map:="$(MAP)" use_sim_time:=$(USE_SIM_TIME) localization:=$(LOCALIZATION) use_rviz:=$(USE_RVIZ)

dev-sim: build
	source "$(ROS_SETUP)" && source "$(WORKSPACE_SETUP)" && \
		ros2 launch autonomous_nav navigation.launch.py \
		map:="$(CURDIR)/src/autonomous_nav/maps/map_turtlebot3.yaml" use_sim_time:=true localization:=$(LOCALIZATION) use_rviz:=$(USE_RVIZ)

sim: check-ros
	source "$(ROS_SETUP)" && source /usr/share/gazebo/setup.sh && \
		TURTLEBOT3_MODEL=$(TURTLEBOT3_MODEL) GAZEBO_MODEL_DATABASE_URI="" \
		ros2 launch turtlebot3_gazebo turtlebot3_world.launch.py

slam: build
	source "$(ROS_SETUP)" && source "$(WORKSPACE_SETUP)" && \
		ros2 launch autonomous_slam mapping.launch.py use_sim_time:=$(USE_SIM_TIME) use_rviz:=$(USE_RVIZ)

teleop: check-ros
	source "$(ROS_SETUP)" && TURTLEBOT3_MODEL=$(TURTLEBOT3_MODEL) \
		ros2 run teleop_twist_keyboard teleop_twist_keyboard

test: build
	source "$(ROS_SETUP)" && source "$(WORKSPACE_SETUP)" && colcon test --packages-select $(PACKAGES)
	source "$(ROS_SETUP)" && source "$(WORKSPACE_SETUP)" && colcon test-result --verbose

clean:
	rm -rf build install log
