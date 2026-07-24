---
name: olimlar-ui-review
description: Frontend/UX review checklist for templates, CSS, navigation, responsive layout, and frontend JS in Olimlar.uz.
---

# olimlar-ui-review

## When to use

Task touches: any file under `templates/` or `static/`, a new page/route
with a rendered response, navigation, or an analytics/map display page.

## Read first

- The template being changed, plus `templates/base.html` (layout shell)
  and any shared partial it pulls in from `templates/partials/`
  (`cta_banner.html`, `seo_meta.html`) or `_reminders_widget.html`.
- Nearby existing pages of the same kind (e.g. another blueprint's
  listing page) before inventing a new layout pattern.

## Steps

1. Identify the closest existing page/pattern and reuse its structure,
   CSS classes, and copy tone (Uzbek, matching the rest of the site)
   instead of designing something new.
2. Check both desktop and ~390px mobile widths.
3. Check for horizontal overflow at 390px — a common regression source
   with tables/wide content.
4. Check touch target sizing on mobile for any new interactive element.
5. Cover all states relevant to the component: loading, empty, error,
   disabled, success — not just the populated/happy path.
6. Check label/focus order and that interactive elements are real
   `<button>`/`<a>`, not `<div onclick>`.
7. Re-read visible Uzbek copy for naturalness — not machine-translated
   phrasing.
8. If the page surfaces data (a table, a stat), check it against
   "Key data entities" in `AGENTS.md` — don't invent a field name that
   doesn't exist on the backing table.

## Browser verification

If a browser tool is available: start the dev server with
`python run_server.py` (port 8000, `FLASK_ENV=development`), load the
page at desktop and 390px width, check the browser console for errors,
and exercise the actual flow relevant to the change (e.g. a form
submission, a filter, pagination). If no browser tool is available, say
so explicitly in the report rather than claiming visual verification.

## Do not

- Don't invent a new design system, color, or component pattern when an
  existing template already solves the same UI need.
- Don't add a new nav entry, page, or section that's empty or
  placeholder content.
- Don't build a near-duplicate of an existing analytics/map/listing page
  — check `blueprints/` + `templates/` first (e.g. don't build a second
  clustering view when `analytics.py` already has one).
- Don't ship a component with only the success state implemented.
- Don't assume browser verification happened if you didn't actually run
  it.
