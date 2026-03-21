"""Explore the Tripletex travel expense API."""
import requests
import json
import urllib3
urllib3.disable_warnings()

auth = ('0', 'eyJ0b2tlbklkIjoyMTQ3NjI5MzM3LCJ0b2tlbiI6IjQ4MTIyOGIzLWU3MWUtNDBiOC1hOWJjLTVmY2I3YTc2YmI0NyJ9')
base = 'https://kkpqfuj-amager.tripletex.dev/v2'

# Get employees
r = requests.get(f'{base}/employee', params={'fields': 'id,firstName,lastName,email'}, auth=auth, verify=False)
emps = r.json().get('values', [])
print('Employees:')
for e in emps[:5]:
    print(f"  id={e['id']} {e.get('firstName','')} {e.get('lastName','')}")

emp_id = emps[0]['id'] if emps else None
if not emp_id:
    print("No employees found!")
    exit()

# Create a travel expense
te = {'employee': {'id': emp_id}, 'title': 'Test Travel Expense', 'date': '2026-03-20'}
r2 = requests.post(f'{base}/travelExpense', json=te, auth=auth, verify=False)
print(f'\nPOST /travelExpense: {r2.status_code}')
te_data = r2.json()
print(json.dumps(te_data, indent=2)[:2000])

te_id = te_data.get('value', {}).get('id')
if not te_id:
    print("Failed to create travel expense")
    exit()

print(f"\nTravel expense ID: {te_id}")

# Now try to add a cost (expense line)
cost_body = {
    'travelExpense': {'id': te_id},
    'description': 'Plane ticket',
    'amount': 2600,
    'date': '2026-03-20',
    'currency': {'code': 'NOK'}
}
r3 = requests.post(f'{base}/travelExpense/cost', json=cost_body, auth=auth, verify=False)
print(f'\nPOST /travelExpense/cost: {r3.status_code}')
print(json.dumps(r3.json(), indent=2)[:2000])

# Try per diem compensation
pd_body = {
    'travelExpense': {'id': te_id},
    'rateCategory': {'id': 1},
    'countryCode': 'NO',
    'overnightsAwayFrom': 5,
    'date': '2026-03-20'
}
r4 = requests.post(f'{base}/travelExpense/perDiemCompensation', json=pd_body, auth=auth, verify=False)
print(f'\nPOST /travelExpense/perDiemCompensation: {r4.status_code}')
print(json.dumps(r4.json(), indent=2)[:2000])

# List travel expense rate categories
r5 = requests.get(f'{base}/travelExpense/rateCategory', auth=auth, verify=False)
print(f'\nGET /travelExpense/rateCategory: {r5.status_code}')
print(json.dumps(r5.json(), indent=2)[:3000])

# List travel expense cost categories
r6 = requests.get(f'{base}/travelExpense/costCategory', auth=auth, verify=False)
print(f'\nGET /travelExpense/costCategory: {r6.status_code}')
print(json.dumps(r6.json(), indent=2)[:3000])

# Clean up - delete the travel expense
r7 = requests.delete(f'{base}/travelExpense/{te_id}', auth=auth, verify=False)
print(f'\nDELETE /travelExpense/{te_id}: {r7.status_code}')
