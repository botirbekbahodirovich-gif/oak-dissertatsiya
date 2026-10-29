"""Scholar name keys shared by the photo matcher and the olim_photos lookup.

strict_key() is the olim_photos.name_norm primary key: any spelling of a name
(Cyrillic or Latin, any apostrophe, any case/spacing) maps to the same
lowercase Latin token string. loose_key() additionally folds patronymic
suffixes, surname gender and common transliteration variants — it is used
only for matching files to names, never as a stored key.

Uzbek Cyrillic: ў→oʻ, ғ→gʻ, қ→q, ҳ→h, ш→sh, ч→ch, ё→yo, ю→yu, я→ya, ц→ts;
word-initial (or post-vowel) е→ye. Apostrophes are dropped from keys because
the photo file names carry none.
"""
import re
import unicodedata

CYR = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo', 'ж': 'j',
    'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o',
    'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'x', 'ц': 'ts',
    'ч': 'ch', 'ш': 'sh', 'щ': 'sh', 'ъ': '', 'ы': 'i', 'ь': '', 'э': 'e', 'ю': 'yu',
    'я': 'ya', 'ў': "o'", 'ғ': "g'", 'қ': 'q', 'ҳ': 'h', 'і': 'i', 'ң': 'n', 'ү': 'u',
    'ө': 'o', 'ә': 'a', 'җ': 'j',
}
APOSTROPHES = "'`ʻʼ‘’ʹ"
_YE_AFTER = 'аеёиоуўэюяьъ'
PATRONYMIC_TAILS = {'ogli', 'ugli', 'qizi', 'kizi', 'ogly', 'kyzy', 'uli', 'qyzy'}


def translit(s):
    """Lowercase Uzbek-Latin form; every apostrophe variant becomes "'"."""
    s = unicodedata.normalize('NFC', s or '').lower()
    out = []
    for i, ch in enumerate(s):
        if ch == 'е' and (i == 0 or not s[i - 1].isalpha() or s[i - 1] in _YE_AFTER):
            out.append('ye')
        else:
            out.append(CYR.get(ch, ch))
    s = ''.join(out)
    for a in APOSTROPHES:
        s = s.replace(a, "'")
    return s


def tokens(s):
    s = translit(s).replace("'", '')
    return re.sub(r'[^a-z]+', ' ', s).split()


def strict_key(name):
    """'Хаитов Хамза Аҳмадович' -> 'xaitov xamza ahmadovich'."""
    return ' '.join(tokens(name))


def loose_key(name):
    """strict tokens minus o'g'li/qizi, -ovich/-evna, -ova and x/h, dj/j, yo/o, ye/e, double letters."""
    parts = [x for x in tokens(name) if x not in PATRONYMIC_TAILS]
    out = []
    for i, x in enumerate(parts):
        if i >= 2:
            x = re.sub(r'(ovich|evich|ovna|evna|ievich|ievna|ich|na)$', '', x)
        if i == 0:
            x = re.sub(r'(ova|eva|ina)$', lambda m: m.group(1)[:-1], x)
        x = x.replace('dj', 'j').replace('x', 'h').replace('kh', 'h').replace('yo', 'o').replace('ye', 'e')
        x = re.sub(r'(.)\1', r'\1', x)
        out.append(x)
    return ' '.join(out)
