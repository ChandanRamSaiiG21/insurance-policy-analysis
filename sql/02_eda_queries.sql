-- =============================================================================
-- Insurance Policy Lifecycle Analysis
-- File   : sql/02_eda_queries.sql  (v2 — data refresh after v3 generation)
-- =============================================================================


-- ---------------------------------------------------------------------------
-- Q1  LAPSE RATE by product line and month
-- ---------------------------------------------------------------------------

WITH monthly_lapse AS (
    SELECT
        dp.product_line,
        dd.year,
        dd.month,
        COUNT(*)                                    AS total_policies,
        COUNT(*) FILTER (WHERE fp.is_lapsed = TRUE) AS lapsed_policies
    FROM fact_policies fp
    JOIN dim_products dp ON fp.product_id           = dp.product_id
    JOIN dim_date     dd ON fp.policy_start_date_id = dd.date_id
    GROUP BY dp.product_line, dd.year, dd.month
)
SELECT
    product_line,
    year,
    month,
    total_policies,
    lapsed_policies,
    ROUND(
        100.0 * lapsed_policies / NULLIF(total_policies, 0), 2
    ) AS lapse_rate_pct
FROM monthly_lapse
ORDER BY product_line, year, month;


-- ---------------------------------------------------------------------------
-- Q2  LOSS RATIO by state and policy status
--     Correct pattern: aggregate each side separately, then join
-- ---------------------------------------------------------------------------

WITH premiums_agg AS (
    SELECT
        fpr.policy_id,
        SUM(fpr.amount_paid) AS total_premium
    FROM fact_premiums fpr
    WHERE fpr.payment_status = 'Paid'
    GROUP BY fpr.policy_id
),
claims_agg AS (
    SELECT
        fc.policy_id,
        SUM(fc.approved_amount) AS total_approved_claims
    FROM fact_claims fc
    WHERE fc.claim_status IN ('Settled', 'Approved')
    GROUP BY fc.policy_id
)
SELECT
    dc.state,
    da.channel                                                         AS sales_channel,
    COUNT(DISTINCT fp.policy_id)                                       AS policy_count,
    ROUND(SUM(pa.total_premium)::NUMERIC, 2)                           AS total_premium_collected,
    ROUND(COALESCE(SUM(ca.total_approved_claims), 0)::NUMERIC, 2)      AS total_claims_paid,
    ROUND(
        100.0 * COALESCE(SUM(ca.total_approved_claims), 0)
              / NULLIF(SUM(pa.total_premium), 0), 2
    )                                                                   AS loss_ratio_pct
FROM fact_policies fp
JOIN dim_customers dc  ON fp.customer_id = dc.customer_id
JOIN dim_agents    da  ON fp.agent_id    = da.agent_id
JOIN premiums_agg  pa  ON fp.policy_id   = pa.policy_id
LEFT JOIN claims_agg ca ON fp.policy_id  = ca.policy_id
GROUP BY dc.state, da.channel
ORDER BY dc.state, loss_ratio_pct DESC;


-- ---------------------------------------------------------------------------
-- Q3  RENEWAL BEHAVIOR by customer tenure
-- ---------------------------------------------------------------------------

WITH tenure_map AS (
    SELECT
        fp.policy_id,
        fp.customer_id,
        EXTRACT(YEAR FROM AGE(CURRENT_DATE, dc.customer_since))::INT AS tenure_years
    FROM fact_policies fp
    JOIN dim_customers dc ON fp.customer_id = dc.customer_id
),
banded AS (
    SELECT
        policy_id,
        customer_id,
        tenure_years,
        CASE
            WHEN tenure_years BETWEEN 0 AND 1  THEN '0–1 yr'
            WHEN tenure_years BETWEEN 2 AND 3  THEN '2–3 yrs'
            WHEN tenure_years BETWEEN 4 AND 6  THEN '4–6 yrs'
            WHEN tenure_years BETWEEN 7 AND 10 THEN '7–10 yrs'
            ELSE '10+ yrs'
        END AS tenure_band,
        CASE
            WHEN tenure_years BETWEEN 0 AND 1  THEN 1
            WHEN tenure_years BETWEEN 2 AND 3  THEN 2
            WHEN tenure_years BETWEEN 4 AND 6  THEN 3
            WHEN tenure_years BETWEEN 7 AND 10 THEN 4
            ELSE 5
        END AS band_order
    FROM tenure_map
)
SELECT
    b.tenure_band,
    b.band_order,
    COUNT(DISTINCT fr.policy_id)                             AS policies_up_for_renewal,
    COUNT(DISTINCT fr.renewal_id) FILTER (WHERE fr.renewed) AS renewed_count,
    ROUND(
        100.0 * COUNT(DISTINCT fr.renewal_id) FILTER (WHERE fr.renewed)
              / NULLIF(COUNT(DISTINCT fr.policy_id), 0), 2
    )                                                        AS renewal_rate_pct
FROM banded b
JOIN fact_renewals fr ON b.policy_id = fr.policy_id
GROUP BY b.tenure_band, b.band_order
ORDER BY b.band_order;


-- ---------------------------------------------------------------------------
-- Q4  CLV SEGMENTS  (FIXED — pre-aggregate premiums and claims separately
--     before joining to avoid fan-out / row multiplication)
-- ---------------------------------------------------------------------------

WITH premiums_per_customer AS (
    -- One row per customer: total premiums paid across all their policies
    SELECT
        fp.customer_id,
        SUM(fpr.amount_paid) AS total_premiums_paid
    FROM fact_policies  fp
    JOIN fact_premiums  fpr ON fp.policy_id = fpr.policy_id
                            AND fpr.payment_status = 'Paid'
    GROUP BY fp.customer_id
),
claims_per_customer AS (
    -- One row per customer: total approved claims across all their policies
    SELECT
        fp.customer_id,
        SUM(fc.approved_amount) AS total_claims_paid
    FROM fact_policies fp
    JOIN fact_claims   fc  ON fp.policy_id = fc.policy_id
                           AND fc.claim_status IN ('Settled', 'Approved')
    GROUP BY fp.customer_id
),
customer_clv AS (
    SELECT
        dc.customer_id,
        dc.first_name || ' ' || dc.last_name             AS full_name,
        dc.state,
        dc.income_bracket,
        COALESCE(ppc.total_premiums_paid, 0)              AS total_premiums_paid,
        COALESCE(cpc.total_claims_paid,   0)              AS total_claims_paid,
        COALESCE(ppc.total_premiums_paid, 0)
            - COALESCE(cpc.total_claims_paid, 0)          AS clv_proxy
    FROM dim_customers        dc
    LEFT JOIN premiums_per_customer ppc ON dc.customer_id = ppc.customer_id
    LEFT JOIN claims_per_customer   cpc ON dc.customer_id = cpc.customer_id
),
ranked AS (
    SELECT *,
        NTILE(5) OVER (ORDER BY clv_proxy DESC) AS quintile
    FROM customer_clv
)
SELECT
    CASE quintile
        WHEN 1 THEN 'Platinum'
        WHEN 2 THEN 'Gold'
        WHEN 3 THEN 'Silver'
        WHEN 4 THEN 'Bronze'
        ELSE        'Standard'
    END                AS clv_segment,
    COUNT(*)           AS customer_count,
    ROUND(AVG(clv_proxy)::NUMERIC, 2) AS avg_clv,
    ROUND(MIN(clv_proxy)::NUMERIC, 2) AS min_clv,
    ROUND(MAX(clv_proxy)::NUMERIC, 2) AS max_clv,
    ROUND(SUM(clv_proxy)::NUMERIC, 2) AS total_clv
FROM ranked
GROUP BY quintile
ORDER BY quintile;


-- ---------------------------------------------------------------------------
-- Q5  PRICING ADEQUACY by product
-- ---------------------------------------------------------------------------

WITH claims_per_policy AS (
    SELECT
        fc.policy_id,
        SUM(fc.approved_amount) AS total_approved
    FROM fact_claims fc
    WHERE fc.claim_status IN ('Settled', 'Approved')
    GROUP BY fc.policy_id
)
SELECT
    dp.product_id,
    dp.product_name,
    dp.product_line,
    COUNT(DISTINCT fp.policy_id)                          AS policy_count,
    ROUND(AVG(fp.annual_premium)::NUMERIC, 2)             AS avg_annual_premium,
    ROUND(AVG(cp.total_approved)::NUMERIC, 2)             AS avg_claim_per_policy,
    ROUND(
        AVG(fp.annual_premium)
      / NULLIF(AVG(cp.total_approved), 0), 3
    )::NUMERIC                                            AS pricing_adequacy_ratio,
    CASE
        WHEN AVG(fp.annual_premium) / NULLIF(AVG(cp.total_approved), 0) >= 1.2
            THEN 'Over-priced'
        WHEN AVG(fp.annual_premium) / NULLIF(AVG(cp.total_approved), 0)
             BETWEEN 0.9 AND 1.2
            THEN 'Adequately priced'
        ELSE 'Under-priced'
    END                                                   AS pricing_verdict
FROM fact_policies fp
JOIN dim_products      dp ON fp.product_id = dp.product_id
LEFT JOIN claims_per_policy cp ON fp.policy_id = cp.policy_id
GROUP BY dp.product_id, dp.product_name, dp.product_line
ORDER BY pricing_adequacy_ratio ASC;


-- ---------------------------------------------------------------------------
-- Q6  AGENT PERFORMANCE — premium collected, lapse rate, renewal rate
-- ---------------------------------------------------------------------------

WITH agent_premiums AS (
    SELECT
        fp.agent_id,
        SUM(fpr.amount_paid) AS total_premium_collected
    FROM fact_policies  fp
    JOIN fact_premiums  fpr ON fp.policy_id = fpr.policy_id
                            AND fpr.payment_status = 'Paid'
    GROUP BY fp.agent_id
),
agent_claims AS (
    SELECT
        fp.agent_id,
        SUM(fc.approved_amount) AS total_claims_paid
    FROM fact_policies fp
    JOIN fact_claims   fc ON fp.policy_id = fc.policy_id
                          AND fc.claim_status IN ('Settled', 'Approved')
    GROUP BY fp.agent_id
)
SELECT
    da.agent_id,
    da.agent_name,
    da.channel,
    da.region,
    COUNT(DISTINCT fp.policy_id)                                   AS total_policies,
    ROUND(AVG(fp.annual_premium)::NUMERIC, 2)                      AS avg_premium,
    ROUND(COALESCE(ap.total_premium_collected, 0)::NUMERIC, 2)     AS total_premium_collected,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE fp.is_lapsed)
              / NULLIF(COUNT(*), 0), 2
    )                                                              AS lapse_rate_pct,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE fp.is_renewed)
              / NULLIF(COUNT(*), 0), 2
    )                                                              AS renewal_rate_pct,
    ROUND(
        100.0 * COALESCE(ac.total_claims_paid, 0)
              / NULLIF(ap.total_premium_collected, 0), 2
    )                                                              AS loss_ratio_pct
FROM fact_policies   fp
JOIN dim_agents      da ON fp.agent_id  = da.agent_id
LEFT JOIN agent_premiums ap ON da.agent_id = ap.agent_id
LEFT JOIN agent_claims   ac ON da.agent_id = ac.agent_id
GROUP BY da.agent_id, da.agent_name, da.channel, da.region,
         ap.total_premium_collected, ac.total_claims_paid
ORDER BY total_premium_collected DESC;


-- ---------------------------------------------------------------------------
-- Q7  RISK SCORE DISTRIBUTION — lapse rate and avg CLV by risk band
-- ---------------------------------------------------------------------------

SELECT
    CASE
        WHEN fp.risk_score < 30              THEN '10–29  Low Risk'
        WHEN fp.risk_score BETWEEN 30 AND 49 THEN '30–49  Moderate Risk'
        WHEN fp.risk_score BETWEEN 50 AND 69 THEN '50–69  High Risk'
        ELSE                                      '70–90  Very High Risk'
    END                                                        AS risk_band,
    COUNT(*)                                                   AS policy_count,
    ROUND(AVG(fp.risk_score)::NUMERIC, 1)                      AS avg_risk_score,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE fp.is_lapsed)
              / NULLIF(COUNT(*), 0), 2
    )                                                          AS lapse_rate_pct,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE fp.is_renewed)
              / NULLIF(COUNT(*), 0), 2
    )                                                          AS renewal_rate_pct,
    ROUND(AVG(fr.clv_at_renewal)::NUMERIC, 2)                  AS avg_clv
FROM fact_policies fp
LEFT JOIN fact_renewals fr ON fp.policy_id = fr.policy_id
GROUP BY risk_band
ORDER BY avg_risk_score;