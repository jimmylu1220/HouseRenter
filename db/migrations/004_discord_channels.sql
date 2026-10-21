-- 004: Discord channels as subscribers with their own filters.
--
-- Each channel is configured with slash commands (/subscribe, /filters) and
-- matched with the same rules as Telegram user_preferences.
--
--   active         : /subscribe sets TRUE, /unsubscribe sets FALSE (filters kept)
--   first_run_done : FALSE after subscribing or changing filters; the next scan
--                    records a baseline instead of pushing every old listing.

CREATE TABLE IF NOT EXISTS discord_channels (
    channel_id         BIGINT PRIMARY KEY,
    guild_id           BIGINT,
    active             BOOLEAN     NOT NULL DEFAULT FALSE,
    first_run_done     BOOLEAN     NOT NULL DEFAULT FALSE,
    min_rent           INTEGER,
    max_rent           INTEGER,
    districts          JSONB,
    min_size           REAL,
    layout_filter      JSONB,
    pets_required      BOOLEAN,
    parking_required   BOOLEAN,
    elevator_required  BOOLEAN,
    keywords_include   JSONB,
    keywords_exclude   JSONB,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
