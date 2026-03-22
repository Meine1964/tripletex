import urllib.request, json, ssl

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
data = urllib.request.urlopen('https://tripletex.no/v2/openapi.json', context=ctx).read()
spec = json.loads(data)
posting = spec.get('components', {}).get('schemas', {}).get('Voucher', {})
props = posting.get('properties', {})
print('Voucher fields:')
for k in sorted(props.keys()):
    v = props[k]
    desc = v.get('description', '')[:100]
    typ = v.get('type', '')
    ref = v.get('$ref', '')
    print(f'  {k}: type={typ} ref={ref} desc={desc}')

# Also check AccountingDimensionValue
adv = spec.get('components', {}).get('schemas', {}).get('AccountingDimensionValue', {})
props2 = adv.get('properties', {})
print('\nAccountingDimensionValue fields:')
for k in sorted(props2.keys()):
    v = props2[k]
    desc = v.get('description', '')[:100]
    typ = v.get('type', '')
    ref = v.get('$ref', '')
    print(f'  {k}: type={typ} ref={ref} desc={desc}')

# Also check AccountingDimensionName
adn = spec.get('components', {}).get('schemas', {}).get('AccountingDimensionName', {})
props3 = adn.get('properties', {})
print('\nAccountingDimensionName fields:')
for k in sorted(props3.keys()):
    v = props3[k]
    desc = v.get('description', '')[:100]
    typ = v.get('type', '')
    ref = v.get('$ref', '')
    print(f'  {k}: type={typ} ref={ref} desc={desc}')
