import urllib.request
import re

url = "https://docs.google.com/forms/d/e/1FAIpQLSd6elawoPmmMVY3pqfKoZocmUWwz9amq20jq11JKJipfouzFg/viewform"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
html = urllib.request.urlopen(req).read().decode('utf-8', errors='ignore')

# data-params contains the question and its ID
import json
matches = re.findall(r'data-params="([^"]+)"', html)

for m in matches:
    try:
        data = m.replace('&quot;', '"')
        parsed = json.loads(data)
        title = parsed[0][1]
        entry_id = parsed[0][4][0][0]
        print(f"{title}: entry.{entry_id}")
    except Exception as e:
        pass

