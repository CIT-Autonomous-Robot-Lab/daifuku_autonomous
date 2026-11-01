# Copyright 2026 Keita Sekiguchi / nop
# SPDX-License-Identifier: Apache-2.0
"""Course lifecycle failures retain the cause when Docker is unavailable."""
import hashlib
import json
from concurrent.futures import Future
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from daifuku_sim.tsukuba2025 import workflow
from daifuku_sim import wslg


def test_sparse_course_defaults_cover_all_configured_targets() -> None:
    # Given: the authoritative sparse course and the public parser.
    from argparse import ArgumentParser
    from daifuku_sim.tsukuba2025.cli import configure_parser

    course = json.loads(
        (workflow.REPO / "src/daifuku_maps/maps/tsukuba2025/course.json").read_bytes()
    )
    parser = ArgumentParser()
    configure_parser(parser)
    # When: the caller does not override target count or the long-leg deadline.
    options = parser.parse_args([])
    # Then: defaults cover the entire configured route, including its longer legs.
    assert options.end_index == len(course["goals"]) <= 30
    assert options.goal_timeout == 1800


def test_display_and_container_cleanup_cannot_replace_course_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the display was opened, then Docker disappears during course startup.
    def fail_start(*args: object, **kwargs: object) -> None:
        raise RuntimeError("course startup lost Docker")

    def fail_cleanup() -> None:
        raise RuntimeError("container cleanup lost Docker")

    def fail_display_cleanup() -> None:
        raise RuntimeError("display cleanup lost Docker")

    course = workflow.REPO / "src/daifuku_maps/maps/tsukuba2025/course.json"
    world = tmp_path / "world.sdf"
    world.with_name("transform.json").write_text(json.dumps({
        "course_sha256": hashlib.sha256(course.read_bytes()).hexdigest(),
        "source_origin_m": [298.2, -146.6, -0.7265624731779599],
        "source_yaw_rad": 3.141592653589793,
    }))
    directory = tmp_path / "run"
    runtime = SimpleNamespace(network="test", start=fail_start)
    fake_workflow = SimpleNamespace(
        run_dir=directory, world=world, run_id="test",
        robot={
            "urdf": str(tmp_path / "robot.urdf"), "base_link": "base_footprint",
            "left_joint": "left_wheel_joint", "right_joint": "right_wheel_joint",
            "wheel_radius": 0.05, "wheel_separation": 0.2,
        },
        runtime=runtime, sessions=[], containers=[], cleanup=fail_cleanup,
        preflight=lambda: None, check_runtime=lambda: None,
        images=lambda: None, prepare=lambda: None,
    )
    monkeypatch.setattr(workflow, "Workflow", lambda *_: fake_workflow)
    monkeypatch.setattr(workflow, "mount_mid360", lambda *_: None)
    monkeypatch.setattr(workflow, "start_session", lambda *_: ControlledProcess())
    monkeypatch.setattr(wslg, "WslgDisplay", lambda *_: SimpleNamespace(
        display="test:0", close=fail_display_cleanup,
    ))
    monkeypatch.setattr(workflow.sys, "platform", "win32")
    monkeypatch.delenv("DISPLAY", raising=False)
    options = SimpleNamespace(
        world=world, backend="gazebo", lidar_sensor_type="ray", engine="docker", run_dir=directory, stack_image="stack",
        base_image="base", usim_image="usim", ros_domain_id=87,
        usim_root=None, headless=False, no_build=True, end_index=30,
        startup_timeout=900, goal_timeout=1800, course_timeout=172800,
        keep_open=True,
    )
    # When: both owned cleanup operations also fail during exception unwinding.
    code = workflow.run(options)
    result = json.loads((directory / "result.json").read_text())
    # Then: all failures remain visible, with the course error as the primary cause.
    assert code == 1
    assert result["status"] == "FAIL"
    assert result["reason"] == "RuntimeError: course startup lost Docker"
    assert result["cleanup_error"] == "container cleanup lost Docker"
    assert result["display_cleanup_error"] == "display cleanup lost Docker"


class ControlledProcess:
    def __init__(self) -> None:
        self.exit: Future[int] = Future()
        self.waiting = Event()
        self.stops = 0

    def wait(self, timeout: float | None = None) -> int:
        assert timeout is None
        self.waiting.set()
        return self.exit.result(timeout=10)

    def finish(self, code: int) -> None:
        if not self.exit.done():
            self.exit.set_result(code)

    def stop(self, timeout: float) -> int:
        self.stops += 1
        self.finish(0)
        return self.exit.result()


@pytest.fixture
def lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    course = workflow.REPO / "src/daifuku_maps/maps/tsukuba2025/course.json"
    world = tmp_path / "world.sdf"
    world.with_name("transform.json").write_text(json.dumps({
        "course_sha256": hashlib.sha256(course.read_bytes()).hexdigest(),
        "source_origin_m": [0, 0, 0], "source_yaw_rad": 0,
    }))
    directory = tmp_path / "run"
    simulator, course_process = ControlledProcess(), ControlledProcess()
    removed: list[str] = []

    def remove(name: str) -> None:
        removed.append(name)
        course_process.finish(137)

    def start(*args: object) -> ControlledProcess:
        course_directory = directory / "course"
        (course_directory / "course.json").write_bytes(course.read_bytes())
        (course_directory / "result.json").write_text(json.dumps({
            "status": "PARTIAL_PASS",
            "total_goals": len(json.loads(course.read_bytes())["goals"]), "end_index": 1,
            "completed": [{"index": 0, "physical_error_m": 0.1}],
            "live_truth_localization": False, "physical_goal_error_limit_m": 0.6,
        }))
        return course_process

    def cleanup() -> None:
        for session in fake_workflow.sessions:
            session.stop(timeout=10)
        for name in fake_workflow.containers:
            fake_workflow.runtime.remove(name)

    fake_workflow = SimpleNamespace(
        run_dir=directory, world=world, run_id="owned",
        robot={
            "urdf": str(tmp_path / "robot.urdf"), "base_link": "base_footprint",
            "left_joint": "left_wheel_joint", "right_joint": "right_wheel_joint",
            "wheel_radius": 0.05, "wheel_separation": 0.2,
        },
        runtime=SimpleNamespace(network="test", start=start, remove=remove),
        sessions=[], containers=[], cleanup=cleanup,
        preflight=lambda: None, check_runtime=lambda: None,
        images=lambda: None, prepare=lambda: None,
    )
    monkeypatch.setattr(workflow, "Workflow", lambda *_: fake_workflow)
    monkeypatch.setattr(workflow, "mount_mid360", lambda *_: None)
    monkeypatch.setattr(workflow, "start_session", lambda *_: simulator)
    options = SimpleNamespace(
        world=world, backend="gazebo", lidar_sensor_type="ray", engine="docker", run_dir=directory, stack_image="stack",
        base_image="base", usim_image="usim", ros_domain_id=87,
        usim_root=None, headless=True, no_build=True, end_index=1,
        startup_timeout=900, goal_timeout=1800, course_timeout=172800,
        keep_open=False,
    )
    return SimpleNamespace(
        workflow=fake_workflow, options=options, simulator=simulator,
        course=course_process, removed=removed, directory=directory,
    )


@pytest.mark.parametrize("simulator_code", [0, 139])
def test_simulator_exit_stops_only_owned_course_and_retains_primary_failure(
    lifecycle: SimpleNamespace, simulator_code: int,
) -> None:
    lifecycle.simulator.finish(simulator_code)
    remove = lifecycle.workflow.runtime.remove

    def fail_after_remove(name: str) -> None:
        remove(name)
        raise RuntimeError("Docker cleanup failed")

    lifecycle.workflow.runtime.remove = fail_after_remove
    assert workflow.run(lifecycle.options) == 1
    result = json.loads((lifecycle.directory / "result.json").read_text())
    assert result["simulator_exit"] == simulator_code
    assert result["reason"] == (
        f"RuntimeError: simulator exited {simulator_code} before course completion"
    )
    assert result["course_cleanup_error"] == "Docker cleanup failed"
    assert result["cleanup_error"] == "Docker cleanup failed"
    assert lifecycle.removed
    assert set(lifecycle.removed) == {"daifuku-usim-vtc-owned-course"}
    assert lifecycle.course.exit.result(timeout=5) == 137
    assert lifecycle.simulator.stops == 1


def test_course_first_success_reaches_audit_and_normal_cleanup(lifecycle: SimpleNamespace) -> None:
    lifecycle.course.finish(0)
    assert workflow.run(lifecycle.options) == 0
    result = json.loads((lifecycle.directory / "result.json").read_text())
    assert result["status"] == "PARTIAL_PASS"
    assert result["course"]["completed"] == [{"index": 0, "physical_error_m": 0.1}]
    assert lifecycle.simulator.stops == 1


def test_native_backend_preserves_course_audit_and_uses_distinct_transport_profiles(
    lifecycle: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from daifuku_sim.tsukuba2025 import isaac

    lifecycle.options.backend = "isaacsim"
    source_transform = lifecycle.workflow.world.with_name("transform.json")
    original = json.loads(source_transform.read_text())
    original.update(source_origin_m=[298.2, -146.6, -0.72], source_yaw_rad=3.141592653589793)
    source_transform.write_text(json.dumps(original))
    image_options = []
    lifecycle.workflow.images = lambda **kwargs: image_options.append(kwargs)
    monkeypatch.setattr(isaac, "check_environment", lambda engine: None)
    monkeypatch.setattr(isaac, "configuration", lambda *args, **kwargs: {
        "configuration": {"world": "original.usda"}, "backend": {"lidar_mode": "rtx"},
    })
    start_course = lifecycle.workflow.runtime.start

    def start_container(name, image, environment, mount, command, log):
        if command == ["getent", "ahostsv4", "host.docker.internal"]:
            log.write_text("$ route\n192.168.65.254 STREAM host.docker.internal\n")
            return SimpleNamespace(wait=lambda timeout: 0)
        assert environment["COURSE_BACKEND"] == "isaacsim"
        assert environment["FASTRTPS_DEFAULT_PROFILES_FILE"] == "/output/isaac-client.xml"
        assert environment["FASTDDS_BUILTIN_TRANSPORTS"] == "LARGE_DATA"
        return start_course(name, image, environment, mount, command, log)

    invocation = []

    def start_native(command, log, environment):
        invocation.extend(command)
        assert environment["FASTRTPS_DEFAULT_PROFILES_FILE"].endswith("isaac-server.xml")
        assert "DISPLAY" not in environment
        return lifecycle.simulator

    lifecycle.workflow.runtime.start = start_container
    monkeypatch.setattr(workflow, "start_session", start_native)
    lifecycle.course.finish(0)
    assert workflow.run(lifecycle.options) == 0
    assert image_options == [{"simulator_image": False}]
    identity = json.loads((lifecycle.directory / "transform.json").read_text())
    assert identity["source_origin_m"] == [0, 0, 0]
    assert identity["source_yaw_rad"] == 0
    assert json.loads((lifecycle.directory / "source-transform.json").read_text()) == original
    assert json.loads((lifecycle.directory / "simulator-command.json").read_text()) == invocation
    assert invocation[3] == "daifuku_sim.tsukuba2025.isaac"
    result = json.loads((lifecycle.directory / "result.json").read_text())
    assert result["backend"] == "isaacsim"
    assert result["status"] == "PARTIAL_PASS"
    assert result["course"]["completed"] == [{"index": 0, "physical_error_m": 0.1}]


@pytest.mark.parametrize("headless", [False, True])
def test_managed_wslg_command_manifest_matches_actual_gpu_invocation(
    lifecycle: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, headless: bool,
) -> None:
    lifecycle.options.headless = headless
    lifecycle.options.lidar_sensor_type = "gpu_ray" if headless else "ray"
    invocation: list[str] = []
    display_closed = [False]
    cleanup_owned = lifecycle.workflow.cleanup

    def close_display():
        assert lifecycle.simulator.stops == 1
        display_closed[0] = True

    def cleanup_network():
        assert display_closed[0]
        cleanup_owned()

    lifecycle.workflow.cleanup = cleanup_network

    def start(command, log, environment):
        invocation.extend(command)
        assert environment["DISPLAY"] == "test:0"
        return lifecycle.simulator

    monkeypatch.setattr(workflow.sys, "platform", "win32")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(workflow, "start_session", start)
    monkeypatch.setattr(wslg, "WslgDisplay", lambda *_: SimpleNamespace(
        display="test:0", close=close_display,
    ))
    lifecycle.course.finish(0)
    try:
        assert workflow.run(lifecycle.options) == 0
    finally:
        lifecycle.simulator.finish(0)
    recorded = json.loads((lifecycle.directory / "simulator-command.json").read_text())
    assert recorded == invocation
    assert "--wslg-gpu" in recorded


def test_keep_open_retains_same_live_simulator_after_course_completion(
    lifecycle: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle.options.keep_open = True
    course_finished = Event()
    outcome: Future[int] = Future()

    def printed(message: str, **kwargs: object) -> None:
        if message.startswith("COURSE_FINISHED "):
            course_finished.set()

    def run() -> None:
        try:
            outcome.set_result(workflow.run(lifecycle.options))
        except BaseException as error:
            outcome.set_exception(error)

    monkeypatch.setattr(workflow, "print", printed, raising=False)
    lifecycle.course.finish(0)
    runner = Thread(target=run, daemon=True)
    runner.start()
    try:
        assert course_finished.wait(timeout=5)
        assert lifecycle.simulator.waiting.is_set()
        assert lifecycle.workflow.sessions == [lifecycle.simulator]
        assert lifecycle.simulator.stops == 0
        assert not lifecycle.simulator.exit.done()
        assert not outcome.done()
        result = json.loads((lifecycle.directory / "result.json").read_text())
        assert result["status"] == "PARTIAL_PASS"
    finally:
        lifecycle.simulator.finish(0)
        runner.join(timeout=5)
    assert not runner.is_alive()
    assert outcome.result(timeout=5) == 0
    result = json.loads((lifecycle.directory / "result.json").read_text())
    assert result["postrun_simulator_exit"] == 0
    assert lifecycle.simulator.stops == 1
    assert lifecycle.workflow.sessions == []
