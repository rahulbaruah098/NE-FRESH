

  // Stop on SPA-like navigations (pushState/replaceState/popstate/hashchange)
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

  // ✅ Most important: whenever URL changes, STOP alerts if not on allowed page
  window.addEventListener("locationchange", () => {
    if (!ALLOWED_PAGES.has(getPage())) stopAlerts();
  });

  // Prevent double-start of poll loop
  window.__alertsRunning = window.__alertsRunning || false;

  // =========================
  // Audio unlock + permission (requires user gesture per session)
  // =========================
  let __audioUnlocked = false;
  let __pendingBeep = false;

  function setAudioUnlocked() {
    __audioUnlocked = true;
    try { sessionStorage.setItem("audioUnlocked", "1"); } catch (_) {}
  }

  // If this tab already unlocked earlier in the same session, restore it
  try {
    if (sessionStorage.getItem("audioUnlocked") === "1") __audioUnlocked = true;
  } catch (_) {}

  function unlockAudio() {
    if (__audioUnlocked) return;

    // Try unlocking <audio id="ding">
    const ding = document.getElementById("ding");
    if (ding) {
      try {
        ding.muted = true;
        const p = ding.play();
        if (p && typeof p.then === "function") {
          p.then(() => {
            ding.pause();
            ding.currentTime = 0;
            ding.muted = false;
            setAudioUnlocked();
            if (__pendingBeep) {
              __pendingBeep = false;
              playBeep(true);
            }
          }).catch(() => {});
        }
      } catch (_) {}
    }

    // Also unlock WebAudio
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      g.gain.value = 0;
      o.connect(g).connect(ctx.destination);
      o.start();
      o.stop(ctx.currentTime + 0.01);
      setTimeout(() => { try { ctx.close(); } catch (_) {} }, 50);
      setAudioUnlocked();
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

  // one-time gesture unlock
  document.addEventListener("click", unlockAll, { once: true });
  document.addEventListener("touchstart", unlockAll, { once: true });

  // =========================
  // BIG Unlock Banner (shown when user enabled alerts but audio is locked)
  // =========================
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
    el.style.border = "3px solid #000";
    el.style.background = "#fff";
    el.style.boxShadow = "6px 6px 0 #000";
    el.style.padding = "14px 14px";
    el.style.fontFamily = "system-ui, -apple-system, Segoe UI, Roboto, Arial";
    el.style.display = "none";
    el.style.maxWidth = "720px";
    el.style.margin = "0 auto";
    el.innerHTML = `
      <div style="display:flex;align-items:flex-start;gap:12px;">
        <div style="width:44px;height:44px;border:3px solid #000;background:#8B0000;color:#fff;display:flex;align-items:center;justify-content:center;font-weight:800;">
          🔔
        </div>
        <div style="flex:1">
          <div style="font-weight:900;font-size:16px;letter-spacing:.02em;color:#000;margin-bottom:2px;">
            Tap once to enable order sound
          </div>
          <div style="font-size:13px;color:#111;opacity:.9;line-height:1.35;">
            Browsers block sound after refresh until you tap anywhere. Your orders will still appear, but sound will play only after one tap.
          </div>
          <button type="button" style="margin-top:10px;border:3px solid #000;background:#8B0000;color:#fff;padding:10px 12px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;cursor:pointer;box-shadow:3px 3px 0 #000;">
            Enable Now
          </button>
        </div>
        <button type="button" aria-label="Close" style="border:0;background:transparent;font-size:22px;line-height:1;cursor:pointer;padding:4px 6px;">✕</button>
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
    const el = ensureUnlockBanner();
    el.style.display = "block";
  }

  function hideUnlockBanner() {
    const el = document.getElementById("alerts-unlock-banner");
    if (el) el.style.display = "none";
  }

  // =========================
  // Beep (audio id="ding" preferred)
  // force=true bypasses the "locked" check after a successful unlock callback
  // =========================
  const playBeep = (() => {
    let pageDing = null;

    const tryPageDing = () => {
      if (!pageDing) pageDing = document.getElementById("ding");
      if (pageDing) {
        try {
          pageDing.currentTime = 0;
          const p = pageDing.play();
          if (p && typeof p.catch === "function") {
            p.catch(() => {});
          }
          return true;
        } catch (_) {}
      }
      return false;
    };

    return (force = false) => {
      if (!isSoundEnabled()) return;

      // if audio is locked, queue it and show banner
      if (!__audioUnlocked && !force) {
        __pendingBeep = true;
        showUnlockBanner();
        return;
      }

      if (tryPageDing()) return;

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
    };
  })();

  // =========================
  // BIG Toast UI
  // =========================
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

  // Scroll helper (used by View Orders)
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

  const toast = ({ title, body, meta, showViewOrders }) => {
    const host = ensureToastHost();
    const card = document.createElement("div");
    card.style.background = "#fff";
    card.style.color = "#0b0b0b";
    card.style.border = "3px solid #000";
    card.style.boxShadow = "8px 8px 0 #000";
    card.style.borderRadius = "14px";
    card.style.maxWidth = "560px";
    card.style.padding = "14px 14px";
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

    card.querySelector('button[data-action="view-orders"]')?.addEventListener("click", () => {
      dismiss();
      scrollToOrders();
      stopAlerts(); // stop loop when user chooses to view
    });

    host.appendChild(card);
    requestAnimationFrame(() => {
      card.style.transition = "opacity .25s ease, transform .25s ease";
      card.style.opacity = "1";
      card.style.transform = "translateY(0)";
    });

    setTimeout(() => { try { dismiss(); } catch (_) {} }, 9000);
  };

  // =========================
  // Desktop notifications (only after user enabled)
  // =========================
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

  // =========================
  // Insert new order rows (desktop)
  // =========================
  const insertOrderRowIfPossible = (role, item) => {
    const list = document.getElementById("orders-list");
    if (!list) return false;

    if (list.querySelector(`[data-order-id="${item.order_id}"]`)) return true;

    const total = Number(item.total_payable ?? 0);

    if (list.tagName === "TBODY") {
      const tr = document.createElement("tr");
      tr.setAttribute("data-order-id", item.order_id);
      tr.setAttribute("data-created-at", item.created_at || new Date().toISOString());

      if (role === "store") {
        tr.innerHTML = `
          <td>${item.order_id}</td>
          <td class="muted">—</td>
          <td class="muted">—</td>
          <td>₹${isFinite(total) ? total.toFixed(2) : "—"}</td>
          <td>PLACED</td>
          <td>PENDING</td>
          <td class="muted">—</td>
          <td style="min-width:260px">
            <div class="muted" style="margin:6px 0">
              Total Payable: <strong>₹${isFinite(total) ? total.toFixed(2) : "—"}</strong>
            </div>
            <a class="btn-ghost" href="/orders/${item.order_id}">View items & track</a>
          </td>
        `;
      } else {
        tr.innerHTML = `
          <td>${item.order_id}</td>
          <td class="muted">—</td>
          <td class="muted">—</td>
          <td>₹${isFinite(total) ? total.toFixed(2) : "—"}</td>
          <td>PLACED</td>
          <td class="muted">—</td>
          <td style="min-width:300px">
            <form method="post" action="/delivery/order/${item.order_id}/assign" style="display:inline-block;margin-right:6px">
              <button class="btn">Assign to me</button>
            </form>
          </td>
          <td class="muted">—</td>
        `;
      }

      if (list.firstChild) list.insertBefore(tr, list.firstChild);
      else list.appendChild(tr);
      return true;
    }

    return false;
  };

  // =========================
  // Poller (✅ FIXED: STORE uses last_id cursor)
  // =========================
  async function startAlerts({ role }) {
    if (window.__alertsRunning) return;

    if (!role || !/^(store|delivery)$/.test(role)) return;
    if (window.DISABLE_ALERTS === true) return;

    window.__alertsRunning = true;
    __stopAlerts = false;

    // ✅ Store cursor (ID-based)
    const STORE_CURSOR_KEY = "store_last_alert_id";
    let storeLastId = 0;
    try {
      storeLastId = Number(localStorage.getItem(STORE_CURSOR_KEY) || "0") || 0;
    } catch (_) {
      storeLastId = 0;
    }

    // Delivery stays time-based (unless you update backend similarly)
    let lastSince = new Date(Date.now() - 2 * 60 * 1000).toISOString();
    let lastReconnectToastAt = 0;

    // remind user to unlock audio if needed
    if (isAlertsEnabled() && isSoundEnabled() && !__audioUnlocked) showUnlockBanner();
    if (isSoundEnabled() && __audioUnlocked && __pendingBeep) {
      __pendingBeep = false;
      playBeep(true);
    }

    while (!__stopAlerts) {
      // ✅ If user navigated away, STOP NOW
      if (!ALLOWED_PAGES.has(getPage())) {
        stopAlerts();
        break;
      }

      try {
        const url =
          role === "store"
            ? `/api/alerts/store?last_id=${encodeURIComponent(String(storeLastId))}`
            : `/api/alerts/delivery?since=${encodeURIComponent(lastSince)}`;

        const res = await fetch(url, { credentials: "same-origin", cache: "no-store" });

        if (res.ok) {
          const data = await res.json();

          if (data && data.ok && Array.isArray(data.new) && data.new.length) {
            // ✅ advance cursor FIRST for store (prevents duplicates)
            if (role === "store") {
              const next = Number(data.next_last_id);
              if (Number.isFinite(next) && next > storeLastId) {
                storeLastId = next;
                try { localStorage.setItem(STORE_CURSOR_KEY, String(storeLastId)); } catch (_) {}
              }
            } else {
              const newestISO = data.new.map((x) => x.created_at).filter(Boolean).sort().pop();
              if (newestISO && newestISO > lastSince) lastSince = newestISO;
            }

            // Insert rows (even if alerts disabled)
            data.new.forEach((item) => insertOrderRowIfPossible(role, item));

            if (isAlertsEnabled()) {
              const ids = data.new.map((x) => `#${x.order_id}`).join(", ");
              const plural = data.new.length > 1 ? "s" : "";
              const title = role === "store" ? `New order${plural} received` : `New order${plural} available`;

              toast({
                title,
                body: `Orders: <b>${ids}</b>`,
                meta: `${data.new.length} new`,
                showViewOrders: true,
              });

              playBeep();
              desktopNotify("Chhimphei Chicken", `${title} (${data.new.length})`);
            }
          } else {
            // even if no new, accept server cursor update
            if (role === "store" && data && data.ok) {
              const next = Number(data.next_last_id);
              if (Number.isFinite(next) && next > storeLastId) {
                storeLastId = next;
                try { localStorage.setItem(STORE_CURSOR_KEY, String(storeLastId)); } catch (_) {}
              }
            }
          }
        }
      } catch (e) {
        const now = Date.now();
        if (hasFocus() && isAlertsEnabled() && !__stopAlerts) {
          if (now - lastReconnectToastAt > 20000) {
            lastReconnectToastAt = now;
            toast({ title: "Reconnecting…", body: "Trying to reconnect for live alerts.", meta: "network" });
          }
        }
      }

      await sleep(hasFocus() ? 5000 : 12000);
    }
  }

  window.initAlerts = startAlerts;

  // ✅ IMPORTANT: DO NOT auto-start on DOMContentLoaded.
  // Your dashboard should call: initAlerts({role:'store'})
})();
