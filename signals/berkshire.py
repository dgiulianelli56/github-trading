"""
Berkshire Hathaway / Warren Buffett holdings tracker.
Primary source: SEC EDGAR 13F filings (free, no API key).
Secondary source: Financial Modeling Prep institutional holdings API.

13F filings have a mandatory 45-day delay after each quarter end and cover
only long U.S. equity positions. This is a directional signal, not real-time.
"""

import json
import logging
from pathlib import Path

import requests

from config import FMP_API_KEY

logger = logging.getLogger(__name__)

_BERKSHIRE_CIK = "0001067983"
_EDGAR_BASE = "https://data.sec.gov"
_FMP_BASE = "https://financialmodelingprep.com/stable"
_CACHE_FILE = Path("state/berkshire_holdings.json")
_EDGAR_HEADERS = {"User-Agent": "DaniloGTrading spm@giulianelli.com"}

# Stable fallback reflecting Q1 2026 13F (filed May 2026)
_KNOWN_HOLDINGS: list[dict] = [
    {"ticker": "AAPL", "weight_pct": 28.0},
    {"ticker": "AXP",  "weight_pct": 16.0},
    {"ticker": "KO",   "weight_pct": 12.0},
    {"ticker": "BAC",  "weight_pct": 10.0},
    {"ticker": "CVX",  "weight_pct":  6.0},
    {"ticker": "OXY",  "weight_pct":  5.5},
    {"ticker": "MCO",  "weight_pct":  4.5},
    {"ticker": "DVA",  "weight_pct":  2.5},
    {"ticker": "KHC",  "weight_pct":  2.0},
    {"ticker": "VZ",   "weight_pct":  1.5},
]


def get_holdings(use_cache: bool = True) -> list[dict]:
    """
    Return Berkshire's current long equity holdings, sorted by portfolio weight.
    Tries FMP first (structured JSON), falls back to SEC EDGAR parsing,
    then falls back to the hardcoded known list.
    """
    if use_cache and _CACHE_FILE.exists():
        try:
            cached = json.loads(_CACHE_FILE.read_text())
            logger.info(f"Berkshire holdings loaded from cache ({len(cached)} positions)")
            return cached
        except Exception:
            pass

    holdings = _fetch_from_fmp() or _fetch_from_edgar() or _KNOWN_HOLDINGS
    _CACHE_FILE.parent.mkdir(exist_ok=True)
    _CACHE_FILE.write_text(json.dumps(holdings, indent=2))
    return holdings


def refresh() -> list[dict]:
    """Force-refresh holdings, bypassing cache."""
    if _CACHE_FILE.exists():
        _CACHE_FILE.unlink()
    return get_holdings(use_cache=False)


def watchlist(top_n: int = 15) -> list[str]:
    """Return ticker symbols for Berkshire's top N holdings."""
    return [h["ticker"] for h in get_holdings()[:top_n]]


def _fetch_from_fmp() -> list[dict] | None:
    if not FMP_API_KEY:
        return None
    try:
        resp = requests.get(
            f"{_FMP_BASE}/institutional-ownership/portfolio-holdings-summary",
            params={"cik": _BERKSHIRE_CIK, "apikey": FMP_API_KEY},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return None
        holdings = [
            {
                "ticker": h["symbol"].upper(),
                "weight_pct": round(float(h.get("portfolioWeight", 0)) * 100, 2),
                "shares": int(h.get("shares", 0)),
            }
            for h in data
            if h.get("symbol")
        ]
        holdings.sort(key=lambda x: x["weight_pct"], reverse=True)
        logger.info(f"Berkshire holdings fetched from FMP ({len(holdings)} positions)")
        return holdings
    except Exception as exc:
        logger.warning(f"FMP Berkshire fetch failed: {exc}")
        return None


def _fetch_from_edgar() -> list[dict] | None:
    """
    Fetch latest 13F-HR filing from SEC EDGAR and extract holdings.
    EDGAR returns an XML info table; we parse the accession number and
    retrieve the structured JSON summary when available.
    """
    try:
        resp = requests.get(
            f"{_EDGAR_BASE}/submissions/CIK{_BERKSHIRE_CIK}.json",
            headers=_EDGAR_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        filings = resp.json().get("filings", {}).get("recent", {})

        forms = filings.get("form", [])
        accessions = filings.get("accessionNumber", [])

        latest_acc = next(
            (acc for form, acc in zip(forms, accessions) if form == "13F-HR"),
            None,
        )
        if not latest_acc:
            logger.warning("No 13F-HR filing found on EDGAR for Berkshire")
            return None

        acc_clean = latest_acc.replace("-", "")
        index_url = (
            f"{_EDGAR_BASE}/Archives/edgar/data/1067983/"
            f"{acc_clean}/{latest_acc}-index.json"
        )
        idx_resp = requests.get(index_url, headers=_EDGAR_HEADERS, timeout=15)
        idx_resp.raise_for_status()
        docs = idx_resp.json().get("documents", [])

        # Look for the primary XML info table document
        xml_doc = next(
            (d for d in docs if "infotable" in d.get("name", "").lower()),
            None,
        )
        if not xml_doc:
            logger.warning("13F info table document not found in EDGAR index")
            return None

        xml_url = f"{_EDGAR_BASE}/Archives/edgar/data/1067983/{acc_clean}/{xml_doc['name']}"
        xml_resp = requests.get(xml_url, headers=_EDGAR_HEADERS, timeout=30)
        xml_resp.raise_for_status()

        holdings = _parse_13f_xml(xml_resp.text)
        logger.info(f"Berkshire holdings fetched from EDGAR ({len(holdings)} positions)")
        return holdings

    except Exception as exc:
        logger.warning(f"EDGAR Berkshire fetch failed: {exc}")
        return None


def _parse_13f_xml(xml_text: str) -> list[dict]:
    """Parse SEC 13F-HR XML info table into a list of holdings dicts."""
    import xml.etree.ElementTree as ET

    ns = {
        "ns": "http://www.sec.gov/edgar/document/thirteenf/informationtable",
        "n2": "http://www.sec.gov/edgar/document/thirteenf/informationtable",
    }

    root = ET.fromstring(xml_text)
    holdings: list[dict] = []
    total_value = 0

    for entry in root.iter():
        if entry.tag.endswith("infoTable"):
            name_el = entry.find(".//{*}nameOfIssuer")
            cusip_el = entry.find(".//{*}cusip")
            value_el = entry.find(".//{*}value")
            shares_el = entry.find(".//{*}sshPrnamt")

            if value_el is not None:
                value = int(value_el.text or 0) * 1000
                total_value += value
                holdings.append({
                    "name": name_el.text if name_el is not None else "",
                    "cusip": cusip_el.text if cusip_el is not None else "",
                    "value_usd": value,
                    "shares": int(shares_el.text or 0) if shares_el is not None else 0,
                    "ticker": "",  # EDGAR 13F doesn't include ticker; resolved below
                    "weight_pct": 0.0,
                })

    # Compute portfolio weights and sort
    for h in holdings:
        h["weight_pct"] = round(h["value_usd"] / total_value * 100, 2) if total_value else 0.0

    holdings.sort(key=lambda x: x["weight_pct"], reverse=True)

    # EDGAR 13F doesn't include ticker symbols; fall back to known-list tickers
    # for the top positions by matching issuer names heuristically
    _enrich_tickers(holdings)
    return [h for h in holdings if h["ticker"]]


def _enrich_tickers(holdings: list[dict]) -> None:
    """Best-effort ticker lookup by matching issuer name to known Berkshire holdings."""
    name_map = {
        "APPLE": "AAPL",
        "AMERICAN EXPRESS": "AXP",
        "COCA-COLA": "KO",
        "BANK OF AMERICA": "BAC",
        "CHEVRON": "CVX",
        "OCCIDENTAL": "OXY",
        "MOODY": "MCO",
        "DAVITA": "DVA",
        "KRAFT HEINZ": "KHC",
        "VERIZON": "VZ",
        "VISA": "V",
        "MASTERCARD": "MA",
        "AMAZON": "AMZN",
        "LIBERTY": "LSXMK",
        "T-MOBILE": "TMUS",
        "SIRIUS": "SIRI",
        "HP INC": "HPQ",
        "SNOWFLAKE": "SNOW",
        "NUBANK": "NU",
        "PILOT": "PLTV",
    }
    for h in holdings:
        name_upper = h["name"].upper()
        for key, ticker in name_map.items():
            if key in name_upper:
                h["ticker"] = ticker
                break
