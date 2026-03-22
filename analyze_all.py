import os, re
from collections import Counter

logs_dir = 'logs'
files = sorted([f for f in os.listdir(logs_dir) if f.endswith('.log')])
print(f'Total logs: {len(files)}')
ok = sum(1 for f in files if '_ok_' in f)
fail = sum(1 for f in files if '_fail_' in f)
print(f'OK: {ok}, FAIL: {fail}, Rate: {ok/len(files)*100:.1f}%')

error_patterns = Counter()
api_calls = Counter()
fix_patterns = Counter()
error_messages = Counter()
wasted_calls = []  # (file, method, path, error_msg)

for f in files:
    path = os.path.join(logs_dir, f)
    content = open(path, encoding='utf-8', errors='replace').read()
    
    for m in re.finditer(r'API (GET|POST|PUT|DELETE) (/[^\s]+)', content):
        method, endpoint = m.group(1), m.group(2)
        endpoint_norm = re.sub(r'/\d+', '/{id}', endpoint)
        api_calls[f'{method} {endpoint_norm}'] += 1
    
    for m in re.finditer(r'\[fix\] (.+)', content):
        fix_patterns[m.group(1).strip()[:80]] += 1
    
    for m in re.finditer(r'\[auto-fix\] (.+)', content):
        fix_patterns[f'[auto-fix] {m.group(1).strip()[:70]}'] += 1
    
    for m in re.finditer(r'(422|400|404|409) ERR.*?"message":\s*"([^"]+)"', content):
        code, msg = m.group(1), m.group(2)
        error_messages[f'{code}: {msg[:60]}'] += 1
    
    for m in re.finditer(r'"validationMessages":\s*\[([^\]]+)\]', content):
        for vm in re.finditer(r'"field":\s*"([^"]+)",\s*"message":\s*"([^"]+)"', m.group(1)):
            field, msg = vm.group(1), vm.group(2)
            error_patterns[f'{field}: {msg[:60]}'] += 1

    # Find tool_call -> error sequences for wasted call analysis
    tool_calls_in_log = re.findall(r'tripletex_api\((\{[^)]+\})\)', content)
    errors_in_log = re.findall(r'(422|400|404|409) ERR \([^)]+\) (\{.+?\})\n', content)

print()
print('=== TOP 25 VALIDATION ERRORS ===')
for pat, cnt in error_patterns.most_common(25):
    print(f'  {cnt:3d}x  {pat}')

print()
print('=== TOP 15 ERROR MESSAGES ===')
for msg, cnt in error_messages.most_common(15):
    print(f'  {cnt:3d}x  {msg}')

print()
print('=== TOP 25 [fix] TRIGGERS ===')
for pat, cnt in fix_patterns.most_common(25):
    print(f'  {cnt:3d}x  {pat}')

print()
print('=== TOP 25 API CALLS ===')
for call, cnt in api_calls.most_common(25):
    print(f'  {cnt:3d}x  {call}')
