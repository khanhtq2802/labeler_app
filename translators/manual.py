from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path

from PIL import Image

from .base import BaseTranslator, ManualTranslationPending

logger = logging.getLogger("labeler.manual")


class ManualTranslator(BaseTranslator):
    """Browser-automation mode.

    There is no public API for Google Translate's image-overlay feature, so we
    drive a real (headed) Chrome window with Playwright: navigate to Google
    Translate's image tab, "browse" the original file into the upload input via
    ``set_input_files`` (the one thing page JS can't do cross-origin), and let
    Google translate it on screen automatically.

    The visible Chrome window *is* the translated view, so nothing is cached:
    every navigation just re-uploads the current image into the same window.
    """

    def translate_image(self, image_path: Path) -> Image.Image:
        # Manual mode never produces a cached overlay image; the live browser
        # window is the result. Kept for interface compatibility.
        url = self.cfg.manual_translate_url.format(
            source=self.cfg.original_language, target=self.cfg.target_language
        )
        raise ManualTranslationPending(
            "Manual mode renders the translation in the browser window.",
            translate_url=url,
        )

    def translate_in_browser(self, image_path: Path, output_path: Path | None = None) -> dict:
        url = self.cfg.manual_translate_url.format(
            source=self.cfg.original_language, target=self.cfg.target_language
        )
        session = _get_session(self.cfg)
        try:
            session.translate(url, image_path, output_path)
            return {"status": "ok", "translate_url": url}
        except Exception as exc:  # surface to UI, keep app alive
            logger.exception("Browser translation failed")
            return {"status": "error", "translate_url": url, "error": str(exc)}

    def prefetch_in_browser(self, image_path: Path, output_path: Path) -> dict:
        """Translate-and-cache an image in a *second* tab, so the next image is
        ready before the user pages onto it. Runs after any in-flight main-tab
        translation (one browser thread), but the user reviews the current image
        meanwhile, so the next one is usually cached by the time they advance."""
        url = self.cfg.manual_translate_url.format(
            source=self.cfg.original_language, target=self.cfg.target_language
        )
        session = _get_session(self.cfg)
        try:
            session.prefetch(url, image_path, output_path)
            return {"status": "ok", "translate_url": url}
        except Exception as exc:
            logger.exception("Browser prefetch failed")
            return {"status": "error", "translate_url": url, "error": str(exc)}


# --- Persistent Playwright browser, owned by a single dedicated thread ---------
#
# FastAPI's sync endpoints run on arbitrary threadpool threads, but Playwright's
# sync API is pinned to the thread that created it. So we run all Playwright work
# on one long-lived worker thread and talk to it through a command queue.

_session: "_BrowserSession | None" = None
_session_lock = threading.Lock()


def _get_session(cfg) -> "_BrowserSession":
    global _session
    with _session_lock:
        if _session is None or not _session.alive:
            _session = _BrowserSession(cfg)
            _session.start()
        return _session


class _BrowserSession:
    def __init__(self, cfg):
        self.cfg = cfg
        self._commands: "queue.Queue[tuple]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.alive = True

    def start(self) -> None:
        self._thread.start()

    def translate(self, url: str, image_path: Path, output_path: Path | None = None) -> None:
        # Foreground (main tab) translation of the image the user is looking at.
        self._run_command("main", url, image_path, output_path, timeout=120)

    def prefetch(self, url: str, image_path: Path, output_path: Path | None = None) -> None:
        # Background (second tab) translation of the next image. Queued behind any
        # in-flight main translation, so allow a more generous deadline.
        self._run_command("prefetch", url, image_path, output_path, timeout=180)

    def _run_command(
        self, kind: str, url: str, image_path: Path, output_path: Path | None, timeout: float
    ) -> None:
        done = threading.Event()
        result: dict = {}
        out = str(output_path) if output_path else None
        self._commands.put((kind, url, str(image_path), out, done, result))
        # Capturing the result waits on Google's OCR to finish, so allow generous time.
        if not done.wait(timeout=timeout):
            raise RuntimeError("Browser did not respond in time.")
        if result.get("error"):
            raise RuntimeError(result["error"])

    def _run(self) -> None:
        from playwright.sync_api import sync_playwright

        profile_dir = self.cfg.cache_folder / "_playwright_profile"
        profile_dir.mkdir(parents=True, exist_ok=True)

        try:
            with sync_playwright() as pw:
                context = pw.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    channel="chrome",
                    headless=False,
                    # Google Translate's image OCR serves a degraded experience
                    # ("Can't detect text…") to browsers it flags as automated.
                    # Drop the automation switches and the navigator.webdriver
                    # flag so this Chrome behaves like a normal user window.
                    args=[
                        "--start-maximized",
                        "--disable-blink-features=AutomationControlled",
                    ],
                    ignore_default_args=["--enable-automation"],
                    no_viewport=True,
                )
                main_page = context.pages[0] if context.pages else context.new_page()
                prefetch_page = None  # second tab, opened lazily on first prefetch

                while True:
                    kind, url, image_path, output_path, done, result = self._commands.get()
                    try:
                        if kind == "prefetch":
                            if prefetch_page is None or prefetch_page.is_closed():
                                prefetch_page = context.new_page()
                            page = prefetch_page
                        else:
                            page = main_page
                        self._do_translate(page, url, image_path, output_path)
                    except Exception as exc:
                        result["error"] = str(exc)
                        logger.exception("%s command failed", kind)
                    finally:
                        done.set()
        except Exception:
            logger.exception("Browser session crashed")
        finally:
            self.alive = False

    @staticmethod
    def _do_translate(page, url: str, image_path: str, output_path: str | None = None) -> None:
        # Re-navigating is the most reliable way to reset between images.
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        _dismiss_consent(page)
        # The page has two file inputs: one for documents (pdf/docx/…) and one
        # for images. The document input rejects images with "Can't translate
        # this file format", so target the image input by its accept attribute.
        file_input = page.locator('input[type="file"][accept*="image"]').first
        file_input.wait_for(state="attached", timeout=15000)
        file_input.set_input_files(image_path)
        if output_path:
            _capture_result(page, output_path)


def _capture_result(page, output_path: str) -> None:
    """Wait for Google to render the translated image, then screenshot just that
    image into ``output_path``.

    Google Translate's image result has no documented markup, so this relies on
    heuristics: wait for the "Download translation" button to appear (it only
    shows once translation is done), then screenshot the largest visible <img>
    (the translated overlay). Both steps are best-effort with fallbacks.
    """
    import time
    from pathlib import Path as _Path

    _Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # 1) Wait for the "done" signal: a download-translation control. Poll a few
    #    possible labels against one ~30s deadline (instead of waiting the full
    #    timeout on each selector in turn, which could block for minutes).
    done_selectors = [
        'button:has-text("Download translation")',
        'button:has-text("Tải bản dịch xuống")',
        'button[aria-label*="Download" i]',
        'a:has-text("Download translation")',
    ]
    deadline = time.time() + 30
    while time.time() < deadline:
        if any(_safe_visible(page, sel) for sel in done_selectors):
            break
        page.wait_for_timeout(500)
    # Let the overlay finish painting even if no button matched.
    page.wait_for_timeout(1500)

    # 2) Pick the largest visible image on the page (the translated result).
    handle = None
    try:
        handle = page.evaluate_handle(
            """() => {
                let best = null, bestArea = 0;
                for (const im of document.querySelectorAll('img')) {
                    const r = im.getBoundingClientRect();
                    const area = r.width * r.height;
                    if (r.width > 100 && r.height > 100 && area > bestArea) {
                        bestArea = area; best = im;
                    }
                }
                return best;
            }"""
        )
    except Exception:
        handle = None

    element = handle.as_element() if handle else None
    try:
        if element:
            element.screenshot(path=output_path, type="jpeg", quality=92)
        else:
            # Fallback: whole page, better than nothing.
            page.screenshot(path=output_path, type="jpeg", quality=92)
    except Exception:
        logger.exception("Failed to screenshot translation result")
        raise


def _safe_visible(page, selector: str) -> bool:
    try:
        return page.locator(selector).first.is_visible()
    except Exception:
        return False


def _dismiss_consent(page) -> None:
    # Google's cookie/consent wall, when it appears. Best-effort; the persistent
    # profile remembers the choice so this normally only runs once.
    for label in ("Accept all", "I agree", "Tôi đồng ý", "Chấp nhận tất cả"):
        try:
            btn = page.get_by_role("button", name=label)
            if btn.count() > 0:
                btn.first.click(timeout=2000)
                page.wait_for_timeout(500)
                return
        except Exception:
            continue
