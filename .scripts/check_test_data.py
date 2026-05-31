import csv
from pathlib import Path
path = Path('data/dissertatsiyalar.csv')
print('exists', path.exists())
with path.open(encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for i, row in enumerate(reader, start=1):
        if any('test' in str(v).lower() for v in row.values()):
            print(i, row)
