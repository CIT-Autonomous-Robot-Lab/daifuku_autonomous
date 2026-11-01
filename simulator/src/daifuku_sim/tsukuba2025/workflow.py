# Copyright 2026 Keita Sekiguchi / nop
# SPDX-License-Identifier: Apache-2.0
"""Run the original course with the selected simulator and the current Docker ROS stack."""
from __future__ import annotations

from contextlib import ExitStack
from collections.abc import Callable
from concurrent.futures import Future
import hashlib
import json
import math
import os
from queue import Empty, Queue
import shutil
import subprocess
import sys
from threading import Thread
from typing import Final, Literal

from ..runtime import ContainerRuntime, start_session
from ..vtc.cli import parse_args
from ..vtc.contract import Json, SessionConfig
from ..vtc.runtime import (
    CONTAINER_PREFIX, FASTDDS_TRANSPORTS, REPO, RMW, Stop, URDF_RELATIVE, simulator_command,
)
from ..vtc.workflow import Workflow
from .robot import mount_mid360
from .contract import CourseMetadata, CourseOptions, CourseScore, TransformJson, audit_score


MID360: Final = {
    "link": "livox_frame", "frame": "livox_frame", "topic": "/livox/lidar",
    "horizontal-samples": 720, "vertical-samples": 28, "update-rate": 10.0,
    "min-angle": -math.pi, "max-angle": math.pi,
    "min-vertical-angle": math.radians(-7), "max-vertical-angle": math.radians(52),
    "range-min": 0.1, "range-max": 70.0, "output-type": "PointCloud2",
}


def course_command(
    config: SessionConfig, *, sensor_type: Literal["ray", "gpu_ray"] = "ray",
) -> list[str]:
    """Use native 3D ray returns, leaving the survey scenario's planar defaults intact."""
    command = simulator_command(config)
    command = [argument for argument in command if not argument.startswith("--lidar-")]
    return command + [
        f"--lidar-{name}={value}" for name, value in {**MID360, "sensor-type": sensor_type}.items()
    ]


def run(options: CourseOptions) -> int:
    arguments = [
        "--world", str(options.world), "--engine", options.engine,
        "--run-dir", str(options.run_dir), "--stack-image", options.stack_image,
        "--base-image", options.base_image, "--usim-image", options.usim_image,
        "--ros-domain-id", str(options.ros_domain_id),
    ]
    if options.usim_root is not None:
        arguments += ["--usim-root", str(options.usim_root)]
    if options.headless:
        arguments.append("--headless")
    if options.no_build:
        arguments.append("--no-build")
    workflow = Workflow(parse_args(arguments), ContainerRuntime(options.engine))
    directory = workflow.run_dir
    directory.mkdir(parents=True, exist_ok=True)
    result: dict[str, Json | CourseScore] = {
        "schema": "daifuku-tsukuba2025/1", "status": "FAIL",
        "run_dir": str(directory), "stage": "preflight",
        "end_index": options.end_index, "mid360": dict(MID360),
        "backend": options.backend,
        "lidar_mode": options.lidar_sensor_type if options.backend == "gazebo" else "rtx",
    }
    code = 1
    stage = "preflight"
    gpu_lidar = options.backend == "gazebo" and options.lidar_sensor_type == "gpu_ray"

    def cleanup(operation: Callable[[], int | None], error_key: str) -> None:
        try:
            operation()
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            result[error_key] = str(error)
            if result["status"] in ("PASS", "PARTIAL_PASS"):
                raise

    try:
        with ExitStack() as owned:
            _ = owned.callback(cleanup, workflow.cleanup, "cleanup_error")
            if options.backend == "isaacsim":
                from .isaac import check_environment
                check_environment(options.engine)
            for stage, operation in (
                ("preflight", workflow.preflight), ("runtime", workflow.check_runtime),
                ("images", workflow.images if options.backend == "gazebo"
                 else lambda: workflow.images(simulator_image=False)),
                ("prepare", workflow.prepare),
            ):
                print(f"COURSE_STAGE {stage}", flush=True)
                operation()
            transform = workflow.world.with_name("transform.json")
            transform_data: TransformJson = json.loads(transform.read_text(encoding="utf-8"))
            original_course = REPO / "src" / "daifuku_maps" / "maps" / "tsukuba2025" / "course.json"
            course_bytes = original_course.read_bytes()
            metadata: CourseMetadata = json.loads(course_bytes)
            if transform_data["course_sha256"] != hashlib.sha256(course_bytes).hexdigest():
                raise ValueError("course world was generated for different original course bytes")
            origin = transform_data["source_origin_m"]
            yaw = transform_data["source_yaw_rad"]
            if len(origin) != 3 or not all(math.isfinite(value) for value in [*origin, yaw]):
                raise ValueError("course world transform must contain finite source XYZ and yaw")
            _ = shutil.copyfile(transform, directory / "transform.json")
            mount_mid360(directory / URDF_RELATIVE)
            config: SessionConfig = {
                "engine": options.engine, "network": workflow.runtime.network,
                "image": options.usim_image, "world": str(workflow.world),
                "robot": workflow.robot, "camera_enabled": False, "headless": options.headless,
            }
            environment = {
                "ROS_DOMAIN_ID": str(options.ros_domain_id), "RMW_IMPLEMENTATION": RMW,
                "ROS_LOCALHOST_ONLY": "0",
            }
            course_environment: dict[str, str] = {}
            if options.backend == "isaacsim":
                from .isaac import configuration, transport_profiles

                route_name = f"{CONTAINER_PREFIX}{workflow.run_id}-native-route"
                route_log = directory / "native-route.log"
                workflow.containers.append(route_name)
                route = workflow.runtime.start(
                    route_name, options.stack_image, {}, directory,
                    ["getent", "ahostsv4", "host.docker.internal"], route_log,
                )
                if route.wait(timeout=60) != 0:
                    raise RuntimeError("Docker Desktop host address lookup failed")
                addresses = [
                    line.split()[0] for line in route_log.read_text().splitlines()[1:]
                    if line and line[0].isdigit()
                ]
                if not addresses:
                    raise ValueError("Docker Desktop host address lookup returned no IPv4 address")
                server, client = transport_profiles(directory, addresses[0], options.ros_domain_id)
                worker_configuration = configuration(
                    workflow.world, workflow.robot, hashlib.sha256(course_bytes).hexdigest(),
                    headless=options.headless,
                )
                worker_path = directory / "isaac-configuration.json"
                _ = worker_path.write_text(json.dumps(worker_configuration, indent=2))
                _ = shutil.copyfile(transform, directory / "source-transform.json")
                identity: TransformJson = {
                    "source_origin_m": [0.0, 0.0, 0.0], "source_yaw_rad": 0.0,
                    "course_sha256": hashlib.sha256(course_bytes).hexdigest(),
                }
                _ = (directory / "transform.json").write_text(json.dumps(identity, indent=2))
                environment["FASTRTPS_DEFAULT_PROFILES_FILE"] = str(server)
                course_environment["FASTRTPS_DEFAULT_PROFILES_FILE"] = "/output/" + client.name
                course_environment["FASTDDS_BUILTIN_TRANSPORTS"] = "LARGE_DATA"
                result["isaac_configuration"] = worker_path.name
                result["rtx_horizontal_endpoint"] = "exclusive"
                command = [
                    sys.executable, "-B", "-m", "daifuku_sim.tsukuba2025.isaac", str(worker_path),
                ]
            else:
                environment["FASTDDS_BUILTIN_TRANSPORTS"] = FASTDDS_TRANSPORTS
                command = course_command(config, sensor_type=options.lidar_sensor_type) + [
                    "--startup-timeout", str(options.startup_timeout),
                ]
            windows_docker = sys.platform == "win32" and options.engine == "docker"
            managed_display = (
                options.backend == "gazebo" and windows_docker
                and (not options.headless or gpu_lidar) and not os.environ.get("DISPLAY")
            )
            if managed_display:
                from ..wslg import WslgDisplay
                display = WslgDisplay(options.usim_image, workflow.runtime.network)
                _ = owned.callback(cleanup, display.close, "display_cleanup_error")
                environment["DISPLAY"] = display.display
            if options.backend == "gazebo" and windows_docker and (managed_display or gpu_lidar):
                command.append("--wslg-gpu")
            _ = (directory / "simulator-command.json").write_text(json.dumps(command, indent=2))
            stage = "course"
            print("COURSE_STAGE course", flush=True)
            session = start_session(
                command, directory / "simulator.log", environment,
            )
            workflow.sessions.append(session)
            _ = owned.callback(workflow.sessions.remove, session)
            _ = owned.callback(
                cleanup, lambda: session.stop(timeout=30), "simulator_cleanup_error",
            )
            course_directory = directory / "course"
            course_directory.mkdir()
            name = f"{CONTAINER_PREFIX}{workflow.run_id}-course"
            workflow.containers.append(name)
            handle = workflow.runtime.start(
                name, options.stack_image,
                {**environment, **course_environment, "HOME": "/tmp", "PYTHONUNBUFFERED": "1",
                 "COURSE_BACKEND": options.backend,
                 "COURSE_GOAL_TIMEOUT": str(options.goal_timeout),
                 "COURSE_STARTUP_TIMEOUT": str(options.startup_timeout),
                 "COURSE_END_INDEX": str(options.end_index)},
                directory, ["bash", "/opt/usim_vtc/course_stack.sh"],
                course_directory / "stack.log",
            )
            completions: Queue[Future[int]] = Queue()

            def watch(wait: Callable[[], int]) -> Future[int]:
                completion: Future[int] = Future()

                def await_exit() -> None:
                    try:
                        completion.set_result(wait())
                    except BaseException as error:
                        completion.set_exception(error)
                    completions.put(completion)

                # keep-open 中の実セッションを監視し続け、executor の終了待ちは作らない。
                Thread(target=await_exit, daemon=True).start()
                return completion

            simulator_exit = watch(session.wait)
            course_exit = watch(handle.wait)
            try:
                first = completions.get(timeout=options.course_timeout)
            except Empty:
                raise subprocess.TimeoutExpired(name, options.course_timeout) from None
            if first is simulator_exit:
                result["simulator_exit"] = simulator_exit.result()
                cleanup(lambda: workflow.runtime.remove(name), "course_cleanup_error")
                raise RuntimeError(
                    f"simulator exited {result['simulator_exit']} before course completion"
                )
            exit_code = course_exit.result()
            score: CourseScore = json.loads((course_directory / "result.json").read_text())
            result["course"] = score
            if exit_code != 0 or score["status"] not in ("PASS", "PARTIAL_PASS"):
                raise RuntimeError(score.get("error", f"course container exited {exit_code}"))
            if (course_directory / "course.json").read_bytes() != course_bytes:
                raise ValueError("ROS image used different original course bytes")
            audit_score(
                score, options.end_index, metadata["acceptance"]["maximum_physical_goal_error_m"],
                total_goals=len(metadata["goals"]),
            )
            result["status"] = "PASS" if options.end_index == len(metadata["goals"]) else "PARTIAL_PASS"
            code = 0
            if options.keep_open:
                result.update(stage=stage, exit_code=code)
                _ = (directory / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
                print("COURSE_FINISHED " + json.dumps({
                    "status": result["status"], "run_dir": str(directory), "gui": "kept_open",
                }), flush=True)
                # This is the same physical session at the measured final pose, not a replay.
                result["postrun_simulator_exit"] = simulator_exit.result()
    except Stop as error:
        code = error.code
        result.update(status=error.status, reason=error.reason)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        code = 1
        result["status"] = "FAIL"
        result["reason"] = f"{type(error).__name__}: {error}"
    finally:
        result.update(stage=stage, exit_code=code)
        _ = (directory / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": result["status"], "stage": stage, "exit_code": code, "run_dir": str(directory),
    }), flush=True)
    return code
