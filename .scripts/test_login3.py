import re,sys,os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app import app
c = app.test_client()
r = c.get('/login')
m = re.search(rb'name="csrf_token" value="([^"]+)"', r.data)
print('got', bool(m))
token = m.group(1).decode() if m else None
print('token:', token)
r2 = c.post('/login', data={'username':'admin','password':'admin','csrf_token': token}, follow_redirects=False)
print('status', r2.status_code)
print('location:', r2.headers.get('Location'))
