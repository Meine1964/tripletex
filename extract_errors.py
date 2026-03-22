"""Extract all unique API errors from log files."""
import os, re, json

logs_dir = "logs"
errors = {}
auto_fixes = {}
nudges = 0
for f in sorted(os.listdir(logs_dir)):
    if not f.endswith(".log"):
        continue
    text = open(os.path.join(logs_dir, f), encoding="utf-8").read()
    
    # Count nudges
    nudges += len(re.findall(r"NUDGE", text))
    
    # Extract validation errors
    for m in re.finditer(r'"validationMessages"\s*:\s*\[(.+?)\]', text):
        block = m.group(1)
        for vm in re.finditer(r'"field"\s*:\s*"([^"]*)"[^}]*"message"\s*:\s*"([^"]*)"', block):
            field = vm.group(1) or "(null)"
            msg = vm.group(2)[:100]
            key = f"{field}: {msg}"
            errors[key] = errors.get(key, 0) + 1
    
    # Extract auto-fixes
    for m in re.finditer(r'\[fix\]\s*(.+)', text):
        fix = m.group(1).strip()[:80]
        # Normalize numbers
        fix = re.sub(r'\d+', 'N', fix)
        auto_fixes[fix] = auto_fixes.get(fix, 0) + 1

print("=== ALL UNIQUE VALIDATION ERRORS ===")
for k, v in sorted(errors.items(), key=lambda x: -x[1]):
    print(f"  {v:3d}x  {k}")

print(f"\n=== AUTO-FIXES ({sum(auto_fixes.values())} total) ===")
for k, v in sorted(auto_fixes.items(), key=lambda x: -x[1]):
    print(f"  {v:3d}x  {k}")

print(f"\n=== NUDGES: {nudges} ===")
