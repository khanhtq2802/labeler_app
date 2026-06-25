"""Tests for the streaming AI functions added in this PR.

Covers:
  - _stream_claude
  - _stream_openai
  - ask_about_region_stream
"""

from __future__ import annotations

import io
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from PIL import Image

# ---------------------------------------------------------------------------
# Helpers to build a minimal Config without touching the filesystem
# ---------------------------------------------------------------------------

def _make_config(provider="claude", default_question="default Q"):
    """Return a Config-like namespace with only the fields ai.py accesses."""
    from config import Config
    return Config(
        image_folders=[],
        csv_path=Path(""),
        image_name_column="",
        file_extension="",
        original_language="ja",
        target_language="vi",
        translation_method="manual",
        cache_folder=Path("/tmp/cache"),
        rotated_folder=Path("/tmp/rotated"),
        state_file=Path("/tmp/state.json"),
        font_path="",
        google_cloud_credentials_json="",
        manual_translate_url="",
        server_host="127.0.0.1",
        server_port=8000,
        ai_provider=provider,
        ai_model="claude-test-model",
        ai_use_aiauth=False,
        ai_aiauth_host="127.0.0.1",
        ai_aiauth_port=8787,
        ai_api_key="test-key",
        ai_base_url="",
        ai_default_question=default_question,
        ai_max_tokens=512,
        base_dir=Path("/tmp"),
        config_path=Path("/tmp/config.yaml"),
    )


def _make_jpeg_bytes(width=10, height=10) -> bytes:
    """Create minimal in-memory JPEG bytes."""
    buf = io.BytesIO()
    img = Image.new("RGB", (width, height), color=(255, 0, 0))
    img.save(buf, "JPEG")
    return buf.getvalue()


def _make_jpeg_file(tmp_path: Path, width=20, height=20) -> Path:
    """Write a small JPEG to a temp path and return it."""
    p = tmp_path / "test.jpg"
    img = Image.new("RGB", (width, height), color=(0, 128, 0))
    img.save(p, "JPEG")
    return p


# ---------------------------------------------------------------------------
# _stream_claude
# ---------------------------------------------------------------------------

class TestStreamClaude:
    """Tests for ai._stream_claude."""

    def _make_stream_ctx(self, chunks):
        """Build a mock context manager whose text_stream yields *chunks*."""
        stream = MagicMock()
        stream.__iter__ = MagicMock(return_value=iter(chunks))
        stream.text_stream = chunks

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=stream)
        ctx.__exit__ = MagicMock(return_value=False)
        return ctx

    def test_yields_text_chunks(self):
        """_stream_claude should yield each text chunk from text_stream."""
        import ai

        cfg = _make_config()
        mock_anthropic_mod = types.ModuleType("anthropic")
        mock_client = MagicMock()
        stream_ctx = self._make_stream_ctx(["Hello", " world", "!"])
        mock_client.messages.stream.return_value = stream_ctx
        mock_anthropic_mod.Anthropic = MagicMock(return_value=mock_client)

        with patch.dict(sys.modules, {"anthropic": mock_anthropic_mod}):
            result = list(ai._stream_claude(cfg, "base64data", "What is this?"))

        assert result == ["Hello", " world", "!"]

    def test_passes_correct_model_and_tokens(self):
        """_stream_claude should pass cfg.ai_model and cfg.ai_max_tokens to the SDK."""
        import ai

        cfg = _make_config()
        mock_anthropic_mod = types.ModuleType("anthropic")
        mock_client = MagicMock()
        stream_ctx = self._make_stream_ctx(["ok"])
        mock_client.messages.stream.return_value = stream_ctx
        mock_anthropic_mod.Anthropic = MagicMock(return_value=mock_client)

        with patch.dict(sys.modules, {"anthropic": mock_anthropic_mod}):
            list(ai._stream_claude(cfg, "b64", "q"))

        call_kwargs = mock_client.messages.stream.call_args
        assert call_kwargs.kwargs["model"] == "claude-test-model"
        assert call_kwargs.kwargs["max_tokens"] == 512

    def test_embeds_b64_and_question_in_message(self):
        """The image base64 and question text appear in the user message."""
        import ai

        cfg = _make_config()
        mock_anthropic_mod = types.ModuleType("anthropic")
        mock_client = MagicMock()
        stream_ctx = self._make_stream_ctx([])
        mock_client.messages.stream.return_value = stream_ctx
        mock_anthropic_mod.Anthropic = MagicMock(return_value=mock_client)

        b64 = "TESTBASE64=="
        question = "describe this image"

        with patch.dict(sys.modules, {"anthropic": mock_anthropic_mod}):
            list(ai._stream_claude(cfg, b64, question))

        messages = mock_client.messages.stream.call_args.kwargs["messages"]
        assert messages[0]["role"] == "user"
        content = messages[0]["content"]
        image_block = content[0]
        text_block = content[1]
        assert image_block["type"] == "image"
        assert image_block["source"]["data"] == b64
        assert image_block["source"]["type"] == "base64"
        assert image_block["source"]["media_type"] == "image/jpeg"
        assert text_block["type"] == "text"
        assert text_block["text"] == question

    def test_raises_aierror_when_anthropic_not_installed(self):
        """_stream_claude must raise AIError (not ImportError) when SDK is absent."""
        import ai

        cfg = _make_config()
        # Remove anthropic from sys.modules so the import inside the function fails
        with patch.dict(sys.modules, {"anthropic": None}):
            with pytest.raises(ai.AIError, match="anthropic"):
                list(ai._stream_claude(cfg, "b64", "q"))

    def test_raises_aierror_on_sdk_exception(self):
        """SDK errors during streaming are wrapped in AIError."""
        import ai

        cfg = _make_config()
        mock_anthropic_mod = types.ModuleType("anthropic")
        mock_client = MagicMock()

        # Simulate the stream raising mid-flight
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(side_effect=RuntimeError("connection refused"))
        ctx.__exit__ = MagicMock(return_value=False)
        mock_client.messages.stream.return_value = ctx
        mock_anthropic_mod.Anthropic = MagicMock(return_value=mock_client)

        with patch.dict(sys.modules, {"anthropic": mock_anthropic_mod}):
            with pytest.raises(ai.AIError):
                list(ai._stream_claude(cfg, "b64", "q"))

    def test_uses_aiauth_base_url_when_enabled(self):
        """When ai_use_aiauth=True the Anthropic client gets the proxy base_url."""
        import ai

        from config import Config
        cfg = Config(
            image_folders=[], csv_path=Path(""), image_name_column="",
            file_extension="", original_language="ja", target_language="vi",
            translation_method="manual", cache_folder=Path("/tmp"),
            rotated_folder=Path("/tmp"), state_file=Path("/tmp"),
            font_path="", google_cloud_credentials_json="",
            manual_translate_url="", server_host="127.0.0.1", server_port=8000,
            ai_provider="claude", ai_model="claude-test-model",
            ai_use_aiauth=True, ai_aiauth_host="localhost", ai_aiauth_port=9999,
            ai_api_key="", ai_base_url="", ai_default_question="",
            ai_max_tokens=256, base_dir=Path("/tmp"), config_path=Path("/tmp/config.yaml"),
        )
        mock_anthropic_mod = types.ModuleType("anthropic")
        mock_client = MagicMock()
        stream_ctx = MagicMock()
        stream_ctx.__enter__ = MagicMock(return_value=MagicMock(text_stream=[]))
        stream_ctx.__exit__ = MagicMock(return_value=False)
        mock_client.messages.stream.return_value = stream_ctx
        mock_anthropic_cls = MagicMock(return_value=mock_client)
        mock_anthropic_mod.Anthropic = mock_anthropic_cls

        with patch.dict(sys.modules, {"anthropic": mock_anthropic_mod}):
            list(ai._stream_claude(cfg, "b64", "q"))

        mock_anthropic_cls.assert_called_once()
        call_kwargs = mock_anthropic_cls.call_args.kwargs
        assert call_kwargs["api_key"] == "aiauth-local"
        assert call_kwargs["base_url"] == "http://localhost:9999/anthropic"

    def test_empty_stream_yields_nothing(self):
        """A stream with zero chunks should produce an empty list without error."""
        import ai

        cfg = _make_config()
        mock_anthropic_mod = types.ModuleType("anthropic")
        mock_client = MagicMock()
        stream_ctx = self._make_stream_ctx([])
        mock_client.messages.stream.return_value = stream_ctx
        mock_anthropic_mod.Anthropic = MagicMock(return_value=mock_client)

        with patch.dict(sys.modules, {"anthropic": mock_anthropic_mod}):
            result = list(ai._stream_claude(cfg, "b64", "q"))

        assert result == []


# ---------------------------------------------------------------------------
# _stream_openai
# ---------------------------------------------------------------------------

class TestStreamOpenAI:
    """Tests for ai._stream_openai."""

    def _make_event(self, event_type, delta=""):
        ev = MagicMock()
        ev.type = event_type
        ev.delta = delta
        return ev

    def _make_stream_ctx(self, events):
        ctx = MagicMock()
        stream_obj = MagicMock()
        stream_obj.__iter__ = MagicMock(return_value=iter(events))
        ctx.__enter__ = MagicMock(return_value=stream_obj)
        ctx.__exit__ = MagicMock(return_value=False)
        return ctx

    def test_yields_delta_for_output_text_events(self):
        """Only response.output_text.delta events should produce yield values."""
        import ai

        cfg = _make_config(provider="openai")
        mock_openai_mod = types.ModuleType("openai")
        mock_client = MagicMock()

        events = [
            self._make_event("response.created"),
            self._make_event("response.output_text.delta", "Hello"),
            self._make_event("response.output_text.delta", " there"),
            self._make_event("response.done"),
        ]
        mock_client.responses.stream.return_value = self._make_stream_ctx(events)
        mock_openai_mod.OpenAI = MagicMock(return_value=mock_client)

        with patch.dict(sys.modules, {"openai": mock_openai_mod}):
            result = list(ai._stream_openai(cfg, "b64", "question"))

        assert result == ["Hello", " there"]

    def test_ignores_non_delta_events(self):
        """Events whose type is not response.output_text.delta are silently ignored."""
        import ai

        cfg = _make_config(provider="openai")
        mock_openai_mod = types.ModuleType("openai")
        mock_client = MagicMock()

        events = [
            self._make_event("response.created"),
            self._make_event("response.in_progress"),
            self._make_event("response.done"),
        ]
        mock_client.responses.stream.return_value = self._make_stream_ctx(events)
        mock_openai_mod.OpenAI = MagicMock(return_value=mock_client)

        with patch.dict(sys.modules, {"openai": mock_openai_mod}):
            result = list(ai._stream_openai(cfg, "b64", "question"))

        assert result == []

    def test_embeds_b64_as_data_url_in_message(self):
        """The image must be embedded as a data:image/jpeg;base64,... URL."""
        import ai

        cfg = _make_config(provider="openai")
        mock_openai_mod = types.ModuleType("openai")
        mock_client = MagicMock()
        mock_client.responses.stream.return_value = self._make_stream_ctx([])
        mock_openai_mod.OpenAI = MagicMock(return_value=mock_client)

        b64 = "MYDATA=="
        question = "what is this?"

        with patch.dict(sys.modules, {"openai": mock_openai_mod}):
            list(ai._stream_openai(cfg, b64, question))

        call_kwargs = mock_client.responses.stream.call_args.kwargs
        input_msg = call_kwargs["input"][0]
        assert input_msg["role"] == "user"
        content = input_msg["content"]
        text_part = content[0]
        image_part = content[1]
        assert text_part["type"] == "input_text"
        assert text_part["text"] == question
        assert image_part["type"] == "input_image"
        assert image_part["image_url"] == f"data:image/jpeg;base64,{b64}"

    def test_raises_aierror_when_openai_not_installed(self):
        """_stream_openai must raise AIError when the openai SDK is missing."""
        import ai

        cfg = _make_config(provider="openai")
        with patch.dict(sys.modules, {"openai": None}):
            with pytest.raises(ai.AIError, match="openai"):
                list(ai._stream_openai(cfg, "b64", "q"))

    def test_raises_aierror_on_sdk_exception(self):
        """SDK errors during streaming are wrapped in AIError."""
        import ai

        cfg = _make_config(provider="openai")
        mock_openai_mod = types.ModuleType("openai")
        mock_client = MagicMock()

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(side_effect=ConnectionError("proxy down"))
        ctx.__exit__ = MagicMock(return_value=False)
        mock_client.responses.stream.return_value = ctx
        mock_openai_mod.OpenAI = MagicMock(return_value=mock_client)

        with patch.dict(sys.modules, {"openai": mock_openai_mod}):
            with pytest.raises(ai.AIError):
                list(ai._stream_openai(cfg, "b64", "q"))

    def test_uses_aiauth_base_url_when_enabled(self):
        """When ai_use_aiauth=True, OpenAI client gets the proxy base_url."""
        import ai

        from config import Config
        cfg = Config(
            image_folders=[], csv_path=Path(""), image_name_column="",
            file_extension="", original_language="ja", target_language="vi",
            translation_method="manual", cache_folder=Path("/tmp"),
            rotated_folder=Path("/tmp"), state_file=Path("/tmp"),
            font_path="", google_cloud_credentials_json="",
            manual_translate_url="", server_host="127.0.0.1", server_port=8000,
            ai_provider="openai", ai_model="gpt-test",
            ai_use_aiauth=True, ai_aiauth_host="localhost", ai_aiauth_port=9999,
            ai_api_key="", ai_base_url="", ai_default_question="",
            ai_max_tokens=256, base_dir=Path("/tmp"), config_path=Path("/tmp/config.yaml"),
        )
        mock_openai_mod = types.ModuleType("openai")
        mock_client = MagicMock()
        mock_client.responses.stream.return_value = self._make_stream_ctx([])
        mock_openai_cls = MagicMock(return_value=mock_client)
        mock_openai_mod.OpenAI = mock_openai_cls

        with patch.dict(sys.modules, {"openai": mock_openai_mod}):
            list(ai._stream_openai(cfg, "b64", "q"))

        call_kwargs = mock_openai_cls.call_args.kwargs
        assert call_kwargs["api_key"] == "aiauth-local"
        assert call_kwargs["base_url"] == "http://localhost:9999/openai/v1"

    def test_passes_model_to_responses_stream(self):
        """cfg.ai_model must be forwarded to client.responses.stream."""
        import ai

        cfg = _make_config(provider="openai")
        mock_openai_mod = types.ModuleType("openai")
        mock_client = MagicMock()
        mock_client.responses.stream.return_value = self._make_stream_ctx([])
        mock_openai_mod.OpenAI = MagicMock(return_value=mock_client)

        with patch.dict(sys.modules, {"openai": mock_openai_mod}):
            list(ai._stream_openai(cfg, "b64", "q"))

        call_kwargs = mock_client.responses.stream.call_args.kwargs
        assert call_kwargs["model"] == "claude-test-model"


# ---------------------------------------------------------------------------
# ask_about_region_stream
# ---------------------------------------------------------------------------

class TestAskAboutRegionStream:
    """Tests for the public ai.ask_about_region_stream function."""

    def test_raises_aierror_for_empty_question_and_no_default(self):
        """An empty question with a blank default question raises AIError."""
        import ai

        cfg = _make_config(default_question="")
        with pytest.raises(ai.AIError, match="Câu hỏi trống"):
            list(ai.ask_about_region_stream(cfg, Path("/nonexistent"), {}, ""))

    def test_raises_aierror_for_whitespace_question(self):
        """A whitespace-only question (no default) raises AIError."""
        import ai

        cfg = _make_config(default_question="")
        with pytest.raises(ai.AIError, match="Câu hỏi trống"):
            list(ai.ask_about_region_stream(cfg, Path("/nonexistent"), {}, "   "))

    def test_uses_default_question_when_question_is_empty(self, tmp_path):
        """When question is '', cfg.ai_default_question is used instead."""
        import ai

        image_file = _make_jpeg_file(tmp_path)
        cfg = _make_config(provider="claude", default_question="Tell me about this")

        with patch.object(ai, "_stream_claude") as mock_stream:
            mock_stream.return_value = iter(["answer"])
            result = list(ai.ask_about_region_stream(
                cfg, image_file,
                {"x": 0, "y": 0, "w": 10, "h": 10},
                "",
            ))

        assert result == ["answer"]
        # The question forwarded to _stream_claude should be the default
        call_args = mock_stream.call_args
        assert call_args.args[2] == "Tell me about this"

    def test_delegates_to_stream_claude_for_claude_provider(self, tmp_path):
        """When provider is 'claude', _stream_claude is called."""
        import ai

        image_file = _make_jpeg_file(tmp_path)
        cfg = _make_config(provider="claude")

        with patch.object(ai, "_stream_claude", return_value=iter(["chunk1", "chunk2"])) as mock_c:
            with patch.object(ai, "_stream_openai") as mock_o:
                result = list(ai.ask_about_region_stream(
                    cfg, image_file,
                    {"x": 0, "y": 0, "w": 10, "h": 10},
                    "some question",
                ))

        mock_c.assert_called_once()
        mock_o.assert_not_called()
        assert result == ["chunk1", "chunk2"]

    def test_delegates_to_stream_openai_for_openai_provider(self, tmp_path):
        """When provider is 'openai', _stream_openai is called."""
        import ai

        image_file = _make_jpeg_file(tmp_path)
        cfg = _make_config(provider="openai")

        with patch.object(ai, "_stream_openai", return_value=iter(["A", "B"])) as mock_o:
            with patch.object(ai, "_stream_claude") as mock_c:
                result = list(ai.ask_about_region_stream(
                    cfg, image_file,
                    {"x": 0, "y": 0, "w": 10, "h": 10},
                    "q",
                ))

        mock_o.assert_called_once()
        mock_c.assert_not_called()
        assert result == ["A", "B"]

    def test_passes_encoded_image_to_stream_function(self, tmp_path):
        """The base64-encoded crop is passed as the second positional arg."""
        import ai
        import base64

        image_file = _make_jpeg_file(tmp_path, width=40, height=40)
        cfg = _make_config(provider="claude")

        captured = {}

        def fake_stream(c, b64, q):
            captured["b64"] = b64
            return iter([])

        with patch.object(ai, "_stream_claude", side_effect=fake_stream):
            list(ai.ask_about_region_stream(
                cfg, image_file,
                {"x": 0, "y": 0, "w": 20, "h": 20},
                "q",
            ))

        # Should be valid base64
        decoded = base64.b64decode(captured["b64"])
        assert len(decoded) > 0

    def test_crop_region_error_propagates(self, tmp_path):
        """An invalid box (zero area) raises AIError from crop_region."""
        import ai

        image_file = _make_jpeg_file(tmp_path)
        cfg = _make_config(provider="claude")

        with pytest.raises(ai.AIError):
            list(ai.ask_about_region_stream(
                cfg, image_file,
                {"x": 0, "y": 0, "w": 0, "h": 0},
                "question",
            ))

    def test_question_stripped_before_use(self, tmp_path):
        """Leading/trailing whitespace in the question is stripped."""
        import ai

        image_file = _make_jpeg_file(tmp_path)
        cfg = _make_config(provider="claude")

        with patch.object(ai, "_stream_claude", return_value=iter(["ok"])) as mock_c:
            list(ai.ask_about_region_stream(
                cfg, image_file,
                {"x": 0, "y": 0, "w": 10, "h": 10},
                "  trimmed question  ",
            ))

        call_question = mock_c.call_args.args[2]
        assert call_question == "trimmed question"

    def test_yields_all_chunks_in_order(self, tmp_path):
        """All chunks from the inner stream function are yielded in order."""
        import ai

        image_file = _make_jpeg_file(tmp_path)
        cfg = _make_config(provider="claude")
        chunks = ["first", " second", " third"]

        with patch.object(ai, "_stream_claude", return_value=iter(chunks)):
            result = list(ai.ask_about_region_stream(
                cfg, image_file,
                {"x": 0, "y": 0, "w": 10, "h": 10},
                "q",
            ))

        assert result == chunks