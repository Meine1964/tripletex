import base64
import io
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone

import requests
import urllib3
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
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


# ── GCS Log Storage ─────────────────────────────────────────────
GCS_BUCKET = os.getenv("GCS_LOG_BUCKET", "tripletex-agent-logs")
GCS_PREFIX = "Day_3/"  # prefix inside bucket
_gcs_client = None

def _get_gcs_bucket():
    """Lazy-init GCS client and return bucket. Returns None if unavailable."""
    global _gcs_client
    try:
        from google.cloud import storage
        if _gcs_client is None:
            _gcs_client = storage.Client()
        return _gcs_client.bucket(GCS_BUCKET)
    except Exception as e:
        print(f"  [gcs] GCS unavailable: {e}", flush=True)
        return None

def push_log_to_gcs(log_text: str, filename: str):
    """Upload a log file to GCS. No concurrency conflicts."""
    try:
        bucket = _get_gcs_bucket()
        if not bucket:
            return
        blob = bucket.blob(f"{GCS_PREFIX}{filename}")
        blob.upload_from_string(log_text, content_type="text/plain")
        print(f"  [gcs] Uploaded: gs://{GCS_BUCKET}/{GCS_PREFIX}{filename}", flush=True)
    except Exception as e:
        print(f"  [gcs] Upload failed: {e}", flush=True)


# ── GitHub Log Push (fallback when GCS unavailable) ─────────────
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = "Meine1964/tripletex"
GITHUB_LOG_PATH = "logs"

def push_log_to_github(log_text: str, filename: str):
    """Push a log file to GitHub repo via API with retry on 409 conflicts."""
    if not GITHUB_TOKEN:
        return
    import base64 as b64
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_LOG_PATH}/{filename}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    content_b64 = b64.b64encode(log_text.encode("utf-8")).decode("ascii")
    for attempt in range(4):
        try:
            body = {"message": f"Log: {filename}", "content": content_b64, "branch": "main"}
            resp = requests.put(url, json=body, headers=headers, timeout=15)
            if resp.status_code in (200, 201):
                print(f"  [github] Log pushed: {filename} (attempt {attempt+1})", flush=True)
                return
            elif resp.status_code == 409 and attempt < 3:
                delay = random.uniform(1.5, 4.0)
                print(f"  [github] Conflict (409), retry in {delay:.1f}s (attempt {attempt+1}/4)...", flush=True)
                time.sleep(delay)
                continue
            else:
                print(f"  [github] Push failed ({resp.status_code}): {resp.text[:200]}", flush=True)
                return
        except Exception as e:
            print(f"  [github] Push error (attempt {attempt+1}): {e}", flush=True)
            if attempt < 3:
                time.sleep(random.uniform(1, 2))
                continue
            return

def list_gcs_logs():
    """List all log files in GCS bucket."""
    try:
        bucket = _get_gcs_bucket()
        if not bucket:
            return []
        blobs = bucket.list_blobs(prefix=GCS_PREFIX)
        return [{
            "name": b.name.replace(GCS_PREFIX, ""),
            "size": b.size,
            "updated": b.updated.isoformat() if b.updated else None,
        } for b in blobs if b.name.endswith(".log")]
    except Exception as e:
        print(f"  [gcs] List failed: {e}", flush=True)
        return []

def read_gcs_log(filename: str):
    """Read a log file from GCS. Returns content string or None."""
    try:
        bucket = _get_gcs_bucket()
        if not bucket:
            return None
        blob = bucket.blob(f"{GCS_PREFIX}{filename}")
        if not blob.exists():
            return None
        return blob.download_as_text()
    except Exception as e:
        print(f"  [gcs] Read failed: {e}", flush=True)
        return None

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
- PUT /employee/{id} — update (include id + version). Can also set department: {"department": {"id": DEPT_ID}}
- GET/POST /employee/employment — employment records for an employee. GET ?employeeId=X&fields=*
- GET/POST/PUT /employee/employment/details — employment details (annualSalary, percentOfFullTimeEquivalent, occupationCode, workingHoursScheme). CRITICAL: enum fields (employmentType, remunerationType, employmentForm, workingHoursScheme) require INTEGER values: 1=ORDINARY/PERMANENT/MONTHLY_PAY/NON_SHIFT
- GET /employee/employment/occupationCode — lookup occupation codes (use ?name=SEARCH_TERM)
- POST /employee/standardTime — set standard work hours: {employee:{id}, hoursPerDay: 7.5, fromDate: "YYYY-MM-DD"} (fromDate is REQUIRED!)
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

Step 1: Find or create the employee.
  GET /employee?fields=* — the employee usually already exists in the sandbox.
  If the task provides an email: FIRST try POST /employee with {firstName, lastName, email}.
  If POST fails (422 "Brukertype" error): GET existing employee, pick one, ALWAYS do PUT to rename.
  CRITICAL: You MUST PUT /employee/{id} to rename the employee! Sandbox employees have generic names.
  CRITICAL: PUT body MUST include id, version, firstName, lastName, dateOfBirth. Do NOT include email!

Step 2: Ensure employee has an employment record.
  GET /employee/employment?employeeId=EMPLOYEE_ID&fields=*
  If employment EXISTS → use it as-is (proceed to Step 3).
  If NO employment: create one:
    a. Find company ID: GET /company/>withLoginAccess. If 0 results, use the employee's "companyId" field from the GET /employee response.
    b. POST /employee/employment with body:
       {"employee": {"id": EMP_ID}, "startDate": "YYYY-MM-01", "division": {"id": COMPANY_ID}}
       Use 1st of current month as startDate. Do NOT include "department" field.
  If POST fails with "Overlappende perioder" (overlapping periods):
    The employee ALREADY HAS employment! GET /employee/employment?employeeId=EMP_ID to find it and use that.
  IMPORTANT: Do NOT try to DELETE employment records — DELETE is not allowed (405). Always use the existing employment.
  If POST /employee/employment fails with "division.id" error, the sandbox may not support division assignment.
  In that case, SKIP creating new employment — use the existing one! GET /employee/employment?employeeId=X to find it.
  An employment with division=null is still USABLE for salary transactions and employment details.

Step 2b: Look up salary types.
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

EMPLOYEE ONBOARDING / OFFER LETTER WORKFLOW (for "integrasjon"/"onboarding"/"offer letter"/"tilbudsbrev"/"carta de oferta"/"lettre d'offre"/"Angebotsschreiben" tasks WITH a PDF attachment):
The task asks you to set up a new employee from a PDF offer letter — create employee, assign department, configure employment details (percentage, salary), and set standard work hours.

Step 1: Read the PDF carefully. Extract: employee name, email (if given), date of birth (if given), department name, employment percentage, annual salary, start date, occupation/title, standard work hours per week.

Step 2: Create or rename the employee.
  Try POST /employee with {firstName, lastName, email}. If 422: GET /employee?fields=*, then PUT /employee/{id} with {id, version, firstName, lastName, dateOfBirth}.
  CRITICAL: If the PDF specifies dateOfBirth, use that exact date! Otherwise use "1990-01-01".
  CRITICAL: Do NOT include email in PUT. MUST do PUT to rename!

Step 3: Assign the department.
  GET /department to list existing departments.
  If the department from the PDF doesn't exist yet: POST /department with {"name": "DEPT_NAME", "departmentNumber": "NEXT_NUMBER"}.
  Then assign department to employee: PUT /employee/{id} with {"id": X, "version": Y, "department": {"id": DEPT_ID}}.
  CRITICAL: You MUST do PUT /employee to set department — it's a field on the employee object!

Step 4: Ensure employment record exists.
  GET /employee/employment?employeeId=EMP_ID&fields=*
  If employment exists → use its id. Note it for Step 5.
  If no employment: POST /employee/employment with {"employee": {"id": EMP_ID}, "startDate": "YYYY-MM-01", "division": {"id": COMPANY_ID}}.
  If POST fails with "Overlappende perioder": GET employment again — it already exists. Use it.
  IMPORTANT: Do NOT try DELETE on employment — it returns 405! Always use the existing one.

Step 5: Configure employment details (percentage, annual salary, occupation code, work hours scheme).
  GET /employee/employment/details?employmentId=EMPLOYMENT_ID to see existing details.
  If details exist: PUT /employee/employment/details/{detailId} with updated fields.
  If no details: POST /employee/employment/details with body:
  {
    "employment": {"id": EMPLOYMENT_ID},
    "date": "START_DATE",
    "employmentType": 1,
    "employmentForm": 1,
    "remunerationType": 1,
    "workingHoursScheme": 1,
    "percentOfFullTimeEquivalent": PERCENTAGE,
    "annualSalary": ANNUAL_SALARY,
    "occupationCode": {"id": CODE_ID}
  }
  CRITICAL: These enum fields require INTEGER values (not strings like "ORDINARY"!):
    employmentType: 0=NOT_CHOSEN, 1=ORDINARY, 2=MARITIME, 3=FREELANCE
    remunerationType: 0=NOT_CHOSEN, 1=MONTHLY_PAY, 2=HOURLY_PAY, 3=COMMISSIONED, 4=FEE
    employmentForm: 0=NOT_CHOSEN, 1=PERMANENT, 2=TEMPORARY
    workingHoursScheme: 0=NOT_CHOSEN, 1=NON_SHIFT, 2=ROUND_THE_CLOCK
  Use 1 for normal employment (ordinary, permanent, monthly pay, non-shift).
  IMPORTANT: Look up occupation codes first: GET /employee/employment/occupationCode?name=SEARCH_TERM
  If the API returns codes, use the best match. If not found, try without occupationCode.
  percentOfFullTimeEquivalent = 100.0 for full time, 80.0 for 80%, etc.

Step 6: Configure standard work hours (if task mentions "standard hours"/"arbeidstid"/"horas de trabalho"/"heures de travail").
  POST /employee/standardTime with body:
  {
    "employee": {"id": EMP_ID},
    "hoursPerDay": HOURS_PER_DAY,
    "fromDate": "{today}"
  }
  CRITICAL: fromDate is REQUIRED! Use today's date or the employment start date.
  Standard Norwegian full-time = 7.5 hours/day. If task says 37.5 hrs/week → 7.5/day.

Step 7: Call done().

KEY ENDPOINTS FOR ONBOARDING:
- PUT /employee/{id} — set department: {"department": {"id": DEPT_ID}} (also include id, version, firstName, lastName, dateOfBirth)
- GET/POST /employee/employment — employment records
- GET/POST/PUT /employee/employment/details — employment details (salary, percentage, occupation)
- GET /employee/employment/occupationCode — lookup occupation codes (use ?name=XXX)
- POST /employee/standardTime — set standard work hours

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

FULL PROJECT CYCLE WORKFLOW (for "prosjektsyklusen"/"project cycle"/"projektzyklus"/"cycle de projet"/"ciclo del proyecto" tasks — involves creating project, registering hours, costs, and invoicing):
This is a MULTI-STEP task: create entities, register work, register costs, and invoice the customer.

Step 1: Create customer. POST /customer with name, isCustomer:true, organizationNumber.

Step 2: Create supplier (if task mentions supplier/leverandør costs). POST /supplier with name, organizationNumber, email, invoiceEmail, overdueNoticeEmail.

Step 3: Create/update employees.
  Try POST /employee with {firstName, lastName, email}.
  If 422: GET /employee?fields=*, then PUT /employee/{id} with {id, version, firstName, lastName, dateOfBirth: "1990-01-01"}.
  CRITICAL: Do NOT include email in PUT. MUST include dateOfBirth. MUST do PUT to rename!

Step 4: Create the project.
  POST /project with: name, number ("PRJ-XXXX" random), projectManager:{id}, startDate, customer:{id}.
  If task mentions "budsjett"/"budget" → set isFixedPrice:true, fixedprice:AMOUNT (lowercase!).

Step 5: Register timesheet entries for each employee.
  POST /timesheet/entry with employee:{id}, project:{id}, activity:{id}, date, hours.
  First GET /activity to find a project activity (look for isProjectActivity:true, e.g. "Fakturerbart arbeid").

Step 6: Register supplier costs (if applicable).
  POST /supplierInvoice with supplier, invoiceNumber, invoiceDate, invoiceDueDate, and voucher with postings.
  Use amountGross/amountGrossCurrency in postings (NOT amount/amountCurrency).

Step 7: Create customer invoice.
  CRITICAL: For fixed-price projects (isFixedPrice=true), the invoice excl. VAT MUST equal the fixedprice!
  a. POST /product — name describing the work, priceExcludingVatCurrency = fixedprice amount, vatType:{id:3} (25% outgoing VAT).
  b. POST /order — customer:{id}, orderDate, deliveryDate, project:{id}.
  c. POST /order/orderline — order:{id}, product:{id}, count:1.
  d. POST /invoice — invoiceDate, invoiceDueDate, orders:[{id}].
  The resulting invoice total incl VAT = fixedprice × 1.25.
  Do NOT use hours×rate for the invoice amount on a fixed-price project! Use the fixedprice directly.

Step 8: Call done().

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

DEPARTMENT WORKFLOW (for "department"/"avdeling"/"Abteilung"/"département"/"departamento" tasks):
The task asks you to create one or more departments.

Step 1: For EACH department requested, POST /department with:
  {"name": "DEPARTMENT_NAME", "departmentNumber": "NUMBER_STRING"}
  - departmentNumber MUST be a string (e.g. "1", "2", "3" or "100", "200", "300").
  - If the task gives specific numbers, use those. Otherwise use sequential "1", "2", "3".
  - If the task gives codes/abbreviations, include them.

Step 2: Repeat for each department. If the task says "create 3 departments", create all 3.

Step 3: Call done().

MULTI-VAT INVOICE WORKFLOW (for invoices with products at DIFFERENT VAT rates like 25%, 15%, 0%):
When the task mentions products with different VAT rates, follow these steps:

Step 1: Create the customer (if not exists). POST /customer with name, isCustomer:true, organizationNumber.

Step 2: Look up VAT types. GET /ledger/vatType.
  - 25% outgoing = look for name containing "Utgående" and percentage=25 (usually id=3)
  - 15% outgoing = look for name containing "Utgående" and percentage=15
  - 0% exempt = look for name containing "Utgående" and percentage=0 or "fritatt"/"exempt"
  Use the "id" field (NOT the "number" field).

Step 3: Create a Product for EACH item with its specific VAT rate.
  POST /product with: name, priceExcludingVatCurrency, vatType:{id: CORRECT_VAT_TYPE_ID}
  CRITICAL: Each product gets its OWN vatType matching its VAT rate!

Step 4: Create the order. POST /order with customer:{id}, orderDate, deliveryDate.

Step 5: Add order lines. POST /order/orderline for EACH product:
  {order:{id}, product:{id}, count: QUANTITY}

Step 6: Create the invoice. POST /invoice with invoiceDate, invoiceDueDate, orders:[{id}].
  The invoice will automatically calculate the correct total with mixed VAT rates.

Step 7: Send if explicitly requested. PUT /invoice/{id}/:send with params={"sendType":"EMAIL"}.

Step 8: Call done().

ORDER-INVOICE-PAYMENT WORKFLOW (for tasks that combine ordering, invoicing, AND payment):
When the task asks to create an order, invoice it, and register payment:

Step 1: Create the customer (if not exists).
Step 2: Create products. POST /product for each item.
Step 3: Create the order. POST /order with customer:{id}, orderDate, deliveryDate.
Step 4: Add order lines. POST /order/orderline for each product.
Step 5: Create the invoice. POST /invoice.
Step 6: Send the invoice if task says send. PUT /invoice/{id}/:send.
Step 7: Register payment. GET /bank to find payment types. Then:
  PUT /invoice/{id}/:payment with params={"paymentDate":"YYYY-MM-DD", "paymentTypeId":BANK_ID, "paidAmount":TOTAL}
Step 8: Call done().

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

    # Pre-check for salary/employee tasks: scan employees and their employment records
    salary_keywords = ["lønn", "salary", "paie", "salario", "gehalt", "payroll", "salário", "lön",
                       "funcionario", "onboarding", "integrasjon", "tilbudsbrev", "offer letter",
                       "carta de oferta", "ansatt", "employee", "empregado", "empleado", "mitarbeiter"]
    salary_pre_info = ""
    if any(kw in prompt_for_kw for kw in salary_keywords):
        print("  [pre] Employee/salary task detected — scanning employees and employment...", flush=True)
        try:
            emp_resp = call_tripletex(base_url, auth, "GET", "/employee", params={"fields": "*"})
            employees = emp_resp.get("values", [])
            if employees:
                salary_pre_info += f"\n\nPRE-SCANNED SANDBOX DATA (use this to save time):\nFound {len(employees)} existing employee(s):"
                for emp in employees:
                    eid = emp.get("id")
                    emp_company_id = emp.get("companyId")
                    emp_dept = emp.get("department")
                    salary_pre_info += f"\n  Employee id={eid}: {emp.get('firstName', '?')} {emp.get('lastName', '?')} (email={emp.get('email', 'none')}, companyId={emp_company_id})"
                    if emp_dept:
                        salary_pre_info += f"\n    Department: id={emp_dept.get('id')}"
                    # Check employment
                    try:
                        empl_resp = call_tripletex(base_url, auth, "GET", "/employee/employment",
                            params={"employeeId": eid, "fields": "*"})
                        empls = empl_resp.get("values", [])
                        if empls:
                            for empl in empls:
                                div = empl.get("division", {})
                                div_id = div.get("id") if div else None
                                salary_pre_info += f"\n    Employment id={empl.get('id')}: startDate={empl.get('startDate')}, division.id={div_id}"
                                # Check employment details
                                empl_details = empl.get("employmentDetails", [])
                                if empl_details:
                                    for det in empl_details:
                                        salary_pre_info += f"\n      Detail id={det.get('id')}"
                        else:
                            salary_pre_info += f"\n    NO employment record — you MUST create one (use companyId={emp_company_id} as division.id)"
                    except Exception:
                        salary_pre_info += "\n    [could not check employment]"
                # Also get company ID for division
                try:
                    co_resp = call_tripletex(base_url, auth, "GET", "/company/>withLoginAccess")
                    companies = co_resp.get("values", [])
                    if companies:
                        salary_pre_info += f"\n  Company id={companies[0].get('id')} (use as division.id for employment)"
                    elif employees:
                        # Fallback: use employee's companyId
                        fallback_cid = employees[0].get("companyId")
                        if fallback_cid:
                            salary_pre_info += f"\n  Company (from employee): id={fallback_cid} (use as division.id for employment)"
                except Exception:
                    pass
                # List existing departments
                try:
                    dept_resp = call_tripletex(base_url, auth, "GET", "/department")
                    depts = dept_resp.get("values", [])
                    if depts:
                        salary_pre_info += f"\n  Existing departments: " + ", ".join(f"{d.get('name')} (id={d.get('id')})" for d in depts)
                except Exception:
                    pass
                salary_pre_info += "\n  IMPORTANT: Do NOT try DELETE on employment records — it returns 405! Use existing employment."
                print(f"  [pre] Pre-scan complete: {len(employees)} employees found", flush=True)
        except Exception as e:
            print(f"  [pre] Pre-scan failed: {e}", flush=True)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_year = today[:4]
    system_prompt = SYSTEM_PROMPT_TEMPLATE.replace("{today}", today).replace("{today_year}", today_year)
    messages = [
        {"role": "system", "content": system_prompt},
    ]

    user_content = f"Task prompt:\n{prompt}\n\nTripletex base_url: {base_url}\nToday's date: {today}\nIMPORTANT REMINDER: Use {today} for ALL dates. Never use 2023/2024/2025 dates.{salary_pre_info}"
    vision_parts = []  # For image vision inputs
    if files:
        user_content += f"\n\nAttached files ({len(files)}):"
        for f in files:
            fname = f.get("filename", "unknown")
            mime = f.get("mime_type", "unknown")
            user_content += f"\n- {fname} ({mime})"
            try:
                raw = base64.b64decode(f["content_base64"])
                is_pdf = mime == "application/pdf" or fname.lower().endswith(".pdf")
                is_image = mime.startswith("image/")

                if is_pdf:
                    # Extract text from PDF using pdfplumber
                    try:
                        import pdfplumber, io
                        with pdfplumber.open(io.BytesIO(raw)) as pdf:
                            pdf_text = "\n\n".join(
                                page.extract_text() or "" for page in pdf.pages
                            )
                        if pdf_text.strip():
                            user_content += f"\n  PDF Text Content:\n{pdf_text[:15000]}"
                            print(f"    [file] Extracted {len(pdf_text)} chars from PDF: {fname}", flush=True)
                        else:
                            user_content += "\n  [PDF has no extractable text, content may be image-based]"
                            print(f"    [file] PDF has no text: {fname}", flush=True)
                    except Exception as e:
                        user_content += f"\n  [PDF text extraction failed: {e}]"
                        print(f"    [file] PDF extraction error: {e}", flush=True)
                elif is_image:
                    # Send images directly as vision input
                    vision_parts.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime};base64,{f['content_base64']}",
                            "detail": "high",
                        },
                    })
                    user_content += "\n  [Image attached — see below]"
                    print(f"    [file] Image sent as vision input: {fname}", flush=True)
                else:
                    # Plain text files
                    text = raw.decode("utf-8", errors="ignore")
                    if len(text) < 10000:
                        user_content += f"\n  Content:\n{text}"
            except Exception as e:
                user_content += f"\n  [Could not read file: {e}]"

    # Build user message: use content array if we have vision parts, else plain text
    if vision_parts:
        user_msg_content = [{"type": "text", "text": user_content}] + vision_parts
    else:
        user_msg_content = user_content
    messages.append({"role": "user", "content": user_msg_content})
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
        finish_reason = response.choices[0].finish_reason
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
                print(f"  ⚠ NUDGE — no tool calls, re-prompting GPT (reason: {finish_reason})", flush=True)
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
                        if any(kw in _pl for kw in ["prosjektsyklusen", "project cycle", "projektzyklus", "cycle de projet", "ciclo del proyecto", "ciclo do projeto"]):
                            _task_checks = (
                                "TASK TYPE: Full project cycle (create project, register hours/costs, invoice).\n"
                                "Check these SPECIFIC things:\n"
                                "- Customer and supplier created with correct names and org numbers?\n"
                                "- Employees created/updated with correct names?\n"
                                "- Project created with correct budget/fixedprice?\n"
                                "- Timesheet hours registered for each employee as specified?\n"
                                "- Supplier costs registered if mentioned in task?\n"
                                "- Customer invoice created and linked to the project?\n"
                                "- BUDGET: The budget/budsjett IS the project fixed price. The invoice excl. VAT should equal the fixedprice. Do NOT flag this as wrong.\n"
                                "- Invoice NOT sent unless task explicitly says send/sende.\n"
                                "- Supplier invoice amount=0 in response is NORMAL for Tripletex — do NOT flag it as an error.\n"
                            )
                        elif any(kw in _pl for kw in ["fastpris", "fixed price", "fixedprice", "prix fixe", "festpreis", "precio fijo", "delbetaling", "milestone", "milepæl"]):
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
                        elif any(kw in _pl for kw in ["integrasjon", "onboarding", "tilbudsbrev", "offer letter", "carta de oferta", "funcionario", "integracao"]):
                            _task_checks = (
                                "TASK TYPE: Employee onboarding / offer letter.\n"
                                "Check these SPECIFIC things:\n"
                                "- Employee created/renamed with correct name from the PDF?\n"
                                "- Department assigned correctly (PUT /employee with department.id)?\n"
                                "- Employment details configured (percentage, annual salary, occupation code)?\n"
                                "- Standard work hours configured if task mentions it?\n"
                                "Do NOT require salary transaction for onboarding tasks — they only need employment setup.\n"
                            )
                        elif any(kw in _pl for kw in ["lønn", "salary", "paie", "salario", "gehalt", "payroll"]):
                            _task_checks = (
                                "TASK TYPE: Salary/payroll.\n"
                                "Check: employee name correct, employment exists, "
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
                        elif any(kw in _pl for kw in ["avdeling", "department", "abteilung", "d\xe9partement", "departamento"]):
                            _task_checks = (
                                "TASK TYPE: Department creation.\n"
                                "Check: all requested departments created with correct names and unique departmentNumbers.\n"
                            )
                        elif any(kw in _pl for kw in ["25%", "15%", "0%", "different vat", "multiple vat", "ulike mva", "verschiedene"]):
                            _task_checks = (
                                "TASK TYPE: Multi-VAT invoice.\n"
                                "Check these SPECIFIC things:\n"
                                "- Customer created with correct name/org?\n"
                                "- Each product has the CORRECT vatType matching its VAT rate (25%, 15%, 0%)?\n"
                                "- Invoice total correct (sum of each product×qty with its specific VAT)?\n"
                                "- Invoice sent if task says send?\n"
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
                            "4. AMOUNTS: If any created entity shows amount=0 or totalAmount=0 in the response, that is WRONG — the postings failed silently. "
                            "EXCEPTION: Supplier invoice (supplierInvoice) responses normally show amount=0 — this is OK.\n"
                            "5. BUDGET vs INVOICE: NEVER flag a mismatch between project budget and invoice total. "
                            "For fixed-price projects the invoice excl. VAT matches the fixedprice — this is CORRECT. "
                            "For other projects, invoice is based on actual work/costs. Either way, do NOT fail on budget vs invoice differences.\n\n"
                            "CRITICAL: Be CONSERVATIVE. When in doubt, say PASS.\n"
                            "Only say FAIL for CLEAR, OBJECTIVE, SPECIFIC errors (wrong math, completely missing step, wrong name).\n"
                            "Do NOT say FAIL for: vague concerns, 'might be wrong', uncertain issues, minor stylistic differences, "
                            "or steps you cannot confirm from the log (absence of evidence is NOT evidence of absence).\n"
                            "If the action log shows all main steps completed with status=ok, say PASS.\n\n"
                            "Reply ONLY with either:\n"
                            "- 'PASS' if everything looks correct or you are unsure\n"
                            "- 'FAIL: <specific issue>' ONLY if there is an obvious, objective error"
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
                # Auto-fix: block DELETE on employment (returns 405)
                if args["method"] == "DELETE" and "/employee/employment/" in args["path"]:
                    # Extract employee ID from path if possible
                    emp_id_hint = ""
                    for prev_msg in reversed(messages[-10:]):
                        if isinstance(prev_msg, dict) and prev_msg.get("role") == "tool":
                            try:
                                c = json.loads(prev_msg["content"])
                                vals = c.get("values", [])
                                if vals and "employeeId" in str(vals[0]) or "employee" in str(vals[0]):
                                    emp_id_hint = f" Use the existing employment."
                                    break
                            except Exception:
                                pass
                    err_msg = (f"DELETE on /employee/employment is NOT ALLOWED (405 Method Not Allowed).{emp_id_hint} "
                               "Instead, use the existing employment record: "
                               "GET /employee/employment?employeeId=X to find it, then proceed with that employment ID.")
                    print(f"    │  [fix] blocked DELETE /employee/employment — not allowed", flush=True)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps({"error": err_msg, "_status_code": 405}),
                    })
                    continue
                # Auto-fix: block POST /invoice/:createFromOrder — wrong endpoint
                if args["method"] == "POST" and "/:createFromOrder" in args["path"]:
                    err_msg = ("Wrong endpoint! /invoice/:createFromOrder does not exist. "
                               'Use POST /invoice with body: {"invoiceDate":"YYYY-MM-DD",'
                               '"invoiceDueDate":"YYYY-MM-DD","orders":[{"id":ORDER_ID}]}')
                    print(f"    │  [fix] blocked /:createFromOrder — wrong endpoint", flush=True)
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
                    if "isSupplier" not in req_body:
                        req_body["isSupplier"] = True
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
                        co_id = None
                        if companies:
                            co_id = companies[0].get("id")
                        if not co_id:
                            # Fallback: find companyId from employee data in previous messages
                            emp_id_for_div = (req_body.get("employee") or {}).get("id")
                            if emp_id_for_div:
                                emp_resp = call_tripletex(base_url, auth, "GET", f"/employee/{emp_id_for_div}", params={"fields": "companyId"})
                                co_id = (emp_resp.get("value") or {}).get("companyId")
                                if co_id:
                                    print(f"    │  [fix] employment: using employee companyId={co_id} as fallback", flush=True)
                        if co_id:
                            req_body["division"] = {"id": co_id}
                            print(f"    │  [fix] employment: added division {{id:{co_id}}}", flush=True)
                    except Exception as e:
                        print(f"    │  [fix] employment division auto-fix failed: {e}", flush=True)

                # Auto-fix: employment startDate — use 1st of month to avoid overlap with existing
                if (args["method"] == "POST" and args["path"].rstrip("/") == "/employee/employment"
                        and req_body):
                    sd = req_body.get("startDate", "")
                    # Force startDate to 1st of current month if not already
                    if sd and not sd.endswith("-01"):
                        new_sd = sd[:8] + "01"
                        req_body["startDate"] = new_sd
                        print(f"    │  [fix] employment startDate {sd} → {new_sd}", flush=True)
                    # Remove department field if present (not accepted)
                    if req_body.get("department"):
                        req_body.pop("department")
                        print(f"    │  [fix] removed department from employment POST", flush=True)

                # Auto-fix: POST /ledger/voucher — ensure each posting has 'date' and 'row'
                if (args["method"] == "POST" and args["path"].rstrip("/") == "/ledger/voucher"
                        and req_body):
                    postings = req_body.get("postings", [])
                    voucher_date = req_body.get("date", today)
                    for idx, p in enumerate(postings):
                        if "date" not in p:
                            p["date"] = voucher_date
                            print(f"    │  [fix] voucher posting[{idx}]: added date={voucher_date}", flush=True)
                        if "row" not in p or p["row"] == 0:
                            p["row"] = idx + 1
                            print(f"    │  [fix] voucher posting[{idx}]: set row={idx+1}", flush=True)
                        # Ensure amountCurrency matches amount if missing
                        if "amount" in p and "amountCurrency" not in p:
                            p["amountCurrency"] = p["amount"]
                            print(f"    │  [fix] voucher posting[{idx}]: added amountCurrency", flush=True)
                        # Strip accountingDimensionValue — not a valid field on voucher postings
                        if "accountingDimensionValue" in p:
                            p.pop("accountingDimensionValue")
                            print(f"    │  [fix] voucher posting[{idx}]: stripped accountingDimensionValue (not supported)", flush=True)

                # Auto-fix: POST /employee/employment/details — ensure employment ref + defaults
                if (args["method"] == "POST" and args["path"].rstrip("/") == "/employee/employment/details"
                        and req_body):
                    # If missing employmentType defaults (use INTEGER values — API rejects strings)
                    if "employmentType" not in req_body:
                        req_body["employmentType"] = 1  # ORDINARY
                        print(f"    │  [fix] employment details: added employmentType=1 (ORDINARY)", flush=True)
                    if "employmentForm" not in req_body:
                        req_body["employmentForm"] = 1  # PERMANENT
                        print(f"    │  [fix] employment details: added employmentForm=1 (PERMANENT)", flush=True)
                    if "remunerationType" not in req_body:
                        req_body["remunerationType"] = 1  # MONTHLY_PAY
                        print(f"    │  [fix] employment details: added remunerationType=1 (MONTHLY_PAY)", flush=True)
                    if "workingHoursScheme" not in req_body:
                        req_body["workingHoursScheme"] = 1  # NON_SHIFT
                        print(f"    │  [fix] employment details: added workingHoursScheme=1 (NON_SHIFT)", flush=True)

                # Auto-fix: employment details enum fields — convert strings to integers
                # Tripletex API requires integer values for these enums, but GET returns strings
                if (args["method"] in ("PUT", "POST")
                        and "/employee/employment/details" in args["path"] and req_body):
                    _enum_maps = {
                        "employmentType": {"NOT_CHOSEN": 0, "ORDINARY": 1, "MARITIME": 2, "FREELANCE": 3, "CREATIVE": 4, "OFFICER": 5},
                        "remunerationType": {"NOT_CHOSEN": 0, "MONTHLY_PAY": 1, "HOURLY_PAY": 2, "COMMISSIONED": 3, "FEE": 4, "PIECEWORK_PAY": 5},
                        "employmentForm": {"NOT_CHOSEN": 0, "PERMANENT": 1, "TEMPORARY": 2},
                        "workingHoursScheme": {"NOT_CHOSEN": 0, "NON_SHIFT": 1, "ROUND_THE_CLOCK": 2, "SHIFT_365": 3, "OFFSHORE_336": 4, "CONTINUOUS": 5, "OTHER_SHIFT": 6},
                    }
                    for field, mapping in _enum_maps.items():
                        val = req_body.get(field)
                        if isinstance(val, str):
                            upper_val = val.upper().replace(" ", "_")
                            if upper_val in mapping:
                                req_body[field] = mapping[upper_val]
                                print(f"    │  [fix] employment details {field}: '{val}' → {mapping[upper_val]}", flush=True)
                            else:
                                # Fuzzy match: ORDINARY_EMPLOYMENT → ORDINARY, ORDINARY_PERMANENT → PERMANENT
                                matched = False
                                for key, int_val in mapping.items():
                                    if key in upper_val or upper_val in key:
                                        req_body[field] = int_val
                                        print(f"    │  [fix] employment details {field}: '{val}' → {int_val} (fuzzy '{key}')", flush=True)
                                        matched = True
                                        break
                                if not matched:
                                    req_body[field] = 1  # Safe default
                                    print(f"    │  [fix] employment details {field}: '{val}' → 1 (default)", flush=True)

                # Auto-fix: POST /employee/standardTime — ensure fromDate
                if (args["method"] == "POST" and "/employee/standardTime" in args["path"] and req_body):
                    if "fromDate" not in req_body:
                        req_body["fromDate"] = today
                        print(f"    │  [fix] standardTime: added fromDate={today}", flush=True)

                # Auto-fix: POST /order — ensure orderDate and deliveryDate
                if (args["method"] == "POST" and args["path"].rstrip("/") == "/order" and req_body):
                    if "orderDate" not in req_body:
                        req_body["orderDate"] = today
                        print(f"    │  [fix] order: added orderDate={today}", flush=True)
                    if "deliveryDate" not in req_body:
                        req_body["deliveryDate"] = req_body.get("orderDate", today)
                        print(f"    │  [fix] order: added deliveryDate", flush=True)

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
                # Applies to /ledger/voucher AND /supplierInvoice (nested voucher.postings)
                _voucher_postings = None
                if args["method"] == "POST" and req_body:
                    if args["path"].rstrip("/") == "/ledger/voucher":
                        _voucher_postings = req_body.get("postings", [])
                    elif args["path"].rstrip("/") == "/supplierInvoice":
                        _voucher_postings = (req_body.get("voucher") or {}).get("postings", [])
                if _voucher_postings is not None:
                    if len(_voucher_postings) < 2:
                        violations.append(
                            "[voucher-min-postings] Voucher must have at least 2 postings "
                            "(one debit, one credit). A single posting will result in amount=0. "
                            "Add both a debit (positive) AND a credit (negative) posting that sum to zero."
                        )
                    elif _voucher_postings:
                        total = sum(p.get("amountGross", p.get("amount", 0)) for p in _voucher_postings)
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

                    # Auto-fix: employment overlap — GET existing employment and guide GPT
                    if (args["method"] == "POST" and args["path"].rstrip("/") == "/employee/employment"
                            and sc >= 400):
                        overlap_msg = "; ".join(m.get("message", "") for m in result.get("validationMessages", []))
                        emp_id_from_body = (req_body or {}).get("employee", {}).get("id")
                        if ("overlappende" in overlap_msg.lower() or "overlap" in overlap_msg.lower()) and emp_id_from_body:
                            print(f"    │  [auto-fix] employment overlap detected — fetching existing employment for employee {emp_id_from_body}", flush=True)
                            try:
                                emp_employment = call_tripletex(base_url, auth, "GET", "/employee/employment",
                                    params={"employeeId": emp_id_from_body, "fields": "*"})
                                existing_emps = emp_employment.get("values", [])
                                if existing_emps:
                                    existing = existing_emps[0]
                                    has_div = bool(existing.get("division", {}).get("id")) if existing.get("division") else False
                                    result["_employment_exists"] = True
                                    result["_existing_employment"] = existing
                                    result["_existing_has_division"] = has_div
                                    result["_hint"] = (
                                        f"Employee {emp_id_from_body} ALREADY HAS an employment record (id={existing.get('id')}). "
                                        f"Division set: {has_div}. "
                                        f"USE THIS EXISTING EMPLOYMENT — do NOT try to delete or recreate it. "
                                        f"Proceed directly to the next step (salary transaction, employment details, etc.) using employment id={existing.get('id')}."
                                    )
                                    print(f"    │  [auto-fix] existing employment id={existing.get('id')}, division={has_div}", flush=True)
                            except Exception as e:
                                print(f"    │  [auto-fix] employment lookup failed: {e}", flush=True)

                # Auto-fix: project manager access error — grant access and retry
                if (args["method"] == "POST" and args["path"].rstrip("/") == "/project"
                        and sc == 422 and result.get("validationMessages")):
                    pm_error = any("prosjektleder" in (m.get("message", "") or "").lower()
                                    for m in result.get("validationMessages", []))
                    if pm_error:
                        pm_id = (req_body or {}).get("projectManager", {}).get("id")
                        if pm_id:
                            result["_hint"] = (
                                f"The employee (id={pm_id}) does not have project manager access. "
                                f"Grant access first: PUT /employee/{pm_id} with body including "
                                f"'allowInformationRegistration': true. "
                                f"Then retry POST /project with the same data."
                            )
                            print(f"    │  [hint] project manager {pm_id} needs access — injected guidance", flush=True)

                # Auto-fix: division.id error on POST /employee/employment — try without division
                if (args["method"] == "POST" and args["path"].rstrip("/") == "/employee/employment"
                        and sc == 422 and req_body):
                    div_error = any("division" in (m.get("field", "") or "")
                                    for m in result.get("validationMessages", []))
                    if div_error and req_body.get("division"):
                        # Retry without division — some sandboxes don't support it
                        print(f"    │  [auto-fix] division.id rejected — retrying without division", flush=True)
                        retry_body = {k: v for k, v in req_body.items() if k != "division"}
                        retry_result = call_tripletex(base_url, auth, "POST", "/employee/employment", body=retry_body)
                        retry_sc = retry_result.get("_status_code", 200)
                        if retry_sc < 400:
                            result = retry_result
                            sc = retry_sc
                            print(f"    │  [auto-fix] employment created without division OK", flush=True)
                        else:
                            print(f"    │  [auto-fix] retry without division also failed: {retry_sc}", flush=True)

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


LOG_DIR = "/tmp/agent_logs"
os.makedirs(LOG_DIR, exist_ok=True)

# In-memory log store (survives for container lifetime, no concurrency issues)
_MEMORY_LOGS: dict[str, str] = {}


@app.get("/logs")
async def list_logs():
    """List log files from memory, GCS, GitHub, or local — merged and deduplicated."""
    all_logs: dict[str, dict] = {}
    # In-memory logs (always available for this container)
    for name, content in _MEMORY_LOGS.items():
        all_logs[name] = {"name": name, "size": len(content), "source": "memory"}
    # Local disk logs
    try:
        for f in os.listdir(LOG_DIR):
            if f.endswith(".log") and f not in all_logs:
                all_logs[f] = {"name": f, "size": os.path.getsize(os.path.join(LOG_DIR, f)), "source": "local"}
    except Exception:
        pass
    # GCS logs
    for item in list_gcs_logs():
        if item["name"] not in all_logs:
            all_logs[item["name"]] = {**item, "source": "gcs"}
    # GitHub logs
    if GITHUB_TOKEN:
        try:
            url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_LOG_PATH}"
            headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                for it in resp.json():
                    if it["name"].endswith(".log") and it["name"] not in all_logs:
                        all_logs[it["name"]] = {"name": it["name"], "size": it.get("size", 0), "source": "github"}
        except Exception:
            pass
    result = sorted(all_logs.values(), key=lambda x: x["name"], reverse=True)
    return JSONResponse(result)


@app.get("/logs/{filename}")
async def get_log(filename: str):
    """Download a specific log file from memory, GCS, GitHub, or local fallback."""
    import pathlib
    safe = pathlib.PurePosixPath(filename).name
    # Try in-memory first
    if safe in _MEMORY_LOGS:
        return PlainTextResponse(_MEMORY_LOGS[safe])
    # Try GCS first
    content = read_gcs_log(safe)
    if content:
        return PlainTextResponse(content)
    # Try GitHub
    if GITHUB_TOKEN:
        try:
            url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_LOG_PATH}/{safe}"
            headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                import base64 as b64
                gh_content = b64.b64decode(resp.json()["content"]).decode("utf-8")
                return PlainTextResponse(gh_content)
        except Exception:
            pass
    # Fallback to local
    path = os.path.join(LOG_DIR, safe)
    if not os.path.isfile(path):
        return JSONResponse({"error": "not found"}, status_code=404)
    with open(path, "r", encoding="utf-8") as f:
        return PlainTextResponse(f.read())


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
        ts_file = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + f"_{random.randint(1000, 9999)}"
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
        # Save to in-memory store (always works, fastest retrieval)
        _MEMORY_LOGS[log_filename] = log_text
        # Save locally (always works, survives GitHub failures)
        try:
            local_path = os.path.join(LOG_DIR, log_filename)
            with open(local_path, "w", encoding="utf-8") as lf:
                lf.write(log_text)
            print(f"  ✓ Log saved locally: {log_filename}", flush=True)
        except Exception as e:
            print(f"  ⚠ Local log save failed: {e}", flush=True)
        # Push to GCS (best-effort, no concurrency conflicts)
        try:
            push_log_to_gcs(log_text, log_filename)
        except Exception as e:
            print(f"  ⚠ GCS log push failed: {e}", flush=True)
        # Push to GitHub (with random initial delay to avoid concurrent commits)
        try:
            time.sleep(random.uniform(0.5, 3.0))  # Stagger pushes
            push_log_to_github(log_text, log_filename)
        except Exception as e:
            print(f"  ⚠ GitHub log push failed: {e}", flush=True)

    return JSONResponse({
        "status": "completed" if diag.get("done") else "incomplete",
        "iterations": diag.get("iterations", 0),
        "api_calls": diag.get("api_calls", []),
        "errors": diag.get("errors", []),
        "tokens": diag.get("tokens", 0),
    })
