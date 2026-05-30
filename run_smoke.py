#!/usr/bin/env python3
import sys
from importlib import import_module
sys.path.insert(0, '/home/botirbek/oak-dissertatsiya')

endpoints = ['/', '/login', '/register', '/stats', '/profile', '/dissertation/1', '/supervisor/Test', '/university/Test']

try:
    mod = import_module('app')
    app = getattr(mod, 'app')
    client = app.test_client()
    for ep in endpoints:
        resp = client.get(ep)
        loc = resp.headers.get('Location', '')
        body = resp.get_data(as_text=True)[:200]
        print(ep, resp.status_code, '->', loc)
        print(body.replace('\n',' ')[:200])
        print('---')
except Exception as e:
    import traceback
    traceback.print_exc()
    sys.exit(2)

