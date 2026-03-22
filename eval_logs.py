import os, re
from collections import defaultdict

logs_dir = 'logs'
total = ok = fail = new_deploy = newest_deploy = 0
type_stats = defaultdict(lambda: {'ok':0,'fail':0,'iters':[],'new_iters':[]})
new_logs = []
old_logs = []

for fn in sorted(os.listdir(logs_dir)):
    if not fn.endswith('.log'): continue
    total += 1
    content = open(os.path.join(logs_dir, fn), encoding='utf-8').read()
    is_ok = '_ok_' in fn
    if is_ok: ok += 1
    else: fail += 1
    is_new = 'false FAIL causes' in content
    # Check for newest deploy (113 rules)
    is_newest = '113 total' in content or 'path cleanup' in content
    if is_new: new_deploy += 1
    if is_newest: newest_deploy += 1
    tm = re.search(r'_(task|invoice|payment|salary|project|employee|reverse|expense|note|credit|supplier_invoice)_', fn)
    tt = tm.group(1) if tm else 'unknown'
    im = re.search(r'_(\d+)iter_', fn)
    iters = int(im.group(1)) if im else 0
    type_stats[tt]['ok' if is_ok else 'fail'] += 1
    if is_ok:
        type_stats[tt]['iters'].append(iters)
        if is_new:
            type_stats[tt]['new_iters'].append(iters)
    entry = {'fn': fn, 'type': tt, 'iters': iters, 'ok': is_ok, 'new': is_new, 'newest': is_newest}
    if is_new:
        new_logs.append(entry)
    else:
        old_logs.append(entry)

print("=" * 70)
print(f"  TOTAL: {total} logs | {ok} OK | {fail} FAIL | {ok/total*100:.1f}% success")
print(f"  New deploy (verifier fix+): {new_deploy} logs, all OK: {all(l['ok'] for l in new_logs)}")
print(f"  Newest deploy (113 rules): {newest_deploy} logs")
print("=" * 70)

print("\n  PERFORMANCE BY TASK TYPE (OK logs only)")
print(f"  {'Type':>20s}  {'OK':>3s} {'Fail':>4s}  {'Avg':>5s} {'Min':>4s} {'Max':>4s}  |  {'NewAvg':>6s} {'NewN':>4s}")
print(f"  {'-'*20}  {'---':>3s} {'----':>4s}  {'-----':>5s} {'----':>4s} {'----':>4s}  |  {'------':>6s} {'----':>4s}")
for tt in sorted(type_stats.keys()):
    s = type_stats[tt]
    v = s['iters']
    nv = s['new_iters']
    avg = sum(v)/len(v) if v else 0
    mn = min(v) if v else 0
    mx = max(v) if v else 0
    navg = sum(nv)/len(nv) if nv else 0
    nn = len(nv)
    new_str = f"{navg:6.1f} {nn:4d}" if nn > 0 else "     -    -"
    print(f"  {tt:>20s}  {s['ok']:3d} {s['fail']:4d}  {avg:5.1f} {mn:4d} {mx:4d}  |  {new_str}")

# New deploy breakdown
print(f"\n  NEW DEPLOY LOGS ({len(new_logs)} total, all OK)")
for l in new_logs:
    marker = " *" if l['newest'] else ""
    print(f"    {l['iters']:2d} iters  {l['type']:>20s}  {l['fn']}{marker}")

# Check auto-fix usage in new deploy
print("\n  AUTO-FIX TRIGGERS (new deploy only)")
fix_patterns = {
    'employee_rename': r'renamed to .* — returning as created',
    'maritime_merge': r'maritime.*?merged|merging maritime',
    'shiftDuration': r'shiftDurationHours.*?35\.5|35\.5.*shift',
    'enum_fix': r'\[fix\] employment details.*?→',
    'invalid_date': r'\[fix\] invalid date',
    'field_rename': r'percentageOfFullTimeEquivalent',
    'division_retry': r'division.*?retry|retrying.*?division',
    'path_dot_strip': r'stripped trailing dot',
    'voucher_posting': r'\[fix\] voucher posting',
}
for name, pattern in fix_patterns.items():
    count = 0
    for l in new_logs:
        content = open(os.path.join(logs_dir, l['fn']), encoding='utf-8').read()
        if re.search(pattern, content, re.IGNORECASE):
            count += 1
    if count > 0:
        print(f"    {name}: {count}/{len(new_logs)} logs")

# Error patterns still happening
print("\n  REMAINING ERROR PATTERNS (new deploy)")
from collections import Counter
error_msgs = Counter()
for l in new_logs:
    content = open(os.path.join(logs_dir, l['fn']), encoding='utf-8').read()
    for m in re.finditer(r'"message"\s*:\s*"([^"]+)"', content):
        msg = m.group(1)
        if any(kw in msg.lower() for kw in ['error','ugyldig','invalid','feil','kan ikke','må ','feltet','verdien','not found']):
            clean = re.sub(r'\d+', 'N', msg)[:100]
            error_msgs[clean] += 1
if error_msgs:
    for msg, count in error_msgs.most_common(15):
        print(f"    {count:3d}x {msg}")
else:
    print("    None!")
