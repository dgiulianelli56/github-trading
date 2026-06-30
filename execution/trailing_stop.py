"""
MA Trailing Stop execution module.
Registered after LadderBuy completes. Recalculates MA each run.
Stop trigger only ratchets in the favourable direction (up for longs).
Executes a market sell when price closes below the effective trigger.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.enums import DataFeed
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

import notifier
from config import AccountConfig, MA_BUFFER_PCT, MA_PERIOD, MA_TYPE

logger = logging.getLogger(__name__)


class TrailingStop:
    def __init__(
        self,
        account: AccountConfig,
        trading_client: TradingClient,
        data_client: StockHistoricalDataClient,
    ) -> None:
        self.account = account
        self.trading = trading_client
        self.data = data_client
        self._state_path = Path(f"state/trailing_{account.id}.json")
        self._state: dict = self._load()

    # ── State persistence ──────────────────────────────────────────────────────

    def _load(self) -> dict:
        local: dict = {}
        try:
            local = json.loads(self._state_path.read_text())
        except FileNotFoundError:
            pass
        # Rebuild from Alpaca positions for any tickers not in local state
        # (handles stateless cloud runs where state files don't persist)
        try:
            positions = self.trading.get_all_positions()
            for pos in positions:
                ticker = pos.symbol
                if ticker not in local:
                    local[ticker] = {
                        "ticker": ticker,
                        "entry_price": float(pos.avg_entry_price),
                        "qty": float(pos.qty),
                        "entry_date": str(pos.created_at)[:10] if hasattr(pos, "created_at") else datetime.now(timezone.utc).date().isoformat(),
                        "highest_trigger": None,
                        "activated": float(pos.current_price or 0) > float(pos.avg_entry_price),
                        "status": "MONITORING",
                    }
                    logger.info(f"[{self.account.name}] {ticker}: reconstructed trailing stop from Alpaca position")
        except Exception as exc:
            logger.warning(f"[{self.account.name}] Could not reconstruct positions from Alpaca: {exc}")
        return local

    def _save(self) -> None:
        self._state_path.parent.mkdir(exist_ok=True)
        self._state_path.write_text(json.dumps(self._state, indent=2, default=str))

    # ── Public API ─────────────────────────────────────────────────────────────

    def register(
        self,
        ticker: str,
        entry_price: float,
        qty: float,
        entry_date: str | None = None,
    ) -> None:
        """Register a completed ladder position for trailing-stop monitoring."""
        if ticker in self._state and self._state[ticker]["status"] == "MONITORING":
            logger.info(f"[{self.account.name}] {ticker}: trailing stop already registered")
            return
        self._state[ticker] = {
            "ticker": ticker,
            "entry_price": entry_price,
            "qty": qty,
            "entry_date": entry_date or datetime.now(timezone.utc).date().isoformat(),
            "highest_trigger": None,
            "activated": False,
            "status": "MONITORING",
        }
        self._save()
        logger.info(
            f"[{self.account.name}] {ticker}: trailing stop registered "
            f"entry=${entry_price:.2f} qty={qty}"
        )

    def check(self, ticker: str) -> None:
        """
        Recalculate MA, update ratchet, and exit if price falls below trigger.
        Call this on every scheduled run for each monitored position.
        """
        if ticker not in self._state:
            return
        pos = self._state[ticker]
        if pos["status"] != "MONITORING":
            return

        try:
            current_price, ma_value = self._compute_ma(ticker)
        except Exception as exc:
            logger.error(f"[{self.account.name}] {ticker} MA computation failed: {exc}")
            # Rule: on data error, retain last trigger and alert — do not trade blindly
            notifier._send(
                f"⚠️ <b>[{self.account.name}] {ticker} data feed error</b>\n"
                f"Retaining last stop trigger. Manual check recommended.\n{exc}"
            )
            return

        trigger = ma_value * (1 - MA_BUFFER_PCT)

        # Rule 1: activation guard — wait until price exceeds entry
        if not pos["activated"]:
            if current_price > pos["entry_price"]:
                pos["activated"] = True
                logger.info(f"[{self.account.name}] {ticker}: trailing stop ACTIVATED")
            else:
                logger.debug(
                    f"[{self.account.name}] {ticker}: pre-activation "
                    f"(price ${current_price:.2f} vs entry ${pos['entry_price']:.2f})"
                )
                self._save()
                return

        # Rule 4: ratchet — trigger never moves against us
        if pos["highest_trigger"] is None or trigger > pos["highest_trigger"]:
            pos["highest_trigger"] = round(trigger, 4)

        effective_trigger = pos["highest_trigger"]

        logger.info(
            f"[{self.account.name}] {ticker}: "
            f"price=${current_price:.2f}  MA({MA_PERIOD})=${ma_value:.2f}  "
            f"stop=${effective_trigger:.2f}"
        )

        if current_price <= effective_trigger:
            self._execute_exit(ticker, current_price, ma_value, effective_trigger, pos)
        else:
            self._save()

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _compute_ma(self, ticker: str) -> tuple[float, float]:
        """Return (current_close_price, MA_value)."""
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=MA_PERIOD * 3)
        req = StockBarsRequest(
            symbol_or_symbols=ticker,
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
            feed=DataFeed.IEX,
        )
        bars = self.data.get_stock_bars(req)
        closes: pd.Series = bars[ticker].df["close"]

        if len(closes) < MA_PERIOD:
            raise ValueError(f"Insufficient bars for MA({MA_PERIOD}): only {len(closes)} available")

        if MA_TYPE == "EMA":
            ma = float(closes.ewm(span=MA_PERIOD, adjust=False).mean().iloc[-1])
        else:
            ma = float(closes.rolling(MA_PERIOD).mean().iloc[-1])

        return float(closes.iloc[-1]), ma

    def _execute_exit(
        self,
        ticker: str,
        current_price: float,
        ma_value: float,
        trigger: float,
        pos: dict,
    ) -> None:
        qty = int(pos["qty"])
        try:
            order = self.trading.submit_order(
                MarketOrderRequest(
                    symbol=ticker,
                    qty=qty,
                    side=OrderSide.SELL,
                    time_in_force=TimeInForce.DAY,
                )
            )
            fill_price = (
                float(order.filled_avg_price) if order.filled_avg_price else current_price
            )
        except Exception as exc:
            logger.error(f"[{self.account.name}] {ticker} exit order failed: {exc}")
            return

        entry_date = datetime.fromisoformat(pos["entry_date"])
        holding_days = (datetime.now() - entry_date).days
        pnl = (fill_price - pos["entry_price"]) * qty
        pnl_pct = (fill_price - pos["entry_price"]) / pos["entry_price"] * 100

        # Record wash-sale log if exiting at a loss
        if pnl < 0:
            self._record_loss_sale(ticker)

        notifier.trailing_stop_triggered(
            account_name=self.account.name,
            ticker=ticker,
            qty=qty,
            exit_price=fill_price,
            ma_value=ma_value,
            pnl=pnl,
            pnl_pct=pnl_pct,
            holding_days=holding_days,
            long_only=self.account.long_only,
        )

        pos["status"] = "EXITED"
        pos["exit_price"] = fill_price
        pos["exit_date"] = datetime.now(timezone.utc).date().isoformat()
        self._save()
        logger.info(
            f"[{self.account.name}] {ticker} EXITED @ ${fill_price:.2f} "
            f"P&L=${pnl:+.0f} ({pnl_pct:+.1f}%)"
        )

    def _record_loss_sale(self, ticker: str) -> None:
        wash_path = Path(f"state/wash_sales_{self.account.id}.json")
        log: dict = {}
        if wash_path.exists():
            log = json.loads(wash_path.read_text())
        log[ticker] = {"date": datetime.now(timezone.utc).isoformat()}
        wash_path.write_text(json.dumps(log, indent=2))

    # ── Queries ────────────────────────────────────────────────────────────────

    @property
    def monitored_tickers(self) -> list[str]:
        return [k for k, v in self._state.items() if v["status"] == "MONITORING"]
