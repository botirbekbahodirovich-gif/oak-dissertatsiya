# -*- coding: utf-8 -*-
"""OpenAlex mavzusi -> OAK fan yo'nalishi TAKLIFI (DB'siz, sof Python).

Manba: `data/openalex_oak_mapping.json` — qo'lda ko'rib chiqilgan jadval.
Faqat maqolaning `primary_topic`i ishlatiladi; ierarxiya:

    primary_topic -> subfield (ustun) -> field (subfield id bo'lmasa)

Natijaning `confidence` qiymati:

* ``high`` — import approve paytida `primary_field_id` avtomatik qo'yiladi;
* ``low``  — faqat taklif: maqola "Yo'nalish kerak" ro'yxatiga tushadi va
  admin o'zi tanlaydi;
* ``None`` — aniq mos yo'q. Sun'iy moslashtirilmaydi.

Bu modul OAK yo'nalishini BAZAGA yozmaydi — u faqat shifr va ishonch
darajasini qaytaradi. Shifrni `scientific_fields.id` ga aylantirish
chaqiruvchida (`services/article_import/service.py`).
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

HIGH = 'high'
LOW = 'low'
CONFIDENCE_LEVELS = (HIGH, LOW)

DEFAULT_MAPPING_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data', 'openalex_oak_mapping.json')


@lru_cache(maxsize=4)
def load_mapping(path=DEFAULT_MAPPING_PATH):
    with open(path, encoding='utf-8') as fh:
        data = json.load(fh)
    if not isinstance(data.get('subfields'), dict) or not isinstance(data.get('fields'), dict):
        raise ValueError('openalex_oak_mapping.json: subfields/fields topilmadi')
    return data


def primary_topic(metadata):
    """`metadata_candidate['topics']` ichidan `is_primary` mavzu (yoki None)."""
    for topic in (metadata or {}).get('topics') or []:
        if isinstance(topic, dict) and topic.get('is_primary'):
            return topic
    return None


def suggest_oak_field(metadata, mapping=None):
    """`metadata_candidate` (dict) -> taklif dict.

    Har doim dict qaytaradi (hisobot va `ingest_evidence` uchun):
    ``{'oak_code', 'confidence', 'basis', 'subfield_id', 'field_id',
       'topic', 'openalex_name'}``. Mos yo'q bo'lsa `oak_code=None`,
    `confidence=None`.
    """
    mapping = mapping or load_mapping()
    topic = primary_topic(metadata)
    out = {'oak_code': None, 'confidence': None, 'basis': None,
           'subfield_id': None, 'field_id': None,
           'topic': (topic or {}).get('name'), 'openalex_name': None}
    if topic is None:
        out['basis'] = 'no_primary_topic'
        return out

    subfield_id = _str_id(topic.get('subfield_id'))
    field_id = _str_id(topic.get('field_id'))
    out['subfield_id'], out['field_id'] = subfield_id, field_id

    row, basis = None, None
    if subfield_id and subfield_id in mapping['subfields']:
        row, basis = mapping['subfields'][subfield_id], 'subfield'
    elif field_id and field_id in mapping['fields']:
        row, basis = mapping['fields'][field_id], 'field'

    if row is None:
        out['basis'] = 'unmapped'
        return out
    out['basis'] = basis
    out['openalex_name'] = row.get('name')
    code = row.get('oak_code')
    confidence = row.get('confidence')
    if code and confidence in CONFIDENCE_LEVELS:
        out['oak_code'], out['confidence'] = code, confidence
    return out


def _str_id(value):
    if value in (None, ''):
        return None
    text = str(value).strip().rstrip('/')
    text = text.rsplit('/', 1)[-1]
    return text if text.isdigit() else None
