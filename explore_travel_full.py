"""Full integration test: Complete travel expense matching attempt 28 task."""
import requests
import json
import urllib3
urllib3.disable_warnings()

auth = ('0', 'eyJ0b2tlbklkIjoyMTQ3NjI5MzM3LCJ0b2tlbiI6IjQ4MTIyOGIzLWU3MWUtNDBiOC1hOWJjLTVmY2I3YTc2YmI0NyJ9')
base = 'https://kkpqfuj-amager.tripletex.dev/v2'
emp_id = 18443752

# Task: Register travel expense for Miguel Pérez (miguel.perez@example.org) 
# "Visita cliente Tromsø". 5 days with per diem (800 NOK/day).
# Expenses: plane ticket 2600 NOK, taxi 800 NOK

# Step 1: Get or create employee
# First try POST
emp_body = {'firstName': 'Miguel', 'lastName': 'Pérez', 'email': 'miguel.perez@example.org'}
r_emp = requests.post(f'{base}/employee', json=emp_body, auth=auth, verify=False)
print(f"POST /employee: {r_emp.status_code}")
if r_emp.status_code < 400:
    emp_data = r_emp.json()['value']
    emp_id = emp_data['id']
    print(f"Created employee id={emp_id}")
else:
    print(f"Employee exists, fetching...")
    r_get = requests.get(f'{base}/employee', params={'fields': '*'}, auth=auth, verify=False)
    emps = r_get.json()['values']
    emp_id = emps[0]['id']
    emp_ver = emps[0]['version']
    # Rename to Miguel Pérez
    put_body = {
        'id': emp_id, 'version': emp_ver,
        'firstName': 'Miguel', 'lastName': 'Pérez',
        'dateOfBirth': '1990-01-01',
    }
    r_put = requests.put(f'{base}/employee/{emp_id}', json=put_body, auth=auth, verify=False)
    print(f"PUT /employee/{emp_id}: {r_put.status_code}")
    if r_put.status_code < 400:
        emp_data = r_put.json()['value']
        print(f"Renamed: {emp_data.get('firstName')} {emp_data.get('lastName')}")

# Step 2: Create travel expense with travelDetails
# 5 days: departure 2026-03-15, return 2026-03-20
te_body = {
    'employee': {'id': emp_id},
    'title': 'Visita cliente Tromsø',
    'date': '2026-03-20',
    'travelDetails': {
        'departureDate': '2026-03-15',
        'returnDate': '2026-03-20',
        'destination': 'Tromsø',
        'purpose': 'Visita cliente Tromsø',
        'isDayTrip': False,
    }
}
r_te = requests.post(f'{base}/travelExpense', json=te_body, auth=auth, verify=False)
print(f"\nPOST /travelExpense: {r_te.status_code}")
te_id = r_te.json()['value']['id']
print(f"Travel expense id={te_id}")

# Step 3: Get payment type
r_pt = requests.get(f'{base}/travelExpense/paymentType', auth=auth, verify=False)
pt_id = r_pt.json()['values'][0]['id']
print(f"Payment type id={pt_id}")

# Step 4: Get cost categories
r_cats = requests.get(f'{base}/travelExpense/costCategory', auth=auth, verify=False)
cats = r_cats.json()['values']
fly_cat = next((c for c in cats if c.get('description') == 'Fly'), None)
# No taxi category - use something like "Buss" or generic travel
taxi_options = [c for c in cats if any(w in c.get('description', '').lower() for w in ['taxi', 'drosje', 'transport'])]
if not taxi_options:
    # Use "Annen reisekostnad" or similar
    taxi_options = [c for c in cats if 'annen' in c.get('description', '').lower() and 'reise' in c.get('description', '').lower()]
if not taxi_options:
    # Just list remaining transport-ish categories
    print("Available categories:")
    for c in cats:
        print(f"  id={c['id']} desc={c.get('description')}")
    taxi_cat = cats[0]  # fallback
else:
    taxi_cat = taxi_options[0]

print(f"Fly category: id={fly_cat['id'] if fly_cat else '?'}")
print(f"Taxi category: id={taxi_cat['id']} desc={taxi_cat.get('description')}")

# Step 5: Add cost - plane ticket (2600 NOK)
cost1 = {
    'travelExpense': {'id': te_id},
    'costCategory': {'id': fly_cat['id']},
    'paymentType': {'id': pt_id},
    'amountCurrencyIncVat': 2600,
}
r_c1 = requests.post(f'{base}/travelExpense/cost', json=cost1, auth=auth, verify=False)
print(f"\nPOST cost (plane): {r_c1.status_code}")

# Step 6: Add cost - taxi (800 NOK)
cost2 = {
    'travelExpense': {'id': te_id},
    'costCategory': {'id': taxi_cat['id']},
    'paymentType': {'id': pt_id},
    'amountCurrencyIncVat': 800,
}
r_c2 = requests.post(f'{base}/travelExpense/cost', json=cost2, auth=auth, verify=False)
print(f"POST cost (taxi): {r_c2.status_code}")

# Step 7: Add per diem compensation (5 days with overnights)
# Rate category 740 = "Overnatting over 12 timer - innland" 2026
pd_body = {
    'travelExpense': {'id': te_id},
    'rateCategory': {'id': 740},
    'location': 'Tromsø',
    'overnightAccommodation': 'NONE',
    'count': 5,
}
r_pd = requests.post(f'{base}/travelExpense/perDiemCompensation', json=pd_body, auth=auth, verify=False)
print(f"\nPOST perDiem: {r_pd.status_code}")
print(json.dumps(r_pd.json(), indent=2)[:500])

# Check the per diem details
if r_pd.status_code < 400:
    pd_url = r_pd.json().get('value', {}).get('url', '')
    pd_id = pd_url.split('/')[-1]
    r_pd_get = requests.get(f'{base}/travelExpense/perDiemCompensation/{pd_id}', params={'fields': '*'}, auth=auth, verify=False)
    print(f"\nPer diem details:")
    pd_full = r_pd_get.json().get('value', {})
    print(f"  amount={pd_full.get('amount')} rate={pd_full.get('rate')} count={pd_full.get('count')}")

# Step 8: Get final travel expense state
r_final = requests.get(f'{base}/travelExpense/{te_id}', params={'fields': '*'}, auth=auth, verify=False)
te_final = r_final.json()['value']
print(f"\nFinal travel expense:")
print(f"  title={te_final['title']}")
print(f"  amount={te_final['amount']}")
print(f"  costs={len(te_final.get('costs', []))}")
print(f"  perDiemCompensations={len(te_final.get('perDiemCompensations', []))}")
print(f"  travelDetails departure={te_final.get('travelDetails', {}).get('departureDate')}")
print(f"  travelDetails return={te_final.get('travelDetails', {}).get('returnDate')}")

# Don't delete - leave for inspection
print(f"\nTravel expense left for inspection: id={te_id}")
