import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app import app
from flask import json
with app.test_client() as c:
    resp = c.get('/login')
    import re
    m = re.search(rb'name="csrf_token" value="([^"]+)"', resp.data)
    print('csrf', bool(m))
    if m:
        token = m.group(1).decode()
        r = c.post('/login', data={'username':'admin','password':'admin','csrf_token': token}, follow_redirects=False)
        print('post status', r.status_code)
        print('set-cookie', r.headers.get('Set-Cookie'))
        print('cookies after post', list(c.cookie_jar))
        r2 = c.get('/dashboard')
        print('dashboard status', r2.status_code)
        print('dashboard loc', r2.headers.get('Location'))
        print('cookies after dashboard get', list(c.cookie_jar))
