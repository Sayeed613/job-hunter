from __future__ import annotations

import asyncio
from pathlib import Path

from app.notifier.whatsapp_notifier import WhatsAppNotifier


class _DummyTwilioError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"twilio error {code}")
        self.code = code


def test_cycle_started_logs_once_after_whatsapp_failure(monkeypatch) -> None:
    notifier = WhatsAppNotifier()
    entries: list[str] = []

    async def fake_fallback(text: str) -> bool:
        entries.append(text)
        return True

    notifier._available = True
    notifier._client = object()
    monkeypatch.setattr(notifier._fallback, "send_message", fake_fallback)

    async def fake_send_message(text: str) -> bool:
        notifier._failed_once = True
        return await notifier._fallback.send_message(text)

    monkeypatch.setattr(notifier, "send_message", fake_send_message)

    result = asyncio.run(notifier.cycle_started(24))

    assert result is True
    assert entries == ["Job Bot cycle started with 24 providers"]


def test_twilio_hint_for_missing_channel() -> None:
    hint = WhatsAppNotifier._twilio_error_hint(_DummyTwilioError(63007))
    assert "channel" in hint.lower()
    assert "sender" in hint.lower()


def test_twilio_hint_for_sandbox_join() -> None:
    hint = WhatsAppNotifier._twilio_error_hint(_DummyTwilioError(63016))
    assert "sandbox" in hint.lower()
    assert "join" in hint.lower()


def test_success_logs_documents_without_duplicate_summary(monkeypatch, tmp_path: Path) -> None:
    notifier = WhatsAppNotifier()
    resume_path = tmp_path / "resume.docx"
    cover_path = tmp_path / "cover.docx"
    resume_path.write_text("resume", encoding="utf-8")
    cover_path.write_text("cover", encoding="utf-8")

    messages: list[str] = []
    documents: list[str] = []

    async def fake_send_message(text: str) -> bool:
        messages.append(text)
        return True

    async def fake_send_document(file_path: str, caption: str = "") -> bool:
        documents.append(f"{caption}|{file_path}")
        return True

    monkeypatch.setattr(notifier, "send_message", fake_send_message)
    monkeypatch.setattr(notifier._fallback, "send_document", fake_send_document)

    result = asyncio.run(
        notifier.success(
            "Frontend Engineer",
            "Example Co",
            resume_path=str(resume_path),
            cover_letter_path=str(cover_path),
            salary="10 LPA",
            location="Remote",
        )
    )

    assert result is True
    assert len(messages) == 1
    assert "Applied: Frontend Engineer @ Example Co" in messages[0]
    assert len(documents) == 2
