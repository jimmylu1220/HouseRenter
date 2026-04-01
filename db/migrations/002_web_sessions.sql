-- 002: Web session tokens for frontend OTP login
CREATE TABLE IF NOT EXISTS web_sessions (
    token       TEXT PRIMARY KEY,
    chat_id     BIGINT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_web_sessions_chat ON web_sessions(chat_id);
