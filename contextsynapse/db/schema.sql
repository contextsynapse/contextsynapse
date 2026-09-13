-- ContextCore Platform Schema
-- Users, tenants, roles, audit, notifications, invoices, settlements

-- ============================================================
-- Identity & Auth
-- ============================================================

CREATE TABLE IF NOT EXISTS tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    plan TEXT DEFAULT 'free',
    status TEXT DEFAULT 'active',
    settings JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id),
    email TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    password_hash TEXT,
    phone TEXT,
    pan TEXT,
    avatar_url TEXT,
    auth_provider TEXT DEFAULT 'local',
    auth_provider_id TEXT,
    status TEXT DEFAULT 'active',
    last_login_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_roles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    vertical TEXT NOT NULL,
    role TEXT NOT NULL,
    scoped_ids TEXT[] DEFAULT '{}',
    granted_by UUID REFERENCES users(id),
    granted_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(user_id, tenant_id, vertical, role)
);

-- ============================================================
-- Sessions
-- ============================================================

CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL,
    ip_address TEXT,
    user_agent TEXT,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_session_user ON sessions(user_id, expires_at DESC);

-- ============================================================
-- Audit Trail
-- ============================================================

CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID REFERENCES tenants(id),
    user_id UUID REFERENCES users(id),
    action TEXT NOT NULL,
    vertical TEXT,
    resource_type TEXT,
    resource_id TEXT,
    details JSONB DEFAULT '{}',
    ip_address TEXT,
    user_agent TEXT,
    status TEXT DEFAULT 'success',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_tenant_time ON audit_log(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id, created_at DESC);

-- ============================================================
-- Notifications
-- ============================================================

CREATE TABLE IF NOT EXISTS notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id),
    recipient_id UUID REFERENCES users(id),
    channel TEXT DEFAULT 'ui',
    priority TEXT DEFAULT 'normal',
    title TEXT NOT NULL,
    message TEXT,
    action_url TEXT,
    vertical TEXT,
    status TEXT DEFAULT 'pending',
    read_at TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_notif_recipient ON notifications(recipient_id, status, created_at DESC);

-- ============================================================
-- Invoices
-- ============================================================

CREATE TABLE IF NOT EXISTS invoices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id),
    vertical TEXT NOT NULL,
    portfolio_id TEXT,
    scheme_id TEXT,
    client_id TEXT,
    quarter TEXT,
    year INTEGER,
    fixed_fee NUMERIC(15,2) DEFAULT 0,
    profit_share NUMERIC(15,2) DEFAULT 0,
    gst NUMERIC(15,2) DEFAULT 0,
    total_fee NUMERIC(15,2) DEFAULT 0,
    high_watermark NUMERIC(15,2),
    status TEXT DEFAULT 'issued',
    issued_at TIMESTAMPTZ DEFAULT now(),
    due_date DATE,
    paid_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- Settlements
-- ============================================================

CREATE TABLE IF NOT EXISTS settlements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id),
    vertical TEXT NOT NULL,
    trade_id TEXT NOT NULL,
    stock_symbol TEXT,
    action TEXT,
    quantity NUMERIC(15,4),
    price NUMERIC(15,4),
    value NUMERIC(15,2),
    execution_date DATE,
    settlement_date DATE,
    status TEXT DEFAULT 'pending',
    settled_at TIMESTAMPTZ,
    failure_reason TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_settlement_status ON settlements(status, settlement_date);

-- ============================================================
-- Corporate Actions
-- ============================================================

CREATE TABLE IF NOT EXISTS corporate_actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id),
    vertical TEXT NOT NULL,
    stock_symbol TEXT NOT NULL,
    action_type TEXT NOT NULL,
    details JSONB DEFAULT '{}',
    portfolio_id TEXT,
    scheme_id TEXT,
    status TEXT DEFAULT 'pending',
    processed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- Compliance Rules
-- ============================================================

CREATE TABLE IF NOT EXISTS compliance_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id),
    vertical TEXT NOT NULL DEFAULT 'pms',
    rule_id TEXT UNIQUE NOT NULL,         -- human-readable ID like rule_abc123
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    category TEXT DEFAULT 'custom',       -- compliance, risk, portfolio, custom
    severity TEXT DEFAULT 'medium',       -- critical, high, medium, low, info
    conditions JSONB DEFAULT '[]',        -- [{field, operator, value, unit}]
    conditions_operator TEXT DEFAULT 'AND', -- AND | OR
    actions JSONB DEFAULT '[]',           -- [{type, params}]
    scope TEXT DEFAULT 'all',             -- 'all' or specific portfolio_id
    triggers JSONB DEFAULT '["on_demand"]', -- when to evaluate
    enabled BOOLEAN DEFAULT true,
    version INTEGER DEFAULT 1,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rules_tenant ON compliance_rules(tenant_id, vertical);
CREATE INDEX IF NOT EXISTS idx_rules_enabled ON compliance_rules(enabled, category);
CREATE INDEX IF NOT EXISTS idx_rules_rule_id ON compliance_rules(rule_id);

CREATE TABLE IF NOT EXISTS rule_executions (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID REFERENCES tenants(id),
    rule_id TEXT NOT NULL,
    portfolio_id TEXT,
    scheme_id TEXT,
    status TEXT NOT NULL,                 -- passed, violated, warning, error
    severity TEXT,
    message TEXT,
    details JSONB DEFAULT '{}',
    executed_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rule_exec_rule ON rule_executions(rule_id, executed_at DESC);
CREATE INDEX IF NOT EXISTS idx_rule_exec_portfolio ON rule_executions(portfolio_id, executed_at DESC);

CREATE TABLE IF NOT EXISTS rule_portfolios (
    rule_id TEXT NOT NULL,
    portfolio_id TEXT NOT NULL,
    PRIMARY KEY (rule_id, portfolio_id)
);

-- ============================================================
-- Fund Manager Profiles
-- ============================================================

CREATE TABLE IF NOT EXISTS fund_manager_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    tenant_id UUID REFERENCES tenants(id),

    -- Personal
    display_name TEXT NOT NULL,
    title TEXT DEFAULT '',                 -- 'Senior Fund Manager', 'CIO', etc.
    bio TEXT DEFAULT '',
    avatar_url TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    linkedin_url TEXT DEFAULT '',

    -- Professional / SEBI
    sebi_registration_no TEXT DEFAULT '',   -- PMS registration
    arn_number TEXT DEFAULT '',             -- AMFI Registration (for MF)
    nism_certification TEXT DEFAULT '',     -- NISM Series V-A / V-B
    experience_years INTEGER DEFAULT 0,
    specialization TEXT DEFAULT '',         -- 'Equity', 'Debt', 'Multi-Asset', 'Quant'
    investment_philosophy TEXT DEFAULT '',  -- free text

    -- Performance
    total_aum NUMERIC(15,2) DEFAULT 0,     -- auto-computed from portfolios
    portfolios_managed INTEGER DEFAULT 0,
    schemes_managed INTEGER DEFAULT 0,
    clients_served INTEGER DEFAULT 0,
    since_date DATE,                       -- managing since
    best_year_return NUMERIC(8,2),
    avg_annual_return NUMERIC(8,2),

    -- Preferences
    preferred_sectors JSONB DEFAULT '[]',   -- ['IT', 'Banking', 'Pharma']
    risk_appetite TEXT DEFAULT 'moderate',  -- conservative, moderate, aggressive
    benchmark TEXT DEFAULT 'Nifty 50',

    -- Settings
    notification_preferences JSONB DEFAULT '{"email": true, "sms": false, "push": true}',
    dashboard_layout JSONB DEFAULT '{}',
    timezone TEXT DEFAULT 'Asia/Kolkata',

    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_profile_user ON fund_manager_profiles(user_id);
CREATE INDEX IF NOT EXISTS idx_profile_tenant ON fund_manager_profiles(tenant_id);

-- ============================================================
-- Graph Storage (PostgreSQL-backed graph nodes + edges)
-- ============================================================

CREATE TABLE IF NOT EXISTS graph_nodes (
    id TEXT NOT NULL,
    tenant_id UUID REFERENCES tenants(id),
    namespace TEXT NOT NULL,           -- graph namespace (e.g., "mf_holdings", "tcs")
    label TEXT NOT NULL,               -- node type (e.g., "MFHolding", "FundManager")
    properties JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (namespace, id)
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_gn_namespace ON graph_nodes(namespace);
CREATE INDEX IF NOT EXISTS idx_gn_tenant ON graph_nodes(tenant_id, namespace);
CREATE INDEX IF NOT EXISTS idx_gn_label ON graph_nodes(namespace, label);
CREATE INDEX IF NOT EXISTS idx_gn_props ON graph_nodes USING gin(properties);  -- JSONB index for property queries

CREATE TABLE IF NOT EXISTS graph_edges (
    id TEXT NOT NULL DEFAULT gen_random_uuid()::text,
    tenant_id UUID REFERENCES tenants(id),
    namespace TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    edge_type TEXT NOT NULL,           -- "HELD_BY", "MANAGES", "DEPENDS_ON"
    properties JSONB DEFAULT '{}',
    weight NUMERIC(8,4) DEFAULT 1.0,
    created_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (namespace, id)
);

CREATE INDEX IF NOT EXISTS idx_ge_source ON graph_edges(namespace, source_id);
CREATE INDEX IF NOT EXISTS idx_ge_target ON graph_edges(namespace, target_id);
CREATE INDEX IF NOT EXISTS idx_ge_type ON graph_edges(namespace, edge_type);
CREATE INDEX IF NOT EXISTS idx_ge_tenant ON graph_edges(tenant_id, namespace);

-- ============================================================
-- Mutual Fund Data Warehouse (pointed to by DataSource nodes)
-- ============================================================

CREATE TABLE IF NOT EXISTS mf_schemes (
    id TEXT PRIMARY KEY,
    tenant_id UUID REFERENCES tenants(id),
    scheme_code TEXT NOT NULL,
    scheme_name TEXT NOT NULL,
    fund_house TEXT NOT NULL,
    category TEXT DEFAULT '',              -- Large Cap, Flexi Cap, etc.
    sub_category TEXT DEFAULT '',
    fund_manager TEXT DEFAULT '',
    benchmark TEXT DEFAULT '',
    aum_cr NUMERIC(15,2) DEFAULT 0,
    nav NUMERIC(12,4) DEFAULT 0,
    expense_ratio NUMERIC(6,4) DEFAULT 0,
    inception_date DATE,
    is_active BOOLEAN DEFAULT true,
    metadata JSONB DEFAULT '{}',
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mf_holdings (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID REFERENCES tenants(id),
    snapshot_month TEXT NOT NULL,           -- "2026-08" (temporal key)
    scheme_id TEXT REFERENCES mf_schemes(id),
    scheme_name TEXT NOT NULL,
    fund_house TEXT DEFAULT '',
    stock_symbol TEXT NOT NULL,
    company_name TEXT DEFAULT '',
    isin TEXT DEFAULT '',
    sector TEXT DEFAULT '',
    quantity BIGINT DEFAULT 0,
    market_value_cr NUMERIC(15,2) DEFAULT 0,
    pct_to_aum NUMERIC(8,4) DEFAULT 0,
    prev_quantity BIGINT DEFAULT 0,
    prev_pct NUMERIC(8,4) DEFAULT 0,
    change_quantity BIGINT DEFAULT 0,
    change_pct NUMERIC(8,4) DEFAULT 0,
    is_new_entry BOOLEAN DEFAULT false,
    is_exit BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mfh_snapshot ON mf_holdings(snapshot_month);
CREATE INDEX IF NOT EXISTS idx_mfh_stock ON mf_holdings(stock_symbol, snapshot_month);
CREATE INDEX IF NOT EXISTS idx_mfh_scheme ON mf_holdings(scheme_id, snapshot_month);
CREATE INDEX IF NOT EXISTS idx_mfh_tenant ON mf_holdings(tenant_id);
CREATE INDEX IF NOT EXISTS idx_mfh_sector ON mf_holdings(sector, snapshot_month);

CREATE TABLE IF NOT EXISTS mf_fund_managers (
    id TEXT PRIMARY KEY,
    tenant_id UUID REFERENCES tenants(id),
    name TEXT NOT NULL,
    fund_house TEXT NOT NULL,
    designation TEXT DEFAULT '',
    tenure_years NUMERIC(5,1) DEFAULT 0,
    total_aum_cr NUMERIC(15,2) DEFAULT 0,
    schemes_managed INTEGER DEFAULT 0,
    investment_style TEXT DEFAULT '',        -- Growth, Value, Blend
    top_sectors JSONB DEFAULT '[]',
    track_record JSONB DEFAULT '{}',         -- {"1y": 12.5, "3y": 15.2, "5y": 18.1}
    conviction_stocks JSONB DEFAULT '[]',    -- top holdings across all schemes
    metadata JSONB DEFAULT '{}',
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mf_performance (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID REFERENCES tenants(id),
    scheme_id TEXT REFERENCES mf_schemes(id),
    snapshot_date DATE NOT NULL,
    nav NUMERIC(12,4),
    return_1d NUMERIC(8,4),
    return_1w NUMERIC(8,4),
    return_1m NUMERIC(8,4),
    return_3m NUMERIC(8,4),
    return_6m NUMERIC(8,4),
    return_1y NUMERIC(8,4),
    return_3y NUMERIC(8,4),
    return_5y NUMERIC(8,4),
    benchmark_return_1y NUMERIC(8,4),
    alpha_1y NUMERIC(8,4),
    sharpe_ratio NUMERIC(8,4),
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mfp_scheme ON mf_performance(scheme_id, snapshot_date);

-- ============================================================
-- FII / DII Flow Data
-- ============================================================

-- Daily aggregate flow (FII bought/sold/net per day)
CREATE TABLE IF NOT EXISTS fii_dii_daily (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID REFERENCES tenants(id),
    trade_date DATE NOT NULL,
    category TEXT NOT NULL,                 -- 'FII', 'DII', 'FPI'
    segment TEXT DEFAULT 'cash',            -- 'cash', 'derivatives', 'debt'
    buy_value_cr NUMERIC(15,2) DEFAULT 0,
    sell_value_cr NUMERIC(15,2) DEFAULT 0,
    net_value_cr NUMERIC(15,2) DEFAULT 0,
    cumulative_mtd_cr NUMERIC(15,2) DEFAULT 0,
    cumulative_ytd_cr NUMERIC(15,2) DEFAULT 0,
    source TEXT DEFAULT 'nsdl',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_fii_date ON fii_dii_daily(trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_fii_category ON fii_dii_daily(category, trade_date DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_fii_unique ON fii_dii_daily(trade_date, category, segment);

-- Monthly sector-wise flow (FII buying Banking +2100 Cr, selling IT -1500 Cr)
CREATE TABLE IF NOT EXISTS fii_sector_flow (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID REFERENCES tenants(id),
    snapshot_month TEXT NOT NULL,
    category TEXT NOT NULL,
    sector TEXT NOT NULL,
    net_flow_cr NUMERIC(15,2) DEFAULT 0,
    buy_value_cr NUMERIC(15,2) DEFAULT 0,
    sell_value_cr NUMERIC(15,2) DEFAULT 0,
    pct_of_total NUMERIC(8,4) DEFAULT 0,
    direction TEXT DEFAULT 'neutral',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_fii_sector ON fii_sector_flow(snapshot_month, category);

-- Monthly per-stock FII/DII holding changes
CREATE TABLE IF NOT EXISTS fii_stock_flow (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID REFERENCES tenants(id),
    snapshot_month TEXT NOT NULL,
    category TEXT NOT NULL,
    stock_symbol TEXT NOT NULL,
    company_name TEXT DEFAULT '',
    sector TEXT DEFAULT '',
    net_shares BIGINT DEFAULT 0,
    net_value_cr NUMERIC(15,2) DEFAULT 0,
    pct_holding_change NUMERIC(8,4) DEFAULT 0,
    total_holding_pct NUMERIC(8,4) DEFAULT 0,
    direction TEXT DEFAULT 'neutral',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_fii_stock ON fii_stock_flow(stock_symbol, snapshot_month);
CREATE INDEX IF NOT EXISTS idx_fii_stock_cat ON fii_stock_flow(category, snapshot_month);

-- ============================================================
-- Fund Manager Personas (rich behavioral profiles)
-- ============================================================

-- Expand fund_managers with persona data
-- (The mf_fund_managers table exists but is thin. Add a persona table.)
CREATE TABLE IF NOT EXISTS fund_manager_personas (
    id TEXT PRIMARY KEY,
    tenant_id UUID REFERENCES tenants(id),
    name TEXT NOT NULL,
    fund_house TEXT NOT NULL,

    -- Background
    education TEXT DEFAULT '',
    previous_firms JSONB DEFAULT '[]',       -- ["UTI MF", "DSP MF"]
    career_start_year INTEGER,
    total_experience_years NUMERIC(4,1) DEFAULT 0,
    certifications JSONB DEFAULT '[]',       -- ["CFA", "NISM V-A"]

    -- Current Role
    designation TEXT DEFAULT '',              -- "CIO", "Senior FM", "Head Equity"
    schemes_managed INTEGER DEFAULT 0,
    total_aum_cr NUMERIC(15,2) DEFAULT 0,
    tenure_current_years NUMERIC(4,1) DEFAULT 0,
    team_size INTEGER DEFAULT 0,

    -- Investment Style (DNA)
    investment_style TEXT DEFAULT 'blend',    -- growth, value, blend, garp, momentum
    approach TEXT DEFAULT 'bottom_up',        -- bottom_up, top_down, quant, thematic
    concentration TEXT DEFAULT 'moderate',    -- concentrated (15-20), moderate (25-35), diversified (40+)
    typical_stocks INTEGER DEFAULT 30,
    churn_rate_annual NUMERIC(5,2) DEFAULT 0, -- % portfolio turnover per year
    avg_holding_period_months INTEGER DEFAULT 24,
    philosophy TEXT DEFAULT '',               -- free text investment philosophy
    key_principles JSONB DEFAULT '[]',       -- ["Buy businesses not stocks", "Margin of safety"]

    -- Behavioral Patterns
    crash_behavior TEXT DEFAULT '',           -- "adds on dips", "holds through", "reduces risk"
    rally_behavior TEXT DEFAULT '',           -- "trims winners", "lets winners run", "raises cash"
    sector_biases JSONB DEFAULT '{}',        -- {"banking": "overweight", "it": "neutral"}
    typical_cash_pct NUMERIC(5,2) DEFAULT 5,
    contrarian_score NUMERIC(3,2) DEFAULT 0.5, -- 0=consensus, 1=strong contrarian

    -- Track Record
    best_year_return NUMERIC(8,2),
    best_year INTEGER,
    worst_year_return NUMERIC(8,2),
    worst_year INTEGER,
    years_beat_benchmark INTEGER DEFAULT 0,
    total_years_managed INTEGER DEFAULT 0,
    batting_average NUMERIC(5,2) DEFAULT 0,  -- % years beat benchmark
    alpha_3y NUMERIC(8,2) DEFAULT 0,
    alpha_5y NUMERIC(8,2) DEFAULT 0,
    alpha_10y NUMERIC(8,2) DEFAULT 0,
    max_drawdown_pct NUMERIC(8,2) DEFAULT 0,
    sharpe_ratio_3y NUMERIC(6,3) DEFAULT 0,
    information_ratio NUMERIC(6,3) DEFAULT 0,
    yearly_returns JSONB DEFAULT '{}',       -- {"2020": -5.2, "2021": 28.1, ...}

    -- Conviction Stocks
    conviction_stocks JSONB DEFAULT '[]',    -- [{stock, weight_pct, held_since, reason}]
    long_term_holds JSONB DEFAULT '[]',      -- stocks held >3 years
    recent_additions JSONB DEFAULT '[]',     -- stocks added in last 6 months
    recent_exits JSONB DEFAULT '[]',         -- stocks removed in last 6 months

    -- Predictive Signals
    buy_hit_rate NUMERIC(5,2) DEFAULT 0,     -- when they buy, what % goes up 20% in 12m
    sell_accuracy NUMERIC(5,2) DEFAULT 0,    -- when they sell, what % underperforms
    alpha_after_buy_12m NUMERIC(8,2) DEFAULT 0,

    -- Metadata
    photo_url TEXT DEFAULT '',
    linkedin_url TEXT DEFAULT '',
    bio TEXT DEFAULT '',
    notable_quotes JSONB DEFAULT '[]',       -- famous quotes/statements
    media_appearances JSONB DEFAULT '[]',

    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_fmp_name ON fund_manager_personas(name);
CREATE INDEX IF NOT EXISTS idx_fmp_house ON fund_manager_personas(fund_house);

-- ============================================================
-- SEBI Fund Categories (comprehensive)
-- ============================================================

CREATE TABLE IF NOT EXISTS mf_categories (
    id TEXT PRIMARY KEY,
    category_group TEXT NOT NULL,             -- 'equity', 'debt', 'hybrid', 'solution', 'other'
    category_name TEXT NOT NULL,              -- 'Large Cap', 'Flexi Cap', etc.
    sebi_definition TEXT DEFAULT '',          -- SEBI's official definition
    min_equity_pct NUMERIC(5,2),
    max_equity_pct NUMERIC(5,2),
    min_debt_pct NUMERIC(5,2),
    max_debt_pct NUMERIC(5,2),
    market_cap_rule TEXT DEFAULT '',          -- "min 80% in top 100 by mcap"
    max_stocks INTEGER,                      -- for Focused fund: max 30
    lock_in_years NUMERIC(3,1),              -- for ELSS: 3 years
    benchmark_typical TEXT DEFAULT '',
    risk_level TEXT DEFAULT 'moderate',       -- low, moderate, moderately_high, high, very_high
    suitable_for TEXT DEFAULT '',             -- "long term wealth creation, 5+ years"
    total_schemes_count INTEGER DEFAULT 0,
    total_aum_cr NUMERIC(15,2) DEFAULT 0,
    avg_expense_ratio NUMERIC(5,3) DEFAULT 0,
    avg_return_1y NUMERIC(8,2) DEFAULT 0,
    avg_return_3y NUMERIC(8,2) DEFAULT 0
);

-- Row-Level Security
ALTER TABLE graph_nodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE graph_edges ENABLE ROW LEVEL SECURITY;

-- Policy: users can only see rows for their tenant
-- (applied when using SET ROLE or session variables)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'tenant_isolation_nodes') THEN
        CREATE POLICY tenant_isolation_nodes ON graph_nodes
            FOR ALL USING (tenant_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'tenant_isolation_edges') THEN
        CREATE POLICY tenant_isolation_edges ON graph_edges
            FOR ALL USING (tenant_id = current_setting('app.tenant_id', true)::uuid);
    END IF;
EXCEPTION WHEN OTHERS THEN
    -- RLS policies may fail on SQLite or if tenant_id not set; skip gracefully
    NULL;
END $$;
