/* Influencer Lead Finder dashboard.
 *
 * Security model (no login API):
 *  - The leads are published only as data.enc.json, encrypted with AES-256-GCM using a
 *    key derived (PBKDF2-SHA256) from "<username>\n<password>". The credentials are not
 *    in this code; a wrong username/password simply fails to decrypt.
 *  - Nothing is fetched until the user submits the form. Decrypted data and the
 *    credentials live only in memory (never localStorage/sessionStorage/cookies).
 *  - Sheet content is inserted with textContent / DOM APIs only (no innerHTML), so a
 *    malicious channel description cannot inject markup or script.
 */
(function () {
  "use strict";

  var CFG = window.APP_CONFIG || {};
  var STATUSES = ["new", "contacted", "replied", "not_interested", "do_not_contact"];
  var PAGE_SIZE = 50;
  var NUMERIC = { subscribers: true, median_views: true, engagement_rate: true };

  // Columns shown in the table (data key, label, sortable).
  var COLUMNS = [
    ["name", "Channel", true],
    ["subscribers", "Subscribers", true],
    ["median_views", "Median views", true],
    ["engagement_rate", "Engagement %", true],
    ["last_upload", "Last upload", true],
    ["niche", "Niche", true],
    ["email", "Email", true],
    ["phone", "Phone", true],
    ["website", "Website", false],
    ["date_found", "Found", true],
    ["status", "Status", true],
    ["notes", "Notes", false],
  ];
  var EXPORT_FIELDS = ["channel_id", "handle", "channel_url", "name", "subscribers", "total_views",
    "video_count", "median_views", "avg_views", "engagement_rate", "last_upload", "country",
    "niche", "description", "website", "email", "email_status", "phone", "contact_source",
    "source_url", "date_found", "status", "notes"];

  var state = {
    leads: [],
    filtered: [],
    sortKey: "date_found",
    sortDir: "desc",
    page: 1,
    idleTimer: null,
  };

  function $(id) { return document.getElementById(id); }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        var value = attrs[key];
        if (value === null || value === undefined || value === false) return;
        if (key === "text") node.textContent = String(value);
        else if (key === "className") node.className = value;
        else node.setAttribute(key, String(value));
      });
    }
    (children || []).forEach(function (child) {
      if (child === null || child === undefined) return;
      node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    });
    return node;
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function safeHttpUrl(value) {
    var raw = String(value || "").trim();
    if (!raw) return null;
    if (!/^https?:\/\//i.test(raw)) raw = "https://" + raw;
    try {
      var url = new URL(raw);
      return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
    } catch (e) {
      return null;
    }
  }

  function toast(message, kind) {
    var node = el("div", { className: "toast " + (kind || ""), text: message, role: "status" });
    $("toast-area").appendChild(node);
    setTimeout(function () { node.remove(); }, kind === "error" ? 6000 : 2500);
  }

  // ---------------------------------------------------------------- theme

  function initTheme() {
    Array.prototype.forEach.call(document.querySelectorAll(".js-theme-toggle"), function (btn) {
      btn.addEventListener("click", function () {
        var root = document.documentElement;
        var current = root.getAttribute("data-theme");
        if (!current) current = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
        root.setAttribute("data-theme", current === "dark" ? "light" : "dark");
      });
    });
  }

  // ---------------------------------------------------------------- crypto

  function b64ToBytes(b64) {
    var bin = atob(b64);
    var bytes = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return bytes;
  }

  // Must match pipeline/export_site.py.
  function decryptBlob(blob, username, password) {
    var subtle = window.crypto && window.crypto.subtle;
    if (!subtle) return Promise.reject(new Error("insecure-context"));
    var enc = new TextEncoder();
    return subtle.importKey("raw", enc.encode(username.trim() + "\n" + password), "PBKDF2", false, ["deriveKey"])
      .then(function (baseKey) {
        return subtle.deriveKey(
          { name: "PBKDF2", salt: b64ToBytes(blob.salt), iterations: Number(blob.iter), hash: "SHA-256" },
          baseKey, { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
      })
      .then(function (key) {
        return subtle.decrypt({ name: "AES-GCM", iv: b64ToBytes(blob.iv) }, key, b64ToBytes(blob.ct));
      })
      .then(function (plain) { return JSON.parse(new TextDecoder().decode(plain)); });
  }

  // ---------------------------------------------------------------- auth

  function showLoginMessage(text, isError) {
    var node = $("login-message");
    node.textContent = text || "";
    node.className = "login-message" + (isError ? " error" : "");
    node.hidden = !text;
  }

  function onSubmit(event) {
    event.preventDefault();
    var username = $("username").value;
    var password = $("password").value;
    if (!username.trim() || !password) {
      showLoginMessage("Enter your username and password.", true);
      return;
    }
    var button = $("signin-btn");
    button.disabled = true;
    button.textContent = "Signing in…";
    showLoginMessage("");

    fetch(CFG.DATA_URL || "data.enc.json", { cache: "no-store" })
      .then(function (resp) {
        if (resp.status === 404) throw new Error("no-data");
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
      })
      .then(function (blob) {
        return decryptBlob(blob, username, password).catch(function (err) {
          if (err && err.message === "insecure-context") throw err;
          throw new Error("bad-credentials");
        });
      })
      .then(function (payload) {
        $("password").value = "";
        showDashboard(payload);
      })
      .catch(function (err) {
        var msg = err && err.message;
        if (msg === "bad-credentials") showLoginMessage("Invalid username or password.", true);
        else if (msg === "no-data") showLoginMessage("No data has been published yet. Run the pipeline and the site deploy first (see SETUP.md).", true);
        else if (msg === "insecure-context") showLoginMessage("This page must be opened over https (or http://localhost).", true);
        else showLoginMessage("Could not load data (" + (msg || "network error") + "). Try again.", true);
        $("password").value = "";
        $("password").focus();
      })
      .then(function () {
        button.disabled = false;
        button.textContent = "Sign in";
      });
  }

  function signOut(message) {
    clearTimeout(state.idleTimer);
    state.leads = [];
    state.filtered = [];
    state.page = 1;
    $("leads-body").replaceChildren();
    $("thead-row").replaceChildren();
    ["stat-total", "stat-week", "stat-email", "stat-run"].forEach(function (id) { $(id).textContent = "–"; });
    $("stat-run-detail").textContent = "";
    $("data-age").textContent = "";
    $("username").value = "";
    $("password").value = "";
    $("app-view").hidden = true;
    $("login-view").hidden = false;
    showLoginMessage(message || "You have been signed out.", false);
  }

  function resetIdleTimer() {
    if ($("app-view").hidden) return;
    clearTimeout(state.idleTimer);
    var minutes = Number(CFG.IDLE_LOCK_MINUTES) || 60;
    state.idleTimer = setTimeout(function () {
      signOut("Signed out after " + minutes + " minutes of inactivity.");
    }, minutes * 60000);
  }

  // ---------------------------------------------------------------- data

  function toNumber(value) {
    var n = parseFloat(String(value || "").replace(/[,%\s]/g, ""));
    return isNaN(n) ? 0 : n;
  }

  function showDashboard(payload) {
    state.leads = (payload.leads || []).map(function (raw) {
      var lead = {};
      Object.keys(raw || {}).forEach(function (k) { lead[k] = raw[k] == null ? "" : String(raw[k]); });
      lead.handle = (lead.handle || "").replace(/^@/, "");
      lead._subs = toNumber(lead.subscribers);
      lead._median = toNumber(lead.median_views);
      lead._er = toNumber(lead.engagement_rate);
      lead.status = (lead.status || "new").trim().toLowerCase();
      return lead;
    }).filter(function (lead) { return lead.channel_id; });

    var generated = payload.generated_at ? new Date(payload.generated_at) : null;
    $("data-age").textContent = generated && !isNaN(generated) ? "Data as of " + generated.toLocaleString() : "";
    renderStats(payload.last_run);
    populateFilters();
    buildHeader();
    $("login-view").hidden = true;
    $("app-view").hidden = false;
    showLoginMessage("");
    applyFilters();
    resetIdleTimer();
  }

  function renderStats(run) {
    var weekAgo = new Date(Date.now() - 7 * 864e5).toISOString().slice(0, 10);
    $("stat-total").textContent = state.leads.length.toLocaleString();
    $("stat-week").textContent = state.leads.filter(function (l) { return (l.date_found || "") >= weekAgo; }).length.toLocaleString();
    $("stat-email").textContent = state.leads.filter(function (l) { return l.email_status === "valid"; }).length.toLocaleString();
    if (!run) {
      $("stat-run").textContent = "No runs yet";
      $("stat-run-detail").textContent = "";
      return;
    }
    var when = run.finished_at || run.started_at;
    var date = when ? new Date(when) : null;
    $("stat-run").textContent = date && !isNaN(date) ? date.toLocaleString() : (when || "Unknown");
    var detail = (run.accepted || 0) + " accepted · " + (run.rejected || 0) + " rejected · " + (run.errors || 0) + " errors";
    if (run.warnings) detail += " · ⚠ " + run.warnings;
    $("stat-run-detail").textContent = detail;
  }

  function populateFilters() {
    fillSelect($("f-niche"), uniqueValues("niche"));
    fillSelect($("f-status"), STATUSES);
  }

  function uniqueValues(key) {
    var seen = {};
    state.leads.forEach(function (l) { if (l[key]) seen[l[key]] = true; });
    return Object.keys(seen).sort(function (a, b) { return a.localeCompare(b); });
  }

  function fillSelect(select, values) {
    var current = select.value;
    select.replaceChildren(el("option", { value: "", text: "All" }));
    values.forEach(function (v) { select.appendChild(el("option", { value: v, text: v.replace(/_/g, " ") })); });
    if (values.indexOf(current) >= 0) select.value = current;
  }

  // ---------------------------------------------------------------- filter / sort

  function readFilters() {
    return {
      q: $("f-search").value.trim().toLowerCase(),
      niche: $("f-niche").value,
      status: $("f-status").value,
      fmin: $("f-fmin").value === "" ? null : Number($("f-fmin").value),
      fmax: $("f-fmax").value === "" ? null : Number($("f-fmax").value),
      hasEmail: $("f-email").checked,
      hasPhone: $("f-phone").checked,
    };
  }

  function applyFilters(keepPage) {
    var f = readFilters();
    state.filtered = state.leads.filter(function (l) {
      if (f.niche && l.niche !== f.niche) return false;
      if (f.status && l.status !== f.status) return false;
      if (f.fmin !== null && l._subs < f.fmin) return false;
      if (f.fmax !== null && l._subs > f.fmax) return false;
      if (f.hasEmail && !l.email) return false;
      if (f.hasPhone && !l.phone) return false;
      if (f.q) {
        var hay = [l.handle, l.name, l.description, l.email, l.niche, l.country, l.notes, l.website].join(" ").toLowerCase();
        if (hay.indexOf(f.q) === -1) return false;
      }
      return true;
    });
    sortRows();
    if (!keepPage) state.page = 1;
    renderTable();
  }

  function sortRows() {
    var key = state.sortKey;
    var dir = state.sortDir === "asc" ? 1 : -1;
    var numKey = { subscribers: "_subs", median_views: "_median", engagement_rate: "_er" }[key];
    state.filtered.sort(function (a, b) {
      var cmp;
      if (numKey) cmp = a[numKey] - b[numKey];
      else cmp = String(a[key] || "").localeCompare(String(b[key] || ""), undefined, { sensitivity: "base" });
      return cmp * dir || a.channel_id.localeCompare(b.channel_id);
    });
  }

  function buildHeader() {
    var row = $("thead-row");
    row.replaceChildren();
    COLUMNS.forEach(function (col) {
      var th = el("th", { scope: "col", className: col[2] ? "sortable" : "", "data-key": col[0] }, [col[1]]);
      if (col[2]) {
        th.setAttribute("tabindex", "0");
        th.setAttribute("aria-sort", "none");
        th.addEventListener("click", function () { setSort(col[0]); });
        th.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSort(col[0]); } });
      }
      row.appendChild(th);
    });
    updateSortIndicators();
  }

  function setSort(key) {
    if (state.sortKey === key) state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
    else { state.sortKey = key; state.sortDir = NUMERIC[key] || key === "date_found" ? "desc" : "asc"; }
    updateSortIndicators();
    applyFilters(true);
  }

  function updateSortIndicators() {
    Array.prototype.forEach.call($("thead-row").children, function (th) {
      var old = th.querySelector(".arrow");
      if (old) old.remove();
      var active = th.getAttribute("data-key") === state.sortKey;
      if (th.classList.contains("sortable")) th.setAttribute("aria-sort", active ? (state.sortDir === "asc" ? "ascending" : "descending") : "none");
      if (active) th.appendChild(el("span", { className: "arrow", text: state.sortDir === "asc" ? "▲" : "▼" }));
    });
  }

  // ---------------------------------------------------------------- render

  function renderTable() {
    var total = state.filtered.length;
    var pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    state.page = Math.min(Math.max(1, state.page), pages);
    var start = (state.page - 1) * PAGE_SIZE;
    var slice = state.filtered.slice(start, start + PAGE_SIZE);

    var body = $("leads-body");
    body.replaceChildren();
    slice.forEach(function (lead) { body.appendChild(renderRow(lead)); });

    $("empty-state").hidden = total > 0;
    $("result-count").textContent = total.toLocaleString() + " of " + state.leads.length.toLocaleString() + " leads";
    $("page-info").textContent = "Page " + state.page + " of " + pages;
    $("prev-btn").disabled = state.page <= 1;
    $("next-btn").disabled = state.page >= pages;
  }

  function link(href, text, external) {
    if (!href) return document.createTextNode(text || "");
    var a = el("a", { href: href, text: text });
    if (external) { a.target = "_blank"; a.rel = "noopener noreferrer"; }
    return a;
  }

  function renderRow(lead) {
    var tr = document.createElement("tr");

    var channelHref = safeHttpUrl(lead.channel_url) ||
      safeHttpUrl("https://www.youtube.com/channel/" + encodeURIComponent(lead.channel_id));
    tr.appendChild(el("td", null, [
      el("span", { className: "handle" }, [link(channelHref, lead.name || lead.handle || lead.channel_id, true)]),
      lead.handle ? el("span", { className: "sub", text: "@" + lead.handle + (lead.country ? " · " + lead.country : "") }) : null,
      lead.description ? el("span", { className: "sub", text: lead.description, title: lead.description }) : null,
    ]));

    tr.appendChild(el("td", { className: "num", text: lead._subs.toLocaleString() }));
    tr.appendChild(el("td", { className: "num", text: lead._median.toLocaleString() }));
    tr.appendChild(el("td", { className: "num", text: lead.engagement_rate === "" ? "" : lead._er.toFixed(2) }));
    tr.appendChild(el("td", { className: "nowrap", text: lead.last_upload }));
    tr.appendChild(el("td", { text: lead.niche }));

    var emailCell = el("td", { className: "nowrap" });
    if (lead.email) {
      emailCell.appendChild(link("mailto:" + encodeURIComponent(lead.email).replace(/%40/g, "@"), lead.email));
      if (lead.email_status) {
        var cls = lead.email_status.replace(/[^a-z_]/g, "");
        emailCell.appendChild(el("span", { className: "badge " + cls, text: lead.email_status.replace(/_/g, " ") }));
      }
    }
    tr.appendChild(emailCell);

    var phoneCell = el("td", { className: "nowrap" });
    if (lead.phone) {
      var tel = lead.phone.replace(/[^\d+]/g, "");
      phoneCell.appendChild(link(tel ? "tel:" + tel : null, lead.phone));
    }
    tr.appendChild(phoneCell);

    var siteCell = el("td");
    var siteHref = safeHttpUrl(lead.website);
    if (siteHref) {
      var label = siteHref.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "");
      siteCell.appendChild(link(siteHref, label.length > 28 ? label.slice(0, 27) + "…" : label, true));
    }
    tr.appendChild(siteCell);

    tr.appendChild(el("td", { className: "nowrap", text: lead.date_found }));
    var statusClass = "status-" + lead.status.replace(/[^a-z_]/g, "");
    tr.appendChild(el("td", null, [el("span", { className: "pill " + statusClass, text: lead.status.replace(/_/g, " ") })]));
    tr.appendChild(el("td", { className: "notes", text: lead.notes }));
    return tr;
  }

  // ---------------------------------------------------------------- export

  function csvCell(value) {
    var s = String(value == null ? "" : value);
    // Neutralise spreadsheet formula injection (but keep phone numbers like +91…).
    if (/^[=+\-@\t\r]/.test(s) && !/^[+-]?\d[\d\s().-]*$/.test(s)) s = "'" + s;
    return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }

  function exportCsv() {
    if (!state.filtered.length) { toast("Nothing to export."); return; }
    var lines = [EXPORT_FIELDS.map(csvCell).join(",")];
    state.filtered.forEach(function (lead) {
      lines.push(EXPORT_FIELDS.map(function (h) { return csvCell(lead[h]); }).join(","));
    });
    var blob = new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var a = el("a", { href: url, download: "leads-" + new Date().toISOString().slice(0, 10) + ".csv" });
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    toast("Exported " + state.filtered.length + " rows", "ok");
  }

  // ---------------------------------------------------------------- wiring

  function debounce(fn, ms) {
    var t;
    return function () { clearTimeout(t); t = setTimeout(fn, ms); };
  }

  function init() {
    document.querySelectorAll(".js-app-name").forEach(function (n) { n.textContent = CFG.APP_NAME || "Influencer Lead Finder"; });
    document.title = CFG.APP_NAME || document.title;
    initTheme();

    $("login-form").addEventListener("submit", onSubmit);
    $("signout-btn").addEventListener("click", function () { signOut(); });
    $("export-btn").addEventListener("click", exportCsv);
    $("prev-btn").addEventListener("click", function () { state.page -= 1; renderTable(); });
    $("next-btn").addEventListener("click", function () { state.page += 1; renderTable(); });
    $("clear-btn").addEventListener("click", function () {
      ["f-search", "f-niche", "f-status", "f-fmin", "f-fmax"].forEach(function (id) { $(id).value = ""; });
      $("f-email").checked = false;
      $("f-phone").checked = false;
      applyFilters();
    });
    $("f-search").addEventListener("input", debounce(function () { applyFilters(); }, 150));
    ["f-niche", "f-status", "f-email", "f-phone"].forEach(function (id) {
      $(id).addEventListener("change", function () { applyFilters(); });
    });
    ["f-fmin", "f-fmax"].forEach(function (id) {
      $(id).addEventListener("input", debounce(function () { applyFilters(); }, 250));
    });
    ["click", "keydown", "scroll", "pointermove"].forEach(function (evt) {
      document.addEventListener(evt, debounce(resetIdleTimer, 1000), { passive: true });
    });
    $("username").focus();
  }

  window.LeadFinder = { escapeHtml: escapeHtml, csvCell: csvCell };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
