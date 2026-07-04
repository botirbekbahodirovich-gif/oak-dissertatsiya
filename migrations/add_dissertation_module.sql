-- Dissertation workspace module (v1) — advisor↔student writing/review loop.
--
-- MUHIM NOM O'ZGARISHI: spetsifikatsiyadagi "dissertations" jadvali bu bazada
-- ALLAQACHON band (27k+ OAK himoya yozuvlari — platformaning asosiy korpusi,
-- scraper har kuni yozadi). Shu sababli yangi loyihalar jadvali `diss_projects`
-- deb nomlanadi; qolgan jadvallar spetsifikatsiyadagi nomlarda.
--
-- Idempotent — qayta ishga tushirish xavfsiz. Xuddi shu DDL blueprint'ning
-- lazy _ensure_schema'sida ham bor: server birinchi so'rovda o'zi migratsiya
-- qiladi. Qo'lda:  psql "$DATABASE_URL" -f migrations/add_dissertation_module.sql

ALTER TABLE users ADD COLUMN IF NOT EXISTS is_premium BOOLEAN DEFAULT FALSE;

-- 1. Advisor ↔ student linking
CREATE TABLE IF NOT EXISTS advisor_links (
    id SERIAL PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    advisor_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'accepted', 'declined', 'removed')),
    invited_by INTEGER REFERENCES users(id),
    invite_message TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    responded_at TIMESTAMP,
    UNIQUE(student_id, advisor_id),
    CHECK (student_id <> advisor_id)
);
CREATE INDEX IF NOT EXISTS idx_advisor_links_student ON advisor_links(student_id) WHERE status = 'accepted';
CREATE INDEX IF NOT EXISTS idx_advisor_links_advisor ON advisor_links(advisor_id) WHERE status = 'accepted';

-- 2. Dissertation projects (NOT the OAK `dissertations` corpus!)
CREATE TABLE IF NOT EXISTS diss_projects (
    id SERIAL PRIMARY KEY,
    owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(600) NOT NULL,
    degree_type VARCHAR(30) DEFAULT 'phd'
        CHECK (degree_type IN ('magistr', 'phd', 'dsc')),
    specialty_code VARCHAR(30),
    language VARCHAR(10) DEFAULT 'uz',
    status VARCHAR(30) DEFAULT 'draft'
        CHECK (status IN ('draft', 'in_review', 'revision', 'approved', 'archived')),
    advisor_id INTEGER REFERENCES users(id),
    last_submitted_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_diss_projects_owner ON diss_projects(owner_id);
CREATE INDEX IF NOT EXISTS idx_diss_projects_advisor ON diss_projects(advisor_id);

-- 3. Hierarchical blocks (TOC nodes + content)
CREATE TABLE IF NOT EXISTS dissertation_blocks (
    id SERIAL PRIMARY KEY,
    dissertation_id INTEGER NOT NULL REFERENCES diss_projects(id) ON DELETE CASCADE,
    parent_id INTEGER REFERENCES dissertation_blocks(id) ON DELETE CASCADE,
    title VARCHAR(500) NOT NULL,
    numbering VARCHAR(50),
    sort_order INTEGER NOT NULL DEFAULT 0,
    depth INTEGER NOT NULL DEFAULT 0,
    content TEXT DEFAULT '',
    content_plain TEXT DEFAULT '',
    word_count INTEGER DEFAULT 0,
    review_status VARCHAR(30) DEFAULT 'not_reviewed'
        CHECK (review_status IN ('not_reviewed', 'deficiencies', 'task_assigned', 'approved')),
    review_status_by INTEGER REFERENCES users(id),
    review_status_at TIMESTAMP,
    is_locked_by INTEGER REFERENCES users(id),
    locked_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    CHECK (depth >= 0 AND depth <= 3)
);
CREATE INDEX IF NOT EXISTS idx_blocks_dissertation ON dissertation_blocks(dissertation_id, parent_id, sort_order);

-- 4. Version history (last 20 per block, pruned in Python on save)
CREATE TABLE IF NOT EXISTS block_versions (
    id SERIAL PRIMARY KEY,
    block_id INTEGER NOT NULL REFERENCES dissertation_blocks(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    word_count INTEGER DEFAULT 0,
    saved_by INTEGER REFERENCES users(id),
    save_type VARCHAR(20) DEFAULT 'manual'
        CHECK (save_type IN ('manual', 'autosave', 'pre_restore')),
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_block_versions_block ON block_versions(block_id, created_at DESC);

-- 5. Text-anchored annotations (sticky notes)
CREATE TABLE IF NOT EXISTS block_annotations (
    id SERIAL PRIMARY KEY,
    block_id INTEGER NOT NULL REFERENCES dissertation_blocks(id) ON DELETE CASCADE,
    author_id INTEGER NOT NULL REFERENCES users(id),
    annotation_type VARCHAR(20) DEFAULT 'comment'
        CHECK (annotation_type IN ('comment', 'correction', 'task')),
    anchor_text TEXT NOT NULL,
    anchor_prefix VARCHAR(100),
    anchor_suffix VARCHAR(100),
    anchor_offset INTEGER,
    body TEXT NOT NULL,
    status VARCHAR(20) DEFAULT 'open'
        CHECK (status IN ('open', 'resolved', 'orphaned')),
    resolved_by INTEGER REFERENCES users(id),
    resolved_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_annotations_block ON block_annotations(block_id, status);

CREATE TABLE IF NOT EXISTS annotation_replies (
    id SERIAL PRIMARY KEY,
    annotation_id INTEGER NOT NULL REFERENCES block_annotations(id) ON DELETE CASCADE,
    author_id INTEGER NOT NULL REFERENCES users(id),
    body TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_annotation_replies ON annotation_replies(annotation_id);

-- 6. Universal messaging
CREATE TABLE IF NOT EXISTS conversations (
    id SERIAL PRIMARY KEY,
    conversation_type VARCHAR(20) DEFAULT 'direct'
        CHECK (conversation_type IN ('direct', 'dissertation')),
    dissertation_id INTEGER REFERENCES diss_projects(id) ON DELETE SET NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    last_message_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS conversation_participants (
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    last_read_at TIMESTAMP DEFAULT NOW(),
    is_muted BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (conversation_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_conv_participants_user ON conversation_participants(user_id);

CREATE TABLE IF NOT EXISTS messages (
    id SERIAL PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    sender_id INTEGER NOT NULL REFERENCES users(id),
    body TEXT,
    attachment_url VARCHAR(600),
    attachment_name VARCHAR(300),
    attachment_type VARCHAR(50),
    attachment_size INTEGER,
    is_deleted BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW(),
    CHECK (body IS NOT NULL OR attachment_url IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, created_at DESC);

-- 7. Module notifications (event-driven, workspace-scoped)
CREATE TABLE IF NOT EXISTS diss_notifications (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_type VARCHAR(40) NOT NULL,
    dissertation_id INTEGER REFERENCES diss_projects(id) ON DELETE CASCADE,
    block_id INTEGER REFERENCES dissertation_blocks(id) ON DELETE CASCADE,
    actor_id INTEGER REFERENCES users(id),
    payload JSONB DEFAULT '{}',
    is_read BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_diss_notif_user ON diss_notifications(user_id, is_read, created_at DESC);

-- 8. AI copilot placeholder (v2 uchun tayyor arxitektura — v1 da ishlatilmaydi)
CREATE TABLE IF NOT EXISTS ai_review_requests (
    id SERIAL PRIMARY KEY,
    block_id INTEGER REFERENCES dissertation_blocks(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id),
    request_type VARCHAR(30),
    status VARCHAR(20) DEFAULT 'pending',
    result JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);
