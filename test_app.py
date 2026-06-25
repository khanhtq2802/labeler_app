"""Tests for the streaming ask_ai endpoint added in this PR (app.py).

The endpoint POST /api/ai/ask now returns a StreamingResponse with NDJSON
instead of a plain JSON answer.  Each line is either:
  {"delta": "<text chunk>"}   for incremental text
  {"error": "<message>"}      on AIError or unexpected exception
"""

from __future__ import annotations

import io
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

def _make_jpeg_file(tmp_path: Path, width=20, height=20) -> Path:
    p = tmp_path / "test_img.jpg"
    img = Image.new("RGB", (width, height), color=(0, 100, 200))
    img.save(p, "JPEG")
    return p


def _parse_ndjson(content: bytes) -> list[dict]:
    """Split streaming response body into parsed JSON objects."""
    lines = content.decode("utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


# ---------------------------------------------------------------------------
# App client setup
#
# app.py runs _load_runtime_state() at import time, which tries to read
# config.yaml from disk.  We patch the filesystem-heavy functions so the
# module loads cleanly and we can control `dataset` and `cfg` in tests.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def app_module(tmp_path_factory):
    """Import app with _load_runtime_state patched so tests control state."""
    tmp = tmp_path_factory.mktemp("apptest")

    # Build a real-ish Config pointing at temp dirs
    from config import Config
    fake_cfg = Config(
        image_folders=[tmp],
        csv_path=tmp / "data.csv",
        image_name_column="image",
        file_extension=".jpg",
        original_language="ja",
        target_language="vi",
        translation_method="manual",
        cache_folder=tmp / "cache",
        rotated_folder=tmp / "rotated",
        state_file=tmp / "state.json",
        font_path="",
        google_cloud_credentials_json="",
        manual_translate_url="",
        server_host="127.0.0.1",
        server_port=8000,
        ai_provider="claude",
        ai_model="claude-test",
        ai_use_aiauth=False,
        ai_aiauth_host="127.0.0.1",
        ai_aiauth_port=8787,
        ai_api_key="test-key",
        ai_base_url="",
        ai_default_question="default Q",
        ai_max_tokens=512,
        base_dir=tmp,
        config_path=tmp / "config.yaml",
    )
    (tmp / "cache").mkdir()
    (tmp / "rotated").mkdir()

    # Patch _load_runtime_state to be a no-op and pre-set globals
    def _noop_load(path=None):
        pass

    # If already imported, reload; otherwise import fresh.
    if "app" in sys.modules:
        del sys.modules["app"]

    with patch("app._load_runtime_state", _noop_load):
        import app as app_mod

    # Manually set module-level state
    app_mod.cfg = fake_cfg
    app_mod.setup_error = None

    return app_mod, fake_cfg, tmp


@pytest.fixture()
def client_and_app(app_module):
    """Return (TestClient, app_module, cfg, tmp_dir)."""
    from fastapi.testclient import TestClient

    app_mod, cfg, tmp = app_module
    return TestClient(app_mod.app), app_mod, cfg, tmp


# ---------------------------------------------------------------------------
# Helpers to build a minimal mock dataset
# ---------------------------------------------------------------------------

def _make_mock_dataset(image_path: Path, length: int = 3) -> MagicMock:
    ds = MagicMock()
    ds.__len__ = MagicMock(return_value=length)
    ds.image_name.return_value = image_path.name
    ds.image_path.return_value = image_path
    ds.row.return_value = {}
    return ds


# ---------------------------------------------------------------------------
# Tests: 409 when dataset is None
# ---------------------------------------------------------------------------

class TestAskAIDatasetRequired:
    def test_returns_409_when_no_dataset(self, client_and_app):
        """Without a loaded dataset the endpoint must respond 409."""
        client, app_mod, cfg, tmp = client_and_app
        original_dataset = app_mod.dataset
        original_error = app_mod.setup_error
        try:
            app_mod.dataset = None
            app_mod.setup_error = "Chưa cấu hình"
            res = client.post("/api/ai/ask", json={
                "index": 0, "x": 0, "y": 0, "w": 10, "h": 10, "question": "q",
            })
            assert res.status_code == 409
        finally:
            app_mod.dataset = original_dataset
            app_mod.setup_error = original_error


# ---------------------------------------------------------------------------
# Tests: 404 for bad index and missing image
# ---------------------------------------------------------------------------

class TestAskAIValidation:
    def test_returns_404_for_negative_index(self, client_and_app):
        client, app_mod, cfg, tmp = client_and_app
        ds = _make_mock_dataset(tmp / "img.jpg")
        app_mod.dataset = ds
        res = client.post("/api/ai/ask", json={
            "index": -1, "x": 0, "y": 0, "w": 10, "h": 10, "question": "q",
        })
        assert res.status_code == 404

    def test_returns_404_for_index_at_length(self, client_and_app):
        client, app_mod, cfg, tmp = client_and_app
        ds = _make_mock_dataset(tmp / "img.jpg", length=3)
        app_mod.dataset = ds
        res = client.post("/api/ai/ask", json={
            "index": 3, "x": 0, "y": 0, "w": 10, "h": 10, "question": "q",
        })
        assert res.status_code == 404

    def test_returns_404_when_image_file_missing(self, client_and_app, tmp_path):
        client, app_mod, cfg, tmp = client_and_app
        nonexistent = tmp_path / "ghost.jpg"
        ds = _make_mock_dataset(nonexistent, length=3)
        app_mod.dataset = ds

        # working_original_path checks rotated first then dataset path; patch it
        with patch("app.working_original_path", return_value=nonexistent):
            res = client.post("/api/ai/ask", json={
                "index": 0, "x": 0, "y": 0, "w": 10, "h": 10, "question": "q",
            })
        assert res.status_code == 404


# ---------------------------------------------------------------------------
# Tests: happy path — streaming NDJSON deltas
# ---------------------------------------------------------------------------

class TestAskAIStreaming:
    def _setup_dataset(self, app_mod, tmp, image_path):
        ds = _make_mock_dataset(image_path, length=5)
        app_mod.dataset = ds
        return ds

    def test_returns_ndjson_content_type(self, client_and_app, tmp_path):
        client, app_mod, cfg, tmp = client_and_app
        image_file = _make_jpeg_file(tmp_path)
        self._setup_dataset(app_mod, tmp, image_file)

        import ai as ai_mod
        with patch.object(ai_mod, "ask_about_region_stream", return_value=iter(["hi"])):
            with patch("app.working_original_path", return_value=image_file):
                res = client.post("/api/ai/ask", json={
                    "index": 0, "x": 0, "y": 0, "w": 10, "h": 10, "question": "q",
                })

        assert res.status_code == 200
        assert "application/x-ndjson" in res.headers["content-type"]

    def test_single_chunk_produces_single_delta_line(self, client_and_app, tmp_path):
        client, app_mod, cfg, tmp = client_and_app
        image_file = _make_jpeg_file(tmp_path)
        self._setup_dataset(app_mod, tmp, image_file)

        import ai as ai_mod
        with patch.object(ai_mod, "ask_about_region_stream", return_value=iter(["hello"])):
            with patch("app.working_original_path", return_value=image_file):
                res = client.post("/api/ai/ask", json={
                    "index": 0, "x": 0, "y": 0, "w": 10, "h": 10, "question": "q",
                })

        objects = _parse_ndjson(res.content)
        assert objects == [{"delta": "hello"}]

    def test_multiple_chunks_produce_multiple_delta_lines(self, client_and_app, tmp_path):
        client, app_mod, cfg, tmp = client_and_app
        image_file = _make_jpeg_file(tmp_path)
        self._setup_dataset(app_mod, tmp, image_file)

        import ai as ai_mod
        chunks = ["foo", " bar", " baz"]
        with patch.object(ai_mod, "ask_about_region_stream", return_value=iter(chunks)):
            with patch("app.working_original_path", return_value=image_file):
                res = client.post("/api/ai/ask", json={
                    "index": 0, "x": 0, "y": 0, "w": 10, "h": 10, "question": "q",
                })

        objects = _parse_ndjson(res.content)
        assert objects == [{"delta": c} for c in chunks]

    def test_chunks_are_newline_delimited(self, client_and_app, tmp_path):
        """Each JSON object must be followed by a newline character."""
        client, app_mod, cfg, tmp = client_and_app
        image_file = _make_jpeg_file(tmp_path)
        self._setup_dataset(app_mod, tmp, image_file)

        import ai as ai_mod
        with patch.object(ai_mod, "ask_about_region_stream", return_value=iter(["a", "b"])):
            with patch("app.working_original_path", return_value=image_file):
                res = client.post("/api/ai/ask", json={
                    "index": 0, "x": 0, "y": 0, "w": 10, "h": 10, "question": "q",
                })

        raw = res.content.decode("utf-8")
        lines = raw.splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"delta": "a"}
        assert json.loads(lines[1]) == {"delta": "b"}

    def test_unicode_chunks_survive_serialization(self, client_and_app, tmp_path):
        """Vietnamese / Japanese text must round-trip through NDJSON intact."""
        client, app_mod, cfg, tmp = client_and_app
        image_file = _make_jpeg_file(tmp_path)
        self._setup_dataset(app_mod, tmp, image_file)

        import ai as ai_mod
        text = "こんにちは tôi là một đoạn văn bản"
        with patch.object(ai_mod, "ask_about_region_stream", return_value=iter([text])):
            with patch("app.working_original_path", return_value=image_file):
                res = client.post("/api/ai/ask", json={
                    "index": 0, "x": 0, "y": 0, "w": 10, "h": 10, "question": "q",
                })

        objects = _parse_ndjson(res.content)
        assert objects == [{"delta": text}]

    def test_question_forwarded_to_stream(self, client_and_app, tmp_path):
        """The question from the request body reaches ask_about_region_stream."""
        client, app_mod, cfg, tmp = client_and_app
        image_file = _make_jpeg_file(tmp_path)
        self._setup_dataset(app_mod, tmp, image_file)

        import ai as ai_mod
        captured = {}

        def fake_stream(c, path, box, question):
            captured["question"] = question
            return iter([])

        with patch.object(ai_mod, "ask_about_region_stream", side_effect=fake_stream):
            with patch("app.working_original_path", return_value=image_file):
                client.post("/api/ai/ask", json={
                    "index": 0, "x": 0, "y": 0, "w": 10, "h": 10,
                    "question": "custom question",
                })

        assert captured.get("question") == "custom question"

    def test_box_forwarded_correctly(self, client_and_app, tmp_path):
        """x, y, w, h from the request are forwarded as a box dict."""
        client, app_mod, cfg, tmp = client_and_app
        image_file = _make_jpeg_file(tmp_path)
        self._setup_dataset(app_mod, tmp, image_file)

        import ai as ai_mod
        captured = {}

        def fake_stream(c, path, box, question):
            captured["box"] = box
            return iter([])

        with patch.object(ai_mod, "ask_about_region_stream", side_effect=fake_stream):
            with patch("app.working_original_path", return_value=image_file):
                client.post("/api/ai/ask", json={
                    "index": 0, "x": 5, "y": 10, "w": 30, "h": 40, "question": "q",
                })

        box = captured.get("box", {})
        assert box["x"] == 5
        assert box["y"] == 10
        assert box["w"] == 30
        assert box["h"] == 40


# ---------------------------------------------------------------------------
# Tests: error paths — AIError and generic Exception
# ---------------------------------------------------------------------------

class TestAskAIErrorHandling:
    def _setup_dataset(self, app_mod, image_path, length=5):
        ds = _make_mock_dataset(image_path, length=length)
        app_mod.dataset = ds
        return ds

    def test_aierror_yields_error_ndjson(self, client_and_app, tmp_path):
        """An AIError from the stream is serialized as {"error": "..."} NDJSON."""
        client, app_mod, cfg, tmp = client_and_app
        image_file = _make_jpeg_file(tmp_path)
        self._setup_dataset(app_mod, image_file)

        import ai as ai_mod

        def raise_ai_error(c, path, box, q):
            raise ai_mod.AIError("something went wrong")
            yield  # make it a generator

        with patch.object(ai_mod, "ask_about_region_stream", side_effect=raise_ai_error):
            with patch("app.working_original_path", return_value=image_file):
                res = client.post("/api/ai/ask", json={
                    "index": 0, "x": 0, "y": 0, "w": 10, "h": 10, "question": "q",
                })

        assert res.status_code == 200
        objects = _parse_ndjson(res.content)
        assert len(objects) == 1
        assert "error" in objects[0]
        assert "something went wrong" in objects[0]["error"]

    def test_aierror_in_middle_of_stream(self, client_and_app, tmp_path):
        """AIError raised after some chunks are emitted still produces an error line."""
        client, app_mod, cfg, tmp = client_and_app
        image_file = _make_jpeg_file(tmp_path)
        self._setup_dataset(app_mod, image_file)

        import ai as ai_mod

        def partial_then_error(c, path, box, q):
            yield "first chunk"
            raise ai_mod.AIError("mid-stream failure")

        with patch.object(ai_mod, "ask_about_region_stream", side_effect=partial_then_error):
            with patch("app.working_original_path", return_value=image_file):
                res = client.post("/api/ai/ask", json={
                    "index": 0, "x": 0, "y": 0, "w": 10, "h": 10, "question": "q",
                })

        assert res.status_code == 200
        objects = _parse_ndjson(res.content)
        assert objects[0] == {"delta": "first chunk"}
        assert "error" in objects[-1]
        assert "mid-stream failure" in objects[-1]["error"]

    def test_generic_exception_yields_error_ndjson(self, client_and_app, tmp_path):
        """Non-AIError exceptions become {"error": "AI thất bại: ..."} lines."""
        client, app_mod, cfg, tmp = client_and_app
        image_file = _make_jpeg_file(tmp_path)
        self._setup_dataset(app_mod, image_file)

        import ai as ai_mod

        def boom(c, path, box, q):
            raise RuntimeError("unexpected boom")
            yield

        with patch.object(ai_mod, "ask_about_region_stream", side_effect=boom):
            with patch("app.working_original_path", return_value=image_file):
                res = client.post("/api/ai/ask", json={
                    "index": 0, "x": 0, "y": 0, "w": 10, "h": 10, "question": "q",
                })

        assert res.status_code == 200
        objects = _parse_ndjson(res.content)
        assert len(objects) == 1
        assert "error" in objects[0]
        # The generic handler prefix is "AI thất bại:"
        assert "AI thất bại" in objects[0]["error"]
        assert "unexpected boom" in objects[0]["error"]

    def test_empty_stream_returns_200_with_empty_body(self, client_and_app, tmp_path):
        """An empty generator produces a 200 response with no NDJSON lines."""
        client, app_mod, cfg, tmp = client_and_app
        image_file = _make_jpeg_file(tmp_path)
        self._setup_dataset(app_mod, image_file)

        import ai as ai_mod
        with patch.object(ai_mod, "ask_about_region_stream", return_value=iter([])):
            with patch("app.working_original_path", return_value=image_file):
                res = client.post("/api/ai/ask", json={
                    "index": 0, "x": 0, "y": 0, "w": 10, "h": 10, "question": "q",
                })

        assert res.status_code == 200
        objects = _parse_ndjson(res.content)
        assert objects == []

    def test_aierror_message_preserved_verbatim(self, client_and_app, tmp_path):
        """The exact AIError message string appears in the error field."""
        client, app_mod, cfg, tmp = client_and_app
        image_file = _make_jpeg_file(tmp_path)
        self._setup_dataset(app_mod, image_file)

        import ai as ai_mod
        msg = "Câu hỏi trống."

        def raise_it(c, path, box, q):
            raise ai_mod.AIError(msg)
            yield

        with patch.object(ai_mod, "ask_about_region_stream", side_effect=raise_it):
            with patch("app.working_original_path", return_value=image_file):
                res = client.post("/api/ai/ask", json={
                    "index": 0, "x": 0, "y": 0, "w": 10, "h": 10, "question": "q",
                })

        objects = _parse_ndjson(res.content)
        assert objects[0]["error"] == msg