"""Explore Tripletex travel expense API - correct schema round 3."""
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

# Get payment types
r_pt = requests.get(f'{base}/travelExpense/paymentType', auth=auth, verify=False)
print(f"\nGET /travelExpense/paymentType: {r_pt.status_code}")
pts = r_pt.json().get('values', [])
print(f"Payment types: {len(pts)}")
for pt in pts[:10]:
    print(f"  id={pt['id']} description={pt.get('description','')} account={pt.get('account',{}).get('id','?')}")

# Create a travel expense
te_body = {'employee': {'id': emp_id}, 'title': 'Visita cliente', 'date': '2026-03-20'}
r2 = requests.post(f'{base}/travelExpense', json=te_body, auth=auth, verify=False)
te_id = r2.json()['value']['id']
print(f"\nTravel expense id={te_id}")

# Add cost with paymentType and amountCurrencyIncVat
if pts:
    cost_body = {
        'travelExpense': {'id': te_id},
        'costCategory': {'id': 32856646},  # Fly
        'paymentType': {'id': pts[0]['id']},
        'amountCurrencyIncVat': 2600,
        'date': '2026-03-20',
    }
    r3 = requests.post(f'{base}/travelExpense/cost', json=cost_body, auth=auth, verify=False)
    print(f"\nPOST /travelExpense/cost (fly): {r3.status_code}")
    print(json.dumps(r3.json(), indent=2)[:3000])

# Add per diem with location
pd_body = {
    'travelExpense': {'id': te_id},
    'rateCategory': {'id': 740},  # Overnatting over 12 timer - innland 2026
    'location': 'Tromsø',
    'overnightAccommodation': 'NONE',
    'count': 5,
}
r4 = requests.post(f'{base}/travelExpense/perDiemCompensation', json=pd_body, auth=auth, verify=False)
print(f"\nPOST /travelExpense/perDiemCompensation: {r4.status_code}")
print(json.dumps(r4.json(), indent=2)[:3000])

# Now GET the travel expense to see full state
r5 = requests.get(f'{base}/travelExpense/{te_id}', params={'fields': '*'}, auth=auth, verify=False)
print(f"\nGET /travelExpense/{te_id}: {r5.status_code}")
te_full = r5.json().get('value', {})
print(f"  title={te_full.get('title')}")
print(f"  amount={te_full.get('amount')}")
print(f"  costs={len(te_full.get('costs', []))}")
print(f"  perDiemCompensations={len(te_full.get('perDiemCompensations', []))}")

# Clean up
r7 = requests.delete(f'{base}/travelExpense/{te_id}', auth=auth, verify=False)
print(f"\nDELETE /travelExpense/{te_id}: {r7.status_code}")
