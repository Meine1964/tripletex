import os, re
from collections import Counter

logs_dir = 'logs'

# SALARY task deep dive
print('=== SALARY TASK DEEP DIVE ===')
for fn in sorted(os.listdir(logs_dir)):
    if not fn.endswith('.log') or '_salary_' not in fn:
        continue
    content = open(os.path.join(logs_dir, fn), encoding='utf-8').read()
    im = re.search(r'_(\d+)iter_', fn)
    iters = int(im.group(1)) if im else 0
    ok = '_ok_' in fn
    status = 'OK' if ok else 'FAIL'

    calls = re.findall(r'((?:POST|PUT|GET|DELETE)\s+/[\w/:>.]+)', content)
    call_counts = Counter(calls)
    repeated = {k: v for k, v in call_counts.items() if v >= 2}

    print(f'\n--- {fn} ({iters} iters, {status}) ---')
    print(f'  Total calls: {len(calls)}')
    if repeated:
        print(f'  Repeated calls:')
        for k, v in sorted(repeated.items(), key=lambda x: -x[1]):
            print(f'    {v}x {k}')

# HIGH-ITERATION task deep dive (non-salary)
print('\n\n=== HIGH-ITER TASKS (>=15, non-salary) ===')
for fn in sorted(os.listdir(logs_dir)):
    if not fn.endswith('.log') or '_salary_' in fn:
        continue
    im = re.search(r'_(\d+)iter_', fn)
    iters = int(im.group(1)) if im else 0
    if iters < 15:
        continue
    content = open(os.path.join(logs_dir, fn), encoding='utf-8').read()
    ok = '_ok_' in fn
    status = 'OK' if ok else 'FAIL'

    calls = re.findall(r'((?:POST|PUT|GET|DELETE)\s+/[\w/:>.]+)', content)
    call_counts = Counter(calls)
    repeated = {k: v for k, v in call_counts.items() if v >= 2}

    # Find specific error messages
    error_msgs = re.findall(r'"message"\s*:\s*"([^"]+)"', content)
    unique_errors = set()
    for msg in error_msgs:
        if any(kw in msg.lower() for kw in ['error', 'ugyldig', 'invalid', 'feil', 'kan ikke', 'må ', 'feltet', 'verdien', 'not found']):
            unique_errors.add(msg[:100])

    print(f'\n--- {fn} ({iters} iters, {status}) ---')
    print(f'  Total calls: {len(calls)}')
    if repeated:
        print(f'  Repeated calls:')
        for k, v in sorted(repeated.items(), key=lambda x: -x[1]):
            print(f'    {v}x {k}')
    if unique_errors:
        print(f'  Unique errors:')
        for e in sorted(unique_errors):
            print(f'    - {e}')

# NEW DEPLOY iteration breakdown - every log
print('\n\n=== NEW DEPLOY - ALL LOGS DETAIL ===')
for fn in sorted(os.listdir(logs_dir)):
    if not fn.endswith('.log'):
        continue
    content = open(os.path.join(logs_dir, fn), encoding='utf-8').read()
    if 'false FAIL causes' not in content:
        continue
    im = re.search(r'_(\d+)iter_', fn)
    iters = int(im.group(1)) if im else 0
    calls = re.findall(r'((?:POST|PUT|GET|DELETE)\s+/[\w/:>.]+)', content)
    call_counts = Counter(calls)
    
    # Check for any 4xx
    has_4xx = bool(re.search(r'→ 4\d\d', content))
    
    # Count auto-fix triggers
    auto_fixes = []
    if re.search(r'renamed to .* — returning as created', content): auto_fixes.append('employee_rename')
    if re.search(r'maritime', content, re.I): auto_fixes.append('maritime')
    if re.search(r'shiftDurationHours', content): auto_fixes.append('shiftDuration')
    if re.search(r'enum.*?→|→.*?enum', content, re.I): auto_fixes.append('enum_fix')
    
    print(f'\n  {iters:2d} iters  {fn}')
    print(f'    Calls: {len(calls)}, Has 4xx: {has_4xx}, Auto-fixes: {auto_fixes or "none"}')
    for k, v in sorted(call_counts.items(), key=lambda x: -x[1])[:5]:
        if v >= 2:
            print(f'    {v}x {k}')
