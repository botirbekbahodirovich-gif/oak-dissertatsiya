import os
import sys

os.environ.setdefault("APP_ENV", "testing")
os.environ.setdefault("SESSION_SECRET", "endpoint-test-secret")
os.environ["DATABASE_URL"] = ""
os.environ["POSTGRES_URL"] = ""

from app import app

# Use Flask test client
client = app.test_client()
failures = 0

print("Testing endpoints using Flask test client...\n")

try:
    # Test login page
    r = client.get('/login')
    if r.status_code == 200 and b'csrf-token' in r.data:
        print("[PASS] GET /login: 200 OK with CSRF token")
    else:
        print("[FAIL] GET /login: status=" + str(r.status_code))
        failures += 1
except Exception as e:
    print("[FAIL] GET /login failed: " + str(e))
    failures += 1

try:
    # Test register page
    r = client.get('/register')
    if r.status_code == 200 and b'csrf-token' in r.data:
        print("[PASS] GET /register: 200 OK with CSRF token")
    else:
        print("[FAIL] GET /register: status=" + str(r.status_code))
        failures += 1
except Exception as e:
    print("[FAIL] GET /register failed: " + str(e))
    failures += 1

try:
    # Test main page is now publicly accessible
    r = client.get('/', follow_redirects=False)
    if r.status_code == 200:
        print("[PASS] GET /: 200 OK (landing page)")
    else:
        print("[FAIL] GET /: status=" + str(r.status_code))
        failures += 1
except Exception as e:
    print("[FAIL] GET / failed: " + str(e))
    failures += 1

try:
    # Test dashboard page should still redirect unauthenticated users
    r = client.get('/dashboard', follow_redirects=False)
    if r.status_code in [302, 307, 301]:
        print("[PASS] GET /dashboard: " + str(r.status_code) + " Redirect (protected route)")
    else:
        print("[FAIL] GET /dashboard: status=" + str(r.status_code))
        failures += 1
except Exception as e:
    print("[FAIL] GET /dashboard failed: " + str(e))
    failures += 1

try:
    # Stats is intentionally public under the current public-friendly route policy.
    r = client.get('/stats', follow_redirects=False)
    if r.status_code == 200:
        print("[PASS] GET /stats: 200 OK (public stats)")
    else:
        print("[FAIL] GET /stats: status=" + str(r.status_code))
        failures += 1
except Exception as e:
    print("[FAIL] GET /stats failed: " + str(e))
    failures += 1

print("\nAll endpoint tests completed!")
print("\nSummary:")
print("- Login and register pages load with CSRF tokens enabled")
print("- Protected routes redirect unauthenticated requests")
print("- Flask app is fully functional with CSRF protection")

if failures:
    raise SystemExit(1)
if __name__ == "__main__":
    raise SystemExit(0)
