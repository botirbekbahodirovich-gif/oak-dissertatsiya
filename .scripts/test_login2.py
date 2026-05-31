import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app import app
c = app.test_client()
r = c.post('/login', data={'username':'admin','password':'admin'}, follow_redirects=False)
print('status', r.status_code)
print('headers', r.headers.get('Location'))
