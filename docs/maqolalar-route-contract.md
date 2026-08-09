# Maqolalar katalogi — route kontrakti

Ushbu hujjat ilmiy maqolalar bo'limining URL tuzilmasini qayd etadi.
Navigatsiya (`utils/navigation.py`) faqat katalog darajasidagi route'larga
havola qiladi; qolganlari sahifa ichidagi navigatsiya orqali ochiladi.

Modul tafsiloti (modellar, PDF qoidasi, o'quvchi arxitekturasi, xavfsizlik va
cheklovlar): **`docs/articles-module.md`**. Bu yerda faqat URL kontrakti.

## Route'lar

| Route | Holat | Vazifasi |
|---|---|---|
| `/maqolalar` | ✅ Phase 2B.1 | Katalog: qidiruv, filtr, sort, karta/jadval, sahifalash |
| `/maqolalar/yonalishlar` | ✅ Phase 2B.1 | Fan yo'nalishlarining ierarxik katalogi |
| `/maqolalar/yonalish/[slug]` | ✅ Phase 2B.1 | Bitta yo'nalish ichidagi maqolalar ro'yxati |
| `/maqolalar/[slug]` | ✅ Phase 2B.1 | Maqola tafsiloti: metadata, annotatsiya, sitata |
| `/maqolalar/[slug]/oqish` | ✅ Phase 2B.2 | **Ichki PDF.js o'quvchisi** (HTML karkas) |
| `/maqolalar/[slug]/pdf` | ✅ Phase 2B.2 | O'quvchi uchun himoyalangan PDF oqimi (inline, Range) |
| `/maqolalar/[slug]/fayl` | ✅ Phase 2B.1 | PDF yuklab olish (attachment) — UI'da tugma yo'q |

`/oqish` va `/pdf` — ataylab **ikki** route: birinchisi HTML karkas, ikkinchisi
baytlar. Ikkalasi ham `blueprints/articles.py:_public_pdf_or_404()` orqali
o'tadi, u esa `can_read_pdf()` ni chaqiradi — qoida hech qayerda nusxalanmaydi.
Ruxsat yo'q bo'lsa ikkalasi ham `404` beradi (fayl mavjudligi fakti ham oshkor
qilinmaydi).

`/fayl` yuklab olish uchun qoladi, lekin **hech qanday tugma unga ishora
qilmaydi**: `can_read_pdf()` — ko'rsatish huquqi, yuklab olish huquqi emas.
Kontrakt: `docs/articles-module.md` §13.9.

URL'lar Phase 2B.1 dan beri o'zgarmadi — Phase 2B.2 da faqat
`templates/maqolalar_oqish.html` shabloni almashdi va `/pdf` qo'shildi.

## PDF viewer — vizual kontrakt (light-only)

Olimlar.uz'da kun/tun almashtirgichi yo'q va qo'shilmaydi. O'quvchi shu
qoidalarga amal qiladi:

- viewer sahifasi umumiy sayt bilan bir xil **light** temada bo'ladi;
- PDF canvas oq yoki hujjatning o'z foni bilan chiziladi — canvasga filtr,
  invert yoki "dark mode" qo'llanmaydi;
- toolbar **oq** (`--color-white`) — hujjat maydoni esa soft-grey
  (`--color-background-soft`) ish stoli. To'q toolbar varianti ko'rib
  chiqildi va tanlanmadi: light birinchi tanlov, va yangi semantik token
  ham kerak bo'lmadi;
- foydalanuvchi uchun theme tanlovi, `data-bs-theme` almashtirish yoki
  `localStorage` da rejim saqlash bo'lmaydi;
- toolbar ikonkalari mavjud Bootstrap Icons to'plamidan olinadi, emoji emas.

Bu qoidalar `tests/test_light_only_theme.py` bilan himoyalangan: manba daraxtida
theme toggle, `data-bs-theme="dark"` yoki `prefers-color-scheme: dark` paydo
bo'lsa testlar yiqiladi.

## Bajarilgan ishlar (Phase 2B.1)

Maqolalar backendi, ma'lumotlar modeli, PDF yuklash tizimi, fan yo'nalishlari
ierarxiyasi va seed mexanizmi tayyor. To'liq tafsilot: **`docs/articles-module.md`**
(modellar, statuslar, PDF ommaviyligi qoidasi, litsenziya talablari, admin
workflow, seed formati, ma'lum cheklovlar).

## Hali qilinmagan

- PDF ichida moslikni **ajratib ko'rsatish** (upstream `PDFFindController`);
  hozir qidiruv moslik topilgan sahifaga o'tadi
  (`docs/articles-module.md` §13.7);
- `can_download_pdf()` va yuklab olish tugmasi (§13.9 dagi kontrakt);
- AI paneli (§13.12 dagi kontrakt: `article_id`, `current_page`,
  `selected_text`, `document_page_count`);
- CJK hujjatlar uchun `cmaps/` vendoring;
- Phase 2C.1 — DOI orqali metadata autofill.

## Mavjud, aralashtirmaslik kerak bo'lgan bo'lim

`/maqolalar-tahlili` — bu boshqa modul (OpenAlex nashr tahlili,
`blueprints/maqolalar.py`). U maqolalar katalogi emas, analitika sahifasi va
navigatsiyada "Tadqiqot" bo'limida turadi.

## Navigatsiya

`utils/navigation.py` dagi "Ilmiy resurslar" bo'limi Phase 2B.1 da
o'zgarishsiz qoldi — yangi route'lar sahifa ichidagi navigatsiya (breadcrumb,
yo'nalishlar daraxti, maqola kartalari) orqali ochiladi, header menyusi
kengaymaydi.
