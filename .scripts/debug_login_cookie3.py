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
        print('location', r.headers.get('Location'))
        print('response snippet:', r.data[:500].decode(errors='ignore'))
