"""Combined Uzbek + Russian + English stop-word list for topic clustering.

app.py's STOP_WORDS (~35 entries) and blueprints/topic_analysis.py's
_STOP_WORDS (~40 entries) are both Uzbek-only — neither has Russian stems
(dissertation topics are often written in Russian) or English at all, which
is why generic words like "ишлаб" (from "ишлаб чиқариш" = manufacturing) and
"технологиясини" (a genitive form of "technology") pollute clusters as if
they were meaningful keywords. This module is a superset meant to replace
both lists; wiring it into app.py's live _clustering_build() is out of scope
for this phase (touches the forbidden monolith) but is documented in
docs/data-quality/architecture.md §5.
"""

STOPWORDS_UZ = frozenset({
    # Latin
    "va", "ham", "yoki", "lekin", "ammo", "biroq", "uchun", "bilan", "kabi",
    "haqida", "orqali", "natijasida", "asosida", "sababli", "tufayli",
    "bu", "shu", "u", "ular", "biz", "siz", "men", "sen", "uning", "ularning",
    "bizning", "sizning", "mening", "sening", "bo'lgan", "bo'lib", "bo'yicha",
    "qilish", "qilib", "qiladi", "qilingan", "etish", "etib", "etadi",
    "mavzusida", "yo'nalishida", "sohasida", "asosiy", "muhim", "yangi",
    "ishlab", "chiqarish", "texnologiyasi", "texnologiyasini", "texnologik",
    # Cyrillic
    "ва", "ҳам", "ёки", "лекин", "аммо", "бироқ", "учун", "билан", "каби",
    "ҳақида", "орқали", "натижасида", "асосида", "сабабли", "туфайли",
    "бу", "шу", "у", "улар", "биз", "сиз", "мен", "сен", "унинг", "уларнинг",
    "бизнинг", "сизнинг", "менинг", "сенинг", "бўлган", "бўлиб", "бўйича",
    "қилиш", "қилиб", "қилади", "қилинган", "этиш", "этиб", "этади",
    "мавзусида", "йўналишида", "соҳасида", "асосий", "муҳим", "янги",
    "ишлаб", "чиқариш", "чиқаришни", "технология", "технологияси",
    "технологиясини", "технологиясига", "технологик", "ишлаб-чиқариш",
})

STOPWORDS_RU = frozenset({
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а",
    "то", "все", "она", "так", "его", "но", "да", "ты", "к", "у", "же",
    "вы", "за", "бы", "по", "только", "ее", "мне", "было", "вот", "от",
    "меня", "еще", "нет", "о", "из", "ему", "теперь", "когда", "даже",
    "ну", "вдруг", "ли", "если", "уже", "или", "ни", "быть", "был",
    "него", "до", "вас", "нибудь", "опять", "уж", "вам", "ведь", "там",
    "потом", "себя", "ничего", "ей", "может", "они", "тут", "где", "есть",
    "надо", "ней", "для", "мы", "тебя", "их", "чем", "была", "сам",
    "чтоб", "без", "будто", "чего", "раз", "тоже", "себе", "под", "будет",
    "тогда", "кто", "этот", "того", "потому", "этого", "какой", "совсем",
    "ним", "здесь", "этом", "один", "почти", "мой", "тем", "чтобы", "нее",
    "сейчас", "были", "куда", "зачем", "всех", "никогда", "можно", "при",
    "наконец", "два", "об", "другой", "хоть", "после", "над", "больше",
    "тот", "через", "эти", "нас", "про", "всего", "них", "какая", "много",
    "разве", "три", "эту", "моя", "впрочем", "хорошо", "свою", "этой",
    "перед", "иногда", "лучше", "чуть", "том", "нельзя", "такой", "им",
    "более", "всегда", "конечно", "всю", "между", "также",
})

STOPWORDS_EN = frozenset({
    "a", "an", "the", "of", "in", "on", "at", "for", "to", "and", "or",
    "but", "is", "are", "was", "were", "be", "been", "being", "this",
    "that", "these", "those", "with", "as", "by", "from", "it", "its",
    "into", "than", "then", "so", "such", "not", "no", "nor", "do",
    "does", "did", "has", "have", "had", "can", "could", "will", "would",
    "shall", "should", "may", "might", "must", "about", "above", "after",
    "again", "against", "all", "am", "any", "because", "before", "below",
    "between", "both", "during", "each", "few", "further", "here", "how",
    "if", "more", "most", "once", "only", "other", "out", "over", "own",
    "same", "some", "than", "too", "under", "until", "up", "very", "what",
    "when", "where", "which", "while", "who", "whom", "why",
})

COMBINED_STOPWORDS = STOPWORDS_UZ | STOPWORDS_RU | STOPWORDS_EN


def is_stopword(word):
    return (word or "").strip().lower() in COMBINED_STOPWORDS


def filter_keywords(tokens, min_len=4):
    """Drop-in replacement shape for app.py's _extract_keywords filter
    (`len(w) < 4 or w in STOP_WORDS or w in seen`), but against the combined
    multilingual list."""
    seen = set()
    out = []
    for tok in tokens:
        w = (tok or "").strip().lower()
        if len(w) < min_len or w in COMBINED_STOPWORDS or w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out
