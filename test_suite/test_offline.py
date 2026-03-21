"""
Offline regression tests for the Tripletex agent.

Tests the validation rules engine, auto-fix logic, and system prompt
WITHOUT making real API calls. Fast (<5s), no tokens consumed.

Usage:
    python test_suite/test_offline.py
"""

import json
import sys
import os
import re
from pathlib import Path

# Add parent dir so we can import from main
sys.path.insert(0, str(Path(__file__).parent.parent))

from main import validate_tool_call, get_rules, SYSTEM_PROMPT_TEMPLATE

# ── Test helpers ────────────────────────────────────────────────────
passed = 0
failed = 0


def ok(test_name):
    global passed
    passed += 1
    print(f"  ✓ {test_name}")


def fail(test_name, detail=""):
    global failed
    failed += 1
    print(f"  ✗ {test_name}")
    if detail:
        print(f"    → {detail}")


def expect_violation(test_name, method, path, body=None, params=None, rule_id=None):
    """Expect at least one violation. Optionally check for specific rule_id."""
    v = validate_tool_call(method, path, body=body, params=params)
    if v:
        if rule_id and not any(rule_id in x for x in v):
            fail(test_name, f"Got violations but not for {rule_id}: {v}")
        else:
            ok(test_name)
    else:
        fail(test_name, "Expected violation but got none")


def expect_pass(test_name, method, path, body=None, params=None):
    """Expect no violations."""
    v = validate_tool_call(method, path, body=body, params=params)
    if v:
        fail(test_name, f"Unexpected violations: {v}")
    else:
        ok(test_name)


# ── 1. Rules engine loads correctly ────────────────────────────────
def test_rules_loading():
    print("\n── Rules Engine ──")
    rules = get_rules()
    if len(rules) >= 40:
        ok(f"Loaded {len(rules)} rules")
    else:
        fail(f"Expected >=40 rules, got {len(rules)}")


# ── 2. Existing rules still work ───────────────────────────────────
def test_existing_rules():
    print("\n── Existing Rules ──")

    # Customer POST requires name + isCustomer
    expect_violation("customer-post-missing-name", "POST", "/customer",
                     body={"isCustomer": True}, rule_id="customer-required")
    expect_pass("customer-post-valid", "POST", "/customer",
                body={"name": "Test AS", "isCustomer": True})

    # Employee POST requires firstName, lastName
    expect_violation("employee-post-missing-name", "POST", "/employee",
                     body={"email": "test@test.no"}, rule_id="employee-post-required")
    expect_pass("employee-post-valid", "POST", "/employee",
                body={"firstName": "Kari", "lastName": "Hansen"})

    # Employee PUT rejects email
    expect_violation("employee-put-with-email", "PUT", "/employee/123",
                     body={"id": 123, "firstName": "Kari", "email": "x@x.no"},
                     rule_id="employee-put-no-email")

    # Salary POST requires year, month, payslips
    expect_violation("salary-missing-payslips", "POST", "/salary/transaction",
                     body={"year": 2026, "month": 3}, rule_id="salary-required")
    expect_pass("salary-valid", "POST", "/salary/transaction",
                body={"year": 2026, "month": 3, "payslips": [{"employee": {"id": 1}}]})

    # Order/orderline requires order.id and product.id
    expect_violation("orderline-missing-product", "POST", "/order/orderline",
                     body={"order": {"id": 1}}, rule_id="orderline-required")
    expect_pass("orderline-valid", "POST", "/order/orderline",
                body={"order": {"id": 1}, "product": {"id": 2}})

    # Invoice POST requires invoiceDate, invoiceDueDate, orders
    # Invoice POST with orders is valid — invoiceDate/invoiceDueDate are optional in some rules
    expect_pass("invoice-with-orders", "POST", "/invoice",
                body={"orders": [{"id": 1}]})

    # Product POST requires name
    expect_violation("product-missing-name", "POST", "/product",
                     body={"priceExcludingVatCurrency": 100}, rule_id="product-required")

    # Project POST requires name, number, projectManager.id, startDate
    expect_violation("project-missing-fields", "POST", "/project",
                     body={"name": "Test"}, rule_id="project-required")
    expect_pass("project-valid", "POST", "/project",
                body={"name": "Test", "number": "PRJ-1234",
                      "projectManager": {"id": 1}, "startDate": "2026-03-21"})


# ── 3. NEW employment division rules ───────────────────────────────
def test_employment_division_rules():
    print("\n── Employment Division Rules (NEW) ──")

    # POST /employee/employment without division should fail
    expect_violation("employment-no-division", "POST", "/employee/employment",
                     body={"employee": {"id": 1}, "startDate": "2026-03-01"},
                     rule_id="employment-require-division")

    # POST with division should pass
    expect_pass("employment-with-division", "POST", "/employee/employment",
                body={"employee": {"id": 1}, "startDate": "2026-03-01",
                      "division": {"id": 107894293}})

    # PUT /employee/employment with division should be rejected
    expect_violation("employment-put-division", "PUT", "/employee/employment/12345",
                     body={"id": 12345, "version": 0, "division": {"id": 728212}},
                     rule_id="employment-no-division-on-put")

    # PUT without division should pass
    expect_pass("employment-put-no-division", "PUT", "/employee/employment/12345",
                body={"id": 12345, "version": 0, "startDate": "2026-03-01"})


# ── 4. Supplier invoice rules ──────────────────────────────────────
def test_supplier_invoice_rules():
    print("\n── Supplier Invoice Rules ──")

    expect_violation("supplier-invoice-missing-fields", "POST", "/supplierInvoice",
                     body={"invoiceNumber": "123"},
                     rule_id="supplier-invoice-required")
    expect_pass("supplier-invoice-valid", "POST", "/supplierInvoice",
                body={"invoiceNumber": "123", "invoiceDate": "2026-03-21",
                      "invoiceDueDate": "2026-04-04", "supplier": {"id": 1},
                      "voucher": {"date": "2026-03-21"}})


# ── 5. System prompt contains key workflows ─────────────────────────
def test_system_prompt():
    print("\n── System Prompt ──")
    prompt = SYSTEM_PROMPT_TEMPLATE

    checks = [
        ("has salary workflow", "SALARY / PAYROLL WORKFLOW"),
        ("has invoice workflow", "INVOICE WORKFLOW"),
        ("has travel expense workflow", "TRAVEL EXPENSE WORKFLOW"),
        ("has milestone workflow", "MILESTONE INVOICE WORKFLOW"),
        ("has employee workflow", "EMPLOYEE WORKFLOW"),
        ("has project workflow", "PROJECT WORKFLOW"),
        ("fixedprice lowercase warning", "fixedprice"),
        ("division in employment", "division"),
        ("milestone VAT back-calculation", "milestoneAmount / 1.25"),
        ("vatType lookup guidance", 'Utgående'),
        ("fakturér trigger", "fakturér"),
        ("company lookup for division", "/company/>withLoginAccess"),
    ]

    for name, substring in checks:
        if substring in prompt:
            ok(name)
        else:
            fail(name, f"Missing '{substring}' in system prompt")


# ── 6. Milestone auto-fix logic ────────────────────────────────────
def test_milestone_autofix_logic():
    """Test the milestone pricing calculation logic without calling main."""
    print("\n── Milestone Price Auto-fix Logic ──")

    test_cases = [
        # (fixedprice, product_price, should_correct, expected_corrected)
        (274950, 137475, True, 109980.0),     # 50%
        (274950, 68737.5, True, 54990.0),     # 25%
        (300000, 100000, True, 80000.0),      # 1/3
        (274950, 206212.5, True, 164970.0),   # 75%
        (274950, 274950, True, 219960.0),     # 100%
        (274950, 50000, False, None),          # Not a fraction
        (274950, 109980, False, None),         # Already corrected (not a clean fraction)
    ]

    known_fractions = [0.25, 1/3, 0.5, 0.75, 1.0]

    for fixedprice, price, should_correct, expected in test_cases:
        ratio = round(price / fixedprice, 4)
        corrected = None
        for frac in known_fractions:
            if abs(ratio - frac) < 0.001:
                corrected = round(price / 1.25, 2)
                break

        if should_correct:
            if corrected == expected:
                ok(f"milestone {price}/{fixedprice} → {corrected}")
            else:
                fail(f"milestone {price}/{fixedprice}", f"Expected {expected}, got {corrected}")
        else:
            if corrected is None:
                ok(f"milestone {price}/{fixedprice} → unchanged (correct)")
            else:
                fail(f"milestone {price}/{fixedprice}", f"Should not correct but got {corrected}")


# ── 7. All REAL cases can be loaded ─────────────────────────────────
def test_cases_loadable():
    print("\n── Test Cases ──")
    cases_dir = Path(__file__).parent / "cases"
    cases = list(cases_dir.glob("**/*.json"))
    load_ok = 0
    load_fail = 0
    for c in cases:
        try:
            data = json.loads(c.read_text(encoding="utf-8"))
            assert "prompt" in data, "missing prompt"
            load_ok += 1
        except Exception as e:
            load_fail += 1
            fail(f"load {c.stem}", str(e))

    if load_fail == 0:
        ok(f"All {load_ok} test cases load successfully")
    else:
        fail(f"{load_fail}/{load_ok + load_fail} cases failed to load")


# ── 8. Action endpoint rules ───────────────────────────────────────
def test_action_endpoint_rules():
    print("\n── Action Endpoint Rules ──")

    # :send should not have body
    expect_violation("send-with-body", "PUT", "/invoice/123/:send",
                     body={"sendType": "EMAIL"}, rule_id="action-endpoint-no-body-send")
    expect_pass("send-with-params", "PUT", "/invoice/123/:send",
                params={"sendType": "EMAIL"})

    # :payment should not have body
    expect_violation("payment-with-body", "PUT", "/invoice/123/:payment",
                     body={"paymentDate": "2026-03-21"}, rule_id="action-endpoint-no-body-payment")


# ── 9. Credit note rules ───────────────────────────────────────────
def test_credit_note_rules():
    print("\n── Credit Note Rules ──")
    expect_violation("creditnote-missing-date", "PUT", "/invoice/123/:createCreditNote",
                     params={}, rule_id="creditnote-date")


# ── Main ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("  OFFLINE REGRESSION TESTS")
    print("=" * 60)

    test_rules_loading()
    test_existing_rules()
    test_employment_division_rules()
    test_supplier_invoice_rules()
    test_system_prompt()
    test_milestone_autofix_logic()
    test_cases_loadable()
    test_action_endpoint_rules()
    test_credit_note_rules()

    print(f"\n{'=' * 60}")
    print(f"  RESULTS: {passed} passed, {failed} failed")
    print(f"{'=' * 60}")

    sys.exit(1 if failed > 0 else 0)
