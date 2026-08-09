/* Ilmiy maqola PDF o'quvchisi — Phase 2B.2 (ES modul).
 *
 * PDF.js ning PAST DARAJALI API'si (getDocument + TextLayer) ishlatiladi,
 * upstream `web/pdf_viewer.mjs` viewer komponenti EMAS: u o'zi bilan
 * `pdf_viewer.css` ni olib keladi, unda esa dark-mode bloklari bor va
 * Olimlar.uz light-only qoidasini buzadi (static/vendor/pdfjs/README.md).
 *
 * Xavfsizlik qoidalari:
 *   * PDF ichidagi JavaScript hech qachon ijro etilmaydi — `enableScripting`
 *     o'chirilgan va quickjs dvigateli serverga umuman ko'chirilmagan.
 *   * `isEvalSupported: false` — CSP `unsafe-eval` bilan bo'shatilmaydi.
 *   * Havolalar faqat http/https bo'lsa chiziladi, `noopener noreferrer` bilan.
 *   * Native <iframe>/plugin fallback yo'q: PDF faqat canvas orqali chiziladi.
 *
 * Barcha yo'llar (kutubxona, worker, oqim) shablondan `data-*` orqali keladi —
 * bu yerda hech qanday manzil qattiq yozilmagan.
 */

const ZOOM_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3];
const FIT_WIDTH = 'fit';

// Bir vaqtning o'zida xotirada saqlanadigan chizilgan sahifalar soni.
// Undan uzoqdagi kanvaslar bo'shatiladi (canvas.width = 0) — aks holda uzun
// hujjatda brauzer yuzlab megabayt rastr saqlab qoladi.
const MAX_RENDERED_PAGES = 4;

// Bitta kanvasning eng katta piksel maydoni. 200% masshtab + Retina'da A4
// sahifa 30 MB dan oshib ketardi; bu chegara device pixel ratio'ni pasaytiradi.
const MAX_CANVAS_PIXELS = 8 * 1024 * 1024;

// Ko'rinish maydonidan qancha oldinga/orqaga sahifalar tayyorlab qo'yiladi.
const PRERENDER_MARGIN = '120% 0px';

const THUMB_WIDTH = 120;
const RESIZE_DEBOUNCE_MS = 150;

const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

function scrollBehavior() {
  return reduceMotion.matches ? 'auto' : 'smooth';
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

/** PDF ichidagi havola faqat oddiy veb-manzil bo'lsa ochiladi.
 *  `javascript:`, `data:`, `file:` va boshqalar rad etiladi. */
function safeExternalUrl(raw) {
  if (!raw) return null;
  let parsed;
  try {
    parsed = new URL(String(raw), window.location.origin);
  } catch (err) {
    return null;
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null;
  return parsed.href;
}

class ArticlePdfViewer {
  constructor(root) {
    this.root = root;
    this.el = {
      doc: root.querySelector('[data-mqv="doc"]'),
      pages: root.querySelector('[data-mqv="pages"]'),
      side: root.querySelector('[data-mqv="side"]'),
      backdrop: root.querySelector('[data-mqv="backdrop"]'),
      thumbs: root.querySelector('[data-mqv="thumbs"]'),
      outline: root.querySelector('[data-mqv="outline"]'),
      outlineTab: root.querySelector('[data-mqv="tab-outline"]'),
      loading: root.querySelector('[data-mqv="loading"]'),
      loadingText: root.querySelector('[data-mqv="loading-text"]'),
      progressBar: root.querySelector('[data-mqv="progress-bar"]'),
      error: root.querySelector('[data-mqv="error"]'),
      errorTitle: root.querySelector('[data-mqv="error-title"]'),
      errorText: root.querySelector('[data-mqv="error-text"]'),
      retry: root.querySelector('[data-mqv="retry"]'),
      pageInput: root.querySelector('[data-mqv="page-input"]'),
      pageTotal: root.querySelector('[data-mqv="page-total"]'),
      zoom: root.querySelector('[data-mqv="zoom"]'),
      find: root.querySelector('[data-mqv="find"]'),
      findInput: root.querySelector('[data-mqv="find-input"]'),
      findStatus: root.querySelector('[data-mqv="find-status"]'),
      menu: root.querySelector('[data-mqv="menu"]'),
      menuToggle: root.querySelector('[data-mqv="menu-toggle"]'),
      sideToggle: root.querySelector('[data-mqv="side-toggle"]'),
      announce: root.querySelector('[data-mqv="announce"]'),
    };

    this.pdf = null;
    this.pageCount = 0;
    this.currentPage = 1;
    this.scaleMode = FIT_WIDTH;
    this.scale = 1;
    this.rotation = 0;
    this.records = [];          // sahifa boshiga: {el, canvas, textEl, linkEl, task, rendered}
    this.renderOrder = [];      // chizilgan sahifalar — eng eskisi birinchi
    this.pageText = null;       // qidiruv keshi
    this.matches = [];
    this.matchIndex = -1;
    this.lastQuery = '';
    this.destroyed = false;
  }

  // ── Yuklash ───────────────────────────────────────────────────────────────

  async start() {
    const data = this.root.dataset;
    let pdfjsLib;
    try {
      pdfjsLib = await import(data.lib);
    } catch (err) {
      console.error('[Olimlar PDF] PDF.js kutubxonasi yuklanmadi:', data.lib, err);
      this.showError("O'quvchi yuklanmadi",
        "Brauzeringiz bu o'quvchini qo'llab-quvvatlamasligi mumkin. "
        + 'Maqola sahifasiga qaytib, keyinroq urinib ko\'ring.', false);
      return;
    }
    this.lib = pdfjsLib;

    if (data.version && pdfjsLib.version !== data.version) {
      // Serverdagi vendor fayl bilan shablondagi versiya rozi emas — bu
      // odatda cache-buster yangilanmaganini bildiradi.
      console.error('[Olimlar PDF] Versiya mos emas: kutubxona %s, kutilgani %s',
        pdfjsLib.version, data.version);
    }

    const workerOptions = this.createWorker(pdfjsLib, data.worker);

    try {
      const task = pdfjsLib.getDocument({
        url: data.pdfUrl,
        // PDF ichidagi skriptlar ijro etilmaydi (spetsifikatsiya §16).
        enableScripting: false,
        // `new Function` ishlatilmaydi — CSP unsafe-eval talab qilmasin (§30).
        isEvalSupported: false,
        standardFontDataUrl: data.fonts,
        wasmUrl: data.wasm,
        ...workerOptions,
      });
      task.onProgress = ({ loaded, total }) => this.onProgress(loaded, total);
      this.pdf = await task.promise;
    } catch (err) {
      this.reportLoadError(err);
      return;
    }

    if (this.destroyed) return;
    this.pageCount = this.pdf.numPages;
    this.el.pageTotal.textContent = '/ ' + this.pageCount;
    this.el.pageInput.max = String(this.pageCount);
    await this.buildPages();
    this.hideLoading();
    this.bind();
    this.buildThumbnails();
    this.buildOutline();
  }

  /** Worker'ni ochiq yaratamiz, shunda yuklanmasa aniq diagnostika chiqadi.
   *  PDF.js ning "fake worker" fallback'i asosiy yo'l sifatida ishlatilmaydi:
   *  u parsingni asosiy oqimga ko'chiradi va interfeys muzlaydi. */
  createWorker(pdfjsLib, workerSrc) {
    pdfjsLib.GlobalWorkerOptions.workerSrc = workerSrc;
    try {
      const port = new Worker(workerSrc, { type: 'module' });
      port.addEventListener('error', (event) => {
        console.error('[Olimlar PDF] Worker yuklanmadi:', workerSrc,
          event.message || event);
        this.showError("Hujjatni ochib bo'lmadi",
          "PDF o'quvchisining yordamchi moduli yuklanmadi. Sahifani yangilab ko'ring.",
          true);
      });
      return { worker: new pdfjsLib.PDFWorker({ port, name: 'olimlar-maqola-pdf' }) };
    } catch (err) {
      // Modul worker'larni qo'llab-quvvatlamaydigan brauzer — PDF.js o'zi
      // urinib ko'radi. Bu holat jimgina o'tmasligi kerak.
      console.warn('[Olimlar PDF] Modul worker yaratilmadi, PDF.js o\'z yo\'lidan '
        + 'boradi:', err);
      return {};
    }
  }

  onProgress(loaded, total) {
    if (!total || !this.el.progressBar) return;
    const percent = clamp(Math.round((loaded / total) * 100), 0, 100);
    this.el.progressBar.style.width = percent + '%';
    this.el.loadingText.textContent = 'Hujjat yuklanmoqda… ' + percent + '%';
  }

  reportLoadError(err) {
    console.error('[Olimlar PDF] Hujjat yuklanmadi:', err);
    const name = (err && err.name) || '';
    if (name === 'PasswordException') {
      this.showError('Hujjat parol bilan himoyalangan',
        "Bu PDF fayl parol bilan yopilgan, shuning uchun uni saytda ochib bo'lmaydi. "
        + "Muammoni administrator ko'rib chiqishi kerak.", false);
      return;
    }
    if (name === 'InvalidPDFException') {
      this.showError("PDF hujjatini ochib bo'lmadi",
        "Fayl buzilgan yoki to'liq emas. Maqola sahifasiga qaytishingiz mumkin.",
        false);
      return;
    }
    if (name === 'MissingPDFException') {
      this.showError('Hujjat topilmadi',
        'Bu maqolaning to‘liq matni hozir mavjud emas.', false);
      return;
    }
    // Tarmoq yoki Range so'rovi xatosi — qayta urinish mantiqiy.
    this.showError("Hujjatni yuklab bo'lmadi",
      "Aloqa uzildi yoki hujjat qismini olishda xato yuz berdi.", true);
  }

  showError(title, text, retryable) {
    this.hideLoading();
    this.el.errorTitle.textContent = title;
    this.el.errorText.textContent = text;
    this.el.retry.hidden = !retryable;
    this.el.error.hidden = false;
  }

  hideLoading() {
    this.el.loading.hidden = true;
  }

  // ── Sahifa karkasi ────────────────────────────────────────────────────────

  /** Har bir sahifa uchun bo'sh joy egallaydigan oq varaq yaratiladi.
   *  Nisbat birinchi sahifadan olinadi va sahifa chizilganda aniqlashtiriladi
   *  — shunda 300 sahifali hujjat uchun ham boshida faqat bitta `getPage()`
   *  qilinadi, lekin scroll balandligi to'g'ri bo'ladi. */
  async buildPages() {
    const first = await this.pdf.getPage(1);
    const base = first.getViewport({ scale: 1 });
    this.baseRatio = base.height / base.width;
    this.baseWidth = base.width;
    this.scale = this.computeScale(base.width);

    const fragment = document.createDocumentFragment();
    for (let number = 1; number <= this.pageCount; number += 1) {
      const item = document.createElement('li');
      item.className = 'mqv-page';
      item.id = 'mqv-page-' + number;
      item.dataset.page = String(number);
      item.setAttribute('role', 'img');
      item.setAttribute('aria-label', number + '-sahifa');
      this.sizePlaceholder(item, base.width, base.height);

      const label = document.createElement('span');
      label.className = 'mqv-page__number';
      label.textContent = String(number);
      item.append(label);

      fragment.append(item);
      this.records.push({ el: item, number, task: null, rendered: false, scale: 0 });
    }
    this.el.pages.append(fragment);

    this.observer = new IntersectionObserver(
      (entries) => this.onIntersect(entries),
      { root: this.el.doc, rootMargin: PRERENDER_MARGIN, threshold: [0, 0.5] });
    this.records.forEach((record) => this.observer.observe(record.el));
    this.updateZoomLabel();
  }

  sizePlaceholder(el, width, height) {
    const scale = this.scale;
    el.style.width = Math.floor(width * scale) + 'px';
    el.style.height = Math.floor(height * scale) + 'px';
    el.style.setProperty('--total-scale-factor', String(scale));
  }

  computeScale(pageWidth) {
    if (this.scaleMode !== FIT_WIDTH) return this.scaleMode;
    const styles = window.getComputedStyle(this.el.doc);
    const padding = parseFloat(styles.paddingLeft) + parseFloat(styles.paddingRight);
    const available = this.el.doc.clientWidth - padding - 2;
    const rotated = this.rotation % 180 !== 0;
    const reference = rotated ? pageWidth * this.baseRatio : pageWidth;
    return clamp(available / reference, 0.1, 5);
  }

  // ── Sahifani chizish ──────────────────────────────────────────────────────

  onIntersect(entries) {
    let best = null;
    entries.forEach((entry) => {
      const record = this.records[Number(entry.target.dataset.page) - 1];
      if (!record) return;
      record.visible = entry.isIntersecting;
      if (entry.isIntersecting) {
        this.renderPage(record);
        if (!best || entry.intersectionRatio > best.ratio) {
          best = { number: record.number, ratio: entry.intersectionRatio };
        }
      } else {
        this.cancelRender(record);
      }
    });
    if (best && best.ratio > 0) this.setCurrentPage(best.number, false);
  }

  async renderPage(record) {
    if (this.destroyed) return;
    if (record.rendered && record.scale === this.scale
        && record.rotation === this.rotation) return;
    if (record.pending) {
      // Masshtab/burilish chizish paytida o'zgardi. Bekor qilingan vazifa
      // tugagach qayta chiziladi, aks holda sahifa eski masshtabda qolib
      // ketardi (bekor qilish asinxron tugaydi).
      record.dirty = true;
      return;
    }
    record.pending = true;
    record.dirty = false;

    try {
      const page = await this.pdf.getPage(record.number);
      if (this.destroyed || !record.visible) return;

      const viewport = page.getViewport({
        scale: this.scale,
        rotation: (page.rotate + this.rotation) % 360,
      });
      // Landshaft yoki boshqa o'lchamli sahifa — joy egasi aniqlashtiriladi.
      record.el.style.width = Math.floor(viewport.width) + 'px';
      record.el.style.height = Math.floor(viewport.height) + 'px';
      record.el.style.setProperty('--total-scale-factor', String(this.scale));

      const ratio = this.pixelRatio(viewport);
      const canvas = record.canvas || document.createElement('canvas');
      canvas.className = 'mqv-page__canvas';
      canvas.width = Math.floor(viewport.width * ratio);
      canvas.height = Math.floor(viewport.height * ratio);
      if (!record.canvas) {
        record.canvas = canvas;
        record.el.prepend(canvas);
      }

      const context = canvas.getContext('2d', { alpha: false });
      const transform = ratio === 1 ? null : [ratio, 0, 0, ratio, 0, 0];
      record.task = page.render({ canvasContext: context, viewport, transform });
      await record.task.promise;
      record.task = null;

      await this.renderTextLayer(record, page, viewport);
      this.renderLinks(record, page, viewport);

      record.rendered = true;
      record.scale = this.scale;
      record.rotation = this.rotation;
      this.trackRendered(record);
    } catch (err) {
      if (err && err.name === 'RenderingCancelledException') return;
      console.error('[Olimlar PDF] %s-sahifa chizilmadi:', record.number, err);
    } finally {
      record.pending = false;
      if (record.dirty && record.visible) {
        record.dirty = false;
        this.renderPage(record);
      }
    }
  }

  /** Device pixel ratio, lekin kanvas maydoni chegarasidan oshmasin. */
  pixelRatio(viewport) {
    const wanted = Math.min(window.devicePixelRatio || 1, 2);
    const pixels = viewport.width * viewport.height;
    if (pixels * wanted * wanted <= MAX_CANVAS_PIXELS) return wanted;
    return Math.max(1, Math.sqrt(MAX_CANVAS_PIXELS / pixels));
  }

  /** Matn qatlami: matn tanlash, nusxa olish va brauzer qidiruvi uchun.
   *  Skanerlangan (matn qatlamisiz) PDF'da bu shunchaki bo'sh qoladi — OCR
   *  qilinmaydi. */
  async renderTextLayer(record, page, viewport) {
    if (record.textLayer) {
      record.textLayer.cancel();
      record.textLayer = null;
    }
    if (record.textEl) record.textEl.remove();

    const container = document.createElement('div');
    container.className = 'mqv-text';
    record.el.append(container);
    record.textEl = container;

    try {
      const layer = new this.lib.TextLayer({
        textContentSource: page.streamTextContent(),
        container,
        viewport,
      });
      record.textLayer = layer;
      await layer.render();
    } catch (err) {
      if (err && err.name === 'AbortException') return;
      console.warn('[Olimlar PDF] %s-sahifa matn qatlami chizilmadi:',
        record.number, err);
    }
  }

  /** Havolalar. PDF.js AnnotationLayer'i ishlatilmaydi — bu yerda faqat
   *  Link tipidagi annotatsiyalar, protokol tekshiruvidan o'tgan holda. */
  renderLinks(record, page, viewport) {
    if (record.linkEl) record.linkEl.remove();
    const layer = document.createElement('div');
    layer.className = 'mqv-links';
    record.linkEl = layer;
    record.el.append(layer);

    page.getAnnotations({ intent: 'display' }).then((annotations) => {
      annotations.forEach((annotation) => {
        if (annotation.subtype !== 'Link') return;
        const box = this.lib.Util.normalizeRect(
          viewport.convertToViewportRectangle(annotation.rect));
        const url = safeExternalUrl(annotation.url);
        let node = null;

        if (url) {
          node = document.createElement('a');
          node.href = url;
          node.target = '_blank';
          node.rel = 'noopener noreferrer nofollow';
          node.setAttribute('aria-label', 'Tashqi havola: ' + url);
        } else if (annotation.dest) {
          node = document.createElement('button');
          node.type = 'button';
          node.setAttribute('aria-label', 'Hujjat ichidagi havola');
          node.addEventListener('click', () => this.goToDestination(annotation.dest));
        } else {
          return;     // nomlangan amal, JavaScript yoki noma'lum — chizilmaydi
        }

        node.style.left = Math.min(box[0], box[2]) + 'px';
        node.style.top = Math.min(box[1], box[3]) + 'px';
        node.style.width = Math.abs(box[2] - box[0]) + 'px';
        node.style.height = Math.abs(box[3] - box[1]) + 'px';
        layer.append(node);
      });
    }).catch((err) => {
      console.warn('[Olimlar PDF] %s-sahifa havolalari o\'qilmadi:',
        record.number, err);
    });
  }

  cancelRender(record) {
    if (record.task) {
      record.task.cancel();
      record.task = null;
    }
    if (record.textLayer) {
      record.textLayer.cancel();
      record.textLayer = null;
    }
  }

  /** Chizilgan sahifalar sonini cheklaydi — uzoqdagilarning kanvasi
   *  bo'shatiladi, joy egasi esa o'z o'lchamida qoladi. */
  trackRendered(record) {
    this.renderOrder = this.renderOrder.filter((n) => n !== record.number);
    this.renderOrder.push(record.number);
    while (this.renderOrder.length > MAX_RENDERED_PAGES) {
      const number = this.renderOrder.shift();
      const old = this.records[number - 1];
      if (!old || old.visible) continue;
      this.releasePage(old);
    }
  }

  releasePage(record) {
    this.cancelRender(record);
    if (record.canvas) {
      record.canvas.width = 0;
      record.canvas.height = 0;
      record.canvas.remove();
      record.canvas = null;
    }
    if (record.textEl) { record.textEl.remove(); record.textEl = null; }
    if (record.linkEl) { record.linkEl.remove(); record.linkEl = null; }
    record.rendered = false;
  }

  // ── Navigatsiya ───────────────────────────────────────────────────────────

  setCurrentPage(number, scroll) {
    const page = clamp(number, 1, this.pageCount);
    if (scroll) {
      const record = this.records[page - 1];
      if (record) {
        const top = record.el.getBoundingClientRect().top
          - this.el.doc.getBoundingClientRect().top + this.el.doc.scrollTop;
        this.el.doc.scrollTo({ top: top - 8, behavior: scrollBehavior() });
      }
    }
    if (page === this.currentPage) return;
    this.currentPage = page;
    if (document.activeElement !== this.el.pageInput) {
      this.el.pageInput.value = String(page);
    }
    this.el.announce.textContent = page + '-sahifa, jami ' + this.pageCount;
    this.el.thumbs.querySelectorAll('[data-page]').forEach((node) => {
      node.setAttribute('aria-current', node.dataset.page === String(page) ? 'true' : 'false');
    });
  }

  goToPage(number) {
    this.setCurrentPage(number, true);
  }

  async goToDestination(dest) {
    try {
      const resolved = typeof dest === 'string' ? await this.pdf.getDestination(dest) : dest;
      if (!Array.isArray(resolved) || !resolved.length) return;
      const index = await this.pdf.getPageIndex(resolved[0]);
      this.goToPage(index + 1);
    } catch (err) {
      console.warn('[Olimlar PDF] Hujjat ichidagi havola ochilmadi:', err);
    }
  }

  // ── Masshtab ──────────────────────────────────────────────────────────────

  setZoom(mode) {
    this.scaleMode = mode;
    this.applyScale();
  }

  stepZoom(direction) {
    const current = this.scale;
    const steps = direction > 0 ? ZOOM_STEPS : [...ZOOM_STEPS].reverse();
    const next = steps.find((step) => (direction > 0 ? step > current + 0.01
      : step < current - 0.01));
    this.setZoom(next || (direction > 0 ? ZOOM_STEPS[ZOOM_STEPS.length - 1] : ZOOM_STEPS[0]));
  }

  applyScale() {
    const keep = this.currentPage;
    this.scale = this.computeScale(this.baseWidth);
    this.records.forEach((record) => {
      record.rendered = false;
      this.cancelRender(record);
      const width = this.baseWidth;
      const height = this.baseWidth * this.baseRatio;
      const rotated = this.rotation % 180 !== 0;
      this.sizePlaceholder(record.el, rotated ? height : width, rotated ? width : height);
      if (record.visible) this.renderPage(record);
    });
    this.updateZoomLabel();
    this.setCurrentPage(keep, true);
  }

  updateZoomLabel() {
    const select = this.el.zoom;
    const percent = Math.round(this.scale * 100);
    const fitOption = select.querySelector('option[value="fit"]');
    if (fitOption) fitOption.textContent = 'Kenglik bo‘yicha (' + percent + '%)';
    if (this.scaleMode === FIT_WIDTH) {
      select.value = FIT_WIDTH;
      return;
    }
    const exact = select.querySelector('option[value="' + this.scaleMode + '"]');
    select.value = exact ? String(this.scaleMode) : FIT_WIDTH;
  }

  rotate() {
    this.rotation = (this.rotation + 90) % 360;
    this.applyScale();
  }

  // ── Yon panel ─────────────────────────────────────────────────────────────

  isNarrow() {
    return window.matchMedia('(max-width: 767px)').matches;
  }

  /** Boshlang'ich holat CSS'da: `data-side` bo'lmasa desktopda ochiq,
   *  mobilda yopiq. JS faqat foydalanuvchi bosganda aniq holat yozadi. */
  sideOpen() {
    const state = this.root.dataset.side;
    if (state === 'open') return true;
    if (state === 'closed') return false;
    return !this.isNarrow();
  }

  toggleSide(force) {
    const open = typeof force === 'boolean' ? force : !this.sideOpen();
    this.root.dataset.side = open ? 'open' : 'closed';
    this.el.sideToggle.setAttribute('aria-expanded', String(open));
    this.el.backdrop.hidden = !(open && this.isNarrow());
    if (open) {
      const first = this.el.side.querySelector('button, [href]');
      if (first) first.focus();
    } else {
      this.el.sideToggle.focus();
    }
  }

  buildThumbnails() {
    const fragment = document.createDocumentFragment();
    for (let number = 1; number <= this.pageCount; number += 1) {
      const item = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'mqv-thumb ol-focusable';
      button.dataset.page = String(number);
      button.setAttribute('aria-current', number === this.currentPage ? 'true' : 'false');
      button.setAttribute('aria-label', number + '-sahifaga o‘tish');

      const frame = document.createElement('span');
      frame.className = 'mqv-thumb__frame';
      frame.style.height = Math.round(THUMB_WIDTH * this.baseRatio) + 'px';
      const caption = document.createElement('span');
      caption.textContent = String(number);

      button.append(frame, caption);
      button.addEventListener('click', () => {
        this.goToPage(number);
        if (this.isNarrow()) this.toggleSide(false);
      });
      item.append(button);
      fragment.append(item);
    }
    this.el.thumbs.append(fragment);

    // Eskizlar ham lazy: faqat panelda ko'ringanida chiziladi.
    this.thumbObserver = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        this.thumbObserver.unobserve(entry.target);
        this.renderThumbnail(entry.target);
      });
    }, { root: this.el.side.querySelector('[data-mqv="thumbs-panel"]'), rootMargin: '200px 0px' });
    this.el.thumbs.querySelectorAll('[data-page]')
      .forEach((node) => this.thumbObserver.observe(node));
  }

  async renderThumbnail(button) {
    const number = Number(button.dataset.page);
    try {
      const page = await this.pdf.getPage(number);
      const base = page.getViewport({ scale: 1, rotation: page.rotate });
      const viewport = page.getViewport({
        scale: THUMB_WIDTH / base.width,
        rotation: page.rotate,
      });
      const canvas = document.createElement('canvas');
      canvas.width = Math.floor(viewport.width);
      canvas.height = Math.floor(viewport.height);
      canvas.setAttribute('aria-hidden', 'true');
      await page.render({
        canvasContext: canvas.getContext('2d', { alpha: false }), viewport,
      }).promise;
      const frame = button.querySelector('.mqv-thumb__frame');
      frame.style.height = '';
      frame.replaceChildren(canvas);
    } catch (err) {
      console.warn('[Olimlar PDF] %s-sahifa eskizi chizilmadi:', number, err);
    }
  }

  async buildOutline() {
    let outline = null;
    try {
      outline = await this.pdf.getOutline();
    } catch (err) {
      console.warn('[Olimlar PDF] Hujjat tuzilmasi o\'qilmadi:', err);
    }
    if (!outline || !outline.length) return;   // tuzilmasi yo'q — tab chiqmaydi

    const build = (items) => {
      const list = document.createElement('ul');
      items.forEach((entry) => {
        const item = document.createElement('li');
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'mqv-outline__link ol-focusable';
        button.textContent = entry.title || '—';
        button.addEventListener('click', () => {
          this.goToDestination(entry.dest);
          if (this.isNarrow()) this.toggleSide(false);
        });
        item.append(button);
        if (entry.items && entry.items.length) item.append(build(entry.items));
        list.append(item);
      });
      return list;
    };

    this.el.outline.replaceChildren(build(outline));
    this.el.outlineTab.hidden = false;
  }

  // ── Hujjat ichidan qidirish ───────────────────────────────────────────────

  /** MVP qidiruvi: moslik topilgan SAHIFAGA o'tiladi, matn ichida ajratib
   *  ko'rsatilmaydi. Upstream `PDFFindController` butun viewer arxitekturasini
   *  talab qiladi — u keyingi bosqichda (docs/articles-module.md). */
  async collectText() {
    if (this.pageText) return this.pageText;
    const pages = [];
    for (let number = 1; number <= this.pageCount; number += 1) {
      this.el.findStatus.textContent = 'Qidirilmoqda… ' + number + '/' + this.pageCount;
      // eslint-disable-next-line no-await-in-loop
      const page = await this.pdf.getPage(number);
      // eslint-disable-next-line no-await-in-loop
      const content = await page.getTextContent();
      pages.push(content.items.map((item) => item.str || '').join(' ')
        .replace(/\s+/g, ' ').toLowerCase());
    }
    this.pageText = pages;
    return pages;
  }

  async runSearch(query) {
    const needle = (query || '').trim().toLowerCase();
    if (!needle) {
      this.matches = [];
      this.matchIndex = -1;
      this.lastQuery = '';
      this.el.findStatus.textContent = '';
      return;
    }
    if (needle === this.lastQuery && this.matches.length) {
      this.stepMatch(1);
      return;
    }
    const pages = await this.collectText();
    this.matches = [];
    pages.forEach((text, index) => {
      let from = text.indexOf(needle);
      while (from !== -1) {
        this.matches.push(index + 1);
        from = text.indexOf(needle, from + needle.length);
      }
    });
    this.lastQuery = needle;
    this.matchIndex = -1;
    if (!this.matches.length) {
      this.el.findStatus.textContent = 'Moslik topilmadi';
      return;
    }
    this.stepMatch(1);
  }

  stepMatch(direction) {
    if (!this.matches.length) return;
    this.matchIndex = (this.matchIndex + direction + this.matches.length)
      % this.matches.length;
    this.el.findStatus.textContent = (this.matchIndex + 1) + ' / '
      + this.matches.length + ' moslik';
    this.goToPage(this.matches[this.matchIndex]);
  }

  toggleFind(force) {
    const open = typeof force === 'boolean' ? force : this.el.find.hidden;
    this.el.find.hidden = !open;
    this.root.querySelector('[data-mqv="find-toggle"]')
      .setAttribute('aria-expanded', String(open));
    if (open) this.el.findInput.focus();
  }

  // ── Hodisalar ─────────────────────────────────────────────────────────────

  bind() {
    const on = (selector, event, handler) => {
      const node = this.root.querySelector(selector);
      if (node) node.addEventListener(event, handler);
    };

    on('[data-mqv="prev"]', 'click', () => this.goToPage(this.currentPage - 1));
    on('[data-mqv="next"]', 'click', () => this.goToPage(this.currentPage + 1));
    on('[data-mqv="zoom-out"]', 'click', () => this.stepZoom(-1));
    on('[data-mqv="zoom-in"]', 'click', () => this.stepZoom(1));
    on('[data-mqv="fit"]', 'click', () => this.setZoom(FIT_WIDTH));
    on('[data-mqv="rotate"]', 'click', () => this.rotate());
    on('[data-mqv="fullscreen"]', 'click', () => this.toggleFullscreen());
    on('[data-mqv="find-toggle"]', 'click', () => this.toggleFind());
    on('[data-mqv="find-prev"]', 'click', () => this.stepMatch(-1));
    on('[data-mqv="find-next"]', 'click', () => this.stepMatch(1));
    on('[data-mqv="side-close"]', 'click', () => this.toggleSide(false));
    on('[data-mqv="backdrop"]', 'click', () => this.toggleSide(false));
    on('[data-mqv="retry"]', 'click', () => window.location.reload());

    this.el.sideToggle.addEventListener('click', () => this.toggleSide());
    this.el.sideToggle.setAttribute('aria-expanded', String(this.sideOpen()));

    this.el.zoom.addEventListener('change', () => {
      const value = this.el.zoom.value;
      this.setZoom(value === FIT_WIDTH ? FIT_WIDTH : Number(value));
    });

    // Noto'g'ri sahifa raqami (0, -1, 999, "abc") xato bermaydi — chegaraga
    // qisiladi va maydon haqiqiy holatni ko'rsatadi.
    const applyPageInput = () => {
      const value = Number.parseInt(this.el.pageInput.value, 10);
      if (Number.isNaN(value)) {
        this.el.pageInput.value = String(this.currentPage);
        return;
      }
      this.goToPage(clamp(value, 1, this.pageCount));
      this.el.pageInput.value = String(this.currentPage);
    };
    this.el.pageInput.addEventListener('change', applyPageInput);
    this.el.pageInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') { event.preventDefault(); applyPageInput(); }
    });

    this.el.findInput.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      this.runSearch(this.el.findInput.value);
    });

    this.el.menuToggle.addEventListener('click', () => {
      const open = this.el.menu.hidden;
      this.el.menu.hidden = !open;
      this.el.menuToggle.setAttribute('aria-expanded', String(open));
    });
    document.addEventListener('click', (event) => {
      if (this.el.menu.hidden) return;
      if (this.el.menu.contains(event.target) || this.el.menuToggle.contains(event.target)) return;
      this.el.menu.hidden = true;
      this.el.menuToggle.setAttribute('aria-expanded', 'false');
    });

    this.bindTabs();
    this.bindKeyboard();

    let timer = null;
    window.addEventListener('resize', () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        if (this.scaleMode === FIT_WIDTH) this.applyScale();
      }, RESIZE_DEBOUNCE_MS);
    });
  }

  bindTabs() {
    const tabs = [...this.root.querySelectorAll('[role="tab"]')];
    tabs.forEach((tab) => {
      tab.addEventListener('click', () => {
        tabs.forEach((other) => {
          const selected = other === tab;
          other.setAttribute('aria-selected', String(selected));
          const panel = this.root.querySelector('#' + other.getAttribute('aria-controls'));
          if (panel) panel.hidden = !selected;
        });
      });
    });
  }

  bindKeyboard() {
    document.addEventListener('keydown', (event) => {
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      const target = event.target;
      const tag = target && target.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT'
          || (target && target.isContentEditable)) {
        if (event.key === 'Escape') target.blur();
        return;
      }
      switch (event.key) {
        case 'ArrowLeft':
        case 'PageUp':
          event.preventDefault(); this.goToPage(this.currentPage - 1); break;
        case 'ArrowRight':
        case 'PageDown':
          event.preventDefault(); this.goToPage(this.currentPage + 1); break;
        case '+':
        case '=':
          event.preventDefault(); this.stepZoom(1); break;
        case '-':
          event.preventDefault(); this.stepZoom(-1); break;
        case '0':
          event.preventDefault(); this.setZoom(FIT_WIDTH); break;
        case 'Escape':
          if (!this.el.menu.hidden) {
            this.el.menu.hidden = true;
            this.el.menuToggle.setAttribute('aria-expanded', 'false');
          } else if (!this.el.find.hidden) {
            this.toggleFind(false);
          } else if (document.fullscreenElement) {
            document.exitFullscreen();
          } else if (this.isNarrow() && this.sideOpen()) {
            this.toggleSide(false);
          }
          break;
        default:
          break;
      }
    });
  }

  toggleFullscreen() {
    if (!document.fullscreenEnabled) return;
    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else {
      this.root.requestFullscreen().catch((err) => {
        console.warn('[Olimlar PDF] To\'liq ekran rejimi ochilmadi:', err);
      });
    }
  }
}

const root = document.querySelector('[data-mqv="root"]');
if (root) {
  const viewer = new ArticlePdfViewer(root);
  // To'liq ekran tugmasi brauzerda mavjud bo'lmasa, ko'rsatilmasin.
  if (!document.fullscreenEnabled) {
    const button = root.querySelector('[data-mqv="fullscreen"]');
    if (button) button.hidden = true;
  }
  viewer.start();
}
