import traceback
from app import app
app.testing = True
client = app.test_client()

try:
    r = client.get('/')
    print('status', r.status_code)
    print(r.data.decode()[:4000])
except Exception:
    print('Exception during GET /')
    traceback.print_exc()

try:
    r = client.get('/stats')
    print('status', r.status_code)
    print(r.data.decode()[:4000])
except Exception:
    print('Exception during GET /stats')
    traceback.print_exc()
