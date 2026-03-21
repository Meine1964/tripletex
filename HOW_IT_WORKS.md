# How It Works — Tripletex AI Accounting Agent

This document explains how our solution for the NM i AI 2026 Tripletex challenge works, in plain language. It covers every part of the system, from the big picture down to the small tricks that make it reliable.

---

## The Challenge

The competition gives us an accounting task in natural language — things like "Create an invoice for customer X with products A and B, then send it by email" or "Register a salary payment for employee Y with a bonus." The task can be in Norwegian, English, Spanish, Portuguese, German, French, or Nynorsk.

Our agent receives the task, figures out what Tripletex API calls to make, executes them one by one, and signals "done" when the task is complete. We have 300 seconds per task.

---

## Architecture Overview

The system has four main parts:

```
┌─────────────────────────────────────────────────────────┐
│                   Competition Platform                   │
│         Sends task prompt + Tripletex credentials        │
└──────────────────────┬──────────────────────────────────┘
                       │ POST /solve
                       ▼
┌─────────────────────────────────────────────────────────┐
│               Our FastAPI Server (Cloud Run)             │
│                                                          │
│  1. Receive task                                         │
│  2. Pre-flight setup (bank account)                      │
│  3. Build system prompt with workflows                   │
│  4. Run agent loop (GPT-4o + tool calling)               │
│     ├── Auto-fixes (correct common mistakes)             │
│     ├── Validation rules (block bad calls)               │
│     └── Tripletex API proxy                              │
│  5. Post-execution verification (GPT-4o-mini)            │
│  6. Return result                                        │
└─────────────────────────────────────────────────────────┘
```

**Tech stack:** Python 3.12, FastAPI, OpenAI GPT-4o (main brain), GPT-4o-mini (verifier), deployed as a Docker container on Google Cloud Run.

---

## Step by Step: What Happens When a Task Arrives

### 1. Receiving the Task

The competition platform sends a POST request to our `/solve` endpoint with:
- A **prompt** (the task in natural language)
- Optional **files** (attachments, sometimes relevant)
- **Tripletex credentials** (a temporary API sandbox URL and session token)

We log everything and capture the task for our test suite.

### 2. Pre-flight Setup: Bank Account

Many tasks involve invoices, and Tripletex refuses to create invoices if the company's bank account isn't set up. So before the AI even starts thinking, we check:

- Is the task about invoices, payments, credit notes, or anything money-related? (We scan the prompt for keywords in all supported languages.)
- If yes, we automatically find ledger account 1920 ("Bankinnskudd") and set a valid Norwegian bank account number on it.

This prevents a common failure where the AI would create everything perfectly, then hit a wall at the invoice step.

### 3. The System Prompt

This is the brain of our solution — a large, carefully crafted instruction document sent to GPT-4o. It contains:

**Today's date** — injected dynamically so the AI never uses stale dates.

**General rules** — things like:
- Never call `done()` until the task is truly finished.
- Only make one API call at a time (no parallel calls).
- Search for existing data before creating duplicates.
- Always read the actual invoice amount from the API response instead of calculating it manually.

**Endpoint reference** — a mini-API-documentation for every Tripletex endpoint the AI might need: what fields are required, what format they expect, and what pitfalls to watch out for.

**Workflow guides** — step-by-step recipes for each task type:

| Workflow | What it covers |
|---|---|
| Invoice | Create products → customer → order → orderlines → invoice → optionally send |
| Credit Note | Search for existing invoice → create credit note from it |
| Payment | Find invoice → look up payment type → register payment |
| Reverse Payment | Find invoice → register negative payment to reverse |
| Salary/Payroll | Find employee → create employment → look up salary types → create transaction |
| Project | Create customer → create/rename employee → create project |
| Fixed-Price Milestone | Create project with fixed price → calculate ex-VAT amount → create milestone invoice |
| Travel Expense | Create expense → add cost lines → add per diem (with correct year's rate category) |
| Time Registration + Invoice | Register hours → create invoice from project |
| Supplier Invoice | Create supplier → look up VAT types and accounts → register incoming invoice |
| Voucher/Journal Entry | Look up accounts → create voucher with balanced postings |
| Customer/Supplier/Department | Create with required fields |

Each workflow is written with extreme specificity. For example, the milestone invoice workflow explicitly says "priceExcludingVatCurrency = milestoneAmount / 1.25" because without that, the AI consistently gets the VAT calculation wrong.

**Error recovery** — specific instructions for fixing common errors:
- Bank account errors → set it on the ledger account, not the company.
- VAT type errors → look up the correct ID, never hardcode.
- Action endpoint errors → use query params, not JSON body.

**Language support** — the prompt includes translations of key terms (e.g., "payment" = "betaling" = "Zahlung" = "pago" = "paiement") so the AI recognizes task types regardless of language.

### 4. The Agent Loop

The core of the system is a loop that runs up to 25 iterations:

```
for each iteration (max 25):
    1. Send conversation history to GPT-4o
    2. GPT-4o decides: call tripletex_api() or done()
    3. If tripletex_api():
       a. Apply auto-fixes to the request
       b. Check validation rules
       c. If rules pass: make the actual API call
       d. Feed result back to GPT-4o
    4. If done():
       a. Run post-execution verification
       b. If verification fails: send GPT-4o back to fix
       c. If verification passes: return success
```

GPT-4o has two tools available:
- **`tripletex_api(method, path, params, body)`** — make an API call
- **`done()`** — signal task completion

The AI decides which tool to call based on the conversation so far. Each API response gets added to the conversation, so the AI always has full context of what it's already done.

If the AI outputs text without calling any tool (which sometimes happens), we nudge it back: "You must either continue with the next API call or call done()."

### 5. Auto-Fixes

Between GPT-4o deciding what to call and the actual API call happening, we intercept the request and fix common mistakes. These are things the AI gets wrong often enough that we catch them programmatically:

| Auto-Fix | What it does |
|---|---|
| **Body field aliasing** | If GPT uses `requestBody`, `data`, `json_body`, or `payload` instead of `body`, we silently rename it. |
| **POST without body** | If GPT sends a POST with no body (except action endpoints), we reject it with a helpful error. |
| **Block /salary/specification** | This endpoint doesn't exist. GPT sometimes invents it. We block it and tell GPT to use inline specifications in /salary/transaction instead. |
| **isCustomer: true** | When creating a customer, GPT sometimes forgets this flag. We add it. |
| **Email propagation** | When creating customers or suppliers, we copy the email to `invoiceEmail` and `overdueNoticeEmail` if those aren't set. |
| **Employee PUT safety** | When updating an employee, we strip the `email` field (it's immutable and causes errors) and add `dateOfBirth` if missing (the API requires it). |
| **Supplier invoice field names** | Supplier invoice postings use `amountGross` (not `amount`). We auto-rename the fields and ensure rows start at 1. |
| **Per diem rate category** | The AI often picks a rate category from the wrong year (e.g., 2008 instead of 2026). We look up the travel expense date, find all rate categories, and swap in the correct one for that year. |
| **Employment division** | Creating an employment without a division causes salary transactions to fail later. We auto-look up the company and inject the division. This is critical because division can't be changed after creation. |
| **Milestone product pricing** | When creating a milestone invoice, the AI often sets the product price to the milestone amount (VAT-inclusive) instead of dividing by 1.25 (to get the ex-VAT price). We detect this by checking if the price is a known fraction (25%, 33%, 50%, 75%, 100%) of the tracked fixed-price project amount, and auto-correct it. |
| **Query params in path** | If GPT embeds query params in the URL path (like `/invoice?invoiceDateFrom=2000-01-01`), we extract them and move them to the params field. |
| **Action endpoint body → params** | Action endpoints like `/:payment`, `/:send`, `/:createCreditNote` use query params, not body. If GPT sends body on these, we move it to params. |
| **Employee auto-rename** | If POST /employee fails (sandbox doesn't allow creation), we track that the AI needs to rename an existing employee. If the AI then skips the rename and jumps to creating a project, we intercept and silently do the rename first. |

### 6. Validation Rules Engine

After auto-fixes and before the actual API call, every request is checked against 44 validation rules defined in `rules.yaml`. If any rule is violated, the call is **rejected** and never reaches Tripletex — the AI gets a clear error message telling it what to fix.

Rules cover things like:

**Required fields:**
- POST /project must have `name`, `number`, `projectManager.id`, and `startDate`
- POST /order must have `customer.id`
- POST /travelExpense/cost must have `travelExpense.id`, `costCategory.id`, `paymentType.id`, and `amountCurrencyIncVat`

**Forbidden fields:**
- PUT /employee must NOT include `email` (it's immutable)
- POST /employee/employment must NOT include `department` (doesn't exist on that endpoint)
- PUT /employee/employment must NOT include `division` (can't be changed)
- POST /project must NOT include `fixedPrice` (wrong casing — must be `fixedprice`)

**Field types:**
- Project `number` must be a string, not an integer
- Invoice `orders` must be an array
- Salary `payslips` must be an array

**Field formats:**
- `invoiceDate` and `startDate` must match `YYYY-MM-DD`

**Required query params:**
- GET /invoice must have `invoiceDateFrom` and `invoiceDateTo`
- PUT /invoice/{id}/:send must have `sendType`
- PUT /invoice/{id}/:payment must have `paymentDate`, `paymentTypeId`, `paidAmount`
- PUT /invoice/{id}/:createCreditNote must have `date`

**Action endpoint body rejection:**
- If `sendType` appears in the body of /:send, it's rejected (must be in params)
- Same for payment fields on /:payment and date on /:createCreditNote

The rules engine catches mistakes **before they cost us a 4xx error**. Every failed API call wastes time and hurts the score, so preventing bad calls is extremely valuable.

Each rule has a clear error message that tells the AI exactly what went wrong and how to fix it. For example:

> `[employment-require-division] POST /employee/employment requires division.id. Without it, salary transactions will fail with 'Arbeidsforholdet er ikke knyttet mot en virksomhet'. GET /company/>withLoginAccess to find the company ID and use it as division:{id}.`

### 7. State Tracking

The agent tracks certain state across iterations to make smarter decisions:

- **`tracked_fixedprice`** — When a fixed-price project is created, we remember the amount. This lets us detect and fix milestone pricing errors later.
- **`pending_employee_rename`** — When POST /employee fails, we save the desired name. If the AI forgets to do PUT /employee later, we catch it.
- **`employee_renamed`** — Tracks whether the rename happened, so we don't do it twice.
- **`diag._verified`** — Prevents the post-execution verification from running more than once.

### 8. Post-Execution Verification

When the AI calls `done()`, we don't immediately accept it. Instead, if there's more than 15 seconds remaining on the clock:

1. We build a compact action log from the entire conversation — every API call made and key response values (amounts, IDs, names).
2. We send this log, along with the original task, to **GPT-4o-mini** (a smaller, faster model).
3. GPT-4o-mini checks three things:
   - **Math:** Are all amounts, percentages, and VAT calculations correct?
   - **Completeness:** Were all steps in the task actually done?
   - **Data:** Do names, org numbers, and dates match the task?
4. If it returns "PASS" — we accept `done()` and finish.
5. If it returns "FAIL: [specific issue]" — we reject `done()`, feed the issue back to GPT-4o, and let it fix the problem.

This only runs once (to avoid loops), and we skip it if less than 15 seconds remain (safety margin). If the verification itself errors out, we fail-open (accept `done()` anyway).

### 9. Supplier Invoice Retry Guidance

When POST /supplierInvoice fails with a 422 validation error, we inject a hint into the response telling the AI to fix and retry the supplier invoice instead of falling through to POST /ledger/voucher. This is important because /ledger/voucher only creates a journal entry — it doesn't create a proper supplier invoice entity, which is what the task requires.

### 10. Task Capture for Test Suite

Every task we receive is automatically logged in a special format (`CASE_CAPTURE:....:END_CAPTURE`). Our `fetch_cases.py` script can later extract these from Cloud Run logs to build our test case library. This is how we collect real competition prompts for offline testing.

---

## Deployment

The solution is packaged as a Docker container:

```
FROM python:3.12-slim
├── main.py          (the entire agent — ~1360 lines)
├── rules.yaml       (44 validation rules)
└── requirements.txt (fastapi, uvicorn, requests, openai, python-dotenv, pyyaml)
```

It runs on **Google Cloud Run** in the `europe-north1` region, with:
- 1 GB memory
- 300-second timeout (matching the competition limit)
- Port 8080
- The OpenAI API key set as an environment variable

Deployment is done from Google Cloud Shell:
```
git pull → copy files → gcloud run deploy
```

---

## Test Infrastructure

We maintain a test suite in the `test_suite/` folder:

- **`cases/`** — 100+ real and synthetic test cases organized in subfolders:
  - `ai/` — hand-crafted edge-case prompts
  - `real/` — actual competition prompts captured from live submissions
  - `synthetic/` — generated diagnostic prompts
- **`logs/`** — execution logs from competition attempts, organized by day
- **`test_offline.py`** — 44 automated tests that validate rules, auto-fixes, system prompt content, and other logic **without making any API calls**
- **`fetch_cases.py`** — extracts captured task prompts from Cloud Run logs

---

## Key Design Decisions

### Why such a long system prompt?

The AI makes better decisions when it has specific, detailed instructions. A short prompt like "complete the Tripletex task" leads to constant mistakes — wrong field names, wrong endpoints, wrong sequence of operations. Our prompt is essentially a cheat sheet that prevents the most common errors.

### Why auto-fixes instead of just better prompting?

Some mistakes happen despite clear instructions. The AI "knows" it should divide by 1.25, but sometimes doesn't. Auto-fixes act as a safety net — they catch errors that slip through prompting. This dual approach (prompt guidance + programmatic correction) is more reliable than either alone.

### Why validation rules in YAML?

Rules in YAML are:
- Easy to add when we discover a new failure pattern
- Testable without running the full agent
- Self-documenting (each rule has an ID, description, and clear error message)
- Faster than natural-language instructions (rules fire instantly vs. hoping the AI remembers)

### Why a verification step?

The AI sometimes declares "done" prematurely — it thinks it finished, but actually missed a step or got a calculation wrong. The verifier catches these cases. It uses a cheaper, faster model (GPT-4o-mini) so it doesn't significantly impact our time budget.

### Why track state (fixedprice, employee rename)?

The LLM is stateless between messages — it only knows what's in the conversation. But some errors only become visible later: setting the wrong product price only matters when the invoice total is wrong, and the AI has already moved on. By tracking state ourselves, we can catch and fix these cross-step issues.

---

## What We Learned

1. **Every 4xx error costs time.** Preventing bad calls (via rules and auto-fixes) is far more efficient than letting them fail and having the AI retry.

2. **The AI is good at following workflows but bad at math.** VAT calculations, percentage splits, and amount handling are where most mistakes happen. Hard-coded auto-fixes work better than instructions for these.

3. **Real competition tasks are multilingual.** The system prompt needs to handle Norwegian (both bokmål and nynorsk), English, Spanish, Portuguese, German, and French. Keywords and workflows must cover all these languages.

4. **Division and employment must be set at creation time.** Some Tripletex fields are immutable after creation. The auto-fix for employment division was critical — without it, every salary task failed.

5. **Post-execution verification catches real issues.** About 10-20% of the time, the verifier catches a genuine problem that would have cost us points.

6. **Simple solutions scale.** The whole system is one Python file, one YAML file, and one Dockerfile. No databases, no message queues, no microservices. It's easy to understand, deploy, and debug.
