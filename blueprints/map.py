"""Tadqiqot xaritasi — interactive Uzbekistan region map.

One public page (/tadqiqot-xaritasi) rendering a D3 + TopoJSON choropleth of the
14 regions, plus a JSON API (/api/regions/stats) with per-region researcher,
dissertation and institution counts.

Data source: the same dissertation → institution_map aggregation the reyting
page already builds and caches for 30 min (`blueprints.reyting._build_rating`).
Region attribution comes from `institution_map.region`, whose canonical values
are short, apostrophe-free strings ('Buxoro', 'Fargona', 'Qoraqalpogiston',
'Toshkent', …) — see `migrate_institutions._REGION_RULES`. We never re-query the
DB here; we slice the cached structure, so the map is effectively free.
"""
from flask import Blueprint, jsonify, render_template

map_bp = Blueprint('map', __name__)

# TopoJSON `properties.name` (datamaps uzb.topo.json, objects key "uzb")
#   → (Uzbek display label, institution_map.region DB value).
# The DB has no separate Toshkent shahri / viloyati split, so both "Tashkent"
# features on the map resolve to the single "Toshkent" aggregate.
REGION_MAP = {
    "Karakalpakstan": ("Qoraqalpog'iston", "Qoraqalpogiston"),
    "Andijon":        ("Andijon",          "Andijon"),
    "Bukhoro":        ("Buxoro",           "Buxoro"),
    "Ferghana":       ("Farg'ona",         "Fargona"),
    "Jizzakh":        ("Jizzax",           "Jizzax"),
    "Khorezm":        ("Xorazm",           "Xorazm"),
    "Namangan":       ("Namangan",         "Namangan"),
    "Navoi":          ("Navoiy",           "Navoiy"),
    "Kashkadarya":    ("Qashqadaryo",      "Qashqadaryo"),
    "Samarkand":      ("Samarqand",        "Samarqand"),
    "Sirdaryo":       ("Sirdaryo",         "Sirdaryo"),
    "Surkhandarya":   ("Surxondaryo",      "Surxondaryo"),
    "Tashkent":       ("Toshkent",         "Toshkent"),
}


@map_bp.route('/tadqiqot-xaritasi')
def research_map():
    return render_template('map.html')


@map_bp.route('/api/regions/stats')
def regions_stats():
    """Per-region counts keyed by TopoJSON name. Always 200 with every region
    present (zeros if aggregation fails) so the map still renders."""
    # seed every DB region with zeros
    agg = {db: {'researchers': 0, 'dissertations': 0, 'institutions': 0}
           for _uz, db in REGION_MAP.values()}
    try:
        from blueprints.reyting import _build_rating
        data = _build_rating()
        for o in data['orgs']:
            a = agg.get(o.get('region'))
            if a is None:
                continue
            a['institutions'] += 1
            a['dissertations'] += o.get('diss', 0)
            a['researchers'] += o.get('scholars', 0)
    except Exception:
        pass  # degrade to zeros — map renders grey rather than erroring

    out = {}
    for topo_name, (uz, db) in REGION_MAP.items():
        a = agg[db]
        out[topo_name] = {
            'uz_name': uz,
            'db_region': db,
            'researchers': a['researchers'],
            'dissertations': a['dissertations'],
            'institutions': a['institutions'],
        }
    return jsonify(out)
