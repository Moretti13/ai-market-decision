from __future__ import annotations

import os
from typing import Optional

import requests


def _secret(name: str) -> Optional[str]:
    val = os.getenv(name)
    if val:
        return val
    try:
        import streamlit as st
        value = st.secrets.get(name)
        return str(value) if value else None
    except Exception:
        return None


def send_telegram(message: str) -> bool:
    token = _secret("TELEGRAM_BOT_TOKEN")
    chat_id = _secret("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message},
            timeout=10,
        )
        return response.ok
    except Exception:
        return False
