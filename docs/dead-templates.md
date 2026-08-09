# O'lik shablonlar

Bu yerda render qilinmaydigan, lekin tarixiy ahamiyatga ega shablonlar bo'yicha
qarorlar qayd etiladi. Maqsad — keyingi sessiya bir xil tekshiruvni qaytadan
qilmasligi.

## `templates/index.html` — o'chirildi (light-only cleanup)

**Nima edi:** saytning eski landing sahifasi. `b969bb34 "v0.2: landing page,
AI panel, dashboard rename"` commitida kiritilgan, keyinchalik `home.html`
bilan almashtirilgan. Mustaqil to'liq HTML hujjat edi — `base.html` ni
`extends` qilmagan, o'z `<!DOCTYPE>` va `<html>` tegiga ega bo'lgan.

**Nega o'lik deb topildi:**

- hech bir view, blueprint yoki `render_template()` chaqiruvi unga ishora
  qilmaydi (`kafedra/index.html` — butunlay boshqa fayl);
- hech bir shablon uni `include` yoki `extends` qilmaydi;
- testlar, hujjatlar va admin preview'da ishlatilmaydi;
- dinamik shablon nomi orqali ham chaqirilmaydi.

**Nega aynan shu bosqichda o'chirildi:** loyihadagi yagona haqiqiy kun/tun
almashtirgichi shu faylda edi — `<html data-bs-theme="dark">`,
`#theme-toggle` tugmasi, `localStorage('oak-theme')` va oy/quyosh ikonkalari.
Light-only qarordan keyin uni saqlash faqat chalkashlik keltirar edi.

**Tiklash:** `git show cb465e7f:templates/index.html`

---

## Tekshirilgan, lekin SAQLANGAN

### `templates/home_legacy.html`

Ataylab saqlanadi. `app.py` ichida rollback yo'li sifatida hujjatlashtirilgan
(bosh sahifa redizayni orqaga qaytarilishi kerak bo'lsa). O'chirmang.

### `oak-dissertatsiya/`

Bu **git submodule** — alohida checkout, bu repo manbasi emas. Ichida eski
`base.html`, `stats.html` va boshqa nusxalar bor, ularda kun/tun almashtirgich
ham qolgan. Bu vazifa doirasida unga tegilmadi.
