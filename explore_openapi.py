"""Download and search Tripletex OpenAPI spec for perDiemCompensation schema."""
import requests
import json
import urllib3
urllib3.disable_warnings()

# Download the OpenAPI spec
r = requests.get('https://tripletex.no/v2/openapi.json', timeout=30, verify=False)
print(f"GET openapi.json: {r.status_code} ({len(r.content)} bytes)")

if r.status_code == 200:
    spec = r.json()
    
    # Find PerDiemCompensation schema
    schemas = spec.get('components', {}).get('schemas', {})
    for name in schemas:
        if 'perdiem' in name.lower():
            print(f"\nSchema: {name}")
            props = schemas[name].get('properties', {})
            for p, v in sorted(props.items()):
                ptype = v.get('type', '')
                ref = v.get('$ref', '')
                desc = v.get('description', '')
                print(f"  {p}: {ptype or ref} — {desc[:100]}")
    
    # Also find TravelExpenseCost schema
    for name in schemas:
        if name.lower() in ['cost', 'travelexpensecost']:
            print(f"\nSchema: {name}")
            props = schemas[name].get('properties', {})
            for p, v in sorted(props.items()):
                ptype = v.get('type', '')
                ref = v.get('$ref', '')
                desc = v.get('description', '')
                print(f"  {p}: {ptype or ref} — {desc[:100]}")
        elif 'cost' in name.lower() and 'travel' in name.lower():
            print(f"\nSchema: {name}")
            props = schemas[name].get('properties', {})
            for p, v in sorted(props.items()):
                ptype = v.get('type', '')
                ref = v.get('$ref', '')
                desc = v.get('description', '')
                print(f"  {p}: {ptype or ref} — {desc[:100]}")
    
    # TravelExpense schema itself
    for name in schemas:
        if name.lower() == 'travelexpense':
            print(f"\nSchema: {name}")
            props = schemas[name].get('properties', {})
            for p, v in sorted(props.items()):
                ptype = v.get('type', '')
                ref = v.get('$ref', '')
                desc = v.get('description', '')
                print(f"  {p}: {ptype or ref} — {desc[:100]}")
