"""Canonical institution ID backfill: a pure planner plus a separate writer.

build_plan() only takes plain rows and returns a Plan, so the dry run never
touches the database beyond its read-only SELECTs. apply_plan() is the only
code that writes, and scripts/backfill_institution_ids.py calls it only with
--apply.

Rules
- Every active institution_map variant (cyrillic_name -> canonical group) is an
  approved alias of its group (source='legacy_merge'): the 896 manual merges
  carry over instead of landing in the review queue.
- Group category/region = majority vote over its variants; a category tie
  becomes 'boshqa' and is flagged in the queue (source='category_tie').
- classify_institution_name(): "huzuridagi" / "Fanlar akademiyasi ... instituti"
  is ONE organisation with parent_id = the parent's ID; "hamda" or two full
  names joined by "va" are TWO organisations - the defence keeps the first as
  dissertations.institution_id and gets the rest in dissertation_institutions.
- Raw OAK text (dissertations.muassasa, institution_map) is never rewritten.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from institution_identity import classify_institution_name, normalize_institution_identity

SOURCE = 'legacy_merge'
# Spellings of the Academy of Sciences as a parent ("FA ... instituti").
_ACADEMY_KEYS = ('ozbekiston respublikasi fanlar akademiyasi', 'ozbekiston fanlar akademiyasi',
                 'ozr fanlar akademiyasi', 'fanlar akademiyasi', 'ozr fa')
CSV_HEADER = ['eski_guruh', 'variantlar', 'dissertatsiyalar', 'taklif_id',
              'parent_id', 'kategoriya', 'qoida', 'muammo']


@dataclass
class Plan:
    institutions: dict = field(default_factory=dict)  # key -> {name, category, region, parent, existing_id}
    aliases: dict = field(default_factory=dict)       # alias key -> {raw, target, alias_type}
    links: dict = field(default_factory=dict)         # raw -> primary target key (every variant)
    co_links: dict = field(default_factory=dict)      # raw -> [co target keys]
    queue: list = field(default_factory=list)         # (raw, key, reason, source, suggested key)
    rows: list = field(default_factory=list)          # CSV rows
    stats: Counter = field(default_factory=Counter)


def _majority(values):
    """(winner, tie) over non-empty values; (None, False) when there are none."""
    counts = Counter(v for v in values if v)
    if not counts:
        return None, False
    ranked = counts.most_common()
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return None, True
    return ranked[0][0], False


def build_plan(map_rows, diss_counts, existing_institutions=(), existing_aliases=()):
    """map_rows: (cyrillic_name, canonical_name|None, category, region) of ACTIVE rows.
    diss_counts: {TRIM(muassasa): n}. existing_institutions: (id, normalized_name)
    of active, unmerged IDs. existing_aliases: (normalized_name, institution_id)."""
    plan = Plan()
    existing_ids = {key: iid for iid, key in existing_institutions}
    id_to_key = {iid: key for key, iid in existing_ids.items()}
    existing_alias = {key: id_to_key.get(iid, f'id:{iid}') for key, iid in existing_aliases}

    groups = defaultdict(list)
    for raw, canonical, category, region in map_rows:
        raw = (raw or '').strip()
        if raw:
            groups[(canonical or raw).strip()].append((raw, category, region))

    def add_alias(raw, target, alias_type, problems, link=True):
        # The admin's per-variant decision always links that variant's
        # defences; only the reusable key-level alias can conflict. A group's
        # canonical name is a key alias only: the same text may be a raw
        # variant the admin put in another group.
        if link:
            plan.links[raw] = target
        key = normalize_institution_identity(raw)
        if not key:
            return
        owner = plan.aliases.get(key, {}).get('target') or existing_alias.get(key)
        if owner and owner != target:
            problems.append(f"alias_toqnashuvi:{raw}")
            plan.queue.append((raw, key, 'legacy_review', SOURCE, target))
            return
        plan.aliases.setdefault(key, {'raw': raw, 'target': target, 'alias_type': alias_type})

    pending_multi = []   # (group name, variants, parts, row index)
    for name in sorted(groups):
        variants = groups[name]
        problems = []
        cls = classify_institution_name(name)
        category, tie = _majority(c for _, c, _ in variants)
        if tie:
            problems.append('kategoriya_teng')
        category = category or 'boshqa'
        region, region_tie = _majority(r for _, _, r in variants)
        if region_tie:
            problems.append('region_teng')
        diss = sum(diss_counts.get(raw, 0) for raw, _, _ in variants)
        row = {'name': name, 'variants': len(variants), 'diss': diss, 'category': category,
               'rule': cls['kind'], 'problems': problems, 'targets': [], 'parent': None}
        plan.rows.append(row)
        plan.stats['groups'] += 1
        plan.stats['group_diss'] += diss
        if not cls['key']:
            problems.append("bo'sh_kalit")
            continue
        if cls['kind'] == 'multiple':
            pending_multi.append((row, variants, cls['parts']))
            continue
        key = cls['key']
        inst = plan.institutions.get(key)
        if inst:
            problems.append(f"kalit_toqnashuvi:{inst['name']}")
        else:
            plan.institutions[key] = {'name': name, 'category': category, 'region': region,
                                      'parent': cls['parent'], 'existing_id': existing_ids.get(key)}
        if tie:
            plan.queue.append((name, key, 'legacy_review', 'category_tie', key))
        row['targets'] = [key]
        row['parent'] = cls['parent']
        add_alias(name, key, 'canonical', problems, link=False)
        for raw, _, _ in variants:
            same = normalize_institution_identity(raw) == key
            add_alias(raw, key, 'exact' if same else 'legacy_merge', problems)

    known = set(plan.institutions) | set(existing_ids)

    def resolve_key(key):
        if key in known:
            return key
        alias = plan.aliases.get(key)
        return alias['target'] if alias else existing_alias.get(key)

    # Multi-organisation groups: first resolvable part is primary, rest are co.
    for row, variants, parts in pending_multi:
        targets, missing = [], []
        for part in parts:
            target = resolve_key(part)
            if target and target not in targets:
                targets.append(target)
            elif not target:
                missing.append(part)
        if missing:
            row['problems'].append('qism_topilmadi:' + ' | '.join(missing))
        if not targets:
            for raw, _, _ in variants:
                plan.queue.append((raw, normalize_institution_identity(raw), 'multiple', SOURCE, None))
            continue
        row['targets'] = targets
        for raw, _, _ in variants:
            add_alias(raw, targets[0], 'multi_primary', row['problems'])
            if len(targets) > 1:
                plan.co_links[raw] = targets[1:]

    # Parents: only link to an organisation that already has (or gets) an ID.
    academy = next((resolve_key(k) for k in _ACADEMY_KEYS if resolve_key(k)), None)
    for row in plan.rows:
        parent = row['parent']
        if not parent:
            continue
        target = resolve_key(parent) or (academy if parent in _ACADEMY_KEYS else None)
        if target and target != row['targets'][0]:
            plan.institutions[row['targets'][0]]['parent'] = target
            row['parent'] = target
        else:
            plan.institutions[row['targets'][0]]['parent'] = None
            row['problems'].append(f"ota_topilmadi:{parent}")
            row['parent'] = None

    # OAK names never put in institution_map: link exact matches, queue the rest.
    mapped = {raw for variants in groups.values() for raw, _, _ in variants}
    for raw, n in sorted(diss_counts.items()):
        raw = (raw or '').strip()
        if not raw or raw in mapped:
            continue
        cls = classify_institution_name(raw)
        target = resolve_key(cls['key']) if cls['key'] else None
        problems = []
        if target and cls['kind'] != 'multiple':
            add_alias(raw, target, 'exact', problems)
        else:
            reason = 'multiple' if cls['kind'] == 'multiple' else 'unresolved'
            plan.queue.append((raw, cls['key'], reason, 'backfill', None))
            problems.append('navbatga:' + reason)
        plan.rows.append({'name': raw, 'variants': 1, 'diss': n, 'category': '',
                          'rule': 'mapda_yoq', 'problems': problems,
                          'targets': [target] if target and not problems else [], 'parent': None})
        plan.stats['unmapped_names'] += 1
        plan.stats['unmapped_diss'] += n

    linked = set(plan.links)
    plan.stats['institutions_new'] = sum(1 for i in plan.institutions.values() if not i['existing_id'])
    plan.stats['aliases'] = len(plan.aliases)
    plan.stats['co_link_names'] = len(plan.co_links)
    plan.stats['queue'] = len(plan.queue)
    plan.stats['diss_linkable'] = sum(n for raw, n in diss_counts.items() if raw in linked)
    plan.stats['diss_total'] = sum(diss_counts.values())
    return plan


def plan_csv_rows(plan):
    """CSV rows; new IDs are shown as yangi:<n> in plan order."""
    labels, counter = {}, 0
    for key, inst in plan.institutions.items():
        if inst['existing_id']:
            labels[key] = str(inst['existing_id'])
        else:
            counter += 1
            labels[key] = f'yangi:{counter}'
    out = []
    for row in sorted(plan.rows, key=lambda r: (-r['diss'], r['name'])):
        out.append([row['name'], row['variants'], row['diss'],
                    '+'.join(labels.get(t, str(t)) for t in row['targets']),
                    labels.get(row['parent'], '') if row['parent'] else '',
                    row['category'], row['rule'], '; '.join(row['problems'])])
    return out


# ── reads (dry run safe) ────────────────────────────────────────────────────

def read_inputs(cur):
    """Only SELECTs. Tables from migration 020 may be absent on an old DB."""
    cur.execute("""
        SELECT TRIM(cyrillic_name), NULLIF(TRIM(canonical_name), ''), category, region
        FROM institution_map WHERE is_active IS NOT FALSE
    """)
    map_rows = cur.fetchall()
    cur.execute("""
        SELECT TRIM(muassasa), COUNT(*) FROM dissertations
        WHERE NULLIF(TRIM(muassasa), '') IS NOT NULL GROUP BY 1
    """)
    diss_counts = dict(cur.fetchall())
    cur.execute("SELECT to_regclass('public.institutions') IS NOT NULL")
    institutions, aliases = [], []
    if cur.fetchone()[0]:
        cur.execute("""SELECT id, normalized_name FROM institutions
                       WHERE is_active = TRUE AND merged_into_id IS NULL""")
        institutions = cur.fetchall()
        cur.execute("""SELECT normalized_name, institution_id FROM institution_aliases
                       WHERE review_status = 'approved'""")
        aliases = cur.fetchall()
    return map_rows, diss_counts, institutions, aliases


# ── writes (--apply only) ───────────────────────────────────────────────────

def apply_plan(cur, plan, *, admin_username='backfill'):
    """Write the plan in the caller's transaction. Needs migration 021."""
    from psycopg2.extras import Json, execute_values
    from institution_identity import queue_resolution
    from institutions import transliterate_display

    cur.execute("SELECT to_regclass('public.dissertation_institutions') IS NOT NULL")
    if not cur.fetchone()[0]:
        raise RuntimeError('migration 021_institution_links.sql is not applied')
    cur.execute("LOCK TABLE institutions, institution_aliases IN SHARE ROW EXCLUSIVE MODE")
    ids, written = {}, Counter()
    for key, inst in plan.institutions.items():
        cur.execute("""
            INSERT INTO institutions (canonical_name, normalized_name, latin_name, category, region)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (normalized_name) DO UPDATE SET
                category = CASE WHEN institutions.category = 'boshqa'
                                THEN EXCLUDED.category ELSE institutions.category END,
                region = COALESCE(institutions.region, EXCLUDED.region)
            RETURNING id, (xmax = 0)
        """, (inst['name'], key, transliterate_display(inst['name']), inst['category'], inst['region']))
        iid, inserted = cur.fetchone()
        ids[key] = iid
        written['institutions_inserted'] += int(bool(inserted))

    def resolve(target):
        if target in ids:
            return ids[target]
        if isinstance(target, str) and target.startswith('id:'):
            return int(target[3:])
        cur.execute("SELECT id FROM institutions WHERE normalized_name = %s", (target,))
        row = cur.fetchone()
        return row[0] if row else None

    for key, inst in plan.institutions.items():
        if inst['parent']:
            parent_id = resolve(inst['parent'])
            if parent_id and parent_id != ids[key]:
                cur.execute("""UPDATE institutions SET parent_id = %s, updated_at = NOW()
                               WHERE id = %s AND parent_id IS NULL""", (parent_id, ids[key]))
                written['parents'] += cur.rowcount
    for key, alias in plan.aliases.items():
        target_id = resolve(alias['target'])
        if not target_id:
            continue
        cur.execute("""
            INSERT INTO institution_aliases
                (institution_id, raw_name, normalized_name, alias_type, source)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, (target_id, alias['raw'], key, alias['alias_type'], SOURCE))
        written['aliases_inserted'] += cur.rowcount
    link_rows = [(raw, resolve(target)) for raw, target in plan.links.items()]
    link_rows = [(raw, iid) for raw, iid in link_rows if iid]
    if link_rows:  # one statement, so rowcount covers every link
        execute_values(cur, """
            UPDATE dissertations d SET institution_id = v.iid
            FROM (VALUES %s) AS v(raw, iid)
            WHERE d.institution_id IS NULL AND TRIM(d.muassasa) = v.raw
        """, link_rows, template='(%s, %s::bigint)', page_size=len(link_rows))
    written['dissertations_linked'] = cur.rowcount if link_rows else 0
    # earlier approved aliases (scraper/admin) that the plan did not name
    cur.execute("""
        UPDATE dissertations d SET institution_id = a.institution_id
        FROM institution_aliases a
        WHERE d.institution_id IS NULL AND TRIM(d.muassasa) = a.raw_name
          AND a.review_status = 'approved'
    """)
    written['dissertations_linked_by_alias'] = cur.rowcount
    for raw, targets in plan.co_links.items():
        for target in targets:
            co_id = resolve(target)
            if not co_id:
                continue
            cur.execute("""
                INSERT INTO dissertation_institutions (dissertation_id, institution_id, role, source)
                SELECT id, %s, 'co', %s FROM dissertations
                WHERE TRIM(muassasa) = %s AND institution_id IS DISTINCT FROM %s
                ON CONFLICT DO NOTHING
            """, (co_id, SOURCE, raw, co_id))
            written['co_links'] += cur.rowcount
    for raw, key, reason, source, suggested in plan.queue:
        queue_resolution(cur, raw, key or raw, reason, source,
                         resolve(suggested) if suggested else None)
        written['queued'] += 1
    cur.execute("""
        INSERT INTO institution_identity_events (action, payload, admin_username)
        VALUES ('backfill', %s, %s)
    """, (Json({'plan': dict(plan.stats), 'written': dict(written)}), admin_username))
    return written
