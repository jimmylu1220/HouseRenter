-- 001: Initial schema
CREATE TABLE IF NOT EXISTS listings (
    id            SERIAL PRIMARY KEY,
    source        TEXT    NOT NULL,
    external_id   TEXT    NOT NULL,
    url           TEXT    NOT NULL,
    title         TEXT,
    area          TEXT,
    district      TEXT,
    address       TEXT,
    rent          INTEGER,
    layout        TEXT,
    size_ping     REAL,
    floor         TEXT,
    floor_total   INTEGER,
    deposit       TEXT,
    utilities     TEXT,
    trash         BOOLEAN,
    kitchen       TEXT,
    parking       TEXT,
    pets          TEXT,
    elevator      BOOLEAN,
    gender        TEXT,
    extra_info    JSONB,
    thumbnail_url TEXT,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_available  BOOLEAN     NOT NULL DEFAULT TRUE,
    UNIQUE(source, external_id)
);

CREATE INDEX IF NOT EXISTS idx_listings_available ON listings(is_available, source);
CREATE INDEX IF NOT EXISTS idx_listings_rent      ON listings(rent);

CREATE TABLE IF NOT EXISTS wishlist (
    id               SERIAL PRIMARY KEY,
    chat_id          BIGINT  NOT NULL,
    listing_id       INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    added_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notified_rented  BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE(chat_id, listing_id)
);

CREATE TABLE IF NOT EXISTS user_preferences (
    chat_id            BIGINT PRIMARY KEY,
    min_rent           INTEGER,
    max_rent           INTEGER,
    regions            JSONB,
    districts          JSONB,
    min_size           REAL,
    layout_filter      JSONB,
    pets_required      BOOLEAN,
    parking_required   BOOLEAN,
    elevator_required  BOOLEAN,
    keywords_include   JSONB,
    keywords_exclude   JSONB,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS active_monitors (
    chat_id        BIGINT PRIMARY KEY,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    first_run_done BOOLEAN     NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS blacklist (
    chat_id     BIGINT NOT NULL,
    source      TEXT   NOT NULL,
    external_id TEXT   NOT NULL,
    added_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (chat_id, source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_blacklist_chat ON blacklist(chat_id);

CREATE TABLE IF NOT EXISTS pagination_sessions (
    chat_id     BIGINT PRIMARY KEY,
    listing_ids JSONB   NOT NULL,
    total       INTEGER NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
