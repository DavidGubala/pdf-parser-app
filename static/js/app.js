// ---------------------------------------------------------------------------
//  PDF Parse — Frontend (vanilla JS)
// ---------------------------------------------------------------------------

const API = "/api";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

// ---------------------------------------------------------------------------
//  Dark mode — auto-detect + manual toggle with localStorage persistence
// ---------------------------------------------------------------------------

(function initTheme() {
  const stored = localStorage.getItem("theme");
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const theme = stored || (prefersDark ? "dark" : "light");
  document.documentElement.setAttribute("data-theme", theme);
})();

function setupThemeToggle() {
  const btn = $("#theme-toggle");
  if (!btn) return;

  function updateIcon() {
    const isDark =
      document.documentElement.getAttribute("data-theme") === "dark";
    btn.textContent = isDark ? "\u2600" : "\u263E";
    btn.title = isDark ? "Switch to light mode" : "Switch to dark mode";
  }
  updateIcon();

  btn.addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme");
    const next = current === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("theme", next);
    updateIcon();
  });

  window
    .matchMedia("(prefers-color-scheme: dark)")
    .addEventListener("change", (e) => {
      if (localStorage.getItem("theme")) return;
      const theme = e.matches ? "dark" : "light";
      document.documentElement.setAttribute("data-theme", theme);
      updateIcon();
    });
}
setupThemeToggle();

// ---------------------------------------------------------------------------
//  User dropdown menu
// ---------------------------------------------------------------------------

(function setupUserMenu() {
  const menuBtn = $("#user-menu-btn");
  const dropdown = $("#user-dropdown");
  const menuWrap = $(".user-menu");
  if (!menuBtn || !dropdown || !menuWrap) return;

  menuBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    const open = !dropdown.hidden;
    dropdown.hidden = open;
    menuWrap.classList.toggle("open", !open);
  });

  document.addEventListener("click", (e) => {
    if (!menuWrap.contains(e.target)) {
      dropdown.hidden = true;
      menuWrap.classList.remove("open");
    }
  });
})();

// Redirect to login on 401 from any API call
const _origFetch = window.fetch;
window.fetch = async function (...args) {
  const res = await _origFetch.apply(this, args);
  if (res.status === 401) {
    window.location.href = "/login";
    return res;
  }
  return res;
};

// DOM references — global
const documentsView = $("#documents-view");
const scheduleView = $("#schedule-view");

// DOM references — documents
const dropZone = $("#drop-zone");
const fileInput = $("#file-input");
const uploadStatus = $("#upload-status");
const docsList = $("#documents-list");
const detailSection = $("#detail-section");
const docsSection = $("#documents-section");
const uploadSection = $("#upload-section");
const detailHeader = $("#detail-header");
const detailContent = $("#detail-content");
const backBtn = $("#back-btn");
const backSchedBtn = $("#back-schedule-btn");
const refreshBtn = $("#refresh-btn");

// DOM references — schedule
const urgencyFilter = $("#urgency-filter");
const companyFilter = $("#company-filter");
const dateFrom = $("#date-from");
const dateTo = $("#date-to");
const clearFiltersBtn = $("#clear-filters-btn");
const refreshScheduleBtn = $("#refresh-schedule-btn");
const scheduleContainer = $("#schedule-table-container");
const scheduleCount = $("#schedule-count");
const listSection = $("#schedule-list-section");
const calSection = $("#schedule-calendar-section");
const calTitle = $("#cal-title");
const calGrid = $("#cal-grid");

let pollingTimer = null;
let currentView = "documents";
let scheduleMode = "list";
let scheduleData = null;
let detailOrigin = "documents"; // tracks where the user came from
let calYear, calMonth;

// ---------------------------------------------------------------------------
//  Navigation
// ---------------------------------------------------------------------------

$$(".nav-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    const view = btn.dataset.view;
    if (view === currentView) return;

    currentView = view;
    $$(".nav-btn").forEach((b) =>
      b.classList.toggle("active", b.dataset.view === view),
    );

    if (view === "documents") {
      documentsView.hidden = false;
      scheduleView.hidden = true;
    } else {
      documentsView.hidden = true;
      scheduleView.hidden = false;
      loadSchedule();
    }
  });
});

// ---------------------------------------------------------------------------
//  Upload handling
// ---------------------------------------------------------------------------

dropZone.addEventListener("click", (e) => {
  if (e.target.closest("label")) return;
  fileInput.click();
});

dropZone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropZone.classList.add("dragover");
});

dropZone.addEventListener("dragleave", () => {
  dropZone.classList.remove("dragover");
});

dropZone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropZone.classList.remove("dragover");
  const file = e.dataTransfer.files[0];
  if (file) uploadFile(file);
});

fileInput.addEventListener("change", () => {
  if (fileInput.files[0]) uploadFile(fileInput.files[0]);
});

async function uploadFile(file) {
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    showUploadStatus("Only PDF files are supported.", "error");
    return;
  }

  showUploadStatus(`Uploading "${file.name}"…`, "info");

  const form = new FormData();
  form.append("file", file);

  try {
    const res = await fetch(`${API}/upload`, { method: "POST", body: form });

    const contentType = res.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) {
      throw new Error(`Upload failed (server returned ${res.status})`);
    }

    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Upload failed");

    showUploadStatus(`"${data.filename}" uploaded — processing…`, "success");
    fileInput.value = "";
    loadDocuments();
    startPolling();
  } catch (err) {
    showUploadStatus(err.message, "error");
  }
}

function showUploadStatus(msg, type) {
  uploadStatus.hidden = false;
  uploadStatus.textContent = msg;
  uploadStatus.className = `upload-status ${type}`;
}

// ---------------------------------------------------------------------------
//  Documents list
// ---------------------------------------------------------------------------

async function loadDocuments() {
  try {
    const res = await fetch(`${API}/documents`);
    const docs = await res.json();
    renderDocumentList(docs);

    const hasActive = docs.some(
      (d) => d.status === "processing" || d.status === "analyzing",
    );
    if (hasActive) startPolling();
    else stopPolling();
  } catch (err) {
    docsList.innerHTML = `<p class="empty-state">Failed to load documents.</p>`;
  }
}

function renderDocumentList(docs) {
  if (!docs.length) {
    docsList.innerHTML = `<p class="empty-state">No documents yet. Upload a PDF to get started.</p>`;
    return;
  }

  docsList.innerHTML = docs
    .map((d) => {
      const date = new Date(d.upload_time).toLocaleString();
      const badge = badgeHTML(d.status);
      const active = d.status === "processing" || d.status === "analyzing";
      const pct = d.status === "processing" ? "33%" : "66%";
      const stageLabel =
        d.status === "processing"
          ? "Extracting PDF…"
          : "Reading purchase order…";
      const progressHTML = active
        ? `<div class="doc-progress"><div class="progress-track"><div class="progress-fill" style="width:${pct}"></div></div><span class="progress-label">${stageLabel}</span></div>`
        : "";
      return `
        <div class="doc-item" data-id="${d.id}">
          <div style="flex:1;min-width:0;display:flex;flex-direction:column;">
            <div class="doc-item-main">
              <span class="doc-name" data-id="${d.id}">${escapeHTML(d.original_name)}</span>
              <div class="doc-item-meta">
                ${badge}
                ${d.page_count ? `<span class="doc-pages">${d.page_count} pg</span>` : ""}
                <span class="doc-date">${date}</span>
              </div>
            </div>
            ${progressHTML}
          </div>
          <button class="btn btn-danger delete-btn" data-id="${d.id}" title="Delete">&#10005;</button>
        </div>`;
    })
    .join("");

  docsList
    .querySelectorAll(".doc-name")
    .forEach((el) =>
      el.addEventListener("click", () =>
        openDocument(el.dataset.id, "documents"),
      ),
    );

  docsList.querySelectorAll(".delete-btn").forEach((el) =>
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteDocument(el.dataset.id);
    }),
  );
}

function badgeHTML(status) {
  const labels = {
    processing: "Processing PDF",
    analyzing: "Analyzing PO",
    completed: "Completed",
    error: "Error",
    pending: "Pending",
  };
  const active = status === "processing" || status === "analyzing";
  const spinner = active ? '<span class="spinner"></span> ' : "";
  return `<span class="badge badge-${status}">${spinner}${labels[status] || status}</span>`;
}

// ---------------------------------------------------------------------------
//  Polling for processing status
// ---------------------------------------------------------------------------

function startPolling() {
  if (pollingTimer) return;
  // Poll immediately so the user sees the current state right away
  loadDocuments();
  if (currentView === "schedule") loadSchedule();
  pollingTimer = setInterval(() => {
    loadDocuments();
    if (currentView === "schedule") loadSchedule();
  }, 3000);
}

function stopPolling() {
  if (pollingTimer) {
    clearInterval(pollingTimer);
    pollingTimer = null;
  }
}

// ---------------------------------------------------------------------------
//  Document detail
// ---------------------------------------------------------------------------

async function openDocument(id, origin) {
  if (origin) detailOrigin = origin;

  try {
    const res = await fetch(`${API}/documents/${id}`);
    if (!res.ok) throw new Error("Not found");
    const doc = await res.json();

    if (doc.status === "processing") {
      showUploadStatus("Document is still processing. Please wait…", "info");
      return;
    }

    uploadSection.hidden = true;
    docsSection.hidden = true;
    detailSection.hidden = false;
    detailSection.dataset.docId = id;

    backBtn.hidden = detailOrigin === "schedule";
    backSchedBtn.hidden = detailOrigin !== "schedule";

    detailHeader.innerHTML = `
      <h3>${escapeHTML(doc.original_name)}</h3>
      <p class="detail-meta">
        Uploaded ${new Date(doc.upload_time).toLocaleString()}
        ${doc.page_count ? ` &middot; ${doc.page_count} pages` : ""}
        &middot; ${badgeHTML(doc.status)}
      </p>
    `;

    if (doc.status === "error") {
      detailContent.innerHTML = `<p class="upload-status error">${escapeHTML(doc.error)}</p>`;
      return;
    }

    let poData = null;
    try {
      const poRes = await fetch(`${API}/purchase-orders`);
      const allPOs = await poRes.json();
      poData = allPOs.find((po) => po.document_id === doc.id) || null;
    } catch (_) {
      /* PO data fetch is optional */
    }

    renderDetailTab("pdf", doc, poData);
    $$(".tab").forEach((t) => {
      t.classList.toggle("active", t.dataset.tab === "pdf");
      t.onclick = () => {
        $$(".tab").forEach((tt) => tt.classList.remove("active"));
        t.classList.add("active");
        renderDetailTab(t.dataset.tab, doc, poData);
      };
    });
  } catch (err) {
    showUploadStatus("Could not load document details.", "error");
  }
}

function renderDetailTab(tab, doc, poData) {
  if (tab === "pdf") {
    detailContent.innerHTML = `<iframe class="pdf-viewer" src="${API}/documents/${doc.id}/pdf" title="PDF Viewer"></iframe>`;
  } else if (tab === "po") {
    renderPOTab(poData);
  }
}

async function saveCorrections() {
  const btn = document.getElementById("save-po-btn");
  const docId = detailSection.dataset.docId;

  const corrections = [];

  // Collect PO meta corrections
  $$(".po-edit-input").forEach((input) => {
    if (input.value !== input.dataset.orig) {
      corrections.push({
        entity_type: "PO",
        entity_id: input.dataset.poId,
        field_name: input.dataset.field,
        new_value: input.value,
      });
    }
  });

  // Collect Item corrections
  $$(".item-edit-input").forEach((input) => {
    if (input.value !== input.dataset.orig) {
      corrections.push({
        entity_type: "ITEM",
        entity_id: input.dataset.itemId,
        field_name: input.dataset.field,
        new_value: input.value,
      });
    }
  });

  if (corrections.length === 0) {
    showUploadStatus("No changes to save.", "info");
    return;
  }

  try {
    btn.disabled = true;
    btn.textContent = "Saving...";

    const res = await fetch(`${API}/purchase-orders/correct`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        document_id: detailSection.dataset.docId,
        corrections: corrections,
      }),
    });

    if (!res.ok) throw new Error("Failed to save corrections");

    showUploadStatus("Corrections saved successfully!", "success");
    // Refresh data
    await openDocument(detailSection.dataset.docId, detailOrigin);
  } catch (err) {
    showUploadStatus(err.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Save Corrections";
  }
}

function renderPOTab(poData) {
  if (!poData) {
    detailContent.innerHTML = `<p class="empty-state">No Purchase Order data extracted from this document.</p>`;
    return;
  }

  const metaHTML = `
    <div class="po-edit-header" style="display:flex; justify-content:space-between; align-items:center; margin-bottom:1rem;">
      <h3 style="margin:0">Extracted PO Data</h3>
      <button id="save-po-btn" class="btn btn-primary" onclick="saveCorrections()">Save Corrections</button>
    </div>
    <div class="po-meta-grid">
      <div class="po-meta-item">
        <span class="label">Company</span>
        <input class="po-edit-input" data-po-id="${poData.id}" data-field="company_name" data-orig="${escapeHTML(poData.company_name || "")}" value="${escapeHTML(poData.company_name || "—")}">
      </div>
      <div class="po-meta-item">
        <span class="label">PO Number</span>
        <input class="po-edit-input" data-po-id="${poData.id}" data-field="po_number" data-orig="${escapeHTML(poData.po_number || "")}" value="${escapeHTML(poData.po_number || "—")}">
      </div>
      <div class="po-meta-item">
        <span class="label">Order Date</span>
        <input class="po-edit-input" data-po-id="${poData.id}" data-field="po_date" data-orig="${formatDate(poData.po_date) || ""}" value="${formatDate(poData.po_date) || "—"}">
      </div>
      <div class="po-meta-item">
        <span class="label">Line Items</span>
        <span class="value">${poData.items ? poData.items.length : 0}</span>
      </div>
    </div>
  `;

  let itemsHTML = "";
  if (poData.items && poData.items.length) {
    const rows = poData.items
      .map(
        (item) => `
        <tr>
          <td data-label="Item" class="item-cell">
            <input class="item-edit-input" data-item-id="${item.id}" data-field="item_name" data-orig="${escapeHTML(item.item_name || "")}" value="${escapeHTML(item.item_name || "—")}">
          </td>
          <td data-label="Description">
            <input class="item-edit-input" data-item-id="${item.id}" data-field="description" data-orig="${escapeHTML(item.description || "")}" value="${escapeHTML(item.description || "—")}">
          </td>
          <td data-label="Due Date">
            <input type="date" class="item-edit-input" data-item-id="${item.id}" data-field="due_date" data-orig="${formatDate(item.due_date) || ""}" value="${formatDate(item.due_date) || ""}">
          </td>
          <td data-label="Qty" style="text-align:center">
            <input class="item-edit-input" data-item-id="${item.id}" data-field="quantity" data-orig="${escapeHTML(item.quantity || "")}" value="${escapeHTML(item.quantity || "—")}">
          </td>
          <td data-label="Unit Price" style="text-align:right">
            <input class="item-edit-input" data-item-id="${item.id}" data-field="unit_price" data-orig="${formatPrice(item.unit_price) || ""}" value="${formatPrice(item.unit_price) || "—"}">
          </td>
        </tr>`,
      )
      .join("");

    itemsHTML = `
      <div class="table-wrapper">
        <table class="data-table">
          <thead>
            <tr>
              <th>Item</th>
              <th>Description</th>
              <th>Due Date</th>
              <th style="text-align:center">Qty</th>
              <th style="text-align:right">Unit Price</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
  } else {
    itemsHTML = `<p class="empty-state">No line items extracted.</p>`;
  }

  detailContent.innerHTML = metaHTML + itemsHTML;
}

// ---------------------------------------------------------------------------
//  Schedule view — data & filters
// ---------------------------------------------------------------------------

async function loadSchedule() {
  try {
    const res = await fetch(`${API}/schedule`);
    scheduleData = await res.json();
    renderScheduleSummary(scheduleData.summary);
    populateCompanyFilter(scheduleData.items);
    applyFiltersAndRender();
  } catch (err) {
    scheduleContainer.innerHTML = `<p class="empty-state">Failed to load schedule data.</p>`;
  }
}

function renderScheduleSummary(summary) {
  $("#stat-total-pos").textContent = summary.total_pos;
  $("#stat-total-items").textContent = summary.total_items;
  $("#stat-due-soon").textContent = summary.due_this_week;
  $("#stat-overdue").textContent = summary.overdue;
}

function populateCompanyFilter(items) {
  const prev = companyFilter.value;
  const companies = [
    ...new Set(items.map((i) => i.company_name).filter(Boolean)),
  ].sort();
  companyFilter.innerHTML =
    `<option value="all">All companies</option>` +
    companies
      .map((c) => `<option value="${escapeHTML(c)}">${escapeHTML(c)}</option>`)
      .join("");
  if (prev && prev !== "all") companyFilter.value = prev;
}

function getFilteredItems() {
  if (!scheduleData) return [];
  let items = scheduleData.items;

  const urg = urgencyFilter.value;
  if (urg !== "all") items = items.filter((i) => i.urgency === urg);

  const comp = companyFilter.value;
  if (comp !== "all") items = items.filter((i) => i.company_name === comp);

  const from = dateFrom.value;
  if (from) items = items.filter((i) => i.due_date && i.due_date >= from);

  const to = dateTo.value;
  if (to) items = items.filter((i) => i.due_date && i.due_date <= to);

  return items;
}

function applyFiltersAndRender() {
  const filtered = getFilteredItems();
  scheduleCount.textContent = `${filtered.length} item${filtered.length !== 1 ? "s" : ""}`;

  if (scheduleMode === "list") {
    renderScheduleTable(filtered);
  } else {
    renderCalendar(filtered);
  }
}

// ---------------------------------------------------------------------------
//  Schedule — List view
// ---------------------------------------------------------------------------

function renderScheduleTable(items) {
  if (!items.length) {
    const msg =
      scheduleData && scheduleData.items.length
        ? "No items match the current filters."
        : "No purchase order items yet. Upload a PDF to get started.";
    scheduleContainer.innerHTML = `<p class="empty-state">${msg}</p>`;
    return;
  }

  const rows = items
    .map((item) => {
      const urgencyLabel =
        {
          overdue: "Overdue",
          due_soon: "Due Soon",
          upcoming: "Upcoming",
          no_date: "No Date",
        }[item.urgency] || item.urgency;
      return `
        <tr>
          <td data-label="Status">
            <span class="urgency-badge urgency-badge--${item.urgency}">
              <span class="urgency-dot urgency-dot--${item.urgency}"></span>
              ${urgencyLabel}
            </span>
          </td>
          <td data-label="Due Date">${formatDate(item.due_date)}</td>
          <td data-label="Company" class="company-cell">${escapeHTML(item.company_name || "—")}</td>
          <td data-label="Description" class="desc-cell" title="${escapeHTML(item.description || "")}">${escapeHTML(item.description || "—")}</td>
          <td data-label="Qty" class="qty-cell">${escapeHTML(item.quantity || "—")}</td>
          <td data-label="Item" class="item-cell">${escapeHTML(item.item_name || "—")}</td>
          <td data-label="PO #">${escapeHTML(item.po_number || "—")}</td>
          <td data-label=""><span class="doc-link" data-doc-id="${item.document_id}">View PO</span></td>
        </tr>`;
    })
    .join("");

  scheduleContainer.innerHTML = `
    <div class="table-wrapper">
      <table class="schedule-table">
        <thead>
          <tr>
            <th>Status</th>
            <th>Due Date</th>
            <th>Company</th>
            <th>Description</th>
            <th class="qty-cell">Qty</th>
            <th>Item</th>
            <th>PO #</th>
            <th></th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;

  scheduleContainer.querySelectorAll(".doc-link").forEach((el) =>
    el.addEventListener("click", () => {
      $$(".nav-btn").forEach((b) =>
        b.classList.toggle("active", b.dataset.view === "documents"),
      );
      currentView = "documents";
      documentsView.hidden = false;
      scheduleView.hidden = true;
      openDocument(el.dataset.docId, "schedule");
    }),
  );
}

// ---------------------------------------------------------------------------
//  Schedule — Calendar view
// ---------------------------------------------------------------------------

function initCalMonth() {
  const now = new Date();
  calYear = now.getFullYear();
  calMonth = now.getMonth();
}
initCalMonth();

function renderCalendar(items) {
  const monthNames = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
  ];
  calTitle.textContent = `${monthNames[calMonth]} ${calYear}`;

  const firstDay = new Date(calYear, calMonth, 1);
  const lastDay = new Date(calYear, calMonth + 1, 0);
  const startDow = firstDay.getDay();
  const daysInMonth = lastDay.getDate();

  const todayStr = new Date().toISOString().slice(0, 10);

  const itemsByDate = {};
  items.forEach((it) => {
    if (!it.due_date) return;
    (itemsByDate[it.due_date] = itemsByDate[it.due_date] || []).push(it);
  });

  const dayHeaders = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  let html = dayHeaders
    .map((d) => `<div class="cal-day-header">${d}</div>`)
    .join("");

  for (let i = 0; i < startDow; i++) {
    html += `<div class="cal-cell cal-cell--empty"></div>`;
  }

  for (let day = 1; day <= daysInMonth; day++) {
    const dateStr = `${calYear}-${String(calMonth + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    const isToday = dateStr === todayStr;
    const dayItems = itemsByDate[dateStr] || [];

    const previewMax = 2;
    const previewHTML = dayItems
      .slice(0, previewMax)
      .map(
        (it) =>
          `<div class="cal-chip cal-chip--${it.urgency}">
        <span class="cal-chip-name">${escapeHTML(it.item_name)}</span>
      </div>`,
      )
      .join("");

    const moreCount = dayItems.length - previewMax;
    const moreHTML =
      moreCount > 0 ? `<div class="cal-more">+${moreCount} more</div>` : "";

    html += `
      <div class="cal-cell${isToday ? " cal-cell--today" : ""}${dayItems.length ? " cal-cell--has-items" : ""}"
           ${dayItems.length ? `data-date="${dateStr}"` : ""}>
        <span class="cal-day-num">${day}</span>
        <div class="cal-chips">${previewHTML}${moreHTML}</div>
      </div>`;
  }

  const totalCells = startDow + daysInMonth;
  const trailing = totalCells % 7 === 0 ? 0 : 7 - (totalCells % 7);
  for (let i = 0; i < trailing; i++) {
    html += `<div class="cal-cell cal-cell--empty"></div>`;
  }

  calGrid.innerHTML = html;

  // Click a day cell to expand it
  calGrid.querySelectorAll(".cal-cell[data-date]").forEach((cell) => {
    cell.addEventListener("click", (e) => {
      if (e.target.closest(".cal-popover")) return;
      const date = cell.dataset.date;
      const dayItems = itemsByDate[date] || [];
      openCalPopover(cell, date, dayItems);
    });
  });
}

let activePopover = null;

function closeCalPopover() {
  if (activePopover) {
    activePopover.remove();
    activePopover = null;
  }
  document.removeEventListener("click", onPopoverOutsideClick, true);
}

function onPopoverOutsideClick(e) {
  if (
    activePopover &&
    !activePopover.contains(e.target) &&
    !e.target.closest(".cal-cell[data-date]")
  ) {
    closeCalPopover();
  }
}

function openCalPopover(cell, dateStr, dayItems) {
  closeCalPopover();

  const label = formatDate(dateStr);
  const rows = dayItems
    .map((it) => {
      const urgencyLabel =
        {
          overdue: "Overdue",
          due_soon: "Due Soon",
          upcoming: "Upcoming",
          no_date: "No Date",
        }[it.urgency] || "";
      return `
      <div class="cal-pop-item cal-pop-item--${it.urgency}" data-doc-id="${it.document_id}">
        <div class="cal-pop-item-top">
          <span class="urgency-dot urgency-dot--${it.urgency}"></span>
          <span class="cal-pop-item-name">${escapeHTML(it.item_name)}</span>
          <span class="cal-pop-item-badge">${urgencyLabel}</span>
        </div>
        <div class="cal-pop-item-desc">${escapeHTML(it.description || "")}</div>
        <div class="cal-pop-item-meta">
          <span>${escapeHTML(it.company_name)}</span>
          <span>PO #${escapeHTML(it.po_number)}</span>
          ${it.quantity ? `<span>Qty ${escapeHTML(it.quantity)}</span>` : ""}
        </div>
      </div>`;
    })
    .join("");

  const pop = document.createElement("div");
  pop.className = "cal-popover";
  pop.innerHTML = `
    <div class="cal-pop-header">
      <span class="cal-pop-date">${label}</span>
      <span class="cal-pop-count">${dayItems.length} item${dayItems.length !== 1 ? "s" : ""}</span>
      <button class="cal-pop-close" title="Close">&times;</button>
    </div>
    <div class="cal-pop-body">${rows}</div>
  `;

  cell.style.position = "relative";
  cell.appendChild(pop);
  activePopover = pop;

  pop.querySelector(".cal-pop-close").addEventListener("click", (e) => {
    e.stopPropagation();
    closeCalPopover();
  });

  pop.querySelectorAll(".cal-pop-item").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      closeCalPopover();
      $$(".nav-btn").forEach((b) =>
        b.classList.toggle("active", b.dataset.view === "documents"),
      );
      currentView = "documents";
      documentsView.hidden = false;
      scheduleView.hidden = true;
      openDocument(el.dataset.docId, "schedule");
    });
  });

  setTimeout(
    () => document.addEventListener("click", onPopoverOutsideClick, true),
    0,
  );
}

$("#cal-prev").addEventListener("click", () => {
  calMonth--;
  if (calMonth < 0) {
    calMonth = 11;
    calYear--;
  }
  applyFiltersAndRender();
});
$("#cal-next").addEventListener("click", () => {
  calMonth++;
  if (calMonth > 11) {
    calMonth = 0;
    calYear++;
  }
  applyFiltersAndRender();
});
$("#cal-today").addEventListener("click", () => {
  initCalMonth();
  applyFiltersAndRender();
});

// ---------------------------------------------------------------------------
//  Schedule — View toggle & filter wiring
// ---------------------------------------------------------------------------

$$(".view-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    const mode = btn.dataset.mode;
    if (mode === scheduleMode) return;
    scheduleMode = mode;
    $$(".view-btn").forEach((b) =>
      b.classList.toggle("active", b.dataset.mode === mode),
    );
    listSection.hidden = mode !== "list";
    calSection.hidden = mode !== "calendar";
    applyFiltersAndRender();
  });
});

urgencyFilter.addEventListener("change", applyFiltersAndRender);
companyFilter.addEventListener("change", applyFiltersAndRender);
dateFrom.addEventListener("change", applyFiltersAndRender);
dateTo.addEventListener("change", applyFiltersAndRender);
refreshScheduleBtn.addEventListener("click", loadSchedule);
clearFiltersBtn.addEventListener("click", () => {
  urgencyFilter.value = "all";
  companyFilter.value = "all";
  dateFrom.value = "";
  dateTo.value = "";
  applyFiltersAndRender();
});

function formatDate(dateStr) {
  if (!dateStr) return "—";
  try {
    const d = new Date(dateStr + "T00:00:00");
    return d.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  } catch {
    return escapeHTML(dateStr);
  }
}

function formatPrice(val) {
  if (!val) return "—";
  const n = parseFloat(val);
  if (isNaN(n)) return escapeHTML(val);
  return (
    "$" +
    n.toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })
  );
}

// ---------------------------------------------------------------------------
//  Delete
// ---------------------------------------------------------------------------

async function deleteDocument(id) {
  if (!confirm("Delete this document?")) return;
  try {
    await fetch(`${API}/documents/${id}`, { method: "DELETE" });
    loadDocuments();
    if (currentView === "schedule") loadSchedule();
  } catch (err) {
    showUploadStatus("Delete failed.", "error");
  }
}

// ---------------------------------------------------------------------------
//  Back / Refresh
// ---------------------------------------------------------------------------

backBtn.addEventListener("click", () => {
  detailSection.hidden = true;
  uploadSection.hidden = false;
  docsSection.hidden = false;
});

backSchedBtn.addEventListener("click", () => {
  detailSection.hidden = true;
  uploadSection.hidden = false;
  docsSection.hidden = false;
  currentView = "schedule";
  $$(".nav-btn").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === "schedule"),
  );
  documentsView.hidden = true;
  scheduleView.hidden = false;
  loadSchedule();
});

refreshBtn.addEventListener("click", loadDocuments);

// ---------------------------------------------------------------------------
//  Utility
// ---------------------------------------------------------------------------

function escapeHTML(str) {
  if (!str) return "";
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// ---------------------------------------------------------------------------
//  Init
// ---------------------------------------------------------------------------

loadDocuments();
