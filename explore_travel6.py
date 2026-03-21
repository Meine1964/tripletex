"""Explore Tripletex API - find correct field names via API spec/exploration."""
import requests
import json
import urllib3
urllib3.disable_warnings()

auth = ('0', 'eyJ0b2tlbklkIjoyMTQ3NjI5MzM3LCJ0b2tlbiI6IjQ4MTIyOGIzLWU3MWUtNDBiOC1hOWJjLTVmY2I3YTc2YmI0NyJ9')
base = 'https://kkpqfuj-amager.tripletex.dev/v2'

emp_id = 18443752

# Create a travel expense
te_body = {'employee': {'id': emp_id}, 'title': 'Test', 'date': '2026-03-20'}
r2 = requests.post(f'{base}/travelExpense', json=te_body, auth=auth, verify=False)
te_id = r2.json()['value']['id']
print(f"Travel expense id={te_id}")

# COST: try without comment, without date, just required fields
cost_body = {
    'travelExpense': {'id': te_id},
    'costCategory': {'id': 32856646},  # Fly
    'paymentType': {'id': 32856630},  # Privat utlegg
    'amountCurrencyIncVat': 2600,
}
r3 = requests.post(f'{base}/travelExpense/cost', json=cost_body, auth=auth, verify=False)
print(f"\nPOST /travelExpense/cost: {r3.status_code}")
cost_data = r3.json()
if r3.status_code < 400:
    # Get the created cost to see all fields
    cost_url = cost_data.get('value', {}).get('url', '')
    cost_id = cost_url.split('/')[-1] if cost_url else None
    if cost_id:
        r3b = requests.get(f'{base}/travelExpense/cost/{cost_id}', params={'fields': '*'}, auth=auth, verify=False)
        print(f"GET cost: {r3b.status_code}")
        cost_full = r3b.json().get('value', {})
        print(f"Cost fields: {list(cost_full.keys())}")
        print(json.dumps(cost_full, indent=2)[:2000])
else:
    print(json.dumps(cost_data, indent=2)[:1000])

# PER DIEM: Get an existing one or try to figure out fields
# Try with just required fields and no dates
pd_body_min = {
    'travelExpense': {'id': te_id},
    'rateCategory': {'id': 740},  # Overnatting over 12 timer - innland 2026
    'location': 'Tromsø',
}
r4 = requests.post(f'{base}/travelExpense/perDiemCompensation', json=pd_body_min, auth=auth, verify=False)
print(f"\nPOST /travelExpense/perDiemCompensation (min): {r4.status_code}")
pd_data = r4.json()
if r4.status_code < 400:
    pd_url = pd_data.get('value', {}).get('url', '')
    pd_id = pd_url.split('/')[-1] if pd_url else None
    if pd_id:
        r4b = requests.get(f'{base}/travelExpense/perDiemCompensation/{pd_id}', params={'fields': '*'}, auth=auth, verify=False)
        print(f"GET perDiem: {r4b.status_code}")
        pd_full = r4b.json().get('value', {})
        print(f"PerDiem fields: {list(pd_full.keys())}")
        print(json.dumps(pd_full, indent=2)[:2000])
else:
    print(json.dumps(pd_data, indent=2)[:1000])
    # "Spesifiser avreisedato og returdato" — the TE itself might not have the dates
    # Let me try setting dates on the travel expense via PUT
    r_te = requests.get(f'{base}/travelExpense/{te_id}', params={'fields': '*'}, auth=auth, verify=False)
    te_full = r_te.json().get('value', {})
    # Try PUT to add dates?
    put_body = {'id': te_id, 'version': te_full['version'], 'title': 'Test',
                'employee': {'id': emp_id}, 'date': '2026-03-20'}
    # Check all key names on the TE
    print(f"\nTE fields: {sorted(te_full.keys())}")
    
    # Maybe we need to set departure/return through a PUT on the TE
    # Let's check if the TE has type-specific fields
    print(f"\nTE type: {te_full.get('type')}")
    
    # Try getting the Swagger/OpenAPI spec
    r_spec = requests.get(f'{base}/../swagger.json', auth=auth, verify=False)
    print(f"\nGET swagger.json: {r_spec.status_code}")
    if r_spec.status_code == 200:
        spec = r_spec.json()
        # Find perDiemCompensation definition
        definitions = spec.get('definitions', {})
        for name, defn in definitions.items():
            if 'perdiem' in name.lower():
                print(f"\nDefinition: {name}")
                props = defn.get('properties', {})
                for p, v in props.items():
                    print(f"  {p}: {v.get('type', v.get('$ref', '?'))}")

# Clean up
r7 = requests.delete(f'{base}/travelExpense/{te_id}', auth=auth, verify=False)
print(f"\nDELETE: {r7.status_code}")
