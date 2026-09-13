"""Fundamental Feed — fetch and store company financial data as Indicator + Fact nodes.

Data sources (all free, no API key required):
  - yfinance: quarterly income statement, cashflow, balance sheet, valuation, dividends

Per-company data fetched:
  - Quarterly P&L: Revenue, EBITDA, Operating Income, Net Income, EPS (last 4 quarters)
  - Quarterly Cashflow: Operating CF, Free CF, CapEx (last 4 quarters)
  - Quarterly Balance Sheet: Total Debt, Equity, Cash (last 4 quarters)
  - Valuation snapshot: Market Cap, P/E, P/B, Dividend Yield, ROE, Profit Margin
  - Shareholding: Insider %, Institutional %
  - Upcoming earnings: date + analyst estimates
  - Recent dividends: last 3 payouts

Usage:
    from contextsynapse.intelligence.fundamental_feed import FundamentalFeed

    feed = FundamentalFeed()
    nodes_created = feed.ingest_into_graph(db, ticker="TCS.NS", bse_code="532540", company="TCS")
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Quarter label helpers ────────────────────────────────────────────────────

def _quarter_label(date) -> str:
    """Convert a date to Indian FY quarter label: 2026-06-30 → Q1FY27."""
    try:
        if hasattr(date, 'to_pydatetime'):
            date = date.to_pydatetime()
        m, y = date.month, date.year
        if m >= 4:
            fy = y + 1
            q = (m - 4) // 3 + 1   # Apr=1, Jul=2, Oct=3
        else:
            fy = y
            q = 4                   # Jan/Feb/Mar = Q4 of previous FY
        return f"Q{q}FY{fy % 100:02d}"
    except Exception:
        return str(date)[:10] if date else ""


def _fmt_cr(value: float) -> str:
    """Format a value in INR crore for human-readable fact text."""
    if abs(value) >= 1_00_000:
        return f"₹{value / 1_00_000:.1f}L Cr"
    return f"₹{value:,.0f} Cr"


def _yoy_pct(curr: float, prev: float) -> Optional[float]:
    if prev and prev != 0:
        return round((curr - prev) / abs(prev) * 100, 1)
    return None


# ── Node ID helpers ──────────────────────────────────────────────────────────

def _ind_id(ticker: str, metric: str, period: str) -> str:
    """Deterministic Indicator node ID — prevents duplicate ingestion."""
    safe = ticker.replace(".", "").replace("/", "").lower()
    metric_safe = metric.lower().replace(" ", "_")
    period_safe = period.lower().replace(" ", "")
    return f"fundamental_{safe}_{metric_safe}_{period_safe}"


# ── FundamentalFeed ──────────────────────────────────────────────────────────

class FundamentalFeed:
    """Fetches and ingests company fundamental data via yfinance."""

    # Rows to pull from quarterly_income_stmt
    _INCOME_ROWS = [
        ("Total Revenue",       "Revenue",          "INR_Cr"),
        ("EBITDA",              "EBITDA",           "INR_Cr"),
        ("Operating Income",    "Operating Income", "INR_Cr"),
        ("Net Income",          "Net Income",       "INR_Cr"),
        ("Diluted EPS",         "EPS",              "INR"),
        ("Gross Profit",        "Gross Profit",     "INR_Cr"),
    ]

    # Rows from quarterly_cashflow
    _CASHFLOW_ROWS = [
        ("Operating Cash Flow", "Operating CF", "INR_Cr"),
        ("Free Cash Flow",      "Free CF",      "INR_Cr"),
        ("Capital Expenditure", "CapEx",        "INR_Cr"),
    ]

    # Rows from quarterly_balance_sheet
    _BALANCE_ROWS = [
        ("Total Debt",              "Total Debt",  "INR_Cr"),
        ("Common Stock Equity",     "Equity",      "INR_Cr"),
        ("Cash And Cash Equivalents", "Cash",      "INR_Cr"),
    ]

    # Fields from ticker.info for valuation snapshot
    _INFO_FIELDS = [
        ("marketCap",       "Market Cap",       "INR"),
        ("trailingPE",      "P/E (TTM)",        "x"),
        ("forwardPE",       "P/E (Fwd)",        "x"),
        ("priceToBook",     "P/B",              "x"),
        ("dividendYield",   "Dividend Yield",   "%"),
        ("trailingEps",     "EPS (TTM)",        "INR"),
        ("profitMargins",   "Profit Margin",    "%"),
        ("operatingMargins","Operating Margin", "%"),
        ("returnOnEquity",  "ROE",              "%"),
        ("revenueGrowth",   "Revenue Growth",   "%"),
        ("earningsGrowth",  "Earnings Growth",  "%"),
        ("debtToEquity",    "Debt/Equity",      "x"),
        ("currentRatio",    "Current Ratio",    "x"),
    ]

    def __init__(self):
        self._yf_cache: Dict[str, Any] = {}

    def _get_ticker(self, ticker: str):
        if ticker not in self._yf_cache:
            try:
                import yfinance as yf
                self._yf_cache[ticker] = yf.Ticker(ticker)
            except Exception as exc:
                logger.warning("[FUNDAMENTAL] yfinance unavailable: %s", exc)
                return None
        return self._yf_cache[ticker]

    # ── Quarterly statement helpers ──────────────────────────────────────────

    def _extract_quarterly_rows(
        self,
        stmt,
        row_map: List[Tuple[str, str, str]],
        ticker: str,
        is_cr: bool = True,
    ) -> List[Dict]:
        """Extract rows from a quarterly DataFrame → list of Indicator dicts."""
        if stmt is None or stmt.empty:
            return []

        cols = list(stmt.columns[:4])  # last 4 quarters
        indicators = []

        for yf_row, metric, unit in row_map:
            if yf_row not in stmt.index:
                continue
            try:
                row_data = stmt.loc[yf_row]
            except Exception:
                continue

            prev_val = None
            for col in cols:
                try:
                    raw = float(row_data[col])
                    if raw != raw:  # NaN
                        continue
                    # Convert rupee values from absolute to crore
                    val = round(raw / 1e7, 2) if is_cr and unit == "INR_Cr" else round(raw, 4)
                    period = _quarter_label(col)
                    yoy = _yoy_pct(val, prev_val) if prev_val is not None else None
                    indicators.append({
                        "node_id": _ind_id(ticker, metric, period),
                        "metric": metric,
                        "unit": unit,
                        "value": val,
                        "period": period,
                        "yoy_growth": yoy,
                        "date": str(col)[:10],
                    })
                    prev_val = val
                except Exception:
                    continue

        return indicators

    # ── Valuation snapshot ───────────────────────────────────────────────────

    def _extract_valuation(self, info: Dict, ticker: str) -> List[Dict]:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        indicators = []
        for yf_key, metric, unit in self._INFO_FIELDS:
            raw = info.get(yf_key)
            if raw is None:
                continue
            try:
                val = float(raw)
                if val != val:
                    continue
                # Convert fractions to percentages
                if unit == "%" and abs(val) < 1:
                    val = round(val * 100, 2)
                # Convert market cap to crore
                if metric == "Market Cap":
                    val = round(val / 1e7, 0)
                    unit = "INR_Cr"
                else:
                    val = round(val, 4)
                indicators.append({
                    "node_id": _ind_id(ticker, metric, today),
                    "metric": metric,
                    "unit": unit,
                    "value": val,
                    "period": today,
                    "yoy_growth": None,
                    "date": today,
                })
            except Exception:
                continue
        return indicators

    # ── Shareholding ─────────────────────────────────────────────────────────

    def _extract_shareholding(self, t, ticker: str) -> List[Dict]:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        indicators = []
        try:
            mh = t.major_holders
            if mh is None or mh.empty:
                return []
            mapping = {
                "insidersPercentHeld":     "Insider Holding",
                "institutionsPercentHeld": "Institutional Holding",
            }
            for idx_val in mh.index:
                label = mapping.get(str(idx_val))
                if not label:
                    continue
                try:
                    val = float(mh.loc[idx_val, "Value"])
                    if val != val:
                        continue
                    val = round(val * 100, 2)
                    indicators.append({
                        "node_id": _ind_id(ticker, label, today),
                        "metric": label,
                        "unit": "%",
                        "value": val,
                        "period": today,
                        "yoy_growth": None,
                        "date": today,
                    })
                except Exception:
                    continue
        except Exception as exc:
            logger.debug("[FUNDAMENTAL] Shareholding fetch failed: %s", exc)
        return indicators

    # ── Dividends ─────────────────────────────────────────────────────────────

    def _extract_dividends(self, t, ticker: str) -> List[Dict]:
        """Return last 3 dividend payouts as fact-ready dicts."""
        facts = []
        try:
            divs = t.dividends
            if divs is None or divs.empty:
                return []
            for date, amount in divs.tail(3).items():
                date_str = str(date)[:10]
                try:
                    dt = date.to_pydatetime() if hasattr(date, 'to_pydatetime') else date
                    date_str = dt.strftime("%Y-%m-%d")
                except Exception:
                    pass
                facts.append({
                    "date": date_str,
                    "amount": round(float(amount), 2),
                })
        except Exception as exc:
            logger.debug("[FUNDAMENTAL] Dividend fetch failed: %s", exc)
        return facts

    # ── Earnings calendar ─────────────────────────────────────────────────────

    def _extract_earnings_calendar(self, t) -> Optional[Dict]:
        try:
            cal = t.calendar
            if not cal:
                return None
            dates = cal.get("Earnings Date", [])
            next_date = str(dates[0]) if dates else ""
            return {
                "next_earnings": next_date,
                "eps_est_low": cal.get("Earnings Low"),
                "eps_est_high": cal.get("Earnings High"),
                "eps_est_avg": cal.get("Earnings Average"),
                "revenue_est_avg": cal.get("Revenue Average"),
            } if next_date else None
        except Exception:
            return None

    # ── Graph builder ─────────────────────────────────────────────────────────

    def ingest_into_graph(
        self,
        db,
        ticker: str,
        company: str,
        bse_code: str = "",
    ) -> int:
        """Fetch all fundamental data and create Indicator + Fact nodes.

        Returns number of nodes created.
        """
        from ..core.graph_structures import GraphNode, GraphEdge

        t = self._get_ticker(ticker)
        if t is None:
            return 0

        created = 0
        is_inr = ticker.endswith(".NS") or ticker.endswith(".BO")
        currency = "INR" if is_inr else "USD"
        now_iso = datetime.now(timezone.utc).isoformat()

        # ── Collect all indicators ────────────────────────────────────────────

        all_indicators: List[Dict] = []

        # Quarterly income statement
        try:
            stmt = t.quarterly_income_stmt
            all_indicators.extend(
                self._extract_quarterly_rows(stmt, self._INCOME_ROWS, ticker, is_cr=is_inr)
            )
        except Exception as exc:
            logger.debug("[FUNDAMENTAL] Income stmt failed for %s: %s", ticker, exc)

        # Quarterly cashflow
        try:
            cf = t.quarterly_cashflow
            all_indicators.extend(
                self._extract_quarterly_rows(cf, self._CASHFLOW_ROWS, ticker, is_cr=is_inr)
            )
        except Exception as exc:
            logger.debug("[FUNDAMENTAL] Cashflow failed for %s: %s", ticker, exc)

        # Quarterly balance sheet
        try:
            bs = t.quarterly_balance_sheet
            all_indicators.extend(
                self._extract_quarterly_rows(bs, self._BALANCE_ROWS, ticker, is_cr=is_inr)
            )
        except Exception as exc:
            logger.debug("[FUNDAMENTAL] Balance sheet failed for %s: %s", ticker, exc)

        # Valuation snapshot
        try:
            info = t.info
            all_indicators.extend(self._extract_valuation(info, ticker))
        except Exception as exc:
            logger.debug("[FUNDAMENTAL] Info failed for %s: %s", ticker, exc)
            info = {}

        # Shareholding
        try:
            all_indicators.extend(self._extract_shareholding(t, ticker))
        except Exception as exc:
            logger.debug("[FUNDAMENTAL] Shareholding failed for %s: %s", ticker, exc)

        # ── Write Indicator nodes ─────────────────────────────────────────────

        for ind in all_indicators:
            props = {
                "name": f"{company} {ind['metric']} {ind['period']}",
                "metric": ind["metric"],
                "value": ind["value"],
                "unit": ind["unit"],
                "period": ind["period"],
                "date": ind["date"],
                "entity": company,
                "ticker": ticker,
                "currency": currency,
                "column_header": ind["metric"],
                "raw_value": str(ind["value"]),
                "source_type": "fundamental_feed",
                "_source_name": "yfinance",
                "_published_at": now_iso,
                "_event_date": ind["date"],
                "_yoy_growth": ind.get("yoy_growth"),
                "confidence": 0.95,
            }
            try:
                db.add_node(GraphNode(
                    id=ind["node_id"],
                    label="Indicator",
                    properties=props,
                ))
                created += 1
            except Exception:
                pass  # already exists — deterministic ID prevents duplicates

        # ── Quarterly earnings summary Fact nodes ─────────────────────────────
        # Group revenue + PAT + EPS by quarter to build one human-readable Fact per quarter

        rev_by_q: Dict[str, float] = {}
        pat_by_q: Dict[str, float] = {}
        ebitda_by_q: Dict[str, float] = {}
        eps_by_q: Dict[str, float] = {}
        yoy_by_q: Dict[str, Optional[float]] = {}

        for ind in all_indicators:
            q = ind["period"]
            if ind["metric"] == "Revenue":
                rev_by_q[q] = ind["value"]
                yoy_by_q[q] = ind.get("yoy_growth")
            elif ind["metric"] == "Net Income":
                pat_by_q[q] = ind["value"]
            elif ind["metric"] == "EBITDA":
                ebitda_by_q[q] = ind["value"]
            elif ind["metric"] == "EPS":
                eps_by_q[q] = ind["value"]

        for q in sorted(rev_by_q.keys(), reverse=True)[:4]:
            rev = rev_by_q.get(q)
            pat = pat_by_q.get(q)
            ebitda = ebitda_by_q.get(q)
            eps = eps_by_q.get(q)
            yoy = yoy_by_q.get(q)

            parts = [f"{company} {q}:"]
            if rev:
                yoy_str = f" ({yoy:+.1f}% YoY)" if yoy is not None else ""
                unit_str = "Cr" if is_inr else "M"
                parts.append(f"Revenue {_fmt_cr(rev) if is_inr else f'${rev:.0f}M'}{yoy_str}")
            if ebitda:
                parts.append(f"EBITDA {_fmt_cr(ebitda) if is_inr else f'${ebitda:.0f}M'}")
            if pat:
                parts.append(f"PAT {_fmt_cr(pat) if is_inr else f'${pat:.0f}M'}")
            if eps:
                parts.append(f"EPS {'₹' if is_inr else '$'}{eps:.2f}")

            statement = ", ".join(parts)
            fact_id = _ind_id(ticker, "earnings_summary", q)
            try:
                db.add_node(GraphNode(
                    id=fact_id,
                    label="Fact",
                    properties={
                        "name": f"{company} {q} Earnings",
                        "statement": statement,
                        "fact_type": "earnings",
                        "period": q,
                        "entity": company,
                        "ticker": ticker,
                        "source_type": "fundamental_feed",
                        "_source_name": "yfinance",
                        "_published_at": now_iso,
                        "_quality": 0.9,
                        "confidence": 0.95,
                    },
                ))
                created += 1
            except Exception:
                pass

        # ── Earnings calendar Fact ────────────────────────────────────────────

        try:
            cal = self._extract_earnings_calendar(t)
            if cal and cal.get("next_earnings"):
                cal_id = _ind_id(ticker, "next_earnings", cal["next_earnings"])
                eps_avg = cal.get("eps_est_avg")
                eps_str = f", EPS est {'₹' if is_inr else '$'}{eps_avg:.2f}" if eps_avg else ""
                db.add_node(GraphNode(
                    id=cal_id,
                    label="Fact",
                    properties={
                        "name": f"{company} Next Earnings",
                        "statement": f"{company} next earnings on {cal['next_earnings']}{eps_str}",
                        "fact_type": "earnings_calendar",
                        "period": cal["next_earnings"],
                        "entity": company,
                        "ticker": ticker,
                        "source_type": "fundamental_feed",
                        "_source_name": "yfinance",
                        "_published_at": now_iso,
                        "_quality": 0.85,
                        "confidence": 0.8,
                    },
                ))
                created += 1
        except Exception as exc:
            logger.debug("[FUNDAMENTAL] Calendar ingest failed for %s: %s", ticker, exc)

        # ── Dividend Fact nodes ───────────────────────────────────────────────

        try:
            divs = self._extract_dividends(t, ticker)
            for div in divs:
                div_id = _ind_id(ticker, "dividend", div["date"])
                amount = div["amount"]
                db.add_node(GraphNode(
                    id=div_id,
                    label="Fact",
                    properties={
                        "name": f"{company} Dividend {div['date']}",
                        "statement": f"{company} paid dividend of {'₹' if is_inr else '$'}{amount:.2f} per share on {div['date']}",
                        "fact_type": "corporate_action",
                        "period": div["date"],
                        "entity": company,
                        "ticker": ticker,
                        "source_type": "fundamental_feed",
                        "_source_name": "yfinance",
                        "_published_at": now_iso,
                        "_quality": 0.9,
                        "confidence": 1.0,
                    },
                ))
                created += 1
        except Exception as exc:
            logger.debug("[FUNDAMENTAL] Dividend ingest failed for %s: %s", ticker, exc)

        logger.info("[FUNDAMENTAL] %s (%s): %d nodes created", company, ticker, created)
        return created

    def fetch_summary(self, ticker: str, company: str) -> Dict[str, Any]:
        """Return a human-readable summary dict (for logging/display, no graph write)."""
        t = self._get_ticker(ticker)
        if t is None:
            return {}

        result: Dict[str, Any] = {"company": company, "ticker": ticker}

        try:
            info = t.info
            result["market_cap_cr"] = round(info.get("marketCap", 0) / 1e7, 0)
            result["pe"] = round(info.get("trailingPE", 0) or 0, 2)
            result["profit_margin_pct"] = round((info.get("profitMargins", 0) or 0) * 100, 1)
        except Exception:
            pass

        try:
            stmt = t.quarterly_income_stmt
            if not stmt.empty and "Total Revenue" in stmt.index:
                latest_col = stmt.columns[0]
                result["latest_quarter"] = _quarter_label(latest_col)
                result["revenue_cr"] = round(float(stmt.loc["Total Revenue", latest_col]) / 1e7, 1)
                if "Net Income" in stmt.index:
                    result["pat_cr"] = round(float(stmt.loc["Net Income", latest_col]) / 1e7, 1)
                if "Diluted EPS" in stmt.index:
                    result["eps"] = round(float(stmt.loc["Diluted EPS", latest_col]), 2)
        except Exception:
            pass

        return result
