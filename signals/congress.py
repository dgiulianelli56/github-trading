"""
Congressional trade signals via Financial Modeling Prep API.
Sources Senate and House periodic transaction reports (STOCK Act disclosures).
Free tier: 250 calls/day at financialmodelingprep.com
"""

import logging
from collections import Counter
from datetime import date, timedelta

import requests

from config import FMP_API_KEY

logger = logging.getLogger(__name__)
_FMP_BASE = "https://financialmodelingprep.com/stable"


def get_recent_trades(days_back: int = 14) -> list[dict]:
    """
    Return recent BUY transactions from Congress members, normalized
    across Senate and House response schemas.
    """
    if not FMP_API_KEY:
        logger.warning("FMP_API_KEY not configured — congressional signals unavailable")
        return []

    since = (date.today() - timedelta(days=days_back)).isoformat()
    raw: list[dict] = []

    # FMP stable endpoints (replaced legacy v4 endpoints in 2025)
    for endpoint, chamber in (("senate-latest", "Senate"), ("house-latest", "House")):
        try:
            resp = requests.get(
                f"{_FMP_BASE}/{endpoint}",
                params={"apikey": FMP_API_KEY, "limit": 100},
                timeout=15,
            )
            resp.raise_for_status()
            for record in resp.json():
                record["_chamber"] = chamber
                raw.append(record)
        except Exception as exc:
            logger.error(f"FMP {endpoint} fetch failed: {exc}")

    normalized: list[dict] = []
    for t in raw:
        tx_date = t.get("transactionDate") or t.get("disclosureDate") or ""
        if tx_date < since:
            continue

        ticker = (t.get("symbol") or "").upper().strip()
        if not ticker or ticker in ("--", "N/A", ""):
            continue

        tx_type = (t.get("type") or "").strip()
        if "purchase" not in tx_type.lower():
            continue

        politician = f"{t.get('firstName', '')} {t.get('lastName', '')}".strip()
        chamber = t["_chamber"]

        normalized.append({
            "ticker": ticker,
            "politician": politician,
            "chamber": chamber,
            "date": tx_date,
            "amount_range": t.get("amount") or t.get("transactionAmount", ""),
        })

    return sorted(normalized, key=lambda x: x["date"], reverse=True)


def high_conviction_tickers(trades: list[dict], min_politicians: int = 2) -> list[str]:
    """Tickers purchased by at least min_politicians distinct members recently."""
    ticker_members: dict[str, set] = {}
    for t in trades:
        ticker_members.setdefault(t["ticker"], set()).add(t["politician"])
    return [
        ticker
        for ticker, members in sorted(
            ticker_members.items(), key=lambda x: len(x[1]), reverse=True
        )
        if len(members) >= min_politicians
    ]


def all_bought_tickers(trades: list[dict]) -> list[str]:
    """All unique tickers with at least one congressional buy, sorted by frequency."""
    counts = Counter(t["ticker"] for t in trades)
    return [ticker for ticker, _ in counts.most_common()]


def summary(trades: list[dict]) -> str:
    """Human-readable summary for logging / Telegram."""
    if not trades:
        return "No recent congressional trades found."
    conviction = high_conviction_tickers(trades)
    lines = [f"Congressional buys (last 14 days): {len(trades)} transactions"]
    if conviction:
        lines.append(f"High-conviction (≥2 politicians): {', '.join(conviction)}")
    lines.append(f"All tickers: {', '.join(all_bought_tickers(trades)[:15])}")
    return "\n".join(lines)
