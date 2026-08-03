#!/usr/bin/env python3
"""isaac_raspicat.py の --rtf-report を読み、その実行が「Pi4 の速度計測として
成立しているか」を判定する。

## なぜこれが必要か

Pi4 相当への減速は cgroup の CPU quota で行う (pi4_sim ハーネスと同じ方式)。
quota は **実時間 (wall clock)** 基準である。一方 `--use-sim-time` を付けると
nav2 の締め切り・タイマ・TF の期限は **シム時間** 基準になる。

ここで RTF (= シム時間の進み / 実時間の進み) が 1.0 を割ると:

    1 シム秒 = 1/RTF 実秒   ->   nav2 は 1 シム秒あたり 1/RTF 倍の CPU 時間を得る

つまり **RTF 0.5 の実行は、Pi4 が実際の 2 倍速いという結果を出す**。
「重い地図でも間に合った」という結論がそのまま嘘になるので、RTF はログではなく
**合否条件**として扱う。

`--use-sim-time` を使わない実行 (既定) では nav2 の時計も cgroup も実時間なので
この歪みは起きない。ただし RTF が落ちていれば「センサが実時間より遅れて届く」と
いう別の非現実性が入るため、警告は出す (判定は緩める)。

## 使い方

    python3 rtf_gate.py /tmp/isaac/rtf.jsonl --min-rtf 0.95 --max-below-frac 0.05

終了コード: 0 = 成立、3 = RTF 不足で**この実行の測定結果は無効**、2 = 入力不備。
"""

import argparse
import json
import os
import sys


def load(path):
    """JSON Lines を読む。末尾の {"summary": ...} 行は無視して生サンプルだけ返す。"""
    samples = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "rtf" in obj:
                samples.append(obj)
    return samples


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("report", help="isaac_raspicat.py --rtf-report が書いた JSON Lines")
    ap.add_argument("--min-rtf", type=float, default=0.95,
                    help="これを下回るサンプルを「落ちた」とみなす (既定: 0.95)")
    ap.add_argument("--max-below-frac", type=float, default=0.05,
                    help="落ちたサンプルの許容割合 (既定: 0.05 = 5%%)")
    ap.add_argument("--warmup", type=float, default=10.0,
                    help="開始から何秒を判定対象外にするか [s] (既定: 10)。"
                         "ステージ読み込み直後は必ず RTF が落ちるため")
    ap.add_argument("--use-sim-time", action="store_true",
                    help="nav2 を use_sim_time:=true で回した実行。"
                         "判定を**厳格**にする (RTF 不足は測定無効)")
    args = ap.parse_args()

    if not os.path.isfile(args.report):
        print(f"rtf_gate: report not found: {args.report}", file=sys.stderr)
        return 2

    samples = load(args.report)
    warm = [s for s in samples if s.get("wall", 0.0) >= args.warmup]
    if not warm:
        print(f"rtf_gate: no samples after warmup {args.warmup}s "
              f"(total {len(samples)})", file=sys.stderr)
        return 2

    vals = sorted(s["rtf"] for s in warm)
    below = [v for v in vals if v < args.min_rtf]
    frac = len(below) / len(vals)
    mean = sum(vals) / len(vals)

    print(f"rtf_gate: n={len(vals)} (warmup {args.warmup}s excluded, "
          f"{len(samples) - len(warm)} dropped)")
    print(f"rtf_gate: min={vals[0]:.3f} p05={vals[max(0, int(0.05 * len(vals)) - 1)]:.3f} "
          f"mean={mean:.3f} max={vals[-1]:.3f}")
    print(f"rtf_gate: below {args.min_rtf}: {len(below)}/{len(vals)} "
          f"({100.0 * frac:.1f}%, allowed {100.0 * args.max_below_frac:.1f}%)")

    ok = frac <= args.max_below_frac
    if ok:
        print("rtf_gate: PASS - Pi4 の速度計測として成立している")
        return 0

    if args.use_sim_time:
        print("rtf_gate: FAIL - RTF が不足しており、この実行の計測は **無効**。", file=sys.stderr)
        print("  use_sim_time:=true では nav2 の締め切りがシム時間基準になる一方、",
              file=sys.stderr)
        print("  cgroup の CPU quota は実時間基準のままなので、RTF が落ちた分だけ",
              file=sys.stderr)
        print(f"  Pi4 が実際より速く見える (この実行では最悪 {1.0 / max(vals[0], 1e-6):.2f} 倍)。",
              file=sys.stderr)
        print("  対処: --render-dt を大きくする / --headless で回す / 地図を",
              file=sys.stderr)
        print("  downsample_map.py で粗くする / use_sim_time を使わない。", file=sys.stderr)
        return 3

    print("rtf_gate: WARN - RTF は落ちているが use_sim_time を使っていないため、",
          file=sys.stderr)
    print("  nav2 の時計と cgroup quota は同じ実時間基準で、減速率そのものは歪まない。",
          file=sys.stderr)
    print("  ただしセンサ更新が実時間に対して遅れているので、観測周期に依存する",
          file=sys.stderr)
    print("  結論 (コストマップの追従性など) は割り引いて読むこと。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
