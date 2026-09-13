"""Stock Price Feed — fetch and store price data as Indicator nodes.

Fetches stock prices from Yahoo Finance (free, no API key needed) and
stores them as Indicator nodes with precise timestamps for correlation
with news events.

Usage:
    from contextsynapse.intelligence.price_feed import PriceFeed

    feed = PriceFeed()
    indicators = feed.fetch("TCS.NS")  # NSE ticker
    # → [IndicatorNode(name="TCS.NS Price", value=4235.50, timestamp="2026-08-31T10:30:00+05:30"), ...]

    # Ingest into context
    feed.ingest_into_graph(db, "TCS.NS")
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class PricePoint:
    """A single price data point."""
    ticker: str
    price: float
    volume: int = 0
    open: float = 0
    high: float = 0
    low: float = 0
    close: float = 0
    change_pct: float = 0
    timestamp: str = ""
    source: str = "yahoo_finance"

    def to_indicator_props(self) -> Dict[str, Any]:
        return {
            "name": f"{self.ticker} Price",
            "value": self.price,
            "unit": "INR" if self.ticker.endswith(".NS") or self.ticker.endswith(".BO") else "USD",
            "period": self.timestamp[:10] if self.timestamp else "",
            "entity": self.ticker.split(".")[0],  # TCS.NS → TCS
            "column_header": "Stock Price",
            "raw_value": f"{self.price:.2f}",
            "source_type": "price_feed",
            "_source_name": self.source,
            "_published_at": self.timestamp,
            "_event_date": self.timestamp[:10] if self.timestamp else "",
            "_price_open": self.open,
            "_price_high": self.high,
            "_price_low": self.low,
            "_price_close": self.close,
            "_price_volume": self.volume,
            "_price_change_pct": self.change_pct,
            "confidence": 1.0,
        }


# Default ticker mapping — can be overridden by domain plugins
# Plugins should call PriceFeed.register_tickers() to add their own
TICKER_MAP = {}
COMPANY_FOR_TICKER = {}


class PriceFeed:
    """Fetches stock prices from Yahoo Finance."""

    def __init__(self):
        self._cache: Dict[str, List[PricePoint]] = {}
        self._last_fetch: Dict[str, float] = {}
        self._cache_ttl = 300  # 5 minutes

    def register_tickers(self, ticker_map: dict):
        """Register ticker mappings from a domain plugin."""
        global TICKER_MAP, COMPANY_FOR_TICKER
        TICKER_MAP.update(ticker_map)
        COMPANY_FOR_TICKER.update({v: k for k, v in ticker_map.items()})

    def get_ticker(self, company_name: str) -> str:
        """Look up ticker for a company name."""
        return TICKER_MAP.get(company_name, "")

    def fetch(self, ticker: str, period: str = "1d", interval: str = "15m") -> List[PricePoint]:
        """Fetch price data from Yahoo Finance via yfinance.

        Args:
            ticker: Yahoo Finance ticker (e.g., "TCS.NS")
            period: "1d", "5d", "1mo"
            interval: "1m", "5m", "15m", "1h", "1d"

        Returns list of PricePoint objects.
        """
        cache_key = f"{ticker}:{period}:{interval}"
        if cache_key in self._cache:
            age = time.time() - self._last_fetch.get(cache_key, 0)
            if age < self._cache_ttl:
                return self._cache[cache_key]

        try:
            import yfinance as yf

            t = yf.Ticker(ticker)
            hist = t.history(period=period, interval=interval)

            if hist.empty:
                logger.warning("[PRICE] No data for %s", ticker)
                return []

            points = []
            prev_close = None

            for ts, row in hist.iterrows():
                close = float(row.get("Close", 0) or 0)
                if close == 0:
                    continue

                change_pct = 0
                if prev_close and prev_close > 0:
                    change_pct = round(((close - prev_close) / prev_close) * 100, 2)

                # ts is a pandas Timestamp — convert to UTC ISO string
                dt = ts.to_pydatetime()
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)

                points.append(PricePoint(
                    ticker=ticker,
                    price=round(close, 2),
                    open=round(float(row.get("Open", 0) or 0), 2),
                    high=round(float(row.get("High", 0) or 0), 2),
                    low=round(float(row.get("Low", 0) or 0), 2),
                    close=round(close, 2),
                    volume=int(row.get("Volume", 0) or 0),
                    change_pct=change_pct,
                    timestamp=dt.isoformat(),
                ))
                prev_close = close

            self._cache[cache_key] = points
            self._last_fetch[cache_key] = time.time()

            logger.info("[PRICE] Fetched %d points for %s (%s/%s)", len(points), ticker, period, interval)
            return points

        except Exception as exc:
            logger.warning("[PRICE] Fetch failed for %s: %s", ticker, exc)
            return []

    def get_latest_price(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Return the single latest price point for a ticker (live quote)."""
        try:
            import yfinance as yf
            t = yf.Ticker(ticker)
            info = t.fast_info
            price = float(getattr(info, "last_price", None) or 0)
            prev_close = float(getattr(info, "previous_close", None) or 0)
            change_pct = 0.0
            if prev_close > 0 and price > 0:
                change_pct = round(((price - prev_close) / prev_close) * 100, 2)
            return {
                "ticker": ticker,
                "price": round(price, 2),
                "prev_close": round(prev_close, 2),
                "change_pct": change_pct,
                "currency": getattr(info, "currency", "INR"),
                "market_cap": getattr(info, "market_cap", None),
                "source": "yahoo_finance",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as exc:
            logger.warning("[PRICE] Live quote failed for %s: %s", ticker, exc)
            return None

    def ingest_into_graph(self, db, ticker: str, period: str = "5d", interval: str = "1h") -> int:
        """Fetch price data and create Indicator nodes in the graph.

        Returns number of Indicator nodes created.
        """
        from ..core.graph_structures import GraphNode
        import uuid

        points = self.fetch(ticker, period=period, interval=interval)
        if not points:
            return 0

        created = 0
        company = COMPANY_FOR_TICKER.get(ticker, ticker.split(".")[0])

        for point in points:
            props = point.to_indicator_props()
            props["entity"] = company

            node_id = f"price_{ticker}_{point.timestamp[:16].replace(':', '').replace('-', '')}"

            try:
                db.add_node(GraphNode(
                    id=node_id,
                    label="Indicator",
                    properties=props,
                ))
                created += 1
            except Exception:
                pass  # duplicate node ID — already exists

        logger.info("[PRICE] Ingested %d price points for %s into graph", created, ticker)
        return created

    def fetch_all_for_context(self, company_name: str) -> List[PricePoint]:
        """Convenience: fetch price data for a company by name."""
        ticker = self.get_ticker(company_name)
        if not ticker:
            logger.warning("[PRICE] No ticker mapping for %s", company_name)
            return []
        return self.fetch(ticker)
