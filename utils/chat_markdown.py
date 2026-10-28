"""AI javobidagi Markdown'ni xavfsiz HTML'ga aylantirish (AI yordamchi chati uchun).

Xavfsizlik: butun matn AVVAL html.escape qilinadi, keyin faqat shu modul o'zi
yozadigan teglar qo'shiladi — <p>, <br>, <strong>, <em>, <code>, <ul>, <ol>, <li>.
Model yoki foydalanuvchi matnidagi hech qanday HTML/atribut/havola o'tmaydi.
"""
import html
import re

_BULLET = re.compile(r'^\s*[-*•]\s+(.*)$')
_NUMBERED = re.compile(r'^\s*\d+[.)]\s+(.*)$')
_HEADING = re.compile(r'^\s*#{1,6}\s+(.*)$')


def _inline(text):
    text = re.sub(r'`([^`\n]+)`', r'<code>\1</code>', text)
    text = re.sub(r'\*\*(?=\S)(.+?)(?<=\S)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'__(?=\S)(.+?)(?<=\S)__', r'<strong>\1</strong>', text)
    text = re.sub(r'(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])', r'<em>\1</em>', text)
    return text


def render_chat_markdown(text):
    lines = html.escape(text or '').replace('\r\n', '\n').split('\n')
    out, para, list_tag = [], [], None

    def flush_para():
        if para:
            out.append('<p>' + '<br>'.join(_inline(l) for l in para) + '</p>')
            para.clear()

    def close_list():
        nonlocal list_tag
        if list_tag:
            out.append(f'</{list_tag}>')
            list_tag = None

    for line in lines:
        m_b, m_n = _BULLET.match(line), _NUMBERED.match(line)
        if m_b or m_n:
            tag = 'ul' if m_b else 'ol'
            flush_para()
            if list_tag != tag:
                close_list()
                out.append(f'<{tag}>')
                list_tag = tag
            out.append('<li>' + _inline((m_b or m_n).group(1)) + '</li>')
            continue
        close_list()
        if not line.strip():
            flush_para()
            continue
        m_h = _HEADING.match(line)
        if m_h:
            flush_para()
            out.append('<p><strong>' + _inline(m_h.group(1)) + '</strong></p>')
            continue
        para.append(line.strip())
    flush_para()
    close_list()
    return ''.join(out)
