"""Verify initial auth over loopback; prints no password or session token."""

import http.client
import json
from pathlib import Path
from urllib.parse import urlsplit

credentials = json.loads(Path('/data/.bootstrap-owner.json').read_text())
host = urlsplit(credentials['url']).netloc
connection = http.client.HTTPConnection('127.0.0.1', 8000, timeout=10)
headers = {'Host': host, 'Origin': credentials['url'], 'Content-Type': 'application/json'}
connection.request('GET', '/api/items', headers=headers)
response = connection.getresponse()
assert response.status == 401, response.status
response.read()
connection.request('POST', '/api/auth/login', json.dumps({
    'username': credentials['username'], 'password': credentials['password']
}), headers)
response = connection.getresponse()
assert response.status == 200, response.status
cookie = response.getheader('Set-Cookie')
assert 'HttpOnly' in cookie and 'Secure' in cookie
response.read()
headers['Cookie'] = cookie.split(';')[0]
connection.request('GET', '/api/items', headers=headers)
response = connection.getresponse()
assert response.status == 200, response.status
items = json.loads(response.read())
connection.request('POST', '/api/auth/logout', '{}', headers)
response = connection.getresponse()
assert response.status == 200, response.status
response.read()
print(f'Owner login, authenticated library ({len(items)} items), secure cookie flags, and logout verified.')
