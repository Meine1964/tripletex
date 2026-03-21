"""Explore Tripletex travel expense API - correct field names."""
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

# Get cost categories first
r6 = requests.get(f'{base}/travelExpense/costCategory', auth=auth, verify=False)
cats = r6.json().get('values', [])
print(f"\nCost categories ({len(cats)}):")
for c in cats[:20]:
    print(f"  {json.dumps(c)[:200]}")

# Try cost with correct fields - use costCategory
if cats:
    cost_body = {
        'travelExpense': {'id': te_id},
        'costCategory': {'id': cats[0]['id']},
        'amount': 2600,
        'amountCurrencyIncVat': 2600,
        'date': '2026-03-20',
        'comment': 'Plane ticket'
    }
    r3 = requests.post(f'{base}/travelExpense/cost', json=cost_body, auth=auth, verify=False)
    print(f"\nPOST /travelExpense/cost: {r3.status_code}")
    print(json.dumps(r3.json(), indent=2)[:2000])

# Try per diem compensation with correct fields
# First get per diem rate categories
pd_cats = [c for c in requests.get(f'{base}/travelExpense/rateCategory', auth=auth, verify=False).json().get('values', []) 
           if c.get('type') == 'PER_DIEM' and c.get('isValidDomestic')]
print(f"\nPer diem rate categories (domestic): {len(pd_cats)} total, showing first 10:")
# Show recent ones (from 2019+)
recent_pd = [c for c in pd_cats if c.get('fromDate') and c['fromDate'] >= '2019-01-01'] or pd_cats[-10:]
for c in recent_pd[:10]:
    print(f"  id={c['id']} name={c['name']} from={c.get('fromDate')} to={c.get('toDate')}")

# Try a proper per diem
pd_body = {
    'travelExpense': {'id': te_id},
    'rateCategory': {'id': recent_pd[0]['id']},
    'countryCode': 'NO',
    'travelExpenseZoneId': 0,
    'overnightAccommodation': 'NONE',
    'location': 'Tromsø',
    'dateFrom': '2026-03-15',
    'dateTo': '2026-03-20',
    'count': 5
}
r4 = requests.post(f'{base}/travelExpense/perDiemCompensation', json=pd_body, auth=auth, verify=False)
print(f"\nPOST /travelExpense/perDiemCompensation: {r4.status_code}")
print(json.dumps(r4.json(), indent=2)[:2000])

# Clean up
r7 = requests.delete(f'{base}/travelExpense/{te_id}', auth=auth, verify=False)
print(f"\nDELETE /travelExpense/{te_id}: {r7.status_code}")
