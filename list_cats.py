import requests, json, urllib3; urllib3.disable_warnings()
auth=('0','eyJ0b2tlbklkIjoyMTQ3NjI5MzM3LCJ0b2tlbiI6IjQ4MTIyOGIzLWU3MWUtNDBiOC1hOWJjLTVmY2I3YTc2YmI0NyJ9')
base='https://kkpqfuj-amager.tripletex.dev/v2'
r=requests.get(f'{base}/travelExpense/costCategory', auth=auth, verify=False)
for c in r.json()['values']:
    print(f"{c['id']}: {c.get('description','')}")
