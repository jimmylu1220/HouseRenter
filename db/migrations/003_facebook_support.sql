-- 003: Support free-form posts (Facebook rental groups, etc.)
--
-- 591/HouseFun listings are highly structured: short title, single cover image.
-- Facebook rental group posts are the opposite: a long body of free text and
-- multiple photos. Two new nullable columns capture that shape without
-- disturbing existing structured scrapers.
--
--   description : full post body (long text)
--   images      : JSONB array of image URLs (or {url, file_id} objects after
--                 the first Telegram delivery, see bot/sender.py)

ALTER TABLE listings ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS images      JSONB;
