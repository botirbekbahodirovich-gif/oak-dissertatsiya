/* F.I.Sh. (ism-familiya-otasining ismi) maydonlari uchun avtomatik katta harf.
 * class="auto-capitalize" qo'yilgan har qanday <input>/<textarea> ga qo'llanadi.
 * Har bir so'zning birinchi harfi katta, qolgani kichik ("aliyev valijon" -> "Aliyev Valijon").
 * Kiril va lotin (o', g', sh, ch) uchun ishlaydi — apostrofdan keyingi harf
 * so'z ichida qolgani uchun avtomatik kichik bo'lib qoladi ("o'ktam" -> "O'ktam").
 */
(function () {
  function toAutoCapital(value) {
    return value.toLowerCase().replace(/(^|[\s-])(\S)/g, function (m, sep, ch) {
      return sep + ch.toUpperCase();
    });
  }

  function handleInput(e) {
    var el = e.target;
    if (!el || !el.classList || !el.classList.contains('auto-capitalize')) return;
    var start = el.selectionStart;
    var end = el.selectionEnd;
    var oldVal = el.value;
    var newVal = toAutoCapital(oldVal);
    if (newVal === oldVal) return;
    el.value = newVal;
    if (typeof start === 'number' && typeof end === 'number') {
      el.setSelectionRange(start, end);
    }
  }

  document.addEventListener('input', handleInput);

  window.autoCapitalizeName = toAutoCapital;
})();
