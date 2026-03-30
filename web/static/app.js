/* ── State ────────────────────────────────────────────────── */
let chatId = null;
let currentPage = "listings"; // listings | wishlist | blacklist
let listingsPage = 1;
let totalPages = 1;
let showUnavailable = false;
let selectedDistricts = new Set();
let currentSort = "newest";
const PER_PAGE = 20;

/* ── Init ─────────────────────────────────────────────────── */
document.addEventListener("DOMContentLoaded", () => {
  chatId = localStorage.getItem("chat_id");
  if (chatId) {
    activateApp();
  } else {
    showModal();
  }

  document
    .getElementById("modal-confirm")
    .addEventListener("click", onModalConfirm);
  document.getElementById("modal-chatid").addEventListener("keydown", (e) => {
    if (e.key === "Enter") onModalConfirm();
  });

  document
    .getElementById("show-unavailable")
    .addEventListener("change", (e) => {
      showUnavailable = e.target.checked;
      listingsPage = 1;
      if (currentPage === "listings") loadListings();
    });

  document.querySelectorAll(".sort-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document
        .querySelectorAll(".sort-btn")
        .forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentSort = btn.dataset.sort;
      listingsPage = 1;
      loadListings();
    });
  });

  document.querySelectorAll(".nav-tab").forEach((tab) => {
    tab.addEventListener("click", () => navigateTo(tab.dataset.page));
  });

  document.getElementById("chat-id-badge").addEventListener("click", () => {
    if (confirm("切換帳號？這會清除目前的 Chat ID。")) {
      localStorage.removeItem("chat_id");
      location.reload();
    }
  });
});

/* ── Page Navigation ──────────────────────────────────────── */
function navigateTo(page) {
  currentPage = page;
  document
    .querySelectorAll(".nav-tab")
    .forEach((t) => t.classList.toggle("active", t.dataset.page === page));
  document
    .querySelectorAll(".page")
    .forEach((p) => p.classList.toggle("active", p.id === `page-${page}`));

  if (page === "listings") loadListings();
  else if (page === "wishlist") loadWishlist();
  else if (page === "blacklist") loadBlacklist();
}

/* ── Auth ─────────────────────────────────────────────────── */
function showModal() {
  document.getElementById("modal-overlay").classList.remove("hidden");
}

function onModalConfirm() {
  const val = document.getElementById("modal-chatid").value.trim();
  if (!val || isNaN(Number(val))) return;
  localStorage.setItem("chat_id", val);
  chatId = val;
  document.getElementById("modal-overlay").classList.add("hidden");
  activateApp();
}

function activateApp() {
  document.getElementById("modal-overlay").classList.add("hidden");
  document.getElementById("chat-id-badge").textContent = `ID: ${chatId}`;
  loadDistricts();
  loadListings();
}

/* ── Districts ────────────────────────────────────────────── */
async function loadDistricts() {
  try {
    const res = await fetch("/api/districts");
    const districts = await res.json();
    const container = document.getElementById("district-chips");
    container.innerHTML = districts
      .map(
        (d) => `<span class="chip" data-district="${esc(d)}">${esc(d)}</span>`,
      )
      .join("");
    container.querySelectorAll(".chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        const d = chip.dataset.district;
        if (selectedDistricts.has(d)) {
          selectedDistricts.delete(d);
          chip.classList.remove("active");
        } else {
          selectedDistricts.add(d);
          chip.classList.add("active");
        }
        listingsPage = 1;
        loadListings();
      });
    });
  } catch (e) {
    console.warn("無法載入地區清單", e);
  }
}

/* ── Listings Page ────────────────────────────────────────── */
async function loadListings() {
  setGrid("listings-grid", "loading");
  try {
    const params = new URLSearchParams({
      chat_id: chatId,
      page: listingsPage,
      per_page: PER_PAGE,
      available_only: showUnavailable ? "false" : "true",
      sort: currentSort,
    });
    selectedDistricts.forEach((d) => params.append("district", d));

    const res = await fetch(`/api/listings?${params}`);
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    totalPages = data.total_pages;

    document.getElementById("total-count").textContent =
      `共 ${data.total.toLocaleString()} 筆房源`;
    const grid = document.getElementById("listings-grid");
    if (!data.items.length) {
      grid.innerHTML = `<div class="state-msg">📭 沒有符合條件的房源</div>`;
    } else {
      grid.innerHTML = data.items
        .map((item) => buildCard(item, "listings"))
        .join("");
      bindCardActions(grid);
    }
    renderPagination();
  } catch (err) {
    setGrid("listings-grid", "error", err.message);
  }
}

/* ── 修改後的 Wishlist 載入邏輯 ── */
async function loadWishlist() {
  setGrid("wishlist-grid", "loading");
  try {
    const res = await fetch(`/api/wishlist/listings?chat_id=${chatId}`);
    if (!res.ok) throw new Error(await res.text());

    const rawData = await res.json();
    // 關鍵修改：確保 items 是一個陣列 (支援直接回傳陣列或 { items: [...] } 格式)
    const items = Array.isArray(rawData) ? rawData : rawData.items || [];

    const countEl = document.getElementById("wishlist-count");
    if (countEl) countEl.textContent = items.length ? `${items.length} 筆` : "";
    const grid = document.getElementById("wishlist-grid");
    if (!items.length) {
      grid.innerHTML = `<div class="state-msg">📭 願望清單是空的</div>`;
    } else {
      grid.innerHTML = items
        .map((item) => buildCard(item, "wishlist"))
        .join("");
      bindCardActions(grid);
    }
  } catch (err) {
    setGrid("wishlist-grid", "error", err.message);
  }
}

/* ── 修改後的 Blacklist 載入邏輯 ── */
async function loadBlacklist() {
  const list = document.getElementById("blacklist-list");
  list.innerHTML = `<div class="state-msg"><div class="spinner"></div><br>載入中…</div>`;
  try {
    const res = await fetch(`/api/blacklist/listings?chat_id=${chatId}`);
    if (!res.ok) throw new Error(await res.text());

    const rawData = await res.json();
    // 關鍵修改：同上，確保 items 是陣列
    const items = Array.isArray(rawData) ? rawData : rawData.items || [];

    const blCountEl = document.getElementById("blacklist-count");
    if (blCountEl)
      blCountEl.textContent = items.length ? `${items.length} 筆` : "";
    if (!items.length) {
      list.innerHTML = `<div class="state-msg">📭 黑名單是空的</div>`;
      return;
    }
    list.innerHTML = items.map((item) => buildBlacklistRow(item)).join("");
    list.querySelectorAll(".btn-unblock").forEach((btn) => {
      btn.addEventListener("click", () =>
        onUnblock(Number(btn.dataset.id), btn),
      );
    });
  } catch (err) {
    list.innerHTML = `<div class="state-msg">❌ 載入失敗：${err.message}</div>`;
  }
}

/* ── Card Builder ─────────────────────────────────────────── */
function buildCard(item, context) {
  const thumbHtml = item.thumbnail_url
    ? `<img class="card-thumb" src="${esc(item.thumbnail_url)}" alt="" loading="lazy">`
    : `<div class="card-thumb-placeholder">🏠</div>`;

  const unavailBadge = !item.is_available
    ? `<div class="unavailable-badge">已下架</div>`
    : "";
  const rent = item.rent ? `${item.rent.toLocaleString()} 元/月` : "租金洽詢";
  const metaParts = [
    item.district || item.area,
    item.layout,
    item.size_ping ? `${item.size_ping} 坪` : null,
    item.floor,
  ].filter(Boolean);
  const tags = [];
  if (item.pets) tags.push(`<span class="tag tag-pets">🐾 寵物</span>`);
  if (item.parking) tags.push(`<span class="tag tag-parking">🚗 停車</span>`);
  if (item.elevator) tags.push(`<span class="tag tag-elevator">🛗 電梯</span>`);

  const dateStr = (item.wishlisted_at || item.first_seen_at || "").slice(0, 10);

  // On wishlist page, show 💔 remove; on listings page show ❤️/💔 toggle
  let wlBtn = "";
  if (context === "wishlist") {
    wlBtn = `<button class="btn-action btn-wishlist active" data-id="${item.id}">💔 移除</button>`;
  } else {
    const wlClass = item.is_wishlisted ? "btn-wishlist active" : "btn-wishlist";
    const wlLabel = item.is_wishlisted ? "💔 移除" : "❤️ 收藏";
    wlBtn = `<button class="btn-action ${wlClass}" data-id="${item.id}">${wlLabel}</button>`;
  }

  const blBtn =
    context === "wishlist"
      ? ""
      : `<button class="btn-action btn-blacklist${item.is_blacklisted ? " active" : ""}" data-id="${item.id}">${item.is_blacklisted ? "🔓 解鎖" : "🚫 封鎖"}</button>`;

  return `
<div class="card${item.is_available ? "" : " unavailable"}" data-id="${item.id}">
  ${thumbHtml}
  ${unavailBadge}
  <div class="card-body">
    <div class="card-title">
      <a href="${esc(item.url || "#")}" target="_blank" rel="noopener">${esc(item.title || "（無標題）")}</a>
    </div>
    <div class="card-rent">${rent}</div>
    ${metaParts.length ? `<div class="card-meta">${metaParts.map((p) => `<span>${esc(String(p))}</span>`).join("")}</div>` : ""}
    ${tags.length ? `<div class="card-tags">${tags.join("")}</div>` : ""}
    ${dateStr ? `<div class="card-date">🕐 ${dateStr}</div>` : ""}
  </div>
  <div class="card-actions">
    ${wlBtn}
    ${blBtn}
  </div>
</div>`;
}

/* ── Blacklist Row Builder ────────────────────────────────── */
function buildBlacklistRow(item) {
  const thumbHtml = item.thumbnail_url
    ? `<img class="blacklist-thumb" src="${esc(item.thumbnail_url)}" alt="" loading="lazy">`
    : `<div class="blacklist-thumb-placeholder">🏠</div>`;

  const rent = item.rent ? `${item.rent.toLocaleString()} 元/月` : "";
  const meta = [
    item.district || item.area,
    item.layout,
    rent,
    (item.blacklisted_at || "").slice(0, 10),
  ]
    .filter(Boolean)
    .join("  ·  ");

  return `
<div class="blacklist-row" data-id="${item.id || ""}">
  ${thumbHtml}
  <div class="blacklist-info">
    <div class="blacklist-title">
      <a href="${esc(item.url || "#")}" target="_blank" rel="noopener">${esc(item.title || "（已從資料庫移除）")}</a>
    </div>
    <div class="blacklist-meta">${esc(meta)}</div>
  </div>
  <button class="btn-unblock" data-id="${item.id || ""}">🔓 解除封鎖</button>
</div>`;
}

/* ── Actions ──────────────────────────────────────────────── */
function bindCardActions(container) {
  container.querySelectorAll(".btn-wishlist").forEach((btn) => {
    btn.addEventListener("click", () =>
      onWishlistToggle(Number(btn.dataset.id), btn),
    );
  });
  container.querySelectorAll(".btn-blacklist").forEach((btn) => {
    btn.addEventListener("click", () =>
      onBlacklistToggle(Number(btn.dataset.id), btn),
    );
  });
}

async function onWishlistToggle(listingId, btn) {
  const adding = !btn.classList.contains("active");
  btn.disabled = true;
  try {
    const method = adding ? "POST" : "DELETE";
    const res = await fetch("/api/wishlist", {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: Number(chatId), listing_id: listingId }),
    });
    if (!res.ok) throw new Error();
    btn.classList.toggle("active", adding);
    btn.textContent = adding ? "💔 移除" : "❤️ 收藏";
    showToast(adding ? "已加入願望清單 ❤️" : "已移出願望清單");

    // If on wishlist page, remove the card
    if (currentPage === "wishlist" && !adding) {
      const card = btn.closest(".card");
      card.style.transition = "opacity .25s, transform .25s";
      card.style.opacity = "0";
      card.style.transform = "scale(.95)";
      setTimeout(() => {
        card.remove();
        updateWishlistCount();
      }, 260);
    }
  } catch {
    showToast("操作失敗，請稍後再試");
  } finally {
    btn.disabled = false;
  }
}

async function onBlacklistToggle(listingId, btn) {
  const adding = !btn.classList.contains("active");
  if (adding && !confirm("封鎖後此房源不會再出現在 Bot 推播中，確定嗎？"))
    return;
  btn.disabled = true;
  try {
    const method = adding ? "POST" : "DELETE";
    const res = await fetch("/api/blacklist", {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: Number(chatId), listing_id: listingId }),
    });
    if (!res.ok) throw new Error();
    btn.classList.toggle("active", adding);
    btn.textContent = adding ? "🔓 解鎖" : "🚫 封鎖";
    showToast(adding ? "已加入黑名單 🚫" : "已解除封鎖");
  } catch {
    showToast("操作失敗，請稍後再試");
  } finally {
    btn.disabled = false;
  }
}

async function onUnblock(listingId, btn) {
  if (!listingId) return;
  btn.disabled = true;
  try {
    const res = await fetch("/api/blacklist", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: Number(chatId), listing_id: listingId }),
    });
    if (!res.ok) throw new Error();
    const row = btn.closest(".blacklist-row");
    row.style.transition = "opacity .25s";
    row.style.opacity = "0";
    setTimeout(() => {
      row.remove();
      updateBlacklistCount();
    }, 260);
    showToast("已解除封鎖 🔓");
  } catch {
    showToast("操作失敗，請稍後再試");
    btn.disabled = false;
  }
}

function updateWishlistCount() {
  const n = document.querySelectorAll("#wishlist-grid .card").length;
  const el = document.getElementById("wishlist-count");
  if (el) el.textContent = n ? `${n} 筆` : "";
}

function updateBlacklistCount() {
  const n = document.querySelectorAll("#blacklist-list .blacklist-row").length;
  const el = document.getElementById("blacklist-count");
  if (el) el.textContent = n ? `${n} 筆` : "";
}

/* ── Pagination ───────────────────────────────────────────── */
function renderPagination() {
  const html = buildPaginationHTML();
  document.getElementById("pagination-top").innerHTML = html;
  document.getElementById("pagination-bottom").innerHTML = html;
  document.querySelectorAll(".page-btn[data-page]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const p = Number(btn.dataset.page);
      if (p === listingsPage) return;
      listingsPage = p;
      window.scrollTo({ top: 0, behavior: "smooth" });
      loadListings();
    });
  });
}

function buildPaginationHTML() {
  if (totalPages <= 1) return "";
  const pages = getPagesRange(listingsPage, totalPages);
  return pages
    .map((p) => {
      if (p === "…") return `<span class="page-btn" disabled>…</span>`;
      return `<button class="page-btn${p === listingsPage ? " active" : ""}" data-page="${p}">${p}</button>`;
    })
    .join("");
}

function getPagesRange(cur, total) {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const pages = new Set(
    [1, total, cur, cur - 1, cur + 1].filter((p) => p >= 1 && p <= total),
  );
  const sorted = Array.from(pages).sort((a, b) => a - b);
  const result = [];
  let prev = 0;
  for (const p of sorted) {
    if (p - prev > 1) result.push("…");
    result.push(p);
    prev = p;
  }
  return result;
}

/* ── Helpers ──────────────────────────────────────────────── */
function setGrid(id, state, msg) {
  const el = document.getElementById(id);
  if (state === "loading") {
    el.innerHTML = `<div class="state-msg"><div class="spinner"></div><br>載入中…</div>`;
  } else if (state === "error") {
    el.innerHTML = `<div class="state-msg">❌ 載入失敗：${msg}</div>`;
  }
}

let _toastTimer = null;
function showToast(msg) {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.remove("show"), 2200);
}

function esc(str) {
  if (str == null) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
