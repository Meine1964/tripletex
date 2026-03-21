"""Explore Tripletex travel expense per diem - find date fields."""
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

# Create a travel expense with departure and return dates
te_body = {
    'employee': {'id': emp_id},
    'title': 'Visita cliente Tromsø',
    'date': '2026-03-20',
    'departureDate': '2026-03-15',
    'returnDate': '2026-03-20',
}
r2 = requests.post(f'{base}/travelExpense', json=te_body, auth=auth, verify=False)
te_data = r2.json()
print(f"TE response: {r2.status_code}")
if r2.status_code >= 400:
    print(json.dumps(te_data, indent=2)[:1000])
    # Try without departure/return dates on TE
    te_body2 = {'employee': {'id': emp_id}, 'title': 'Visita cliente Tromsø', 'date': '2026-03-20'}
    r2 = requests.post(f'{base}/travelExpense', json=te_body2, auth=auth, verify=False)
    te_data = r2.json()
    print(f"TE (no dates) response: {r2.status_code}")
te_id = te_data['value']['id']
print(f"Travel expense id={te_id}")
print(f"Full TE response keys: {list(te_data['value'].keys())}")

# Try per diem with departure and return dates on travel expense
# Rate category 740 = Overnatting over 12 timer - innland 2026
pd_body = {
    'travelExpense': {'id': te_id},
    'rateCategory': {'id': 740},
    'location': 'Tromsø',
    'overnightAccommodation': 'NONE',
    'count': 5,
}
r4 = requests.post(f'{base}/travelExpense/perDiemCompensation', json=pd_body, auth=auth, verify=False)
print(f"\nPOST /travelExpense/perDiemCompensation: {r4.status_code}")
print(json.dumps(r4.json(), indent=2)[:2000])

if r4.status_code >= 400:
    # Maybe dates need to be on the compensation, not the travelExpense
    # Try: departureDateTime / returnDateTime
    pd_body2 = {
        'travelExpense': {'id': te_id},
        'rateCategory': {'id': 740},
        'location': 'Tromsø',
        'overnightAccommodation': 'NONE',
        'count': 5,
        'departureDate': '2026-03-15',
        'returnDate': '2026-03-20',
    }
    r4b = requests.post(f'{base}/travelExpense/perDiemCompensation', json=pd_body2, auth=auth, verify=False)
    print(f"\nPOST with departureDate/returnDate: {r4b.status_code}")
    print(json.dumps(r4b.json(), indent=2)[:2000])

# Also try adding costs for taxi
taxi_cats = requests.get(f'{base}/travelExpense/costCategory', auth=auth, verify=False).json().get('values', [])
taxi_cat = next((c for c in taxi_cats if 'taxi' in c.get('description', '').lower() or 'drosje' in c.get('description', '').lower()), None)
if not taxi_cat:
    print("\nNo taxi category found. Available:")
    for c in taxi_cats:
        print(f"  id={c['id']} desc={c.get('description')}")

# Add taxi cost
cost_body = {
    'travelExpense': {'id': te_id},
    'costCategory': {'id': 32856646},  # Fly
    'paymentType': {'id': 32856630},  # Privat utlegg
    'amountCurrencyIncVat': 800,
    'date': '2026-03-20',
    'comment': 'Taxi',
}
r5 = requests.post(f'{base}/travelExpense/cost', json=cost_body, auth=auth, verify=False)
print(f"\nPOST /travelExpense/cost (taxi): {r5.status_code}")
print(json.dumps(r5.json(), indent=2)[:500])

# Add plane ticket cost
cost_body2 = {
    'travelExpense': {'id': te_id},
    'costCategory': {'id': 32856646},  # Fly
    'paymentType': {'id': 32856630},  # Privat utlegg
    'amountCurrencyIncVat': 2600,
    'date': '2026-03-15',
    'comment': 'Billete de avión',
}
r6 = requests.post(f'{base}/travelExpense/cost', json=cost_body2, auth=auth, verify=False)
print(f"\nPOST /travelExpense/cost (plane): {r6.status_code}")
print(json.dumps(r6.json(), indent=2)[:500])

# Get final state
r7 = requests.get(f'{base}/travelExpense/{te_id}', params={'fields': '*'}, auth=auth, verify=False)
te_full = r7.json().get('value', {})
print(f"\nFinal state:")
print(f"  title={te_full.get('title')}")
print(f"  amount={te_full.get('amount')}")
print(f"  costs={len(te_full.get('costs', []))}")
print(f"  perDiemCompensations={len(te_full.get('perDiemCompensations', []))}")
print(f"  departureDate={te_full.get('departureDate')}")
print(f"  returnDate={te_full.get('returnDate')}")

# Clean up
r8 = requests.delete(f'{base}/travelExpense/{te_id}', auth=auth, verify=False)
print(f"\nDELETE: {r8.status_code}")
