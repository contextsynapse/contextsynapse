"""Content strategies — per-content-type configuration for chunking, extraction, and fact patterns.

Each strategy defines HOW to process content for a specific type:
- Chunking: paragraph vs section vs feature-based
- Extraction: what entity types to prioritize
- Facts: what patterns to look for
- Edges: what relationships matter

Usage:
    strategy = get_strategy("news_article")
    chunks = strategy.chunk(body)
    prompt = strategy.build_extraction_prompt(chunk_text, schema)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ChunkConfig:
    """How to chunk content."""
    method: str = "hierarchical"  # hierarchical | paragraph | section | sentence | fixed
    #   hierarchical — section-aware: detects headings, groups paragraphs under sections,
    #                  then splits long sections at sentence boundaries. Each PassageChunk
    #                  carries section_title + section_index for graph hierarchy.
    #   paragraph    — flat: splits on blank lines, merges short, splits long (legacy).
    #   section      — alias for hierarchical (kept for backward compat with strategies).
    #   sentence     — splits at sentence boundaries (short-form content).
    #   fixed        — fixed token windows (not recommended).
    min_tokens: int = 100
    max_tokens: int = 800
    target_tokens: int = 500
    overlap_sentences: int = 1
    split_on_headings: bool = True   # always True for hierarchical; ignored for paragraph
    first_chunk_weight: float = 1.0  # weight multiplier for first chunk (lede)
    quote_as_separate: bool = False  # treat quoted blocks as separate chunks


@dataclass
class ExtractionConfig:
    """What to extract and how."""
    entity_priority: List[str] = field(default_factory=list)  # ordered by importance
    fact_patterns: List[str] = field(default_factory=list)     # regex patterns for facts
    fact_types: List[str] = field(default_factory=list)        # what kind of facts to look for
    edge_focus: List[str] = field(default_factory=list)        # priority edge types
    extraction_hints: str = ""                                  # extra LLM prompt instructions
    extract_quotes: bool = False                                # extract direct quotes as facts
    extract_attributions: bool = False                           # extract "X said Y" patterns


@dataclass
class ContentStrategy:
    """Complete strategy for processing a content type."""
    name: str
    description: str = ""
    chunk_config: ChunkConfig = field(default_factory=ChunkConfig)
    extraction_config: ExtractionConfig = field(default_factory=ExtractionConfig)

    def get_extraction_hints(self) -> str:
        """Generate extra LLM prompt instructions based on strategy."""
        hints = []
        ec = self.extraction_config

        if ec.extraction_hints:
            hints.append(ec.extraction_hints)

        if ec.entity_priority:
            hints.append(f"Priority entities (extract these first): {', '.join(ec.entity_priority)}")

        if ec.fact_types:
            hints.append(f"Focus on these fact types: {', '.join(ec.fact_types)}")

        if ec.extract_quotes:
            hints.append("Extract direct quotes as separate facts with the speaker attributed.")

        if ec.extract_attributions:
            hints.append("For each claim or statement, identify WHO said it (attribution).")

        if ec.edge_focus:
            hints.append(f"Priority relationships: {', '.join(ec.edge_focus)}")

        return "\n".join(hints)

    def get_fact_patterns(self) -> List[re.Pattern]:
        """Compile fact regex patterns."""
        compiled = []
        for pattern in self.extraction_config.fact_patterns:
            try:
                compiled.append(re.compile(pattern))
            except re.error:
                pass
        return compiled


# ══════════════════════════════════════════════════════════════════════
# Built-in strategies
# ══════════════════════════════════════════════════════════════════════

NEWS_ARTICLE = ContentStrategy(
    name="news_article",
    description="For news articles, press releases, and current events",
    chunk_config=ChunkConfig(
        method="hierarchical",
        min_tokens=100,
        max_tokens=800,
        target_tokens=500,
        overlap_sentences=1,
        first_chunk_weight=1.5,  # lede paragraph gets boosted
        quote_as_separate=True,
    ),
    extraction_config=ExtractionConfig(
        entity_priority=["Person", "Organization", "Location", "Event"],
        fact_patterns=[
            r'[A-Z][^.!?]*\$[\d,.]+\s*(?:billion|million|trillion)[^.!?]*[.!?]',  # money
            r'[A-Z][^.!?]*\d+(?:\.\d+)?%[^.!?]*[.!?]',  # percentages
            r'[A-Z][^.!?]*\d{1,3}(?:,\d{3})+[^.!?]*[.!?]',  # large numbers
            r'"[^"]{10,}"',  # direct quotes
            r'[A-Z][^.!?]*(?:announced|confirmed|stated|said|reported)[^.!?]*[.!?]',  # attributions
        ],
        fact_types=["statistic", "quote", "announcement", "attribution", "date_bound"],
        edge_focus=["WORKS_AT", "LOCATED_IN", "ANNOUNCED", "SAID"],
        extraction_hints=(
            "This is a news article. Focus on:\n"
            "- WHO: people and their roles/titles\n"
            "- WHAT: key events, decisions, announcements\n"
            "- WHERE: locations (countries, cities, regions)\n"
            "- WHEN: dates and timeframes\n"
            "- HOW MUCH: financial figures, statistics, percentages\n"
            "Extract direct quotes as facts with speaker attribution."
        ),
        extract_quotes=True,
        extract_attributions=True,
    ),
)

TECHNICAL_DOC = ContentStrategy(
    name="technical_doc",
    description="For documentation, API docs, technical guides, and specs",
    chunk_config=ChunkConfig(
        method="section",
        min_tokens=150,
        max_tokens=1200,
        target_tokens=800,
        overlap_sentences=0,
        split_on_headings=True,
    ),
    extraction_config=ExtractionConfig(
        entity_priority=["Component", "API", "Technology", "Concept", "Person"],
        fact_patterns=[
            r'(?:must|shall|should|required|MUST|SHALL)\s+[^.!?]+[.!?]',  # requirements
            r'(?:default|defaults? to|configured? (?:as|to|with))\s+[^.!?]+[.!?]',  # defaults
            r'(?:version|v)\s*\d+\.\d+[^.!?]*[.!?]',  # versions
        ],
        fact_types=["requirement", "specification", "constraint", "default", "deprecation"],
        edge_focus=["DEPENDS_ON", "IMPLEMENTS", "USES", "EXTENDS", "CONFIGURES"],
        extraction_hints=(
            "This is technical documentation. Focus on:\n"
            "- Components and their responsibilities\n"
            "- APIs, endpoints, and interfaces\n"
            "- Dependencies between components\n"
            "- Configuration options and defaults\n"
            "- Requirements (must/shall/should statements)"
        ),
    ),
)

RESEARCH_PAPER = ContentStrategy(
    name="research_paper",
    description="For academic papers, research reports, and scientific articles",
    chunk_config=ChunkConfig(
        method="section",
        min_tokens=200,
        max_tokens=1000,
        target_tokens=700,
        overlap_sentences=1,
        split_on_headings=True,
    ),
    extraction_config=ExtractionConfig(
        entity_priority=["Author", "Institution", "Method", "Dataset", "Finding"],
        fact_patterns=[
            r'[A-Z][^.!?]*(?:p\s*[<>=]\s*0\.\d+|significance|significant)[^.!?]*[.!?]',  # p-values
            r'[A-Z][^.!?]*(?:accuracy|precision|recall|F1)\s*(?:of|=|:)\s*\d+[^.!?]*[.!?]',  # metrics
            r'(?:we found|results show|data suggests?|evidence indicates?)[^.!?]*[.!?]',  # findings
        ],
        fact_types=["finding", "methodology", "result", "limitation", "hypothesis"],
        edge_focus=["AUTHORED_BY", "CITES", "USES_METHOD", "TRAINED_ON", "OUTPERFORMS"],
        extraction_hints=(
            "This is a research paper. Focus on:\n"
            "- Authors and their institutions\n"
            "- Key findings and results (with numbers)\n"
            "- Methods and datasets used\n"
            "- Comparisons with prior work\n"
            "- Limitations acknowledged"
        ),
    ),
)

BUSINESS_REPORT = ContentStrategy(
    name="business_report",
    description="For financial reports, earnings calls, market analysis",
    chunk_config=ChunkConfig(
        method="hierarchical",
        min_tokens=100,
        max_tokens=900,
        target_tokens=600,
        overlap_sentences=1,
    ),
    extraction_config=ExtractionConfig(
        entity_priority=["Company", "Person", "Product", "Market", "Metric"],
        fact_patterns=[
            r'[A-Z][^.!?]*\$[\d,.]+\s*(?:billion|million|trillion|B|M|K)[^.!?]*[.!?]',  # money
            r'[A-Z][^.!?]*(?:revenue|profit|loss|margin|growth|decline)[^.!?]*\d+[^.!?]*[.!?]',  # financial + number
            r'[A-Z][^.!?]*(?:Q[1-4]|quarter|fiscal year|FY)[^.!?]*[.!?]',  # quarters
            r'[A-Z][^.!?]*(?:forecast|guidance|outlook|expect)[^.!?]*[.!?]',  # forward looking
            r'[A-Z][^.!?]*\d+(?:\.\d+)?%[^.!?]*[.!?]',  # percentages
            r'[A-Z][^.!?]*(?:announced|reported|confirmed|launched|expanded|held)[^.!?]*[.!?]',  # actions
        ],
        fact_types=["financial_metric", "forecast", "market_share", "growth_rate", "valuation"],
        edge_focus=["COMPETES_WITH", "INVESTED_IN", "ACQUIRED", "REPORTS", "LEADS"],
        extraction_hints=(
            "This is a business/financial document. Focus on:\n"
            "- Companies and their financial metrics\n"
            "- Revenue, profit, growth numbers\n"
            "- Market share and competitive positioning\n"
            "- Forecasts and guidance\n"
            "- Key executives and their statements"
        ),
    ),
)

SOCIAL_MEDIA = ContentStrategy(
    name="social_media",
    description="For social media posts, threads, and comments",
    chunk_config=ChunkConfig(
        method="sentence",
        min_tokens=20,
        max_tokens=300,
        target_tokens=100,
        overlap_sentences=0,
    ),
    extraction_config=ExtractionConfig(
        entity_priority=["Person", "Organization", "Topic", "Hashtag"],
        fact_patterns=[
            r'#\w+',  # hashtags
            r'@\w+',  # mentions
        ],
        fact_types=["opinion", "claim", "announcement"],
        edge_focus=["MENTIONS", "REPLIES_TO", "ABOUT"],
        extraction_hints=(
            "This is social media content. Focus on:\n"
            "- Who is posting (person/org)\n"
            "- Key topics and hashtags\n"
            "- Opinions vs facts\n"
            "- Mentions of other people/orgs"
        ),
    ),
)

STOCK_NEWS = ContentStrategy(
    name="stock_news",
    description="For stock-related news, earnings reports, regulatory filings, and analyst notes",
    chunk_config=ChunkConfig(
        method="hierarchical",
        min_tokens=80,
        max_tokens=700,
        target_tokens=450,
        overlap_sentences=1,
        first_chunk_weight=1.5,
        quote_as_separate=True,
    ),
    extraction_config=ExtractionConfig(
        entity_priority=["Company", "Person", "Financial Metric", "Product", "Regulator", "Market"],
        fact_patterns=[
            r'[A-Z][^.!?]*\$[\d,.]+\s*(?:billion|million|crore|lakh|trillion|B|M|K|Cr)[^.!?]*[.!?]',  # money
            r'[A-Z][^.!?]*(?:revenue|profit|loss|EBITDA|PAT|EPS|margin|NII|NPA)[^.!?]*\d+[^.!?]*[.!?]',  # financials
            r'[A-Z][^.!?]*(?:Q[1-4]|quarter|FY|fiscal year|half-year|H[12])[^.!?]*[.!?]',  # periods
            r'[A-Z][^.!?]*(?:guidance|outlook|forecast|target|expect)[^.!?]*[.!?]',  # forward looking
            r'[A-Z][^.!?]*\d+(?:\.\d+)?%[^.!?]*[.!?]',  # percentages
            r'[A-Z][^.!?]*(?:buy|sell|hold|upgrade|downgrade|target price)[^.!?]*[.!?]',  # analyst actions
            r'[A-Z][^.!?]*(?:promoter|FII|DII|institutional|retail)[^.!?]*[.!?]',  # shareholding
            r'[A-Z][^.!?]*(?:merger|acquisition|stake|divest|subsidiary)[^.!?]*[.!?]',  # corporate actions
        ],
        fact_types=[
            "financial_metric", "earnings", "forecast", "analyst_rating",
            "corporate_action", "regulatory", "shareholding", "valuation",
        ],
        edge_focus=[
            "REPORTS", "ACQUIRES", "COMPETES_WITH", "REGULATED_BY",
            "ANALYST_COVERS", "INVESTED_IN", "SUBSIDIARY_OF",
        ],
        extraction_hints=(
            "This is a stock market / financial news article about an Indian listed company.\n"
            "Focus on:\n"
            "- Financial metrics: revenue, profit, EBITDA, EPS, margins (with numbers)\n"
            "- Quarterly/annual results (Q1/Q2/Q3/Q4 FY comparisons)\n"
            "- Analyst actions: upgrades, downgrades, target price changes\n"
            "- Promoter / FII / DII shareholding changes\n"
            "- Corporate actions: acquisitions, mergers, dividends, buybacks\n"
            "- Regulatory events: SEBI, RBI, MCA filings\n"
            "- Management commentary and guidance\n"
            "Extract numbers precisely. Attribute statements to speakers."
        ),
        extract_quotes=True,
        extract_attributions=True,
    ),
)

WEBSITE = ContentStrategy(
    name="website",
    description="Generic web page — blog post, landing page, about page, product page",
    chunk_config=ChunkConfig(
        method="hierarchical",
        min_tokens=60,
        max_tokens=600,
        target_tokens=350,
        overlap_sentences=1,
        first_chunk_weight=0.8,  # hero/above-fold text is often low-signal
        quote_as_separate=False,
    ),
    extraction_config=ExtractionConfig(
        entity_priority=["Organization", "Product", "Person", "Location", "Topic"],
        fact_patterns=[
            r'[A-Z][^.!?]*\d+(?:\.\d+)?%[^.!?]*[.!?]',           # percentages
            r'[A-Z][^.!?]*\$[\d,.]+\s*(?:billion|million|B|M)[^.!?]*[.!?]',  # money
            r'[A-Z][^.!?]*(?:launched|announced|released|partnered|acquired)[^.!?]*[.!?]',  # events
            r'[A-Z][^.!?]*(?:available|supports?|compatible with|requires?)[^.!?]*[.!?]',   # product specs
        ],
        fact_types=["claim", "statistic", "announcement", "product_spec", "offer"],
        edge_focus=["OFFERS", "LOCATED_IN", "MENTIONS", "RELATED_TO", "COMPETES_WITH"],
        extraction_hints=(
            "This is a web page. Focus on:\n"
            "- What the organization/person/product does or offers\n"
            "- Key claims and statistics\n"
            "- Named entities (people, orgs, places, products, technologies)\n"
            "- Announcements and events\n"
            "Ignore navigation text, cookie notices, and footer boilerplate."
        ),
    ),
)

GENERAL = ContentStrategy(
    name="general",
    description="General purpose — works for any content type",
    chunk_config=ChunkConfig(
        method="paragraph",
        min_tokens=100,
        max_tokens=800,
        target_tokens=500,
        overlap_sentences=1,
    ),
    extraction_config=ExtractionConfig(
        entity_priority=["Person", "Organization", "Location", "Fact"],
        fact_patterns=[
            r'[A-Z][^.!?]*(?:\d+%|(?:19|20)\d{2}|"[^"]+")[^.!?]*[.!?]',  # data sentences
        ],
        fact_types=["statement", "statistic", "claim"],
        edge_focus=["RELATED_TO", "MENTIONS", "WORKS_AT"],
    ),
)

# ══════════════════════════════════════════════════════════════════════
# Strategy registry
# ══════════════════════════════════════════════════════════════════════

STRATEGIES: Dict[str, ContentStrategy] = {
    "stock_news": STOCK_NEWS,
    "news_article": NEWS_ARTICLE,
    "business_report": BUSINESS_REPORT,
    "technical_doc": TECHNICAL_DOC,
    "research_paper": RESEARCH_PAPER,
    "social_media": SOCIAL_MEDIA,
    "website": WEBSITE,
    "general": GENERAL,
}


def get_strategy(name: str) -> ContentStrategy:
    """Get a content strategy by name. Falls back to general."""
    return STRATEGIES.get(name, GENERAL)


def list_strategies() -> Dict[str, str]:
    """List all available strategies with descriptions."""
    return {name: s.description for name, s in STRATEGIES.items()}
