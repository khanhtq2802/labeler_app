# Invoice Labeler

A local web app for labeling Japanese invoice images: it walks through your
CSV in order, shows the original image next to an auto-translated version,
and caches translations so each image is only translated once.

## Setup

```bash
cd labeler_app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt   # see notes below if you only want one translation method

cp config.example.yaml config.yaml
# edit config.yaml: image_folder_path, csv_path, image_name_column, etc.

python app.py
# open http://127.0.0.1:8000
```

If `pip` isn't available: `sudo apt install python3-pip python3-venv` first.

## Translation methods (`translation_method` in config.yaml)

You can switch methods live from the dropdown in the UI; this just changes
how new (uncached) images get translated.

- **`google_cloud`** — Cloud Vision OCR + Cloud Translation. Best quality.
  Requires a GCP service account JSON with both APIs enabled, set via
  `google_cloud.credentials_json` (or the `GOOGLE_APPLICATION_CREDENTIALS`
  env var). Costs a small amount per image.
  `pip install google-cloud-vision google-cloud-translate`

- **`manual`** — There's no public API for Google Translate's image-overlay
  feature, so this mode drives a real Chrome window with Playwright: it opens
  `translate.google.com/?sl=…&tl=…&op=images` and auto-uploads the original
  image, letting Google translate it live on screen. That browser window *is*
  the translated view, so nothing is cached — each navigation re-uploads the
  current image. Requires `pip install playwright` plus a browser
  (`playwright install chromium`, or it reuses system Google Chrome).

Translations are cached on disk under `cache_folder` keyed by filename +
target language, so switching methods won't retranslate images you already
have cached. Delete the relevant file in the cache folder to force a
re-translation.

## AI reference (Ask AI)

In the **Original** panel, click **🤖 AI** to show a box you can drag and
resize over any region of the image. Hit **Ask AI** and the app crops just that
region and sends it (plus a question) to a vision model; the answer appears on
the box. **Edit question** changes the prompt for a single ask — handy for
"translate each part and explain" style questions on a specific stamp, total, or
handwritten note. While the box is active the image is frozen (no zoom/pan) so
the selection stays put, and the mouse wheel scrolls the answer instead.

Provider, model, and the default question are set under the `ai:` block in
`config.yaml` and can also be edited at runtime on the startup confirmation
screen.

By default, calls route through the **aiauth** proxy
(<https://github.com/khanhtq2802/aiauth>), which reuses your Claude Code / Codex
subscription token — **no API key needed**. To use it:

```bash
pip install anthropic        # (or 'openai' if ai.provider: openai)
# install aiauth from https://github.com/khanhtq2802/aiauth, then leave it running:
aiauth serve                 # http://127.0.0.1:8787
```

Prefer a direct API key instead? Set `ai.use_aiauth: false` and fill in
`ai.api_key` (and optionally `ai.base_url`) in `config.yaml`. See
`config.example.yaml` for all `ai:` options.

## Config fields

See `config.example.yaml` for the full list with comments. The important
ones: `image_folder_path`, `csv_path`, `image_name_column`, `file_extension`
(only appended if the CSV value has no extension already), `original_language`,
`target_language`.

## Notes / limitations

- The overlay is an approximation: detected text regions are painted white
  and the translation is drawn in their place. It works best on plain,
  light-background invoices/receipts (the typical case here) and can look
  rough on heavily styled or vertical text.
- `font_path` must point to a font that supports the target language's
  characters (defaults to DejaVu Sans, which covers Vietnamese).
- Your position in the CSV is remembered across restarts in `state.json`.
- Editing the CSV labels themselves is intentionally out of scope — this
  app is a read-only viewer/navigator; keep doing that part in your own tool.
