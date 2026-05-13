-- =============================================================
-- sql/03_powerbi_views.sql  (v2 — fixed fan-out bugs)
-- Insurance Policy Lifecycle Analysis
-- =============================================================
-- FAN-OUT BUG EXPLAINED:
--   Joining fact_premiums AND fact_claims both to fact_policies
--   in the same query causes row multiplication:
--   1 policy × 12 premium rows × 3 claim rows = 36 rows per policy
--   SUM() then counts each amount 3× or 12× respectively.
--   FIX: pre-aggregate each fact table to one row per policy
--   in a CTE before joining.
-- =============================================================


-- ── View 1: Monthly lapse trend ───────────────────────────────
-- No fan-out risk (only joins fact_policies to dim_date)
CREATE OR REPLACE VIEW vw_lapse_trend AS
SELECT
    dd.year,
    dd.month,
    dd.month_name,
    TO_DATE(
        dd.year::text || '-' || LPAD(dd.month::text, 2, '0') || '-01',
        'YYYY-MM-DD'
    )                                                          AS month_date,
    COUNT(*)                                                   AS total_policies,
    SUM(CASE WHEN fp.is_lapsed THEN 1 ELSE 0 END)             AS lapsed_count,
    ROUND(
        SUM(CASE WHEN fp.is_lapsed THEN 1 ELSE 0 END)::numeric
        / NULLIF(COUNT(*), 0) * 100, 2
    )                                                          AS lapse_rate_pct
FROM fact_policies fp
JOIN dim_date dd ON dd.date_id = fp.policy_start_date_id
GROUP BY dd.year, dd.month, dd.month_name
ORDER BY dd.year, dd.month;


-- ── View 2: Loss ratio by state ───────────────────────────────
-- FIXED: pre-aggregate premiums and claims separately per policy
-- Original had direct JOIN of both fact tables → inflated totals
DROP VIEW IF EXISTS vw_loss_ratio_state;

CREATE VIEW vw_loss_ratio_state AS
WITH premiums_per_policy AS (
    SELECT
        policy_id,
        SUM(amount_paid) AS total_premium
    FROM fact_premiums
    WHERE payment_status = 'Paid'
    GROUP BY policy_id
),
claims_per_policy AS (
    SELECT
        policy_id,
        SUM(approved_amount) AS total_claims
    FROM fact_claims
    WHERE claim_status IN ('Settled', 'Approved')
    GROUP BY policy_id
)
SELECT
    dc.state,
    COUNT(DISTINCT fp.policy_id)                               AS policy_count,
    ROUND(SUM(pp.total_premium)::numeric, 2)                   AS total_premium,
    ROUND(COALESCE(SUM(cp.total_claims), 0)::numeric, 2)       AS total_claims,
    ROUND(
        COALESCE(SUM(cp.total_claims), 0)::numeric
        / NULLIF(SUM(pp.total_premium), 0) * 100, 2
    )                                                          AS loss_ratio_pct
FROM fact_policies        fp
JOIN dim_customers        dc ON dc.customer_id = fp.customer_id
LEFT JOIN premiums_per_policy pp ON pp.policy_id = fp.policy_id
LEFT JOIN claims_per_policy   cp ON cp.policy_id = fp.policy_id
GROUP BY dc.state
ORDER BY loss_ratio_pct DESC;


-- ── View 3: Customer lifetime value ──────────────────────────
-- FIXED: pre-aggregate premiums and claims per customer separately
-- Original joined all three fact tables together → severe fan-out
DROP VIEW IF EXISTS vw_clv;

CREATE VIEW vw_clv AS
WITH premiums_per_customer AS (
    SELECT
        fp.customer_id,
        SUM(fpr.amount_paid) AS total_premium_paid
    FROM fact_policies  fp
    JOIN fact_premiums  fpr ON fpr.policy_id = fp.policy_id
                            AND fpr.payment_status = 'Paid'
    GROUP BY fp.customer_id
),
claims_per_customer AS (
    SELECT
        fp.customer_id,
        SUM(fc.approved_amount) AS total_claims_paid
    FROM fact_policies fp
    JOIN fact_claims   fc  ON fc.policy_id = fp.policy_id
                           AND fc.claim_status IN ('Settled', 'Approved')
    GROUP BY fp.customer_id
),
renewals_per_customer AS (
    SELECT
        fp.customer_id,
        COUNT(DISTINCT fr.renewal_id)                              AS renewal_count,
        ROUND(AVG(CASE WHEN fr.renewed THEN 1.0 ELSE 0 END)*100, 2) AS renewal_rate_pct
    FROM fact_policies  fp
    JOIN fact_renewals  fr ON fr.policy_id = fp.policy_id
    GROUP BY fp.customer_id
),
policy_counts AS (
    SELECT
        customer_id,
        COUNT(DISTINCT policy_id) AS policy_count
    FROM fact_policies
    GROUP BY customer_id
)
SELECT
    dc.customer_id,
    dc.first_name || ' ' || dc.last_name                      AS full_name,
    dc.state,
    dc.income_bracket,
    dc.gender,
    COALESCE(pc.policy_count,   0)                             AS policy_count,
    COALESCE(ppc.total_premium_paid, 0)                        AS total_premium_paid,
    COALESCE(cpc.total_claims_paid,  0)                        AS total_claims_paid,
    ROUND(
        COALESCE(ppc.total_premium_paid, 0)
        - COALESCE(cpc.total_claims_paid, 0), 2
    )                                                          AS clv_estimate,
    COALESCE(rpc.renewal_count,        0)                      AS renewal_count,
    COALESCE(rpc.renewal_rate_pct,     0)                      AS renewal_rate_pct
FROM dim_customers            dc
LEFT JOIN policy_counts        pc  ON pc.customer_id  = dc.customer_id
LEFT JOIN premiums_per_customer ppc ON ppc.customer_id = dc.customer_id
LEFT JOIN claims_per_customer   cpc ON cpc.customer_id = dc.customer_id
LEFT JOIN renewals_per_customer rpc ON rpc.customer_id = dc.customer_id;


-- ── View 4: Renewal behavior by product ──────────────────────
-- Clean — only joins fact_renewals to fact_policies to dim_products
DROP VIEW IF EXISTS vw_renewal_behavior;

CREATE VIEW vw_renewal_behavior AS
SELECT
    dp.product_name,
    dp.product_line,
    dp.coverage_type,
    COUNT(fr.renewal_id)                                       AS total_renewals_due,
    SUM(CASE WHEN fr.renewed THEN 1 ELSE 0 END)                AS renewed_count,
    ROUND(
        SUM(CASE WHEN fr.renewed THEN 1 ELSE 0 END)::numeric
        / NULLIF(COUNT(fr.renewal_id), 0) * 100, 2
    )                                                          AS renewal_rate_pct,
    ROUND(AVG(fr.previous_premium), 2)                         AS avg_previous_premium,
    ROUND(AVG(fr.new_premium), 2)                              AS avg_new_premium,
    ROUND(AVG(fr.premium_change_pct), 2)                       AS avg_premium_change_pct
FROM fact_renewals fr
JOIN fact_policies fp ON fp.policy_id  = fr.policy_id
JOIN dim_products  dp ON dp.product_id = fp.product_id
GROUP BY dp.product_name, dp.product_line, dp.coverage_type;


-- ── View 5: Pricing adequacy by product line + year ──────────
-- FIXED: pre-aggregate claims per policy before joining
-- Original joined fact_claims directly → inflated claims vs annual_premium
DROP VIEW IF EXISTS vw_pricing_adequacy;

CREATE VIEW vw_pricing_adequacy AS
WITH claims_per_policy AS (
    SELECT
        policy_id,
        SUM(approved_amount) AS total_claims
    FROM fact_claims
    WHERE claim_status IN ('Settled', 'Approved')
    GROUP BY policy_id
)
SELECT
    dp.product_name,
    dp.product_line,
    dp.coverage_type,
    dd.year,
    COUNT(DISTINCT fp.policy_id)                               AS policy_count,
    ROUND(SUM(fp.annual_premium)::numeric, 2)                  AS premium_collected,
    ROUND(COALESCE(SUM(cp.total_claims), 0)::numeric, 2)       AS claims_paid,
    ROUND(AVG(fp.risk_score), 2)                               AS avg_risk_score,
    ROUND(
        COALESCE(SUM(cp.total_claims), 0)::numeric
        / NULLIF(SUM(fp.annual_premium), 0) * 100, 2
    )                                                          AS loss_ratio_pct,
    CASE
        WHEN COALESCE(SUM(cp.total_claims), 0)
             / NULLIF(SUM(fp.annual_premium), 0) < 0.6  THEN 'Adequate'
        WHEN COALESCE(SUM(cp.total_claims), 0)
             / NULLIF(SUM(fp.annual_premium), 0) < 0.9  THEN 'Watch'
        ELSE                                                 'Inadequate'
    END                                                        AS pricing_status
FROM fact_policies          fp
JOIN dim_products            dp ON dp.product_id = fp.product_id
JOIN dim_date                dd ON dd.date_id    = fp.policy_start_date_id
LEFT JOIN claims_per_policy  cp ON cp.policy_id  = fp.policy_id
GROUP BY dp.product_name, dp.product_line, dp.coverage_type, dd.year
ORDER BY dd.year, dp.product_name;


-- ── View 6: Agent performance ─────────────────────────────────
-- FIXED: pre-aggregate premiums per policy to avoid fan-out
-- from joining both fact_renewals and fact_premiums to fact_policies
DROP VIEW IF EXISTS vw_agent_performance;

CREATE VIEW vw_agent_performance AS
WITH premiums_per_policy AS (
    SELECT
        policy_id,
        SUM(amount_paid) AS total_premium
    FROM fact_premiums
    WHERE payment_status = 'Paid'
    GROUP BY policy_id
),
policy_summary AS (
    SELECT
        fp.agent_id,
        COUNT(DISTINCT fp.policy_id)                               AS policies_sold,
        SUM(COALESCE(pp.total_premium, 0))                         AS total_premium_written,
        ROUND(AVG(fp.risk_score), 2)                               AS avg_risk_score,
        COUNT(CASE WHEN fp.is_lapsed THEN 1 END)                   AS lapsed_policies,
        ROUND(
            COUNT(CASE WHEN fp.is_lapsed THEN 1 END) * 100.0
            / NULLIF(COUNT(DISTINCT fp.policy_id), 0), 2
        )                                                          AS lapse_rate_pct
    FROM fact_policies        fp
    LEFT JOIN premiums_per_policy pp ON pp.policy_id = fp.policy_id
    GROUP BY fp.agent_id
),
renewal_summary AS (
    SELECT
        fp.agent_id,
        ROUND(
            COUNT(CASE WHEN fr.renewed THEN 1 END) * 100.0
            / NULLIF(COUNT(fr.renewal_id), 0), 2
        )                                                          AS renewal_rate_pct
    FROM fact_policies  fp
    JOIN fact_renewals  fr ON fr.policy_id = fp.policy_id
    GROUP BY fp.agent_id
)
SELECT
    a.agent_id,
    a.agent_name,
    a.channel,
    a.region,
    COALESCE(ps.policies_sold,         0)                      AS policies_sold,
    COALESCE(ps.total_premium_written, 0)                      AS total_premium_written,
    COALESCE(ps.avg_risk_score,        0)                      AS avg_risk_score,
    COALESCE(ps.lapsed_policies,       0)                      AS lapsed_policies,
    COALESCE(ps.lapse_rate_pct,        0)                      AS lapse_rate_pct,
    COALESCE(rs.renewal_rate_pct,      0)                      AS renewal_rate_pct
FROM dim_agents              a
LEFT JOIN policy_summary     ps ON ps.agent_id = a.agent_id
LEFT JOIN renewal_summary    rs ON rs.agent_id = a.agent_id
ORDER BY total_premium_written DESC;


-- ── View 7: Customer segment profile ─────────────────────────
-- Clean — only joins fact_policies to dim_customers (no multi-fact join)
CREATE OR REPLACE VIEW vw_customer_segments AS
SELECT
    dc.income_bracket,
    dc.gender,
    dc.marital_status,
    dc.education_level,
    dc.state,
    COUNT(DISTINCT dc.customer_id)                             AS customer_count,
    COUNT(DISTINCT fp.policy_id)                               AS policy_count,
    ROUND(AVG(fp.annual_premium), 2)                           AS avg_annual_premium,
    ROUND(AVG(fp.risk_score), 2)                               AS avg_risk_score,
    SUM(CASE WHEN fp.is_lapsed THEN 1 ELSE 0 END)              AS lapsed_count,
    ROUND(
        SUM(CASE WHEN fp.is_lapsed THEN 1 ELSE 0 END)::numeric
        / NULLIF(COUNT(DISTINCT fp.policy_id), 0) * 100, 2
    )                                                          AS lapse_rate_pct
FROM dim_customers dc
JOIN fact_policies fp ON fp.customer_id = dc.customer_id
GROUP BY dc.income_bracket, dc.gender, dc.marital_status,
         dc.education_level, dc.state;


-- =============================================================
-- SANITY CHECKS — run after creating views
-- =============================================================

-- Check 1: Loss ratio should be 70–110% at portfolio level
SELECT
    ROUND(SUM(total_claims)::numeric / NULLIF(SUM(total_premium), 0) * 100, 1)
    AS portfolio_loss_ratio_pct
FROM vw_loss_ratio_state;

-- Check 2: Lapse rate should be ~18–22%
SELECT
    ROUND(SUM(lapsed_count)::numeric / NULLIF(SUM(total_policies), 0) * 100, 1)
    AS overall_lapse_rate_pct
FROM vw_lapse_trend;

-- Check 3: Renewal rate should be ~55–65% (of eligible policies)
SELECT
    ROUND(SUM(renewed_count)::numeric / NULLIF(SUM(total_renewals_due), 0) * 100, 1)
    AS overall_renewal_rate_pct
FROM vw_renewal_behavior;

-- Check 4: CLV should show a mix of positive and negative
SELECT
    COUNT(CASE WHEN clv_estimate > 0 THEN 1 END)  AS positive_clv_customers,
    COUNT(CASE WHEN clv_estimate <= 0 THEN 1 END) AS negative_clv_customers,
    ROUND(AVG(clv_estimate)::numeric, 0)           AS avg_clv
FROM vw_clv;

-- Check 5: Pricing adequacy distribution
SELECT pricing_status, COUNT(*) AS product_year_combinations
FROM vw_pricing_adequacy
GROUP BY pricing_status;

-- Check 6: Top 5 agents by premium written
SELECT agent_name, channel, policies_sold,
       total_premium_written, lapse_rate_pct, renewal_rate_pct
FROM vw_agent_performance
LIMIT 5;