"""Test new rules against all existing logs to check for false positives."""
import os, re, yaml

# Load rules
data = yaml.safe_load(open('rules.yaml', encoding='utf-8'))
rules = data.get('rules', [])

NEW_RULE_IDS = {'activity-no-project', 'get-posting-dates', 'get-project-no-project-subfield'}
new_rules = [r for r in rules if r['id'] in NEW_RULE_IDS]
print(f"Testing {len(new_rules)} new rules against logs...")

def _field_exists(body, field):
    if not body or not isinstance(body, dict):
        return False
    if "." in field:
        parts = field.split(".", 1)
        return _field_exists(body.get(parts[0], {}), parts[1])
    return field in body

def check_rule(rule, method, path, body, params):
    """Check if a rule is violated."""
    r_method = rule.get("when", {}).get("method")
    r_path = rule.get("when", {}).get("path")
    r_pat = rule.get("when", {}).get("path_pattern")
    
    if r_method and r_method != method:
        return None
    
    clean_path = re.sub(r'/\d+', '/{id}', path)
    if r_path and clean_path != r_path and path != r_path:
        # Also try without {id} normalization
        if r_path not in (clean_path, path):
            return None
    
    if r_pat and not re.match(r_pat, path.rstrip("/")):
        return None
    
    if not r_path and not r_pat:
        return None
    
    rid = rule.get("id", "?")
    msg = rule.get("message", "").strip()
    
    for f in rule.get("reject_fields", []):
        if _field_exists(body, f):
            return f"[{rid}] forbidden field: {f}"
    
    for f in rule.get("require_params", []):
        if f not in params:
            return f"[{rid}] missing param: {f}"
    
    for p, forbidden in rule.get("reject_params_values", {}).items():
        val = params.get(p, "")
        if forbidden in str(val):
            return f"[{rid}] {p} contains '{forbidden}'"
    
    return None

# Parse API calls from logs
logs_dir = 'logs'
files = sorted([f for f in os.listdir(logs_dir) if f.endswith('.log')])
total_calls = 0
total_violations = 0
violations_by_rule = {}

for f in files:
    content = open(os.path.join(logs_dir, f), encoding='utf-8', errors='replace').read()
    
    # Extract tool calls
    for m in re.finditer(r'tripletex_api\((\{.*?\})\)', content):
        try:
            import json
            call_str = m.group(1)
            call = json.loads(call_str)
            method = call.get("method", "GET")
            path = call.get("path", "")
            body = call.get("body", {})
            params_from_call = call.get("params", {})
            
            # Also check for query params in path
            if "?" in path:
                path_parts = path.split("?", 1)
                path = path_parts[0]
                for param in path_parts[1].split("&"):
                    if "=" in param:
                        k, v = param.split("=", 1)
                        params_from_call[k] = v
            
            total_calls += 1
            
            for rule in new_rules:
                v = check_rule(rule, method, path, body, params_from_call)
                if v:
                    total_violations += 1
                    violations_by_rule.setdefault(rule['id'], []).append((f[:60], v))
        except Exception:
            pass

print(f"\nTotal API calls checked: {total_calls}")
print(f"Total violations found: {total_violations}")

if violations_by_rule:
    for rid, viols in violations_by_rule.items():
        print(f"\n  Rule '{rid}': {len(viols)} triggers")
        for fname, msg in viols[:5]:
            print(f"    {fname}")
            print(f"      {msg}")
        if len(viols) > 5:
            print(f"    ... and {len(viols)-5} more")
else:
    print("\n✓ No false positives — all new rules are safe!")
