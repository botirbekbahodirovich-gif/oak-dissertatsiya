
import os
import sys
from app import app

os.environ['FLASK_ENV'] = 'development'

if __name__ == '__main__':
    print("Flask server starting on port 8000...")
    sys.stdout.flush()
    app.run(host='0.0.0.0', port=8000, debug=False, threaded=True)
