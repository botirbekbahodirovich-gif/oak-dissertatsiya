Project: OAK AI - CIS Academic Intelligence Platform

System status: The platform has evolved past v0.1. Current state is v0.2+ —
a full academic intelligence platform, not just a dissertation table viewer.

Tech stack:
- Flask (blueprint-based)
- PostgreSQL (migrating to, via psycopg2) + SQLite (legacy users.db)
- pandas
- Jinja2 + Bootstrap
- Groq AI (topic analysis)
- Gunicorn + nginx + systemd (Google Cloud VPS / GCE deploy)

Current features (v0.2+):
- User authentication (register, login, logout) + Telegram / Google OAuth
- User Cabinets — researcher self-registration and profiles (`cabinet.py`)
- CSV upload and validation for dissertations
- Paginated, filterable dissertation views + CSV export
- Advanced Analytics: Groq AI topic analysis, heatmaps, clustering, comparison, trends
- Academic Lineage Trees — supervisor/student genealogy graph (`static/js/genealogy.js`)
- Collaboration graph
- Content: blog, news (yangiliklar), vacancies, courses, journals, university profiles, top scholars
- Grants module: filterable listing, tracking, admin CRUD (`blueprints/grants.py`)
- Smart Reminders (Smart Eslatmalar): deadline-aware alerts over site + Telegram,
  targeted by degree/region/ixtisoslik, daily cron via GitHub Actions
  (`blueprints/reminders.py`, `.github/workflows/reminders.yml` — needs the
  REMINDERS_API_KEY secret in server .env + GitHub)
- Notification preferences: per-user ON/OFF toggles (notification_prefs table,
  key-value; API in `blueprints/notifications.py`, UI in cabinet)
- Himoya auto-match: new OAK imports notify scholars with matching ixtisoslik
  (site + Telegram, 3/day cap) — hook in `data.py` import_oak
- Advisor pages: /rahbar-topish directory + /rahbar/<slug> profiles
  (`blueprints/advisors.py`, 10-min cached aggregation over dissertations;
  invite CTA bridges to Konstruktor `?invite=` prefill)
- Ixtisoslik obunasi: specialty_subscriptions (max 5/user, site+Telegram
  toggles) — new-defense alerts on OAK import (`blueprints/subscriptions.py`,
  second hook in import_oak; chat id in users.telegram_chat_id, set at
  Telegram login, or derived from `<id>@telegram.uz` e-mails)
- Public /reminders page (filter tabs) + upcoming-deadlines widget
  (`templates/_reminders_widget.html` on home, dashboard, cabinet)
- Admin Dashboard: user blocking, broadcasts, surveys, full content management
- PWA support: manifest.json, service worker (sw.js), offline mode
- SEO: sitemap.xml, robots.txt, OG images
- CSRF protection and secret management via `.env`

File structure:
- `app.py` — Bootstrap, shared utilities, blueprint registration, and main routes
- `auth.py` — Authentication (login, register, logout, OAuth)
- `cabinet.py` — User cabinet / researcher profiles (olim_profiles is the home
  of scholar attributes: degree, region, ixtisoslik — never duplicate onto users)
- `data.py` — CSV loading, caching, filtering, data endpoints, OAK import API
- `analytics.py` — Analytics API endpoints
- `upload.py` — Upload endpoints and validation
- `blueprints/` — admin.py, content.py, notifications.py (alerts + prefs),
  grants.py, reminders.py (smart reminders + dispatch), advisors.py (rahbar
  pages), subscriptions.py (ixtisoslik obunasi + dispatch)
- `templates/` — Jinja2 templates (50+: home, dashboard, stats, genealogy,
  clustering, heatmap, blog, admin_*, cabinet, reminders, etc.)
- `static/` — js (genealogy, heatmap, collaboration), PWA assets, uploads;
  brand mark: `static/images/logo-mark.png` (white, for dark/gradient bg),
  `logo.png` (2480px white master), gradient-tiled favicon/icon-192/512
- `data/dissertatsiyalar.csv` — Source data CSV
- `users.db` — legacy SQLite user DB (untracked from git; PII — never commit)
- `deploy/` — GCE native deploy (Gunicorn + systemd + nginx + certbot)
- `Dockerfile`, `nginx.conf` — containerized deploy
- `requirements.txt` — Python dependencies

Rules / Conventions
- CRITICAL: `app.py` is currently a 212KB monolith with ~79 routes. Never read
  or rewrite the whole `app.py` file unless explicitly ordered. Focus only on
  the modular blueprints (auth, cabinet, data, analytics, upload).
- UI text stays in Uzbek.
- All secrets must be stored in `.env` and never committed.
- Never commit virtual environments (venv/, .venv/, wsl_venv/), get-pip.py,
  or any *.db file (users.db holds PII).
- Line endings: the repo is normalized to LF via .gitattributes — editors on
  Windows (\\wsl$ paths) must not reintroduce CRLF.
- Commit after each task.

Contact: repo maintainer


## Future roadmap
- Complete PostgreSQL migration for reliable storage and proper indexing.
- Add semantic search with embeddings and vector DB (e.g., FAISS or Pinecone).
- Implement RAG chatbot for guided literature queries.
- Expand opponent/advisor graph for supervisors and examiners.
- Add multi-language UI and content (UZ/EN/RU) with i18n support.
