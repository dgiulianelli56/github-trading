import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")


@dataclass
class AccountConfig:
    id: str
    name: str
    key: str
    secret: str
    long_only: bool


ACCOUNTS: dict[str, AccountConfig] = {
    "ROTH_IRA": AccountConfig(
        id="ROTH_IRA",
        name="ROTH IRA",
        key=os.getenv("ROTH_IRA_KEY", ""),
        secret=os.getenv("ROTH_IRA_SECRET", ""),
        long_only=True,
    ),
    "JOINT_WROS": AccountConfig(
        id="JOINT_WROS",
        name="Joint WROS-TOD",
        key=os.getenv("JOINT_WROS_KEY", ""),
        secret=os.getenv("JOINT_WROS_SECRET", ""),
        long_only=False,
    ),
    "ROLLOVER_IRA": AccountConfig(
        id="ROLLOVER_IRA",
        name="Rollover IRA",
        key=os.getenv("ROLLOVER_IRA_KEY", ""),
        secret=os.getenv("ROLLOVER_IRA_SECRET", ""),
        long_only=True,
    ),
}

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Financial Modeling Prep
FMP_API_KEY = os.getenv("FMP_API_KEY", "")

# Strategy parameters
POSITION_SIZE_PCT = float(os.getenv("POSITION_SIZE_PCT", "0.05"))
LADDER_RUNGS = int(os.getenv("LADDER_RUNGS", "4"))
RUNG_SPACING_PCT = float(os.getenv("RUNG_SPACING_PCT", "0.02"))
MA_PERIOD = int(os.getenv("MA_PERIOD", "50"))
MA_TYPE = os.getenv("MA_TYPE", "SMA")          # "SMA" or "EMA"
MA_BUFFER_PCT = float(os.getenv("MA_BUFFER_PCT", "0.01"))
TIMEZONE = os.getenv("TIMEZONE", "America/New_York")
