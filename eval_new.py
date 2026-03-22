"""Evaluate all logs with focus on new ones since last code push (0f1b5a5)."""
import os, re, json
from collections import Counter, defaultdict

logs_dir = 'logs'
files = sorted([f for f in os.listdir(logs_dir) if f.endswith('.log')])

# New logs: those after our code commit (timestamps >= 194416 on 20260321)
# The code push was 0f1b5a5, logs before that are on the pre-116-rules deploy
# Logs committed AFTER our push are still on OLD deploy (116 rules not yet deployed)
# But let's analyze all new deploy logs (from session start onwards: >= 20260321_19)

new_logs = [f for f in files if f >= '20260321_194416']  # logs since last evaluation

print(f"=== OVERALL STATS ===")
print(f"Total logs: {len(files)}")
ok = sum(1 for f in files if '_ok_' in f)
fail = sum(1 for f in files if '_fail_' in f)
print(f"OK: {ok}, FAIL: {fail}, Rate: {ok/len(files)*100:.1f}%")
print(f"\nNew logs since last eval: {len(new_logs)}")

print(f"\n=== NEW LOGS DETAIL ===")
for f in new_logs:
    path = os.path.join(logs_dir, f)
    content = open(path, encoding='utf-8', errors='replace').read()
    
    # Extract metadata
    m_type = re.search(r'_(\w+)_(ok|fail)_(\d+)iter_', f)
    task_type = m_type.group(1) if m_type else '?'
    status = m_type.group(2) if m_type else '?'
    iters = int(m_type.group(3)) if m_type else 0
    
    # Count API calls
    api_count = len(re.findall(r'API (GET|POST|PUT|DELETE) /', content))
    
    # Count errors
    err_422 = len(re.findall(r'422 ERR', content))
    err_400 = len(re.findall(r'400 ERR', content))
    err_404 = len(re.findall(r'404 ERR', content))
    
    # Count fix triggers
    fixes = len(re.findall(r'\[fix\]', content))
    autofixes = len(re.findall(r'\[auto-fix\]', content))
    
    # Extract prompt (first 80 chars)
    pm = re.search(r'Prompt:\s*(.+)', content)
    prompt = pm.group(1)[:70] if pm else '?'
    
    # Get token count
    tm = re.search(r'(\d+) iterations, (\d+) tokens', content)
    tokens = int(tm.group(2)) if tm else 0
    time_m = re.search(r'total (\d+\.?\d*)s', content)
    total_time = float(time_m.group(1)) if time_m else 0
    
    status_icon = '✓' if status == 'ok' else '✗'
    err_str = f"{err_422+err_400+err_404} errs" if (err_422+err_400+err_404) > 0 else "0 errs"
    fix_str = f"{fixes+autofixes} fixes" if (fixes+autofixes) > 0 else ""
    
    print(f"\n{status_icon} {task_type:20s} {iters:2d} iters | {api_count:2d} API | {err_str:8s} | {fix_str}")
    print(f"  {prompt}")
    if err_422 > 0 or err_400 > 0:
        # Show specific errors
        for em in re.finditer(r'(422|400) ERR.*?"message":\s*"([^"]+)"', content):
            code, msg = em.group(1), em.group(2)[:50]
            print(f"  [{code}] {msg}")

print(f"\n\n=== ITERATION AVERAGES BY TASK TYPE (ALL LOGS) ===")
type_iters = defaultdict(list)
for f in files:
    m = re.match(r'\d+_\d+_\d+_(\w+)_(ok|fail)_(\d+)iter', f)
    if m:
        task_type, status, iters = m.group(1), m.group(2), int(m.group(3))
        type_iters[task_type].append((iters, status))

for t in sorted(type_iters, key=lambda x: sum(i for i,s in type_iters[x])/len(type_iters[x])):
    vals = type_iters[t]
    ok_vals = [i for i,s in vals if s == 'ok']
    fail_vals = [i for i,s in vals if s == 'fail']
    avg = sum(i for i,_ in vals)/len(vals)
    ok_avg = sum(ok_vals)/len(ok_vals) if ok_vals else 0
    print(f"  {t:20s} avg={avg:5.1f}  ok_avg={ok_avg:5.1f}  n={len(vals):2d} (ok:{len(ok_vals)} fail:{len(fail_vals)})  vals={[i for i,_ in vals]}")

print(f"\n=== ERROR PATTERNS IN NEW LOGS ===")
all_errors = Counter()
for f in new_logs:
    content = open(os.path.join(logs_dir, f), encoding='utf-8', errors='replace').read()
    for m in re.finditer(r'"validationMessages":\s*\[([^\]]+)\]', content):
        for vm in re.finditer(r'"field":\s*"([^"]*)",\s*"message":\s*"([^"]+)"', m.group(1)):
            field, msg = vm.group(1), vm.group(2)
            all_errors[f'{field}: {msg[:50]}'] += 1

for pat, cnt in all_errors.most_common(15):
    print(f"  {cnt:3d}x  {pat}")

print(f"\n=== FIX TRIGGERS IN NEW LOGS ===")
all_fixes = Counter()
for f in new_logs:
    content = open(os.path.join(logs_dir, f), encoding='utf-8', errors='replace').read()
    for m in re.finditer(r'\[(fix|auto-fix)\] (.+)', content):
        all_fixes[f'[{m.group(1)}] {m.group(2).strip()[:70]}'] += 1

for pat, cnt in all_fixes.most_common(15):
    print(f"  {cnt:3d}x  {pat}")
