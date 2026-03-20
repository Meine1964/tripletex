import base64
import json
import os
import time
from datetime import datetime, timezone

import requests
import urllib3
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from openai import OpenAI

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
load_dotenv()

app = FastAPI()
client = OpenAI()

SYSTEM_PROMPT_TEMPLATE = """\
IMPORTANT — TODAY'S DATE IS {today}. Use {today} for all dates (invoiceDate, orderDate, deliveryDate, startDate, paymentDate, credit note date). NEVER use 2023 or 2024 or 2025 dates.

You are an AI accounting agent for Tripletex. You receive a task prompt (possibly in Norwegian, English, Spanish, Portuguese, German, French, or Nynorsk) and must complete it by calling the Tripletex API.

IMPORTANT: For most tasks (create invoice, customer, supplier, employee, project, department), the sandbox is FRESH and EMPTY — you must create everything from scratch.
HOWEVER: For credit note and payment tasks, the sandbox MAY ALREADY CONTAIN the relevant invoice, customer, and products. You MUST SEARCH for existing data first before creating anything!

ABSOLUTE RULES (never violate):
1. NEVER call done() unless the task is FULLY completed successfully. If you hit an error, FIX it and retry.
2. NEVER guess vatType IDs. Your VERY FIRST API call for invoice/product tasks MUST be GET /ledger/vatType. Wait for the result, find the correct id (for 25% outgoing: look for "Utgående" in name), THEN use that id. NEVER use id=3 or any hardcoded id. Wait for the vatType response BEFORE creating any product.
3. Only make ONE tool call at a time. NEVER make multiple tool calls in a single response. Always wait for the result before making the next call.
4. Create entities in dependency order: customer before order, product before orderline, order before invoice.
5. When the task says "send"/"senden"/"sende"/"enviar"/"envoyer" an invoice, you MUST also call PUT /invoice/{id}/:send with params={"sendType":"EMAIL"}.
6. Parse the prompt carefully. Extract ALL names, emails, org numbers, amounts, dates, currencies.
7. After completing ALL steps successfully, call the done tool.
8. Every 4xx error hurts efficiency. Look up data first when uncertain.
9. Reuse IDs from POST responses — do NOT re-fetch things you just created.
10. ALL action endpoints (path contains /:) use query PARAMS, not JSON body! Always use the "params" field for /:payment, /:send, /:createCreditNote, etc.

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
- GET/POST /customer — name, email, isCustomer:true, organizationNumber, phoneNumber
- PUT /customer/{id} — update customer
- GET/POST /supplier — name, email, isSupplier:true, organizationNumber, phoneNumber (SEPARATE endpoint from /customer!)
- PUT /supplier/{id} — update supplier
- GET/POST /product — name, number, priceExcludingVatCurrency, vatType:{id:X} (do NOT set costExcludingVatCurrency)
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
- GET/POST /department — name, departmentNumber (string)
- GET/POST/DELETE /travelExpense — employee:{id:X}, title, date
- GET/POST/DELETE /ledger/voucher — journal entries with postings
- GET /ledger/account — chart of accounts
- GET/POST /contact — firstName, lastName, email, customer:{id:X}
- GET /company/{id} — get company by ID. /company/0 may return 204 (empty), try /company/1 or higher
- GET /company/>withLoginAccess — list all accessible companies
- PUT /company — update company (no ID in path!). Include id + version in body. NOTE: bankAccountNumber is NOT on company — use PUT /ledger/account/{id} instead!
- GET /ledger/account?isBankAccount=true — find bank accounts. PUT /ledger/account/{id} to set bankAccountNumber.
- GET/POST /deliveryAddress — delivery addresses
- POST /incomingInvoice — [BETA] register a supplier/incoming invoice (voucherDate, supplier, invoiceNumber, amount, postings)
- GET/POST /ledger/voucher — journal entries with postings. POST requires JSON BODY (not params!): {date, description, postings:[...]}
- POST /ledger/accountingDimensionName — create a free/user-defined accounting dimension
- POST /ledger/accountingDimensionValue — create a value for a free accounting dimension
- GET /ledger/accountingDimensionName — list accounting dimension names

INVOICE WORKFLOW (follow EXACT order — do NOT skip steps):
0. Bank account is set up automatically before you start. If POST /invoice still fails with bank error:
   - GET /ledger/account?isBankAccount=true → find account 1920 "Bankinnskudd" (get id and version)
   - PUT /ledger/account/{id} with body {"id":X,"version":Y,"number":1920,"name":"Bankinnskudd","bankAccountNumber":"15030100112"}
   - Then retry POST /invoice
1. GET /ledger/vatType — MUST be your FIRST call! Find correct VAT type id. For 25% outgoing VAT: look for name containing "Utgående" with percentage=25. For exempt: look for number 6. Use the "id" field, NOT the "number" field.
2. POST /customer — name, isCustomer:true, organizationNumber (if given), email (if given)
3. POST /product — name, priceExcludingVatCurrency, vatType:{id: from step 1}
4. POST /order — customer:{id: from step 2}, orderDate, deliveryDate (use invoiceDate or today)
5. POST /order/orderline — order:{id: from step 4}, product:{id: from step 3}, count:1
6. POST /invoice — invoiceDate, invoiceDueDate (14 days after invoiceDate), orders:[{id: from step 4}]
7. If task says "send": PUT /invoice/{id from step 6}/:send with params={"sendType":"EMAIL"} (use params, NOT body!)
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
2. Find the matching invoice by customer name or amount.
3. GET /invoice/paymentType — find payment type (use the first one, typically "Kontant")
4. PUT /invoice/{id}/:payment — use params (NOT body!) with: paymentDate, paymentTypeId, paidAmount, paidAmountCurrency
   IMPORTANT: paidAmount and paidAmountCurrency must be the TOTAL INCLUDING VAT, not the ex-VAT amount!
   If the task says "19250 NOK excl. VAT" with 25% VAT, the payment amount is 19250 * 1.25 = 24062.5
5. If no existing invoice found, create the full invoice chain first, then register payment.
   CRITICAL: All action endpoints (/:payment, /:send, /:createCreditNote, /:createReminder) use query PARAMS, not JSON body!
   Example: tripletex_api(method="PUT", path="/invoice/123/:payment", params={"paymentDate":"{today}","paymentTypeId":1,"paidAmount":1000,"paidAmountCurrency":1000})

REVERSE PAYMENT WORKFLOW (for "reverse" / "revert" / "devuelto" / "tilbakefør" / "stornieren" / "annuler" payment tasks):
1. Search for the invoice: GET /invoice with params={"invoiceDateFrom":"2000-01-01","invoiceDateTo":"2030-12-31"}
2. GET /invoice/paymentType — find payment type id
3. Register a NEGATIVE payment to reverse: PUT /invoice/{id}/:payment with params:
   - paymentDate: {today}
   - paymentTypeId: (from step 2)
   - paidAmount: NEGATIVE total including VAT (e.g. if invoice total is 24062.5, use -24062.5)
   - paidAmountCurrency: same negative amount
   IMPORTANT: Use the invoice's "amount" or "amountCurrency" field (total incl. VAT) as the basis, then NEGATE it.
   The amount from the task prompt is usually ex-VAT. Multiply by 1.25 for 25% VAT, then negate.

SALARY / PAYROLL WORKFLOW (for "paie"/"lønn"/"salary"/"Gehalt"/"salario"/"lön"/"payroll" tasks):
The task will ask you to run payroll for an employee with a base salary and possibly a bonus or other additions.

Step 1: Find the employee.
  GET /employee?fields=* — look for the employee by name/email. The employee usually already exists.
  If not found, create with POST /employee (firstName, lastName, email).

Step 2: Look up salary types.
  GET /salary/type — returns available salary types. Key types:
  - number "2000" = "Fastlønn" (Fixed salary / base salary)
  - number "2002" = "Bonus"
  Note the "id" of each needed type (IDs vary per sandbox).

Step 3: Create the salary transaction with payslip and specifications.
  POST /salary/transaction with body:
  {
    "year": {today_year},
    "month": CURRENT_MONTH_NUMBER,
    "payslips": [{
      "employee": {"id": EMPLOYEE_ID},
      "specifications": [
        {"salaryType": {"id": FASTLONN_TYPE_ID}, "rate": BASE_SALARY_AMOUNT, "count": 1},
        {"salaryType": {"id": BONUS_TYPE_ID}, "rate": BONUS_AMOUNT, "count": 1}
      ]
    }]
  }
  - "month" is the current month (1-12) from today's date.
  - "rate" is the salary amount (e.g. 33900 for base salary).
  - "count" is always 1 for monthly salary/bonus.
  - Only include bonus specification if the task mentions a bonus.

Step 4: If the inline specifications approach fails with "field doesn't exist" for specifications:
  a. POST /salary/transaction with just: {"year": Y, "month": M, "payslips": [{"employee": {"id": EMP_ID}}]}
     This creates a payslip. Get the payslip id from response: value.payslips[0].id
  b. POST /salary/specification for each salary line:
     {"payslip": {"id": PAYSLIP_ID}, "salaryType": {"id": TYPE_ID}, "rate": AMOUNT, "count": 1}

Step 5: Call done() when complete.

IMPORTANT: "month" in the salary transaction = the NUMERIC month from today's date ({today}).
For base salary, use salary type with number "2000" (Fastlønn).
For bonus, use salary type with number "2002" (Bonus).

EMPLOYEE WORKFLOW:
- POST /employee with firstName, lastName, email
- If you get error about "Brukertype" (user type), you MUST do ALL of these steps:
  1. GET /employee?fields=* to find the existing admin employee (get their id and version)
  2. PUT /employee/{id} with body {id, version, firstName:"REQUIRED_FIRST_NAME", lastName:"REQUIRED_LAST_NAME", email:"REQUIRED_EMAIL"} — you MUST set the names and email from the task!
  3. Use that employee's id for the project manager or other references
  CRITICAL: The admin employee will NOT already have the right name! You MUST ALWAYS update firstName, lastName, and email with PUT. Never assume it matches — it never does.

PROJECT WORKFLOW:
- Step 1: Create or find the customer first (POST /customer)
- Step 2: Create the project manager employee (POST /employee). If that fails with "Brukertype" error:
  a. GET /employee?fields=* to find the admin
  b. PUT /employee/{adminId} to update firstName, lastName, email to match the required project manager
- Step 3: POST /project with ALL of these fields:
  * name (required)
  * number (string like "1" or "P001", required)
  * projectManager:{id:X} (required)
  * startDate (YYYY-MM-DD, required! Use today's date if not specified)
  * customer:{id:X} (REQUIRED if the task mentions the project is linked/connected to a customer!)
  * endDate (if given)
- CRITICAL: If the task says project is linked/connected/associated with a customer, you MUST include customer:{id:X}.
- CRITICAL: startDate is REQUIRED. Always include it.

TRAVEL EXPENSE WORKFLOW:
- GET /employee or create one
- POST /travelExpense with employee:{id:X}, title, date

SUPPLIER WORKFLOW:
- IMPORTANT: Use POST /supplier (NOT POST /customer with isSupplier:true!)
- POST /supplier with: name, organizationNumber, email, phoneNumber (if given)
- Supplier and customer are SEPARATE endpoints in Tripletex.
- "Lieferant" (German) = "leverandør" (Norwegian) = "fournisseur" (French) = "proveedor" (Spanish) = "fornecedor" (Portuguese) = supplier

CUSTOMER WORKFLOW:
- POST /customer with: name, isCustomer:true, organizationNumber, email, phoneNumber (if given)
- "Kunde" (German/Norwegian) = "client" (French) = "cliente" (Spanish/Portuguese) = customer

DEPARTMENT WORKFLOW:
- POST /department with name, departmentNumber (string)

SUPPLIER INVOICE / INCOMING INVOICE WORKFLOW (for "supplier invoice"/"leverandørfaktura"/"Eingangsrechnung"/"facture fournisseur"/"factura proveedor"/"incoming invoice"/"received invoice" tasks):
The task asks you to register an invoice RECEIVED FROM a supplier (not an outgoing invoice to a customer).

Step 1: Create the supplier.
  POST /supplier with: name, organizationNumber (if given), email (if given), phoneNumber (if given)
  NOTE: Use /supplier NOT /customer! Suppliers are separate entities.

Step 2: Look up VAT types.
  GET /ledger/vatType — find the INCOMING/INPUT VAT type for the given percentage.
  For 25% input VAT: look for name containing "Inngående" (incoming) with percentage=25.
  Note the "id" — do NOT use the "number" field.

Step 3: Calculate amounts.
  If the task says "65850 NOK including VAT" with 25% VAT:
  - Total incl. VAT = 65850
  - VAT amount = 65850 / 1.25 * 0.25 = 13170
  - Amount excl. VAT = 65850 - 13170 = 52680

Step 4: Create a ledger voucher to register the supplier invoice.
  POST /ledger/voucher with JSON BODY (NOT params!):
  {
    "date": "{today}",
    "description": "Supplier invoice INVOICE_NUMBER from SUPPLIER_NAME",
    "postings": [
      {
        "debit": {"account": {"number": EXPENSE_ACCOUNT_NUMBER}, "amount": AMOUNT_EXCL_VAT},
        "credit": {"account": {"number": 2400}, "amount": TOTAL_INCL_VAT}
      }
    ]
  }
  NOTE: The exact posting structure may vary. If 422 errors occur, try alternative structures:
  - Use "account" with {"id": X} instead of {"number": X}
  - First GET /ledger/account to find the account IDs for the given account numbers
  - Try flat postings: one debit posting for expense account, one debit posting for VAT (account 2710), one credit posting for supplier (account 2400)

Step 5: If POST /ledger/voucher fails, try POST /incomingInvoice [BETA endpoint]:
  POST /incomingInvoice with body:
  {
    "voucherDate": "{today}",
    "supplier": {"id": SUPPLIER_ID},
    "invoiceNumber": "INVOICE_NUMBER",
    "amount": TOTAL_INCL_VAT,
    "amountCurrency": TOTAL_INCL_VAT
  }
  If that also fails, try different field combinations based on error messages.

IMPORTANT: POST /ledger/voucher uses JSON BODY, not query params! Use the "body" field.
"Register supplier invoice" / "Eingangsrechnung" / "facture reçue" = incoming invoice, NOT outgoing.

LEDGER VOUCHER / JOURNAL ENTRY WORKFLOW (for "voucher"/"bilag"/"Buchung"/"écriture comptable"/"asiento" tasks):
The task asks you to create a journal entry / voucher with specific postings.

IMPORTANT: POST /ledger/voucher requires a JSON BODY, NOT query params!
Always use the "body" field, NEVER the "params" field for this endpoint.

Step 1: If the task involves accounting dimensions (e.g. "Produktlinje", "Avdeling", custom categories):
  a. Create the dimension name: POST /ledger/accountingDimensionName with body: {"name": "DIMENSION_NAME"}
  b. Create dimension values: POST /ledger/accountingDimensionValue with body: {"name": "VALUE_NAME", "accountingDimensionName": {"id": DIMENSION_ID}}
  c. Repeat for each value.

Step 2: Look up ledger accounts if needed.
  GET /ledger/account — find account IDs for the account numbers mentioned in the task.
  GET /ledger/vatType — if VAT is involved.

Step 3: Create the voucher.
  POST /ledger/voucher with BODY (not params!):
  {
    "date": "{today}",
    "description": "Description of the journal entry",
    "postings": [
      {"amount": DEBIT_AMOUNT, "amountCurrency": DEBIT_AMOUNT, "account": {"id": DEBIT_ACCOUNT_ID}},
      {"amount": -CREDIT_AMOUNT, "amountCurrency": -CREDIT_AMOUNT, "account": {"id": CREDIT_ACCOUNT_ID}}
    ]
  }
  NOTE: Debit = positive amount, Credit = negative amount. Postings must balance (sum to zero).
  If using account NUMBER instead of ID: {"account": {"number": 6340}}
  If the task specifies a dimension, add to each posting: "accountingDimensionValue": {"id": VALUE_ID}

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


def _fmt(d, max_len: int = 300) -> str:
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
            err = json.dumps(data, ensure_ascii=False)[:600]
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


def run_agent(prompt: str, files: list, base_url: str, auth: tuple) -> None:
    agent_start = time.time()

    # Pre-check: set bank account for invoice-related tasks (also credit note and payment tasks need invoices)
    prompt_lower = prompt.lower()
    invoice_keywords = ["faktura", "invoice", "rechnung", "factura", "facture", "fatura",
                        "credit", "kredit", "gutschrift", "nota de crédito",
                        "payment", "betaling", "zahlung", "pago", "pagamento", "paiement",
                        "reverse", "revert", "devuelto", "tilbakefør", "stornieren", "annuler"]
    if any(kw in prompt_lower for kw in invoice_keywords):
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
                return

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

        if not msg.tool_calls:
            print(f"  ✗ No tool calls — LLM stopped. Elapsed: {time.time()-agent_start:.1f}s", flush=True)
            break

        print(f"  Tool calls ({len(msg.tool_calls)}):", flush=True)
        for i, tc in enumerate(msg.tool_calls):
            if tc.function.name == "done":
                print(f"    [{i+1}] done()", flush=True)
            else:
                args_preview = tc.function.arguments[:200]
                print(f"    [{i+1}] {tc.function.name}({args_preview})", flush=True)

        for tool_call in msg.tool_calls:
            name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)

            if name == "done":
                elapsed = time.time() - agent_start
                print(f"\n  ✓ DONE — {iteration+1} iterations, {total_tokens} tokens, {elapsed:.1f}s", flush=True)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": "Task marked as completed.",
                })
                return

            if name == "tripletex_api":
                result = call_tripletex(
                    base_url, auth,
                    method=args["method"],
                    path=args["path"],
                    params=args.get("params"),
                    body=args.get("body"),
                )
                result_str = json.dumps(result, ensure_ascii=False)
                # Log response data (truncated for readability)
                preview = result_str[:400] + "…" if len(result_str) > 400 else result_str
                print(f"    │  response: {preview}", flush=True)
                if len(result_str) > 8000:
                    result_str = result_str[:8000] + "...(truncated)"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_str,
                })

        print(f"  Iteration {iteration+1} took {time.time()-iter_start:.1f}s", flush=True)

    print(f"\n  ⚠ Max iterations reached. Elapsed: {time.time()-agent_start:.1f}s, Tokens: {total_tokens}", flush=True)


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

    print(f"\n{'='*70}", flush=True)
    print(f"  NEW TASK RECEIVED", flush=True)
    print(f"{'='*70}", flush=True)
    print(f"  Prompt: {prompt[:500]}{'…' if len(prompt)>500 else ''}", flush=True)
    print(f"  Files:  {len(files)}", flush=True)
    print(f"  URL:    {base_url}", flush=True)
    if files:
        for f in files:
            print(f"    - {f.get('filename', '?')} ({f.get('mime_type', '?')})", flush=True)
    print(f"{'─'*70}", flush=True)

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
    try:
        run_agent(prompt, files, base_url, auth)
    except Exception as e:
        import traceback
        print(f"  ✗ AGENT ERROR: {e}", flush=True)
        traceback.print_exc()

    print(f"\n{'='*70}", flush=True)
    print(f"  TASK COMPLETE — total {time.time()-t0:.1f}s", flush=True)
    print(f"{'='*70}\n", flush=True)
    return JSONResponse({"status": "completed"})
