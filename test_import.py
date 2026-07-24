
import os

os.environ.setdefault("APP_ENV", "testing")
os.environ.setdefault("SESSION_SECRET", "import-test-secret")
os.environ["DATABASE_URL"] = ""
os.environ["POSTGRES_URL"] = ""

try:
    import app
    print("SUCCESS: Flask app imported successfully!")
    print("CSRF protection is enabled")
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()
