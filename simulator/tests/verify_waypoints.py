#!/usr/bin/env python3
"""ウェイポイント YAML の書式を、実機 (joy_teleop) と同じパーサで検算する。

パネル (waypoint_manager_panel.cpp の readYamlFile) と同じ契約:

  * frame_id は必須
  * position.z は省略可 (0.0)
  * 有限でない値と長さ 0 のクォータニオンは弾く

片方だけが通す形にすると、手で書いた順路が「実機では走るのにパネルでは開けない」
(あるいはその逆) になる。同梱の順路は全部通ること、壊した入力は落ちることが要点。

    cd simulator
    uv run python tests/verify_waypoints.py

終了コード: 0 = 全部 OK、1 = どれかが契約と食い違う。
"""

import math
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "daifuku_bringup" / "src"))

from joy_waypoints import load_waypoint_document  # noqa: E402

WAYPOINTS_DIR = ROOT / "src" / "daifuku_stack" / "waypoints"


def _write(body):
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".yaml", delete=False, encoding="utf-8",
    )
    handle.write(body)
    handle.close()
    return Path(handle.name)


def _must_fail(body, needle):
    path = _write(body)
    try:
        load_waypoint_document(path)
    except (ValueError, OSError) as exc:
        text = str(exc)
        if needle not in text:
            print(f"FAIL reject: 期待する文句 {needle!r} が無い: {text}")
            return False
        return True
    finally:
        path.unlink(missing_ok=True)
    print(f"FAIL reject: 通ってしまった (文句 {needle!r} を期待)")
    return False


def check_bundled():
    files = sorted(WAYPOINTS_DIR.glob("waypoints_*.yaml"))
    if not files:
        print(f"FAIL: {WAYPOINTS_DIR} に waypoints_*.yaml が無い")
        return False
    ok = True
    for path in files:
        try:
            frame_id, poses = load_waypoint_document(path)
        except (OSError, ValueError) as exc:
            print(f"FAIL {path.name}: {exc}")
            ok = False
            continue
        if frame_id != "map":
            print(f"FAIL {path.name}: frame_id={frame_id!r} (map ではない)")
            ok = False
            continue
        print(f"OK   {path.name}: {len(poses)} points")
    return ok


def check_contract():
    ok = True
    ok &= _must_fail("waypoints: []\n", "frame_id")
    ok &= _must_fail("frame_id: map\nwaypoints: []\n", "no waypoints")
    ok &= _must_fail(
        "frame_id: map\nwaypoints:\n- orientation: {x: 0, y: 0, z: 0, w: 1}\n",
        "waypoint 0",
    )
    # z は省略可。クォータニオンは単位でなくてよいが、長さ 0 は不可。
    path = _write(
        "frame_id: map\n"
        "waypoints:\n"
        "- position: {x: 1.0, y: 2.0}\n"
        "  orientation: {x: 0.0, y: 0.0, z: 0.0, w: 1.0}\n"
    )
    try:
        frame_id, poses = load_waypoint_document(path)
        xyz, quat = poses[0]
        if frame_id != "map" or xyz != (1.0, 2.0, 0.0) or quat[3] != 1.0:
            print(f"FAIL optional z: got {xyz} {quat}")
            ok = False
        else:
            print("OK   optional z defaults to 0.0")
    except (OSError, ValueError) as exc:
        print(f"FAIL optional z: {exc}")
        ok = False
    finally:
        path.unlink(missing_ok=True)

    ok &= _must_fail(
        "frame_id: map\n"
        "waypoints:\n"
        "- position: {x: .nan, y: 0.0, z: 0.0}\n"
        "  orientation: {x: 0.0, y: 0.0, z: 0.0, w: 1.0}\n",
        "not finite",
    )
    ok &= _must_fail(
        "frame_id: map\n"
        "waypoints:\n"
        "- position: {x: 0.0, y: 0.0, z: 0.0}\n"
        "  orientation: {x: 0.0, y: 0.0, z: 0.0, w: 0.0}\n",
        "quaternion",
    )
    # 有限な非単位クォータニオンは通る (正規化は読み手の仕事ではない)。
    path = _write(
        "frame_id: map\n"
        "waypoints:\n"
        "- position: {x: 0.0, y: 0.0, z: 0.0}\n"
        "  orientation: {x: 0.0, y: 0.0, z: 0.0, w: 2.0}\n"
    )
    try:
        _, poses = load_waypoint_document(path)
        if not math.isclose(poses[0][1][3], 2.0):
            print("FAIL unnormalized quaternion rejected")
            ok = False
        else:
            print("OK   unnormalized quaternion accepted")
    except (OSError, ValueError) as exc:
        print(f"FAIL unnormalized quaternion: {exc}")
        ok = False
    finally:
        path.unlink(missing_ok=True)
    return ok


def main():
    ok = check_bundled()
    ok = check_contract() and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
