-- Universitet B2B paneli (Part 2) — boshqaruv, hisobotlar, engagement.
-- Kod lazy _ensure_schema (blueprints/univer.py + conferences.py) orqali ham
-- xuddi shu sxemani yaratadi; bu fayl hujjat + qo'lda migratsiya uchun.

-- Universitet tomonidan yuborilgan konferensiyalar (moderatsiya oqimi).
-- MUHIM: pending yozuvlar is_active=FALSE bilan yaratiladi — barcha ommaviy
-- so'rovlar (is_active filtri) ularni avtomatik chiqarib tashlaydi.
ALTER TABLE conferences ADD COLUMN IF NOT EXISTS canonical_institution VARCHAR(500);
ALTER TABLE conferences ADD COLUMN IF NOT EXISTS submitted_by INTEGER REFERENCES users(id);
ALTER TABLE conferences ADD COLUMN IF NOT EXISTS moderation_status VARCHAR(20) DEFAULT 'approved';
CREATE INDEX IF NOT EXISTS idx_conf_canonical ON conferences(canonical_institution)
    WHERE canonical_institution IS NOT NULL;

-- Jurnallar: universitetga bog'lash + so'rov oqimi (xuddi shu naqsh)
ALTER TABLE journals ADD COLUMN IF NOT EXISTS canonical_institution VARCHAR(500);
ALTER TABLE journals ADD COLUMN IF NOT EXISTS submitted_by INTEGER REFERENCES users(id);
ALTER TABLE journals ADD COLUMN IF NOT EXISTS moderation_status VARCHAR(20) DEFAULT 'approved';

-- Taklif tokenlari kengaytmasi: doktorant takliflari (role) + email logi
ALTER TABLE university_invite_tokens ADD COLUMN IF NOT EXISTS role VARCHAR(30) DEFAULT 'staff';
ALTER TABLE university_invite_tokens ADD COLUMN IF NOT EXISTS email VARCHAR(255);

-- Doktorant ↔ universitet to'g'ridan-to'g'ri bog'lash (taklifni qabul qilganda;
-- olim_profiles.institution zanjiri bo'lmagan foydalanuvchilar uchun ham ishlaydi)
CREATE TABLE IF NOT EXISTS university_doctorant_links (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    canonical_institution VARCHAR(500) NOT NULL,
    invited_by INTEGER REFERENCES users(id),
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, canonical_institution)
);

-- Kunlik xodim digest'i: xodim darajasida ON/OFF + kunlik dedup logi
ALTER TABLE university_staff ADD COLUMN IF NOT EXISTS digest_enabled BOOLEAN DEFAULT TRUE;
CREATE TABLE IF NOT EXISTS university_digest_log (
    id SERIAL PRIMARY KEY,
    canonical_institution VARCHAR(500) NOT NULL,
    digest_date DATE NOT NULL DEFAULT CURRENT_DATE,
    events_count INTEGER DEFAULT 0,
    sent_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(canonical_institution, digest_date)
);

-- Ommaviy /university/<name> sahifasi uchun litsenziyali universitet o'zi
-- tahrirlaydigan maydonlar (canonical nom bilan kalitlangan)
CREATE TABLE IF NOT EXISTS university_public_profiles (
    id SERIAL PRIMARY KEY,
    canonical_institution VARCHAR(500) UNIQUE NOT NULL,
    description TEXT,
    website VARCHAR(500),
    logo_url VARCHAR(500),
    contact_email VARCHAR(255),
    updated_by INTEGER REFERENCES users(id),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Oddiy audit jurnali — Part 1-2 dagi har bir boshqaruv amali uchun bitta qator
CREATE TABLE IF NOT EXISTS university_audit_log (
    id SERIAL PRIMARY KEY,
    canonical_institution VARCHAR(500) NOT NULL,
    user_id INTEGER REFERENCES users(id),
    action VARCHAR(200) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_univ_audit ON university_audit_log(canonical_institution, created_at DESC);
