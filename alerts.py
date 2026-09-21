from __future__ import annotations

import os
from typing import Optional

try:
    import requests
except Exception:
    requests = None


def _secret(name: str) -> Optional[str]:
    value = os.getenv(name)
    if value:
        return value
    try:
        import streamlit as st
        value = st.secrets.get(name)
        return str(value) if value else None
    except Exception:
        return None


def telegram_configured() -> bool:
    return bool(_secret("TELEGRAM_BOT_TOKEN") and _secret("TELEGRAM_CHAT_ID"))


def send_telegram_detailed(message: str) -> dict:
    """Send a Telegram message and return a safe diagnostic result.

    The token and chat id are never returned or logged. The response is capped
    to a short Telegram description so GitHub Actions can show why a test failed.
    """
    token = _secret("TELEGRAM_BOT_TOKEN")
    chat_id = _secret("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return {"ok": False, "status_code": None, "error": "Telegram secrets missing"}
    if requests is None:
        return {"ok": False, "status_code": None, "error": "requests package unavailable"}
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
            timeout=15,
        )
        description = ""
        try:
            payload = response.json()
            description = str(payload.get("description") or "")[:240]
            api_ok = bool(payload.get("ok", response.ok))
        except Exception:
            api_ok = bool(response.ok)
            description = (response.text or "")[:240]
        return {
            "ok": bool(response.ok and api_ok),
            "status_code": int(response.status_code),
            "error": "" if (response.ok and api_ok) else (description or f"HTTP {response.status_code}"),
        }
    except Exception as exc:
        return {"ok": False, "status_code": None, "error": f"{type(exc).__name__}: {exc}"[:240]}


def send_telegram(message: str) -> bool:
    return bool(send_telegram_detailed(message).get("ok"))
