
try:
    import app
    print("SUCCESS: Flask app imported successfully!")
    print("CSRF protection is enabled")
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()
