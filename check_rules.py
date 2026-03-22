import yaml

with open('rules.yaml', 'r', encoding='utf-8') as f:
    data = yaml.safe_load(f)

rules = data['rules']
print(f'Total rules: {len(rules)}')

# Verify all IDs are unique
ids = [r['id'] for r in rules]
dupes = [i for i in ids if ids.count(i) > 1]
if dupes:
    print(f'DUPLICATE IDs: {set(dupes)}')
else:
    print('All rule IDs unique')

# Print last 15 rule IDs
print('Last 15 rules:')
for r in rules[-15:]:
    print(f"  - {r['id']}")
