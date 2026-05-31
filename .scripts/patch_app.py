from pathlib import Path
p = Path('app.py')
s = p.read_text(encoding='utf-8')
needle = 'csrf = CSRFProtect(app)'
if needle not in s:
    print('needle not found')
    raise SystemExit(1)
ins = """
from flask_wtf.csrf import generate_csrf

@app.context_processor
def _inject_csrf_token():
    return dict(csrf_token=lambda: '<input type="hidden" name="csrf_token" value="%s">' % generate_csrf())
"""
new_s = s.replace(needle, needle + '\n' + ins)
p.write_text(new_s, encoding='utf-8')
print('Patched app.py')
