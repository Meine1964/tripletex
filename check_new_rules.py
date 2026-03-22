import yaml

data = yaml.safe_load(open('rules.yaml', encoding='utf-8'))
rules = data.get('rules', [])
print(f'Total rules: {len(rules)}')

ids = [r['id'] for r in rules]
dups = set(x for x in ids if ids.count(x) > 1)
print(f'Duplicates: {dups if dups else "none"}')

print('Last 5 rules:')
for r in rules[-5:]:
    print(f"  {r['id']}")

# Check new rules specifically
new_ids = ['activity-no-project', 'get-posting-dates', 'get-project-no-project-subfield']
for nid in new_ids:
    found = [r for r in rules if r['id'] == nid]
    if found:
        print(f"\n✓ Rule '{nid}' found:")
        print(f"  description: {found[0].get('description','')}")
        print(f"  when: {found[0].get('when','')}")
    else:
        print(f"\n✗ Rule '{nid}' NOT FOUND!")
