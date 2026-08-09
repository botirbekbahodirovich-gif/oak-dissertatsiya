"""Olimlar.uz — yagona navigatsiya konfiguratsiyasi.

Desktop header, mobil drawer va footer AYNAN shu fayldan o'qiydi. Linklarni
shablon ichida qo'lda takrorlash taqiqlanadi — yangi sahifa qo'shilganda faqat
shu yerdagi ro'yxat tahrirlanadi.

Route qo'shishdan oldin: route haqiqatan mavjudligiga ishonch hosil qiling.
Mavjud bo'lmagan route navigatsiyaga qo'shilmaydi (404 havolalar chiqmasin).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NavItem:
    """Navigatsiyadagi bitta havola.

    `exact=True` — faqat aynan shu path aktiv sanaladi. Bu bir bo'lim ichida
    ichma-ich joylashgan route'lar uchun kerak (masalan `/reyting` va
    `/reyting/ball` yonma-yon tursa, `/reyting` ni exact qilish shart).
    `aliases` — eski yoki muqobil route'lar; ular ochilganda ham shu band aktiv.
    """

    label: str
    href: str
    icon: str = ""
    badge: str = ""
    description: str = ""
    exact: bool = False
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class NavSection:
    """Header'dagi asosiy bo'lim — desktop'da dropdown, mobilda accordion."""

    key: str
    label: str
    children: tuple[NavItem, ...] = ()


@dataclass(frozen=True)
class FooterColumn:
    title: str
    items: tuple[NavItem, ...] = ()


# ── Asosiy header navigatsiyasi ───────────────────────────────────────────────
# Premium, Qidiruv va Kabinet bu ro'yxatda EMAS — ular header'ning o'ng
# tomonidagi alohida boshqaruv elementlari (site_header.html).

PRIMARY_NAV: tuple[NavSection, ...] = (
    NavSection(
        key="tadqiqot",
        label="Tadqiqot",
        children=(
            NavItem("Dissertatsiyalar", "/dissertatsiyalar", icon="bi-journal-text",
                    description="29 000+ himoya qilingan ish",
                    aliases=("/dashboard", "/data", "/dissertation")),
            NavItem("Mavzu tahlili", "/compare", icon="bi-bar-chart-steps",
                    description="Mavzular bo'yicha qiyosiy tahlil",
                    aliases=("/mavzu-tahlili", "/mavzu/tahlil")),
            NavItem("Maqolalar tahlili", "/maqolalar-tahlili", icon="bi-graph-up",
                    description="OpenAlex bo'yicha nashr tahlili"),
            NavItem("Ilmiy rahbar topish", "/rahbar-topish", icon="bi-person-badge",
                    description="Rahbarlar katalogi va profillari",
                    aliases=("/rahbar",)),
            NavItem("Ilmiy kengashlar", "/councils", icon="bi-building-check",
                    description="Kengashlar va ixtisosliklar"),
            NavItem("Dissertatsiya konstruktori", "/workspace", icon="bi-tools",
                    badge="AI", description="Ishni bosqichma-bosqich yig'ish",
                    aliases=("/reja",)),
            NavItem("Himoyaga tayyorlanish", "/preparation", icon="bi-clipboard-check",
                    description="Himoya jarayoni bo'yicha qo'llanma"),
        ),
    ),
    NavSection(
        key="olimlar",
        label="Olimlar",
        children=(
            NavItem("Olimlar katalogi", "/olimlar", icon="bi-people", exact=True,
                    description="Tadqiqotchilar profillari",
                    aliases=("/olim", "/top-olimlar")),
            NavItem("Ilmiy hamkorlik", "/collaboration", icon="bi-diagram-3",
                    description="Hammualliflik va hamkorlik grafigi"),
        ),
    ),
    NavSection(
        key="universitet",
        label="Universitet",
        children=(
            NavItem("Universitetlar", "/universities", icon="bi-bank",
                    description="Tashkilot profillari va reytingi"),
            NavItem("Kafedra hujjatlari", "/kafedra/", icon="bi-folder2-open",
                    badge="AI", description="Kafedra hujjatlari generatori"),
        ),
    ),
    NavSection(
        key="resurslar",
        label="Ilmiy resurslar",
        children=(
            NavItem("Ilmiy maqolalar", "/maqolalar", icon="bi-file-earmark-text", exact=True,
                    description="Maqolalar katalogi"),
            NavItem("Fan yo'nalishlari", "/maqolalar/yonalishlar", icon="bi-list-nested",
                    description="Ierarxik yo'nalishlar katalogi"),
            NavItem("Ilmiy jurnallar", "/journals", icon="bi-journals",
                    description="Jurnallar bazasi"),
            NavItem("Jurnal tekshiruvi", "/jurnal-tekshirish", icon="bi-shield-check",
                    description="Jurnal ishonchliligini tekshirish"),
        ),
    ),
    NavSection(
        key="imkoniyatlar",
        label="Imkoniyatlar",
        children=(
            NavItem("Grantlar", "/grants", icon="bi-cash-coin",
                    description="Ochiq grant tanlovlari"),
            NavItem("Konferensiyalar", "/konferensiyalar", icon="bi-mic",
                    description="Mahalliy va xalqaro anjumanlar"),
            NavItem("Kurslar", "/courses", icon="bi-mortarboard",
                    description="Malaka oshirish kurslari"),
            NavItem("Vakansiyalar", "/vacancies", icon="bi-briefcase",
                    description="Ilmiy va pedagogik ish o'rinlari"),
            NavItem("Yaqinlashayotgan muddatlar", "/reminders", icon="bi-alarm",
                    description="Deadline'lar va eslatmalar"),
        ),
    ),
    NavSection(
        key="tahlil",
        label="Tahlil",
        children=(
            NavItem("Umumiy tahlil", "/stats", icon="bi-pie-chart",
                    description="Platforma bo'yicha umumiy statistika"),
            NavItem("Trendlar", "/trends", icon="bi-graph-up-arrow",
                    description="Yillar kesimidagi o'zgarishlar"),
            NavItem("Mavzular klasterlari", "/clustering", icon="bi-bounding-box",
                    description="Mavzular bo'yicha klasterlash",
                    aliases=("/cluster",)),
            # Canonical geo-route. /heatmap eski (backward-compatible) alias.
            NavItem("Tadqiqot xaritasi", "/tadqiqot-xaritasi", icon="bi-map",
                    description="Hududlar kesimidagi xarita",
                    aliases=("/heatmap", "/xarita")),
            NavItem("H-index reyting", "/reyting", icon="bi-award", exact=True,
                    description="Olimlar H-index reytingi",
                    aliases=("/reyting/tashkilotlar",)),
            NavItem("Ichki ball reytingi", "/reyting/ball", icon="bi-123",
                    description="Platformaning ichki ball tizimi"),
        ),
    ),
)


# ── Header'ning o'ng tomonidagi alohida elementlar ────────────────────────────

# DIQQAT: bu sayt bo'ylab qidiruv EMAS. Loyihada global qidiruv mavjud emas —
# har bir modul o'z qidiruviga ega (dissertatsiyalar, olimlar katalogi,
# hamkorlik, maqolalar tahlili). Header tugmasi shu sababli faqat
# dissertatsiyalar qidiruviga yo'naltiradi va overlay matni buni aniq aytadi.
# TODO(global-search): haqiqiy ko'p-entity qidiruv (dissertatsiya + olim +
# jurnal + grant) paydo bo'lganda shu konstanta va site_header.html dagi
# overlay o'sha endpoint'ga o'tkazilsin.
SEARCH_TARGET = "/dissertatsiyalar"
SEARCH_SCOPE_LABEL = "Dissertatsiyalar bo'yicha qidiruv"
SEARCH_SCOPE_HINT = ("Hozircha faqat dissertatsiyalar bazasi bo'yicha qidiriladi. "
                     "Olimlar, jurnallar va grantlar o'z bo'limlarida qidiriladi.")

PREMIUM_ITEM = NavItem("Premium", "/premium", icon="bi-star-fill",
                       description="Kengaytirilgan imkoniyatlar")
LOGIN_ITEM = NavItem("Kirish", "/login", icon="bi-box-arrow-in-right")

# Header'dagi ikonka tugmalari (faqat tizimga kirgan foydalanuvchiga).
# Shu yerdan render qilinadi — shablon ichida qo'lda yozilmaydi. Tor
# ekranlarda ular yashiriladi va drawer ichidagi ACCOUNT_MENU orqali ochiladi.
HEADER_ICON_ITEMS: tuple[NavItem, ...] = (
    NavItem("Xabarlar", "/messages", icon="bi-chat-dots"),
    NavItem("Bildirishnomalar", "/notifications", icon="bi-bell"),
)

# Avatar dropdown'i. Admin bandlar shablon tomonidan alohida qo'shiladi.
ACCOUNT_MENU: tuple[NavItem, ...] = (
    NavItem("Kabinet", "/cabinet", icon="bi-person-circle", exact=True),
    NavItem("Saqlanganlar", "/cabinet/saqlangan", icon="bi-bookmark"),
    NavItem("Mening maqolalarim", "/cabinet/maqolalar", icon="bi-file-earmark-text"),
    NavItem("To'lovlar", "/cabinet/tolovlar", icon="bi-credit-card"),
    NavItem("Xabarlar", "/messages", icon="bi-chat-dots"),
    NavItem("Bildirishnomalar", "/notifications", icon="bi-bell"),
)

LOGOUT_ITEM = NavItem("Chiqish", "/logout", icon="bi-box-arrow-right")

# Eski "Boshqa" dropdown'i admin navigatsiyasi vazifasini ham bajargan —
# admin sahifalarida o'zaro havolalar yo'q edi. To'liq ro'yxat ADMIN_SERVICES
# da saqlanadi va admin panelining yuqorisida grid sifatida chiqadi; avatar
# dropdown'ida esa faqat bir nechta tez havola qoladi (22 bandli dropdown
# 1440x900 ekranda 812px balandlikka cho'zilib ketgan edi).
ADMIN_HUB = NavItem("Admin panel", "/admin/analytics", icon="bi-speedometer2")

ADMIN_SERVICES: tuple[NavItem, ...] = (
    NavItem("Foydalanuvchi manbalari", "/admin/acquisition", icon="bi-signpost-split",
            description="Ro'yxatdan o'tish manbalari tahlili"),
    NavItem("Ma'lumot sifati", "/admin/data-quality", icon="bi-clipboard-data",
            description="Dublikatlar va tuzatish jurnali"),
    NavItem("Yangiliklar", "/admin/yangiliklar", icon="bi-newspaper",
            description="Yangiliklarni boshqarish"),
    NavItem("Blog", "/admin/blog", icon="bi-pencil-square",
            description="Blog postlari"),
    NavItem("Vakansiyalar", "/admin/vacancies", icon="bi-briefcase",
            description="Ish o'rinlari e'lonlari"),
    NavItem("Grantlar", "/admin/grants", icon="bi-cash-coin",
            description="Grant tanlovlari"),
    NavItem("Jurnallar", "/admin/journals", icon="bi-journals",
            description="Jurnallar bazasi va shubhalilar"),
    NavItem("Konferensiyalar", "/admin/conferences", icon="bi-mic",
            description="Anjumanlar import/eksporti"),
    NavItem("Kurslar talabi", "/admin/courses", icon="bi-mortarboard",
            description="Kurslarga talab statistikasi"),
    NavItem("Universitetlar", "/admin/universities", icon="bi-bank", exact=True,
            description="Tashkilot profillari"),
    NavItem("Universitet litsenziyalari", "/admin/universities/licenses", icon="bi-key",
            description="B2B litsenziyalar"),
    NavItem("Institutlarni birlashtirish", "/admin/institutions", icon="bi-diagram-2",
            description="Dublikat tashkilotlarni birlashtirish"),
    NavItem("So'rovnoma", "/admin/survey", icon="bi-ui-checks",
            description="Foydalanuvchi so'rovnomalari"),
    NavItem("Smart eslatmalar", "/admin/reminders", icon="bi-alarm",
            description="Muddat eslatmalari dispatchi"),
    NavItem("To'lovlar", "/admin/payments", icon="bi-credit-card",
            description="ATMOS to'lovlari"),
    NavItem("Reyting", "/admin/reyting", icon="bi-award",
            description="H-index snapshotlari"),
    NavItem("Maqolalar importi", "/admin/articles/import", icon="bi-cloud-arrow-down",
            description="DOI/CSV orqali ommaviy import va ko'rib chiqish"),
    NavItem("Yangi maqolalarni qidirish", "/admin/articles/discovery", icon="bi-search",
            description="OpenAlex'dan kalit so'z bo'yicha nomzod maqolalar topish"),
    NavItem("Maqolalar metadata nazorati", "/admin/articles/metadata-sync",
            icon="bi-arrow-repeat",
            description="Tashqi manbalarda topilgan o'zgarishlarni ko'rib chiqish"),
)

# Avatar dropdown'idagi qisqa admin bloki. To'liq ro'yxat admin panelida.
ADMIN_MENU: tuple[NavItem, ...] = (
    ADMIN_HUB,
    NavItem("Ma'lumot sifati", "/admin/data-quality", icon="bi-clipboard-data"),
    NavItem("Smart eslatmalar", "/admin/reminders", icon="bi-alarm"),
)

# Universitet B2B ish maydoni — faqat litsenziyaga ega foydalanuvchilarga.
UNIVER_ITEM = NavItem("Universitetim", "/univer", icon="bi-building")


# ── Footer ────────────────────────────────────────────────────────────────────
# Yangiliklar va Blog ATAYIN header'da emas — ular "Axborot" ustunida.

FOOTER_COLUMNS: tuple[FooterColumn, ...] = (
    FooterColumn("Platforma", (
        NavItem("Tadqiqot", "/dissertatsiyalar"),
        NavItem("Olimlar", "/olimlar"),
        NavItem("Universitet", "/universities"),
        NavItem("Tahlil", "/stats"),
    )),
    FooterColumn("Ilmiy resurslar", (
        NavItem("Ilmiy maqolalar", "/maqolalar"),
        NavItem("Fan yo'nalishlari", "/maqolalar/yonalishlar"),
        NavItem("Ilmiy jurnallar", "/journals"),
        NavItem("Jurnal tekshiruvi", "/jurnal-tekshirish"),
    )),
    FooterColumn("Imkoniyatlar", (
        NavItem("Grantlar", "/grants"),
        NavItem("Konferensiyalar", "/konferensiyalar"),
        NavItem("Kurslar", "/courses"),
        NavItem("Vakansiyalar", "/vacancies"),
        NavItem("Muddatlar", "/reminders"),
    )),
    FooterColumn("Axborot", (
        NavItem("Yangiliklar", "/yangiliklar"),
        NavItem("Blog", "/blog"),
        NavItem("Biz haqimizda", "/about"),
        NavItem("Jamoa", "/team"),
        NavItem("Aloqa", "/contact"),
    )),
    FooterColumn("Huquqiy", (
        NavItem("Ommaviy oferta", "/oferta"),
        NavItem("To'lovni qaytarish", "/tolovni-qaytarish"),
        NavItem("Maxfiylik siyosati", "/maxfiylik"),
    )),
)


# ── Aktiv holat ───────────────────────────────────────────────────────────────

def _normalize(path: str) -> str:
    """Query parametrlarni tashlab, oxirgi '/' ni olib tashlaydi ('/' o'zi qoladi)."""
    if not path:
        return "/"
    path = path.split("?", 1)[0].split("#", 1)[0]
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return path or "/"


def _matches(target: str, path: str, exact: bool) -> bool:
    target = _normalize(target)
    if target == "/":
        return path == "/"
    if path == target:
        return True
    return not exact and path.startswith(target + "/")


def item_is_active(item: NavItem, path: str) -> bool:
    """Aniq route, ichma-ich route va alias'larni qamrab oladi.

    Query parametrlar `_normalize` da tashlab yuboriladi, shuning uchun
    `/dissertatsiyalar?q=...` ham aktiv sanaladi.
    """
    path = _normalize(path)
    if _matches(item.href, path, item.exact):
        return True
    return any(_matches(alias, path, False) for alias in item.aliases)


def section_is_active(section: NavSection, path: str) -> bool:
    return any(item_is_active(child, path) for child in section.children)


def navigation_context() -> dict:
    """base.html va komponentlar uchun context processor ma'lumotlari."""
    return {
        "primary_nav": PRIMARY_NAV,
        "footer_columns": FOOTER_COLUMNS,
        "account_menu": ACCOUNT_MENU,
        "admin_menu": ADMIN_MENU,
        "admin_services": ADMIN_SERVICES,
        "admin_hub": ADMIN_HUB,
        "header_icon_items": HEADER_ICON_ITEMS,
        "univer_item": UNIVER_ITEM,
        "premium_item": PREMIUM_ITEM,
        "login_item": LOGIN_ITEM,
        "logout_item": LOGOUT_ITEM,
        "nav_search_target": SEARCH_TARGET,
        "nav_search_label": SEARCH_SCOPE_LABEL,
        "nav_search_hint": SEARCH_SCOPE_HINT,
        "nav_item_active": item_is_active,
        "nav_section_active": section_is_active,
    }
