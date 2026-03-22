import os, re, json
from collections import defaultdict, Counter

logs_dir = 'logs'
all_errors = []
total_logs = 0
ok_logs = 0
fail_logs = 0
task_types = Counter()
log_details = []  # (fn, iters, ok, task_type, errors_in_log)

for fn in sorted(os.listdir(logs_dir)):
    if not fn.endswith('.log'):
        continue
    total_logs += 1
    fp = os.path.join(logs_dir, fn)
    content = open(fp, encoding='utf-8').read()

    is_ok = '_ok_' in fn
    is_fail = '_fail_' in fn
    if is_ok:
        ok_logs += 1
    elif is_fail:
        fail_logs += 1

    # Extract task type
    tm = re.search(r'_(task|invoice|payment|salary|project|employee|reverse|expense|note|credit|supplier_invoice)_', fn)
    task_type = tm.group(1) if tm else 'unknown'
    task_types[task_type] += 1

    # Extract iteration count
    im = re.search(r'_(\d+)iter_', fn)
    iters = int(im.group(1)) if im else 0

    log_errors = []

    # Find error messages in responses
    for m in re.finditer(r'"message"\s*:\s*"([^"]+)"', content):
        msg = m.group(1)
        if any(kw in msg.lower() for kw in ['error', 'ugyldig', 'invalid', 'feil', 'kan ikke', 'må ', 'must', 'required', 'not found', 'eksisterer ikke', 'feltet', 'verdien']):
            log_errors.append(('msg', msg[:200]))
            all_errors.append((fn, 'msg', msg[:200]))

    # Find validation messages
    for m in re.finditer(r'"validationMessages"\s*:\s*\[([^\]]*)\]', content):
        try:
            arr = json.loads('[' + m.group(1) + ']')
            for vm in arr:
                if isinstance(vm, dict) and 'message' in vm:
                    log_errors.append(('validation', vm['message'][:200]))
                    all_errors.append((fn, 'validation', vm['message'][:200]))
        except:
            pass

    # Find HTTP 4xx status lines
    for m in re.finditer(r'→ (4\d\d)', content):
        log_errors.append(('http', m.group(1)))
        all_errors.append((fn, 'http', m.group(1)))

    log_details.append((fn, iters, is_ok, task_type, log_errors))

print(f'=== SUMMARY ===')
print(f'Total logs: {total_logs}, OK: {ok_logs}, Fail: {fail_logs}')
print(f'Success rate: {ok_logs/total_logs*100:.1f}%')
print(f'Task types: {dict(task_types)}')
print()

# Count error messages
error_msgs = Counter()
error_to_logs = defaultdict(set)
for fn, typ, msg in all_errors:
    if typ in ('msg', 'validation'):
        clean = re.sub(r'\d+', 'N', msg)[:120]
        error_msgs[clean] += 1
        error_to_logs[clean].add(fn)

print(f'=== TOP ERROR MESSAGES (by frequency) ===')
for msg, count in error_msgs.most_common(50):
    n_logs = len(error_to_logs[msg])
    print(f'  {count:3d}x ({n_logs:2d} logs) {msg}')

print()

# Find wasted iterations - where GPT retries the same failing call
print(f'=== LOGS WITH MOST 4xx ERRORS ===')
http_by_log = Counter()
for fn, typ, msg in all_errors:
    if typ == 'http':
        http_by_log[fn] += 1
for fn, count in http_by_log.most_common(15):
    detail = [d for d in log_details if d[0] == fn][0]
    status = 'OK' if detail[2] else 'FAIL'
    print(f'  {count:2d} errors in {detail[1]:2d} iters [{status}] {fn}')

print()

# Analyze iteration efficiency
print('=== ITERATION STATS BY TASK TYPE ===')
from collections import defaultdict
type_iters = defaultdict(list)
for fn, iters, is_ok, task_type, errors in log_details:
    if is_ok:
        type_iters[task_type].append(iters)
for tt in sorted(type_iters.keys()):
    vals = type_iters[tt]
    avg = sum(vals) / len(vals)
    print(f'  {tt:>20s}: avg={avg:.1f} iters, n={len(vals)}, min={min(vals)}, max={max(vals)}')

print()

# Find patterns in GPT tool calls that waste iterations
print('=== ENDPOINT ERROR PATTERNS ===')
endpoint_errors = Counter()
for fn in sorted(os.listdir(logs_dir)):
    if not fn.endswith('.log'):
        continue
    fp = os.path.join(logs_dir, fn)
    content = open(fp, encoding='utf-8').read()
    # Find tool_call followed by error
    for m in re.finditer(r'tool_call.*?(?:POST|PUT|GET|DELETE)\s+(/[\w/]+).*?→ (4\d\d)', content, re.DOTALL):
        endpoint = re.sub(r'/\d+', '/N', m.group(1))
        status = m.group(2)
        endpoint_errors[f'{status} {endpoint}'] += 1

for pattern, count in endpoint_errors.most_common(20):
    print(f'  {count:3d}x {pattern}')

print()

# Check for new deploy logs specifically
print('=== NEW DEPLOY LOGS ===')
new_deploy_logs = []
for fn in sorted(os.listdir(logs_dir)):
    if not fn.endswith('.log'):
        continue
    content = open(os.path.join(logs_dir, fn), encoding='utf-8').read()
    if 'false FAIL causes' in content:
        im = re.search(r'_(\d+)iter_', fn)
        iters = int(im.group(1)) if im else 0
        ok = '_ok_' in fn
        new_deploy_logs.append((fn, iters, ok))
        print(f'  {"OK" if ok else "FAIL":4s} {iters:2d} iters  {fn}')

print(f'\nNew deploy: {len(new_deploy_logs)} logs, {sum(1 for _,_,ok in new_deploy_logs if ok)} OK')

# Auto-fix effectiveness
print()
print('=== AUTO-FIX TRIGGERS IN LOGS ===')
fix_patterns = {
    'employee_rename': r'renamed to .* — returning as created',
    'maritime_merge': r'maritime.*?merged|merging maritime',
    'shiftDuration_fix': r'shiftDurationHours.*?35\.5|auto.?set.*?35\.5',
    'activityType_fix': r'activityType.*?auto|auto.*?PROJECT_GENERAL',
    'invalid_date_fix': r'invalid date|date.*?capped|auto.?fix.*?date',
    'employment_enum': r'enum.*?auto|employment.*?type.*?convert',
    'field_rename': r'percentageOfFullTimeEquivalent.*?renamed|renamed.*?percent',
    'division_retry': r'division\.id.*?retry|retrying.*?division',
}
for name, pattern in fix_patterns.items():
    count = 0
    for fn in sorted(os.listdir(logs_dir)):
        if not fn.endswith('.log'):
            continue
        content = open(os.path.join(logs_dir, fn), encoding='utf-8').read()
        if re.search(pattern, content, re.IGNORECASE):
            count += 1
    if count > 0:
        print(f'  {name}: triggered in {count} logs')
