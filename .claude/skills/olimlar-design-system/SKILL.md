---
name: olimlar-design-system
description: Mandatory visual contract for Olimlar.uz UI work — light-only academic design, real CSS token names, shared Jinja components, grid, typography, charts, accessibility and prohibited patterns. Read before creating or changing any template, CSS or frontend JS.
---

# olimlar-design-system

## 1. Purpose

Keep every Olimlar.uz screen visually identical in system, not in style
opinion. One token file, one component set, one light theme. This skill is
the enforcement layer: it turns `static/css/olimlar-design-system.css` into
checkable rules so no task invents a second design system.

Design stance: light-only academic interface. Functional principles are
inspired by ScienceDirect (dense, readable, scannable research UI) — it is
**not** a visual copy. Orange is the brand accent only; blue carries every
action, link, active state and focus ring.

## 2. When to use this skill

Read this file **before** the first edit whenever the task touches:

- anything under `templates/` (including `templates/admin/` and
  `templates/components/`, `templates/partials/`);
- anything under `static/css/` or `static/js/` that affects rendering;
- a new route that returns a rendered page;
- navigation, header, footer, breadcrumbs;
- charts, tables, filters, forms, modals, empty/error states;
- a review of "does this page look right".

Do **not** use it as a licence to redesign existing pages. It constrains new
and changed UI only.

## 3. Mandatory source files

Open these before writing UI code:

| File | What you get from it |
| --- | --- |
| [static/css/olimlar-design-system.css](../../../static/css/olimlar-design-system.css) | All tokens + every `.ol-*` component. Single token source. |
| [templates/base.html](../../../templates/base.html) | Shell, font loading, block names (`extra_head`, `content`, `scripts`), the legacy `--oak-*` bridge. |
| [templates/components/page_header.html](../../../templates/components/page_header.html) | `page_header`, `page_header_with_actions`, `breadcrumb` macros. |
| [templates/components/site_header.html](../../../templates/components/site_header.html) | Header, dropdowns, drawer, search overlay. Links come from `utils/navigation.py`. |
| [templates/components/site_footer.html](../../../templates/components/site_footer.html) | Footer columns from `utils/navigation.py:FOOTER_COLUMNS`. |
| [static/js/olimlar-charts.js](../../../static/js/olimlar-charts.js) | `OlimlarCharts.tokens`, `.palette(n)`, `.axis(extra)`. |

Reference pages that already follow the system — copy their structure
instead of inventing one:

- [templates/maqolalar_yonalishlar.html](../../../templates/maqolalar_yonalishlar.html) — cleanest full example: `ol-page` → `page_header` → `ol-section` → `ol-filters` → `ol-empty`.
- [templates/dashboard.html](../../../templates/dashboard.html) — `page_header` + `ol-stat-grid` + facets + table.
- [templates/university_profile.html](../../../templates/university_profile.html) — `ol-tabs` on a detail page.
- [templates/trends.html](../../../templates/trends.html) — Chart.js wired to `OlimlarCharts`.
- Listing/card pages in mid-migration — read them for pattern, **not** for
  token hygiene: [grants.html](../../../templates/grants.html),
  [courses.html](../../../templates/courses.html),
  [journals.html](../../../templates/journals.html),
  [compare.html](../../../templates/compare.html),
  [maqolalar_katalog.html](../../../templates/maqolalar_katalog.html).

## 4. Source of truth hierarchy

When two sources disagree, the higher one wins:

```text
1. static/css/olimlar-design-system.css        (real tokens and components)
2. existing shared Jinja components            (templates/components/*, partials/*)
3. .claude/skills/olimlar-design-system/SKILL.md
4. docs/olimlar-design-system.md
5. the individual task prompt
```

Rules that follow from this order:

- If a task prompt contradicts the design system, **do not silently obey and
  do not silently refuse.** Implement the design-system-compliant version,
  and state the conflict explicitly in the final report.
- If a colour name or value in any document disagrees with the CSS, the CSS
  wins. Use the real token, report the difference, and **do not** rewrite
  production CSS to match a document.
- Never "fix" an existing page's tokens as a side effect of an unrelated
  task. Migration of legacy pages is its own task.

## 5. Color system

Never write a raw HEX/RGB value in templates, CSS or chart config. Use these
tokens, spelled exactly as below (`var(--color-…)`):

| Token | Value | Use for |
| --- | --- | --- |
| `--color-brand-orange` | `#ff4203` | Brand accent only: wordmark dot, brand badge, one accent per view. Never a button fill. |
| `--color-action-blue` | `#0056d6` | Primary buttons, links, active tab/nav, focus ring, checkbox accent. |
| `--color-action-blue-light` | `#6593dc` | Hover border on cards/chips. |
| `--color-action-blue-dark` | `#00429f` | Pressed/hover state of blue fills. |
| `--color-text-primary` | `#0c0f12` | Headings, body, table cells, tooltip surface. |
| `--color-text-secondary` | `#333333` | Labels, nav links, secondary body. |
| `--color-text-muted` | `#666666` | Meta, captions, counts, placeholders, axis ticks. |
| `--color-white` | `#ffffff` | Card, form, modal, header surface. |
| `--color-background` | `#f4f7fb` | Page (body) background — deliberately split from card surface. |
| `--color-background-soft` | `#f6f6f6` | Table head, footer, empty state, neutral badge. |
| `--color-border` | `#e6e6e6` | Every card/field/table outer border. |
| `--color-border-subtle` | `#ecebe9` | Inner row separators, chart grid lines. |
| `--color-orange-soft` | `#fff7ef` | Background of brand badge only. |
| `--color-blue-soft` | `#ebf1f9` | Hover row, chip fill, avatar, nav hover. |
| `--color-success` | `#0c8930` | Confirmed state text/icon. |
| `--color-success-soft` | `#f4faf6` | Success badge/alert background. |
| `--color-error` | `#af1d1d` | Error text, danger button, notification dot. |
| `--color-error-soft` | `#fdf4f4` | Error badge/alert background. |
| `--color-notice` | `#ff9900` | Warning icon, premium star, highlight mark. |
| `--color-notice-soft` | `#fff9e6` | Warning badge background, search highlight. |
| `--color-ai-background` | `#171426` | **Reserved.** Semantic AI surfaces only. Currently has zero consumers. Never use it for a page, card, header or "dark mode". |

Rules:

1. `color-scheme: light` is declared on `:root`. Never add `dark` or
   `light dark`, and never add `prefers-color-scheme: dark` styling.
2. Colour alone must never carry state. Every status needs text, icon or
   label as well as colour (`.ol-badge--error` has the word, not just red).
3. Soft backgrounds pair only with their saturated text token
   (`--color-error-soft` + `--color-error`). Do not mix pairs.
4. One brand-orange accent per viewport at most. If a screen needs two
   emphases, the second one is blue.
5. Chart colours come from `OlimlarCharts.palette(n)` — see §15.

## 6. Typography

| Token | Value |
| --- | --- |
| `--font-serif` | `'Noto Serif', Georgia, 'Times New Roman', serif` |
| `--font-sans` | `'Inter', 'Manrope', system-ui, -apple-system, 'Segoe UI', sans-serif` |
| `--font-display` | `'Manrope', 'Inter', system-ui, sans-serif` (a **sans** face despite the name) |

Scale: `--text-hero` 56 · `--text-h1` 48 · `--text-h2` 40 · `--text-h3` 32 ·
`--text-h4` 24 · `--text-h5` 20 · `--text-body-lg` 20 · `--text-body` 16 ·
`--text-small` 14 · `--text-meta` 12.
Line height: `--leading-heading` 1.25 · `--leading-body` 1.5 ·
`--leading-meta` 1.4.

Rules:

1. **H1 and H2 are serif. H3–H6 and all UI text are sans.** This is already
   enforced element-wide in the CSS; do not override `font-family` on a
   heading to "fix" it.
2. Exactly **one `<h1>` per page**, produced by the `page_header` macro.
   Section titles are `<h2>`; do not skip levels for size — use `.ol-h3`,
   `.ol-h4`, `.ol-h5` classes to change size.
3. Never set a font size in `px`/`rem` literals in new code. Use the tokens.
4. Uzbek is the UI language, including `aria-label`, placeholders and empty
   states. Fonts ship latin + cyrillic subsets — OAK data may be Cyrillic.
5. Fonts are self-hosted in `base.html`. Do not add a Google Fonts or other
   external font request.

## 7. Grid and spacing

- Spacing tokens only: `--space-0/4/8/12/16/20/24/32/40/48/64/80/96/112/128/144/160`
  (plus the legacy `--space-6`, see §7 note). Base unit 4px. No arbitrary
  values like `margin: 18px` in new code.
- Grid: `.ol-grid` = 12 columns desktop/tablet, **6 columns at ≤649px**.
- Gutter: `--gutter` 20px, 16px at ≤1023px, 12px at ≤649px.
- Container: `.ol-container` — `max-width: var(--container-max)` = **1600px**,
  padding `clamp(24px, 8vw, 130px)`; `.ol-container--wide` uses
  `clamp(16px, 3vw, 40px)`.
  **`base.html` already wraps `{% block content %}` in
  `<main class="ol-container ol-container--wide">`. Do not add another
  container inside the block** — it doubles the side padding.
- Page frame: wrap content in `.ol-page`; separate blocks with `.ol-section`
  (`+ .ol-section` gets 64px top margin); stack small items with `.ol-stack`.
- Radius: `--radius-default` **4px** (cards, fields, buttons, tables),
  `--radius-banner` **8px** (banners), `--radius-rounded` 24px (badges,
  chips), `--radius-full` (avatars, dots only).
- Shadow: exactly one — `--shadow-dropdown`
  (`0 4px 12px rgba(12,15,18,0.08)`), and only on `.ol-menu`-style overlays.
  Cards, tables and panels use a 1px border, never a shadow.

Note: `--space-6` (6px) exists and is off the 4px scale. It is legacy, used
by `.ol-chip`/`.ol-nav` gaps. Do not add new 6px spacing; do not delete the
token either.

## 8. Shared components

Reuse before you build. Jinja components that already exist:

| Component | Import |
| --- | --- |
| Page header + breadcrumb | `{% from "components/page_header.html" import page_header %}` (or `page_header_with_actions` inside `{% call %}`) |
| Header / footer | rendered by `base.html`; links from `utils/navigation.py` — never hardcode a nav link |
| Admin service grid | `components/admin_services_grid.html` |
| SEO meta | `{% from 'partials/seo_meta.html' import seo_meta %}` in `{% block seo_extra %}` |
| Login gate modal | `components/login_required_modal.html` |
| Field tree, article filters/bits | `components/field_tree.html`, `components/article_filters.html`, `components/article_bits.html` |

CSS components in the token file (use the class, do not clone it):
`ol-container`, `ol-container--wide`, `ol-grid`, `ol-page`, `ol-section`,
`ol-stack`, `ol-empty`, `ol-hero-title`, `ol-h1`–`ol-h5`, `ol-body-lg`,
`ol-body`, `ol-small`, `ol-meta`, `ol-muted`, `ol-skip-link`, `ol-btn`,
`ol-card`, `ol-badge`, `ol-field-group`, `ol-label`, `ol-field`,
`ol-table-wrap`, `ol-table`, `ol-breadcrumb`, `ol-page-header`,
`ol-section-header`, `ol-stat-grid`, `ol-stat`, `ol-filters`, `ol-chips`,
`ol-chip`, `ol-chart`, `ol-tabs`, `ol-tab`, `ol-pager`, `ol-facets`,
`ol-header`, `ol-brand`, `ol-nav`, `ol-menu`, `ol-icon-btn`, `ol-account`,
`ol-avatar`, `ol-drawer`, `ol-acc`, `ol-footer`.

Standard page skeleton:

```jinja
{% extends "base.html" %}
{% from "components/page_header.html" import page_header %}
{% block title %}Sahifa nomi | Olimlar.uz{% endblock %}
{% block content %}
{# base.html already supplies <main class="ol-container ol-container--wide"> #}
<div class="ol-page">
  {{ page_header("Sahifa nomi", "Bir jumlalik izoh.",
                 crumbs=[('Bosh sahifa', '/')]) }}
  <section class="ol-section" aria-labelledby="sec-1">
    <h2 id="sec-1" class="ol-h4">Bo'lim</h2>
    …
  </section>
</div>
{% endblock %}
```

Page-local CSS goes in `{% block extra_head %}` and must be namespaced with
a short page prefix (`.mq-`, `.up-`, `.jr-`). Never style a bare element or
a global class from a page.

## 9. Cards and panels

- Card = `.ol-card`: white surface, 1px `--color-border`, 4px radius, 24px
  padding (16px at ≤649px), hover raises border to
  `--color-action-blue-light`. Title `.ol-card__title`, meta
  `.ol-card__meta`.
- Metric = `.ol-stat` inside `.ol-stat-grid` (auto-fit, min 200px).
  `.ol-stat__value` is tabular-nums; `.ol-stat__trend--up/--down` carry an
  arrow **and** a label, never colour alone.
- Chart panel = `.ol-chart` (§15). Empty state = `.ol-empty`.
- A card never gets a shadow, a gradient, a blur, or a radius other than 4px.
- Do not nest a card inside a card. Use a section + heading instead.

## 10. Buttons and actions

| Class | Use |
| --- | --- |
| `.ol-btn--primary` | The single main action of a view. Blue fill. |
| `.ol-btn--secondary` | Everything else with a visible boundary. |
| `.ol-btn--tertiary` | Inline text action ("Tozalash"). |
| `.ol-btn--danger` | Destructive, confirmed action only. |
| `.ol-btn--block` | Full width, mobile/drawer. |
| `.ol-icon-btn` | 44×44 icon-only control; **requires `aria-label`**. |

Rules:

1. One `--primary` per page region. Two primaries in one row is a bug.
2. Minimum interaction target **44px**. `.ol-btn`, `.ol-field`,
   `.ol-icon-btn`, `.ol-nav__link`, `.ol-tab`, `.ol-menu__link` already
   satisfy it. Existing exceptions in the CSS (`.ol-pager` 40px, `.ol-chip`
   and `.ol-facets__opt` 32px) are legacy — do not copy them into new
   components.
3. Actions are real `<button>` / `<a>`. Never `<div onclick>`.
4. Icons are Bootstrap Icons (`<i class="bi bi-…" aria-hidden="true">`).
   Emoji are not UI. At most one decorative emoji anywhere, and prefer none.

## 11. Tables and filters

- Always `.ol-table` inside `.ol-table-wrap` (the wrapper owns the border,
  radius and `overflow-x: auto`). A table must never make the page scroll
  horizontally.
- `th` — soft background, 14px, weight 500, left aligned, `white-space:
  nowrap`. `td` — 14px, subtle bottom border. Row hover =
  `--color-blue-soft`.
- Sortable header = a `<button>` inside `<th>` with `aria-sort`.
- Filters live in `.ol-filters` → `.ol-filters__row` → `.ol-field-group`,
  actions in `.ol-filters__actions`, result count in `.ol-filters__count`.
  Below 767px the panel collapses behind `.ol-filters__toggle`; keep that
  markup.
- Applied filters render as `.ol-chip` inside `.ol-chips`, each with a
  remove `<button>` carrying an `aria-label`.
- Pagination = `.ol-pager` with `aria-current="page"` on the active page.
- Faceted sidebar = `.ol-facets` (260px, sticky under the 64px header).
- Every list needs three states: populated, empty (`.ol-empty` with a way
  out), and error. Ship all three or the component is incomplete.

## 12. Forms and validation

- Field trio: `.ol-field-group` > `.ol-label` + `.ol-field`. Every control
  has a `<label for>`; placeholder is never the label.
- `.ol-field` is 44px min-height, 4px radius, blue border on focus.
- Errors: message text in `--color-error` under the field, plus
  `aria-invalid="true"` and `aria-describedby` pointing at it. Red border
  alone is not an error state.
- Required fields are marked in the label text, in Uzbek.
- Every POST form includes the CSRF token: `{{ csrf_token()|safe }}` in
  templates (it returns a ready `<input>` HTML string, hence `|safe`);
  in JS read `csrf_token_value()` or `<meta name="csrf-token">`.
- Success/error banners reuse `.ol-badge--success` / `.ol-badge--error` or
  the existing `.alert-inline` from `base.html`. Do not create a third.

## 13. Navigation

- Header and footer come from `components/site_header.html` /
  `site_footer.html`. Every link is produced from `utils/navigation.py`
  (`primary_nav`, `account_menu`, `admin_menu`, `footer_columns`,
  `premium_item`, `login_item`). **Never hardcode a nav href in a template.**
- Adding a page to navigation = adding an entry in `utils/navigation.py`,
  nothing else.
- Desktop dropdowns switch to the mobile drawer at ≤1199px. `.ol-drawer`
  toggles `body.ol-scroll-locked`.
- Active state: `nav_item_active` / `nav_section_active` set
  `--active` classes and `aria-current="page"`.
- Breadcrumbs come from the `page_header(crumbs=[…])` argument, listing
  ancestors only; the current page is added automatically and gets
  `aria-current="page"`.
- Keep the `.ol-skip-link` as the first focusable element.

## 14. Admin UI

Admin uses the **same** tokens and components as the public site. There is
no separate admin theme, no denser variant, no second palette.

- Admin pages extend `base.html`, use `page_header`, `.ol-table-wrap`,
  `.ol-filters`, `.ol-btn`.
- The admin landing grid is `components/admin_services_grid.html`
  (`.ol-admin-hub*`); its entries come from
  `utils/navigation.py:ADMIN_SERVICES`.
- Destructive admin actions use `.ol-btn--danger` and require an explicit
  confirmation step.
- Admin-only surfaces still meet the accessibility rules in §17.

## 15. Charts and data visualization

- Chart.js is loaded in `base.html` for every path **except `/`**. A chart on
  the home page would need its own decision — do not silently add the CDN
  script there.
- `static/js/olimlar-charts.js` runs after Chart.js and applies light-theme
  defaults. Use its API and nothing else:
  - `OlimlarCharts.palette(n)` — categorical series colours (10-colour
    sequence, contrast-ordered).
  - `OlimlarCharts.tokens` — `textPrimary`, `textSecondary`, `textMuted`,
    `border`, `borderSubtle`, `white`, `actionBlue`, `brandOrange`,
    `success`, `error`, `notice`.
  - `OlimlarCharts.axis(extra)` — grid `borderSubtle`, ticks `textMuted`.
- Chart.js draws to canvas and cannot read CSS `var()`. Literal values are
  allowed **only** through `OlimlarCharts.tokens` / `.palette()`. A raw hex
  inside a chart config is a defect.
- Wrap each chart in `.ol-chart` with `.ol-chart__title`, optional
  `.ol-chart__note`, and `.ol-chart__body` (min-height 240px). Loading,
  empty and error states use `.ol-chart__state` / `--error`.
- Every chart needs a text alternative: a caption, a summary sentence, or an
  accessible table with the same numbers. Never encode a category by colour
  alone in a legend-less chart.
- `maintainAspectRatio: false` + a sized `.ol-chart__body` — do not let a
  canvas set page height.

## 16. Responsive rules

Test these viewports for every UI change:

| Width | What must hold |
| --- | --- |
| 1600px+ | Container caps at 1600px and stays centred. |
| 1280px | 12-column layout, desktop nav visible. |
| 1199px | Desktop nav switches to burger + drawer. |
| 1023px | Gutter 16px, footer drops to 3 columns. |
| 767px | Filters collapse behind `.ol-filters__toggle`. |
| 649px | 6-column grid, reduced type scale, footer 2 columns, gutter 12px. |
| 390px | **No horizontal page scroll.** Wide content scrolls inside its own wrapper. |
| 479px | Premium CTA leaves the header and lives in the drawer only. |

Rules: use the existing breakpoints — do not introduce a new one without a
reason stated in the report. Tables, charts and code blocks scroll inside
`overflow-x: auto` containers, never the body. Touch targets stay ≥44px at
mobile widths.

## 17. Accessibility

1. One `<h1>`; heading levels never skip.
2. Visible focus everywhere: `outline: 2px solid var(--color-action-blue)`
   with `outline-offset: 2px`. A new interactive class must be added to the
   focus-ring selector list in the token CSS, or carry `.ol-focusable`.
   Never `outline: none`.
3. Full keyboard operation: Tab order follows DOM order, Escape closes
   drawer/menu/modal, focus returns to the trigger.
4. Icon-only controls need `aria-label`; decorative icons need
   `aria-hidden="true"`.
5. Landmarks: `<header>`, `<nav aria-label>`, `<main>`, `<footer>`; sections
   labelled with `aria-labelledby`.
6. Dropdowns use `aria-expanded` + `aria-controls`; tabs use `role="tablist"`
   + `aria-selected`; modals use `role="dialog"` + `aria-modal="true"`.
7. Contrast: body text on white uses `--color-text-primary` or
   `--color-text-secondary`. `--color-text-muted` is for meta only, never
   for primary reading text.
8. Respect `prefers-reduced-motion` — the token file already disables
   animation there; do not re-enable it.
9. State is never conveyed by colour alone (§5.2).

## 18. Prohibited patterns

Never, in any Olimlar.uz UI:

- a global dark theme, a `prefers-color-scheme: dark` block, or a
  `color-scheme` other than `light`;
- a day/night switch or any theme toggle;
- gradient heroes, glassmorphism, `backdrop-filter: blur`, neon colours;
- large or decorative shadows (only `--shadow-dropdown` on overlays);
- over-rounded cards (anything other than 4px card / 8px banner);
- decorative emoji as UI, or emoji instead of icons;
- state signalled by colour alone;
- overriding global `.card`, `.table`, `.btn`, or bare element selectors from
  a page-level stylesheet;
- `!important` without a written justification in a comment;
- raw HEX/RGB/HSL literals in templates, page CSS or chart config;
- arbitrary spacing/size values outside the token scale;
- a second component that does what an existing `.ol-*` component already
  does;
- a hardcoded navigation link instead of `utils/navigation.py`;
- an external font, CSS or icon CDN beyond what `base.html` already loads;
- repurposing `--color-ai-background` for non-AI surfaces.

Legacy note: `base.html` carries an `--oak-*` alias bridge and legacy classes
(`.content-card`, `.page-heading`, `.table-panel`, `.btn-action`,
`.alert-inline`) so ~50 older templates keep working. **Read them, do not
extend them.** New code uses `--color-*` and `.ol-*` only.

## 19. Implementation workflow

1. Read `static/css/olimlar-design-system.css`. Confirm which tokens and
   `.ol-*` classes already cover the need.
2. Find the closest existing page (§3) and copy its structure.
3. Only if nothing fits, design a new component — and justify it in the
   report against §20 of `docs/olimlar-design-system.md`.
4. Write the template: `ol-page` → container → `page_header` → `ol-section`s.
5. Page-local CSS in `{% block extra_head %}`, namespaced, tokens only.
6. Wire JS in `{% block scripts %}`. Static assets referenced with a `?v=`
   cache-buster — nginx caches `/static/` for 7 days.
7. Implement every state: populated, loading, empty, error, disabled.
8. Run the checks in §20.
9. Report per §21, including any conflict with the task prompt.

## 20. Visual QA checklist

Before saying the work is done, verify each line:

- [ ] No raw HEX/RGB in the diff (`git diff -U0 | grep -nE '#[0-9a-fA-F]{3,8}|rgba?\('`) except inside `OlimlarCharts` usage.
- [ ] No new `!important`, no `outline: none`, no `prefers-color-scheme: dark`.
- [ ] Exactly one `<h1>`, produced by `page_header`.
- [ ] H1/H2 render serif; H3+ and UI render sans.
- [ ] All spacing/radius/font sizes come from tokens.
- [ ] Reused `.ol-*` components; no near-duplicate component added.
- [ ] Nav links come from `utils/navigation.py`.
- [ ] Checked at 1280px, 1023px, 767px, 649px and 390px; no horizontal page scroll at 390px.
- [ ] Every interactive element ≥44px on mobile and keyboard reachable.
- [ ] Focus ring visible on every new interactive element.
- [ ] Icon-only controls have `aria-label`; decorative icons `aria-hidden`.
- [ ] Empty, loading and error states exist and are styled.
- [ ] Charts use `OlimlarCharts.palette()`/`.tokens` and have a text alternative.
- [ ] Uzbek copy reads naturally, including labels and aria text.
- [ ] Static asset URLs carry a `?v=` cache-buster.
- [ ] If a browser was not actually opened, say so — do not claim visual verification.

## 21. Final report checklist

End every UI task with:

1. Files created / changed.
2. Components and tokens reused (name them).
3. Any new component added, and why nothing existing fit.
4. Viewports actually checked, and how (browser or reasoning).
5. Accessibility items verified (focus, keyboard, labels, states).
6. Any conflict between the task prompt and this design system, stated
   plainly — never silently resolved.
7. Any deviation found in existing code but deliberately **not** fixed.
8. Remaining risks or follow-ups.

---

## Invoking this skill in later prompts

Paste this at the top of any UI task:

```text
Avval `.claude/skills/olimlar-design-system/SKILL.md` faylini o'qi
va ushbu vazifadagi barcha yangi yoki o'zgartiriladigan UI qismlarida
unga qat'iy amal qil.
```

Human-facing rationale, good/bad examples and the "new component or reuse?"
decision guide live in [docs/olimlar-design-system.md](../../../docs/olimlar-design-system.md).
