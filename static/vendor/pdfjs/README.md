# PDF.js — vendored build (Phase 2B.2)

Mozilla PDF.js, ichki ilmiy maqola o'quvchisi (`/maqolalar/<slug>/oqish`) uchun.
Loyihada frontend build tizimi yo'q, shuning uchun tayyor `pdfjs-dist` build'i
qo'lda ko'chirilgan (vendoring). Runtime CDN ATAYIN ishlatilmaydi: `security.py`
dagi CSP `script-src 'self'` bilan cheklangan va uchinchi tomon hosti maqola
o'qish oqimiga kira olmasligi kerak.

## Versiya va manba

| | |
| --- | --- |
| Paket | `pdfjs-dist` |
| Versiya | **5.7.284** (2026-04-27, 5.x liniyasining oxirgi relizi) |
| Build | `legacy/` — eskiroq brauzerlar uchun transpil qilingan variant |
| Tarball | `https://registry.npmjs.org/pdfjs-dist/-/pdfjs-dist-5.7.284.tgz` |
| Tarball SHA-256 | `f14db72530dec74fe666910e246242b9bb698df30f0e06622c4471b340a0413d` |

Fayl checksumlari (nom o'zgargan, baytlar o'zgarmagan):

```text
93a1c3b8106822070313c9a001623d086be33bc1f7743081eef27353f7266959  pdf.min.js
cd10f3bdfa9bb5eb7632776675385f5948f4b09afe6b8b563b6199aa99d5da52  pdf.worker.min.js
```

## Nima olingan

| Fayl | Upstream yo'li | Nima uchun |
| --- | --- | --- |
| `pdf.min.js` | `legacy/build/pdf.min.mjs` | asosiy kutubxona (ES modul) |
| `pdf.worker.min.js` | `legacy/build/pdf.worker.min.mjs` | worker — parsing va rasterizatsiya |
| `standard_fonts/` | `standard_fonts/` | PDF ichiga font joylanmagan bo'lsa (base-14) |
| `wasm/jbig2.wasm` | `wasm/jbig2.wasm` | skanerlangan (bitonal) maqolalar |
| `wasm/openjpeg.wasm` | `wasm/openjpeg.wasm` | JPEG 2000 rasmlar (skanlarda uchraydi) |
| `wasm/qcms_bg.wasm` | `wasm/qcms_bg.wasm` | ICC rang profillari |
| `LICENSE`, `wasm/LICENSE_*` | o'sha joyidan | atribut saqlanishi shart (Apache-2.0 va h.k.) |

### `.mjs` -> `.js` nomi

Ikki sabab:

1. nginx `mime.types` ba'zi versiyalarda `.mjs` ni bilmaydi va faylni
   `application/octet-stream` sifatida beradi — brauzer strict MIME tekshiruvi
   sababli modulni rad etadi. `.js` esa hamma joyda `application/javascript`.
2. `tests/test_light_only_theme.py` uchinchi tomon minifikatsiya qilingan
   fayllarni `.min.js` suffiksi bo'yicha chetlab o'tadi.

Baytlar o'zgarmagan; `<script type="module">` uchun kengaytma ahamiyatsiz.

## Nima ATAYIN olinmagan

| Nima | Sabab |
| --- | --- |
| `wasm/quickjs-eval.wasm` | PDF ichidagi JavaScript dvigateli. Maqola o'quvchisida PDF skriptlari **hech qachon** ishlamasligi kerak (spetsifikatsiya §16), shuning uchun dvigatel umuman serverda yo'q — `enableScripting: false` ustiga qo'shimcha to'siq. |
| `wasm/*_nowasm_fallback.js` | WASM'siz muhitlar uchun; barcha maqsadli brauzerlar WASM'ni qo'llab-quvvatlaydi. Bu fayllar `.js` bo'lgani uchun loyiha manba skanerlariga ham tushib qolardi. |
| `cmaps/` | CJK (xitoy/yapon/koreys) kodlash jadvallari — 1.1 MB. Katalog o'zbek/rus/ingliz maqolalari uchun; CJK PDF paydo bo'lsa alohida qo'shiladi. |
| `iccs/` | Qo'shimcha ICC profillari; `qcms_bg.wasm` asosiy holatlarni qoplaydi. |
| `web/pdf_viewer.css` | Upstream viewer CSS'ida 5 ta `prefers-color-scheme: dark` bloki bor — Olimlar.uz light-only qoidasini buzadi. Kerakli `.textLayer` uslublari `static/css/maqolalar_viewer.css` ichida dizayn tizimi tokenlari bilan qayta yozilgan. |
| `web/pdf_viewer.mjs` | To'liq upstream viewer komponenti (`PDFViewer`, `PDFFindController`, annotatsiya muharriri). Uni olish yuqoridagi CSS'ni ham olib kelardi; o'rniga `getDocument` + `TextLayer` past darajali API'si ishlatiladi. |

## Yangilash tartibi

```bash
curl -sL -o p.tgz https://registry.npmjs.org/pdfjs-dist/-/pdfjs-dist-<VERSIYA>.tgz
sha256sum p.tgz                     # README'dagi qiymatni yangilang
tar xzf p.tgz package/legacy/build/pdf.min.mjs package/legacy/build/pdf.worker.min.mjs \
                package/LICENSE package/standard_fonts package/wasm
cp package/legacy/build/pdf.min.mjs        static/vendor/pdfjs/pdf.min.js
cp package/legacy/build/pdf.worker.min.mjs static/vendor/pdfjs/pdf.worker.min.js
# standard_fonts/ va wasm/{jbig2,openjpeg,qcms_bg}.wasm — yuqoridagi jadval bo'yicha
```

Keyin `blueprints/articles.py:PDFJS_VERSION` ni yangilang: shablon shu qiymatni
`?v=` cache-buster sifatida ishlatadi (nginx `/static/` ni 7 kun keshlaydi) va
`tests/test_articles_viewer.py` asosiy skript bilan worker versiyasi bir xilligini
tekshiradi.

## Litsenziya

PDF.js — Apache License 2.0, (c) Mozilla Foundation. To'liq matn: `LICENSE`.
WASM dekoderlari o'z litsenziyalari bilan: `wasm/LICENSE_*`.
