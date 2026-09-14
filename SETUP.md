# ContextSynapse — Setup & Demo Guide

## Quick Start

```bash
git clone https://github.com/contextsynapse/contextsynapse.git
cd contextsynapse
pip install -e "."
```

## Run the Demo

The demo builds a **Customer Support Platform** to show how context transforms support from robotic to empathetic.

```bash
# 1. Seed demo data (creates customers, tickets, knowledge base)
python scripts/seed_demo.py

# 2. Run the hero demo (interactive walkthrough)
python scripts/hero_demo.py
```

### What the Demo Shows

**The Problem:** A customer calls about video buffering. The agent knows nothing — asks for account number, plan, issue details. Customer repeats everything. Frustrated.

**With ContextSynapse:** The agent sees everything BEFORE picking up:

```
Customer:  Alice Chen — Premium, 18 months, Rs 8,982 lifetime value
Sentiment: FRUSTRATED (2 recent issues)
Pattern:   Repeat contact — also has unresolved billing ticket
KB Match:  "Fix Video Buffering" article (92% relevant)
```

One context graph. One API call. The agent says "I see you're having buffering issues AND we double-charged you — let me fix both." Resolution: 4 minutes instead of 25.

### Demo Steps

| # | What | Shows |
|---|------|-------|
| 1 | Support without context | The problem — agent knows nothing |
| 2 | Context graph | Customers + tickets + products + KB connected |
| 3 | Context assembly | One call assembles everything agent needs |
| 4 | The difference | Same call, with context — 4 min vs 25 min |
| 5 | PII masking | Admin sees full email, agent sees masked |
| 6 | Workflows | Escalation + auto-approved refund |
| 7 | Pattern detection | Graph finds at-risk customers before they churn |
| 8 | The platform | 10 capabilities you get for free |

## Build Your Own Vertical

See the [README](README.md#build-your-own-vertical) for a complete boilerplate.

```python
from contextsynapse.app_factory import create_app

app = create_app(
    title="My App",
    verticals={"myapp": {"register": register_fn, "routers": [my_router]}},
)
# uvicorn app:app
```

## Full Stack (with PostgreSQL + Redis)

```bash
# Start infrastructure
docker compose up -d postgres redis

# Set database URL
export DATABASE_URL=postgresql://contextsynapse:contextsynapse@localhost:5432/contextsynapse

# Run with full stack
./scripts/setup.sh
```
