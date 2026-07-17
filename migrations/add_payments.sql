-- To'lov moduli (ATMOS) — blueprints/payments.py _ensure_schema bilan bir xil.
-- Server birinchi so'rovda self-migrate qiladi; bu fayl hujjat + qo'lda
-- ishga tushirish uchun: psql "$DATABASE_URL" -f migrations/add_payments.sql

CREATE TABLE IF NOT EXISTS payments (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    product_code VARCHAR(40) NOT NULL,
    -- 'topic_analysis_1', 'csv_export_1', 'premium_month', 'premium_year'
    amount INTEGER NOT NULL,              -- TIYINDA (5000 UZS = 500000 tiyin)
    currency VARCHAR(3) DEFAULT 'UZS',
    status VARCHAR(20) NOT NULL DEFAULT 'created'
        CHECK (status IN ('created','pending','paid','failed','cancelled','refunded')),
    provider VARCHAR(20) DEFAULT 'atmos',
    provider_transaction_id VARCHAR(100), -- ATMOS transaction_id
    idempotency_key VARCHAR(64) UNIQUE NOT NULL,
    paid_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    meta JSONB DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id, status);
CREATE INDEX IF NOT EXISTS idx_payments_provider_tx ON payments(provider_transaction_id);

CREATE TABLE IF NOT EXISTS user_entitlements (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    entitlement VARCHAR(40) NOT NULL,
    -- 'topic_analysis_credit' | 'csv_export_credit' (kredit),
    -- 'premium' (muddatli, valid_until)
    credits_remaining INTEGER,
    valid_until TIMESTAMP,
    source_payment_id INTEGER REFERENCES payments(id),
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_entitlements_user
    ON user_entitlements(user_id, entitlement);

-- Har bir callback xom payload bilan saqlanadi (nizolarni hal qilish uchun).
CREATE TABLE IF NOT EXISTS payments_log (
    id SERIAL PRIMARY KEY,
    payment_id INTEGER,
    provider_transaction_id VARCHAR(100),
    event VARCHAR(40),
    raw TEXT,
    ip VARCHAR(64),
    created_at TIMESTAMP DEFAULT NOW()
);

ALTER TABLE users ADD COLUMN IF NOT EXISTS is_premium BOOLEAN DEFAULT FALSE;
