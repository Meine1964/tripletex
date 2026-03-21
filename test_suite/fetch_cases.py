"""
Extract captured test cases from Cloud Run logs.

Usage:

1. Default — scan all files in test_suite/logs/:
     python test_suite/fetch_cases.py

2. Paste logs interactively:
     python test_suite/fetch_cases.py --paste

3. Pipe from stdin:
     cat logs.txt | python test_suite/fetch_cases.py --stdin

4. Read a specific file:
     python test_suite/fetch_cases.py --file path/to/logs.txt
"""

import hashlib
import json
import re
import sys
from pathlib import Path

CASES_DIR = Path(__file__).parent / "cases"
LOGS_DIR = Path(__file__).parent / "logs"
CAPTURE_PATTERN = re.compile(r"CASE_CAPTURE:({.*?}):END_CAPTURE")


def extract_cases(text: str) -> list[dict]:
    """Extract captured cases from log text."""
    cases = []
    for match in CAPTURE_PATTERN.finditer(text):
        try:
            case = json.loads(match.group(1))
            if "prompt" in case:
                cases.append(case)
        except json.JSONDecodeError:
            continue
    return cases


def save_cases(cases: list[dict]) -> int:
    """Save cases to test_suite/cases/, skipping duplicates."""
    CASES_DIR.mkdir(parents=True, exist_ok=True)

    # Load existing prompts to deduplicate
    existing_prompts = set()
    for f in CASES_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            existing_prompts.add(data.get("prompt", ""))
        except Exception:
            continue

    saved = 0
    for case in cases:
        prompt = case.get("prompt", "")
        if prompt in existing_prompts:
            print(f"  skip (duplicate): {prompt[:60]}...")
            continue

        slug = hashlib.md5(prompt.encode()).hexdigest()[:8]
        ts = case.get("captured_at", "unknown")
        filename = f"{ts}_{slug}.json"
        filepath = CASES_DIR / filename

        filepath.write_text(
            json.dumps(case, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        existing_prompts.add(prompt)
        saved += 1
        print(f"  saved: {filename}  — {prompt[:60]}...")

    return saved


def main():
    args = sys.argv[1:]

    if "--paste" in args:
        print("Paste Cloud Run log text below, then press Ctrl+Z (Windows) or Ctrl+D (Linux/Mac):")
        text = sys.stdin.read()
    elif "--stdin" in args:
        text = sys.stdin.read()
    elif "--file" in args:
        idx = args.index("--file")
        if idx + 1 < len(args):
            text = Path(args[idx + 1]).read_text(encoding="utf-8")
        else:
            print("Usage: --file <path>")
            sys.exit(1)
    else:
        # Default: scan all .txt files in test_suite/logs/ (recursively)
        log_files = sorted(LOGS_DIR.glob("**/*.txt"))
        if not log_files:
            print(f"No .txt files in {LOGS_DIR}")
            print("Save Cloud Run logs there, then re-run this script.")
            print("Or use: --paste, --stdin, or --file <path>")
            sys.exit(0)
        print(f"Scanning {len(log_files)} log files in {LOGS_DIR.name}/...")
        text = ""
        for lf in log_files:
            content = lf.read_text(encoding="utf-8", errors="replace")
            count = len(CAPTURE_PATTERN.findall(content))
            print(f"  {lf.name}: {count} captures")
            text += content + "\n"

    cases = extract_cases(text)
    if not cases:
        print("No CASE_CAPTURE entries found.")
        sys.exit(0)

    print(f"\nFound {len(cases)} captured cases.")
    saved = save_cases(cases)
    print(f"\nDone: {saved} new cases saved, {len(cases) - saved} duplicates skipped.")


if __name__ == "__main__":
    main()
