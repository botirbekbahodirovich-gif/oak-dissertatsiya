# Maqolalar katalogi — route kontrakti

Ushbu hujjat ilmiy maqolalar bo'limining kelajakdagi URL tuzilmasini qayd etadi.
Navigatsiya (`utils/navigation.py`) hozircha faqat mavjud ikkita route'ga
havola qiladi; qolganlari implementatsiya qilingach shu yerdan qo'shiladi.

## Route'lar

| Route | Holat | Vazifasi |
|---|---|---|
| `/maqolalar` | ✅ mavjud (karkas) | Katalog kirish sahifasi, qidiruv UI karkasi, empty state |
| `/maqolalar/yonalishlar` | ✅ mavjud (karkas) | Fan yo'nalishlarining ierarxik katalogi |
| `/maqolalar/yonalish/[slug]` | ⏳ rejalashtirilgan | Bitta yo'nalish ichidagi maqolalar ro'yxati |
| `/maqolalar/[slug]` | ⏳ rejalashtirilgan | Maqola tafsiloti: metadata, annotatsiya, sitata |
| `/maqolalar/[slug]/oqish` | ⏳ rejalashtirilgan | **Ichki PDF viewer** — maqolani saytdan chiqmasdan o'qish |

`/maqolalar/[slug]/oqish` atayin alohida route: u sahifa ichiga o'rnatilgan PDF
viewer uchun mo'ljallangan (tashqi faylga to'g'ridan-to'g'ri havola emas).
Viewer chiqqanda `/maqolalar/[slug]` dagi "O'qish" tugmasi shu route'ga
yo'naltiriladi.

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

## Bu bosqichda ATAYIN qilinmagan ishlar

- maqolalar backendi va ma'lumotlar modeli;
- PDF yuklash tizimi;
- fan yo'nalishlarini seed qilish (sun'iy kategoriyalar kiritilmadi);
- PDF viewer implementatsiyasi.

## Mavjud, aralashtirmaslik kerak bo'lgan bo'lim

`/maqolalar-tahlili` — bu boshqa modul (OpenAlex nashr tahlili,
`blueprints/maqolalar.py`). U maqolalar katalogi emas, analitika sahifasi va
navigatsiyada "Tadqiqot" bo'limida turadi.

## Keyingi bosqich uchun eslatma

Katalog ishga tushganda `utils/navigation.py` dagi "Ilmiy resurslar" bo'limi
o'zgarishsiz qoladi — yangi route'lar sahifa ichidagi navigatsiya orqali
ochiladi, header menyusi kengaymaydi.
