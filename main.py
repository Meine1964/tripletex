import base64
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests
import urllib3
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from openai import OpenAI

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
load_dotenv()

app = FastAPI()
client = OpenAI()

# ── Version identifier ────────────────────────────────────────────
import subprocess as _sp
try:
    _GIT_SHA = _sp.check_output(["git", "rev-parse", "--short", "HEAD"],
                                stderr=_sp.DEVNULL, cwd=os.path.dirname(__file__) or "."
                                ).decode().strip()
except Exception:
    _GIT_SHA = None
# Fallback: read from VERSION file (baked in at deploy time)
if not _GIT_SHA:
    _ver_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")
    if os.path.exists(_ver_path):
        with open(_ver_path) as _vf:
            _GIT_SHA = _vf.read().strip()
AGENT_VERSION = _GIT_SHA or "unknown"


# ── Log capture + GitHub push ────────────────────────────────────
class LogCapture:
    """Tees stdout to a StringIO buffer so we can capture all print output."""
    def __init__(self):
        self.buffer = io.StringIO()
        self._original = None

    def write(self, text):
        if self._original:
            self._original.write(text)
        self.buffer.write(text)

    def flush(self):
        if self._original:
            self._original.flush()

    def __enter__(self):
        self._original = sys.stdout
        sys.stdout = self
        return self

    def __exit__(self, *args):
        sys.stdout = self._original

    def getvalue(self):
        return self.buffer.getvalue()


def push_log_to_github(log_text: str, filename: str):
    """Push a log file to the GitHub repo with retry on conflict."""
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        print("  [log] GITHUB_TOKEN not set — skipping log push", flush=True)
        return
    try:
        content_b64 = base64.b64encode(log_text.encode("utf-8")).decode("ascii")
        url = f"https://api.github.com/repos/Meine1964/tripletex/contents/test_suite/logs/Day_3/{filename}"
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        }
        data = {
            "message": f"Auto-log: {filename}",
            "content": content_b64,
        }
        for attempt in range(5):
            resp = requests.put(url, headers=headers, json=data, timeout=15)
            if resp.status_code in (200, 201):
                print(f"  [log] Pushed to GitHub: test_suite/logs/Day_3/{filename}", flush=True)
                return
            elif resp.status_code == 409:
                # Conflict — branch moved, retry after delay
                wait = 2 * (attempt + 1)
                print(f"  [log] GitHub 409 conflict, retry {attempt+1}/5 in {wait}s...", flush=True)
                time.sleep(wait)
                continue
            elif resp.status_code == 422 and "sha" in resp.text.lower():
                # File already exists (duplicate filename) — skip
                print(f"  [log] File already exists, skipping: {filename}", flush=True)
                return
            else:
                print(f"  [log] GitHub push failed: {resp.status_code} {resp.text[:200]}", flush=True)
                return
        print(f"  [log] GitHub push failed after 5 retries: {filename}", flush=True)
    except Exception as e:
        print(f"  [log] GitHub push error: {e}", flush=True)

# ── Validation Rules Engine ─────────────────────────────────────
_RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules.yaml")

def _load_rules():
    """Load validation rules from rules.yaml (cached after first load)."""
    if not os.path.exists(_RULES_PATH):
        return []
    with open(_RULES_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("rules", []) if data else []

_CACHED_RULES = None

def get_rules():
    global _CACHED_RULES
    if _CACHED_RULES is None:
        _CACHED_RULES = _load_rules()
    return _CACHED_RULES

def _field_exists(obj, dot_path):
    """Check if a nested field exists using dot notation (e.g. 'travelExpense.id')."""
    parts = dot_path.split(".")
    cur = obj
    for p in parts:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return False
    return True

def _get_field(obj, dot_path):
    """Get a nested field value using dot notation. Returns None if missing."""
    parts = dot_path.split(".")
    cur = obj
    for p in parts:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return None
    return cur

def validate_tool_call(method, path, body=None, params=None):
    """Check a tool call against all applicable rules.
    Returns a list of violation strings (empty = all OK)."""
    rules = get_rules()
    body = body or {}
    params = params or {}
    violations = []
    clean_path = path.rstrip("/")

    for rule in rules:
        w = rule.get("when", {})
        # Method match
        if w.get("method") and w["method"].upper() != method.upper():
            continue
        # Path match (exact or regex)
        rule_path = w.get("path")
        rule_pat = w.get("path_pattern")
        if rule_path and clean_path != rule_path.rstrip("/"):
            continue
        if rule_pat and not re.match(rule_pat, clean_path):
            continue
        if not rule_path and not rule_pat:
            continue

        rid = rule.get("id", "?")
        msg = rule.get("message", "Validation failed").strip()

        # Required body fields
        for f in rule.get("require_fields", []):
            if not _field_exists(body, f):
                violations.append(f"[{rid}] {msg} (missing: {f})")
                break

        # Rejected body fields
        for f in rule.get("reject_fields", []):
            if _field_exists(body, f):
                violations.append(f"[{rid}] {msg} (forbidden field: {f})")
                break

        # Required params
        for f in rule.get("require_params", []):
            if f not in params:
                violations.append(f"[{rid}] {msg} (missing param: {f})")
                break

        # Field format (regex)
        for f, pattern in rule.get("field_format", {}).items():
            val = _get_field(body, f) or params.get(f)
            if val is not None and not re.match(pattern, str(val)):
                violations.append(f"[{rid}] {msg} ({f}='{val}')")

        # Field type
        type_map = {"number": (int, float), "string": str, "array": list, "object": dict, "boolean": bool}
        for f, expected in rule.get("field_type", {}).items():
            val = _get_field(body, f)
            if val is not None:
                py_type = type_map.get(expected)
                if py_type and not isinstance(val, py_type):
                    violations.append(f"[{rid}] {msg} ({f} is {type(val).__name__}, expected {expected})")

    return violations

# ── End Validation Rules Engine ─────────────────────────────────

SYSTEM_PROMPT_TEMPLATE = """\
IMPORTANT — TODAY'S DATE IS {today}. Use {today} for all dates (invoiceDate, orderDate, deliveryDate, startDate, paymentDate, credit note date). NEVER use 2023 or 2024 or 2025 dates.

You are an AI accounting agent for Tripletex. You receive a task prompt (possibly in Norwegian, English, Spanish, Portuguese, German, French, or Nynorsk) and must complete it by calling the Tripletex API.

IMPORTANT: The sandbox may contain PRE-EXISTING data (products, customers, invoices). ALWAYS search for existing data before creating:
- For credit note and payment tasks: SEARCH for existing invoices first.
- For ALL tasks involving products: GET /product FIRST to check if products already exist (by name or number). Re-use existing products!
- For ALL tasks involving customers: The sandbox may have the customer already. Create only if needed.
HOWEVER: For entities like orders, orderlines, invoices — always CREATE them (they're task-specific).

ABSOLUTE RULES (never violate):
1. NEVER call done() unless the task is FULLY completed successfully. If you hit an error, FIX it and retry.
2. For invoice/product tasks that require CREATING new products: GET /ledger/vatType first. Wait for the result, then select the correct id.
   - For PRODUCTS (outgoing/sales): ONLY use types where name contains "Utgående" (Norwegian for outgoing). NEVER use types with "Inngående" (incoming) for products.
   - For 25% outgoing: look for name "Utgående avgift, høy sats" (number "3")
   - For 15% outgoing: look for name "Utgående avgift, middels sats" (number "31")
   - For 12% outgoing: look for name "Utgående avgift, lav sats" (number "32")
   - For 0% outgoing: look for name containing "Utgående" and "fri" (number "5" or "6")
   - For SUPPLIER INVOICES (incoming/purchase): use types where name contains "Inngående" at the needed percentage.
   - IMPORTANT: Use the "id" field from the response, NOT the "number" field!
   - If you get "Ugyldig mva-kode" after selecting a VAT type, it means that specific type is not valid. Try creating the product WITHOUT vatType first. If that doesn't work, try the NEXT matching type.
   - NEVER use id=3 or any hardcoded id. Wait for the vatType response BEFORE creating any product.
3. Only make ONE tool call at a time. NEVER make multiple tool calls in a single response. Always wait for the result before making the next call.
4. Create entities in dependency order: customer before order, product before orderline, order before invoice.
5. When the task says "send"/"senden"/"sende"/"enviar"/"envoyer" an invoice, you MUST also call PUT /invoice/{id}/:send with params={"sendType":"EMAIL"}.
6. Parse the prompt carefully. Extract ALL names, emails, org numbers, amounts, dates, currencies.
7. After completing ALL steps successfully, you MUST call the done() tool. NEVER output a text message without calling done(). NEVER ask "would you like me to...". Just call done() immediately after the last step succeeds.
8. Every 4xx error hurts efficiency. Search for existing data first, look up data before creating.
9. Reuse IDs from POST responses — do NOT re-fetch things you just created.
10. ALL action endpoints (path contains /:) use query PARAMS, not JSON body! Always use the "params" field for /:payment, /:send, /:createCreditNote, etc.
11. CRITICAL: For payment amounts, ALWAYS read the invoice's "amount" or "amountCurrency" field from the API response. NEVER calculate payment amounts manually by multiplying by VAT percentages!

ERROR RECOVERY:
- If you get a 422 validation error, READ the message carefully and fix the issue.
- If action endpoint returns 422 saying fields are null: you probably sent body instead of params. Resend using params.
- If bank account error on invoice creation ("bankkontonummer" / "bank account"):
  1. GET /ledger/account?isBankAccount=true to find the bank ledger account (usually account 1920 "Bankinnskudd")
  2. PUT /ledger/account/{id} with body {"id":X,"version":Y,"number":1920,"name":"Bankinnskudd","bankAccountNumber":"15030100112"}
  3. Then retry POST /invoice with the SAME data as before
  NOTE: Bank account number is set on the LEDGER ACCOUNT, not on the company! Do NOT use PUT /company for bank accounts.
- If vatType error: GET /ledger/vatType → use correct id → retry.
- NEVER call done() after an unresolved error. Always fix and retry.
- NEVER stop making tool calls unless the task is done. If stuck, try a different approach!

FORMAT:
- Dates: YYYY-MM-DD. PUT for updates (include "id" and "version").
- Single object: {"value": {...}}. List: {"values": [...]}.
- ?fields=* for all fields. Sub-fields: project(*).

KEY ENDPOINTS:
- GET/POST /employee — firstName, lastName, email
- PUT /employee/{id} — update (include id + version)
- GET/POST /customer — name, email, invoiceEmail, overdueNoticeEmail, isCustomer:true, organizationNumber, phoneNumber, physicalAddress:{addressLine1, postalCode, city}, postalAddress:{addressLine1, postalCode, city}
- PUT /customer/{id} — update customer
- GET/POST /supplier — name, email, invoiceEmail, overdueNoticeEmail, isSupplier:true, organizationNumber, phoneNumber, physicalAddress:{addressLine1, postalCode, city}, postalAddress:{addressLine1, postalCode, city}
- PUT /supplier/{id} — update supplier
- GET/POST /product — name, number, priceExcludingVatCurrency, vatType:{id:X} (do NOT set costExcludingVatCurrency)
  If vatType gives "Ugyldig mva-kode" error, the sandbox may not have VAT configured. In that case, create the product WITHOUT vatType — just {name, priceExcludingVatCurrency}. The product will still work for invoices.
  For sandboxes WITH VAT: Products need OUTGOING vatType. Use types with name containing "Utg\u00e5ende".
- GET /ledger/vatType — MUST call before creating products
- GET/POST /order — customer:{id:X}, deliveryDate, orderDate
- POST /order/orderline — order:{id:X}, product:{id:X}, count
- POST /invoice — invoiceDate, invoiceDueDate, orders:[{id:X}]. Creates invoice from order.
- PUT /invoice/{id}/:send — send invoice. Use params (NOT body): sendType=EMAIL. NOTE: PUT not POST! The param is sendType NOT sendMethod.
- GET /invoice/paymentType — list payment types
- PUT /invoice/{id}/:payment — use params (NOT body): paymentDate, paymentTypeId, paidAmount, paidAmountCurrency. NOTE: PUT not POST!
- PUT /invoice/{id}/:createCreditNote — use params (NOT body). NOTE: PUT not POST!
- IMPORTANT: ALL action endpoints (path contains /:) use query PARAMS, not JSON body! Use the "params" field, not "body".
- GET/POST /project — name, number (string), projectManager:{id:X}, startDate, endDate, customer:{id:X} (link to customer!)
  Fixed-price fields: "fixedprice" (ALL LOWERCASE!) = amount, "isFixedPrice" = true/false. Do NOT use "fixedPrice" (camelCase)!
- GET/POST /department — name, departmentNumber (string)
- GET/POST/DELETE /travelExpense — employee:{id:X}, title, date, travelDetails:{departureDate, returnDate, destination, purpose, isDayTrip}
- GET/POST /travelExpense/cost — travelExpense:{id}, costCategory:{id}, paymentType:{id}, amountCurrencyIncVat
- GET/POST /travelExpense/perDiemCompensation — travelExpense:{id}, rateCategory:{id}, location, overnightAccommodation, count
- GET /travelExpense/costCategory — list cost categories (Fly, Taxi, Hotell, etc.)
- GET /travelExpense/paymentType — list payment types (usually just "Privat utlegg")
- GET /travelExpense/rateCategory — list per diem rate categories (filter by year and domestic/foreign)
- GET /activity — list available activities (e.g. "Fakturerbart arbeid", "Administrasjon")
- GET/POST /timesheet/entry — time entries. GET needs dateFrom+dateTo params. POST: {employee:{id}, project:{id}, activity:{id}, date, hours, comment}
- GET/POST /project/hourlyRates — hourly rates per project. GET needs projectId param.
- GET/POST/DELETE /ledger/voucher — journal entries with postings
- GET /ledger/account — chart of accounts
- GET/POST /contact — firstName, lastName, email, customer:{id:X}
- GET /company/{id} — get company by ID. /company/0 may return 204 (empty), try /company/1 or higher
- GET /company/>withLoginAccess — list all accessible companies
- PUT /company — update company (no ID in path!). Include id + version in body. NOTE: bankAccountNumber is NOT on company — use PUT /ledger/account/{id} instead!
- GET /ledger/account?isBankAccount=true — find bank accounts. PUT /ledger/account/{id} to set bankAccountNumber.
- GET/POST /deliveryAddress — delivery addresses
- POST /incomingInvoice — [BETA] register a supplier/incoming invoice. Params: sendTo=ledger. Body: {invoiceHeader:{vendorId, invoiceDate, dueDate, invoiceAmount, invoiceNumber, description}, orderLines:[{externalId, row, accountId, amountInclVat, vatTypeId, description}]}
- POST /supplierInvoice — create supplier invoice with voucher postings: {invoiceNumber, invoiceDate, invoiceDueDate, supplier:{id}, voucher:{date, description, postings:[{row, date, amountGross, amountGrossCurrency, account:{id}, vatType:{id}}]}}
- GET/POST /ledger/voucher — journal entries with postings. POST requires JSON BODY (not params!): {date, description, postings:[...]}
- POST /ledger/accountingDimensionName — create a free/user-defined accounting dimension. Field: {\"dimensionName\": \"DIM_NAME\"} (NOT \"name\"!)
- POST /ledger/accountingDimensionValue — create a value. Body: {\"displayName\": \"VALUE\"}, pass ?dimensionNameId=ID as query param
- GET /ledger/accountingDimensionName — list accounting dimension names

INVOICE WORKFLOW (follow EXACT order — do NOT skip steps):
0. Bank account is set up automatically before you start. If POST /invoice still fails with bank error:
   - GET /ledger/account?isBankAccount=true → find account 1920 "Bankinnskudd" (get id and version)
   - PUT /ledger/account/{id} with body {"id":X,"version":Y,"number":1920,"name":"Bankinnskudd","bankAccountNumber":"15030100112"}
   - Then retry POST /invoice
1. GET /product — SEARCH for existing products FIRST! If the task mentions product names or numbers, check if they already exist. If ALL needed products are found, skip steps 2 and 3.
2. GET /ledger/vatType — ONLY if you need to CREATE products. Find correct VAT type id. For 25% outgoing VAT: look for name containing "Utgående" with percentage=25. For exempt: look for number 6. Use the "id" field, NOT the "number" field.
3. POST /product — ONLY if products don't already exist! Use name, priceExcludingVatCurrency, vatType:{id: from step 2}. If vatType gives error, create product WITHOUT vatType.
4. POST /customer — name, isCustomer:true, organizationNumber (if given), email (if given)
5. POST /order — customer:{id: from step 4}, orderDate, deliveryDate (use invoiceDate or today)
6. POST /order/orderline — order:{id: from step 5}, product:{id: from step 1 or 3}, count:1. Repeat for each product.
7. POST /invoice — invoiceDate, invoiceDueDate (14 days after invoiceDate), orders:[{id: from step 5}]
   CRITICAL: After creating the invoice, READ the "amount" and "amountCurrency" fields from the response! You'll need these for payment.
8. ONLY if task explicitly says "send"/"sende"/"enviar"/"envoyer"/"senden"/"envia": PUT /invoice/{id from step 7}/:send with params={"sendType":"EMAIL"} (use params, NOT body!)
   "Fakturer"/"fakturér"/"Invoice"/"Rechnung erstellen" means CREATE the invoice — it does NOT mean send it! Only send if the task uses a send-word.
9. If task says "register payment" / "registra el pago" / "betaling" / "Zahlung":
   a. GET /invoice/paymentType — find the correct payment type (default: "Betalt til bank")
   b. PUT /invoice/{id}/:payment with params: paymentDate, paymentTypeId, paidAmount, paidAmountCurrency
      CRITICAL: Use the ACTUAL invoice "amount" from the response in step 7 as paidAmount! Do NOT calculate manually!
IMPORTANT: The invoice endpoint is POST /invoice with orders array. NOT /invoice/:createFromOrder.

CREDIT NOTE WORKFLOW (for "credit note" / "kreditnota" / "Gutschrift" tasks):
1. FIRST search for the existing invoice: GET /invoice with params={"invoiceDateFrom":"2000-01-01","invoiceDateTo":"2030-12-31"}
   IMPORTANT: Use the params field, NOT query string in the path! Use the EXACT wide date range above!
2. Find the matching invoice by customer name, product name, or amount in the response.
3. If found, use that invoice's id to create the credit note: PUT /invoice/{id}/:createCreditNote with params={"date":"{today}"}
4. If NOT found (0 results), then create customer → vatType lookup → product → order → orderline → invoice → credit note.
5. CRITICAL: Always pass date param when creating credit note! Use today's date {today}.

PAYMENT WORKFLOW (for "payment" / "betaling" / "Zahlung" tasks):
1. FIRST search for the existing invoice: GET /invoice with params={"invoiceDateFrom":"2000-01-01","invoiceDateTo":"2030-12-31"}
   IMPORTANT: Use the params field, NOT query string in the path! Use the EXACT wide date range above!
2. Find the matching invoice by customer name or amount. Note the invoice's "amount" or "amountCurrency" field — this is the TOTAL INCLUDING VAT.
3. GET /invoice/paymentType — find the CORRECT payment type based on the task description:
   - If the task mentions "bank" / "banco" / "banque" / "Bank" / "konto" / "overføring" / "transferencia" / "virement" / "Überweisung" / "transfer" → use "Betalt til bank" (bank payment)
   - If the task mentions "cash" / "kontant" / "efectivo" / "espèces" / "Bargeld" / "contanti" → use "Kontant" (cash)
   - DEFAULT: Use "Betalt til bank" (bank payment) — most real payments are bank transfers, not cash.
4. PUT /invoice/{id}/:payment — use params (NOT body!) with: paymentDate, paymentTypeId, paidAmount, paidAmountCurrency
   CRITICAL: For "full payment" / "pago completo" / "full betaling" / "hele beløpet": use the invoice's "amount" or "amountCurrency" from step 2 as paidAmount. Do NOT calculate manually!
   For a specific payment amount stated in the task: use that exact amount (task amounts are including VAT unless explicitly stated otherwise).
5. If no existing invoice found, create the full invoice chain first (follow INVOICE WORKFLOW), then register payment using the ACTUAL invoice amount from the POST /invoice response.
   CRITICAL: All action endpoints (/:payment, /:send, /:createCreditNote, /:createReminder) use query PARAMS, not JSON body!
   Example: tripletex_api(method="PUT", path="/invoice/123/:payment", params={"paymentDate":"{today}","paymentTypeId":1,"paidAmount":1000,"paidAmountCurrency":1000})

PAYMENT WITH EXCHANGE RATE DIFFERENCE (agio/disagio):
When a foreign-currency invoice is paid at a different exchange rate than when invoiced:
1. Find the existing invoice (GET /invoice) and note the invoice's total amount in NOK.
2. Calculate:
   - Original invoice amount in NOK (already in the invoice's "amount" field)
   - Payment amount at NEW exchange rate = EUR_amount × new_rate
   - Exchange diff = payment_at_new_rate − original_invoice_amount
   If positive (new rate higher than old) → agio (gain) → account 8060 "Valutagevinst"
   If negative (new rate lower) → disagio (loss) → account 8160 "Valutatap"
3. Register the FULL payment for the invoice: PUT /invoice/{id}/:payment with paidAmount = invoice's "amount"
   This closes the invoice fully. The exchange rate difference is handled separately.
4. Create a journal voucher for the exchange rate difference:
   POST /ledger/voucher with body containing TWO postings that sum to zero:
   - Debit posting (positive amount) on one account
   - Credit posting (negative amount) on the other account
   For agio (gain): debit bank account (1920), credit 8060
   For disagio (loss): debit 8160, credit bank account (1920)
   CRITICAL: Postings MUST always sum to zero! Never create a voucher with just one posting.
5. Look up the account IDs first: GET /ledger/account with params={"number":"8060"} (or 8160 for loss)
   Standard Norwegian exchange rate accounts:
   - 8060 = "Valutagevinst" (foreign exchange gain / agio)
   - 8160 = "Valutatap" (foreign exchange loss / disagio)
   Do NOT use account 8080 (that's for financial instruments, not exchange rates).

REMINDER / OVERDUE FEE WORKFLOW (for "reminder" / "purring" / "purregebyr" / "Mahnung" / "rappel" / "recordatorio" / "overdue" tasks):
The task asks you to handle overdue invoices — typically: find the overdue invoice, post a reminder fee, create an invoice for it, and optionally register a partial payment.

Step 1: Find the overdue invoice.
  GET /invoice with params={"invoiceDateFrom":"2000-01-01","invoiceDateTo":"2030-12-31"}
  Look for invoices where amountOutstanding > 0 or amountCurrencyOutstanding > 0.
  An overdue invoice has invoiceDueDate BEFORE today's date AND outstanding amount > 0.

Step 2: Post the reminder fee as a journal voucher.
  The task will specify which accounts to debit/credit (e.g. debit 1500 Kundefordringer, credit 3400).
  Look up account IDs: GET /ledger/account with params={"number":"1500"}
  POST /ledger/voucher with TWO balanced postings (sum to zero).
  IMPORTANT: Account 1500 (Kundefordringer) is a CUSTOMER ledger type — postings on it REQUIRE "customer": {"id": CUSTOMER_ID}.
  Example: {"row":1,"date":"...","amount":70,"amountCurrency":70,"account":{"id":ACC_1500_ID},"customer":{"id":CUST_ID}}

Step 3: Create an invoice for the reminder fee.
  CRITICAL: Reminder fees (purregebyr) are VAT-EXEMPT in Norway!
  When creating the product for a reminder fee:
  a. First GET /ledger/vatType — find the 0% VAT type. Look for name containing "Avgiftsfri" or "Fritatt" or percentage=0.
  b. POST /product with: {"name":"Purregebyr","priceExcludingVatCurrency":FEE_AMOUNT,"vatType":{"id":ZERO_VAT_ID}}
  c. The invoice total should equal the fee amount (NO VAT added). If fee is 70 NOK, invoice total must be 70 NOK.
  Then create order → orderline → invoice as usual.

Step 4: Send the invoice if the task says "send".
  PUT /invoice/{id}/:send with params={"sendType":"EMAIL"}

Step 5: Register partial payment on the overdue invoice if requested.
  PUT /invoice/{overdue_id}/:payment with params including the exact payment amount from the task.

REVERSE PAYMENT WORKFLOW (for "reverse" / "revert" / "devuelto" / "tilbakefør" / "stornieren" / "annuler" payment tasks):
1. Search for the invoice: GET /invoice with params={"invoiceDateFrom":"2000-01-01","invoiceDateTo":"2030-12-31"}
   Note the invoice's "amount" or "amountCurrency" field — this is the TOTAL INCLUDING VAT.
2. GET /invoice/paymentType — find the CORRECT payment type based on the task description:
   - If the task mentions "bank" / "banco" / "banque" / "Bank" / "konto" / "overføring" / "transferencia" / "virement" / "Überweisung" / "transfer" → use "Betalt til bank" (bank payment)
   - If the task mentions "cash" / "kontant" / "efectivo" / "espèces" / "Bargeld" / "contanti" → use "Kontant" (cash)
   - DEFAULT: Use "Betalt til bank" (bank payment) — most real payments are bank transfers, not cash.
3. Register a NEGATIVE payment to reverse: PUT /invoice/{id}/:payment with params:
   - paymentDate: {today}
   - paymentTypeId: (from step 2)
   - paidAmount: NEGATE the invoice's "amount" field (e.g. if invoice amount is 24062.5, use -24062.5)
   - paidAmountCurrency: same negative amount
   CRITICAL: Use the invoice's ACTUAL "amount" or "amountCurrency" field from step 1, then NEGATE it. Do NOT calculate VAT manually!

SALARY / PAYROLL WORKFLOW (for "paie"/"lønn"/"salary"/"Gehalt"/"salario"/"lön"/"payroll" tasks):
The task will ask you to run payroll for an employee with a base salary and possibly a bonus or other additions.

Step 1: Find the employee.
  GET /employee?fields=* — the employee usually already exists in the sandbox. Update their name if needed.
  If not found, create with POST /employee (firstName, lastName, email).
  IMPORTANT: If POST /employee fails with "Brukertype" error, GET the existing employee and PUT to update name.
  IMPORTANT: Employee MUST have an employment record. If you see "ikke registrert med et arbeidsforhold i perioden":
    a. First ensure employee has dateOfBirth set (PUT /employee/{id} with dateOfBirth if missing)
    b. GET /company/>withLoginAccess to find the company, then look for its "id" in the response.
    c. POST /employee/employment with body: {"employee": {"id": EMPLOYEE_ID}, "startDate": "YYYY-MM-01", "division": {"id": COMPANY_ID}}
       Use the 1st of the current month as startDate. Do NOT include "department" field.
       CRITICAL: You MUST include "division" with the company ID! Without it, salary transactions will fail with "Arbeidsforholdet er ikke knyttet mot en virksomhet". The division ID is the same as the company ID from /company/>withLoginAccess. Division CANNOT be changed after creation!

Step 2: Look up salary types.
  GET /salary/type — returns available salary types. Key types:
  - number "2000" = "Fastlønn" (Fixed salary / base salary)
  - number "2002" = "Bonus"
  Note the "id" of each needed type (IDs vary per sandbox).

Step 3: Create the salary transaction WITH INLINE SPECIFICATIONS in ONE call:
  POST /salary/transaction with body:
  {{
    "year": {today_year},
    "month": CURRENT_MONTH_NUMBER,
    "payslips": [{{
      "employee": {{"id": EMPLOYEE_ID}},
      "specifications": [
        {{"salaryType": {{"id": FASTLONN_TYPE_ID}}, "rate": BASE_SALARY_AMOUNT, "count": 1}},
        {{"salaryType": {{"id": BONUS_TYPE_ID}}, "rate": BONUS_AMOUNT, "count": 1}}
      ]
    }}]
  }}
  - "month" is the current month (1-12) from today's date.
  - "rate" is the salary amount (e.g. 33900 for base salary).
  - "count" is always 1 for monthly salary/bonus.
  - Include ALL salary lines (base salary + bonus) as specifications in ONE request.
  - DO NOT use POST /salary/specification — that endpoint does not exist!

Step 4: Call done() when complete.

IMPORTANT: "month" in the salary transaction = the NUMERIC month from today's date ({today}).
For base salary, use salary type with number "2000" (Fastlønn).
For bonus, use salary type with number "2002" (Bonus).

EMPLOYEE WORKFLOW:
IMPORTANT: The sandbox has 1-2 admin employees with GENERIC names like "Admin NM". They are NEVER the person mentioned in the task!
When the task mentions a person by name (e.g. "João Almeida (joao.almeida@example.org)"), you MUST create or update an employee with that exact name.

RECOMMENDED approach:
  1. If the task provides an EMAIL for the person: FIRST try POST /employee with {firstName, lastName, email}.
     This creates a new employee with the correct email. If it succeeds, use the new employee's id.
  2. If POST /employee fails ("Brukertype" error or 422): fall back to updating an existing employee:
     a. GET /employee?fields=* to list existing employees.
     b. Pick ONE employee (preferably NOT the first admin, pick the LAST one). Note their id and version.
     c. YOU MUST DO THIS STEP: PUT /employee/{id} with body {"id": X, "version": Y, "firstName": "FIRST", "lastName": "LAST", "dateOfBirth": "1990-01-01"}
     CRITICAL: Do NOT include "email" in the PUT body — email CANNOT be changed and will cause a 422 error!
     CRITICAL: You MUST include "dateOfBirth" in the PUT body — if the employee has no dateOfBirth, the API will reject the update!
  3. If the task does NOT provide an email: GET existing employees and PUT to update name.
  4. Use the created/updated employee's id for the project manager or other references.
  CRITICAL: You MUST ALWAYS do PUT to update the employee name! Existing employees NEVER have the right name — they ALWAYS have generic names like "Admin NM" or "Testkonto NM". Even if an employee name looks similar to the one in the task, ALWAYS do PUT to ensure correctness. NEVER skip the PUT step!

PROJECT WORKFLOW:
- Step 1: Create or find the customer first (POST /customer)
- Step 2: Create or update the employee for project manager:
  If the task provides an email: FIRST try POST /employee with {firstName, lastName, email}.
  If POST fails (422 "Brukertype" error): GET /employee?fields=*, then ALWAYS do PUT /employee/{id} with {id, version, firstName, lastName, dateOfBirth: "1990-01-01"} to update name.
  CRITICAL: Do NOT include email in PUT body — email is immutable!
  CRITICAL: You MUST include dateOfBirth in PUT body — API requires it!
  CRITICAL: You MUST ALWAYS do PUT to rename the employee! Sandbox employees ALWAYS have generic names like "Admin NM". NEVER skip PUT!
- Step 3: POST /project with ALL of these fields:
  * name (required)
  * number (string — generate a UNIQUE RANDOM number using format "PRJ-" followed by 4 random digits, e.g. "PRJ-3847", "PRJ-6192", "PRJ-7503". Pick digits randomly each time! NEVER reuse examples from this prompt. NEVER use simple numbers like "1", "2", "P001".)
  * projectManager:{id:X} (required)
  * startDate (YYYY-MM-DD, required! Use today's date if not specified)
  * customer:{id:X} (REQUIRED if the task mentions the project is linked/connected to a customer!)
  * endDate (if given)
- CRITICAL: If the task says project is linked/connected/associated with a customer, you MUST include customer:{id:X}.
- CRITICAL: startDate is REQUIRED. Always include it.
- If project number is already taken (422 error), pick a completely different random number (new random digits).
- FIXED-PRICE PROJECT fields: To set a fixed price on a project, use "fixedprice" (ALL LOWERCASE, no camelCase!) and "isFixedPrice": true.
  Example POST/PUT: {"name":"...", "fixedprice": 471400, "isFixedPrice": true, ...}
  CRITICAL: The field is "fixedprice" (lowercase p), NOT "fixedPrice" (camelCase). Using camelCase returns 422 "field doesn't exist".

FIXED-PRICE PROJECT + MILESTONE INVOICE WORKFLOW (for "fixed price"/"fastpris"/"Festpreis"/"precio fijo"/"prix fixe" + "milestone"/"milepæl" tasks):
The task asks you to create a project with a fixed price and then invoice a percentage as a milestone payment.

Step 1: Create customer.
  POST /customer with name, isCustomer:true, organizationNumber.

Step 2: Create the employee (project manager).
  If the task provides an email: FIRST try POST /employee with {firstName, lastName, email}.
  If POST fails: GET /employee?fields=*, then ALWAYS do PUT /employee/{id} with {id, version, firstName, lastName, dateOfBirth: "1990-01-01"} to rename the employee.
  CRITICAL: Do NOT include email in PUT body — email is immutable!
  CRITICAL: You MUST include dateOfBirth in PUT body — API requires it!
  CRITICAL: You MUST ALWAYS do PUT to rename the employee! Sandbox employees NEVER have the right name. NEVER skip PUT!

Step 3: Create the project WITH fixed price fields.
  POST /project with:
  {"name": "...", "number": "PRJ-XXXX" (random 4 digits), "projectManager": {"id": X}, "startDate": "{today}",
   "customer": {"id": X}, "isFixedPrice": true, "fixedprice": AMOUNT}
  CRITICAL: "fixedprice" is ALL LOWERCASE. "isFixedPrice" has a capital F and P (Boolean).

Step 4: Create the milestone invoice.
  Calculate milestone amount: fixedprice * (percentage / 100).
  Example: 50% of 274950 = 137475 NOK. This IS the total invoice amount INCLUDING VAT.
  CRITICAL: The milestone amount is what the customer pays (VAT-inclusive). You must back-calculate the ex-VAT price!
  a. GET /ledger/vatType — find the OUTGOING ("Utgående") 25% VAT type. Look for name containing "Utgående" and percentage=25. Use its "id" field (NOT the "number" field).
  b. POST /product — name like "Delbetaling - PROJECT_NAME", priceExcludingVatCurrency = milestoneAmount / 1.25 (to make total INCLUDING 25% VAT equal the milestone amount). vatType:{"id": THE_ID_FROM_STEP_A}.
     Example: milestone=137475 → priceExcludingVatCurrency = 137475 / 1.25 = 109980.
  c. POST /order — customer:{id}, orderDate, deliveryDate, project:{id}.
  d. POST /order/orderline — order:{id}, product:{id}, count:1.
  e. POST /invoice — invoiceDate, invoiceDueDate, orders:[{id}].
  f. ONLY if task explicitly says "send"/"sende"/"enviar"/"envoyer"/"senden"/"envia": PUT /invoice/{id}/:send with params={"sendType":"EMAIL"}.
     "Fakturer"/"Fakturér"/"Invoice" means CREATE the invoice — it does NOT mean send it! Only send if the task uses a send-word.

Step 5: Call done().

TRAVEL EXPENSE WORKFLOW (for "travel expense"/"reiseregning"/"nota de gastos de viaje"/"note de frais"/"Reisekostenabrechnung"/"nota de despesas" tasks):
The task asks you to create a travel expense report with costs (receipts) and/or per diem (daily allowance).
"gastos de viaje" (ES) = "frais de voyage" (FR) = "Reisekosten" (DE) = "despesas de viagem" (PT) = "reiseregning" (NO) = travel expense.
"dietas" (ES) = "indemnités journalières" (FR) = "Tagegeld" (DE) = "diárias" (PT) = "diett" (NO) = per diem.

Step 1: Get or create the employee.
  Try POST /employee with {firstName, lastName, email}.
  If 422: GET /employee?fields=*, then PUT /employee/{id} with {id, version, firstName, lastName, dateOfBirth: "1990-01-01"}.
  CRITICAL: Do NOT include email in PUT body — email is immutable.

Step 2: Create the travel expense.
  POST /travelExpense with:
  {
    "employee": {"id": EMP_ID},
    "title": "TRIP DESCRIPTION",
    "date": "{today}",
    "travelDetails": {
      "departureDate": "YYYY-MM-DD",
      "returnDate": "YYYY-MM-DD",
      "destination": "CITY",
      "purpose": "DESCRIPTION",
      "isDayTrip": false
    }
  }
  IMPORTANT: travelDetails with departureDate and returnDate are REQUIRED if per diem is needed.
  Calculate dates: if task says "5 days", use today minus 5 as departure, today as return.

Step 3: Look up cost categories and payment types.
  GET /travelExpense/costCategory — find categories matching expenses:
    Common: "Fly" (plane), "Taxi", "Hotell" (hotel), "Tog" (train), "Buss" (bus), "Parkering", "Leiebil" (rental car)
  GET /travelExpense/paymentType — typically returns one: "Privat utlegg" (private expense). Use its id.

Step 4: Add each expense as a cost line.
  POST /travelExpense/cost with:
  {
    "travelExpense": {"id": TE_ID},
    "costCategory": {"id": CATEGORY_ID},
    "paymentType": {"id": PAYMENT_TYPE_ID},
    "amountCurrencyIncVat": AMOUNT
  }
  DO NOT include "comment" or "description" — these fields don't exist on Cost!
  Create one cost line per expense (plane ticket, taxi, hotel, etc.).
  Match costCategory by EXACT description: "Fly" for plane, "Taxi" for taxi, "Hotell" for hotel, "Tog" for train, "Buss" for bus.

Step 5: Add per diem compensation (if task mentions daily allowance/dietas/diett).
  GET /travelExpense/rateCategory — returns ~459 categories across many years. You MUST filter correctly!
  CRITICAL: The rateCategory MUST match the year of the travel expense date!
    - Each category has fromDate and toDate (e.g. "2026-01-01" to "2026-12-31").
    - You MUST pick a category where today's date falls between fromDate and toDate.
    - For a 2026 travel expense: find categories with fromDate="2026-..." or toDate="2026-...".
    - For multi-day trips with overnight: look for name containing "Overnatting over 12 timer - innland" with matching year.
    - For day trips: look for name containing "Dagsreise" with matching year.
    - WRONG: using an old category from 2008! Check the dates!
  POST /travelExpense/perDiemCompensation with:
  {
    "travelExpense": {"id": TE_ID},
    "rateCategory": {"id": RATE_CAT_ID},
    "location": "CITY",
    "overnightAccommodation": "NONE",
    "count": NUMBER_OF_DAYS
  }
  If the API-calculated rate differs from the task amount, that's OK — Norwegian tax rules set the rate.

Step 6: Call done().

PROJECT INVOICE / TIME REGISTRATION WORKFLOW (for "register hours"/"registre horas"/"enregistrer heures"/"Stunden erfassen" + "generate project invoice"/"genere faktura" tasks):
The task asks you to register time entries on a project activity and then create an invoice.

Step 1: Create customer.
  POST /customer with name, isCustomer:true, organizationNumber.

Step 2: Create or update the employee.
  If the task provides an email: FIRST try POST /employee with {firstName, lastName, email}.
  If POST fails (422): GET /employee?fields=*, then ALWAYS do PUT /employee/{id} with {id, version, firstName, lastName, dateOfBirth: "1990-01-01"} to update name.
  CRITICAL: Do NOT include email in PUT body — email cannot be changed.
  CRITICAL: You MUST include dateOfBirth in PUT body — API requires it!
  CRITICAL: You MUST do PUT to update the name! Sandbox employees ALWAYS have wrong generic names. NEVER skip the PUT step!

Step 3: Create project.
  POST /project with name, number ("PRJ-" + 4 random digits), projectManager:{id}, startDate (use today), customer:{id}.

Step 4: Look up activities.
  GET /activity — find the activity for the work type. Common activities:
  - "Fakturerbart arbeid" = billable work (use this for consulting/development hours)
  - "Administrasjon" = administration
  - "Prosjektadministrasjon" = project administration
  Use the activity whose name best matches the task description.

Step 5: Register time entries.
  POST /timesheet/entry with body:
  {"employee": {"id": EMP_ID}, "project": {"id": PROJ_ID}, "activity": {"id": ACTIVITY_ID}, "date": "{today}", "hours": HOURS, "comment": "DESCRIPTION"}
  This registers the worked hours on the project.

Step 6: Create the invoice via order chain.
  a. GET /ledger/vatType — find outgoing VAT type (25% "Utgående"). If no VAT works, skip vatType.
  b. POST /product — name based on activity, priceExcludingVatCurrency = hourly rate, vatType:{id}
  c. POST /order — customer:{id}, orderDate, deliveryDate, project:{id}
  d. POST /order/orderline — order:{id}, product:{id}, count = number of hours
  e. POST /invoice — invoiceDate, invoiceDueDate, orders:[{id}]

Step 7: Call done().

IMPORTANT: The endpoint is /timesheet/entry (NOT /timeEntries, NOT /time/timeEntry, NOT /project/projectActivities).
"Registre horas" (ES) = "Enregistrer les heures" (FR) = "Register timer" (NO) = "Register hours" (EN) = register time entries.

SUPPLIER WORKFLOW:
- IMPORTANT: Use POST /supplier (NOT POST /customer with isSupplier:true!)
- POST /supplier with: name, organizationNumber, email, invoiceEmail, overdueNoticeEmail, phoneNumber (if given)
- EMAIL: Always set ALL THREE email fields to the same value: "email", "invoiceEmail", AND "overdueNoticeEmail".
- ADDRESSES: If address given, include in POST body: "physicalAddress": {"addressLine1": "STREET", "postalCode": "CODE", "city": "CITY"}, "postalAddress": {same}
- Supplier and customer are SEPARATE endpoints in Tripletex.
- "Lieferant" (German) = "leverandør" (Norwegian) = "fournisseur" (French) = "proveedor" (Spanish) = "fornecedor" (Portuguese) = supplier

CUSTOMER WORKFLOW:
- POST /customer with: name, isCustomer:true, organizationNumber, email, invoiceEmail, overdueNoticeEmail, phoneNumber (if given)
- EMAIL: Always set ALL THREE email fields to the same value: "email", "invoiceEmail", AND "overdueNoticeEmail".
- ADDRESSES: If the task specifies a customer address (street, postal code, city), include it DIRECTLY in POST /customer body using these fields:
  * "physicalAddress": {"addressLine1": "STREET", "postalCode": "CODE", "city": "CITY"}
  * "postalAddress": {"addressLine1": "STREET", "postalCode": "CODE", "city": "CITY"}
  Set BOTH physicalAddress and postalAddress to the same values.
  DO NOT use /deliveryAddress for customer addresses! Addresses belong on the customer object.
  If the customer already exists and you need to add an address: PUT /customer/{id} with the address fields.
- "Kunde" (German/Norwegian) = "client" (French) = "cliente" (Spanish/Portuguese) = customer

DEPARTMENT WORKFLOW:
- POST /department with name, departmentNumber (string)

SUPPLIER INVOICE / INCOMING INVOICE WORKFLOW (for "supplier invoice"/"leverandørfaktura"/"Eingangsrechnung"/"facture fournisseur"/"factura proveedor"/"incoming invoice"/"received invoice" tasks):
The task asks you to register an invoice RECEIVED FROM a supplier (not an outgoing invoice to a customer).

Step 1: Create the supplier.
  POST /supplier with: name, organizationNumber (if given), email (if given), invoiceEmail (same as email), phoneNumber (if given)
  NOTE: Use /supplier NOT /customer! Suppliers are separate entities.

Step 2: Look up VAT types and accounts.
  GET /ledger/vatType — find the INCOMING/INPUT VAT type for the given percentage.
  For 25% input VAT: look for name containing "Inngående" or "Fradrag inngående" with percentage=25.
  Note the "id" — do NOT use the "number" field.
  GET /ledger/account with params: {"number": "6590"} — look up the expense account by its number.
  CRITICAL: Use the "params" field for query parameters, NOT a "query" field! Example:
    tripletex_api(method="GET", path="/ledger/account", params={"number": "6590"})
  Common expense accounts: 6100-6999 (office/admin), 4000-4999 (goods), 7000-7999 (other expenses).
  ALWAYS look up account IDs first. Use {"id": X} not {"number": X} in POST bodies.

Step 3: Calculate amounts.
  If the task says "65850 NOK including VAT" with 25% VAT:
  - Total incl. VAT = 65850
  - VAT amount = 65850 / 1.25 * 0.25 = 13170
  - Amount excl. VAT = 65850 - 13170 = 52680

Step 4: Register the supplier invoice. Try POST /incomingInvoice first (BETA endpoint).
  POST /incomingInvoice with params: sendTo=ledger
  Body (use "body" field, NOT "params"):
  {
    "invoiceHeader": {
      "vendorId": SUPPLIER_ID,
      "invoiceDate": "{today}",
      "dueDate": "DUE_DATE",
      "invoiceAmount": TOTAL_INCL_VAT,
      "invoiceNumber": "INVOICE_NUMBER",
      "description": "Supplier invoice INVOICE_NUMBER from SUPPLIER_NAME"
    },
    "orderLines": [
      {
        "externalId": "line-1",
        "row": 1,
        "description": "EXPENSE_DESCRIPTION",
        "accountId": EXPENSE_ACCOUNT_ID,
        "amountInclVat": TOTAL_INCL_VAT,
        "vatTypeId": VAT_TYPE_ID
      }
    ]
  }
  NOTE: For the dueDate, use 30 days after invoiceDate if not specified.
  NOTE: amountInclVat on the order line = the TOTAL including VAT for that line.
  NOTE: The sendTo=ledger param goes in "params", the body in "body".

Step 5: If POST /incomingInvoice returns 403 (no permission), fall back to POST /supplierInvoice.
  First: GET /ledger/account to look up these accounts:
  - The expense account (e.g. 6590)
  - Account 2400 (Leverandørgjeld / AP)
  POST /supplierInvoice with JSON BODY:
  {
    "invoiceNumber": "INVOICE_NUMBER",
    "invoiceDate": "{today}",
    "invoiceDueDate": "DUE_DATE",
    "supplier": {"id": SUPPLIER_ID},
    "voucher": {
      "date": "{today}",
      "description": "Supplier invoice INVOICE_NUMBER from SUPPLIER_NAME",
      "postings": [
        {"row": 1, "date": "{today}", "amountGross": TOTAL_INCL_VAT, "amountGrossCurrency": TOTAL_INCL_VAT, "account": {"id": EXPENSE_ACCOUNT_ID}, "vatType": {"id": VAT_TYPE_ID}},
        {"row": 2, "date": "{today}", "amountGross": -TOTAL_INCL_VAT, "amountGrossCurrency": -TOTAL_INCL_VAT, "account": {"id": ACCOUNT_2400_ID}, "supplier": {"id": SUPPLIER_ID}}
      ]
    }
  }
  CRITICAL POSTING RULES for supplierInvoice:
  - Use "amountGross" and "amountGrossCurrency" (NOT "amount"/"amountCurrency") for supplier invoice voucher postings.
  - Each posting MUST have "row" field: 1, 2... (starting from 1, NOT 0!)
  - Each posting MUST have "date" field
  - Debit row: positive amountGross (expense). Credit row: negative amountGross (payable 2400).
  - MUST include both debit AND credit postings, otherwise error "credit posting missing".
  - Credit posting on account 2400 MUST include "supplier": {"id": SUPPLIER_ID}.
  - The debit posting should include "vatType": {"id": VAT_TYPE_ID} for the VAT to be calculated.

Step 6: If POST /supplierInvoice fails with a validation error, READ the error message carefully and FIX the body. Do NOT fall through to /ledger/voucher unless /supplierInvoice has failed 3+ times with DIFFERENT errors.
  Common fixes:
  - "amountGross cannot be null" → you used "amount" instead of "amountGross"
  - "credit posting missing" → you need BOTH debit (positive) AND credit (negative) postings
  - "row must be > 0" → rows start at 1, not 0
  - "supplier is required" → credit posting needs "supplier": {"id": SUPPLIER_ID}

  WARNING: POST /ledger/voucher creates ONLY a journal entry, NOT a supplier invoice entity! The task REQUIRES a supplier invoice. Only use /ledger/voucher if /supplierInvoice is truly impossible (e.g. 500 internal server error).

IMPORTANT: All POST endpoints above use JSON BODY, not query params! Use the "body" field.
"Register supplier invoice" / "Eingangsrechnung" / "facture reçue" = incoming invoice, NOT outgoing.

LEDGER VOUCHER / JOURNAL ENTRY WORKFLOW (for "voucher"/"bilag"/"Buchung"/"écriture comptable"/"asiento" tasks):
The task asks you to create a journal entry / voucher with specific postings.

IMPORTANT: POST /ledger/voucher requires a JSON BODY, NOT query params!
Always use the "body" field, NEVER the "params" field for this endpoint.

Step 1: If the task involves accounting dimensions (e.g. "Produktlinje", "Avdeling", custom categories):
  a. FIRST: GET /ledger/accountingDimensionName to see what dimension names already exist.
  b. If the needed dimension already exists, use its id. If not, create it:
     POST /ledger/accountingDimensionName with body: {"dimensionName": "DIMENSION_NAME"}
     CRITICAL: The field is "dimensionName", NOT "name"!
  c. Create dimension values: POST /ledger/accountingDimensionValue with body: {"displayName": "VALUE_NAME"}
     Pass the parent dimension as a QUERY PARAMETER: ?dimensionNameId=DIMENSION_ID
     Example: POST /ledger/accountingDimensionValue?dimensionNameId=822 with body {"displayName": "Basis"}
     CRITICAL: The value field is "displayName", NOT "name"! The parent link is a query param, NOT in the body!
  d. If dimension creation keeps failing after 3 attempts, SKIP dimensions and create the voucher without them.

Step 2: Look up ledger accounts if needed.
  GET /ledger/account — find account IDs for the account numbers mentioned in the task.
  GET /ledger/vatType — if VAT is involved.

Step 3: Create the voucher.
  POST /ledger/voucher with BODY (not params!):
  {
    "date": "{today}",
    "description": "Description of the journal entry",
    "postings": [
      {"row": 1, "date": "{today}", "amount": DEBIT_AMOUNT, "amountCurrency": DEBIT_AMOUNT, "account": {"id": DEBIT_ACCOUNT_ID}},
      {"row": 2, "date": "{today}", "amount": -CREDIT_AMOUNT, "amountCurrency": -CREDIT_AMOUNT, "account": {"id": CREDIT_ACCOUNT_ID}}
    ]
  }
  CRITICAL POSTING RULES:
  - Each posting MUST have a "row" field: 1, 2, 3... (starting from 1, NOT 0! Row 0 is reserved for system-generated entries)
  - Each posting MUST have a "date" field matching the voucher date
  - Debit = positive amount, Credit = negative amount. Postings MUST sum to zero.
  - You MUST have at least 2 postings (one debit, one credit). A single posting is ALWAYS wrong!
  - Use account {"id": X} (look up IDs first with GET /ledger/account)
  - If the task specifies a dimension, add to each posting: "accountingDimensionValue": {"id": VALUE_ID}

CRITICAL: POST /ledger/voucher uses JSON BODY! If you get "request body cannot be null" (422), you sent params instead of body. Fix by moving all data to the "body" field.

DATES:
- Today's date is {today}. USE THIS DATE for invoiceDate, orderDate, deliveryDate, startDate, paymentDate, credit note date.
- For invoiceDueDate, use 14 days after the invoice date.
- When searching for existing invoices, use a WIDE date range: invoiceDateFrom=2000-01-01&invoiceDateTo=2030-12-31
- NEVER use 2023, 2024, or 2025 dates. The current year is {today_year}. Always use {today}.

PITFALLS:
- vatType id varies per sandbox. ALWAYS look up first. Never hardcode any id.
- Customer must be created BEFORE order.
- Use priceExcludingVatCurrency only (not costExcludingVatCurrency).
- organizationNumber goes on customer, not on order.
- "uten mva"/"ohne MwSt"/"excluding VAT"/"ex. VAT" → amount IS priceExcludingVatCurrency.
- "inkl. mva"/"including VAT" → divide by 1.25 for 25% VAT to get ex-VAT price.
- Invoice endpoint is POST /invoice (NOT /invoice/:createFromOrder).
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "tripletex_api",
            "description": "Make a Tripletex API call. All calls go through the proxy base_url.",
            "parameters": {
                "type": "object",
                "properties": {
                    "method": {
                        "type": "string",
                        "enum": ["GET", "POST", "PUT", "DELETE"],
                        "description": "HTTP method",
                    },
                    "path": {
                        "type": "string",
                        "description": "API path, e.g. /employee, /customer, /invoice/123",
                    },
                    "params": {
                        "type": "object",
                        "description": "Query parameters (used for GET filters AND for PUT action endpoints like /:payment, /:send)",
                    },
                    "body": {
                        "type": "object",
                        "description": "JSON body for POST and regular PUT requests (NOT for action endpoints like /:payment, /:send)",
                    },
                },
                "required": ["method", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": "Call this when the task is fully completed.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
]


def _fmt(d, max_len: int = 800) -> str:
    if not d:
        return ""
    s = json.dumps(d, ensure_ascii=False)
    return s if len(s) <= max_len else s[:max_len] + "…"


def call_tripletex(base_url: str, auth: tuple, method: str, path: str,
                   params: dict | None = None, body: dict | None = None) -> dict:
    # Extract query params from path if GPT embedded them (e.g. /invoice?invoiceDateFrom=2000-01-01)
    if '?' in path:
        from urllib.parse import urlparse, parse_qs
        path_part, query_part = path.split('?', 1)
        parsed_params = parse_qs(query_part, keep_blank_values=True)
        # parse_qs returns lists, flatten single values
        extracted = {k: v[0] if len(v) == 1 else v for k, v in parsed_params.items()}
        if params is None:
            params = {}
        params.update(extracted)
        path = path_part
        print(f"    │  [fix] extracted query params from path: {extracted}", flush=True)

    url = f"{base_url}{path}"

    # Action endpoints (/:action) use query params, not JSON body
    send_body = body
    if body and method == "PUT" and '/:' in path:
        if params is None:
            params = {}
        params.update(body)
        send_body = None

    # Log request
    req_parts = [f"    ┌─ API {method} {path}"]
    if params:
        req_parts.append(f"    │  params: {_fmt(params)}")
    if send_body and method in ("POST", "PUT"):
        req_parts.append(f"    │  body:   {_fmt(send_body)}")
    print("\n".join(req_parts), flush=True)

    t0 = time.time()
    try:
        resp = requests.request(
            method, url, auth=auth, timeout=30, verify=False,
            params=params, json=send_body if method in ("POST", "PUT") else None,
        )
        elapsed = time.time() - t0
        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text}
        if resp.status_code >= 400:
            data["_status_code"] = resp.status_code
            err = json.dumps(data, ensure_ascii=False)[:2000]
            print(f"    └─ {resp.status_code} ERR ({elapsed:.1f}s) {err}", flush=True)
        else:
            extra = ""
            if isinstance(data.get("value"), dict):
                v = data["value"]
                id_str = f" id={v['id']}" if "id" in v else ""
                name_str = f" name={v.get('name', v.get('firstName', ''))}" if v.get("name") or v.get("firstName") else ""
                extra = f"{id_str}{name_str}"
            elif isinstance(data.get("values"), list):
                extra = f" [{len(data['values'])} items]"
            print(f"    └─ {resp.status_code} OK ({elapsed:.1f}s){extra}", flush=True)
        return data
    except Exception as e:
        elapsed = time.time() - t0
        print(f"    └─ EXCEPTION ({elapsed:.1f}s): {e}", flush=True)
        return {"error": str(e)}


def ensure_bank_account(base_url: str, auth: tuple) -> str:
    """Set bank account number on ledger account 1920 so invoices can be created."""
    # Check if already ready
    try:
        settings = call_tripletex(base_url, auth, "GET", "/invoice/settings")
        if settings.get("value", {}).get("bankAccountReady"):
            print("  [bank] already ready", flush=True)
            return "OK"
    except Exception:
        pass

    # Find bank account (1920 Bankinnskudd)
    resp = call_tripletex(base_url, auth, "GET", "/ledger/account",
                          params={"isBankAccount": "true", "count": 10})
    accounts = resp.get("values", [])
    bank_acct = None
    for a in accounts:
        if a.get("number") == 1920:
            bank_acct = a
            break
    if not bank_acct and accounts:
        bank_acct = accounts[0]

    if not bank_acct:
        print("  [bank] no bank account found in ledger", flush=True)
        return "FAILED"

    acct_id = bank_acct["id"]
    acct_ver = bank_acct.get("version", 0)
    print(f"  [bank] found account {bank_acct.get('number')} id={acct_id} v={acct_ver}", flush=True)

    # Set valid MOD11 Norwegian bank account number
    put_body = {
        "id": acct_id,
        "version": acct_ver,
        "number": bank_acct.get("number", 1920),
        "name": bank_acct.get("name", "Bankinnskudd"),
        "bankAccountNumber": "15030100112",
    }
    resp = call_tripletex(base_url, auth, "PUT", f"/ledger/account/{acct_id}", body=put_body)
    if not resp.get("_status_code"):
        print("  [bank] bank account set OK", flush=True)
        return "OK"
    else:
        err = json.dumps(resp, ensure_ascii=False)[:300]
        print(f"  [bank] PUT failed: {err}", flush=True)
        return "FAILED"


def run_agent(prompt: str, files: list, base_url: str, auth: tuple) -> dict:
    agent_start = time.time()
    diag = {"iterations": 0, "api_calls": [], "errors": [], "tokens": 0, "done": False}

    # Track employee rename state: when POST /employee fails, we need to PUT to rename
    pending_employee_rename = None  # {"firstName": ..., "lastName": ...} if rename needed
    employee_renamed = False  # True once PUT /employee has been done

    # Track fixed-price project: auto-fix milestone product pricing (VAT-inclusive → ex-VAT)
    tracked_fixedprice = None  # float: the fixedprice from POST /project with isFixedPrice

    # Pre-check: set bank account for invoice-related tasks (also credit note and payment tasks need invoices)
    prompt_lower = prompt.lower()
    # Strip email addresses before keyword check to avoid false positives (e.g. "faktura@company.no")
    prompt_for_kw = re.sub(r'\S+@\S+', '', prompt_lower)
    invoice_keywords = ["faktura", "invoice", "rechnung", "factura", "facture", "fatura",
                        "credit", "kredit", "gutschrift", "nota de crédito",
                        "payment", "betaling", "zahlung", "pago", "pagamento", "paiement",
                        "reverse", "revert", "devuelto", "tilbakefør", "stornieren", "annuler"]
    if any(kw in prompt_for_kw for kw in invoice_keywords):
        print("  [pre] Invoice/credit/payment task detected — ensuring bank account...", flush=True)
        result = ensure_bank_account(base_url, auth)
        print(f"  [pre] Bank account setup: {result}", flush=True)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_year = today[:4]
    system_prompt = SYSTEM_PROMPT_TEMPLATE.replace("{today}", today).replace("{today_year}", today_year)
    messages = [
        {"role": "system", "content": system_prompt},
    ]

    user_content = f"Task prompt:\n{prompt}\n\nTripletex base_url: {base_url}\nToday's date: {today}\nIMPORTANT REMINDER: Use {today} for ALL dates. Never use 2023/2024/2025 dates."
    if files:
        user_content += f"\n\nAttached files ({len(files)}):"
        for f in files:
            user_content += f"\n- {f.get('filename', 'unknown')} ({f.get('mime_type', 'unknown')})"
            try:
                raw = base64.b64decode(f["content_base64"])
                text = raw.decode("utf-8", errors="ignore")
                if len(text) < 10000:
                    user_content += f"\n  Content:\n{text}"
            except Exception:
                pass

    messages.append({"role": "user", "content": user_content})
    total_tokens = 0

    for iteration in range(25):
        iter_start = time.time()
        # Safety: stop 20s before Cloud Run's 300s timeout so we can still push logs
        elapsed_total = time.time() - agent_start
        if elapsed_total > 260:
            print(f"\n  ⏰ TIME LIMIT — {elapsed_total:.0f}s elapsed, stopping to save log", flush=True)
            diag["errors"].append(f"Time limit reached at {elapsed_total:.0f}s")
            break

        print(f"\n{'─'*50}", flush=True)
        print(f"  ITERATION {iteration+1}/25", flush=True)
        print(f"{'─'*50}", flush=True)

        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                parallel_tool_calls=False,
            )
        except Exception as e:
            print(f"  ⚠ OpenAI error: {e} — retrying in 2s...", flush=True)
            diag["errors"].append(f"OpenAI: {e}")
            time.sleep(2)
            try:
                response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=messages,
                    tools=TOOLS,
                    tool_choice="auto",
                    parallel_tool_calls=False,
                )
            except Exception as e2:
                print(f"  ✗ OpenAI retry failed: {e2}", flush=True)
                diag["errors"].append(f"OpenAI retry failed: {e2}")
                return diag

        msg = response.choices[0].message
        messages.append(msg)

        # Token usage
        usage = response.usage
        if usage:
            total_tokens += usage.total_tokens
            print(f"  Tokens: prompt={usage.prompt_tokens} completion={usage.completion_tokens} total_session={total_tokens}", flush=True)

        # Log assistant reasoning/message
        if msg.content:
            print(f"  GPT says: {msg.content[:500]}", flush=True)

        diag["iterations"] = iteration + 1

        # Log message history size for token budget awareness
        msg_count = len(messages)
        approx_chars = sum(len(m.get("content", "") if isinstance(m, dict) else (m.content or "")) for m in messages)
        print(f"  Messages: {msg_count} ({approx_chars:,} chars)", flush=True)

        if not msg.tool_calls:
            # GPT stopped without calling done() — nudge it to continue or call done()
            if iteration < 24:
                print(f"  ⚠ NUDGE — no tool calls, re-prompting GPT (reason: {msg.finish_reason})", flush=True)
                messages.append({
                    "role": "user",
                    "content": "You must either continue with the next API call or call done() if the task is complete. Do NOT output text without a tool call. What is the next step?"
                })
                continue
            print(f"  ✗ No tool calls — LLM stopped. Elapsed: {time.time()-agent_start:.1f}s", flush=True)
            break

        print(f"  Tool calls ({len(msg.tool_calls)}):", flush=True)
        for i, tc in enumerate(msg.tool_calls):
            if tc.function.name == "done":
                print(f"    [{i+1}] done()", flush=True)
            else:
                args_preview = tc.function.arguments[:800]
                print(f"    [{i+1}] {tc.function.name}({args_preview})", flush=True)

        for tool_call in msg.tool_calls:
            name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)

            if name == "done":
                elapsed = time.time() - agent_start
                remaining = 280 - elapsed  # 300s timeout, 20s safety margin
                # ── Post-execution verification (single LLM call) ──
                if remaining > 15 and not diag.get("_verified"):
                    diag["_verified"] = True
                    print(f"\n  ⏳ Verifying task completion ({remaining:.0f}s remaining)...", flush=True)
                    try:
                        # Build compact action log from messages
                        action_log = []
                        for m in messages:
                            if isinstance(m, dict) and m.get("role") == "tool":
                                try:
                                    c = json.loads(m["content"])
                                    # Summarize: status, id, key values
                                    sc = c.get("_status_code", "ok")
                                    val = c.get("value", {})
                                    summary_parts = [f"status={sc}"]
                                    for k in ("id", "name", "fixedprice", "isFixedPrice",
                                              "priceExcludingVatCurrency", "priceIncludingVatCurrency",
                                              "amount", "amountCurrency", "invoiceNumber",
                                              "totalAmount", "count"):
                                        if isinstance(val, dict) and k in val:
                                            summary_parts.append(f"{k}={val[k]}")
                                    action_log.append(" ".join(summary_parts))
                                except (json.JSONDecodeError, TypeError):
                                    pass
                            elif not isinstance(m, dict) and hasattr(m, "tool_calls") and m.tool_calls:
                                for tc_v in m.tool_calls:
                                    action_log.append(f"CALL: {tc_v.function.name}({tc_v.function.arguments[:300]})")
                        # Detect task type for targeted verification
                        _pl = prompt.lower()
                        _task_checks = ""
                        if any(kw in _pl for kw in ["fastpris", "fixed price", "fixedprice", "prix fixe", "festpreis", "precio fijo", "delbetaling", "milestone", "milepæl"]):
                            _task_checks = (
                                "TASK TYPE: Fixed-price project + milestone invoice.\n"
                                "Check these SPECIFIC things:\n"
                                "- Customer created with correct name and org number?\n"
                                "- Project created with isFixedPrice=true AND fixedprice=AMOUNT (lowercase 'fixedprice')?\n"
                                "- Project linked to customer (customer.id set on project)?\n"
                                "- Project manager set correctly?\n"
                                "- Milestone amount = fixedprice × percentage. This is the TOTAL invoice amount incl. VAT.\n"
                                "- Product priceExcludingVatCurrency = milestoneAmount / 1.25 (for 25% VAT)?\n"
                                "- Invoice created from order linked to the project?\n"
                                "- Invoice NOT sent unless task explicitly says send/sende/enviar/envoyer?\n"
                                "  'Fakturer'/'fakturér' means CREATE invoice, NOT send it!\n"
                            )
                        elif any(kw in _pl for kw in ["lønn", "salary", "paie", "salario", "gehalt", "payroll"]):
                            _task_checks = (
                                "TASK TYPE: Salary/payroll.\n"
                                "Check: employee name correct, employment with division exists, "
                                "salary transaction has correct base salary and bonus amounts.\n"
                            )
                        elif any(kw in _pl for kw in ["kreditnota", "credit note", "gutschrift", "nota de crédito"]):
                            _task_checks = (
                                "TASK TYPE: Credit note.\n"
                                "Check: original invoice found, credit note created referencing it, date set.\n"
                            )
                        elif any(kw in _pl for kw in ["leverandorfaktura", "leverandørfaktura", "supplier invoice", "eingangsrechnung", "facture reçue", "factura de proveedor"]):
                            _task_checks = (
                                "TASK TYPE: Supplier invoice.\n"
                                "Check these SPECIFIC things:\n"
                                "- Supplier created with correct name and org number?\n"
                                "- Correct expense account used (typically 4000-7999 range, NOT 1000-range asset accounts)?\n"
                                "- Correct input VAT type (inngående MVA, typically vatType 1 for 25%)?\n"
                                "- Invoice amounts NOT zero — if amount=0 or amountCurrency=0 in the response, the postings were WRONG\n"
                                "- Invoice number, date, due date match the PDF/task?\n"
                            )
                        elif any(kw in _pl for kw in ["agio", "disagio", "exchange rate", "tipo de cambio", "valutakurs", "taux de change", "wechselkurs", "valutagevinst", "valutatap"]):
                            _task_checks = (
                                "TASK TYPE: Payment with exchange rate difference (agio/disagio).\n"
                                "Check these SPECIFIC things:\n"
                                "- Payment registered for full invoice amount (closes the invoice)?\n"
                                "- Exchange rate difference calculated correctly: EUR_amount × new_rate − invoice_NOK_amount?\n"
                                "- Agio (gain) booked to account 8060, or disagio (loss) to 8160?\n"
                                "  Account 8080 is WRONG — that's for financial instruments, not exchange rates!\n"
                                "- Journal voucher has at LEAST 2 postings that sum to zero?\n"
                                "  A voucher with only 1 posting is ALWAYS wrong (amounts will be zero)!\n"
                                "- If any created voucher shows amount=0 or amountCurrency=0, the postings failed!\n"
                            )
                        elif any(kw in _pl for kw in ["reminder", "purring", "purregebyr", "mahnung", "rappel", "recordatorio", "overdue", "forfalte", "forfalt"]):
                            _task_checks = (
                                "TASK TYPE: Reminder / overdue fee.\n"
                                "Check these SPECIFIC things:\n"
                                "- Overdue invoice found (amountOutstanding > 0 and past due date)?\n"
                                "- Reminder fee voucher posted with correct accounts and balanced postings?\n"
                                "- Reminder fee INVOICE created — fee product must be VAT-EXEMPT (0% VAT)!\n"
                                "  If fee is 70 NOK, invoice total should be 70 NOK (NOT 87.5).\n"
                                "  If invoice amount includes 25% VAT on a reminder fee, that is WRONG.\n"
                                "- Invoice sent if task says 'send'?\n"
                                "- Partial payment registered on overdue invoice if requested?\n"
                            )

                        verify_prompt = (
                            f"TASK: {prompt}\n\n"
                            f"{_task_checks}\n"
                            f"ACTION LOG (chronological):\n" + "\n".join(action_log[-40:]) + "\n\n"
                            "You are an accounting verification agent. Based ONLY on the action log above, check:\n"
                            "1. MATH: All amounts, percentages, VAT calculations correct? "
                            "(e.g. if task says 50% of 274950, invoice total incl VAT must be 137475)\n"
                            "2. COMPLETENESS: Were all steps in the task done? Look at the CALL entries — "
                            "if the action log shows the step was performed with status=ok, it WAS done. "
                            "Do NOT say a step is missing if you can see it in the log!\n"
                            "IMPORTANT: 'Fakturer'/'Invoice' just means create an invoice — do NOT require sending unless the task explicitly says send/sende/enviar/envoyer/senden!\n"
                            "3. DATA: Names, org numbers, dates match the task?\n"
                            "4. AMOUNTS: If any created entity shows amount=0 or totalAmount=0 in the response, that is WRONG — the postings failed silently.\n"
                            "5. BUDGET vs INVOICE: A project 'budget'/'budsjett' is NOT the invoice amount. The invoice is for actual work/costs. Do NOT flag a mismatch between budget and invoice total.\n\n"
                            "Reply ONLY with either:\n"
                            "- 'PASS' if everything looks correct\n"
                            "- 'FAIL: <specific issue>' if something is wrong"
                        )
                        print(f"  📋 Verification prompt ({len(verify_prompt)} chars):", flush=True)
                        # Log the prompt in chunks for readability
                        for vp_line in verify_prompt.split("\n"):
                            print(f"    │ {vp_line}", flush=True)
                        verify_resp = client.chat.completions.create(
                            model="gpt-4o-mini",
                            messages=[{"role": "user", "content": verify_prompt}],
                            max_tokens=300,
                            temperature=0,
                        )
                        verdict = verify_resp.choices[0].message.content.strip()
                        v_tokens = verify_resp.usage.total_tokens if verify_resp.usage else 0
                        print(f"  🔍 Verification ({v_tokens} tokens): {verdict}", flush=True)
                        if verdict.upper().startswith("FAIL"):
                            # Reject done() — feed back to agent
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "content": json.dumps({
                                    "error": f"VERIFICATION FAILED — do NOT call done() yet. Fix this issue first: {verdict}",
                                }),
                            })
                            print(f"  ↩ Returning to agent to fix issue", flush=True)
                            continue  # continue the tool_calls loop, then next iteration
                    except Exception as e:
                        print(f"  ⚠ Verification error (proceeding): {e}", flush=True)

                print(f"\n  ✓ DONE — {iteration+1} iterations, {total_tokens} tokens, {elapsed:.1f}s", flush=True)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": "Task marked as completed.",
                })
                diag["done"] = True
                diag["tokens"] = total_tokens
                return diag

            if name == "tripletex_api":
                # Auto-fix: GPT sometimes puts query params in a 'query' field instead of 'params'
                if args.get("query") and isinstance(args["query"], str) and args["query"].startswith("?"):
                    from urllib.parse import parse_qs
                    qs = args.pop("query").lstrip("?")
                    parsed = parse_qs(qs, keep_blank_values=True)
                    extracted = {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}
                    if args.get("params") is None:
                        args["params"] = {}
                    args["params"].update(extracted)
                    print(f"    │  [fix] moved query string → params: {extracted}", flush=True)
                # Auto-fix: GPT sometimes uses wrong field names instead of "body"
                req_body = args.get("body")
                if not req_body:
                    for alt in ("requestBody", "data", "json_body", "json", "payload"):
                        if args.get(alt):
                            req_body = args.pop(alt)
                            print(f"    │  [fix] moved {alt} → body", flush=True)
                            break
                # Auto-fix: GET with body → move to params (body is ignored on GET)
                if args["method"] == "GET" and req_body and isinstance(req_body, dict):
                    if args.get("params") is None:
                        args["params"] = {}
                    args["params"].update(req_body)
                    print(f"    │  [fix] GET body → params: {req_body}", flush=True)
                    req_body = None
                    args.pop("body", None)
                # Auto-fix: reject POST without body (except for action endpoints)
                if args["method"] == "POST" and not req_body and '/:' not in args["path"]:
                    err_msg = (f"ERROR: POST {args['path']} requires a JSON body but you sent none. "
                               f"You MUST include a 'body' field with the data. "
                               f"Example: tripletex_api(method='POST', path='{args['path']}', body={{...}})")
                    print(f"    │  [fix] blocked POST without body: {args['path']}", flush=True)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps({"error": err_msg, "_status_code": 400}),
                    })
                    continue
                # Auto-fix: block POST /salary/specification — endpoint doesn't exist
                if args["method"] == "POST" and args["path"].rstrip("/") == "/salary/specification":
                    err_msg = ("ERROR: POST /salary/specification does NOT exist! "
                               "Include specifications INLINE in POST /salary/transaction body: "
                               '{"year":Y,"month":M,"payslips":[{"employee":{"id":EID},'
                               '"specifications":[{"salaryType":{"id":TID},"rate":AMT,"count":1}]}]}')
                    print(f"    │  [fix] blocked POST /salary/specification — endpoint doesn't exist", flush=True)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps({"error": err_msg, "_status_code": 404}),
                    })
                    continue
                # Auto-fix: ensure isCustomer:true on POST /customer
                if args["method"] == "POST" and args["path"].rstrip("/") == "/customer" and req_body:
                    if "isCustomer" not in req_body:
                        req_body["isCustomer"] = True
                    # Auto-set invoiceEmail and overdueNoticeEmail from email if missing
                    if req_body.get("email") and not req_body.get("invoiceEmail"):
                        req_body["invoiceEmail"] = req_body["email"]
                        print(f"    │  [fix] copied email to invoiceEmail on POST /customer", flush=True)
                    if req_body.get("email") and not req_body.get("overdueNoticeEmail"):
                        req_body["overdueNoticeEmail"] = req_body["email"]
                        print(f"    │  [fix] copied email to overdueNoticeEmail on POST /customer", flush=True)
                # Auto-fix: ensure invoiceEmail and overdueNoticeEmail on POST /supplier
                if args["method"] == "POST" and args["path"].rstrip("/") == "/supplier" and req_body:
                    if req_body.get("email") and not req_body.get("invoiceEmail"):
                        req_body["invoiceEmail"] = req_body["email"]
                        print(f"    │  [fix] copied email to invoiceEmail on POST /supplier", flush=True)
                    if req_body.get("email") and not req_body.get("overdueNoticeEmail"):
                        req_body["overdueNoticeEmail"] = req_body["email"]
                        print(f"    │  [fix] copied email to overdueNoticeEmail on POST /supplier", flush=True)
                # Auto-fix: strip email from PUT /employee (email is immutable) + ensure dateOfBirth
                is_employee_put = (args["method"] == "PUT" and "/employee/" in args["path"] and "employment" not in args["path"] and req_body)
                if is_employee_put:
                    req_body.pop("email", None)
                    if "dateOfBirth" not in req_body:
                        req_body["dateOfBirth"] = "1990-01-01"
                        print(f"    │  [fix] added dateOfBirth to PUT /employee", flush=True)
                # Auto-fix: supplier invoice voucher postings — amount→amountGross
                if args["method"] == "POST" and args["path"].rstrip("/") == "/supplierInvoice" and req_body:
                    voucher = req_body.get("voucher", {})
                    postings = voucher.get("postings", [])
                    for p in postings:
                        # Fix amount field names: amount→amountGross, amountCurrency→amountGrossCurrency
                        if "amount" in p and "amountGross" not in p:
                            p["amountGross"] = p.pop("amount")
                            print(f"    │  [fix] supplierInvoice posting: amount → amountGross", flush=True)
                        if "amountCurrency" in p and "amountGrossCurrency" not in p:
                            p["amountGrossCurrency"] = p.pop("amountCurrency")
                            print(f"    │  [fix] supplierInvoice posting: amountCurrency → amountGrossCurrency", flush=True)
                        # Ensure amountGrossCurrency matches amountGross if missing
                        if "amountGross" in p and "amountGrossCurrency" not in p:
                            p["amountGrossCurrency"] = p["amountGross"]
                            print(f"    │  [fix] supplierInvoice posting: added amountGrossCurrency", flush=True)
                        # Ensure row is >= 1
                        if p.get("row", 1) == 0:
                            p["row"] = postings.index(p) + 1
                            print(f"    │  [fix] supplierInvoice posting: row 0 → {p['row']}", flush=True)
                # Auto-fix: perDiemCompensation — find correct rateCategory for the travel expense year
                if args["method"] == "POST" and args["path"].rstrip("/") == "/travelExpense/perDiemCompensation" and req_body:
                    te_ref_id = req_body.get("travelExpense", {}).get("id")
                    if te_ref_id:
                        try:
                            te_resp = call_tripletex(base_url, auth, "GET", f"/travelExpense/{te_ref_id}",
                                                     params={"fields": "date,travelDetails"})
                            te_date = te_resp.get("value", {}).get("date", today)
                            te_year = te_date[:4]
                            # Look up all rate categories and find one matching the year
                            rc_resp = call_tripletex(base_url, auth, "GET", "/travelExpense/rateCategory",
                                                     params={"count": 1000})
                            all_cats = rc_resp.get("values", [])
                            current_cat = req_body.get("rateCategory", {}).get("id")
                            # Find matching category: same type of name, date range covering travel year
                            # Determine if looking for overnight or day trip
                            is_overnight = any(kw in str(req_body.get("overnightAccommodation", "")).upper() for kw in ["NONE", "HOTEL"]) or req_body.get("count", 1) > 1
                            target_names = ["Overnatting over 12 timer - innland"] if is_overnight else ["Dagsreise over 12 timer - innland", "Dagsreise 6-12 timer - innland"]
                            best_cat = None
                            for rc in all_cats:
                                rc_from = rc.get("fromDate", "")
                                rc_to = rc.get("toDate", "")
                                rc_name = rc.get("name", "")
                                if rc_from and rc_from[:4] == te_year and any(tn in rc_name for tn in target_names):
                                    if rc.get("isValidDomestic"):
                                        best_cat = rc
                                        break
                            if best_cat and best_cat["id"] != current_cat:
                                old_id = current_cat
                                req_body["rateCategory"] = {"id": best_cat["id"]}
                                print(f"    │  [fix] perDiem rateCategory {old_id} → {best_cat['id']} ({best_cat['name']})", flush=True)
                        except Exception as e:
                            print(f"    │  [fix] perDiem auto-fix failed: {e}", flush=True)

                # Auto-fix: employment division — salary fails without it, and it can't be changed after creation
                if (args["method"] == "POST" and args["path"].rstrip("/") == "/employee/employment"
                        and req_body and not req_body.get("division")):
                    try:
                        co_resp = call_tripletex(base_url, auth, "GET", "/company/>withLoginAccess")
                        companies = co_resp.get("values", [])
                        if companies:
                            co_id = companies[0].get("id")
                            if co_id:
                                req_body["division"] = {"id": co_id}
                                print(f"    │  [fix] employment: added division {{id:{co_id}}} from company lookup", flush=True)
                    except Exception as e:
                        print(f"    │  [fix] employment division auto-fix failed: {e}", flush=True)

                # Auto-fix: milestone product pricing — price should be ex-VAT
                # If we tracked a fixedprice and now POST /product with price = fixedprice * fraction,
                # the LLM likely forgot to divide by 1.25. Auto-correct.
                if (tracked_fixedprice and args["method"] == "POST"
                        and args["path"].rstrip("/") == "/product" and req_body):
                    price = req_body.get("priceExcludingVatCurrency")
                    if price and tracked_fixedprice > 0:
                        ratio = round(price / tracked_fixedprice, 4)
                        # Common milestone fractions: 25%, 33%, 50%, 75%, 100%
                        known_fractions = [0.25, 1/3, 0.5, 0.75, 1.0]
                        for frac in known_fractions:
                            if abs(ratio - frac) < 0.001:
                                # Price matches a clean fraction → LLM used milestone amount as ex-VAT
                                corrected = round(price / 1.25, 2)
                                req_body["priceExcludingVatCurrency"] = corrected
                                print(f"    │  [fix] milestone product price {price} → {corrected} "
                                      f"(÷1.25 so total incl. 25% VAT = {price})", flush=True)
                                break

                # ── Validation rules check (after auto-fixes, before API call) ──
                violations = validate_tool_call(
                    args["method"], args["path"],
                    body=req_body, params=args.get("params"),
                )
                # Extra: voucher postings must have ≥2 rows and sum to ~zero
                if args["method"] == "POST" and args["path"].rstrip("/") == "/ledger/voucher" and req_body:
                    postings = req_body.get("postings", [])
                    if len(postings) < 2:
                        violations.append(
                            "[voucher-min-postings] Voucher must have at least 2 postings "
                            "(one debit, one credit). A single posting will result in amount=0. "
                            "Add both a debit (positive) AND a credit (negative) posting that sum to zero."
                        )
                    elif postings:
                        total = sum(p.get("amount", 0) for p in postings)
                        if abs(total) > 0.01:
                            violations.append(
                                f"[voucher-balance] Voucher postings must sum to zero but sum to {total}. "
                                f"Debit = positive, Credit = negative. Adjust amounts so they balance."
                            )
                if violations:
                    v_text = "\n".join(violations)
                    print(f"    │  [reject] {len(violations)} rule violation(s):", flush=True)
                    for v in violations:
                        print(f"    │    • {v}", flush=True)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps({
                            "error": f"VALIDATION REJECTED — fix these issues and retry:\n{v_text}",
                            "_status_code": 400,
                            "_validation_rules": violations,
                        }),
                    })
                    diag["errors"].append(f"RULE_VIOLATION: {args['method']} {args['path']}")
                    continue

                result = call_tripletex(
                    base_url, auth,
                    method=args["method"],
                    path=args["path"],
                    params=args.get("params"),
                    body=req_body,
                )
                sc = result.get("_status_code", 200)
                call_info = f"{args['method']} {args['path']} -> {sc}"
                diag["api_calls"].append(call_info)
                if sc >= 400:
                    # Capture validation error details
                    val_msgs = result.get("validationMessages", [])
                    msg_text = "; ".join(m.get("message", "") for m in val_msgs) if val_msgs else ""
                    err_detail = f"{call_info}: {msg_text}" if msg_text else call_info
                    diag["errors"].append(err_detail)

                    # Guide GPT to retry /supplierInvoice instead of falling through to /ledger/voucher
                    if args["method"] == "POST" and args["path"].rstrip("/") == "/supplierInvoice" and sc == 422:
                        result["_retry_hint"] = ("IMPORTANT: Fix the validation error and retry POST /supplierInvoice. "
                                                 "Do NOT fall through to POST /ledger/voucher — that only creates a journal entry, "
                                                 "not a supplier invoice entity. Check: amountGross (not amount), row >= 1, "
                                                 "date on each posting, supplier on credit posting.")
                        print(f"    │  [hint] injected retry guidance for /supplierInvoice", flush=True)

                # Track fixed-price project creation + diagnostic GET-back
                if (args["method"] == "POST" and args["path"].rstrip("/") == "/project"
                        and sc < 400 and req_body
                        and req_body.get("isFixedPrice") and req_body.get("fixedprice")):
                    tracked_fixedprice = float(req_body["fixedprice"])
                    print(f"    │  [track] fixedprice project: {tracked_fixedprice}", flush=True)
                    # Diagnostic: GET the project back to verify fixedprice is visible
                    proj_id = result.get("value", {}).get("id")
                    if proj_id:
                        diag_resp = call_tripletex(base_url, auth, "GET", f"/project/{proj_id}",
                                                   params={"fields": "*,project(*)"})
                        diag_val = diag_resp.get("value", {})
                        fp_back = diag_val.get("fixedprice")
                        is_fp_back = diag_val.get("isFixedPrice")
                        print(f"    │  [diag] GET /project/{proj_id}: fixedprice={fp_back}, isFixedPrice={is_fp_back}", flush=True)
                        if fp_back is None or not is_fp_back:
                            print(f"    │  [diag] WARNING: fixedprice not visible after creation!", flush=True)
                            # Try PUT to re-set the fixedprice
                            proj_ver = diag_val.get("version", 0)
                            put_body = {"id": proj_id, "version": proj_ver,
                                        "fixedprice": req_body["fixedprice"],
                                        "isFixedPrice": True}
                            put_resp = call_tripletex(base_url, auth, "PUT", f"/project/{proj_id}",
                                                      body=put_body)
                            put_sc = put_resp.get("_status_code", 200)
                            print(f"    │  [diag] PUT fixedprice re-set: {put_sc}", flush=True)

                # Track employee rename state
                # PUT /employee succeeded → mark rename done
                if is_employee_put and sc < 400:
                    employee_renamed = True
                    pending_employee_rename = None
                    print(f"    │  [track] employee renamed successfully via PUT", flush=True)

                # Step 1: POST /employee failed → save desired name
                if args["method"] == "POST" and args["path"].rstrip("/") == "/employee" and sc >= 400 and req_body:
                    fn = req_body.get("firstName", "")
                    ln = req_body.get("lastName", "")
                    if fn and ln:
                        pending_employee_rename = {"firstName": fn, "lastName": ln}
                        employee_renamed = False
                        print(f"    │  [track] employee rename pending: {fn} {ln}", flush=True)
                # Step 1b: POST /employee succeeded → no rename needed
                if args["method"] == "POST" and args["path"].rstrip("/") == "/employee" and sc < 400:
                    pending_employee_rename = None
                    employee_renamed = True

                # Step 2: GPT is about to use employee without renaming — auto-rename
                # Detect: GPT calls POST /project while rename is pending
                if (pending_employee_rename and not employee_renamed
                        and args["method"] == "POST"
                        and args["path"].rstrip("/") in ("/project", "/timesheet/entry", "/travelExpense", "/salary/transaction")):
                    # Find the last GET /employee response to get employee id/version
                    emp_data = None
                    for prev_msg in reversed(messages):
                        if isinstance(prev_msg, dict) and prev_msg.get("role") == "tool":
                            try:
                                content = json.loads(prev_msg["content"])
                                if isinstance(content.get("values"), list) and len(content["values"]) > 0:
                                    first_emp = content["values"][0]
                                    if "firstName" in first_emp:
                                        # Pick last employee (not first admin)
                                        emp_data = content["values"][-1]
                                        break
                            except (json.JSONDecodeError, TypeError):
                                pass
                    if emp_data and "id" in emp_data:
                        emp_id = emp_data["id"]
                        emp_ver = emp_data.get("version", 0)
                        put_body = {
                            "id": emp_id,
                            "version": emp_ver,
                            "firstName": pending_employee_rename["firstName"],
                            "lastName": pending_employee_rename["lastName"],
                            "dateOfBirth": "1990-01-01",
                        }
                        print(f"    │  [auto-fix] GPT skipped PUT /employee — auto-renaming employee {emp_id} to {pending_employee_rename['firstName']} {pending_employee_rename['lastName']}", flush=True)
                        rename_result = call_tripletex(base_url, auth, "PUT", f"/employee/{emp_id}", body=put_body)
                        rename_sc = rename_result.get("_status_code", 200)
                        if rename_sc < 400:
                            employee_renamed = True
                            pending_employee_rename = None
                            print(f"    │  [auto-fix] employee renamed successfully", flush=True)
                        else:
                            print(f"    │  [auto-fix] employee rename failed: {rename_sc}", flush=True)

                result_str = json.dumps(result, ensure_ascii=False)
                # Log response data (truncated for readability)
                preview = result_str[:1500] + "…" if len(result_str) > 1500 else result_str
                print(f"    │  response: {preview}", flush=True)
                if len(result_str) > 8000:
                    result_str = result_str[:8000] + "...(truncated)"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_str,
                })

        print(f"  Iteration {iteration+1} took {time.time()-iter_start:.1f}s", flush=True)

    diag["tokens"] = total_tokens
    print(f"\n  ⚠ Max iterations reached. Elapsed: {time.time()-agent_start:.1f}s, Tokens: {total_tokens}", flush=True)
    return diag


@app.post("/")
@app.post("/solve")
async def solve(request: Request):
    body = await request.json()

    prompt = body.get("prompt", "")
    files = body.get("files", [])
    creds = body["tripletex_credentials"]

    base_url = creds["base_url"]
    token = creds["session_token"]
    auth = ("0", token)

    # Auto-capture task to logs (extract with test_suite/fetch_cases.py)
    try:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        capture = json.dumps({
            "prompt": prompt,
            "files": [{"filename": f.get("filename"), "mime_type": f.get("mime_type")} for f in files],
            "source": "competition",
            "captured_at": ts,
        }, ensure_ascii=False)
        print(f"CASE_CAPTURE:{capture}:END_CAPTURE", flush=True)
    except Exception as e:
        print(f"  [capture] Failed: {e}", flush=True)

    t0 = time.time()
    diag = {}
    log_capture = LogCapture()
    try:
        with log_capture:
            print(f"\n{'='*70}", flush=True)
            print(f"  NEW TASK RECEIVED", flush=True)
            print(f"  Version: {AGENT_VERSION}", flush=True)
            print(f"{'='*70}", flush=True)
            print(f"  Prompt: {prompt[:500]}{'…' if len(prompt)>500 else ''}", flush=True)
            print(f"  Files:  {len(files)}", flush=True)
            print(f"  URL:    {base_url}", flush=True)
            if files:
                for f in files:
                    print(f"    - {f.get('filename', '?')} ({f.get('mime_type', '?')})", flush=True)
            print(f"{'─'*70}", flush=True)

            try:
                diag = run_agent(prompt, files, base_url, auth) or {}
            except Exception as e:
                import traceback
                print(f"  ✗ AGENT ERROR: {e}", flush=True)
                traceback.print_exc()
                diag["errors"] = [str(e)]

            elapsed = time.time()-t0
            print(f"\n{'='*70}", flush=True)
            print(f"  TASK COMPLETE — total {elapsed:.1f}s", flush=True)
            print(f"{'='*70}\n", flush=True)
    except Exception as outer_err:
        print(f"  ✗ OUTER ERROR: {outer_err}", flush=True)
        diag["errors"] = diag.get("errors", []) + [str(outer_err)]

    # Push log to GitHub (best-effort, non-blocking)
    log_text = log_capture.getvalue()
    if log_text:
        ts_file = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        # Classify task type for a readable filename
        pl = prompt.lower()
        if any(k in pl for k in ["reiseregning", "travel expense", "nota de gastos de viaje", "note de frais", "reisekosten", "despesas de viagem"]):
            task_type = "travel_expense"
        elif any(k in pl for k in ["purregebyr", "reminder fee", "late fee", "mora", "frais de retard", "mahngebühr"]):
            task_type = "reminder_fee"
        elif any(k in pl for k in ["kreditnota", "credit note", "nota de crédito", "gutschrift", "avoir"]):
            task_type = "credit_note"
        elif any(k in pl for k in ["leverandørfaktura", "supplier invoice", "factura de proveedor", "fournisseur", "lieferantenrechnung", "fatura do fornecedor"]):
            task_type = "supplier_invoice"
        elif any(k in pl for k in ["tilbakefør", "reverse", "stornieren", "annuler", "reverter", "devuelto"]):
            task_type = "reverse"
        elif any(k in pl for k in ["betaling", "payment", "pago", "paiement", "zahlung", "pagamento"]):
            task_type = "payment"
        elif any(k in pl for k in ["lønn", "salary", "salario", "salaire", "gehalt", "salário"]):
            task_type = "salary"
        elif any(k in pl for k in ["faktura", "invoice", "rechnung", "factura", "facture", "fatura"]):
            task_type = "invoice"
        elif any(k in pl for k in ["prosjekt", "project", "proyecto", "projet", "projekt"]):
            task_type = "project"
        elif any(k in pl for k in ["ansatt", "employee", "empleado", "employé", "mitarbeiter", "empregado"]):
            task_type = "employee"
        else:
            task_type = "task"
        # Add short prompt hint (first meaningful words after task-type keywords)
        clean = re.sub(r'[^\w\s]', '', prompt[:80])
        short = "_".join(clean.split()[:3]).lower()
        status = "ok" if diag.get("done") else "fail"
        iters = diag.get("iterations", 0)
        log_filename = f"{ts_file}_{task_type}_{status}_{iters}iter_{short}.log"
        # Push synchronously (short timeout) so log is saved before Cloud Run kills the instance
        try:
            push_log_to_github(log_text, log_filename)
        except Exception as e:
            print(f"  ⚠ Log push failed: {e}", flush=True)

    return JSONResponse({
        "status": "completed" if diag.get("done") else "incomplete",
        "iterations": diag.get("iterations", 0),
        "api_calls": diag.get("api_calls", []),
        "errors": diag.get("errors", []),
        "tokens": diag.get("tokens", 0),
    })
