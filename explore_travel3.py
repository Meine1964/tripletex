"""Explore Tripletex travel expense API - find correct schema."""
import requests
import json
import urllib3
urllib3.disable_warnings()

auth = ('0', 'eyJ0b2tlbklkIjoyMTQ3NjI5MzM3LCJ0b2tlbiI6IjQ4MTIyOGIzLWU3MWUtNDBiOC1hOWJjLTVmY2I3YTc2YmI0NyJ9')
base = 'https://kkpqfuj-amager.tripletex.dev/v2'

# Get employees
r = requests.get(f'{base}/employee', params={'fields': 'id,firstName,lastName,email'}, auth=auth, verify=False)
emps = r.json().get('values', [])
emp_id = emps[0]['id']
print(f"Using employee id={emp_id}")

# Create a travel expense
te = {'employee': {'id': emp_id}, 'title': 'Test Travel Expense', 'date': '2026-03-20'}
r2 = requests.post(f'{base}/travelExpense', json=te, auth=auth, verify=False)
te_data = r2.json()
te_id = te_data['value']['id']
print(f"Travel expense id={te_id}")

# Try minimal cost - just category and travel expense
cost_body1 = {
    'travelExpense': {'id': te_id},
    'costCategory': {'id': 32856646},  # Fly
}
r3 = requests.post(f'{base}/travelExpense/cost', json=cost_body1, auth=auth, verify=False)
print(f"\nPOST /travelExpense/cost (minimal): {r3.status_code}")
resp = r3.json()
if r3.status_code < 400:
    # print full response to see all fields
    print(json.dumps(resp, indent=2)[:3000])
else:
    print(json.dumps(resp, indent=2)[:1000])
    
    # Try even more minimal
    cost_body2 = {
        'travelExpense': {'id': te_id},
    }
    r3b = requests.post(f'{base}/travelExpense/cost', json=cost_body2, auth=auth, verify=False)
    print(f"\nPOST /travelExpense/cost (just TE): {r3b.status_code}")
    print(json.dumps(r3b.json(), indent=2)[:2000])

# Try perDiemCompensation minimal - just travelExpense and rateCategory
pd_body1 = {
    'travelExpense': {'id': te_id},
    'rateCategory': {'id': 540},  # Overnatting over 12 timer from 2019
}
r4 = requests.post(f'{base}/travelExpense/perDiemCompensation', json=pd_body1, auth=auth, verify=False)
print(f"\nPOST /travelExpense/perDiemCompensation (minimal): {r4.status_code}")
resp4 = r4.json()
if r4.status_code < 400:
    print(json.dumps(resp4, indent=2)[:3000])
else:
    print(json.dumps(resp4, indent=2)[:1000])

# Get rate categories from 2026
all_rate_cats = requests.get(f'{base}/travelExpense/rateCategory', auth=auth, verify=False).json().get('values', [])
pd_cats = [c for c in all_rate_cats if c.get('type') == 'PER_DIEM']
active_2026 = [c for c in pd_cats if not c.get('toDate') or c['toDate'] >= '2026-01-01']
print(f"\n2026+ rate categories: {len(active_2026)}")
for c in active_2026[:15]:
    print(f"  id={c['id']} name={c.get('name')} from={c.get('fromDate')} to={c.get('toDate')} dom={c.get('isValidDomestic')}")

# Try with a 2026 category
if active_2026:
    pd_body2 = {
        'travelExpense': {'id': te_id},
        'rateCategory': {'id': active_2026[0]['id']},
    }
    r5 = requests.post(f'{base}/travelExpense/perDiemCompensation', json=pd_body2, auth=auth, verify=False)
    print(f"\nPOST /travelExpense/perDiemCompensation (2026 cat): {r5.status_code}")
    print(json.dumps(r5.json(), indent=2)[:3000])

# Clean up
r7 = requests.delete(f'{base}/travelExpense/{te_id}', auth=auth, verify=False)
print(f"\nDELETE /travelExpense/{te_id}: {r7.status_code}")
