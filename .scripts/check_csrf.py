import sys,os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app import app
c = app.test_client()
r = c.get('/login')
print('len', len(r.data))
idx = r.data.find(b'csrf_token')
print('index', idx)
if idx!=-1:
    start = max(0, idx-80)
    end = min(len(r.data), idx+80)
    print(r.data[start:end].decode(errors='ignore'))
