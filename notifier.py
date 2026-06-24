import logging
import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, MA_PERIOD

logger = logging.getLogger(__name__)
_TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def _send(text: str) -> bool:
    if not TELEGRAM_CHAT_ID:
        logger.warning("TELEGRAM_CHAT_ID not set — skipping notification")
        return False
    try:
        resp = requests.post(
            f"{_TELEGRAM_URL}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        logger.error(f"Telegram send failed: {exc}")
        return False


def ladder_rung_filled(
    account_name: str,
    ticker: str,
    rung: int,
    total_rungs: int,
    fill_price: float,
    fill_qty: float,
    avg_entry: float,
    total_invested: float,
) -> None:
    _send(
        f"🟢 <b>[{account_name}] {ticker} — Rung {rung}/{total_rungs} FILLED</b>\n"
        f"Fill: ${fill_price:,.2f} × {fill_qty:g} shares\n"
        f"Avg entry so far: ${avg_entry:,.2f} | Total: ${total_invested:,.0f}\n"
        f"⚡ Mirror this BUY in Fidelity {account_name}"
    )


def ladder_complete(
    account_name: str,
    ticker: str,
    avg_entry: float,
    total_qty: float,
    total_invested: float,
) -> None:
    _send(
        f"✅ <b>[{account_name}] {ticker} — LADDER COMPLETE</b>\n"
        f"All rungs filled | Avg entry: ${avg_entry:,.2f}\n"
        f"Total: {total_qty:g} shares @ ${total_invested:,.0f}\n"
        f"MA({MA_PERIOD}) trailing stop is now ACTIVE"
    )


def trailing_stop_triggered(
    account_name: str,
    ticker: str,
    qty: float,
    exit_price: float,
    ma_value: float,
    pnl: float,
    pnl_pct: float,
    holding_days: int,
    long_only: bool,
) -> None:
    sign = "+" if pnl >= 0 else ""
    term = "LONG-TERM ✅" if holding_days >= 365 else "SHORT-TERM ⚠️"
    tax_line = ""
    if not long_only:
        tax_line = f"\nTax: {term} capital gain ({holding_days} days held)"
    elif "IRA" not in account_name:
        tax_line = f"\nTax: {term} capital gain ({holding_days} days held)"

    _send(
        f"🔴 <b>[{account_name}] {ticker} — TRAILING STOP HIT</b>\n"
        f"SELL {qty:g} shares @ ${exit_price:,.2f}\n"
        f"MA({MA_PERIOD}): ${ma_value:,.2f}\n"
        f"P&amp;L: {sign}${pnl:,.0f} ({sign}{pnl_pct:.1f}%){tax_line}\n"
        f"⚡ Mirror this SELL in Fidelity {account_name}"
    )


def hard_stop_triggered(
    account_name: str,
    ticker: str,
    qty: float,
    exit_price: float,
    hard_stop: float,
) -> None:
    _send(
        f"🚨 <b>[{account_name}] {ticker} — HARD STOP HIT</b>\n"
        f"Price ${exit_price:,.2f} breached hard stop ${hard_stop:,.2f}\n"
        f"Exiting {qty:g} shares | Remaining ladder orders CANCELLED\n"
        f"⚡ Mirror this SELL in Fidelity {account_name}"
    )


def wash_sale_warning(account_name: str, ticker: str, days_since_loss: int) -> None:
    ira_note = (
        "\n🚫 In an IRA this loss is <b>PERMANENTLY disallowed</b> — not just deferred."
        if "IRA" in account_name
        else "\nLoss deduction deferred until 30-day window closes."
    )
    _send(
        f"⚠️ <b>WASH SALE WARNING — [{account_name}] {ticker}</b>\n"
        f"Last loss sale was {days_since_loss} days ago.\n"
        f"Re-entering within 30 days triggers wash sale rules.{ira_note}\n"
        f"Ladder buy PAUSED until day 30."
    )


def new_signal(account_name: str, ticker: str, source: str, detail: str) -> None:
    _send(
        f"📡 <b>[{account_name}] New signal: {ticker}</b>\n"
        f"Source: {source}\n"
        f"{detail}\n"
        f"Ladder buy will activate at 10:00 AM ET"
    )


def scan_heartbeat(run_type: str, positions_monitored: int, new_signals: list[str]) -> None:
    if not new_signals:
        return  # stay silent when nothing is happening
    signals_text = "\n".join(f"  • {s}" for s in new_signals)
    _send(
        f"📊 <b>{run_type} scan complete</b>\n"
        f"{positions_monitored} positions monitored\n\n"
        f"New signals:\n{signals_text}"
    )
