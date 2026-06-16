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
  });
});

[["original", els.viewportOriginal], ["translated", els.viewportTranslated]].forEach(([target, el]) => {
  el.addEventListener(
    "wheel",
    (e) => {
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

(async function init() {
  const state = await fetchState();
  await refreshFromState(state);
})();
