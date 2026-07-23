"""Universal Jinja filterlar — kiril/lotin ko'rsatish uchun (SEO, dual-alifbo)."""
from utils.transliterate import kiril_to_lotin, lotin_to_kiril


def register_filters(app):
    @app.template_filter('to_latin')
    def to_latin_filter(text):
        if not text:
            return ''
        return kiril_to_lotin(str(text))

    @app.template_filter('to_kiril')
    def to_kiril_filter(text):
        if not text:
            return ''
        return lotin_to_kiril(str(text))

    @app.template_filter('dual_alifbo')
    def dual_alifbo_filter(text):
        if not text:
            return ''
        text = str(text)
        latin = kiril_to_lotin(text)
        if latin and latin != text.lower():
            return f"{text}, {latin}"
        return text
