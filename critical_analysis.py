"""Critical analysis: What exactly did each task produce? Focus on created entities and their fields."""
import os, re, json

logs_dir = 'logs'
# Focus on the most recent logs (likely the ones scoring 0)
recent = sorted([f for f in os.listdir(logs_dir) if f.endswith('.log') and f >= '20260321_195'])

for f in recent:
    content = open(os.path.join(logs_dir, f), encoding='utf-8', errors='replace').read()
    
    # Extract prompt
    pm = re.search(r'Prompt:\s*(.+)', content)
    prompt = pm.group(1)[:120] if pm else '?'
    
    # Extract task type
    m_type = re.search(r'_(\w+)_(ok|fail)_(\d+)iter_', f)
    task_type = m_type.group(1) if m_type else '?'
    status = m_type.group(2) if m_type else '?'
    iters = int(m_type.group(3)) if m_type else 0
    
    print(f"\n{'='*80}")
    print(f"FILE: {f}")
    print(f"TYPE: {task_type} | STATUS: {status} | ITERS: {iters}")
    print(f"PROMPT: {prompt}")
    
    # Find all successful POST/PUT operations and their responses
    # This shows what was actually CREATED
    created_entities = []
    for m in re.finditer(r'API (POST|PUT) (/[^\s]+)\n.*?body:\s*(\{.*?\})\n.*?(?:201|200) OK.*?response: (\{.*?\})\n', content, re.DOTALL):
        method, path, body_str, resp_str = m.group(1), m.group(2), m.group(3), m.group(4)
        try:
            resp = json.loads(resp_str)
            val = resp.get('value', resp)
            entity_id = val.get('id', '?')
            name = val.get('name', val.get('firstName', val.get('displayName', '')))
            created_entities.append(f"  {method} {path} → id={entity_id} name={name}")
        except:
            pass
    
    # Find all successful API calls with status
    successes = re.findall(r'API (POST|PUT) (/[^\s]+).*?\n.*?(?:201|200) OK', content, re.DOTALL)
    failures = re.findall(r'API (POST|PUT) (/[^\s]+).*?\n.*?(?:422|400|404) ERR', content, re.DOTALL)
    
    print(f"\nSUCCESSFUL POST/PUT: {len(successes)}")
    for s in successes:
        print(f"  ✓ {s[0]} {s[1]}")
    print(f"FAILED POST/PUT: {len(failures)}")
    for f2 in failures:
        print(f"  ✗ {f2[0]} {f2[1]}")
    
    # Check: did the agent call done()?
    done_calls = re.findall(r'done\(\)', content)
    print(f"DONE called: {'Yes' if done_calls else 'NO!'}")
    
    # Check verification result
    verify = re.search(r'Verification.*?:\s*(PASS|FAIL.*)', content)
    if verify:
        print(f"VERIFY: {verify.group(1)[:80]}")
    
    # Check final status line
    final = re.search(r'(DONE|FAIL|TIMEOUT).*?iterations.*?tokens.*?(\d+\.?\d*)s', content)
    if final:
        print(f"FINAL: {final.group(0)[:80]}")
    
    # For employee tasks: check if dateOfBirth was set correctly
    if 'employee' in task_type.lower() or 'employee' in prompt.lower():
        dob_matches = re.findall(r'"dateOfBirth":\s*"([^"]*)"', content)
        if dob_matches:
            print(f"DATE OF BIRTH values seen: {set(dob_matches)}")
    
    # For salary: check if salary transaction was created
    if 'salary' in task_type.lower():
        sal_tx = re.findall(r'POST /salary/transaction.*?(?:201|200) OK', content, re.DOTALL)
        voucher_created = re.findall(r'POST /ledger/voucher.*?(?:201|200) OK', content, re.DOTALL)
        print(f"SALARY TX created: {len(sal_tx)}, VOUCHER created: {len(voucher_created)}")
