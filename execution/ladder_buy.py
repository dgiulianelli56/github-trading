"""
Ladder Buy execution module.
Places N limit orders at descending price levels (for LONG) with weighted sizing.
Tracks fill state in a JSON file under state/.
Hands off to TrailingStop when all rungs are filled.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

import notifier
from config import AccountConfig, LADDER_RUNGS, RUNG_SPACING_PCT, POSITION_SIZE_PCT

logger = logging.getLogger(__name__)


class LadderBuy:
    def __init__(
        self,
        account: AccountConfig,
        trading_client: TradingClient,
        data_client: StockHistoricalDataClient,
    ) -> None:
        self.account = account
        self.trading = trading_client
        self.data = data_client
        self._state_path = Path(f"state/ladder_{account.id}.json")
        self._state: dict = self._load()

    # ── State persistence ──────────────────────────────────────────────────────

    def _load(self) -> dict:
        try:
            return json.loads(self._state_path.read_text())
        except FileNotFoundError:
            return {}

    def _save(self) -> None:
        self._state_path.parent.mkdir(exist_ok=True)
        self._state_path.write_text(json.dumps(self._state, indent=2, default=str))

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _cash_balance(self) -> float:
        return float(self.trading.get_account().cash)

    def _ask_price(self, ticker: str) -> float:
        req = StockLatestQuoteRequest(symbol_or_symbols=ticker)
        quote = self.data.get_stock_latest_quote(req)
        return float(quote[ticker].ask_price)

    def _has_recent_loss(self, ticker: str) -> int | None:
        """
        Return days since last loss sale for wash-sale check, or None if clean.
        Reads from a simple wash-sale log written on every exit at a loss.
        """
        wash_file = Path(f"state/wash_sales_{self.account.id}.json")
        if not wash_file.exists():
            return None
        log = json.loads(wash_file.read_text())
        entry = log.get(ticker)
        if not entry:
            return None
        sale_date = datetime.fromisoformat(entry["date"]).date()
        days_ago = (datetime.now().date() - sale_date).days
        return days_ago if days_ago < 30 else None

    # ── Public API ─────────────────────────────────────────────────────────────

    def activate(
        self,
        ticker: str,
        num_rungs: int = LADDER_RUNGS,
        rung_spacing_pct: float = RUNG_SPACING_PCT,
    ) -> dict | None:
        """
        Place all rung limit orders simultaneously.
        Returns the new state entry, or None if activation was skipped.
        """
        if ticker in self._state:
            logger.info(f"[{self.account.name}] {ticker}: ladder already active")
            return self._state[ticker]

        # Wash-sale guard for IRA accounts (loss is permanent there)
        days = self._has_recent_loss(ticker)
        if days is not None:
            notifier.wash_sale_warning(self.account.name, ticker, days)
            return None

        cash = self._cash_balance()
        total_capital = cash * POSITION_SIZE_PCT
        current_price = self._ask_price(ticker)
        hard_stop = current_price * (1 - rung_spacing_pct * num_rungs * 1.5)

        # Weighted sizing: rung 1 gets weight 1, rung N gets weight N (most at lowest)
        weights = list(range(1, num_rungs + 1))
        total_weight = sum(weights)
        rung_prices = [current_price * (1 - rung_spacing_pct * i) for i in range(num_rungs)]
        rung_capitals = [(w / total_weight) * total_capital for w in weights]

        rungs: list[dict] = []
        for i, (price, capital) in enumerate(zip(rung_prices, rung_capitals)):
            qty = int(capital / price)
            if qty < 1:
                logger.warning(f"[{self.account.name}] {ticker} rung {i+1}: qty rounds to 0, skipping")
                continue
            try:
                order = self.trading.submit_order(
                    LimitOrderRequest(
                        symbol=ticker,
                        qty=qty,
                        side=OrderSide.BUY,
                        time_in_force=TimeInForce.GTC,
                        limit_price=round(price, 2),
                    )
                )
                rungs.append({
                    "rung_index": i + 1,
                    "target_price": round(price, 2),
                    "allocated_capital": round(capital, 2),
                    "qty": qty,
                    "order_id": str(order.id),
                    "status": "PENDING",
                    "fill_price": None,
                    "fill_qty": None,
                })
                logger.info(
                    f"[{self.account.name}] {ticker} rung {i+1}/{num_rungs}: "
                    f"LIMIT BUY {qty} @ ${price:.2f}"
                )
            except Exception as exc:
                logger.error(f"[{self.account.name}] {ticker} rung {i+1} order failed: {exc}")

        if not rungs:
            return None

        entry = {
            "ticker": ticker,
            "activated_at": datetime.now(timezone.utc).isoformat(),
            "total_rungs": len(rungs),
            "rungs": rungs,
            "average_entry": None,
            "total_qty": 0.0,
            "status": "ACTIVE",
            "hard_stop": round(hard_stop, 2),
        }
        self._state[ticker] = entry
        self._save()
        return entry

    def check_fills(self, ticker: str) -> None:
        """Poll Alpaca for order status updates; detect hard stop breach."""
        if ticker not in self._state:
            return
        entry = self._state[ticker]
        if entry["status"] not in ("ACTIVE",):
            return

        current_price = self._ask_price(ticker)

        if current_price <= entry["hard_stop"]:
            self._trigger_hard_stop(ticker, current_price, entry)
            return

        newly_filled = False
        for rung in entry["rungs"]:
            if rung["status"] == "FILLED":
                continue
            try:
                order = self.trading.get_order_by_id(rung["order_id"])
                status = str(order.status)
                if status in ("filled", "partially_filled") and order.filled_avg_price:
                    rung["status"] = "FILLED"
                    rung["fill_price"] = float(order.filled_avg_price)
                    rung["fill_qty"] = float(order.filled_qty)
                    newly_filled = True
            except Exception as exc:
                logger.error(f"[{self.account.name}] {ticker} order poll failed: {exc}")

        if newly_filled:
            self._recompute_avg(entry)
            filled = [r for r in entry["rungs"] if r["status"] == "FILLED"]
            latest = max(filled, key=lambda r: r["rung_index"])
            notifier.ladder_rung_filled(
                account_name=self.account.name,
                ticker=ticker,
                rung=latest["rung_index"],
                total_rungs=entry["total_rungs"],
                fill_price=latest["fill_price"],
                fill_qty=latest["fill_qty"],
                avg_entry=entry["average_entry"],
                total_invested=entry["average_entry"] * entry["total_qty"],
            )

        pending = [r for r in entry["rungs"] if r["status"] == "PENDING"]
        if not pending:
            entry["status"] = "LADDER_COMPLETE"
            notifier.ladder_complete(
                account_name=self.account.name,
                ticker=ticker,
                avg_entry=entry["average_entry"],
                total_qty=entry["total_qty"],
                total_invested=entry["average_entry"] * entry["total_qty"],
            )
            logger.info(
                f"[{self.account.name}] {ticker} LADDER COMPLETE "
                f"avg=${entry['average_entry']:.2f} qty={entry['total_qty']}"
            )

        self._save()

    def cancel_unfilled(self, ticker: str, reason: str = "end of session") -> None:
        """Cancel all PENDING rung orders (e.g., pre-close cleanup)."""
        if ticker not in self._state or self._state[ticker]["status"] != "ACTIVE":
            return
        entry = self._state[ticker]
        for rung in entry["rungs"]:
            if rung["status"] == "PENDING":
                try:
                    self.trading.cancel_order_by_id(rung["order_id"])
                    rung["status"] = "CANCELLED"
                except Exception as exc:
                    logger.error(f"Cancel rung {rung['rung_index']} failed: {exc}")
        entry["status"] = "PARTIAL_TIMEOUT"
        self._save()
        logger.info(f"[{self.account.name}] {ticker} ladder cancelled ({reason})")

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _recompute_avg(self, entry: dict) -> None:
        filled = [r for r in entry["rungs"] if r["status"] == "FILLED" and r["fill_price"]]
        total_cost = sum(r["fill_price"] * r["fill_qty"] for r in filled)
        total_qty = sum(r["fill_qty"] for r in filled)
        entry["average_entry"] = round(total_cost / total_qty, 4) if total_qty else None
        entry["total_qty"] = total_qty

    def _trigger_hard_stop(self, ticker: str, current_price: float, entry: dict) -> None:
        for rung in entry["rungs"]:
            if rung["status"] == "PENDING":
                try:
                    self.trading.cancel_order_by_id(rung["order_id"])
                    rung["status"] = "CANCELLED"
                except Exception as exc:
                    logger.error(f"Hard stop cancel failed: {exc}")

        filled_qty = sum(r["fill_qty"] for r in entry["rungs"] if r["status"] == "FILLED" and r["fill_qty"])
        if filled_qty >= 1:
            try:
                self.trading.submit_order(
                    MarketOrderRequest(
                        symbol=ticker,
                        qty=int(filled_qty),
                        side=OrderSide.SELL,
                        time_in_force=TimeInForce.DAY,
                    )
                )
            except Exception as exc:
                logger.error(f"Hard stop market sell failed: {exc}")

        entry["status"] = "STOPPED_OUT"
        self._save()
        notifier.hard_stop_triggered(
            account_name=self.account.name,
            ticker=ticker,
            qty=filled_qty,
            exit_price=current_price,
            hard_stop=entry["hard_stop"],
        )

    # ── Queries ────────────────────────────────────────────────────────────────

    @property
    def completed(self) -> dict[str, dict]:
        """Ladders that finished all rungs and are ready for trailing stop handoff."""
        return {k: v for k, v in self._state.items() if v["status"] == "LADDER_COMPLETE"}

    @property
    def active_tickers(self) -> list[str]:
        return [k for k, v in self._state.items() if v["status"] == "ACTIVE"]
