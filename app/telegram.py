from __future__ import annotations

import aiohttp

from app.config import CFG


async def send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{CFG.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CFG.TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=payload) as r:
            # swallow response; if you want debug, print await r.text()
            _ = await r.text()
