import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app import app
import re
with app.test_client() as c:
    resp = c.get('/login')
    m = re.search(rb'name="csrf_token" value="([^"]+)"', resp.data)
    print('csrf', bool(m))
    if m:
        token = m.group(1).decode()
        r = c.post('/login', data={'username':'admin','password':'admin','csrf_token': token}, follow_redirects=False)
        print('post status', r.status_code)
        print('set-cookie', r.headers.get('Set-Cookie'))
        r2 = c.get('/dashboard', follow_redirects=False)
        print('dashboard status', r2.status_code)
        print('dashboard location', r2.headers.get('Location'))
        print('dashboard data snippet', r2.data[:300].decode(errors='ignore'))
