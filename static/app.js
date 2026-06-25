const els = {
  position: document.getElementById("position"),
  method: document.getElementById("method"),
  imgOriginal: document.getElementById("img-original"),
  imgTranslated: document.getElementById("img-translated"),
  viewportOriginal: document.getElementById("viewport-original"),
  viewportTranslated: document.getElementById("viewport-translated"),
  panelOriginal: document.getElementById("panel-original"),
  panelTranslated: document.getElementById("panel-translated"),
  translatedStatus: document.getElementById("translated-status"),
  btnPrev: document.getElementById("btn-prev"),
  btnNext: document.getElementById("btn-next"),
  btnGoto: document.getElementById("btn-goto"),
  gotoInput: document.getElementById("goto-input"),
  toggleRowInfo: document.getElementById("toggle-row-info"),
  rowInfoContent: document.getElementById("row-info-content"),
  imageName: document.getElementById("image-name"),
};

let currentIndex = 0;
let total = 0;
let currentMethod = "manual";
let currentTranslatedBlobUrl = null;

const MAX_SCALE = 8;

// A SINGLE shared view drives both panels, so Original and Translated stay in
// sync. It's stored in normalized terms so it works even though the two images
// can differ in pixel size:
//   zoom     – multiple of each panel's own fit scale (>= 1 → can't shrink below fit)
//   cx, cy   – the image-space point (fraction 0..1 of the rotated bounding box)
//              that should sit at the viewport center
//   rotation – preview rotation in degrees (0/90/180/270), not yet baked to disk
const view = { zoom: 1, cx: 0.5, cy: 0.5, rotation: 0 };

function elsFor(target) {
  return target === "original"
    ? { img: els.imgOriginal, vp: els.viewportOriginal }
    : { img: els.imgTranslated, vp: els.viewportTranslated };
}

// Resolve the shared view against one panel's live viewport/image size: clamp
// scale to [fit, MAX_SCALE] and clamp the pan to the image edges (eog-style, no
// black border). Returns geometry, or null if that image isn't loaded yet.
function geom(target) {
  const { img, vp } = elsFor(target);
  if (!img.naturalWidth || !img.naturalHeight) return null;
  const vw = vp.clientWidth;
  const vh = vp.clientHeight;
  if (!vw || !vh) return null;

  const W = img.naturalWidth;
  const H = img.naturalHeight;
  const r = (((view.rotation % 360) + 360) % 360);
  const swap = r === 90 || r === 270;
  const bw = swap ? H : W; // rotated bounding-box dimensions
  const bh = swap ? W : H;

  const fit = Math.min(vw / bw, vh / bh); // scale at which the image just fits
  let scale = fit * view.zoom;
  scale = Math.min(Math.max(scale, fit), Math.max(fit, MAX_SCALE));

  const dispW = bw * scale;
  const dispH = bh * scale;

  // Put the shared center fraction at the viewport center, then clamp so an edge
  // can't be dragged inside the viewport. Center the axis if it already fits.
  let ox = dispW <= vw ? (vw - dispW) / 2 : vw / 2 - view.cx * dispW;
  let oy = dispH <= vh ? (vh - dispH) / 2 : vh / 2 - view.cy * dispH;
  if (dispW > vw) ox = Math.min(0, Math.max(vw - dispW, ox));
  if (dispH > vh) oy = Math.min(0, Math.max(vh - dispH, oy));

  return { vw, vh, W, H, r, bw, bh, fit, scale, ox, oy, dispW, dispH };
}

function renderOne(target) {
  const { img, vp } = elsFor(target);
  const g = geom(target);
  if (!g) return;
  // Place the bounding-box center; rotate/scale happen about the element center.
  const tx = g.ox + g.dispW / 2 - g.W / 2;
  const ty = g.oy + g.dispH / 2 - g.H / 2;
  img.style.transform = `translate(${tx}px, ${ty}px) rotate(${g.r}deg) scale(${g.scale})`;
  const pannable = g.dispW > g.vw + 0.5 || g.dispH > g.vh + 0.5;
  vp.classList.toggle("pannable", pannable);
}

// Always render both panels from the shared view → they stay in sync.
function render() {
  renderOne("original");
  renderOne("translated");
}

// Fit to viewport, keeping the current rotation.
function fitView() {
  view.zoom = 1;
  view.cx = 0.5;
  view.cy = 0.5;
  render();
}

// Full reset (also clears preview rotation), used when a new image is shown.
function resetView() {
  view.rotation = 0;
  fitView();
}

// Zoom by `factor` about a focal point in `target`'s viewport (default: its
// center), keeping the image content under that point fixed.
function zoomAt(target, factor, fx, fy) {
  const g = geom(target);
  if (!g) return;
  if (fx == null) fx = g.vw / 2;
  if (fy == null) fy = g.vh / 2;
  const newScale = Math.min(Math.max(g.scale * factor, g.fit), Math.max(g.fit, MAX_SCALE));
  // Fraction of the displayed image currently under the focal point.
  const fracX = (fx - g.ox) / g.dispW;
  const fracY = (fy - g.oy) / g.dispH;
  const newDispW = g.bw * newScale;
  const newDispH = g.bh * newScale;
  view.zoom = newScale / g.fit;
  view.cx = clamp01(fracX + (g.vw / 2 - fx) / newDispW);
  view.cy = clamp01(fracY + (g.vh / 2 - fy) / newDispH);
  render();
}

function panBy(target, dx, dy) {
  const g = geom(target);
  if (!g) return;
  view.cx = clamp01(view.cx - dx / g.dispW);
  view.cy = clamp01(view.cy - dy / g.dispH);
  render();
}

function rotateView(deg) {
  view.rotation = (((view.rotation + deg) % 360) + 360) % 360;
  fitView(); // refit the rotated image into the viewport
}

function clamp01(v) {
  return Math.min(1, Math.max(0, v));
}

let soloTarget = null; // null = show both, "original" / "translated" = that panel full-width

function toggleSolo(target) {
  soloTarget = soloTarget === target ? null : target;
  els.panelOriginal.classList.toggle("hidden", soloTarget === "translated");
  els.panelTranslated.classList.toggle("hidden", soloTarget === "original");
  document.querySelectorAll('[data-action="toggle-solo"]').forEach((b) => {
    b.classList.toggle("active", soloTarget !== null && b.dataset.target === soloTarget);
  });
  // Viewport widths changed; re-fit so the visible image fills the new space.
  fitView();
}

document.querySelectorAll(".controls button").forEach((btn) => {
  btn.addEventListener("click", () => {
    const target = btn.dataset.target;
    const action = btn.dataset.action;
    if (action === "toggle-solo") { toggleSolo(target); return; }
    if (action === "zoom-in") { zoomAt(target, 1.25); return; }
    if (action === "zoom-out") { zoomAt(target, 1 / 1.25); return; }
    if (action === "zoom-reset") { fitView(); return; }
    if (action === "rotate-left") { rotateView(-90); return; }
    if (action === "rotate-right") { rotateView(90); return; }
    if (action === "commit-rotate") { commitRotate(); return; }
    if (action === "ai-toggle") { toggleAIBox(); return; }
  });
});

[["original", els.viewportOriginal], ["translated", els.viewportTranslated]].forEach(([target, el]) => {
  el.addEventListener(
    "wheel",
    (e) => {
      // While the AI box is active the image is frozen; let the wheel scroll the
      // AI answer natively (don't preventDefault) instead of zooming.
      if (aiLocked()) return;
      e.preventDefault();
      const factor = e.deltaY < 0 ? 1.1 : 1 / 1.1;
      const rect = el.getBoundingClientRect();
      zoomAt(target, factor, e.clientX - rect.left, e.clientY - rect.top);
    },
    { passive: false }
  );

  // Kill the browser's native image drag-and-drop so it can't conflict with pan.
  el.addEventListener("dragstart", (e) => e.preventDefault());

  // Drag to pan.
  let dragging = false;
  let lastX = 0;
  let lastY = 0;
  el.addEventListener("pointerdown", (e) => {
    if (aiLocked()) return; // image is frozen while the AI box is active
    dragging = true;
    lastX = e.clientX;
    lastY = e.clientY;
    el.setPointerCapture(e.pointerId);
    el.classList.add("grabbing");
  });
  el.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    panBy(target, e.clientX - lastX, e.clientY - lastY);
    lastX = e.clientX;
    lastY = e.clientY;
  });
  const endDrag = () => {
    if (!dragging) return;
    dragging = false;
    el.classList.remove("grabbing");
  };
  el.addEventListener("pointerup", endDrag);
  el.addEventListener("pointercancel", endDrag);
});

// Fit when the anchor (original) image's real size is known; when the translated
// image arrives (possibly later), just render it into the shared view so an
// in-progress zoom/pan isn't reset.
els.imgOriginal.addEventListener("load", () => fitView());
els.imgTranslated.addEventListener("load", () => render());

// Keep the fit/clamp correct when the window (or a panel) is resized.
window.addEventListener("resize", render);

function showStatus(html) {
  els.translatedStatus.innerHTML = html;
  els.translatedStatus.classList.add("visible");
  els.imgTranslated.style.display = "none";
}

function hideStatus() {
  els.translatedStatus.classList.remove("visible");
}

async function loadOriginalImage() {
  els.imgOriginal.src = `/images/original/${currentIndex}?t=${Date.now()}`;
}

// Load whatever translation is cached for the current image. Works the same for
// every method now (manual mode caches a screenshot of Google's result), so the
// Translated panel always shows a real image with full zoom/pan/rotate.
//
// If nothing is cached yet in manual mode, automatically drive the browser to
// translate it (unless `autoTranslate` is false, which we pass after a just-run
// translation to avoid an infinite retry loop when capture didn't produce a file).
async function loadTranslatedImage({ autoTranslate = true } = {}) {
  showStatus(`<div>Đang tải bản dịch…</div>`);
  try {
    const res = await fetch(`/images/translated/${currentIndex}?t=${Date.now()}`);
    const contentType = res.headers.get("content-type") || "";

    if (res.ok && contentType.startsWith("image/")) {
      const blob = await res.blob();
      if (currentTranslatedBlobUrl) URL.revokeObjectURL(currentTranslatedBlobUrl);
      currentTranslatedBlobUrl = URL.createObjectURL(blob);
      els.imgTranslated.src = currentTranslatedBlobUrl;
      els.imgTranslated.style.display = "block";
      hideStatus();
      return;
    }

    const body = await res.json().catch(() => ({}));
    if (res.status === 202 && body.status === "pending") {
      // Manual mode, nothing cached yet.
      if (currentMethod === "manual" && autoTranslate) {
        return autoTranslateInBrowser();
      }
      showStatus(`
        <div>Chưa có bản dịch cho ảnh này.</div>
        <button id="btn-open-manual">Dịch trong trình duyệt</button>
      `);
      document.getElementById("btn-open-manual").addEventListener("click", autoTranslateInBrowser);
      return;
    }

    showStatus(`<div>Dịch thất bại: ${body.detail || res.statusText}</div>
      <button id="btn-retry">Thử lại</button>`);
    document.getElementById("btn-retry").addEventListener("click", () => loadTranslatedImage());
  } catch (err) {
    showStatus(`<div>Lỗi: ${err.message}</div><button id="btn-retry">Thử lại</button>`);
    document.getElementById("btn-retry").addEventListener("click", () => loadTranslatedImage());
  }
}

// Drive the headed Chrome to translate the current image, then pull the captured
// result back into the Translated panel.
async function autoTranslateInBrowser() {
  els.imgTranslated.style.display = "none";
  showStatus(`<div>Đang mở Google Translate và dịch ảnh…</div>
    <div class="hint">Giữ cửa sổ Chrome mở để Google nhận diện và dịch.</div>`);
  try {
    const res = await fetch(`/api/manual/auto/${currentIndex}`, { method: "POST" });
    const body = await res.json().catch(() => ({}));
    if (res.ok && body.status === "ok") {
      // autoTranslate:false → if capture produced no file, show a button instead
      // of re-triggering the browser in a loop.
      await loadTranslatedImage({ autoTranslate: false });
    } else {
      showStatus(`
        <div>Không dịch được trong trình duyệt: ${body.error || body.detail || res.statusText}</div>
        <button id="btn-open-manual">Thử lại</button>
      `);
      document.getElementById("btn-open-manual").addEventListener("click", autoTranslateInBrowser);
    }
  } catch (err) {
    showStatus(`<div>Lỗi mở trình duyệt: ${err.message}</div>
      <button id="btn-open-manual">Thử lại</button>`);
    document.getElementById("btn-open-manual").addEventListener("click", autoTranslateInBrowser);
  }
}

// Bake the current preview rotation into a saved copy of the original on disk,
// then re-translate the new orientation and reload both panels.
async function commitRotate() {
  const degrees = view.rotation;
  els.imgTranslated.style.display = "none";
  showStatus(`<div>Đang lưu ảnh đã xoay và dịch lại…</div>
    <div class="hint">Có thể mất vài giây nếu đang dùng trình duyệt.</div>`);
  try {
    const res = await fetch(`/api/rotate/${currentIndex}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ degrees }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      showStatus(`<div>Lưu/xoay thất bại: ${body.detail || res.statusText}</div>
        <button id="btn-retry-rotate">Thử lại</button>`);
      document.getElementById("btn-retry-rotate").addEventListener("click", commitRotate);
      return;
    }
    // The file on disk is now physically rotated, so drop the preview rotation
    // (otherwise the rotation would be applied twice).
    view.rotation = 0;
    await loadOriginalImage();
    await loadTranslatedImage();
  } catch (err) {
    showStatus(`<div>Lỗi: ${err.message}</div>
      <button id="btn-retry-rotate">Thử lại</button>`);
    document.getElementById("btn-retry-rotate").addEventListener("click", commitRotate);
  }
}

function updatePosition() {
  els.position.textContent = `${currentIndex + 1} / ${total}`;
  els.gotoInput.value = currentIndex + 1;
}

async function refreshFromState(state) {
  currentIndex = state.index;
  total = state.total;
  currentMethod = state.translation_method;
  updatePosition();
  els.method.value = state.translation_method;
  els.imageName.textContent = state.image_name || "";
  els.imageName.title = state.image_name || "";
  els.rowInfoContent.textContent = JSON.stringify(state.row, null, 2);
  resetView();
  await loadOriginalImage();
  await loadTranslatedImage();
}

async function fetchState() {
  const res = await fetch("/api/state");
  return res.json();
}

async function navigate(action, index) {
  const res = await fetch("/api/navigate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, index }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    alert(body.detail || "Navigation failed");
    return;
  }
  const state = await res.json();
  await refreshFromState(state);
}

els.btnPrev.addEventListener("click", () => navigate("prev"));
els.btnNext.addEventListener("click", () => navigate("next"));
els.btnGoto.addEventListener("click", () => {
  const n = parseInt(els.gotoInput.value, 10);
  if (!isNaN(n)) navigate("goto", n - 1);
});

document.addEventListener("keydown", (e) => {
  if (document.activeElement === els.gotoInput) return;
  if (e.key === "ArrowRight") navigate("next");
  if (e.key === "ArrowLeft") navigate("prev");
});

els.method.addEventListener("change", async () => {
  currentMethod = els.method.value;
  await fetch("/api/method", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ method: els.method.value }),
  });
  await loadTranslatedImage();
});

els.toggleRowInfo.addEventListener("click", () => {
  const visible = els.rowInfoContent.style.display !== "none";
  els.rowInfoContent.style.display = visible ? "none" : "block";
  els.toggleRowInfo.textContent = visible ? "Show row data ▾" : "Hide row data ▴";
});

// ---------------------------------------------------------------------------
// Startup confirmation screen: shown once per run before labeling. Surfaces the
// loaded config, warns about CSV images that are missing or that match more than
// one folder, and lets the user pick the folder for each ambiguous image.
// ---------------------------------------------------------------------------
const setupEls = {
  overlay: document.getElementById("setup-overlay"),
  summary: document.getElementById("setup-summary"),
  missing: document.getElementById("setup-missing"),
  conflicts: document.getElementById("setup-conflicts"),
  method: document.getElementById("setup-method-select"),
  confirm: document.getElementById("setup-confirm"),
};

// index -> chosen folder path, for images found in multiple folders.
const conflictChoices = {};

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]
  ));
}

function folderLabel(path) {
  // Last path segment is usually enough to tell folders apart at a glance.
  const parts = String(path).replace(/\/+$/, "").split("/");
  return parts[parts.length - 1] || path;
}

function folderRowHtml(value) {
  return `
    <div class="folder-row">
      <input type="text" class="folder-input" value="${esc(value)}" placeholder="/đường/dẫn/tới/thư-mục-ảnh">
      <button type="button" class="folder-remove" title="Xóa thư mục">✕</button>
    </div>`;
}

function wireFolderRemoveButtons() {
  document.querySelectorAll(".folder-remove").forEach((btn) => {
    btn.onclick = () => {
      const rows = document.querySelectorAll("#folder-list .folder-row");
      if (rows.length <= 1) {
        // Keep at least one row so the user always has somewhere to type.
        btn.previousElementSibling.value = "";
        return;
      }
      btn.parentElement.remove();
    };
  });
}

function collectConfigForm() {
  return {
    image_folders: [...document.querySelectorAll(".folder-input")]
      .map((i) => i.value.trim())
      .filter(Boolean),
    csv_path: document.getElementById("cfg-csv").value.trim(),
    image_name_column: document.getElementById("cfg-col").value.trim(),
    file_extension: document.getElementById("cfg-ext").value,
    original_language: document.getElementById("cfg-src").value.trim(),
    target_language: document.getElementById("cfg-tgt").value.trim(),
    ai_provider: document.getElementById("cfg-ai-provider").value,
    ai_model: document.getElementById("cfg-ai-model").value.trim(),
    ai_default_question: document.getElementById("cfg-ai-question").value,
  };
}

async function applyConfig() {
  const payload = collectConfigForm();
  if (!payload.image_folders.length) {
    alert("Cần ít nhất một thư mục ảnh.");
    return;
  }
  const btn = document.getElementById("cfg-apply");
  btn.disabled = true;
  btn.textContent = "Đang quét…";
  try {
    const res = await fetch("/api/config/update", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || res.statusText);
    renderSetup(body); // re-render with the rebuilt config + fresh scan
    // Pick up the just-saved AI provider/model/default question for the Ask AI box.
    aiQuestion = null;
    loadAIConfig();
  } catch (err) {
    alert("Không áp dụng được: " + err.message);
    btn.disabled = false;
    btn.textContent = "Áp dụng & quét lại";
  }
}

function renderSetup(setup) {
  const folderRows = (setup.image_folders.length ? setup.image_folders : [""])
    .map(folderRowHtml)
    .join("");
  const errHtml = setup.error
    ? `<div class="setup-error">⚠ ${esc(setup.error)}</div>`
    : "";
  setupEls.summary.innerHTML = `
    ${errHtml}
    <div class="setup-row"><span>Tổng số ảnh trong CSV:</span><b>${setup.total}</b></div>
    <div class="cfg-field">
      <label>Thư mục ảnh <span class="cfg-note">(tìm ảnh trong tất cả các thư mục)</span></label>
      <div id="folder-list">${folderRows}</div>
      <button type="button" id="folder-add" class="cfg-add">+ Thêm thư mục</button>
    </div>
    <div class="cfg-field">
      <label for="cfg-csv">File CSV</label>
      <input type="text" id="cfg-csv" value="${esc(setup.csv_path)}">
    </div>
    <div class="cfg-grid">
      <div class="cfg-field">
        <label for="cfg-col">Cột tên ảnh</label>
        <input type="text" id="cfg-col" value="${esc(setup.image_name_column)}">
      </div>
      <div class="cfg-field">
        <label for="cfg-ext">Đuôi file ảnh</label>
        <input type="text" id="cfg-ext" value="${esc(setup.file_extension)}" placeholder=".jpg">
      </div>
      <div class="cfg-field">
        <label for="cfg-src">Ngôn ngữ gốc</label>
        <input type="text" id="cfg-src" value="${esc(setup.original_language)}" placeholder="ja">
      </div>
      <div class="cfg-field">
        <label for="cfg-tgt">Ngôn ngữ đích</label>
        <input type="text" id="cfg-tgt" value="${esc(setup.target_language)}" placeholder="vi">
      </div>
    </div>
    <div class="cfg-field">
      <label>AI tham khảo <span class="cfg-note">(nút 🤖 AI ở panel Original)</span></label>
      <div class="cfg-grid">
        <div class="cfg-field">
          <label for="cfg-ai-provider">Provider</label>
          <select id="cfg-ai-provider">
            <option value="claude"${(setup.ai_provider || "claude") === "claude" ? " selected" : ""}>Claude (Anthropic)</option>
            <option value="openai"${setup.ai_provider === "openai" ? " selected" : ""}>OpenAI / Codex</option>
          </select>
        </div>
        <div class="cfg-field">
          <label for="cfg-ai-model">Model</label>
          <input type="text" id="cfg-ai-model" value="${esc(setup.ai_model || "")}" placeholder="claude-opus-4-8">
        </div>
      </div>
      <div class="cfg-field">
        <label for="cfg-ai-question">Câu hỏi mặc định</label>
        <textarea id="cfg-ai-question" rows="2">${esc(setup.ai_default_question || "")}</textarea>
      </div>
    </div>
    <button type="button" id="cfg-apply" class="cfg-add">Áp dụng & quét lại</button>
  `;
  document.getElementById("folder-add").onclick = () => {
    document.getElementById("folder-list").insertAdjacentHTML("beforeend", folderRowHtml(""));
    wireFolderRemoveButtons();
  };
  document.getElementById("cfg-apply").onclick = applyConfig;
  wireFolderRemoveButtons();

  // Missing originals --------------------------------------------------------
  const missing = setup.missing || [];
  if (missing.length === 0) {
    setupEls.missing.innerHTML = `<div class="setup-ok">✓ Tất cả ảnh trong CSV đều tìm thấy.</div>`;
  } else {
    const items = missing
      .map((m) => `<li>#${m.index + 1} — <code>${esc(m.filename)}</code></li>`)
      .join("");
    setupEls.missing.innerHTML = `
      <details class="setup-warn" open>
        <summary>⚠ ${missing.length} ảnh KHÔNG tìm thấy trong bất kỳ thư mục nào</summary>
        <p class="setup-hint">Các ảnh này sẽ không hiển thị được khi gán nhãn.</p>
        <ul class="setup-list">${items}</ul>
      </details>`;
  }

  // Conflicting originals (in more than one folder) --------------------------
  const conflicts = setup.conflicts || [];
  for (const key in conflictChoices) delete conflictChoices[key];
  if (conflicts.length === 0) {
    setupEls.conflicts.innerHTML = `<div class="setup-ok">✓ Không có ảnh nào trùng ở nhiều thư mục.</div>`;
  } else {
    // Quick "apply to all" by folder, plus a per-image override below.
    const allFolders = setup.image_folders;
    const quick = allFolders
      .map((f) => `<option value="${esc(f)}">${esc(folderLabel(f))}</option>`)
      .join("");
    const rows = conflicts
      .map((c) => {
        conflictChoices[c.index] = c.chosen; // default = first match
        const opts = c.candidates
          .map(
            (f) =>
              `<option value="${esc(f)}" title="${esc(f)}"${f === c.chosen ? " selected" : ""}>` +
              `${esc(folderLabel(f))}</option>`
          )
          .join("");
        return `
          <tr>
            <td>#${c.index + 1}</td>
            <td><code>${esc(c.filename)}</code></td>
            <td><select class="conflict-select" data-index="${c.index}">${opts}</select></td>
          </tr>`;
      })
      .join("");
    setupEls.conflicts.innerHTML = `
      <details class="setup-warn" open>
        <summary>⚠ ${conflicts.length} ảnh tồn tại ở NHIỀU thư mục — chọn thư mục để trích xuất</summary>
        <div class="setup-quick">
          <label>Áp dụng cho tất cả (nếu có):</label>
          <select id="conflict-apply-all"><option value="">— chọn —</option>${quick}</select>
        </div>
        <div class="setup-table-wrap">
          <table class="setup-table">
            <thead><tr><th>#</th><th>Tên ảnh</th><th>Thư mục</th></tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </details>`;

    setupEls.conflicts.querySelectorAll(".conflict-select").forEach((sel) => {
      sel.addEventListener("change", () => {
        conflictChoices[parseInt(sel.dataset.index, 10)] = sel.value;
      });
    });
    const applyAll = document.getElementById("conflict-apply-all");
    applyAll.addEventListener("change", () => {
      if (!applyAll.value) return;
      setupEls.conflicts.querySelectorAll(".conflict-select").forEach((sel) => {
        // Only switch images that actually have this folder as a candidate.
        if ([...sel.options].some((o) => o.value === applyAll.value)) {
          sel.value = applyAll.value;
          conflictChoices[parseInt(sel.dataset.index, 10)] = applyAll.value;
        }
      });
    });
  }

  setupEls.method.value = setup.translation_method;

  // Gate "start labeling" until the config is valid and the dataset has loaded.
  // Before that, the only useful action is editing the config and re-scanning.
  const ready = setup.ready !== false && setup.total > 0;
  setupEls.confirm.disabled = !ready;
  setupEls.confirm.title = ready ? "" : "Hãy áp dụng cấu hình hợp lệ trước khi bắt đầu.";
}

async function confirmSetup() {
  setupEls.confirm.disabled = true;
  setupEls.confirm.textContent = "Đang khởi tạo…";
  try {
    const res = await fetch("/api/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ method: setupEls.method.value, choices: conflictChoices }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || res.statusText);
    }
    const state = await res.json();
    setupEls.overlay.style.display = "none";
    await refreshFromState(state);
  } catch (err) {
    alert("Không thể bắt đầu: " + err.message);
    setupEls.confirm.disabled = false;
    setupEls.confirm.textContent = "Bắt đầu gán nhãn ▶";
  }
}

setupEls.confirm.addEventListener("click", confirmSetup);

(async function init() {
  loadAIConfig();
  const setup = await fetchSetup();
  renderSetup(setup);
})();

async function fetchSetup() {
  const res = await fetch("/api/setup");
  return res.json();
}

// ---------------------------------------------------------------------------
// AI reference box (Original panel). Drag/resize a box over a region, then
// "Ask AI" sends that crop + a question to a vision model and shows the answer.
// The box lives in viewport (screen) coordinates; on "Ask AI" we map its corners
// back into original-image pixels using the same geometry that renders the image.
// ---------------------------------------------------------------------------
const aiEls = {
  box: document.getElementById("ai-box"),
  ask: document.getElementById("ai-ask"),
  editQ: document.getElementById("ai-edit-q"),
  answer: document.getElementById("ai-answer"),
};

let aiDefaultQuestion = "";
let aiQuestion = null; // current (possibly user-edited) question; null until config loads

async function loadAIConfig() {
  try {
    const res = await fetch("/api/ai/config");
    if (!res.ok) return;
    const cfg = await res.json();
    aiDefaultQuestion = cfg.default_question || "";
    if (aiQuestion === null) aiQuestion = aiDefaultQuestion;
  } catch (_) {
    /* AI is optional; leave defaults empty */
  }
}

function aiToggleBtn() {
  return document.querySelector('[data-action="ai-toggle"]');
}

// True while the AI box is shown — used to freeze image zoom/pan so the box's
// screen position keeps mapping to the same image pixels.
function aiLocked() {
  return aiEls && aiEls.box && !aiEls.box.hidden;
}

// Disable every panel control button (zoom/rotate/solo/commit on both panels)
// except the AI toggle, so the shared view can't shift under the box.
function setControlsLocked(locked) {
  document.querySelectorAll(".controls button").forEach((btn) => {
    if (btn.dataset.action === "ai-toggle") return;
    btn.disabled = locked;
  });
}

function toggleAIBox() {
  if (aiEls.box.hidden) showAIBox();
  else hideAIBox();
}

function showAIBox() {
  const vp = els.viewportOriginal;
  const vw = vp.clientWidth;
  const vh = vp.clientHeight;
  const w = Math.max(80, vw * 0.4);
  const h = Math.max(60, vh * 0.3);
  setAIBoxRect((vw - w) / 2, (vh - h) / 2, w, h);
  aiEls.box.hidden = false;
  aiToggleBtn().classList.add("active");
  setControlsLocked(true);
}

function hideAIBox() {
  aiEls.box.hidden = true;
  hideAIAnswer();
  aiToggleBtn().classList.remove("active");
  setControlsLocked(false);
}

// Place/clamp the box (viewport-px coords) so it stays inside the viewport.
function setAIBoxRect(left, top, width, height) {
  const vp = els.viewportOriginal;
  width = Math.max(20, width);
  height = Math.max(20, height);
  left = Math.min(Math.max(0, left), Math.max(0, vp.clientWidth - width));
  top = Math.min(Math.max(0, top), Math.max(0, vp.clientHeight - height));
  aiEls.box.style.left = `${left}px`;
  aiEls.box.style.top = `${top}px`;
  aiEls.box.style.width = `${width}px`;
  aiEls.box.style.height = `${height}px`;
}

function resizeAIBox(dir, rect, dx, dy) {
  let { left, top, width, height } = rect;
  if (dir.includes("e")) width = rect.width + dx;
  if (dir.includes("s")) height = rect.height + dy;
  if (dir.includes("w")) { width = rect.width - dx; left = rect.left + dx; }
  if (dir.includes("n")) { height = rect.height - dy; top = rect.top + dy; }
  if (width < 20) { if (dir.includes("w")) left = rect.left + rect.width - 20; width = 20; }
  if (height < 20) { if (dir.includes("n")) top = rect.top + rect.height - 20; height = 20; }
  setAIBoxRect(left, top, width, height);
}

// Drag the body to move; drag a corner handle to resize. stopPropagation keeps
// the viewport's pan handler from also firing.
aiEls.box.addEventListener("pointerdown", (e) => {
  if (e.target.closest(".ai-box-toolbar") || e.target.closest(".ai-answer")) {
    e.stopPropagation();
    return;
  }
  e.stopPropagation();
  e.preventDefault();
  const dir = e.target.dataset.dir || null;
  const startX = e.clientX;
  const startY = e.clientY;
  const rect = {
    left: aiEls.box.offsetLeft,
    top: aiEls.box.offsetTop,
    width: aiEls.box.offsetWidth,
    height: aiEls.box.offsetHeight,
  };
  aiEls.box.setPointerCapture(e.pointerId);
  const onMove = (ev) => {
    const dx = ev.clientX - startX;
    const dy = ev.clientY - startY;
    if (dir) resizeAIBox(dir, rect, dx, dy);
    else setAIBoxRect(rect.left + dx, rect.top + dy, rect.width, rect.height);
  };
  const onUp = () => {
    aiEls.box.releasePointerCapture(e.pointerId);
    aiEls.box.removeEventListener("pointermove", onMove);
    aiEls.box.removeEventListener("pointerup", onUp);
    aiEls.box.removeEventListener("pointercancel", onUp);
  };
  aiEls.box.addEventListener("pointermove", onMove);
  aiEls.box.addEventListener("pointerup", onUp);
  aiEls.box.addEventListener("pointercancel", onUp);
});

// Invert the render transform: viewport point → original-image pixel.
function viewportToImage(vx, vy) {
  const g = geom("original");
  if (!g) return null;
  const cx0 = g.ox + g.dispW / 2;
  const cy0 = g.oy + g.dispH / 2;
  const dx = vx - cx0;
  const dy = vy - cy0;
  const rad = (-g.r * Math.PI) / 180;
  const cos = Math.cos(rad);
  const sin = Math.sin(rad);
  const ux = dx * cos - dy * sin;
  const uy = dx * sin + dy * cos;
  return { ix: ux / g.scale + g.W / 2, iy: uy / g.scale + g.H / 2 };
}

// The box's region in original-image pixels (axis-aligned bbox of its 4 mapped
// corners), clamped to the image. null if the box isn't over the image.
function aiBoxImageRect() {
  const g = geom("original");
  if (!g) return null;
  const left = aiEls.box.offsetLeft;
  const top = aiEls.box.offsetTop;
  const right = left + aiEls.box.offsetWidth;
  const bottom = top + aiEls.box.offsetHeight;
  const corners = [
    [left, top], [right, top], [left, bottom], [right, bottom],
  ].map(([x, y]) => viewportToImage(x, y));
  if (corners.some((c) => !c)) return null;

  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const c of corners) {
    minX = Math.min(minX, c.ix);
    minY = Math.min(minY, c.iy);
    maxX = Math.max(maxX, c.ix);
    maxY = Math.max(maxY, c.iy);
  }
  minX = Math.max(0, Math.min(g.W, minX));
  maxX = Math.max(0, Math.min(g.W, maxX));
  minY = Math.max(0, Math.min(g.H, minY));
  maxY = Math.max(0, Math.min(g.H, maxY));
  const w = maxX - minX;
  const h = maxY - minY;
  if (w < 2 || h < 2) return null;
  return { x: Math.round(minX), y: Math.round(minY), w: Math.round(w), h: Math.round(h) };
}

function showAIAnswer(text, isError) {
  aiEls.answer.innerHTML = "";
  const close = document.createElement("button");
  close.className = "ai-answer-close";
  close.type = "button";
  close.textContent = "✕";
  close.title = "Đóng";
  close.addEventListener("click", (e) => { e.stopPropagation(); hideAIAnswer(); });
  const body = document.createElement("div");
  body.className = "ai-answer-body";
  aiEls.answer.appendChild(close);
  aiEls.answer.appendChild(body);
  aiEls.answer.hidden = false;
  setAIAnswerBody(text, isError);
}

// Update just the answer text in-place, so streamed chunks can be rendered
// incrementally without rebuilding the close button each time.
function setAIAnswerBody(text, isError) {
  const body = aiEls.answer.querySelector(".ai-answer-body");
  if (!body) return;
  body.className = isError ? "ai-answer-body ai-answer-err" : "ai-answer-body";
  body.textContent = text;
}

function hideAIAnswer() {
  aiEls.answer.hidden = true;
  aiEls.answer.innerHTML = "";
}

function editQuestion() {
  const current = aiQuestion != null ? aiQuestion : aiDefaultQuestion;
  const next = window.prompt("Câu hỏi gửi cho AI:", current);
  if (next !== null) aiQuestion = next;
}

async function askAI() {
  const rect = aiBoxImageRect();
  if (!rect) {
    showAIAnswer("Vùng chọn không nằm trên ảnh — hãy kéo khung vào phần cần hỏi.", true);
    return;
  }
  const question = aiQuestion != null ? aiQuestion : aiDefaultQuestion;
  aiEls.ask.disabled = true;
  const prevLabel = aiEls.ask.textContent;
  aiEls.ask.textContent = "Đang hỏi…";
  showAIAnswer("Đang hỏi AI…", false);
  try {
    const res = await fetch("/api/ai/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ index: currentIndex, ...rect, question }),
    });
    if (!res.ok) {
      const detail = await res.text().catch(() => "");
      throw new Error(detail || res.statusText);
    }

    showAIAnswer("", false);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let acc = "";
    let failed = false;

    const handleLine = (line) => {
      const trimmed = line.trim();
      if (!trimmed) return;
      let msg;
      try {
        msg = JSON.parse(trimmed);
      } catch {
        return;
      }
      if (msg.error) {
        showAIAnswer("Lỗi: " + msg.error, true);
        failed = true;
      } else if (msg.delta) {
        acc += msg.delta;
        setAIAnswerBody(acc, false);
      }
    };

    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buffer.indexOf("\n")) >= 0) {
        handleLine(buffer.slice(0, nl));
        buffer = buffer.slice(nl + 1);
        if (failed) break;
      }
      if (failed) break;
    }
    if (!failed) {
      handleLine(buffer);
      if (!acc) setAIAnswerBody("(trống)", false);
    }
  } catch (err) {
    showAIAnswer("Lỗi: " + err.message, true);
  } finally {
    aiEls.ask.disabled = false;
    aiEls.ask.textContent = prevLabel;
  }
}

aiEls.ask.addEventListener("click", (e) => { e.stopPropagation(); askAI(); });
aiEls.editQ.addEventListener("click", (e) => { e.stopPropagation(); editQuestion(); });
