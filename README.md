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

- **`free`** — Local Tesseract OCR + the free/unofficial Google Translate
  endpoint (`deep-translator`). No account needed, lower accuracy,
  especially on stylised receipt fonts. Requires the Tesseract binary and
  the Japanese language pack:
  `sudo apt install tesseract-ocr tesseract-ocr-jpn tesseract-ocr-jpn-vert`
  `pip install pytesseract deep-translator`

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
