"""Find per diem compensation schema."""
import requests
import json
import urllib3
urllib3.disable_warnings()

auth = ('0', 'eyJ0b2tlbklkIjoyMTQ3NjI5MzM3LCJ0b2tlbiI6IjQ4MTIyOGIzLWU3MWUtNDBiOC1hOWJjLTVmY2I3YTc2YmI0NyJ9')
base = 'https://kkpqfuj-amager.tripletex.dev/v2'

# Try to get swagger/API spec from various URLs
for path in ['/swagger.json', '/v2/swagger.json', '/api-docs', '/v2-docs', '/../swagger.json']:
    url = f'https://kkpqfuj-amager.tripletex.dev{path}'
    r = requests.get(url, auth=auth, verify=False, timeout=5)
    print(f"GET {path}: {r.status_code} ({len(r.content)} bytes)")
    if r.status_code == 200 and len(r.content) > 1000:
        # Search for perDiem in the spec
        text = r.text
        idx = text.lower().find('perdiem')
        if idx > -1:
            print(f"  Found 'perDiem' at position {idx}")
            print(f"  Context: ...{text[max(0,idx-50):idx+200]}...")
