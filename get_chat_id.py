#!/usr/bin/env python3
"""Run once to retrieve your Telegram chat_id after messaging the bot."""

import os
import requests
from dotenv import load_dotenv

load_dotenv()
token = os.getenv("TELEGRAM_BOT_TOKEN", "")

resp = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=10)
updates = resp.json().get("result", [])

if not updates:
    print("No messages found. Make sure you sent a message to @DaniloGTradingBot first.")
else:
    for u in updates:
        msg = u.get("message", {})
        chat = msg.get("chat", {})
        print(f"chat_id : {chat.get('id')}")
        print(f"from    : {msg.get('from', {}).get('first_name')} {msg.get('from', {}).get('last_name')}")
        print(f"text    : {msg.get('text')}")
        print()
