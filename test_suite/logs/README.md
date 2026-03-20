# Logs

Save Cloud Run logs here after each competition run.

## Naming convention
Use date + short description:
```
2026-03-20_invoice_run.txt
2026-03-20_all_tasks.txt
2026-03-21_v2_deploy.txt
```

## How to get logs
1. Go to GCP Console → Cloud Run → tripletex-agent → Logs
2. Filter to the time range of your run
3. Copy all log text → paste into a new `.txt` file here

## Extracting test cases
After saving logs here, run:
```bash
python test_suite/fetch_cases.py
```
It scans all `.txt` files in this directory and extracts new cases automatically.
