# Olimlar.uz dizayn tizimi

Bu hujjat — dasturchi va dizaynerlar uchun. U **nima uchun** shunday
qilinganini tushuntiradi.

Claude Code uchun qisqa, majburiy qoidalar alohida faylda:
[`.claude/skills/olimlar-design-system/SKILL.md`](../.claude/skills/olimlar-design-system/SKILL.md).

Kodda haqiqiy manba esa doim bitta:
[`static/css/olimlar-design-system.css`](../static/css/olimlar-design-system.css).

---

## 1. Brend tamoyili

Olimlar.uz — akademik ma'lumot platformasi. Foydalanuvchi bu yerda
"chiroyli sayt" ko'rgani emas, **ma'lumot topgani** keladi: dissertatsiya,
olim profili, grant, jurnal, maqola, reyting.

Shundan uchta qaror kelib chiqadi:

1. **Light-only.** Kun/tun almashtirgichi yo'q va qo'shilmaydi. Ilmiy
   matn, jadval va grafik oq fonda eng yaxshi o'qiladi; ikkita tema esa
   ikki barobar QA va ikki barobar regressiya degani.
2. **Funksional ilhom — ScienceDirect.** Zich, skanerlanadigan, filtrga
   asoslangan tadqiqot interfeysi. Bu **nusxa emas**: ranglar, tipografiya
   va komponentlar bizniki. ScienceDirect'dan olingani — *tamoyil*
   (ma'lumot birinchi, bezak keyin), *ko'rinish* emas.
3. **Ikki rang, ikki vazifa.** Orange — brend. Blue — harakat. Bu ikkisini
   almashtirmaslik butun saytni bir tizimda ushlab turadi.

### Nima uchun "yangi dizayn tizimi yaratmaymiz"

Loyihada 50+ shablon bor. Har bir sahifa o'z rang va o'z kartasini yozsa,
sayt bir necha oyda parchalanadi — bu allaqachon bir marta yuz bergan
(pastdagi "Ma'lum farqlar" bo'limiga qarang). Shuning uchun qoida qat'iy:
**mavjud komponentni qayta ishlat, parallel tizim yaratma.**

---

## 2. Ranglardan foydalanish

Barcha ranglar `:root` da token sifatida e'lon qilingan. Shablonda yoki
sahifa CSS'ida **xom HEX yozilmaydi** — faqat `var(--color-…)`.

### Asosiy ranglar

| Token | Qiymat | Qayerda |
| --- | --- | --- |
| `--color-brand-orange` | `#e8631a` | Faqat brend detali: logotipdagi nuqta, brend badge. Tugma foni emas. |
| `--color-action-blue` | `#0056d6` | Asosiy tugma, link, active tab, focus ring, checkbox. |
| `--color-action-blue-light` | `#6593dc` | Karta/chip hover chegarasi. |
| `--color-action-blue-dark` | `#2f6feb` | Ko'k tugmaning bosilgan/hover holati. |
| `--color-text-primary` | `#0c0f12` | Sarlavha, asosiy matn, jadval kataklari. |
| `--color-text-secondary` | `#57626f` | Label, nav link, ikkilamchi matn. |
| `--color-text-muted` | `#57626f` | Meta, izoh, sanoq, placeholder. |
| `--color-white` / `--color-surface` | `#ffffff` | Karta yuzasi. |
| `--color-background` | `#f4f7fb` | Sahifa foni. |
| `--color-background-soft` | `#f4f7fb` | Jadval sarlavhasi, footer, bo'sh holat. |
| `--color-border` | `#dce3ec` | Karta, maydon, jadvalning tashqi chegarasi. |
| `--color-border-subtle` | `#dce3ec` | Ichki qator ajratgichi, grafik grid. |
| `--color-orange-soft` | `#fdeee3` | Brend badge foni. |
| `--color-blue-soft` | `#ebf1f9` | Qator hover, chip, avatar, nav hover. |

### Holat ranglari

| Token | Qiymat | Juftligi |
| --- | --- | --- |
| `--color-success` | `#0c8930` | `--color-success-soft` `#f4faf6` |
| `--color-error` | `#af1d1d` | `--color-error-soft` `#fdf4f4` |
| `--color-notice` | `#c98a16` | `--color-notice-soft` `#fff9e6` |

To'yingan rang — matn va ikonka uchun; soft rang — fon uchun. Juftlikni
aralashtirmang.

### AI yuzasi

`--color-ai-background: #1f2a44` — AI va premium bloklari, launcher va panel sarlavhasi uchun. Global dark tema emas.

### Rang bilan bog'liq uchta qoida

1. **Rangning o'zi holatni bildirmaydi.** "Qizil qator = xato" yetarli emas
   — matn, ikonka yoki yozuv ham kerak. Bu rang ko'rmaydigan foydalanuvchi
   uchun ham, chop etilgan sahifa uchun ham majburiy.
2. **Bir ekranda ko'pi bilan bitta orange urg'u.** Ikkinchi urg'u kerak
   bo'lsa — u ko'k.
3. **Matn kontrasti.** Asosiy o'qiladigan matn `--color-text-primary` yoki
   `--color-text-secondary`. `--color-text-muted` faqat meta uchun.

---

## 3. Tipografiya ierarxiyasi

| Rol | Shrift | Token |
| --- | --- | --- |
| H1–H3, ilmiy nom va sarlavhalar | Source Serif 4 600 | `--font-serif` |
| H4–H6, body, nav, jadval, tugma | Inter 400/500/600 | `--font-sans` |
| `--font-display` | serif | `var(--font-serif)` |

Source Serif 4 va Inter `static/fonts/` dan yuklanadi. Source Serif 4 litsenziyasi shu papkada saqlanadi. Ish vaqtida Google Fonts so‘rovi kerak emas.

Desktop shkala: H1 48px, H2 34px, H3 24px, H4 22px, body 16px, kichik matn 14px, meta 12px. Mobil qiymatlar token manbasida belgilanadi. Line-height: sarlavha 1.25, matn 1.5.

### Sarlavha qoidalari

- **Har sahifada bitta `<h1>`** — uni `page_header` makrosi chiqaradi.
- Ierarxiya sakramaydi: h1 → h2 → h3. O'lcham kerak bo'lsa, teg emas,
  **klass** o'zgaradi (`.ol-h3`, `.ol-h4`, `.ol-h5`).
- Barcha matn o'zbekcha, shu jumladan `aria-label` va placeholder.

---

## 4. Spacing

Asosiy ritm — **8px**. Ixcham ikonka, badge va forma ichida 4px qadamdan foydalanish mumkin.

```
--space-4  --space-8   --space-12  --space-16  --space-20  --space-24
--space-32 --space-40  --space-48  --space-64  --space-80  --space-96
--space-112 --space-128 --space-144 --space-160
```

Amaliy tanlov:

- 4–8px — ikonka va matn orasi, badge ichi;
- 12–16px — element ichidagi padding, forma maydonlari orasi;
- 24px — karta padding;
- 32px — sahifa sarlavhasidan keyin;
- 64px — bo'limlar orasi (`.ol-section + .ol-section`);
- 96px — sahifa oxiridagi bo'shliq.

> `--space-6` (6px) ham mavjud — bu eski qoldiq, `.ol-chip` va `.ol-nav`
> gap'larida ishlatiladi. Yangi kodda 6px qo'shmang, lekin tokenni ham
> o'chirmang.

**Radius:** karta 8px (`--radius-card`); maydon, tugma, badge 4px (`--radius-input` / `--radius-default`). Doira faqat avatar va nuqta uchun.

**Soya:** dropdown va bosiladigan karta uchun minimal. Oddiy panel 1px chegara bilan ajraladi.

## 5. Grid

- Konteyner 1280px; desktop yon bo‘shliq 32px, mobil 16px.
- 12 ustun desktop/planshet, 6 ustun kichik ekranlarda.
- Mobil jadvallar mos kelganida sarlavhali qator kartalariga aylanadi; issiqlik xaritasi jadval holida qoladi.

Sahifa karkasi:

```
<main class="ol-container ol-container--wide">   ← base.html beradi
  .ol-page                 ← yuqori/pastki bo'shliq (48px / 96px)
    page_header()          ← breadcrumb + H1 + izoh + meta
    .ol-section            ← mantiqiy blok (orasi 64px)
      .ol-stack            ← ichidagi mayda elementlar (16px)
```

> **Diqqat:** `base.html` dagi `<main>` allaqachon
> `.ol-container.ol-container--wide` klasslarini oladi. `{% block content %}`
> ichida yana bir konteyner o'ramang — yon padding ikki barobar bo'lib
> qoladi.

---

## 6. Komponentlar

### Umumiy Jinja komponentlari

| Komponent | Fayl | Vazifasi |
| --- | --- | --- |
| Sahifa sarlavhasi | `templates/components/page_header.html` | `page_header`, `page_header_with_actions`, `breadcrumb` |
| Header | `templates/components/site_header.html` | Dropdown nav, mobil drawer, qidiruv overlay |
| Footer | `templates/components/site_footer.html` | Ustunlar `utils/navigation.py:FOOTER_COLUMNS` dan |
| Admin xizmatlari | `templates/components/admin_services_grid.html` | `ADMIN_SERVICES` grid |
| SEO meta | `templates/partials/seo_meta.html` | `{% block seo_extra %}` ichida |
| Login gate | `templates/components/login_required_modal.html` | Freemium chegarasi |
| Fan daraxti / maqola filtrlari | `components/field_tree.html`, `article_filters.html`, `article_bits.html` | Maqolalar moduli |

### CSS komponentlari

Tokendan tashqari, `olimlar-design-system.css` tayyor komponentlarni ham
beradi. Ularni **klass sifatida ishlating**, nusxa ko'chirmang:

- Karkas: `ol-page`, `ol-section`, `ol-stack`, `ol-container`, `ol-grid`
- Matn: `ol-h1`…`ol-h5`, `ol-body`, `ol-small`, `ol-meta`, `ol-muted`
- Tugma: `ol-btn` + `--primary/--secondary/--tertiary/--danger/--block`,
  `ol-icon-btn`
- Sirt: `ol-card`, `ol-stat` (+`ol-stat-grid`), `ol-chart`, `ol-empty`
- Ma'lumot: `ol-table` (+`ol-table-wrap`), `ol-pager`, `ol-tabs`/`ol-tab`
- Filtr: `ol-filters`, `ol-facets`, `ol-chips`/`ol-chip`
- Forma: `ol-field-group`, `ol-label`, `ol-field`
- Status: `ol-badge` + `--neutral/--info/--brand/--success/--error/--notice`
- Navigatsiya: `ol-header`, `ol-nav`, `ol-menu`, `ol-drawer`, `ol-acc`,
  `ol-breadcrumb`, `ol-footer`, `ol-skip-link`

### Namunaviy sahifa

Eng toza namuna — [`templates/maqolalar_yonalishlar.html`](../templates/maqolalar_yonalishlar.html).
Yangi sahifa yozayotganda avval shuni oching.

```jinja
{% extends "base.html" %}
{% from "components/page_header.html" import page_header %}

{% block title %}Fan yo'nalishlari | Olimlar.uz{% endblock %}

{% block content %}
<div class="ol-page">
  {{ page_header("Fan yo'nalishlari",
                 "Bir jumlalik izoh.",
                 crumbs=[('Bosh sahifa', '/')],
                 meta=['128 ta yo\'nalish']) }}

  <section class="ol-section" aria-labelledby="sec-royxat">
    <h2 id="sec-royxat" class="ol-h4">Katalog</h2>
    …
  </section>
</div>
{% endblock %}
```

### Sahifa CSS'i

Sahifaga xos stil `{% block extra_head %}` ichida yoziladi va **qisqa
prefiks** bilan nomlanadi (`.mq-`, `.up-`, `.jr-`). Global klass yoki yalang'och
teg (`table`, `.card`, `.btn`) sahifadan qayta yozilmaydi.

---

## 7. Ochiq (public) va admin sahifalar

Admin **alohida tema emas**. Bir xil token, bir xil komponent, bir xil
tipografiya. Farq faqat mazmunda.

| | Public | Admin |
| --- | --- | --- |
| Karkas | `base.html` | `base.html` |
| Sarlavha | `page_header` | `page_header` |
| Jadval | `.ol-table-wrap` + `.ol-table` | bir xil |
| Filtr | `.ol-filters` | bir xil |
| Kirish nuqtasi | header nav | `admin_services_grid.html` |
| Xavfli amal | kam uchraydi | `.ol-btn--danger` + tasdiqlash qadami |

Admin sahifasi ham accessibility qoidalariga to'liq bo'ysunadi — u "faqat
biz ishlatamiz" degani istisno degani emas.

---

## 8. Forma va jadval

### Forma

```jinja
<div class="ol-field-group">
  <label class="ol-label" for="q">Yo'nalish nomi yoki shifri</label>
  <input class="ol-field" type="search" id="q" name="q"
         placeholder="Masalan: pedagogika yoki 13.00.01">
</div>
```

- Har bir maydonning `<label for>` i bor. Placeholder **label o'rnini
  bosmaydi** — u ko'rinmay qoladi va skrinrider uchun yo'q hisob.
- `.ol-field` — 44px minimal balandlik, fokusda ko'k chegara.
- Xato: maydon ostida `--color-error` rangli matn + `aria-invalid="true"` +
  `aria-describedby`. Faqat qizil chegara — xato holati emas.
- POST formada CSRF token majburiy. Shablonda `{{ csrf_token()|safe }}`
  (u tayyor `<input>` HTML qaytaradi, shuning uchun `|safe` shart), JS'da
  esa `csrf_token_value()` yoki `<meta name="csrf-token">`.

### Jadval

```jinja
<div class="ol-table-wrap">
  <table class="ol-table">
    <thead><tr><th>Mavzu</th><th>Yil</th></tr></thead>
    <tbody>…</tbody>
  </table>
</div>
```

- O'ram (`.ol-table-wrap`) chegara, radius va `overflow-x: auto` ni oladi.
  Jadval hech qachon **sahifani** gorizontal skroll qilmaydi.
- Saralanadigan sarlavha — `<th>` ichidagi `<button>` + `aria-sort`.
- Sahifalash — `.ol-pager`, faol sahifada `aria-current="page"`.
- Filtr paneli 767px dan pastda `.ol-filters__toggle` ortiga yig'iladi;
  tanlangan filtrlar `.ol-chip` sifatida ko'rinadi.
- Har bir ro'yxatda **uchta holat**: to'ldirilgan, bo'sh (`.ol-empty`,
  chiqish yo'li bilan), xato. Faqat "hammasi joyida" holatini yozish —
  tugallanmagan ish.

---

## 9. Grafiklar

Chart.js `base.html` da barcha sahifalar uchun yuklanadi — **`/` (bosh
sahifa) bundan mustasno** (LCP uchun). Undan keyin
[`static/js/olimlar-charts.js`](../static/js/olimlar-charts.js) global
light-theme sozlamalarini qo'yadi.

```js
var C = window.OlimlarCharts;
var colors = C.palette(5);       // kategoriya ranglari
var K = C.tokens;                // K.actionBlue, K.textMuted, …
var xAxis = C.axis({ grid: { display: false } });
```

Nima uchun bu yerda xom rang qiymatlari bor: Chart.js `<canvas>` ga
chizadi va CSS `var()` ni **o'qiy olmaydi**. Shuning uchun token qiymatlari
`olimlar-charts.js` da takrorlangan. Token o'zgarsa — shu fayl ham
yangilanishi shart. Sahifa ichida xom hex yozish esa baribir xato: doim
`C.tokens` / `C.palette()` orqali oling.

Grafik o'ramasi:

```html
<div class="ol-chart">
  <div class="ol-chart__head">
    <h3 class="ol-chart__title">Yillar kesimida himoyalar</h3>
    <p class="ol-chart__note">Manba: OAK, 2015–2025</p>
  </div>
  <div class="ol-chart__body"><canvas id="c-yearly"></canvas></div>
</div>
```

- `maintainAspectRatio: false` + o'lchamli `.ol-chart__body`.
- Yuklanish / bo'sh / xato — `.ol-chart__state` (`--error`).
- Har bir grafikning **matnli muqobili** bo'lsin: izoh, xulosa jumla yoki
  bir xil raqamlarni beruvchi jadval.

---

## 10. Responsive

| Kenglik | Nima o'zgaradi |
| --- | --- |
| 1600px+ | Konteyner 1600px da to'xtaydi, markazda qoladi |
| 1199px | Desktop nav → burger + drawer |
| 1023px | Gutter 16px, footer 3 ustun |
| 767px | Filtrlar yig'iladi, Premium yorlig'i ikonkaga qisqaradi |
| 649px | 6 ustunli grid, kichraygan tipografiya, footer 2 ustun, gutter 12px |
| 479px | Premium CTA header'dan chiqib, drawer ichida qoladi |

Har bir UI o'zgarishi **1280 / 1023 / 767 / 649 / 390px** da tekshiriladi.
390px eng muhim nazorat nuqtasi: u yerda **sahifa gorizontal skroll
qilmasligi** kerak. Keng kontent o'z o'ramida (`overflow-x: auto`)
skroll qiladi.

Yangi breakpoint qo'shmang — mavjudlari yetadi. Zarur bo'lsa, sababi
hisobotda yozilsin.

---

## 11. Accessibility

Bular "keyin qo'shamiz" ro'yxati emas, qabul mezoni:

1. Bitta `<h1>`, sarlavha darajalari sakramaydi.
2. Fokus doim ko'rinadi: `outline: 2px solid var(--color-action-blue)`,
   `outline-offset: 2px`. `outline: none` — taqiqlangan. Yangi interaktiv
   klass fokus ro'yxatiga qo'shiladi yoki `.ol-focusable` oladi.
3. Klaviatura bilan to'liq boshqarish: Tab tartibi DOM tartibida, Escape
   drawer/menyu/modalni yopadi, fokus chaqirgan tugmaga qaytadi.
4. Faqat ikonkali tugmada `aria-label`; bezak ikonkada `aria-hidden="true"`.
5. Landmark'lar: `<header>`, `<nav aria-label>`, `<main>`, `<footer>`;
   bo'limlar `aria-labelledby` bilan.
6. Dropdown — `aria-expanded` + `aria-controls`; tablar — `role="tablist"` +
   `aria-selected`; modal — `role="dialog"` + `aria-modal="true"`.
7. Minimal tegish maydoni **44px**.
8. `prefers-reduced-motion` hurmat qilinadi (token faylida allaqachon
   yozilgan — uni qaytarib yoqmang).
9. Holat rang bilan emas, matn/ikonka bilan ham bildiriladi.

---

## 12. Yaxshi va yomon misollar

### Rang

```css
/* ✅ */
.mq-card { border: 1px solid var(--color-border); background: var(--color-white); }
.mq-card--active { border-color: var(--color-action-blue); }

/* ❌ xom hex + eski dark palitra */
.mq-card { border: 1px solid #334155; background: #162032; }
```

### Sarlavha

```jinja
{# ✅ #}
{{ page_header("Grantlar", "Xalqaro va mahalliy imkoniyatlar") }}

{# ❌ ikkinchi h1 va qo'lda yozilgan sarlavha #}
<h1 style="font-size:44px">Grantlar</h1>
```

### Holat

```html
<!-- ✅ rang + yozuv -->
<span class="ol-badge ol-badge--error">Muddati o'tgan</span>

<!-- ❌ faqat rang -->
<span style="color:#af1d1d">●</span>
```

### Tugma

```html
<!-- ✅ -->
<button type="button" class="ol-btn ol-btn--secondary">Tozalash</button>
<button type="button" class="ol-icon-btn" aria-label="Menyuni ochish">
  <i class="bi bi-list" aria-hidden="true"></i>
</button>

<!-- ❌ div, aria yo'q, 30px balandlik -->
<div class="my-btn" onclick="reset()">Tozalash</div>
```

### Spacing

```css
/* ✅ */ .up-row { gap: var(--space-12); margin-bottom: var(--space-24); }
/* ❌ */ .up-row { gap: 13px; margin-bottom: 22px; }
```

### Grafik

```js
/* ✅ */ backgroundColor: C.palette(3)
/* ❌ */ backgroundColor: ['#3b82f6', '#f87171', '#34d399']
```

### Taqiqlangan uslublar

```css
/* ❌ hammasi taqiqlangan */
@media (prefers-color-scheme: dark) { … }
.hero { background: linear-gradient(135deg, #6366f1, #ec4899); }
.panel { backdrop-filter: blur(12px); }
.card { border-radius: 20px; box-shadow: 0 20px 60px rgba(0,0,0,.35); }
.table td { padding: 6px !important; }
```

---

## 13. Yangi komponent qachon yaratiladi?

**Avval qayta ishlatishga urinib ko'ring.** Yangi komponent faqat quyidagi
uchta savolning **hammasiga** "ha" bo'lsa yaratiladi:

1. Mavjud `.ol-*` komponentlarining hech biri bu vazifani bajarmaydi
   (nafaqat "ko'rinishi boshqacha", balki **vazifasi** boshqacha)?
2. Bu naqsh kamida ikkita joyda kerak bo'ladi?
3. Uni mavjud komponentning modifikatori sifatida yozib bo'lmaydi
   (`.ol-card--compact` kabi)?

Agar javob "yo'q" bo'lsa:

- ko'rinishi biroz farq qilsa → **modifikator** qo'shing;
- bir sahifaga xos bo'lsa → sahifa prefiksi bilan lokal klass yozing
  (`.up-tabs`, `templates/university_profile.html` dagidek — u `.ol-tabs`
  ustiga qo'shimcha beradi, uni almashtirmaydi);
- xuddi shu vazifani bajaruvchi ikkinchi komponent → **taqiqlangan**.

Yangi umumiy komponent qo'shilsa: u `olimlar-design-system.css` ga
`.ol-` prefiksi bilan tushadi, tokenlardan foydalanadi, fokus ro'yxatiga
qo'shiladi va shu hujjatga bir qator bilan yoziladi.

## 14. Mavjud komponent qachon qayta ishlatiladi?

Deyarli doim. Amaliy moslik jadvali:

| Kerak bo'lsa | Ishlating |
| --- | --- |
| Sahifa sarlavhasi + breadcrumb | `page_header` makrosi |
| Ro'yxat filtri | `.ol-filters` |
| Yon panel filtr | `.ol-facets` |
| Raqamli ko'rsatkich | `.ol-stat` + `.ol-stat-grid` |
| Element kartasi | `.ol-card` |
| Status yorlig'i | `.ol-badge--*` |
| Jadval | `.ol-table-wrap` + `.ol-table` |
| Sahifalash | `.ol-pager` |
| Bo'limlar almashinuvi | `.ol-tabs` |
| Grafik | `.ol-chart` |
| Bo'sh holat | `.ol-empty` |
| Navigatsiya havolasi | `utils/navigation.py` (qo'lda link yozilmaydi) |

---

## 15. Visual QA jarayoni

Ishni "tayyor" deyishdan oldin:

1. **Diff auditi.** `git diff` da xom rang bormi?
   `git diff -U0 | grep -nE '#[0-9a-fA-F]{3,8}|rgba?\('`
   Yagona istisno — `OlimlarCharts` orqali olingan qiymatlar.
2. **Token auditi.** Barcha spacing/radius/font o'lchamlari tokendanmi?
3. **Struktura.** Bitta `<h1>`, `page_header` ishlatilganmi? H1/H2 serif,
   qolgani sans ko'rinyaptimi?
4. **Viewport.** 1280 / 1023 / 767 / 649 / 390px. 390px da gorizontal
   skroll yo'qmi?
5. **Klaviatura.** Tab bilan har bir interaktiv elementga yetish mumkinmi?
   Fokus halqasi ko'rinadimi? Escape modalni yopadimi?
6. **Holatlar.** To'ldirilgan, yuklanmoqda, bo'sh, xato, o'chirilgan —
   hammasi bormi?
7. **Konsol.** Brauzer konsolida xato yo'qmi?
8. **Kesh.** Yangi/o'zgargan JS/CSS `?v=` bilan ulanganmi? (nginx `/static/`
   ni 7 kun keshlaydi — busiz foydalanuvchi eski faylni ko'radi.)
9. **Til.** O'zbekcha matn tabiiy o'qiladimi? `aria-label` lar ham
   o'zbekchami?

Brauzer ochilmagan bo'lsa — hisobotda **shuni ochiq yozing**. "Vizual
tekshirildi" deb yozish faqat haqiqatan ochilgan bo'lsa mumkin.

---

## 16. Ma'lum farqlar (kod vs hujjat)

Bular hozirgi holat. Bu ro'yxat **vazifa emas** — ular alohida migratsiya
ishlarida hal qilinadi. Boshqa vazifa ustida ishlayotganda ularni "yo'l-yo'lakay
tuzatish" mumkin emas.

1. **Eski dark-era palitra hali 50+ shablonda.** `#94a3b8`, `#3b82f6`,
   `#0f172a`, `#162032`, `#e2e8f0` kabi qiymatlar qoldi; eng ko'p
   `cabinet.html` (~82), `olim_profile.html` (~68), `home_legacy.html` (~68).
   `base.html` dagi `--oak-*` ko'prigi ularni light tokenlarga bog'lab
   turadi. Yangi kod bu qiymatlarni takrorlamaydi.
2. **`journals.html`** hali `rgba(239,68,68,…)` va `#fca5a5` (dark tema
   uchun mo'ljallangan och qizil matn) ishlatadi — oq fonda kontrasti past.
3. **`trends.html`** ba'zi joylarda `'#57626f'`, `'#57626f'`,
   `rgba(59,130,246,0.7)` ni to'g'ridan-to'g'ri yozadi, `OlimlarCharts.tokens`
   o'rniga.
4. **44px qoidasidan istisnolar** token faylining o'zida bor: `.ol-pager`
   tugmalari 40px, `.ol-chip` va `.ol-facets__opt` 32px. Ular eski;
   yangi komponentga ko'chirmang.
5. **`--space-6` (6px)** 4px shkalasiga tushmaydi (4-bo'limga qarang).
6. **`--color-ai-background`** e'lon qilingan, lekin iste'molchisi yo'q.
7. **Token nomlari** ba'zi hujjatlardagi tavsifiy nomlardan farq qiladi:
   "pale orange" → `--color-orange-soft`, "pale blue" → `--color-blue-soft`.
   Qiymatlar bir xil; **kodda mavjud nom** ishlatiladi.
8. **`--font-display`** nomiga qaramay sans-serif (Manrope). Serif faqat
   `--font-serif`.
9. **Bootstrap 5.3 + Bootstrap Icons** hali CDN'dan yuklanadi; dizayn
   tizimi ular ustiga qo'yiladi va ularni global ravishda qayta yozmaydi.

---

## 17. Claude Code bilan ishlash

Har qanday UI vazifasining boshiga shuni qo'ying:

```text
Avval `.claude/skills/olimlar-design-system/SKILL.md` faylini o'qi
va ushbu vazifadagi barcha yangi yoki o'zgartiriladigan UI qismlarida
unga qat'iy amal qil.
```

Manbalar ustuvorligi (ziddiyat bo'lsa yuqoridagisi g'alaba qiladi):

```text
1. static/css/olimlar-design-system.css
2. mavjud shared Jinja komponentlari
3. .claude/skills/olimlar-design-system/SKILL.md
4. docs/olimlar-design-system.md
5. alohida task prompti
```

Agar task prompti dizayn tizimiga zid bo'lsa, Claude ziddiyatni yashirmaydi
— dizayn tizimiga mos variantni bajaradi va farqni hisobotda yozadi.

## 2026-09 mahsulot yangilanishi

Qidiruv markazli bosh sahifa haqiqiy mavjud endpointlardan foydalanadi: dissertatsiyalar, olimlar, universitetlar, grantlar. Umumiy qidiruv API yo‘qligi sababli qo‘llanmaydigan “Barchasi” rejimi chiqarilmaydi. Statistika faqat server ma’lumotidan olinadi; test namunalari faqat `artifacts/redesign` ichida.

`olimlar-product.css` sahifa kompozitsiyalari uchun, tokenlar esa yagona `olimlar-design-system.css` faylida. `OlimlarCharts.tokens` va `palette()` canvas/SVG uchun CSS tokenlarining hisoblangan qiymatlarini beradi.

Tekshiruv qamrovi va cheklovlar: [UI tekshiruv hisoboti](ui-redesign-verification.md).
