"""
Test runner for Tripletex AI agent.

Sends each test case to the local agent server, captures logs,
and validates behavioral patterns (not sandbox state, since the
persistent sandbox may differ from competition sandboxes).

Usage:
    python test_suite/run_tests.py                  # run all cases
    python test_suite/run_tests.py invoice           # filter by keyword
    python test_suite/run_tests.py --list            # list available cases
"""

import json
import os
import re
import sys
import time
import threading
import io
from pathlib import Path

import requests

# ── Config ──────────────────────────────────────────────────────────
AGENT_URL = os.environ.get("AGENT_URL", "http://127.0.0.1:8000/solve")
SANDBOX_URL = os.environ.get(
    "SANDBOX_URL", "https://kkpqfuj-amager.tripletex.dev/v2"
)
SESSION_TOKEN = os.environ.get(
    "SESSION_TOKEN",
    "eyJ0b2tlbklkIjoyMTQ3NjI5MzM3LCJ0b2tlbiI6IjQ4MTIyOGIzLWU3MWUtNDBiOC1hOWJjLTVmY2I3YTc2YmI0NyJ9",
)
TIMEOUT = int(os.environ.get("TEST_TIMEOUT", "180"))
CASES_DIR = Path(__file__).parent / "cases"


# ── Behavioral checks ──────────────────────────────────────────────
def check_invoice_behavior(logs: str, prompt_lower: str) -> list[str]:
    """Validate behavioral patterns for invoice tasks."""
    issues = []

    # Must look up vatType before creating products
    vat_lookup = logs.find("GET /ledger/vatType")
    product_create = logs.find("POST /product")
    if product_create != -1 and (vat_lookup == -1 or vat_lookup > product_create):
        issues.append("FAIL: Created product BEFORE looking up vatType")

    # Bank account should be set up
    if "bank" in logs.lower() and "already ready" not in logs and "bank account set OK" not in logs:
        if "bankAccountReady" not in logs:
            issues.append("WARN: Bank account setup may have failed")

    # Must create customer before order
    cust_create = logs.find("POST /customer")
    order_create = logs.find("POST /order\n")  # not /order/orderline
    if order_create == -1:
        order_create = logs.find("POST /order ")
    if order_create != -1 and (cust_create == -1 or cust_create > order_create):
        issues.append("FAIL: Created order BEFORE creating customer")

    # Must use sendType, not sendMethod
    if "sendmethod" in logs.lower() and "not sendmethod" not in logs.lower():
        issues.append("FAIL: Used sendMethod instead of sendType for invoice send")

    # Invoice must be created
    if "POST /invoice" not in logs:
        issues.append("FAIL: Never attempted POST /invoice")

    # If task says "send", must call :send
    send_words = ["send", "sende", "senden", "enviar", "envoyer", "envie"]
    if any(w in prompt_lower for w in send_words):
        if "/:send" not in logs:
            issues.append("FAIL: Task requires sending invoice but :send was never called")

    return issues


def check_supplier_behavior(logs: str, prompt_lower: str) -> list[str]:
    issues = []
    if "POST /supplier" not in logs:
        issues.append("FAIL: Never called POST /supplier")
    if "POST /customer" in logs and "isSupplier" in logs:
        issues.append("WARN: Used POST /customer for supplier — should use POST /supplier")
    return issues


def check_employee_behavior(logs: str, prompt_lower: str) -> list[str]:
    issues = []
    if "POST /employee" not in logs and "PUT /employee" not in logs:
        issues.append("FAIL: Never created or updated an employee")
    return issues


def check_department_behavior(logs: str, prompt_lower: str) -> list[str]:
    issues = []
    if "POST /department" not in logs:
        issues.append("FAIL: Never called POST /department")
    return issues


def check_project_behavior(logs: str, prompt_lower: str) -> list[str]:
    issues = []
    if "POST /project" not in logs:
        issues.append("FAIL: Never called POST /project")
    # Project requires startDate
    if "startDate" not in logs:
        issues.append("WARN: No startDate found in project creation")
    return issues


def check_payment_behavior(logs: str, prompt_lower: str) -> list[str]:
    issues = []
    if "/:payment" not in logs:
        issues.append("FAIL: Never called /:payment endpoint")
    if "GET /invoice/paymentType" not in logs:
        issues.append("WARN: Did not look up paymentType before paying")
    return issues


def check_common_behavior(logs: str, prompt_lower: str) -> list[str]:
    """Checks that apply to all tasks."""
    issues = []

    # Must call done()
    if "done()" not in logs and "DONE" not in logs:
        issues.append("FAIL: Agent never called done()")

    # Should not hit max iterations
    if "Max iterations reached" in logs:
        issues.append("FAIL: Hit max iterations — agent gave up")

    # No tool calls = LLM stopped prematurely
    if "No tool calls" in logs and "DONE" not in logs:
        issues.append("FAIL: LLM stopped without calling done()")

    # Count 4xx errors (efficiency metric)
    error_count = len(re.findall(r"ERR \(", logs))
    if error_count > 5:
        issues.append(f"WARN: {error_count} API errors — poor efficiency")
    elif error_count > 2:
        issues.append(f"INFO: {error_count} API errors")

    return issues


# ── Task type detection ─────────────────────────────────────────────
TASK_CHECKS = {
    "invoice": {
        "keywords": ["faktura", "invoice", "rechnung", "factura", "facture", "fatura"],
        "check": check_invoice_behavior,
    },
    "supplier": {
        "keywords": ["leverandør", "supplier", "lieferant", "proveedor", "fournisseur", "fornecedor"],
        "check": check_supplier_behavior,
    },
    "employee": {
        "keywords": ["ansatt", "employee", "mitarbeiter", "empleado", "employé", "empregado"],
        "check": check_employee_behavior,
    },
    "department": {
        "keywords": ["avdeling", "department", "abteilung", "departamento", "département"],
        "check": check_department_behavior,
    },
    "project": {
        "keywords": ["prosjekt", "project", "projekt", "proyecto", "projet"],
        "check": check_project_behavior,
    },
    "payment": {
        "keywords": ["betal", "payment", "zahlung", "pago", "paiement", "pagamento"],
        "check": check_payment_behavior,
    },
}


def detect_task_type(prompt_lower: str) -> str:
    for task_type, info in TASK_CHECKS.items():
        if any(kw in prompt_lower for kw in info["keywords"]):
            return task_type
    return "unknown"


# ── Test execution ──────────────────────────────────────────────────
def run_single_test(case_path: Path, verbose: bool = True) -> dict:
    """Run a single test case and return results."""
    with open(case_path, "r", encoding="utf-8") as f:
        case = json.load(f)

    prompt = case["prompt"]
    files = case.get("files", [])
    source = case.get("source", "unknown")
    prompt_lower = prompt.lower()
    task_type = detect_task_type(prompt_lower)
    tag = "AI" if source == "ai-generated" else "REAL" if source == "competition" else "?"

    print(f"\n{'━'*70}")
    print(f"  TEST: {case_path.stem}  [{tag}]")
    print(f"  Type: {task_type}")
    print(f"  Prompt: {prompt[:120]}{'…' if len(prompt) > 120 else ''}")
    print(f"{'━'*70}")

    # Start a log capture thread via the agent's stdout
    # We'll rely on the agent returning and then check Cloud Run logs
    # For local testing, we capture via the response time and errors

    t0 = time.time()
    try:
        resp = requests.post(
            AGENT_URL,
            json={
                "prompt": prompt,
                "files": files,
                "tripletex_credentials": {
                    "base_url": SANDBOX_URL,
                    "session_token": SESSION_TOKEN,
                },
            },
            timeout=TIMEOUT,
        )
        elapsed = time.time() - t0
        status_ok = resp.status_code == 200
        response_data = resp.json() if status_ok else {}
    except requests.Timeout:
        elapsed = time.time() - t0
        status_ok = False
        response_data = {}
        print(f"  ✗ TIMEOUT after {elapsed:.1f}s")
    except Exception as e:
        elapsed = time.time() - t0
        status_ok = False
        response_data = {}
        print(f"  ✗ ERROR: {e}")

    result = {
        "case": case_path.stem,
        "task_type": task_type,
        "source_tag": tag,
        "prompt_preview": prompt[:100],
        "http_ok": status_ok,
        "elapsed": elapsed,
        "status": response_data.get("status", "error"),
        "iterations": response_data.get("iterations", 0),
        "api_calls": response_data.get("api_calls", []),
        "api_errors": response_data.get("errors", []),
        "tokens": response_data.get("tokens", 0),
        "issues": [],
    }

    if not status_ok:
        result["issues"].append(f"FAIL: HTTP {resp.status_code if 'resp' in dir() else 'N/A'}")

    if result["status"] == "incomplete":
        result["issues"].append("FAIL: Agent did not call done()")

    if result["iterations"] == 0:
        result["issues"].append("FAIL: Zero iterations")

    if len(result["api_errors"]) > 5:
        result["issues"].append(f"WARN: {len(result['api_errors'])} API errors")

    # Note: For full behavioral checks, we need server logs.
    # When running locally, logs go to the server's stdout.
    # We print a summary here; the detailed logs are in the server terminal.
    print(f"  HTTP: {'✓' if status_ok else '✗'} {resp.status_code if status_ok else 'FAIL'}")
    print(f"  Time: {elapsed:.1f}s | Iters: {result['iterations']} | Tokens: {result['tokens']}")
    print(f"  Status: {result['status']} | API calls: {len(result['api_calls'])} | Errors: {len(result['api_errors'])}")
    if result["api_calls"]:
        for ac in result["api_calls"]:
            marker = "  ✗" if "-> 4" in ac or "-> 5" in ac else "  ✓"
            print(f"    {marker} {ac}")
    if result["api_errors"]:
        # Show unique error details (validation messages) for failed calls
        seen = set()
        for err in result["api_errors"]:
            # err may contain ": validation message" after the status code
            if ": " in err and err not in seen:
                seen.add(err)
                parts = err.split(": ", 1)
                if len(parts) == 2 and parts[1]:
                    print(f"      ^ {parts[1][:120]}")
    if result["issues"]:
        for iss in result["issues"]:
            print(f"  ⚠ {iss}")

    return result


def run_all_tests(filter_keyword: str = None) -> None:
    """Run all test cases, optionally filtered."""
    cases = sorted(CASES_DIR.glob("**/*.json"))
    if not cases:
        print(f"No test cases found in {CASES_DIR}")
        print("Run some tasks first — prompts are auto-captured to test_suite/cases/")
        print("Or add cases manually (see test_suite/cases/README.md)")
        return

    if filter_keyword:
        filtered = []
        for c in cases:
            with open(c, "r", encoding="utf-8") as f:
                data = json.load(f)
            if filter_keyword.lower() in data.get("prompt", "").lower() or filter_keyword.lower() in c.stem.lower():
                filtered.append(c)
        cases = filtered
        print(f"Filtered to {len(cases)} cases matching '{filter_keyword}'")

    print(f"\n{'='*70}")
    print(f"  RUNNING {len(cases)} TEST CASES")
    print(f"  Agent: {AGENT_URL}")
    print(f"  Sandbox: {SANDBOX_URL}")
    print(f"{'='*70}")

    results = []
    for case_path in cases:
        result = run_single_test(case_path)
        results.append(result)

    # Summary
    print(f"\n\n{'='*70}")
    print(f"  RESULTS SUMMARY")
    print(f"{'='*70}")
    total = len(results)
    ok = sum(1 for r in results if r["http_ok"] and r["status"] == "completed")
    failed = total - ok
    total_time = sum(r["elapsed"] for r in results)

    for r in results:
        done = r["status"] == "completed"
        status_icon = "✓" if done else "✗"
        tag = r.get("source_tag", "?")
        errs = len(r.get("api_errors", []))
        iters = r.get("iterations", 0)
        tokens = r.get("tokens", 0)
        err_str = f" [{errs} err]" if errs > 0 else ""
        issues_str = f" — {', '.join(r['issues'])}" if r["issues"] else ""
        print(f"  {status_icon} [{r['task_type']:>10}] {r['case'][:40]:<40} {r['elapsed']:>5.1f}s i={iters} t={tokens}{err_str}{issues_str}")

    print(f"\n  Total: {total} | Passed: {ok} | Failed: {failed} | Time: {total_time:.1f}s")
    print(f"{'='*70}\n")


def list_cases() -> None:
    """List all available test cases."""
    cases = sorted(CASES_DIR.glob("**/*.json"))
    if not cases:
        print(f"No test cases in {CASES_DIR}")
        return

    print(f"\nAvailable test cases ({len(cases)}):")
    for c in cases:
        with open(c, "r", encoding="utf-8") as f:
            data = json.load(f)
        prompt = data.get("prompt", "")
        task_type = detect_task_type(prompt.lower())
        source = data.get("source", "unknown")
        tag = "AI" if source == "ai-generated" else "REAL" if source == "competition" else "?"
        print(f"  [{task_type:>10}] [{tag:>4}] {c.stem}  \u2014 {prompt[:70]}{'\u2026' if len(prompt) > 70 else ''}")


# ── Main ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    args = sys.argv[1:]

    if "--list" in args:
        list_cases()
    elif args:
        run_all_tests(filter_keyword=args[0])
    else:
        run_all_tests()
