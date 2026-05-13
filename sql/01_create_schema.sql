-- ============================================================
-- DIMENSION TABLES (run first — facts depend on these)
-- ============================================================

CREATE TABLE dim_date (
    date_id            INTEGER PRIMARY KEY,
    full_date          DATE NOT NULL UNIQUE,
    year               SMALLINT NOT NULL,
    quarter            SMALLINT NOT NULL,
    month              SMALLINT NOT NULL,
    month_name         VARCHAR(15),
    week_of_year       SMALLINT,
    day_of_week        SMALLINT,
    is_weekday         BOOLEAN,
    is_fiscal_year_end BOOLEAN DEFAULT FALSE
);

CREATE TABLE dim_customers (
    customer_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    first_name       VARCHAR(100) NOT NULL,
    last_name        VARCHAR(100) NOT NULL,
    date_of_birth    DATE NOT NULL,
    gender           VARCHAR(20),
    education_level  VARCHAR(50),
    marital_status   VARCHAR(30),
    occupation       VARCHAR(100),
    income_bracket   VARCHAR(30),
    city             VARCHAR(100),
    state            VARCHAR(100),
    customer_since   DATE NOT NULL,
    is_active        BOOLEAN DEFAULT TRUE,
    CONSTRAINT chk_gender CHECK (gender IN ('Male', 'Female', 'Other'))
);

CREATE TABLE dim_agents (
    agent_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_name  VARCHAR(150) NOT NULL,
    agency_name VARCHAR(150),
    region      VARCHAR(100),
    channel     VARCHAR(50),
    hire_date   DATE,
    is_active   BOOLEAN DEFAULT TRUE
);

CREATE TABLE dim_products (
    product_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_name      VARCHAR(150) NOT NULL,
    product_line      VARCHAR(50) NOT NULL,
    coverage_type     VARCHAR(100),
    vehicle_category  VARCHAR(50),
    base_premium_rate DECIMAL(8,4),
    min_sum_insured   DECIMAL(15,2),
    max_sum_insured   DECIMAL(15,2),
    is_active         BOOLEAN DEFAULT TRUE
);

SELECT table_name 
FROM information_schema.tables 
WHERE table_schema = 'public' 
AND table_name LIKE 'dim_%'
ORDER BY table_name;


-- ============================================================
-- FACT TABLES (dims must exist before running this)
-- ============================================================

CREATE TABLE fact_policies (
    policy_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id          UUID NOT NULL REFERENCES dim_customers(customer_id),
    product_id           UUID NOT NULL REFERENCES dim_products(product_id),
    agent_id             UUID REFERENCES dim_agents(agent_id),
    policy_start_date_id INTEGER NOT NULL REFERENCES dim_date(date_id),
    policy_end_date_id   INTEGER NOT NULL REFERENCES dim_date(date_id),
    policy_number        VARCHAR(50) UNIQUE NOT NULL,
    policy_status        VARCHAR(30) NOT NULL,
    sum_insured          DECIMAL(15,2) NOT NULL,
    annual_premium       DECIMAL(10,2) NOT NULL,
    risk_score           DECIMAL(5,2),
    policy_tenure_months SMALLINT,
    is_renewed           BOOLEAN DEFAULT FALSE,
    is_lapsed            BOOLEAN DEFAULT FALSE,
    lapse_reason_code    SMALLINT,
    created_at           TIMESTAMP DEFAULT NOW(),
    updated_at           TIMESTAMP DEFAULT NOW(),
    CONSTRAINT chk_policy_status CHECK (
        policy_status IN ('Active', 'Lapsed', 'Expired', 'Cancelled', 'Renewed')
    ),
    CONSTRAINT chk_dates CHECK (policy_end_date_id > policy_start_date_id)
);

CREATE TABLE fact_premiums (
    premium_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    policy_id        UUID NOT NULL REFERENCES fact_policies(policy_id),
    due_date_id      INTEGER NOT NULL REFERENCES dim_date(date_id),
    paid_date_id     INTEGER REFERENCES dim_date(date_id),
    installment_type VARCHAR(20) NOT NULL,
    amount_due       DECIMAL(10,2) NOT NULL,
    amount_paid      DECIMAL(10,2) DEFAULT 0,
    days_to_payment  SMALLINT,
    payment_method   VARCHAR(50),
    payment_status   VARCHAR(20) NOT NULL,
    is_overdue       BOOLEAN DEFAULT FALSE,
    CONSTRAINT chk_payment_status CHECK (
        payment_status IN ('Paid', 'Overdue', 'Waived', 'Partial')
    )
);

CREATE TABLE fact_claims (
    claim_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    policy_id          UUID NOT NULL REFERENCES fact_policies(policy_id),
    incident_date_id   INTEGER NOT NULL REFERENCES dim_date(date_id),
    filed_date_id      INTEGER NOT NULL REFERENCES dim_date(date_id),
    settled_date_id    INTEGER REFERENCES dim_date(date_id),
    claim_type         VARCHAR(50),
    claim_status       VARCHAR(30) NOT NULL,
    claim_amount       DECIMAL(15,2) NOT NULL,
    approved_amount    DECIMAL(15,2) DEFAULT 0,
    rejected_amount    DECIMAL(15,2) DEFAULT 0,
    days_to_settlement SMALLINT,
    rejection_reason   VARCHAR(200),
    CONSTRAINT chk_claim_status CHECK (
        claim_status IN ('Filed', 'Under Review', 'Approved', 'Rejected', 'Settled')
    )
);

CREATE TABLE fact_renewals (
    renewal_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    policy_id              UUID NOT NULL REFERENCES fact_policies(policy_id),
    new_policy_id          UUID REFERENCES fact_policies(policy_id),
    renewal_date_id        INTEGER NOT NULL REFERENCES dim_date(date_id),
    original_start_date_id INTEGER NOT NULL REFERENCES dim_date(date_id),
    renewal_number         SMALLINT NOT NULL DEFAULT 1,
    previous_premium       DECIMAL(10,2) NOT NULL,
    new_premium            DECIMAL(10,2),
    premium_change_pct     DECIMAL(6,2),
    renewed                BOOLEAN NOT NULL DEFAULT FALSE,
    non_renewal_reason     VARCHAR(100),
    clv_at_renewal         DECIMAL(12,2)
);


SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
AND table_name LIKE 'fact_%'
ORDER BY table_name;


SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
ORDER BY table_name;


-- ============================================================
-- INDEXES
-- ============================================================

CREATE INDEX idx_policies_customer   ON fact_policies(customer_id);
CREATE INDEX idx_policies_status     ON fact_policies(policy_status);
CREATE INDEX idx_policies_start_date ON fact_policies(policy_start_date_id);
CREATE INDEX idx_claims_policy       ON fact_claims(policy_id);
CREATE INDEX idx_claims_status       ON fact_claims(claim_status);
CREATE INDEX idx_premiums_policy     ON fact_premiums(policy_id);
CREATE INDEX idx_premiums_overdue    ON fact_premiums(is_overdue);
CREATE INDEX idx_renewals_policy     ON fact_renewals(policy_id);
CREATE INDEX idx_renewals_renewed    ON fact_renewals(renewed);

SELECT indexname, tablename
FROM pg_indexes
WHERE schemaname = 'public'
ORDER BY tablename;


SELECT
    relname    AS table_name,
    n_live_tup AS row_count
FROM pg_stat_user_tables
WHERE schemaname = 'public'
ORDER BY relname;


