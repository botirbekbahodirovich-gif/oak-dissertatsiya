from app import app
c = app.test_client()
r = c.post('/login', data={'username':'admin','password':'admin'}, follow_redirects=False)
print('status', r.status_code)
print('headers', r.headers.get('Location'))
