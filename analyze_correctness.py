"""Deep analysis: What are tasks ACTUALLY achieving vs what they should?
Focus on correctness issues that would cause 0/10 scores."""
import os, re, json
from collections import defaultdict

logs_dir = 'logs'
files = sorted([f for f in os.listdir(logs_dir) if f.endswith('.log')])

print(f"=== CRITICAL CORRECTNESS ANALYSIS ===\n")

for f in files:
    path = os.path.join(logs_dir, f)
    content = open(path, encoding='utf-8', errors='replace').read()
    
    # Extract task type and prompt
    m_type = re.search(r'_(\w+)_(ok|fail)_(\d+)iter_', f)
    if not m_type:
        continue
    task_type = m_type.group(1)
    status = m_type.group(2)
    iters = int(m_type.group(3))
    
    pm = re.search(r'Prompt:\s*(.+?)(?:\n|$)', content)
    prompt = pm.group(1).strip() if pm else '?'
    
    # Find what was actually created (successful POST/PUT responses)
    created_entities = []
    for m in re.finditer(r'API (POST|PUT) (/[^\s]+).*?\n.*?(200|201) OK.*?id=(\d+)', content, re.DOTALL):
        method, endpoint, code, eid = m.group(1), m.group(2), m.group(3), m.group(4)
        endpoint_short = endpoint.split('?')[0]
        created_entities.append(f"{method} {endpoint_short} → id={eid}")
    
    # Find failed API calls
    failed_calls = []
    for m in re.finditer(r'API (POST|PUT) (/[^\s]+).*?\n.*?(422|400|404|409) ERR', content, re.DOTALL):
        method, endpoint, code = m.group(1), m.group(2), m.group(3)
        endpoint_short = endpoint.split('?')[0]
        failed_calls.append(f"{method} {endpoint_short} → {code}")
    
    # Check for specific correctness issues
    issues = []
    
    # SALARY: Did it use voucher fallback instead of salary/transaction?
    if task_type == 'salary':
        used_salary_api = 'POST /salary/transaction' in content
        used_voucher_fallback = bool(re.search(r'API POST /ledger/voucher', content))
        salary_ok = bool(re.search(r'201 OK.*salary/transaction', content, re.DOTALL))
        if used_voucher_fallback and not salary_ok:
            issues.append("VOUCHER FALLBACK: Used manual voucher instead of salary/transaction API")
        if not used_salary_api and not used_voucher_fallback:
            issues.append("NO SALARY ACTION: Neither salary/transaction nor voucher was created")
    
    # EMPLOYEE: Was employee actually renamed, not created?
    if task_type == 'employee':
        renamed = 'employee renamed' in content.lower() or 'auto-fix] employee' in content
        post_emp_ok = bool(re.search(r'API POST /employee.*?201 OK', content, re.DOTALL))
        if renamed and not post_emp_ok:
            issues.append("RENAMED NOT CREATED: Employee was renamed from admin, not truly created")
        # Check if dateOfBirth was correct
        dob_in_prompt = re.search(r'(?:born|født|geboren|nacido|né|nascido)\s+(\d+)', prompt, re.IGNORECASE)
        if dob_in_prompt:
            # Check if the actual dateOfBirth was set correctly in the PUT
            dob_puts = re.findall(r'"dateOfBirth":\s*"(\d{4}-\d{2}-\d{2})"', content)
            if dob_puts:
                actual_dob = dob_puts[-1]  # Last one used
                if actual_dob == "1990-01-01":
                    issues.append(f"WRONG DOB: Set to default 1990-01-01, prompt says born {dob_in_prompt.group(0)}")
    
    # INVOICE: Check if total amounts make sense
    if task_type == 'invoice':
        # Check if invoice was actually created
        invoice_created = bool(re.search(r'API POST /invoice.*?201 OK', content, re.DOTALL))
        if not invoice_created:
            issues.append("NO INVOICE CREATED")
        # Check if amount is 0
        if re.search(r'"amount":\s*0\.0.*?"totalAmount":\s*0\.0', content):
            issues.append("ZERO AMOUNT: Invoice created with amount=0")
    
    # PAYMENT: Check if payment was registered
    if task_type == 'payment':
        payment_done = bool(re.search(r'/:payment.*?200 OK', content, re.DOTALL))
        if not payment_done:
            issues.append("NO PAYMENT REGISTERED")
    
    # PROJECT: Check if customer was linked
    if task_type == 'project':
        if re.search(r'(linked|tilknyttet|verknüpft|vinculado|lié|ligado)', prompt, re.IGNORECASE):
            project_body = re.findall(r'API POST /project.*?body:\s*(\{.*?\})', content, re.DOTALL)
            for body_str in project_body:
                if '"customer"' not in body_str:
                    issues.append("MISSING CUSTOMER LINK: Task says linked to customer but no customer in project body")
    
    # REVERSE: Check if reversal was done
    if task_type == 'reverse':
        reversed_ok = bool(re.search(r'API (POST|PUT) /ledger/voucher.*?201 OK', content, re.DOTALL))
        if not reversed_ok:
            issues.append("NO REVERSAL VOUCHER CREATED")
    
    # Only print tasks with issues
    if issues:
        print(f"{'FAIL' if status=='fail' else 'OK  '} {task_type:20s} {iters:2d} iters | {f[:55]}")
        print(f"  Prompt: {prompt[:100]}")
        for issue in issues:
            print(f"  ⚠ {issue}")
        print(f"  Created: {', '.join(created_entities[:5])}")
        if failed_calls:
            print(f"  Failed: {', '.join(failed_calls[:5])}")
        print()

# Now check employee tasks specifically - many renamed employees have wrong dateOfBirth
print("\n=== EMPLOYEE RENAME DOB CHECK ===")
for f in files:
    content = open(os.path.join(logs_dir, f), encoding='utf-8', errors='replace').read()
    if 'employee renamed' in content.lower() or '[auto-fix] employee' in content:
        pm = re.search(r'Prompt:\s*(.+?)(?:\n|$)', content)
        prompt = pm.group(1).strip()[:100] if pm else '?'
        
        # What dateOfBirth was requested?
        dob_match = re.search(r'(\d+)\.?\s*(January|February|March|April|May|June|July|August|September|October|November|December|januar|februar|mars|april|mai|juni|juli|august|september|oktober|november|desember|Janeiro|Fevereiro|Março|Abril|Maio|Junho|Julho|Agosto|Setembro|Outubro|Novembro|Dezembro|enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre|Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember|janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre)\s*(\d{4})', prompt, re.IGNORECASE)
        
        # What was actually set?
        actual_dobs = re.findall(r'"dateOfBirth":\s*"(\d{4}-\d{2}-\d{2})"', content)
        last_dob = actual_dobs[-1] if actual_dobs else 'not set'
        
        rename_success = 'renamed to' in content.lower()
        print(f"  {f[:55]}")
        print(f"    Prompt DOB: {dob_match.group(0) if dob_match else 'not found'}")
        print(f"    Actual DOB: {last_dob}")
        print(f"    Renamed: {'Yes' if rename_success else 'No'}")
        print()
