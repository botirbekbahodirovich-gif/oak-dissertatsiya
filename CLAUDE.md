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
- Admin Dashboard: user blocking, broadcasts, surveys, full content management
- PWA support: manifest.json, service worker (sw.js), offline mode
- SEO: sitemap.xml, robots.txt, OG images
- CSRF protection and secret management via `.env`

File structure:
- `app.py` — Bootstrap, shared utilities, blueprint registration, and main routes
- `auth.py` — Authentication (login, register, logout, OAuth)
- `cabinet.py` — User cabinet / researcher profiles
- `data.py` — CSV loading, caching, filtering, data endpoints
- `analytics.py` — Analytics API endpoints
- `upload.py` — Upload endpoints and validation
- `templates/` — Jinja2 templates (50+: home, dashboard, stats, genealogy,
  clustering, heatmap, blog, admin_*, cabinet, etc.)
- `static/` — js (genealogy, heatmap, collaboration), PWA assets, uploads
- `data/dissertatsiyalar.csv` — Source data CSV
- `users.db` — legacy SQLite user database
- `deploy/` — GCE native deploy (Gunicorn + systemd + nginx + certbot)
- `Dockerfile`, `nginx.conf` — containerized deploy
- `requirements.txt` — Python dependencies

Rules / Conventions
- CRITICAL: `app.py` is currently a 212KB monolith with ~79 routes. Never read
  or rewrite the whole `app.py` file unless explicitly ordered. Focus only on
  the modular blueprints (auth, cabinet, data, analytics, upload).
- UI text stays in Uzbek.
- All secrets must be stored in `.env` and never committed.
- Never commit virtual environments (venv/, .venv/, wsl_venv/) or get-pip.py.
- Commit after each task.

Contact: repo maintainer


## Future roadmap
- Complete PostgreSQL migration for reliable storage and proper indexing.
- Add semantic search with embeddings and vector DB (e.g., FAISS or Pinecone).
- Implement RAG chatbot for guided literature queries.
- Expand opponent/advisor graph for supervisors and examiners.
- Add multi-language UI and content (UZ/EN/RU) with i18n support.
