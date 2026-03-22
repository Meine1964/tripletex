import os, re
from collections import Counter, defaultdict

logs_dir = 'logs'
files = sorted([f for f in os.listdir(logs_dir) if f.endswith('.log')])

# Pattern 1: invoiceDueDate wrong type
# Pattern 2: project field doesn't exist
# Pattern 3: dateFrom/dateTo null
# Pattern 4: email validation errors
# Pattern 5: POST without body (which endpoints)
# Pattern 6: GET /ledger/account repetitions per log
# Pattern 7: employee.dateOfBirth required
# Pattern 8: GET /ledger/posting individual calls per log
# Pattern 9: fields filter errors
# Pattern 10: blocked POST without body details

print("=== DETAILED PATTERN ANALYSIS ===\n")

for f in files:
    path = os.path.join(logs_dir, f)
    content = open(path, encoding='utf-8', errors='replace').read()
    shortname = f[:60]
    
    # Find invoiceDueDate errors - what was sent?
    if 'invoiceDueDate' in content and 'Verdien er ikke av korrekt type' in content:
        # Find the API call that caused it
        calls = re.findall(r'tripletex_api\((\{.*?invoiceDueDate.*?\})\)', content)
        for c in calls:
            print(f"[invoiceDueDate type error] {shortname}")
            # Extract the invoiceDueDate value
            m = re.search(r'"invoiceDueDate":\s*([^,}\s]+)', c)
            if m:
                print(f"  value sent: {m.group(1)}")

    # Find project field errors
    if 'project' in content and 'Feltet eksisterer ikke' in content:
        # What endpoint was called?
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if '"project"' in line and 'eksisterer ikke' in line:
                # Look back for the API call
                for j in range(max(0, i-10), i):
                    if 'API ' in lines[j]:
                        print(f"[project field doesn't exist] {shortname}")
                        print(f"  endpoint: {lines[j].strip()[:100]}")
                        break

    # Find email validation errors
    if '"email"' in content and 'validationMessages' in content:
        for m in re.finditer(r'"field":\s*"email",\s*"message":\s*"([^"]+)"', content):
            print(f"[email error] {shortname}")
            print(f"  message: {m.group(1)[:80]}")

    # Count GET /ledger/account calls per log
    acct_calls = len(re.findall(r'API GET /ledger/account', content))
    if acct_calls >= 5:
        iters = re.search(r'_(\d+)iter_', f)
        iter_count = iters.group(1) if iters else '?'
        print(f"[excessive /ledger/account] {shortname}")
        print(f"  {acct_calls}x GET /ledger/account in {iter_count} iterations")

    # Count GET /ledger/posting individual calls
    posting_calls = len(re.findall(r'API GET /ledger/posting/\d+', content))
    if posting_calls >= 3:
        print(f"[individual posting lookups] {shortname}")
        print(f"  {posting_calls}x GET /ledger/posting/{{id}}")

    # Fields filter errors
    if 'Illegal field in fields filter' in content:
        for m in re.finditer(r'Illegal field in fields filter: (\w+)', content):
            print(f"[illegal fields filter] {shortname}")
            print(f"  field: {m.group(1)}")
            # Find which endpoint
            lines = content.split('\n')
            for i, line in enumerate(lines):
                if 'Illegal field' in line:
                    for j in range(max(0, i-10), i):
                        if 'API ' in lines[j]:
                            print(f"  endpoint: {lines[j].strip()[:100]}")
                            break

    # dateFrom/dateTo null errors - which endpoints?
    if 'dateFrom' in content and 'Kan ikke' in content:
        for m in re.finditer(r'"field":\s*"dateFrom",\s*"message":\s*"Kan ikke v', content):
            lines = content.split('\n')
            for i, line in enumerate(lines):
                if 'dateFrom' in line and 'Kan ikke' in line:
                    for j in range(max(0, i-15), i):
                        if 'API ' in lines[j]:
                            print(f"[dateFrom null] {shortname}")
                            print(f"  endpoint: {lines[j].strip()[:100]}")
                            break
                    break

print("\n=== BLOCKED POST WITHOUT BODY BREAKDOWN ===")
blocked = Counter()
for f in files:
    content = open(os.path.join(logs_dir, f), encoding='utf-8', errors='replace').read()
    for m in re.finditer(r'\[fix\] blocked POST without body: (/\S+)', content):
        blocked[m.group(1)] += 1

for ep, cnt in blocked.most_common():
    print(f"  {cnt:3d}x  POST (no body) {ep}")

print("\n=== NEW DEPLOY ITERATION AVERAGES ===")
# Focus on logs from after commit 7ac02f3 (the latest deploy)
type_iters = defaultdict(list)
for f in files:
    if f >= '20260321_19':  # New deploy logs
        m = re.match(r'\d+_\d+_\d+_(\w+)_ok_(\d+)iter', f)
        if m:
            task_type, iters = m.group(1), int(m.group(2))
            type_iters[task_type].append(iters)

for t in sorted(type_iters, key=lambda x: sum(type_iters[x])/len(type_iters[x])):
    vals = type_iters[t]
    avg = sum(vals)/len(vals)
    print(f"  {t:20s} avg={avg:5.1f}  n={len(vals):2d}  vals={vals}")
