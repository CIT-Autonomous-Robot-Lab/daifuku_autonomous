#!/usr/bin/env python3
"""ROS-side input, mapping survey, and navigation acceptance commands (Humble)."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from contextlib import ExitStack
from pathlib import Path

import rclpy
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseWithCovarianceStamped, Twist
from lifecycle_msgs.msg import State
from lifecycle_msgs.srv import GetState
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient
from vtc_contract import parse_waypoints, survey_pose_arrived, survey_targets
from vtc_telemetry import ProbeResult

from vtc_probe import BASE, LIDAR, Probe, ProbeError, parse_pose, pose, wrap
from vtc_recording import NavigationRecorder

# --------------------------------------------------------------------------- inputs


def cmd_inputs(probe: Probe, args: argparse.Namespace) -> ProbeResult:
    probe.spin_until(
        lambda: probe.counts['clock'] > 0 and probe.counts['odom'] > 0 and probe.counts['scan_raw'] > 0,
        args.timeout,
        '/clock, /odom and /scan_raw from usim',
    )
    lidar = probe.wait_lookup(BASE, LIDAR, 60.0)
    probe.spin_for(3.0)  # collect /tf edges for a few periods
    odom = probe.odom
    scan = probe.scan_raw
    if odom is None or scan is None:
        raise ProbeError('simulator input counters have no matching messages')
    parents = sorted({parent for (parent, child) in probe.tf_edges if child == BASE})
    publishers = {
        topic: sorted(f'{i.node_namespace.rstrip("/")}/{i.node_name}' for i in probe.get_publishers_info_by_topic(topic))
        for topic in ('/odom', '/tf', '/scan_raw', '/scan', '/clock')
    }
    nodes = sorted(probe.get_node_names())
    result: ProbeResult = {
        'odom_frame': odom.header.frame_id,
        'odom_child_frame': odom.child_frame_id,
        'scan_raw_frame': scan.header.frame_id,
        'scan_raw_samples': len(scan.ranges),
        'scan_raw_finite': sum(1 for r in scan.ranges if math.isfinite(r)),
        'base_to_lidar': lidar,
        'base_footprint_dynamic_parents': parents,
        'base_footprint_static_parent': probe.static_edges.get(BASE),
        'static_edges': probe.static_edges,
        'publishers': publishers,
        'nodes': nodes,
        'counts': dict(probe.counts),
    }
    problems = []
    if odom.header.frame_id.lstrip('/') != 'odom' or odom.child_frame_id.lstrip('/') != BASE:
        problems.append(f'/odom is {odom.header.frame_id} -> {odom.child_frame_id}, expected odom -> {BASE}')
    if scan.header.frame_id.lstrip('/') != LIDAR:
        problems.append(f'/scan_raw frame is {scan.header.frame_id!r}, expected {LIDAR!r}')
    if parents != ['odom'] or BASE in probe.static_edges:
        problems.append(f'{BASE} must have exactly one parent (odom via Gazebo); saw {parents} + static {probe.static_edges.get(BASE)}')
    if len(publishers['/odom']) != 1:
        problems.append(f'/odom must have exactly one publisher, saw {publishers["/odom"]}')
    if args.require_idle_scan:
        if publishers['/scan']:
            problems.append(f'/scan already has publishers {publishers["/scan"]}: another scan pipeline is alive')
        if any(name.endswith('slam_toolbox') for name in nodes):
            problems.append('slam_toolbox is still alive')
    if problems:
        raise ProbeError('; '.join(problems), result)
    return result


# --------------------------------------------------------------------------- survey


def cmd_survey(probe: Probe, args: argparse.Namespace) -> ProbeResult:
    waypoints = parse_waypoints(args.waypoints)
    probe.spin_until(
        lambda: probe.odom is not None and probe.counts['scan'] > 0 and probe.map is not None,
        args.timeout,
        '/odom, /scan from the daifuku scan pipeline and the first SLAM /map',
    )
    start_alignment = probe.wait_lookup('map', 'odom', 60.0)
    start = probe.truth()
    world_goal = pose(*parse_pose(args.world_goal)) if args.world_goal else None
    targets = survey_targets(start, waypoints, world_goal)
    result: ProbeResult = {
        'waypoints_world': targets,
        'waypoints_completed': 0,
        'waypoint_arrivals': [],
    }
    completed = 0
    probe.set_motor(True)
    began = time.monotonic()
    distance = 0.0
    previous = start
    for target in targets:
        target_x, target_y = target['x'], target['y']
        deadline = time.monotonic() + args.waypoint_timeout
        while True:
            if time.monotonic() > deadline:
                raise ProbeError(f'survey waypoint ({target_x:.2f}, {target_y:.2f}) not reached', result)
            rclpy.spin_once(probe, timeout_sec=0.05)
            now = probe.truth()
            distance += math.hypot(now['x'] - previous['x'], now['y'] - previous['y'])
            previous = now
            dx, dy = target_x - now['x'], target_y - now['y']
            remaining = math.hypot(dx, dy)
            if survey_pose_arrived(now, target):
                break
            heading = wrap(math.atan2(dy, dx) - now['yaw'])
            twist = Twist()
            if remaining < 0.12:
                twist.angular.z = max(-0.6, min(0.6, 1.4 * wrap(target['yaw'] - now['yaw'])))
            elif abs(heading) > 0.25:
                twist.angular.z = max(-0.6, min(0.6, 1.4 * heading))
            else:
                twist.linear.x = min(0.2, 0.5 * remaining)
                twist.angular.z = max(-0.4, min(0.4, 1.0 * heading))
            probe.cmd_vel.publish(twist)
        completed += 1
        result['waypoint_arrivals'].append(now)
        result['waypoints_completed'] = completed
    probe.stop_robot()
    before = probe.counts['map']
    probe.spin_until(lambda: probe.counts['map'] >= before + 2, 30.0, 'two SLAM map updates after the survey')
    end_alignment = probe.wait_lookup('map', 'odom', 10.0)
    slam = probe.wait_lookup('map', BASE, 10.0)
    truth = probe.truth()
    stats = probe.map_stats()
    result.update(
        duration_s=time.monotonic() - began,
        distance_m=distance,
        counts=dict(probe.counts),
        map=stats,
        alignment={'start': start_alignment, 'end': end_alignment},
        slam_vs_truth={
            'slam_map': slam,
            'truth_world': truth,
            'error_m': math.hypot(slam['x'] - truth['x'], slam['y'] - truth['y']),
            'yaw_error_rad': wrap(slam['yaw'] - truth['yaw']),
        },
        tf_edges=sorted(f'{p}->{c}' for (p, c) in probe.tf_edges),
    )
    if stats['known_cells'] < args.min_known_cells:
        raise ProbeError(
            f'SLAM map has only {stats["known_cells"]} known cells (< {args.min_known_cells}); '
            'the selected usim lidar did not observe enough of the VTC world',
            result,
        )
    return result


# --------------------------------------------------------------------------- navigate


def cmd_navigate(probe: Probe, args: argparse.Namespace, recorder: NavigationRecorder) -> ProbeResult:
    seed = parse_pose(args.seed)
    goal = parse_pose(args.goal)
    probe.spin_until(
        lambda: probe.odom is not None and probe.map is not None and probe.counts['scan'] > 0,
        args.timeout,
        '/odom, the saved /map and /scan',
    )
    result: ProbeResult = {'seed_map': pose(*seed), 'goal_map': pose(*goal), 'truth_start': probe.truth()}
    # emcl2's initialpose callback assumes MCL already exists. /map can arrive
    # before its separate /map_loc and /scan initialization completes.
    probe.spin_until(
        lambda: recorder.mcl_pose_count > 0,
        args.timeout,
        'the first emcl2 /mcl_pose before publishing /initialpose',
    )

    message = PoseWithCovarianceStamped()
    message.header.frame_id = 'map'
    message.pose.pose.position.x, message.pose.pose.position.y = seed[0], seed[1]
    message.pose.pose.orientation.z, message.pose.pose.orientation.w = math.sin(seed[2] / 2), math.cos(seed[2] / 2)
    message.pose.covariance[0] = message.pose.covariance[7] = 0.02
    message.pose.covariance[35] = 0.01
    deadline = time.monotonic() + 60.0
    estimate = None
    while True:
        message.header.stamp = probe.get_clock().now().to_msg()
        probe.initial_pose.publish(message)
        probe.spin_for(1.0)
        estimate = probe.lookup('map', BASE)
        if estimate and math.hypot(estimate['x'] - seed[0], estimate['y'] - seed[1]) < 0.3 \
                and abs(wrap(estimate['yaw'] - seed[2])) < 0.2:
            break
        if time.monotonic() > deadline:
            raise ProbeError(f'emcl2 did not converge on the origin seed (estimate {estimate})', result)
    result['estimate_start'] = estimate
    nodes = sorted(probe.get_node_names())
    owners = sorted(info.node_name for info in probe.get_publishers_info_by_topic('/mcl_pose'))
    result.update(nodes=nodes, publishers={'/mcl_pose': owners})
    if not {'vi_planner', 'emcl2'}.issubset(nodes) or owners != ['emcl2'] or 'amcl' in nodes:
        raise ProbeError(f'expected VI + emcl2, got nodes {nodes} and /mcl_pose owners {owners}', result)

    server_state = None
    if args.wait_nav2:
        state_client = probe.create_client(GetState, '/bt_navigator/get_state')
        probe.spin_until(
            state_client.service_is_ready,
            args.timeout,
            '/bt_navigator/get_state service',
        )
        deadline = time.monotonic() + args.timeout
        response = None
        while time.monotonic() < deadline:
            future = state_client.call_async(GetState.Request())
            probe.spin_until(future.done, min(5.0, deadline - time.monotonic()),
                             '/bt_navigator lifecycle state response')
            response = future.result()
            if response is not None and response.current_state.id == State.PRIMARY_STATE_ACTIVE:
                server_state = response.current_state.label
                break
            rclpy.spin_once(probe, timeout_sec=0.1)
        if server_state is None:
            state = '' if response is None else response.current_state.label
            raise ProbeError(f'/bt_navigator did not become active (state {state!r})', result)
    if server_state is not None:
        result['navigation_server_state'] = server_state

    action = ActionClient(probe, NavigateToPose, '/navigate_to_pose')
    if not action.wait_for_server(timeout_sec=args.timeout):
        raise ProbeError('/navigate_to_pose action server did not appear', result)

    probe.set_motor(True)
    request = NavigateToPose.Goal()
    request.pose.header.frame_id = 'map'
    request.pose.pose.position.x, request.pose.pose.position.y = goal[0], goal[1]
    request.pose.pose.orientation.z, request.pose.pose.orientation.w = math.sin(goal[2] / 2), math.cos(goal[2] / 2)
    feedback = [0]

    def on_feedback(_message) -> None:
        feedback[0] += 1

    began = time.monotonic()
    recorder.state = 'navigating'
    sent = action.send_goal_async(request, feedback_callback=on_feedback)
    probe.spin_until(sent.done, 30.0, 'goal acceptance')
    handle = sent.result()
    result['accepted'] = bool(handle and handle.accepted)
    if handle is None or not handle.accepted:
        raise ProbeError('vi_planner rejected the NavigateToPose goal', result)
    done = handle.get_result_async()
    try:
        probe.spin_until(done.done, args.goal_timeout, 'NavigateToPose result')
        response = done.result()
        if response is None:
            raise ProbeError('NavigateToPose completed without a result', result)
        status = response.status
    except ProbeError:
        cancel = handle.cancel_goal_async()
        probe.spin_until(cancel.done, 15.0, 'goal cancel')
        status = GoalStatus.STATUS_UNKNOWN
        result['timed_out'] = True
    result['elapsed_s'] = time.monotonic() - began
    probe.stop_robot()
    probe.spin_for(2.0)
    final_estimate = probe.lookup('map', BASE)
    truth = probe.truth()
    result.update(
        action_status=int(status),
        feedback_count=feedback[0],
        truth_final=truth,
        estimate_final=final_estimate,
        counts=dict(probe.counts),
    )
    recorder.finish(int(status))
    return result


# --------------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    inputs = sub.add_parser('inputs')
    inputs.add_argument('--require-idle-scan', action='store_true')
    survey = sub.add_parser('survey')
    survey.add_argument('--waypoints', required=True)
    survey.add_argument('--world-goal')
    survey.add_argument('--waypoint-timeout', type=float, default=180.0)
    survey.add_argument('--min-known-cells', type=int, default=500)
    navigate = sub.add_parser('navigate')
    navigate.add_argument('--seed', required=True)
    navigate.add_argument('--goal', required=True)
    navigate.add_argument('--wait-nav2', action='store_true')
    navigate.add_argument('--goal-timeout', type=float, default=300.0)
    for command in (inputs, survey, navigate):
        command.add_argument('--out', type=Path, required=True)
        command.add_argument('--timeout', type=float, default=180.0)
    args = parser.parse_args()

    rclpy.init()
    probe = Probe()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    try:
        with ExitStack() as resources:
            if args.command == 'navigate':
                stream = resources.enter_context(args.out.with_name('recording.jsonl').open('w', encoding='utf-8'))
                recorder = NavigationRecorder(probe, stream, pose(*parse_pose(args.goal)), args.out.parent)
                resources.callback(recorder.timer.cancel)
                result = cmd_navigate(probe, args, recorder)
            else:
                handler = {'inputs': cmd_inputs, 'survey': cmd_survey}[args.command]
                result = handler(probe, args)
        result['ok'] = True
        code = 0
    except ProbeError as error:
        result = error.result
        result.update(ok=False, error=str(error))
        code = 1
    finally:
        probe.stop_robot()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({'command': args.command, 'ok': code == 0, 'error': result.get('error')}), flush=True)
    probe.destroy_node()
    rclpy.shutdown()
    return code


if __name__ == '__main__':
    sys.exit(main())
