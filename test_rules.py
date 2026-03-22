import yaml
rules = yaml.safe_load(open('rules.yaml', encoding='utf-8'))['rules']
print(f'Parsed {len(rules)} rules OK')
# List all new rules (last 16)
print('\nNew rules:')
for r in rules[-16:]:
    print(f'  {r["id"]}')

# Quick check: test a few of the new rules would trigger
from main import validate_tool_call

# Test: wrong percentage field name
v = validate_tool_call("POST", "/employee/employment/details", 
    body={"employment": {"id": 1}, "date": "2026-01-01", "percentOfFullTimeEquivalent": 100})
print(f'\nTest percentOfFullTimeEquivalent: {len(v)} violations')
for x in v: print(f'  {x[:100]}')

# Test: string employmentType
v = validate_tool_call("POST", "/employee/employment/details",
    body={"employment": {"id": 1}, "date": "2026-01-01", "employmentType": "ORDINARY"})
print(f'\nTest string employmentType: {len(v)} violations')
for x in v: print(f'  {x[:100]}')

# Test: GET /timesheet/entry without dates
v = validate_tool_call("GET", "/timesheet/entry", params={})
print(f'\nTest GET /timesheet/entry no dates: {len(v)} violations')
for x in v: print(f'  {x[:100]}')

# Test: GET /ledger/voucher without dates
v = validate_tool_call("GET", "/ledger/voucher", params={})
print(f'\nTest GET /ledger/voucher no dates: {len(v)} violations')
for x in v: print(f'  {x[:100]}')

# Test: PUT /supplierInvoice (should block)
v = validate_tool_call("PUT", "/supplierInvoice", body={"invoiceNumber": "X"})
print(f'\nTest PUT /supplierInvoice: {len(v)} violations')
for x in v: print(f'  {x[:100]}')
