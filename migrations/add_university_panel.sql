-- Universitet B2B paneli (Part 1) — kirish modeli.
-- Kod lazy _ensure_schema (blueprints/univer.py) orqali ham xuddi shu sxemani
-- yaratadi; bu fayl hujjat + qo'lda migratsiya uchun (psql -f).

-- Universitet xodimlari (kimlar workspace'ga kira oladi)
CREATE TABLE IF NOT EXISTS university_staff (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    canonical_institution VARCHAR(500) NOT NULL,  -- institution_map.canonical_name
    role VARCHAR(30) NOT NULL DEFAULT 'staff'
        CHECK (role IN ('owner', 'staff', 'viewer')),
    -- owner: xodimlar ro'yxatini boshqaradi; staff: to'liq monitoring;
    -- viewer: faqat o'qish
    title VARCHAR(200),                -- lavozimi: "Ilmiy bo'lim boshlig'i"
    invited_by INTEGER REFERENCES users(id),
    status VARCHAR(20) DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, canonical_institution)
);
CREATE INDEX IF NOT EXISTS idx_university_staff_user
    ON university_staff(user_id) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_university_staff_inst
    ON university_staff(canonical_institution);

-- Litsenziyalar (bitta universitet — bitta litsenziya)
CREATE TABLE IF NOT EXISTS university_licenses (
    id SERIAL PRIMARY KEY,
    canonical_institution VARCHAR(500) UNIQUE NOT NULL,
    plan VARCHAR(20) DEFAULT 'pilot' CHECK (plan IN ('pilot', 'standard', 'premium')),
    valid_until DATE,
    max_staff INTEGER DEFAULT 5,
    created_at TIMESTAMP DEFAULT NOW(),
    notes TEXT
);

-- Xodim taklif havolalari (advisor_invite_tokens naqshi, 7 kun amal qiladi)
CREATE TABLE IF NOT EXISTS university_invite_tokens (
    id SERIAL PRIMARY KEY,
    token VARCHAR(64) UNIQUE NOT NULL,
    license_id INTEGER NOT NULL REFERENCES university_licenses(id) ON DELETE CASCADE,
    created_by INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TIMESTAMP DEFAULT (NOW() + INTERVAL '7 days'),
    used_by INTEGER REFERENCES users(id),
    used_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_university_invite_token
    ON university_invite_tokens(token);

-- Maxfiylik: doktorant universitetiga ko'rinishni o'chirib qo'yishi mumkin
ALTER TABLE users ADD COLUMN IF NOT EXISTS
    hide_from_university BOOLEAN DEFAULT FALSE;
