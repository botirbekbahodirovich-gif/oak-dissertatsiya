import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app import app
c = app.test_client()
print('GET /login', c.get('/login').status_code)
resp = c.get('/login')
import re
m = re.search(rb'name="csrf_token" value="([^"]+)"', resp.data)
print('csrf present', bool(m))
if m:
    token = m.group(1).decode()
    r = c.post('/login', data={'username':'admin','password':'admin','csrf_token': token}, follow_redirects=False)
    print('POST /login status', r.status_code, 'location', r.headers.get('Location'))
    if r.status_code in (302,301):
        r2 = c.get('/dashboard')
        print('GET /dashboard after login', r2.status_code)
        r3 = c.get('/stats')
        print('GET /stats after login', r3.status_code)
        r4 = c.get('/supervisor/test')
        print('GET /supervisor/test', r4.status_code)
        r5 = c.get('/university/test')
        print('GET /university/test', r5.status_code)
