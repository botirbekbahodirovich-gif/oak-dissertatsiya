# -*- coding: utf-8 -*-
"""Fan yo'nalishlari ierarxiyasi — daraja, kod va daraxt yordamchilari.

OAK klassifikatsiyasi shifr orqali ierarxiyani o'zi kodlaydi:

    01.00.00   -> fan tarmog'i        (level 1)
    05.01.00   -> ixtisoslik guruhi   (level 2)
    05.01.04   -> ixtisoslik          (level 3)

Shuning uchun daraja va ota-ona shifrni kod bo'yicha hisoblash mumkin —
sun'iy kategoriya o'ylab topilmaydi. Kodi yo'q kichik yo'nalish (level 4)
ham qo'llab-quvvatlanadi: uning darajasi otasidan +1 olinadi.

Daraxt DB'da parent_id bilan saqlanadi (recursive), lekin katalogda 4 daraja
yetarli — chuqurroq daraxt ham buziladigan joyi yo'q, faqat UI uni yig'ib
ko'rsatadi (progressive disclosure).
"""
from __future__ import annotations

import re

MAX_DEPTH = 6           # himoya chegarasi: bundan chuqur daraxt qabul qilinmaydi
BRANCH_LEVEL = 1
GROUP_LEVEL = 2
SPECIALTY_LEVEL = 3

_CODE_RE = re.compile(r'^(\d{2})\.(\d{2})\.(\d{2})$')


class FieldValidationError(ValueError):
    """Foydalanuvchiga ko'rsatiladigan yo'nalish validatsiya xatosi."""

    def __init__(self, public_message, field=None):
        self.public_message = public_message
        self.field = field
        super().__init__(public_message)


def normalize_code(raw):
    """"5.1.4" / " 05.01.04 " -> "05.01.04"; bo'sh yoki noformat -> None.

    Kod MAJBURIY EMAS (§4): yuqori fan tarmog'ida yoki kichik yo'nalishda
    kod bo'lmasligi mumkin. None qaytishi xato emas.
    """
    if raw is None:
        return None
    value = str(raw).strip()
    if not value:
        return None
    parts = value.split('.')
    if len(parts) == 3 and all(p.strip().isdigit() for p in parts):
        return '.'.join(p.strip().zfill(2) for p in parts)
    return value[:32] or None


def is_oak_code(code):
    return bool(code and _CODE_RE.match(code))


def level_from_code(code):
    """OAK shifridan daraja. Shifr OAK formatida bo'lmasa None."""
    match = _CODE_RE.match(code or '')
    if not match:
        return None
    _, group, specialty = match.groups()
    if group == '00' and specialty == '00':
        return BRANCH_LEVEL
    if specialty == '00':
        return GROUP_LEVEL
    return SPECIALTY_LEVEL


def parent_code(code):
    """Shifrning ota-ona shifri. Fan tarmog'i uchun None."""
    match = _CODE_RE.match(code or '')
    if not match:
        return None
    branch, group, specialty = match.groups()
    if specialty != '00':
        return f'{branch}.{group}.00'
    if group != '00':
        return f'{branch}.00.00'
    return None


def branch_code(code):
    """Shifrning eng yuqori fan tarmog'i shifri ("05.01.04" -> "05.00.00")."""
    match = _CODE_RE.match(code or '')
    return f'{match.group(1)}.00.00' if match else None


def field_slug(name, code=None, level=None):
    """Yo'nalish slugi. Kod bor bo'lsa u qo'shiladi — bir xil nomli
    yo'nalishlar (masalan turli tarmoqdagi "Umumiy pedagogika") to'qnashmaydi."""
    from utils.article_rules import make_slug

    base = make_slug(name, fallback='yonalish', max_len=140)
    if code and is_oak_code(code):
        return f'{base}-{code.replace(".", "")}'
    if code:
        return f'{base}-{make_slug(code, fallback="kod", max_len=20)}'
    return base


def resolve_level(code, parent_level=None):
    """Yangi yo'nalish darajasi: kod aytsa koddan, aytmasa otadan +1."""
    by_code = level_from_code(code)
    if by_code is not None:
        return by_code
    if parent_level is None:
        return BRANCH_LEVEL
    return parent_level + 1


def check_no_cycle(node_id, new_parent_id, parent_of):
    """Circular parent aloqasini rad etadi (§4).

    `parent_of` — {id: parent_id} lug'ati yoki shunday chaqiriladigan funksiya.
    Xato holatlar: o'zini ota qilish, o'z avlodini ota qilish, juda chuqur zanjir.
    """
    if new_parent_id is None:
        return True
    if node_id is not None and int(new_parent_id) == int(node_id):
        raise FieldValidationError("Yo'nalish o'ziga ota bo'la olmaydi.", 'parent_id')

    lookup = parent_of.get if hasattr(parent_of, 'get') else parent_of
    seen = set()
    current = new_parent_id
    depth = 0
    while current is not None:
        current = int(current)
        if node_id is not None and current == int(node_id):
            raise FieldValidationError(
                "Yo'nalishni o'z ichki yo'nalishiga bo'ysundirib bo'lmaydi.", 'parent_id')
        if current in seen:
            raise FieldValidationError(
                "Yo'nalishlar zanjirida halqa aniqlandi.", 'parent_id')
        seen.add(current)
        depth += 1
        if depth > MAX_DEPTH:
            raise FieldValidationError(
                "Yo'nalishlar daraxti juda chuqur (maksimal %d daraja)." % MAX_DEPTH,
                'parent_id')
        current = lookup(current)
    return True


def build_tree(rows):
    """Tekis qatorlardan daraxt quradi: har bir dict ga 'children' qo'shiladi.

    `rows` — id/parent_id kalitlari bor dict ro'yxati. Yetim qator (otasi
    ro'yxatda yo'q) ildizga chiqariladi — sahifa yo'qolgan yo'nalish sababli
    bo'sh qolmasin.
    """
    by_id = {}
    for row in rows:
        node = dict(row)
        node['children'] = []
        by_id[node['id']] = node
    roots = []
    for node in by_id.values():
        parent = by_id.get(node.get('parent_id'))
        if parent is None:
            roots.append(node)
        else:
            parent['children'].append(node)
    key = lambda n: (n.get('sort_order') or 0, n.get('code') or '', n.get('name_uz') or '')
    for node in by_id.values():
        node['children'].sort(key=key)
    roots.sort(key=key)
    return roots


def descendant_ids(field_id, children_of):
    """field_id va uning barcha avlodlari (o'zini ham qo'shadi).

    `children_of` — {parent_id: [child_id, ...]}. Halqa bo'lsa ham tugaydi
    (ko'rilganlar to'plami bilan himoyalangan).
    """
    out = []
    seen = set()
    stack = [field_id]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        out.append(current)
        stack.extend(children_of.get(current, ()))
    return out
