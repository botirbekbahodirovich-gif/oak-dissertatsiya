/* Ilmiy maqolalar katalogi — progressiv yaxshilanishlar.
 *
 * Sahifa JS'siz ham TO'LIQ ishlaydi: filtrlar oddiy GET forma, yo'nalishlar
 * daraxti <details>siz ham to'liq render qilinadi (skript faqat yig'adi).
 * Bu yerda theme bilan bog'liq hech narsa yo'q va bo'lmaydi (light-only).
 */
(function () {
  'use strict';

  /* Mobil filter paneli — dizayn tizimining ol-filters__body mexanizmi. */
  document.querySelectorAll('[data-mq-filter-toggle]').forEach(function (button) {
    var body = document.getElementById(button.getAttribute('aria-controls'));
    if (!body) return;
    button.addEventListener('click', function () {
      var open = body.getAttribute('data-open') === 'true';
      body.setAttribute('data-open', open ? 'false' : 'true');
      button.setAttribute('aria-expanded', open ? 'false' : 'true');
    });
  });

  /* Sort tanlovi: <option value> — tayyor query string, JS faqat o'tadi. */
  document.querySelectorAll('select[data-mq-nav]').forEach(function (select) {
    select.addEventListener('change', function () {
      if (select.value) window.location.href = select.value;
    });
  });

  /* Yo'nalishlar daraxti — progressive disclosure (§20).
     Sahifa yuklanganda faqat yuqori tarmoqlar ochiq; qidiruv natijasi bo'lsa
     server `data-expanded` qo'yadi va shoxlar ochiq qoladi. */
  document.querySelectorAll('[data-mq-tree-toggle]').forEach(function (button) {
    var panel = document.getElementById(button.getAttribute('aria-controls'));
    if (!panel) return;
    button.addEventListener('click', function () {
      var expanded = button.getAttribute('aria-expanded') === 'true';
      button.setAttribute('aria-expanded', expanded ? 'false' : 'true');
      panel.hidden = expanded;
    });
  });

  /* Admin formasi: muallif qatorini qo'shish. Boshlang'ich qator serverdan
     keladi, shuning uchun JS o'chiq bo'lsa ham kamida bitta muallif kiritiladi. */
  var addAuthor = document.querySelector('[data-mq-add-author]');
  if (addAuthor) {
    addAuthor.addEventListener('click', function () {
      var list = document.getElementById('mq-authors');
      var rows = list.querySelectorAll('.mq-authorrow');
      var template = rows[rows.length - 1];
      var clone = template.cloneNode(true);
      var index = rows.length;
      clone.querySelectorAll('input').forEach(function (input) {
        if (input.type === 'checkbox') {
          input.checked = false;
          input.value = String(index);
        } else {
          input.value = '';
        }
        var id = input.getAttribute('id');
        if (id) input.setAttribute('id', id.replace(/\d+$/, String(index)));
      });
      clone.querySelectorAll('label[for]').forEach(function (label) {
        label.setAttribute('for', label.getAttribute('for').replace(/\d+$/, String(index)));
      });
      list.appendChild(clone);
    });
  }

  /* Admin formasi: metadata-only / restricted tanlansa PDF maydoni yopiladi —
     backenddagi qoidaning (utils/article_rules.can_store_pdf) ko'zga ko'rinadigan
     aksi. Haqiqiy tekshiruv baribir serverda. */
  var accessSelect = document.getElementById('mq-access-type');
  var pdfSection = document.getElementById('mq-pdf-section');
  if (accessSelect && pdfSection) {
    var allowed = (pdfSection.getAttribute('data-allowed') || '').split(',');
    var sync = function () {
      var ok = allowed.indexOf(accessSelect.value) !== -1;
      pdfSection.hidden = !ok;
      pdfSection.querySelectorAll('input').forEach(function (input) {
        input.disabled = !ok;
      });
    };
    accessSelect.addEventListener('change', sync);
    sync();
  }
})();
