// /static/js/alerts.js
(() => {
  "use strict";

  // ============================================================
  // Helpers
  // ============================================================
  const hasFocus = () => document.visibilityState === "visible";
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const SOUND_FLAG_KEY = "soundEnabled";
  const ALERTS_FLAG_KEY = "alertsEnabled";

  try {
    if (localStorage.getItem(SOUND_FLAG_KEY) === "1") window.__soundEnabled = true;
    if (localStorage.getItem(ALERTS_FLAG_KEY) === "1") window.__alertsEnabled = true;
  } catch (_) {}

  // If storage is blocked, allow in-memory enable via window flags.
  const isAlertsEnabled = () => window.__alertsEnabled === true;
  const isSoundEnabled  = () => window.__soundEnabled === true;

  // ============================================================
  // ✅ IMPORTANT: DO NOT early-return at file load time.
  // This file can load before DOM exists.
  // ============================================================
  const ALLOWED_PAGES = new Set(["store-dashboard"]);

  const getPage = () => {
    const bp = document.body && document.body.getAttribute("data-page");
    if (bp) return bp;

    const els = document.querySelectorAll("[data-page]");
    if (!els || !els.length) return "";
    return (els[els.length - 1].getAttribute("data-page") || "");
  };

  const isAllowedNow = () => {
    const p = getPage();
    return ALLOWED_PAGES.has(p);
  };

  // ============================================================
  // Lifecycle stop flag
  // ============================================================
  let __stopAlerts = false;

  const cleanupUi = () => {
    try { document.getElementById("alerts-unlock-banner")?.remove(); } catch (_) {}
    try { document.getElementById("toast-host")?.remove(); } catch (_) {}
  };

  const stopAlerts = () => {
    __stopAlerts = true;
    window.__alertsRunning = false;
    cleanupUi();
  };

  // Stop on normal navigations
  window.addEventListener("pagehide", stopAlerts);
  window.addEventListener("beforeunload", stopAlerts);

  // ============================================================
  // ✅ Optional SPA navigation guard (safe to keep)
  // ============================================================
  const fireLocationChange = () => window.dispatchEvent(new Event("locationchange"));
  try {
    const _push = history.pushState;
    const _replace = history.replaceState;
    history.pushState = function () {
      const r = _push.apply(this, arguments);
      fireLocationChange();
      return r;
    };
    history.replaceState = function () {
      const r = _replace.apply(this, arguments);
      fireLocationChange();
      return r;
    };
  } catch (_) {}

  window.addEventListener("popstate", fireLocationChange);
  window.addEventListener("hashchange", fireLocationChange);

  window.addEventListener("locationchange", () => {
    // if we moved away, stop loop
    if (!isAllowedNow()) stopAlerts();
  });

  // ============================================================
  // ✅ Cooldown: View Orders pauses toast spam but DOES NOT stop polling
  // ============================================================
  const NOTIFY_COOLDOWN_MS = 90 * 1000;
  let suppressNotifyUntil = 0;
  const canNotifyNow = () => Date.now() >= suppressNotifyUntil;

  // ============================================================
  // Audio unlock (needs user gesture)
  // ============================================================
  let __audioUnlocked = false;
  let __pendingBeep = false;

  try {
    if (sessionStorage.getItem("audioUnlocked") === "1") __audioUnlocked = true;
  } catch (_) {}

  function markAudioUnlocked() {
    __audioUnlocked = true;
    try { sessionStorage.setItem("audioUnlocked", "1"); } catch (_) {}
  }

  function getDingEl() {
    return document.getElementById("ding") || document.getElementById("dingMobile");
  }

  // ============================================================
  // Unlock banner (ONLY when alerts are enabled, and ONLY once per session)
  // ============================================================
  const UNLOCK_BANNER_SHOWN_KEY = "unlockBannerShown";

  function ensureUnlockBanner() {
    let el = document.getElementById("alerts-unlock-banner");
    if (el) return el;

    el = document.createElement("div");
    el.id = "alerts-unlock-banner";
    el.style.position = "fixed";
    el.style.left = "16px";
    el.style.right = "16px";
    el.style.bottom = "16px";
    el.style.zIndex = "999999";
    el.style.maxWidth = "760px";
    el.style.margin = "0 auto";
    el.style.border = "3px solid #000";
    el.style.background = "#fff";
    el.style.boxShadow = "8px 8px 0 #000";
    el.style.padding = "14px";
    el.style.fontFamily = "system-ui, -apple-system, Segoe UI, Roboto, Arial";
    el.style.display = "none";
    el.innerHTML = `
      <div style="display:flex;gap:12px;align-items:flex-start;">
        <div style="width:44px;height:44px;border:3px solid #000;background:#8B0000;color:#fff;display:flex;align-items:center;justify-content:center;font-weight:900;">
          🔔
        </div>
        <div style="flex:1;min-width:0;">
          <div style="font-weight:900;font-size:16px;letter-spacing:.02em;">Tap once to enable sound</div>
          <div style="margin-top:4px;font-size:13px;line-height:1.35;opacity:.9;">
            Browsers block sound after refresh until a tap/click happens.
          </div>
          <button type="button" style="margin-top:10px;border:3px solid #000;background:#8B0000;color:#fff;padding:10px 12px;font-weight:900;letter-spacing:.06em;text-transform:uppercase;cursor:pointer;box-shadow:4px 4px 0 #000;">
            Enable Now
          </button>
        </div>
        <button type="button" aria-label="Close" style="border:0;background:transparent;font-size:22px;cursor:pointer;line-height:1;padding:4px 6px;">✕</button>
      </div>
    `;

    el.addEventListener("click", (e) => {
      const btn = e.target.closest("button");
      if (!btn) return;
      if (btn.getAttribute("aria-label") === "Close") {
        el.style.display = "none";
        return;
      }
      unlockAll();
    });

    document.body.appendChild(el);
    return el;
  }

  function showUnlockBanner() {
    ensureUnlockBanner().style.display = "block";
  }

  function hideUnlockBanner() {
    const el = document.getElementById("alerts-unlock-banner");
    if (el && el.style) el.style.display = "none";
  }

  function showUnlockBannerOnce() {
    // ✅ Only show banner when alerts are enabled (your requirement)
    if (!isAlertsEnabled()) return;

    try {
      if (sessionStorage.getItem(UNLOCK_BANNER_SHOWN_KEY) === "1") return;
      sessionStorage.setItem(UNLOCK_BANNER_SHOWN_KEY, "1");
    } catch (_) {}

    showUnlockBanner();
  }

  function playBeep(force = false) {
    if (!isSoundEnabled()) return;

    if (!__audioUnlocked && !force) {
      __pendingBeep = true;
      // ✅ only once per session + only if alerts enabled
      showUnlockBannerOnce();
      return;
    }

    const ding = getDingEl();
    if (ding) {
      try {
        ding.currentTime = 0;
        const p = ding.play();
        if (p && typeof p.catch === "function") {
          p.catch(() => {
            __pendingBeep = true;
            showUnlockBannerOnce();
          });
        }
        return;
      } catch (_) {
        __pendingBeep = true;
        showUnlockBannerOnce();
        return;
      }
    }

    // WebAudio fallback
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.type = "sine";
      o.frequency.value = 880;
      g.gain.value = 0.10;
      o.connect(g);
      g.connect(ctx.destination);
      o.start();
      setTimeout(() => {
        o.stop();
        ctx.close();
      }, 450);
    } catch (_) {}
  }

  function unlockAudio() {
    if (__audioUnlocked) return;

    const ding = getDingEl();
    if (ding) {
      try {
        ding.muted = true;
        const p = ding.play();
        if (p && typeof p.then === "function") {
          p.then(() => {
            ding.pause();
            ding.currentTime = 0;
            ding.muted = false;
            markAudioUnlocked();
            if (__pendingBeep) {
              __pendingBeep = false;
              playBeep(true);
            }
          }).catch(() => {});
        }
      } catch (_) {}
    }

    // WebAudio unlock fallback
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      g.gain.value = 0;
      o.connect(g).connect(ctx.destination);
      o.start();
      o.stop(ctx.currentTime + 0.01);
      setTimeout(() => { try { ctx.close(); } catch (_) {} }, 50);
      markAudioUnlocked();
      if (__pendingBeep) {
        __pendingBeep = false;
        playBeep(true);
      }
    } catch (_) {}
  }

  async function unlockNotifyPermission() {
    try {
      if (!("Notification" in window)) return;
      const isLocalhost = location.hostname === "localhost" || location.hostname === "127.0.0.1";
      if (!window.isSecureContext && !isLocalhost) return;
      if (Notification.permission === "default") {
        await Notification.requestPermission();
      }
    } catch (_) {}
  }

  function unlockAll() {
    unlockAudio();
    unlockNotifyPermission();
    hideUnlockBanner();
  }

  document.addEventListener("click", unlockAll, { once: true });
  document.addEventListener("touchstart", unlockAll, { once: true });

  // ============================================================
  // Scroll target
  // ============================================================
  function scrollToOrders() {
    const target =
      document.getElementById("orders") ||
      document.getElementById("ordersMobile") ||
      document.querySelector("tbody#orders-list")?.closest("section");

    if (!target) return;
    target.scrollIntoView({ behavior: "smooth", block: "start" });

    try {
      target.style.outline = "4px solid #8B0000";
      target.style.outlineOffset = "6px";
      setTimeout(() => {
        target.style.outline = "";
        target.style.outlineOffset = "";
      }, 2000);
    } catch (_) {}
  }

  // ============================================================
  // Toast UI
  // ============================================================
  const ensureToastHost = () => {
    let el = document.getElementById("toast-host");
    if (!el) {
      el = document.createElement("div");
      el.id = "toast-host";
      el.style.position = "fixed";
      el.style.zIndex = "999999";
      el.style.right = "16px";
      el.style.top = "16px";
      el.style.display = "grid";
      el.style.gap = "12px";
      document.body.appendChild(el);
    }
    return el;
  };

  const toast = ({ title, body, meta, showViewOrders }) => {
    const host = ensureToastHost();
    const card = document.createElement("div");
    card.style.background = "#fff";
    card.style.color = "#0b0b0b";
    card.style.border = "3px solid #000";
    card.style.boxShadow = "8px 8px 0 #000";
    card.style.borderRadius = "14px";
    card.style.maxWidth = "560px";
    card.style.padding = "14px";
    card.style.fontFamily = "system-ui, -apple-system, Segoe UI, Roboto, Arial";
    card.style.opacity = "0";
    card.style.transform = "translateY(-10px)";

    const time = new Date().toLocaleTimeString();

    card.innerHTML = `
      <div style="display:flex;gap:12px;align-items:flex-start;">
        <div style="width:44px;height:44px;border:3px solid #000;background:#8B0000;color:#fff;display:flex;align-items:center;justify-content:center;font-weight:900;">
          🛒
        </div>
        <div style="flex:1;min-width:0;">
          <div style="display:flex;justify-content:space-between;gap:10px;align-items:flex-start;">
            <div style="font-weight:900;font-size:16px;letter-spacing:.02em;">${title || "New order"}</div>
            <button type="button" aria-label="Dismiss" style="border:0;background:transparent;font-size:20px;cursor:pointer;line-height:1;">✕</button>
          </div>

          <div style="margin-top:6px;font-size:13px;line-height:1.35;opacity:.92;">${body || ""}</div>

          ${meta ? `
            <div style="margin-top:10px;font-size:12px;opacity:.75;display:flex;gap:10px;flex-wrap:wrap;">
              <span style="padding:4px 8px;border:2px solid #000;background:#FAF9F6;font-weight:800;">${meta}</span>
              <span style="padding:4px 8px;border:2px solid #000;background:#FAF9F6;">${time}</span>
            </div>
          ` : ""}

          ${showViewOrders ? `
            <div style="margin-top:12px;display:flex;gap:10px;flex-wrap:wrap;">
              <button type="button" data-action="view-orders"
                style="border:3px solid #000;background:#000;color:#fff;padding:10px 12px;font-weight:900;letter-spacing:.06em;text-transform:uppercase;cursor:pointer;box-shadow:4px 4px 0 #8B0000;">
                View Orders
              </button>
              <button type="button" data-action="dismiss"
                style="border:3px solid #000;background:#FAF9F6;color:#000;padding:10px 12px;font-weight:900;letter-spacing:.06em;text-transform:uppercase;cursor:pointer;">
                Close
              </button>
            </div>
          ` : ""}
        </div>
      </div>
    `;

    const dismiss = () => {
      card.style.opacity = "0";
      card.style.transform = "translateY(-8px)";
      setTimeout(() => { try { host.removeChild(card); } catch (_) {} }, 250);
    };

    card.querySelector('button[aria-label="Dismiss"]')?.addEventListener("click", dismiss);
    card.querySelector('button[data-action="dismiss"]')?.addEventListener("click", dismiss);

    // ✅ DO NOT stop polling. Just scroll + cooldown.
    card.querySelector('button[data-action="view-orders"]')?.addEventListener("click", () => {
      dismiss();
      scrollToOrders();
      suppressNotifyUntil = Date.now() + NOTIFY_COOLDOWN_MS;
    });

    host.appendChild(card);
    requestAnimationFrame(() => {
      card.style.transition = "opacity .25s ease, transform .25s ease";
      card.style.opacity = "1";
      card.style.transform = "translateY(0)";
    });

    setTimeout(() => { try { dismiss(); } catch (_) {} }, 9000);
  };

  // ============================================================
  // Desktop notifications (only when enabled)
  // ============================================================
  const desktopNotify = async (title, body) => {
    try {
      if (!("Notification" in window)) return;
      if (!isAlertsEnabled()) return;
      if (Notification.permission === "granted") {
        const n = new Notification(title, { body, icon: "/static/logo.png" });
        n.onclick = () => { try { window.focus(); n.close(); } catch (_) {} };
      }
    } catch (_) {}
  };

  // ============================================================
  // ✅ Hydrate newly inserted row with full order detail
  // Uses your API: GET /api/store/orders/<id>
  // ============================================================
  async function hydrateOrderRowFromDetailApi(orderId, tr) {
    if (!tr || !orderId) return;

    // Avoid repeated hydration for the same row
    if (tr.getAttribute("data-hydrating") === "1") return;
    if (tr.getAttribute("data-hydrated") === "1") return;

    tr.setAttribute("data-hydrating", "1");

    try {
      const res = await fetch(`/api/store/orders/${encodeURIComponent(String(orderId))}`, {
        credentials: "same-origin",
        cache: "no-store",
      });

      const ct = res.headers.get("content-type") || "";
      if (!ct.includes("application/json")) return;

      const data = await res.json();
      if (!data || !data.ok || !data.order) return;

      const o = data.order;

      const tdCustomer = tr.querySelector('[data-col="customer"]');
      const tdPhone    = tr.querySelector('[data-col="phone"]');
      const tdDeliver  = tr.querySelector('[data-col="deliver"]');
      const tdStatus   = tr.querySelector('[data-col="status"]');
      const tdPayment  = tr.querySelector('[data-col="payment"]');

      if (tdCustomer) tdCustomer.textContent = o.customer_name || "—";
      if (tdPhone) tdPhone.textContent = o.customer_phone || "—";

      // ✅ Deliver-to formatting (DO NOT wipe the track link/button)
      if (tdDeliver) {
        const parts = [];
        if (o.addr_line1) parts.push(o.addr_line1);
        if (o.addr_line2) parts.push(o.addr_line2);

        const cityLine = [o.addr_city, o.addr_state].filter(Boolean).join(", ");
        const pin = o.addr_pincode ? String(o.addr_pincode) : "";
        const tail = [cityLine, pin].filter(Boolean).join(" ").trim();
        if (tail) parts.push(tail);

        const text = parts.length ? parts.join(", ") : "—";

        // ✅ if we have a dedicated node, update only that
        const addrNode = tdDeliver.querySelector("[data-deliver-text]");
        if (addrNode) addrNode.textContent = text;
        else tdDeliver.textContent = text; // fallback for server-rendered rows
      }

      if (tdStatus && o.status) tdStatus.textContent = String(o.status);
      if (tdPayment && o.payment_status) tdPayment.textContent = String(o.payment_status);

      tr.setAttribute("data-hydrated", "1");
    } catch (_) {
      // ignore
    } finally {
      tr.removeAttribute("data-hydrating");
    }
  }

  // ============================================================
  // Insert order row (desktop) — MATCHES YOUR TEMPLATE STRUCTURE
  // ============================================================
  const insertOrderRowIfPossible = (role, item) => {
    const list = document.getElementById("orders-list");
    if (!list) return false;

    const existing = list.querySelector(`[data-order-id="${item.order_id}"]`);
    if (existing) {
      // If row exists but placeholders remain, hydrate once
      const c = existing.querySelector('[data-col="customer"]');
      const d = existing.querySelector('[data-col="deliver"]');
      const looksBlank =
        (c && (c.textContent || "").trim() === "—") ||
        (d && (d.textContent || "").trim() === "—");
      if (role === "store" && looksBlank) hydrateOrderRowFromDetailApi(item.order_id, existing);
      return true;
    }

    // Prefer total_amount for "Total (₹)" column if API provides it, else fallback.
    const totalCol = Number(item.total_amount ?? item.total_payable ?? 0);

    // Total payable shown in Action column
    const totalPayable = Number(item.total_payable ?? totalCol ?? 0);

    if (list.tagName === "TBODY") {
      const tr = document.createElement("tr");
      tr.setAttribute("data-order-id", item.order_id);
      tr.setAttribute("data-created-at", item.created_at || new Date().toISOString());

      // NOTE: Track link here is /orders/<id>. If your route is different,
      // change only this href to match your Flask url_for('order_track', oid=id).
      tr.innerHTML = `
        <td>${item.order_id}</td>

        <td class="muted" data-col="customer">—</td>
        <td class="muted" data-col="phone">—</td>

        <td>₹${isFinite(totalCol) ? totalCol.toFixed(2) : "—"}</td>

        <td data-col="status">PLACED</td>
        <td data-col="payment">PENDING</td>

        <!-- ✅ DELIVER TO: address node + track link preserved -->
        <td class="muted" data-col="deliver">
          <div data-deliver-text>—</div>
          <div style="margin-top:10px">
            <a class="btn-ghost" href="/orders/${item.order_id}">View items &amp; track</a>
          </div>
        </td>

        <!-- ✅ ACTION: Total payable + status form -->
        <td style="min-width:260px">
          <div class="muted" style="margin:6px 0">
            Total Payable:
            <strong>₹${isFinite(totalPayable) ? totalPayable.toFixed(2) : "—"}</strong>
          </div>

          <form method="post" action="/store/orders/${item.order_id}/status">
            <select name="status">
              <option selected>PLACED</option>
              <option>CONFIRMED</option>
              <option>OUT_FOR_DELIVERY</option>
              <option>DELIVERED</option>
              <option>CANCELLED</option>
            </select>
            <button class="btn-small" type="submit">Update</button>
          </form>
        </td>
      `;

      // Insert at top
      if (list.firstChild) list.insertBefore(tr, list.firstChild);
      else list.appendChild(tr);

      // ✅ Immediately hydrate full details (customer/phone/address/status/payment)
      if (role === "store") hydrateOrderRowFromDetailApi(item.order_id, tr);

      return true;
    }

    return false;
  };

  // ============================================================
  // Max existing order id in DOM
  // ============================================================
  function getMaxOrderIdFromDom() {
    let maxId = 0;
    document.querySelectorAll('#orders-list [data-order-id]').forEach((tr) => {
      const id = Number(tr.getAttribute('data-order-id') || 0);
      if (Number.isFinite(id) && id > maxId) maxId = id;
    });
    document.querySelectorAll('.mobile-order-card[data-order-id]').forEach((card) => {
      const id = Number(card.getAttribute('data-order-id') || 0);
      if (Number.isFinite(id) && id > maxId) maxId = id;
    });
    return maxId;
  }

  // ============================================================
  // Poller
  // ============================================================
  async function startAlerts({ role }) {
    // ✅ Only start when called AND page is correct
    if (window.__alertsRunning) return;
    if (window.DISABLE_ALERTS === true) return;
    if (!role || !/^(store|delivery)$/.test(role)) return;

    // Wait briefly for DOM to exist
    const t0 = Date.now();
    while (!isAllowedNow() && Date.now() - t0 < 2500) {
      await sleep(50);
    }
    if (!isAllowedNow()) return;

    window.__alertsRunning = true;
    __stopAlerts = false;

    const STORE_CURSOR_KEY = "store_last_alert_id";
    let storeLastId = 0;
    try { storeLastId = Number(localStorage.getItem(STORE_CURSOR_KEY) || "0") || 0; } catch (_) {}

    const domMax = getMaxOrderIdFromDom();
    if (Number.isFinite(domMax) && domMax > storeLastId) {
      storeLastId = domMax;
      try { localStorage.setItem(STORE_CURSOR_KEY, String(storeLastId)); } catch (_) {}
    }

    let lastSince = new Date(Date.now() - 2 * 60 * 1000).toISOString();
    let lastReconnectToastAt = 0;

    // ✅ If user already enabled alerts+sound but audio isn't unlocked, show banner ONCE.
    if (isAlertsEnabled() && isSoundEnabled() && !__audioUnlocked) showUnlockBannerOnce();

    if (isSoundEnabled() && __audioUnlocked && __pendingBeep) {
      __pendingBeep = false;
      playBeep(true);
    }

    while (!__stopAlerts) {
      if (!isAllowedNow()) { stopAlerts(); break; }

      try {
        const url =
          role === "store"
            ? `/api/alerts/store?last_id=${encodeURIComponent(String(storeLastId))}`
            : `/api/alerts/delivery?since=${encodeURIComponent(lastSince)}`;

        const res = await fetch(url, { credentials: "same-origin", cache: "no-store" });

        const ct = res.headers.get("content-type") || "";
        if (!ct.includes("application/json")) {
          stopAlerts();
          break;
        }

        if (res.ok) {
          const data = await res.json();

          // Always advance store cursor if server says so
          if (role === "store" && data && data.ok) {
            const next = Number(data.next_last_id);
            if (Number.isFinite(next) && next > storeLastId) {
              storeLastId = next;
              try { localStorage.setItem(STORE_CURSOR_KEY, String(storeLastId)); } catch (_) {}
            }
          }

          if (data && data.ok && Array.isArray(data.new) && data.new.length) {
            if (role !== "store") {
              const newestISO = data.new.map((x) => x.created_at).filter(Boolean).sort().pop();
              if (newestISO && newestISO > lastSince) lastSince = newestISO;
            }

            data.new.forEach((item) => insertOrderRowIfPossible(role, item));

            if (isAlertsEnabled() && canNotifyNow()) {
              const ids = data.new.map((x) => `#${x.order_id}`).join(", ");
              const plural = data.new.length > 1 ? "s" : "";
              const label = role === "store" ? "New order" : "New order available";

              toast({
                title: `${label}${plural} received`,
                body: `Orders: <b>${ids}</b>`,
                meta: `${data.new.length} new`,
                showViewOrders: true,
              });

              playBeep();
              desktopNotify("Chhimphei Chicken", `${label}${plural}: ${data.new.length}`);
            }
          }
        }
      } catch (e) {
        const now = Date.now();
        if (hasFocus() && isAlertsEnabled() && !__stopAlerts) {
          if (now - lastReconnectToastAt > 20000) {
            lastReconnectToastAt = now;
            if (canNotifyNow()) {
              toast({ title: "Reconnecting…", body: "Trying to reconnect for live alerts.", meta: "network" });
            }
          }
        }
      }

      await sleep(hasFocus() ? 5000 : 12000);
    }
  }

  // ✅ Always expose initAlerts (even if DOM not ready yet)
  window.initAlerts = startAlerts;
})();
