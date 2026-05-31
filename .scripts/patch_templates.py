from pathlib import Path
for tpl in ['templates/login.html', 'templates/register.html']:
    p = Path(tpl)
    s = p.read_text(encoding='utf-8')
    if "{{ csrf_token() }}" in s:
        print(tpl + ' already has csrf')
        continue
    # find opening form tag
    i = s.find('<form')
    if i == -1:
        print('form not found in', tpl)
        continue
    j = s.find('>', i)
    if j == -1:
        print('form close not found in', tpl)
        continue
    new_s = s[:j+1] + '\n      {{ csrf_token() }}' + s[j+1:]
    p.write_text(new_s, encoding='utf-8')
    print('Patched', tpl)
