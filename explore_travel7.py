"""Get TravelDetails schema and test complete flow."""
import requests
import json
import urllib3
urllib3.disable_warnings()

# Download the OpenAPI spec
r = requests.get('https://tripletex.no/v2/openapi.json', timeout=30, verify=False)
spec = r.json()
schemas = spec.get('components', {}).get('schemas', {})

# Find TravelDetails
for name in schemas:
    if 'traveldetail' in name.lower():
        print(f"Schema: {name}")
        props = schemas[name].get('properties', {})
        for p, v in sorted(props.items()):
            ptype = v.get('type', '')
            ref = v.get('$ref', '')
            desc = v.get('description', '')
            print(f"  {p}: {ptype or ref} — {desc[:100]}")

# Now test the complete flow
auth = ('0', 'eyJ0b2tlbklkIjoyMTQ3NjI5MzM3LCJ0b2tlbiI6IjQ4MTIyOGIzLWU3MWUtNDBiOC1hOWJjLTVmY2I3YTc2YmI0NyJ9')
base = 'https://kkpqfuj-amager.tripletex.dev/v2'
emp_id = 18443752

# Create travel expense with travelDetails
te_body = {
    'employee': {'id': emp_id},
    'title': 'Visita cliente Tromsø',
    'date': '2026-03-20',
    'travelDetails': {
        'departureDate': '2026-03-15',
        'returnDate': '2026-03-20',
    }
}
r2 = requests.post(f'{base}/travelExpense', json=te_body, auth=auth, verify=False)
print(f"\nPOST /travelExpense: {r2.status_code}")
if r2.status_code >= 400:
    print(json.dumps(r2.json(), indent=2)[:1000])
    # Try without Travel Details and set via PUT later
    te_body2 = {'employee': {'id': emp_id}, 'title': 'Visita cliente Tromsø', 'date': '2026-03-20'}
    r2 = requests.post(f'{base}/travelExpense', json=te_body2, auth=auth, verify=False)
    te_data = r2.json()
    te_id = te_data['value']['id']
    te_ver = te_data['value']['version']
    print(f"Created TE id={te_id} v={te_ver}")
    
    # PUT to add travelDetails
    put_body = {
        'id': te_id,
        'version': te_ver,
        'employee': {'id': emp_id},
        'title': 'Visita cliente Tromsø',
        'date': '2026-03-20',
        'travelDetails': {
            'departureDate': '2026-03-15',
            'returnDate': '2026-03-20',
        }
    }
    r_put = requests.put(f'{base}/travelExpense/{te_id}', json=put_body, auth=auth, verify=False)
    print(f"PUT /travelExpense/{te_id}: {r_put.status_code}")
    print(json.dumps(r_put.json(), indent=2)[:1000])
else:
    te_data = r2.json()
    te_id = te_data['value']['id']
    print(f"Created TE id={te_id}")
    # Check travelDetails
    print(f"travelDetails: {te_data['value'].get('travelDetails')}")

# Try per diem compensation now
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

# Clean up
r7 = requests.delete(f'{base}/travelExpense/{te_id}', auth=auth, verify=False)
print(f"\nDELETE: {r7.status_code}")
