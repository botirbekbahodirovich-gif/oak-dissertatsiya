Project: OAK AI - CIS Academic Intelligence Platform

Tech stack:
- Flask
- SQLite
- pandas
- Jinja2
- Bootstrap

Current features:
- User authentication (register, login, logout)
- CSV upload and validation for dissertations
- Paginated table view of dissertations with filters (search, degree, institution, specialization)
- Export filtered CSV
- Analytics endpoints (stats, charts) for counts/trends
- CSRF protection and secret management via `.env`

File structure:
- `app.py` — Application bootstrap, shared utilities, blueprint registration, and main routes
- `auth.py` — Authentication routes (login, register, logout)
- `data.py` — CSV loading, caching, filtering, and data endpoints
- `analytics.py` — Analytics API endpoints
- `upload.py` — Upload endpoints and validation
- `templates/` — Jinja2 templates: `index.html`, `login.html`, `register.html`, `stats.html`, `upload.html`, `dissertation.html`
- `data/dissertatsiyalar.csv` — Source data CSV
- `users.db` — SQLite user database
- `requirements.txt` — Python dependencies

Rules / Conventions
- We are working on v0.1 only. Do not add features from later versions.
- UI text stays in Uzbek.
- All secrets must be stored in `.env` and never committed.
- Commit after each task.

Contact: repo maintainer


## Future roadmap
- Migrate to PostgreSQL for reliable storage and proper indexing.
- Add semantic search with embeddings and vector DB (e.g., FAISS or Pinecone).
- Implement RAG chatbot for guided literature queries.
- Build opponent/advisor graph for supervisors and examiners.
- Add multi-language UI and content (UZ/EN/ RU) with i18n support.
