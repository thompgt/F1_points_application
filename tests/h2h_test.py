import urllib.request
import urllib.parse
import json

def get_json(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, json.load(r)
    except Exception as e:
        return None, str(e)

base = 'http://127.0.0.1:8000'
print('Testing', base)

s_status, seasons = get_json(base + '/api/seasons')
print('/api/seasons ->', s_status)
print('seasons sample:', (seasons.get('seasons')[:5] if isinstance(seasons, dict) and 'seasons' in seasons else seasons))

# Test head-to-head for drivers 1 and 3 and a sample season if available
season = None
if isinstance(seasons, dict) and 'seasons' in seasons and seasons['seasons']:
    season = seasons['seasons'][-1]
else:
    season = 2014

params = urllib.parse.urlencode({'driver1_id': 1, 'driver2_id': 3, 'season': season, 'mode': 'season'})
url = base + '/api/head-to-head?' + params
h_status, h_data = get_json(url)
print('/api/head-to-head ->', h_status)
if isinstance(h_data, dict):
    print('driver1_stats keys:', list(h_data.get('driver1_stats', {}).keys()))
    print('race_by_race count:', len(h_data.get('race_by_race', [])))
else:
    print('head-to-head response:', h_data)

# Quick check root page
try:
    with urllib.request.urlopen(base + '/', timeout=10) as r:
        print('/ ->', r.status)
except Exception as e:
    print('/', 'error', e)

print('Done')
