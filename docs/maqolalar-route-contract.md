# Maqolalar katalogi — route kontrakti

Ushbu hujjat ilmiy maqolalar bo'limining kelajakdagi URL tuzilmasini qayd etadi.
Navigatsiya (`utils/navigation.py`) hozircha faqat mavjud ikkita route'ga
havola qiladi; qolganlari implementatsiya qilingach shu yerdan qo'shiladi.

## Route'lar

| Route | Holat | Vazifasi |
|---|---|---|
| `/maqolalar` | ✅ Phase 2B.1 | Katalog: qidiruv, filtr, sort, karta/jadval, sahifalash |
| `/maqolalar/yonalishlar` | ✅ Phase 2B.1 | Fan yo'nalishlarining ierarxik katalogi |
| `/maqolalar/yonalish/[slug]` | ✅ Phase 2B.1 | Bitta yo'nalish ichidagi maqolalar ro'yxati |
| `/maqolalar/[slug]` | ✅ Phase 2B.1 | Maqola tafsiloti: metadata, annotatsiya, sitata |
| `/maqolalar/[slug]/fayl` | ✅ Phase 2B.1 | PDF oqimi (`can_read_pdf` bilan qo'riqlangan) |
| `/maqolalar/[slug]/oqish` | ⏳ Phase 2B.2 | **Ichki PDF viewer** — hozircha vaqtinchalik sahifa |

`/maqolalar/[slug]/oqish` atayin alohida route: u sahifa ichiga o'rnatilgan PDF
viewer uchun mo'ljallangan (tashqi faylga to'g'ridan-to'g'ri havola emas).
`/maqolalar/[slug]` dagi "PDF'ni o'qish" tugmasi ALLAQACHON shu route'ga
ishora qiladi — Phase 2B.2 da faqat `templates/maqolalar_oqish.html` shabloni
almashadi, URL o'zgarmaydi.

Route hozirdan ishlaydi va kirish qoidasi to'liq kuchda: PDF ommaga ochiq
bo'lmasa 404 (fayl mavjudligi fakti ham oshkor qilinmaydi). Sahifa `noindex` —
viewer tayyor bo'lmaguncha qidiruvdan vaqtinchalik sahifaga tushib qolinmaydi.

## PDF viewer — vizual kontrakt (light-only)

Olimlar.uz'da kun/tun almashtirgichi yo'q va qo'shilmaydi. Viewer yaratilganda
shu qoidalarga amal qilinsin:

- viewer sahifasi umumiy sayt bilan bir xil **light** temada bo'ladi;
- PDF canvas oq yoki hujjatning o'z foni bilan chiziladi — canvasga filtr,
  invert yoki "dark mode" qo'llanmaydi;
- toolbar soft-grey (`--color-background-soft`) yoki juda to'q neutral
  bo'lishi mumkin. To'q toolbar — bu **semantik komponent tanlovi**, global
  dark theme emas; u sahifaning qolgan qismiga tarqalmaydi;
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

- PDF viewer implementatsiyasi (Phase 2B.2) — kontrakti
  `docs/articles-module.md` §13 da;
- fan yo'nalishlarining 2-3 darajasi: rasmiy OAK registri hali topilmadi,
  sun'iy kategoriyalar ATAYIN kiritilmadi (`docs/articles-module.md` §9).

## Mavjud, aralashtirmaslik kerak bo'lgan bo'lim

`/maqolalar-tahlili` — bu boshqa modul (OpenAlex nashr tahlili,
`blueprints/maqolalar.py`). U maqolalar katalogi emas, analitika sahifasi va
navigatsiyada "Tadqiqot" bo'limida turadi.

## Navigatsiya

`utils/navigation.py` dagi "Ilmiy resurslar" bo'limi Phase 2B.1 da
o'zgarishsiz qoldi — yangi route'lar sahifa ichidagi navigatsiya (breadcrumb,
yo'nalishlar daraxti, maqola kartalari) orqali ochiladi, header menyusi
kengaymaydi.
