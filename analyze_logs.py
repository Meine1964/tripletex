#!/usr/bin/env python3
"""
Log analysis script for Tripletex agent.
Run: python analyze_logs.py [logs_dir]

Scans all .log files and produces:
1. Summary table (task type, status, iterations, time)
2. Top error patterns with counts
3. Actionable recommendations for new auto-fixes or rules
"""

import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


def parse_log(filepath: str) -> dict:
    """Parse a single log file and extract key metrics."""
    text = Path(filepath).read_text(encoding="utf-8", errors="ignore")
    name = os.path.basename(filepath)

    info = {
        "name": name,
        "status": "ok" if "_ok_" in name else "fail",
        "iterations": 0,
        "time_s": 0.0,
        "api_errors": [],
        "auto_fixes": [],
        "rule_violations": [],
        "task_type": "unknown",
        "prompt_hint": "",
    }

    # Extract task type and hint from filename
    # Format: YYYYMMDD_HHMMSS_RAND_TYPE_STATUS_NITERiter_HINT.log
    parts = name.replace(".log", "").split("_")
    if len(parts) >= 5:
        # Find the status part (ok/fail)
        for i, p in enumerate(parts):
            if p in ("ok", "fail") and i >= 3:
                info["task_type"] = parts[i - 1] if i > 3 else "task"
                info["prompt_hint"] = " ".join(parts[i + 1:]).replace("iter", "").strip()
                break

    # Extract iterations
    m = re.search(r"(\d+)iter", name)
    if m:
        info["iterations"] = int(m.group(1))

    # Extract total time
    m = re.search(r"TASK COMPLETE.*?total\s+([\d.]+)s", text)
    if m:
        info["time_s"] = float(m.group(1))

    # Extract API errors (422, 400, 404, 500)
    for m in re.finditer(r'└─\s+(\d{3})\s+ERR.*?"validationMessages".*?"field":\s*"([^"]*)".*?"message":\s*"([^"]*)"', text):
        info["api_errors"].append({
            "status": int(m.group(1)),
            "field": m.group(2),
            "message": m.group(3),
        })

    # Extract auto-fixes applied
    for m in re.finditer(r'\[fix\]\s+(.+)', text):
        info["auto_fixes"].append(m.group(1).strip())

    # Extract rule violations
    for m in re.finditer(r'\[reject\].*?violation', text):
        info["rule_violations"].append(m.group(0).strip())

    return info


def analyze_logs(log_dir: str):
    """Analyze all logs and print report."""
    logs = sorted(Path(log_dir).glob("*.log"))
    if not logs:
        print(f"No .log files found in {log_dir}")
        return

    results = [parse_log(str(f)) for f in logs]

    # Summary table
    print(f"\n{'='*80}")
    print(f"  LOG ANALYSIS — {len(results)} logs")
    print(f"{'='*80}\n")

    ok_count = sum(1 for r in results if r["status"] == "ok")
    fail_count = sum(1 for r in results if r["status"] == "fail")
    print(f"  Success: {ok_count}/{len(results)} ({ok_count/len(results)*100:.0f}%)")
    print(f"  Failed:  {fail_count}/{len(results)}")
    print()

    # Per-task-type breakdown
    by_type = defaultdict(list)
    for r in results:
        by_type[r["task_type"]].append(r)

    print(f"  {'Task Type':<20} {'OK':>4} {'Fail':>4} {'Avg Iter':>9} {'Avg Time':>9}")
    print(f"  {'-'*20} {'-'*4} {'-'*4} {'-'*9} {'-'*9}")
    for tt in sorted(by_type.keys()):
        runs = by_type[tt]
        ok = sum(1 for r in runs if r["status"] == "ok")
        fail = sum(1 for r in runs if r["status"] == "fail")
        avg_iter = sum(r["iterations"] for r in runs) / len(runs) if runs else 0
        avg_time = sum(r["time_s"] for r in runs) / len(runs) if runs else 0
        print(f"  {tt:<20} {ok:>4} {fail:>4} {avg_iter:>8.1f} {avg_time:>8.1f}s")

    # Top error patterns
    print(f"\n{'─'*80}")
    print(f"  TOP API ERRORS")
    print(f"{'─'*80}\n")

    error_counter = Counter()
    for r in results:
        for e in r["api_errors"]:
            key = f"{e['field']}: {e['message']}"
            error_counter[key] += 1

    if error_counter:
        for pattern, count in error_counter.most_common(15):
            print(f"  {count:>3}x  {pattern[:100]}")
    else:
        print("  No API errors found!")

    # Auto-fixes applied
    print(f"\n{'─'*80}")
    print(f"  AUTO-FIXES APPLIED")
    print(f"{'─'*80}\n")

    fix_counter = Counter()
    for r in results:
        for f in r["auto_fixes"]:
            # Normalize: strip specific IDs/values
            norm = re.sub(r'\d+', 'N', f)
            fix_counter[norm] += 1

    for fix, count in fix_counter.most_common(15):
        print(f"  {count:>3}x  {fix[:100]}")

    # Failed tasks detail
    if fail_count:
        print(f"\n{'─'*80}")
        print(f"  FAILED TASKS")
        print(f"{'─'*80}\n")

        for r in results:
            if r["status"] == "fail":
                print(f"  {r['name']}")
                print(f"    Type: {r['task_type']}, Iterations: {r['iterations']}, Time: {r['time_s']:.1f}s")
                if r["api_errors"]:
                    unique_errors = set(f"{e['field']}: {e['message']}" for e in r["api_errors"])
                    for e in list(unique_errors)[:5]:
                        print(f"    Error: {e[:120]}")
                print()

    # High-iteration tasks (potential optimization targets)
    print(f"\n{'─'*80}")
    print(f"  HIGH-ITERATION TASKS (>10 iters, optimization targets)")
    print(f"{'─'*80}\n")

    for r in sorted(results, key=lambda x: x["iterations"], reverse=True):
        if r["iterations"] > 10:
            errors = len(r["api_errors"])
            print(f"  {r['iterations']:>2} iters  {r['time_s']:>6.1f}s  {errors} errors  {r['status']:>4}  {r['name'][:60]}")

    print(f"\n{'='*80}")
    print(f"  END OF ANALYSIS")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    log_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "logs")
    analyze_logs(log_dir)
