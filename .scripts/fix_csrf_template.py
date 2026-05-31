from pathlib import Path
for tpl in ['templates/login.html', 'templates/register.html']:
    p = Path(tpl)
    s = p.read_text(encoding='utf-8')
    if '{{ csrf_token()|safe }}' in s:
        print(tpl, 'already safe')
        continue
    s = s.replace('{{ csrf_token() }}', '{{ csrf_token()|safe }}')
    p.write_text(s, encoding='utf-8')
    print('Updated', tpl)
