import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from pydantic import BaseModel

from db.database import init_db, close_db
import db.queries as queries


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await close_db()


app = FastAPI(title="HouseRenter Web", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


# ── Pages ──────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# ── API ────────────────────────────────────────────────────────────────────────

@app.get("/api/listings")
async def api_listings(
    chat_id: int = Query(..., description="Telegram chat ID"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    available_only: bool = Query(True),
    district: list[str] = Query(default=[]),
    sort: str = Query(default="newest"),
):
    offset = (page - 1) * per_page
    rows, total = await queries.get_listings_for_web(
        chat_id=chat_id,
        offset=offset,
        limit=per_page,
        available_only=available_only,
        districts=district or None,
        sort=sort,
    )
    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
        "items": rows,
    }


@app.get("/api/districts")
async def api_districts():
    return await queries.get_distinct_districts()


@app.get("/api/wishlist/listings")
async def api_wishlist_listings(chat_id: int = Query(...)):
    return await queries.get_wishlist_listings_for_web(chat_id)


@app.get("/api/blacklist/listings")
async def api_blacklist_listings(chat_id: int = Query(...)):
    return await queries.get_blacklist_listings_for_web(chat_id)


class WishlistBody(BaseModel):
    chat_id: int
    listing_id: int


@app.post("/api/wishlist")
async def api_wishlist_add(body: WishlistBody):
    added = await queries.add_to_wishlist(body.chat_id, body.listing_id)
    return {"added": added}


@app.delete("/api/wishlist")
async def api_wishlist_remove(body: WishlistBody):
    await queries.remove_from_wishlist(body.chat_id, body.listing_id)
    return {"removed": True}


class BlacklistBody(BaseModel):
    chat_id: int
    listing_id: int


@app.post("/api/blacklist")
async def api_blacklist_add(body: BlacklistBody):
    listing = await queries.get_listing_by_id(body.listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    added = await queries.add_to_blacklist(body.chat_id, listing.source, listing.external_id)
    return {"added": added}


@app.delete("/api/blacklist")
async def api_blacklist_remove(body: BlacklistBody):
    listing = await queries.get_listing_by_id(body.listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    await queries.remove_from_blacklist(body.chat_id, listing.source, listing.external_id)
    return {"removed": True}
