-- =====================================================================
-- monitored_chats — Supabase durability mirror [CHAT-PERSIST v4.5.17]
-- =====================================================================
-- مرآة Supabase لجدول monitored_chats المحلي (SQLite على القرص المؤقت
-- لـRender). المشكلة: كل نشر يمسح القرص → الجدول يُبنى من جديد → كل
-- first_seen يصير «اليوم». هذه المرآة تجعل التواريخ والبيانات دائمة:
--
--   • عند الإقلاع: البوت يسحب كل الصفوف ويعيدها للجدول المحلي
--     (INSERT OR IGNORE) مع first_seen التاريخي — قبل أي إعادة بناء.
--   • كل دقيقتين: دفع الصفوف الجديدة/المحدّثة (merge-duplicates).
--   • عمود first_seen محمي تصميمياً: التحديثات لا ترسله إطلاقاً →
--     ON CONFLICT لا يمسه، والدفع الأول لصف جديد يكتب التاريخ الصحيح.
--   • شفاء أولد-يفوز كل دورة يصحح أي first_seen محلي أحدث من الدائم.
--
-- التنفيذ: انسخ هذا الملف كاملاً والصقه في Supabase SQL Editor ثم Run
-- (مرة واحدة فقط — idempotent). لحظة إنشائه يبدأ البوت بالمرآة تلقائياً
-- بلا أي نشر جديد.
--
-- ملاحظة: PostgreSQL لا يدعم IF NOT EXISTS مع CREATE POLICY — النمط
-- الصحيح: DROP POLICY IF EXISTS ثم CREATE POLICY (نفس نمط
-- message_journal_snapshot.sql).
--
-- هذا الجدول بيانات تشغيلية للمجموعات (معرفات/عناوين/تواريخ) — لا
-- علاقة له بأمر [SUPA-MSG-KILL] الذي كان عن محتوى الرسائل.

CREATE TABLE IF NOT EXISTS monitored_chats (
    chat_id          BIGINT PRIMARY KEY,
    chat_title       TEXT,
    username         TEXT,
    link_type        TEXT,
    monitored_by     TEXT,
    member_count     INTEGER,
    ai_classification TEXT,
    ai_country       TEXT,
    ai_relevance     INTEGER,
    ai_description   TEXT,
    should_monitor   INTEGER DEFAULT 1,
    reader_phones    TEXT,
    primary_reader   TEXT,
    last_seen        TIMESTAMPTZ,
    mirrored_at      TIMESTAMPTZ DEFAULT now(),
    -- أولاً-اكتشاف: يُكتب مرة واحدة عند إنشاء الصف فقط (DEFAULT) —
    -- البوت لا يرسل هذا العمود في التحديثات فيبقى محفوظاً للأبد.
    first_seen       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_monitored_chats_first_seen
    ON monitored_chats (first_seen);

CREATE INDEX IF NOT EXISTS idx_monitored_chats_should_monitor
    ON monitored_chats (should_monitor);

-- =====================================================================
-- Row Level Security (نفس نمط message_journal_snapshot)
-- =====================================================================
ALTER TABLE monitored_chats ENABLE ROW LEVEL SECURITY;

-- service_role: وصول كامل (البوت يستخدم SERVICE_ROLE_KEY)
DROP POLICY IF EXISTS "service_role full access monitored_chats"
    ON monitored_chats;

CREATE POLICY "service_role full access monitored_chats"
    ON monitored_chats
    FOR ALL
    TO service_role
    USING (true) WITH CHECK (true);

-- anon: ممنوع (لا وصول عام لبيانات تشغيلية)
DROP POLICY IF EXISTS "anon no access monitored_chats"
    ON monitored_chats;

CREATE POLICY "anon no access monitored_chats"
    ON monitored_chats
    FOR SELECT
    TO anon
    USING (false);
