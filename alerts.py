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


def send_telegram(message: str) -> bool:
    token = _secret("TELEGRAM_BOT_TOKEN")
    chat_id = _secret("TELEGRAM_CHAT_ID")
    if not token or not chat_id or requests is None:
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
            timeout=10,
        )
        return bool(response.ok)
    except Exception:
        return False
