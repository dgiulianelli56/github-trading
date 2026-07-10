"""
Trading engine — orchestrates signal sourcing, ladder entries, and trailing stops
across all three paper accounts.
"""

import logging

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.trading.client import TradingClient

import notifier
import signals.berkshire as berkshire
import signals.congress as congress
from config import ACCOUNTS, AccountConfig
from execution.ladder_buy import LadderBuy
from execution.trailing_stop import TrailingStop

logger = logging.getLogger(__name__)


# ── Account agent bundle ────────────────────────────────────────────────────────

class AccountAgent:
    def __init__(self, config: AccountConfig) -> None:
        self.config = config
        self.trading = TradingClient(config.key, config.secret, paper=True)
        self.data = StockHistoricalDataClient(config.key, config.secret)
        self.ladder = LadderBuy(config, self.trading, self.data)
        self.stop = TrailingStop(config, self.trading, self.data)

    def sync_handoffs(self) -> None:
        """Promote any completed ladders into the trailing stop monitor."""
        for ticker, entry in self.ladder.completed.items():
            if ticker not in self.stop.monitored_tickers:
                self.stop.register(
                    ticker=ticker,
                    entry_price=entry["average_entry"],
                    qty=entry["total_qty"],
                    entry_date=entry["activated_at"][:10],
                )


# ── Watchlist builder ───────────────────────────────────────────────────────────

def build_watchlist() -> dict[str, str]:
    """
    Merge congressional buys + Berkshire holdings into a priority-ranked watchlist.
    Returns an ordered dict of {ticker: source_label}.

    Tier 1 — tickers bought by multiple politicians AND held by Berkshire (highest conviction)
    Tier 2 — tickers bought by multiple politicians (congressional cluster)
    Tier 3 — Berkshire top-10 holdings
    Tier 4 — all other recent congressional buys
    """
    trades = congress.get_recent_trades(days_back=14)
    congress_all = congress.all_bought_tickers(trades)
    congress_high = set(congress.high_conviction_tickers(trades, min_politicians=2))
    berk = berkshire.watchlist(top_n=10)
    berk_set = set(berk)

    watchlist: dict[str, str] = {}

    for t in congress_high:
        if t in berk_set and t not in watchlist:
            watchlist[t] = "Congressional + Berkshire"
    for t in congress_high:
        if t not in watchlist:
            watchlist[t] = "Congressional"
    for t in berk:
        if t not in watchlist:
            watchlist[t] = "Berkshire"
    for t in congress_all:
        if t not in watchlist:
            watchlist[t] = "Congressional"

    logger.info(f"Watchlist ({len(watchlist)}): {list(watchlist.keys())}")
    if trades:
        logger.info(congress.summary(trades))
    return watchlist


# ── Engine ──────────────────────────────────────────────────────────────────────

class Engine:
    def __init__(self) -> None:
        self.agents = {aid: AccountAgent(cfg) for aid, cfg in ACCOUNTS.items()}

    def _all_agents(self):
        return self.agents.values()

    # ── Scheduled run methods ──────────────────────────────────────────────────

    def run_premarket(self) -> None:
        """
        9:15 AM ET — refresh watchlist and log upcoming candidates.
        No orders placed yet; let early-morning gap volatility settle.
        """
        logger.info("=== PRE-MARKET (9:15 AM) ===")
        watchlist = build_watchlist()
        all_monitored = sorted(set(
            t for a in self._all_agents() for t in a.stop.monitored_tickers
        ))
        notifier.scan_heartbeat(
            "Pre-market",
            monitored_tickers=all_monitored,
            watchlist=[f"{t} ({src})" for t, src in watchlist.items()],
            new_signals=[],
        )

    def run_midopen(self) -> None:
        """
        10:00 AM ET — activate ladders for new watchlist entries;
        check fills on existing ladders; sync handoffs to trailing stop.
        """
        logger.info("=== MID-OPEN (10:00 AM) ===")
        watchlist = build_watchlist()

        new_signals: list[str] = []
        for agent in self._all_agents():
            for ticker, source in watchlist.items():
                ladder_status = agent.ladder._state.get(ticker, {}).get("status")
                already_active = (
                    ladder_status in ("ACTIVE", "LADDER_COMPLETE", "STOPPED_OUT")
                    or ticker in agent.stop.monitored_tickers
                )
                if not already_active:
                    result = agent.ladder.activate(ticker)
                    if result:
                        new_signals.append(f"{ticker} ladder activated in {agent.config.name}")
                        notifier.new_signal(
                            account_name=agent.config.name,
                            ticker=ticker,
                            source=source,
                            rungs=[(r["target_price"], r["qty"]) for r in result["rungs"]],
                            hard_stop=result["hard_stop"],
                        )

            for ticker in agent.ladder.active_tickers:
                agent.ladder.check_fills(ticker)
            agent.sync_handoffs()

        all_monitored = sorted(set(
            t for a in self._all_agents() for t in a.stop.monitored_tickers
        ))
        notifier.scan_heartbeat(
            "Mid-open",
            monitored_tickers=all_monitored,
            watchlist=[f"{t} ({src})" for t, src in watchlist.items()],
            new_signals=new_signals,
        )

    def run_midday(self) -> None:
        """
        12:00 PM and 2:30 PM ET — check ladder fills and trailing stops.
        """
        logger.info("=== MIDDAY / AFTERNOON CHECK ===")
        for agent in self._all_agents():
            for ticker in agent.ladder.active_tickers:
                agent.ladder.check_fills(ticker)
            agent.sync_handoffs()
            for ticker in agent.stop.monitored_tickers:
                agent.stop.check(ticker)

    def run_preclose(self) -> None:
        """
        3:45 PM ET — cancel unfilled ladder rungs; final trailing-stop check.
        Unfilled limit orders should not sit open over the weekend.
        """
        logger.info("=== PRE-CLOSE (3:45 PM) ===")
        for agent in self._all_agents():
            for ticker in list(agent.ladder.active_tickers):
                agent.ladder.cancel_unfilled(ticker, reason="pre-close")
            for ticker in agent.stop.monitored_tickers:
                agent.stop.check(ticker)
