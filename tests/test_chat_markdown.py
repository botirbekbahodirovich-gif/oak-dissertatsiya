import re

from utils.chat_markdown import render_chat_markdown as md

ALLOWED = {'p', 'br', 'strong', 'em', 'code', 'ul', 'ol', 'li'}


def _tags(out):
    return set(re.findall(r'</?([a-zA-Z0-9]+)', out))


def test_bold_italic_code():
    assert md('**qalin** va *kursiv* va `kod`') == \
        '<p><strong>qalin</strong> va <em>kursiv</em> va <code>kod</code></p>'


def test_lists_and_paragraphs():
    out = md("Kirish:\n- birinchi\n- **ikkinchi**\n\n1. bir\n2. ikki\nXulosa")
    assert out == ('<p>Kirish:</p><ul><li>birinchi</li><li><strong>ikkinchi</strong></li></ul>'
                   '<ol><li>bir</li><li>ikki</li></ol><p>Xulosa</p>')


def test_heading_becomes_bold_paragraph():
    assert md('## Natija') == '<p><strong>Natija</strong></p>'


def test_line_breaks_inside_paragraph():
    assert md('a\nb') == '<p>a<br>b</p>'


def test_html_injection_is_escaped():
    payloads = [
        '<script>alert(1)</script>',
        '<img src=x onerror=alert(1)>',
        '**<a href="javascript:alert(1)">x</a>**',
        '- <iframe src=//evil></iframe>',
        '`<svg onload=alert(1)>`',
        '"><b onmouseover=alert(1)>x',
    ]
    for p in payloads:
        out = md(p)
        assert _tags(out) <= ALLOWED, (p, out)
        assert '&lt;' in out
        assert '<script' not in out and '<a ' not in out and '<img' not in out and '<iframe' not in out and '<svg' not in out


def test_no_attributes_ever_emitted():
    out = md('**x** *y* `z`\n- a\n1. b\n# h <p class="x">')
    assert not re.search(r'<[a-z]+\s', out)


def test_empty_and_none():
    assert md('') == '' and md(None) == ''


def test_asterisks_in_math_not_italic():
    assert '<em>' not in md('2*3*4 = 24')
