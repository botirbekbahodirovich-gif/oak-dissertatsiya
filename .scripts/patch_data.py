from pathlib import Path
p = Path('data.py')
s = p.read_text(encoding='utf-8')
start = s.find('\ndef load_data():')
if start == -1:
    print('load_data not found')
    raise SystemExit(1)
# find end: next '\ndef ' after start+1
end = s.find('\ndef ', start+1)
if end == -1:
    end = len(s)
new_block = '''\ndef load_data():\n    # Prefer database when available, but fall back to CSV file for simpler local setups.\n    try:\n        # Attempt to query the database if psycopg2 is present and DATABASE_URL set\n        database_url = os.environ.get('DATABASE_URL')\n        if psycopg2 and database_url:\n            sql = (\n                'SELECT id, sana AS "Sana", daraja AS "Daraja", olim AS "Olim", '\n                'mavzu AS "Mavzu", ixtisoslik AS "Ixtisoslik", muassasa AS "Muassasa", '\n                'ilmiy_rahbar AS "Ilmiy_rahbar", link AS "Link" '\n                'FROM dissertations ORDER BY id'\n            )\n            return _query_rows(sql)\n    except Exception:\n        # fall through to CSV fallback\n        pass\n\n    # Fallback: load from the packaged CSV file `data/dissertatsiyalar.csv`.\n    csv_path = os.path.join(os.path.dirname(__file__), 'data', 'dissertatsiyalar.csv')\n    if not os.path.exists(csv_path):\n        return []\n    rows = []\n    try:\n        with open(csv_path, newline='', encoding='utf-8') as fh:\n            reader = csv.DictReader(fh)\n            for idx, r in enumerate(reader, start=1):\n                row = {\n                    'id': r.get('id') or idx,\n                    'Sana': r.get('Sana') or r.get('sana') or '',\n                    'Daraja': r.get('Daraja') or r.get('daraja') or '',\n                    'Olim': r.get('Olim') or r.get('olim') or '',\n                    'Mavzu': r.get('Mavzu') or r.get('mavzu') or '',\n                    'Ixtisoslik': r.get('Ixtisoslik') or r.get('ixtisoslik') or '',\n                    'Muassasa': r.get('Muassasa') or r.get('muassasa') or '',\n                    'Ilmiy_rahbar': r.get('Ilmiy_rahbar') or r.get('ilmiy_rahbar') or '',\n                    'Link': r.get('Link') or r.get('link') or ''\n                }\n                rows.append(normalize_row(row))\n    except Exception:\n        return []\n    return rows\n'''
new_s = s[:start] + new_block + s[end:]
backup = Path('data.py.bak')
backup.write_text(s, encoding='utf-8')
p.write_text(new_s, encoding='utf-8')
print('Patched data.py, backup at data.py.bak')
