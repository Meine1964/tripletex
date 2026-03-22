"""Test new rules against all existing logs - ensure 0 false positives."""
import os, re, json, yaml

with open('rules.yaml', 'r', encoding='utf-8') as f:
    data = yaml.safe_load(f)
rules = data['rules']

new_rule_ids = {
    'employment-details-put-employmentType-type',
    'employment-details-put-remunerationType-type',
    'employment-details-put-employmentForm-type',
    'employment-details-put-workingHoursScheme-type',
    'employment-details-put-no-percent',
    'get-supplierinvoice-dates',
    'activity-name-required',
    'standardtime-fromdate-format',
    'supplier-invoice-put-id-blocked',
    'project-no-manager',
    'project-manager-type',
}

new_rules = [r for r in rules if r['id'] in new_rule_ids]
print(f"Testing {len(new_rules)} new rules against all logs")

def check_rule_match(rule, method, path, params, body):
    when = rule.get('when', {})
    if when.get('method') and when['method'] != method:
        return None
    clean_path = path.rstrip("/")
    if when.get('path') and clean_path != when['path'].rstrip("/"):
        return None
    if when.get('path_pattern'):
        if not re.match(when['path_pattern'], clean_path):
            return None
    violations = []
    for field in rule.get('require_fields', []):
        if body is None:
            violations.append(f"missing field '{field}' (no body)")
            continue
        parts = field.split('.')
        obj = body
        found = True
        for part in parts:
            if isinstance(obj, dict) and part in obj:
                obj = obj[part]
            else:
                found = False
                break
        if not found:
            violations.append(f"missing required field '{field}'")
    for field in rule.get('reject_fields', []):
        if body is None:
            continue
        parts = field.split('.')
        obj = body
        found = True
        for part in parts:
            if isinstance(obj, dict) and part in obj:
                obj = obj[part]
            else:
                found = False
                break
        if found:
            violations.append(f"rejected field '{field}' present")
    for param in rule.get('require_params', []):
        if not params or param not in params:
            violations.append(f"missing required param '{param}'")
    for field, expected_type in rule.get('field_type', {}).items():
        if body and field in body:
            val = body[field]
            if expected_type == 'number' and not isinstance(val, (int, float)):
                violations.append(f"field '{field}' type mismatch")
            elif expected_type == 'string' and not isinstance(val, str):
                violations.append(f"field '{field}' type mismatch")
            elif expected_type == 'object' and not isinstance(val, dict):
                violations.append(f"field '{field}' type mismatch")
            elif expected_type == 'array' and not isinstance(val, list):
                violations.append(f"field '{field}' type mismatch")
    for field, pattern in rule.get('field_format', {}).items():
        if body and field in body:
            val = str(body[field])
            if not re.match(pattern, val):
                violations.append(f"field '{field}' format mismatch")
    return violations if violations else None

logs_dir = 'logs'
total_calls = 0
false_positives = []

for fn in sorted(os.listdir(logs_dir)):
    if not fn.endswith('.log'):
        continue
    content = open(os.path.join(logs_dir, fn), encoding='utf-8').read()
    lines = content.split('\n')

    i = 0
    while i < len(lines):
        line = lines[i]
        m_api = re.search(r'API (GET|POST|PUT|DELETE) (/\S+)', line)
        if m_api:
            method, path = m_api.group(1), m_api.group(2)
            body = None
            params = {}
            status_code = None
            # Look for body, params, status in nearby lines
            for j in range(max(i-3, 0), min(i+8, len(lines))):
                bm = re.search(r'body:\s*(\{.*\})', lines[j])
                if bm:
                    try:
                        body = json.loads(bm.group(1))
                    except:
                        pass
                pm = re.search(r'params:\s*(\{.*\})', lines[j])
                if pm:
                    try:
                        params = json.loads(pm.group(1))
                    except:
                        pass
                sm = re.search(r'(\d{3}) (OK|ERR)', lines[j])
                if sm:
                    status_code = int(sm.group(1))
                    break

            if status_code:
                total_calls += 1
                for rule in new_rules:
                    violations = check_rule_match(rule, method, path, params, body)
                    if violations and status_code < 400:
                        false_positives.append({
                            'log': fn,
                            'rule': rule['id'],
                            'method': method,
                            'path': path,
                            'status': status_code,
                            'violations': violations,
                        })
        i += 1

print(f"\nTotal API calls analyzed: {total_calls}")
print(f"FALSE POSITIVES: {len(false_positives)}")

if false_positives:
    print("\n=== FALSE POSITIVES ===")
    for fp in false_positives:
        print(f"  Rule: {fp['rule']}")
        print(f"  Log: {fp['log']}")
        print(f"  Call: {fp['method']} {fp['path']} -> {fp['status']}")
        print(f"  Violations: {fp['violations']}")
        print()
else:
    print("All new rules are safe - 0 false positives!")
