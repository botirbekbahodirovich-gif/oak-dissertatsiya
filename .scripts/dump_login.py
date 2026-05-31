import sys,os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app import app
c = app.test_client()
r = c.get('/login')
print(r.data.decode())
