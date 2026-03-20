"""
Quick script to test Tripletex API calls against the persistent sandbox.
Usage: python test_api.py

First, get a session token:
1. Go to https://kkpqfuj-amager.tripletex.dev
2. Log in with meine.van.der.meulen@gmail.com
3. Create a session token via API or use the one from the web UI

Or create one via API:
  POST https://kkpqfuj-amager.tripletex.dev/v2/token/session/:create
  params: consumerToken=YOUR_CONSUMER_TOKEN&employeeToken=YOUR_EMPLOYEE_TOKEN&expirationDate=2026-03-31
"""
import json
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# === CONFIGURE THIS ===
BASE_URL = "https://kkpqfuj-amager.tripletex.dev/v2"
SESSION_TOKEN = "eyJ0b2tlbklkIjoyMTQ3NjI5MzM3LCJ0b2tlbiI6IjQ4MTIyOGIzLWU3MWUtNDBiOC1hOWJjLTVmY2I3YTc2YmI0NyJ9"  # Fill in your session token here
# ======================

auth = ("0", SESSION_TOKEN)

def api(method, path, params=None, body=None):
    url = f"{BASE_URL}{path}"
    resp = requests.request(
        method, url, auth=auth, timeout=30, verify=False,
        params=params, json=body if method in ("POST", "PUT") else None,
    )
    print(f"\n{method} {path} -> {resp.status_code}")
    try:
        data = resp.json()
        print(json.dumps(data, indent=2, ensure_ascii=False)[:2000])
        return data
    except Exception:
        print(f"  Raw: {resp.text[:500]}")
        return {"raw": resp.text}


if __name__ == "__main__":
    if not SESSION_TOKEN:
        print("ERROR: Set SESSION_TOKEN in this file first!")
        print()
        print("To get a token, try creating one via the API:")
        print(f"  POST {BASE_URL}/token/session/:create")
        print("  with consumerToken + employeeToken + expirationDate")
        print()
        print("Or log in at https://kkpqfuj-amager.tripletex.dev and check browser dev tools for the token")
        exit(1)

    print("=== Testing Company Discovery ===")

    # Test 1: /company/>withLoginAccess
    print("\n--- GET /company/>withLoginAccess ---")
    r = api("GET", "/company/>withLoginAccess", params={"count": 100})

    # Test 2: /company/>withLoginAccess with fields=*
    print("\n--- GET /company/>withLoginAccess?fields=* ---")
    r = api("GET", "/company/>withLoginAccess", params={"count": 100, "fields": "*"})

    # Test 3: /company (list)
    print("\n--- GET /company ---")
    r = api("GET", "/company", params={"count": 100})

    # Test 4: /company/0
    print("\n--- GET /company/0 ---")
    r = api("GET", "/company/0")

    # Test 5: /company/0?fields=*
    print("\n--- GET /company/0?fields=* ---")
    r = api("GET", "/company/0", params={"fields": "*"})

    # Test 6: /company/1
    print("\n--- GET /company/1 ---")
    r = api("GET", "/company/1")

    # Test 7: Try blind PUT with minimal body
    print("\n=== Testing Bank Account PUT ===")
    print("\n--- PUT /company (id=0, version=0) ---")
    r = api("PUT", "/company", body={"id": 0, "version": 0, "name": "Test", "bankAccountNumber": "12345678901"})

    print("\n--- PUT /company (id=0, version=1) ---")
    r = api("PUT", "/company", body={"id": 0, "version": 1, "name": "Test", "bankAccountNumber": "12345678901"})

    print("\nDone! Check the output to understand the API responses.")

    # Test 8: Try to discover the correct field name
    # PUT with just id+version to see what fields ARE accepted
    print("\n=== Discovering Company Fields ===")
    print("\n--- PUT /company (minimal, just id+version+name) ---")
    r = api("PUT", "/company", body={"id": 0, "version": 0, "name": "TestCompany"})

    # Test 9: Try different bank-related field names
    for field_name in ["bankAccount", "bankAccountNo", "bankAccountNr", "kontonummer", "bankkontonummer", "bankAccount.number"]:
        print(f"\n--- PUT /company with {field_name} ---")
        r = api("PUT", "/company", body={"id": 0, "version": 0, "name": "TestCompany", field_name: "12345678901"})

    # Test 10: Check swagger spec for Company schema
    print("\n--- Checking Swagger/OpenAPI spec ---")
    import requests as req2
    for url_suffix in ["/swagger.json", "/v2/swagger.json", "/openapi.json", "/v2/openapi.json", "/v2-docs"]:
        test_url = BASE_URL.replace("/v2", "") + url_suffix
        try:
            resp = req2.get(test_url, verify=False, timeout=10)
            print(f"  {test_url} -> {resp.status_code} ({len(resp.text)} bytes)")
            if resp.status_code == 200 and resp.text.startswith("{"):
                spec = resp.json()
                defs = spec.get("definitions", spec.get("components", {}).get("schemas", {}))
                for name, schema in defs.items():
                    if "company" in name.lower():
                        props = schema.get("properties", {})
                        bank_fields = [k for k in props if "bank" in k.lower() or "konto" in k.lower() or "account" in k.lower()]
                        if bank_fields or len(props) < 30:
                            print(f"  Schema: {name} -> bank fields: {bank_fields}, all: {sorted(props.keys())[:20]}")
                break
        except Exception as e:
            print(f"  {url_suffix} -> error: {e}")

    # Test 11: Try to find company via employee (employees know their company)
    print("\n--- GET /employee?fields=* (to find company) ---")
    r = api("GET", "/employee", params={"fields": "*"})

    # Test 12: Try GET /company/altinn to list companies
    print("\n--- GET /company/divisions ---")
    r = api("GET", "/company/divisions")

    # Test 13: Try different company IDs higher up
    for cid in [100, 1000, 10000]:
        print(f"\n--- GET /company/{cid} ---")
        r = api("GET", f"/company/{cid}")
