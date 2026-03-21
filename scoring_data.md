# NM i AI 2026 — Tripletex Agent Scoring Data

All times are CET (UTC+1). Log timestamps are UTC.
Agent version during all these runs: **unknown** (fixes from commits 7d017f1+ NOT deployed).

---

## Batch 1 — March 21, 2026 ~20:53–22:13 CET (logs 20:xx UTC)

| # | Score | Time  | Dur(s) | Log File | Task Type | Prompt (short) | Root Cause |
|---|-------|-------|--------|----------|-----------|----------------|------------|
| 1 | 0/10 | 20:53 | 69 | `20260321_200144_1376_reverse_ok_16iter` | year-end | Gjer forenkla årsoppgjer for 2025 (depreciation) | **amountGross bug** — voucher postings use `amount` (read-only), all amounts=0 |
| 2 | 0/8 | 20:55 | 36 | `20260321_200446_1523_task_ok_13iter` | year-end | Realize o encerramento anual simplificado de 2025 | **amountGross bug** |
| 3 | 8/8 | 20:58 | 44 | `20260321_200650_4811_reminder_fee_ok_18iter` | reminder_fee | En av kundene dine har en forfalt faktura, purregebyr 40 | OK ✅ |
| 4 | 0/10 | 20:59 | 156 | `20260321_200836_6989_payment_ok_9iter` | payment/agio | EUR 5044 invoice, Wechselkurs 10.51→11.20 NOK/EUR | **amountGross bug** + possibly wrong exchange calc |
| 5 | 0/10 | 21:03 | 108 | `20260321_201018_4064_invoice_ok_3iter` | supplier reg | Registrieren Sie den Lieferanten Brückentor GmbH | Score 0/10 but only supplier creation? Might be mismatched |
| 6 | 7/10 | 21:05 | 71 | `20260321_201130_9308_salary_ok_12iter` | salary/onboarding | Voce recebeu uma carta de oferta (PDF) | Partial — likely employment/onboarding details incomplete |
| 7 | 8/10 | 21:08 | 24 | `20260321_201159_1256_reverse_ok_4iter` | reverse payment | Die Zahlung von Nordlicht GmbH, Rechnung "Wartung" | Nearly perfect ✅ |
| 8 | 6/6 | 21:10 | 9 | `20260321_201422_2495_reverse_ok_14iter` | year-end | Perform simplified year-end closing for 2025 | Wait — 6/6 for year-end? Maybe different task type? Or scored before voucher step |
| 9 | 7/14 | 21:10 | 47 | `20260321_201545_8423_task_fail_25iter` | ledger correction | We have discovered errors in general ledger, 4 errors | **Hit 25-iter limit** — only 1 of 4 corrections made, agent spent all iterations on GETs |
| 10 | 8/8 | 21:11 | 26 | `20260321_201627_2056_credit_note_ok_3iter` | credit note | O cliente Cascata Lda reclamou sobre a fatura | OK ✅ |
| 11 | 0/10 | 21:12 | 104 | `20260321_201747_1109_salary_ok_15iter` | salary/onboarding | Sie haben einen Arbeitsvertrag erhalten (PDF) | **Employment creation failed** — dateOfBirth missing + division rejected |
| 12 | 0/10 | 21:14 | 70 | `20260321_201854_8622_salary_ok_11iter` | salary | Ejecute la nómina de María Rodríguez, salario base | **Employment creation failed** — dateOfBirth missing, salary/transaction rejected |
| 13 | 8/8 | 21:16 | 14 | `20260321_201925_1645_task_ok_4iter` | departments | Créez trois départements: Økonomi, Lager, IT | OK ✅ |
| 14 | 8/22 | 21:17 | 47 | `20260321_202029_4969_travel_expense_ok_11iter` | travel expense | Registrer en reiseregning, Konferanse Ålesund, 4 dager diett | Partial — per diem rate category issues |
| 15 | 0/8 | 21:17 | 70 | `20260321_204701_8824_salary_ok_14iter` | salary/year-end | Realize o encerramento mensal de março, reversão de acréscimos | **amountGross bug** (monthly close with voucher postings) |
| 16 | 7/7 | 21:19 | 12 | `20260321_204756_9052_project_ok_12iter` | project (analysis) | Die Gesamtkosten sind von Januar bis Februar gestiegen | OK ✅ — create projects for top 3 expense accounts |
| 17 | 4.5/8 | 21:19 | 46 | (no separate log — possibly between project batches) | ? | ? | Uncertain match |
| 18 | 4/10 | 21:46 | 48 | (may be from delayed scoring of earlier task) | ? | ? | Uncertain match |
| 19 | 3/10 | 21:47 | 44 | (may be from delayed scoring of earlier task) | ? | ? | Uncertain match |
| 20 | 2/8 | 22:13 | 46 | (may be from delayed scoring of earlier task) | ? | ? | Uncertain match |

### Batch 1 Summary
- **Perfect (8/8, 7/7, 6/6):** departments ×1, credit note ×1, reminder ×1, project analysis ×1, reverse payment ×1, year-end(?) ×1
- **Good (7/10, 8/10):** salary/onboarding, reverse payment
- **Partial (7/14, 8/22):** ledger correction (1/4 fixed), travel expense
- **Zero (0/10, 0/8):** year-end ×2 (amountGross), agio (amountGross), salary ×2 (dateOfBirth/employment), monthly close (amountGross)

---

## Batch 2 — March 21, 2026 ~22:16–22:53 CET (logs 21:xx UTC)

Scores listed oldest first (matching log chronological order):

| # | Score | Time  | Dur(s) | Log File | Task Type | Prompt (short) | Root Cause |
|---|-------|-------|--------|----------|-----------|----------------|------------|
| 1 | 8/8 | 22:16 | 8.1 | `20260321_211432_7484_payment_ok_10iter` | fixedprice+invoice | Sett fastpris 274950 kr, Nettbutikk-utvikling, Skogheim AS | **Scored 8/8!** But prev similar task scored 0. Maybe different checker variant? |
| 2 | 10/22 | 22:17 | 45.8 | `20260321_211546_2597_invoice_ok_22iter` | project cycle | Führen Sie den vollständigen Projektzyklus, Cloud-Migration Brückentor | Partial — **employee duplication** (both employees → same ID 18670543) |
| 3 | 8/8 | 22:17 | 14.8 | `20260321_211655_1029_task_ok_3iter` | customer creation | Crea el cliente Montaña SL, org 957430201, Nygata | OK ✅ |
| 4 | 0/8 | 22:23 | 37.0 | `20260321_211746_6047_employee_ok_17iter` | employee/onboarding | Du har mottatt en arbeidskontrakt (PDF), opprett ansatte | **Employment/salary setup failed** — likely dateOfBirth or employment issues |
| 5 | 10/10 | 22:24 | 22.4 | `20260321_211758_4831_payment_ok_4iter` | payment | O pagamento de Montanha Lda, fatura "Consultoria de dados" | OK ✅ |
| 6 | 10/10 | 22:26 | 21.2 | `20260321_212430_7517_supplier_invoice_ok_9iter` | supplier invoice | Vi har mottatt faktura INV-2026-7530 fra Stormberg AS | OK ✅ |
| 7 | 4/8 | 22:27 | 44.3 | `20260321_212507_9947_payment_ok_9iter` | payment/agio | Enviámos uma fatura de 13986 EUR, Porto Alegre Lda | Partial — **amountGross bug** on agio voucher |
| 8 | 2/8 | 22:29 | 48.5 | `20260321_212711_3038_payment_ok_7iter` | payment/agio | Enviamos una factura por 9487 EUR a Estrella SL | Partial — **amountGross bug** on agio voucher |
| 9 | 0/7 | 22:31 | 18.8 | `20260321_212802_6274_invoice_ok_11iter` | time reg + invoice | Registrer 30 timar for Gunnhild Eide, Rådgivning | **0/7** — timesheet + invoice task, likely employee/timesheet issues |
| 10 | 7/7 | 22:32 | 34.9 | `20260321_213042_9208_payment_ok_11iter` | fixedprice+invoice | Establezca un precio fijo de 457650 NOK, Implementación ERP, Solmar SL | OK ✅ — fixedprice task scored perfectly! |
| 11 | 8/8 | 22:32 | 66.2 | `20260321_213117_6626_project_ok_4iter` | project creation | Erstellen Sie das Projekt "Integration Bergwerk" | OK ✅ |
| 12 | 8/8 | 22:34 | 13.1 | `20260321_213233_6901_invoice_ok_9iter` | invoice | Create and send invoice, Northwave Ltd, 21050 NOK | OK ✅ |
| 13 | 2/10 | 22:35 | 59.1 | `20260321_213354_2552_invoice_ok_13iter` | time reg + invoice | Enregistrez 12 heures pour Louis Petit, Design | Partial — likely timesheet/hours issues |
| 14 | 7/7 | 22:44 | 38.5 | `20260321_213423_8742_credit_note_ok_3iter` | credit note | Kunden Havbris AS har reklamert, Programvarelisens 34xx | OK ✅ |
| 15 | 7/7 | 22:44 | 21.9 | `20260321_213615_5888_project_fail_25iter` | project (analysis) | Total costs increased significantly from January to February 2026 | Wait — 7/7 for a fail_25iter? The scoring might use different criteria |
| 16 | 5/10 | 22:48 | 40.0 | `20260321_214442_1453_invoice_ok_8iter` | invoice | Opprett og send en faktura til Polaris AS, 32600 kr ekskl MVA | Partial — half scored |
| 17 | 0/8 | 22:49 | 81.5 | `20260321_214514_6456_payment_ok_4iter` | payment | Der Kunde Sonnental GmbH, offene Rechnung 33500 NOK | **0/8** — simple payment should work? Needs investigation |
| 18 | 2/8 | 22:51 | 63.1 | `20260321_214919_1807_reminder_fee_ok_13iter` | reminder fee | One of your customers has an overdue invoice, reminder fee | Partial — reminder was 8/8 before, now 2/8? |
| 19 | 8/8 | 22:52 | 22.6 | `20260321_215040_6429_salary_ok_9iter` | salary | Exécutez la paie de Manon Durand | OK ✅ — salary worked! |
| 20 | 0/8 | 22:53 | 77.0 | `20260321_215215_2013_payment_ok_11iter` | fixedprice+invoice | Sett fastpris 365950 kr, Digital transformasjon, Fossekraft AS | **0/8** — fixedprice task failed |

Note: 2 additional logs exist but may not have been scored yet:
- `20260321_215300_3558_credit_note_ok_3iter` — credit note, Greenfield Ltd
- `20260321_215457_4529_task_ok_11iter` — salary (Køyr løn for Gunnhild Aasen)

### Batch 2 Summary
- **Perfect (10/10, 8/8, 7/7):** payment ×1, supplier invoice ×1, customer creation ×1, fixedprice ×1, project creation ×1, invoice ×1, credit note ×1, project analysis ×1, salary ×1
- **Partial (5/10, 4/8, 2/8, 2/10, 10/22):** project cycle (employee dup), agio ×2 (amountGross), time reg ×2, invoice, reminder
- **Zero (0/8, 0/7):** employee onboarding, time reg+invoice, payment, fixedprice

---

## Failure Pattern Analysis

### Category 1: amountGross Bug (FIXED in code, NOT deployed)
**Impact:** All voucher postings use read-only `amount` field → amounts=0 in Tripletex
**Affected tasks:** year-end closing, agio/exchange rate, monthly close, any manual voucher
**Expected improvement after deploy:** +6-8 tasks should go from 0→points
**Logs:** 200144, 200446, 200836, 204701, 212507, 212711

### Category 2: Employee dateOfBirth / Employment Chain (FIXED in commit 9d76eec, NOT deployed)
**Impact:** Employment creation fails → salary/payroll fails → 0 score
**Root cause chain:** POST /employee/employment → division.id rejected → retry without division → dateOfBirth missing → employment never created → salary/transaction rejected
**Fix:** Pre-check dateOfBirth before employment POST, auto-PUT if missing
**Affected tasks:** salary payroll, employee onboarding
**Logs:** 201747, 201854, 211746

### Category 3: Employee Duplication (FIXED in commit 9d76eec, NOT deployed)
**Impact:** Both employees in multi-employee tasks map to same ID → timesheet conflicts
**Root cause:** Sandbox has only 1 employee, auto-fix renames same employee twice
**Fix:** Track renamed employee IDs, skip already-renamed
**Affected tasks:** project cycle
**Logs:** 211546

### Category 4: Ledger Correction Iteration Budget (FIXED in commit 9d76eec, NOT deployed)
**Impact:** Agent spends 23+ iterations on GETs, only creates 1/4 corrections
**Root cause:** Agent fetches individual postings instead of using fields=*
**Fix:** Pre-scan all vouchers+postings+accounts before agent starts
**Affected tasks:** ledger correction / audit tasks
**Logs:** 201545

### Category 5: Fixedprice Milestone — Inconsistent Scoring
**Observation:** Same task type scores 0/8, 8/8, and 7/7 across different runs
**Possible causes:** API field visibility, VAT calculation variants, checker expectations
**Logs:** 211432 (8/8), 213042 (7/7), 215215 (0/8)

### Category 6: Time Registration + Invoice — Low Scores
**Observation:** 0/7 and 2/10 for timesheet+invoice tasks
**Possible causes:** Employee not properly set up for timesheet, activity not linked
**Logs:** 212802 (0/7), 213354 (2/10)

### Category 7: Payment Tasks — Inconsistent
**Observation:** Simple payment 0/8 (214514), other payments 10/10
**Needs investigation**

### Category 8: Reminder Fee — Degraded
**Observation:** Was 8/8 in batch 1, now 2/8 in batch 2
**Needs investigation**

---

## Score Distribution

### Batch 1 (20 tasks)
- Perfect: 6 tasks (30%)
- Good (>50%): 3 tasks (15%)
- Partial (25-50%): 2 tasks (10%)
- Zero: 6+ tasks (30%+)
- Uncertain: 3 tasks

### Batch 2 (20 tasks)
- Perfect: 9 tasks (45%)
- Good (>50%): 1 task (5%)
- Partial (25-50%): 4 tasks (20%)
- Zero: 4 tasks (20%)
- Low (<25%): 2 tasks (10%)

### Overall Trend
Batch 2 shows improvement in perfect scores (45% vs 30%) despite same un-deployed code.
This is likely due to task variant randomization — some task types are easier.

---

## Deploy Status
- **Commit 7d017f1:** amountGross fix, verifier disable, year-end workflow, keyword fix — PUSHED, NOT deployed
- **Commit 9d76eec:** dateOfBirth pre-check, employee dedup, ledger pre-scan — PUSHED, NOT deployed
- **Deploy blocked:** Cloud Shell VM must be woken via browser console
- **Deploy command:**
```
cd ~/tripletex-git && git pull && cp main.py rules.yaml requirements.txt ~/tripletex/ && cd ~/tripletex && git -C ~/tripletex-git rev-parse --short HEAD > VERSION && gcloud run deploy tripletex-agent --source . --region europe-north1 --allow-unauthenticated --set-env-vars "OPENAI_API_KEY=<KEY>,GITHUB_TOKEN=<TOKEN>,GCS_LOG_BUCKET=tripletex-agent-logs" --memory 1Gi --timeout 300 --port 8080 --project project-5fdef027-2cfc-4398-bd8
```
