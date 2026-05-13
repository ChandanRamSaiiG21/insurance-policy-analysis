"""
Insurance Policy Lifecycle Analysis
File   : notebooks/05_ml_analysis.py  (v2 — CLV leakage fixed)
Day    : 5 — ML Analysis
Author : Chandan Ram Saii

FIXES vs original notebook:
  1. CLV leakage removed: previous_premium, new_premium, premium_change_pct
     dropped from clv_features. These mathematically define CLV so R²=1.0
     was guaranteed regardless of data quality.
     New features: risk_score, tenure, income, product_line, age — things
     that PREDICT CLV without DEFINING it.
  2. Chart save paths updated to reports/v2_calibrated/
  3. class_weight='balanced' added to LogisticRegression for imbalanced target
  4. Silhouette score added to KMeans evaluation
  5. Age feature engineered from date_of_birth for CLV model
"""

import os
import warnings
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import seaborn as sns
from urllib.parse import quote_plus
from sqlalchemy import create_engine
from sklearn.preprocessing import LabelEncoder, StandardScaler
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.linear_model import LogisticRegression, LinearRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import (
    classification_report, confusion_matrix,
    roc_auc_score, roc_curve, r2_score, mean_absolute_error
)

warnings.filterwarnings("ignore")

# ── Plot style ────────────────────────────────────────────────
plt.rcParams["figure.figsize"]     = (12, 6)
plt.rcParams["font.family"]        = "sans-serif"
plt.rcParams["axes.spines.top"]    = False
plt.rcParams["axes.spines.right"]  = False
PURPLE = "#534AB7"
RED    = "#E24B4A"
GREEN  = "#1D9E75"
AMBER  = "#BA7517"

# ── Output path ───────────────────────────────────────────────
REPORTS = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "reports", "v2_calibrated"
)
os.makedirs(REPORTS, exist_ok=True)

# ─────────────────────────────────────────────────────────────
# 1. DATA LOADING
# ─────────────────────────────────────────────────────────────
from dotenv import load_dotenv
from pathlib import Path

env_path = Path("D:/DataAnalyticsProjects/insurance-policy-analysis/.env")
load_dotenv(dotenv_path=env_path, encoding="utf-8-sig")

db_password = os.getenv("DB_PASSWORD", "NOT_FOUND")
password    = quote_plus(db_password)
engine      = create_engine(
    f"postgresql://postgres:{password}@localhost:5432/insurance_policy_db"
)

print("Loading data...")
policies  = pd.read_sql("SELECT * FROM public.fact_policies",  engine)
customers = pd.read_sql("SELECT * FROM public.dim_customers",  engine)
products  = pd.read_sql("SELECT * FROM public.dim_products",   engine)
agents    = pd.read_sql("SELECT * FROM public.dim_agents",     engine)
renewals  = pd.read_sql("SELECT * FROM public.fact_renewals",  engine)
claims    = pd.read_sql("SELECT * FROM public.fact_claims",    engine)

print(f"  Policies:  {len(policies):,}")
print(f"  Customers: {len(customers):,}")
print(f"  Renewals:  {len(renewals):,}")
print(f"  Claims:    {len(claims):,}")

# ─────────────────────────────────────────────────────────────
# 2. EDA — KEY DISTRIBUTIONS
# ─────────────────────────────────────────────────────────────
print("\n[EDA] Generating distribution charts...")

lapse_rate   = policies["is_lapsed"].mean() * 100
avg_premium  = policies["annual_premium"].mean()
avg_risk     = policies["risk_score"].mean()
std_risk     = policies["risk_score"].std()

print(f"  Overall lapse rate:  {lapse_rate:.1f}%")
print(f"  Avg annual premium:  ₹{avg_premium:,.0f}")
print(f"  Avg risk score:      {avg_risk:.1f} ± {std_risk:.1f}")

# Distribution plots
fig, axes = plt.subplots(1, 3, figsize=(18, 5))

axes[0].hist(policies["annual_premium"], bins=50,
             color=PURPLE, alpha=0.8, edgecolor="white")
axes[0].set_title("Annual Premium Distribution", fontweight="bold")
axes[0].set_xlabel("Annual Premium (₹)")
axes[0].set_ylabel("Count")
axes[0].xaxis.set_major_formatter(
    mticker.FuncFormatter(lambda x, _: f"₹{x/1000:.0f}K")
)

axes[1].hist(policies["risk_score"], bins=50,
             color=AMBER, alpha=0.8, edgecolor="white")
axes[1].axvline(avg_risk, color=RED, linestyle="--", linewidth=2,
                label=f"Mean: {avg_risk:.1f}")
axes[1].set_title("Risk Score Distribution", fontweight="bold")
axes[1].set_xlabel("Risk Score")
axes[1].set_ylabel("Count")
axes[1].legend()

axes[2].hist(policies["policy_tenure_months"], bins=3,
             color=GREEN, alpha=0.8, edgecolor="white")
axes[2].set_title("Policy Tenure Distribution", fontweight="bold")
axes[2].set_xlabel("Tenure (months)")
axes[2].set_ylabel("Count")

plt.suptitle("Policy Feature Distributions", fontsize=14, fontweight="bold", y=1.02)
plt.tight_layout()
plt.savefig(os.path.join(REPORTS, "06_policy_distributions.png"),
            dpi=150, bbox_inches="tight")
plt.close()
print("  Saved: 06_policy_distributions.png")

# Lapse rate by income bracket
lapse_income = (
    policies.merge(customers[["customer_id", "income_bracket"]], on="customer_id")
    .groupby("income_bracket")["is_lapsed"]
    .mean()
    .mul(100)
    .reindex(["<2L", "2L-5L", "5L-10L", "10L-25L", "25L+"])
)

fig, ax = plt.subplots(figsize=(10, 5))
bars = ax.bar(lapse_income.index, lapse_income.values,
              color=[RED, AMBER, PURPLE, GREEN, "#0F6E56"], alpha=0.85)
ax.bar_label(bars, fmt="%.1f%%", padding=4)
ax.set_title("Lapse Rate by Income Bracket", fontsize=14, fontweight="bold")
ax.set_xlabel("Income Bracket")
ax.set_ylabel("Lapse Rate (%)")
ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.0f%%"))
plt.tight_layout()
plt.savefig(os.path.join(REPORTS, "07_lapse_by_income.png"),
            dpi=150, bbox_inches="tight")
plt.close()
print("  Saved: 07_lapse_by_income.png")

# CLV distribution
clv_data = renewals["clv_at_renewal"].dropna()

fig, axes = plt.subplots(1, 2, figsize=(14, 5))
axes[0].hist(clv_data, bins=60, color=PURPLE, alpha=0.8, edgecolor="white")
axes[0].axvline(0, color=RED, linestyle="--", linewidth=2, label="Break-even")
axes[0].axvline(clv_data.mean(), color=GREEN, linestyle="--", linewidth=2,
                label=f"Mean: ₹{clv_data.mean():,.0f}")
axes[0].set_title("CLV Distribution", fontweight="bold")
axes[0].set_xlabel("CLV at Renewal (₹)")
axes[0].set_ylabel("Count")
axes[0].legend()

segments = pd.cut(
    clv_data,
    bins=[-np.inf, 0, 10000, 30000, 60000, np.inf],
    labels=["Negative", "₹0–10K", "₹10K–30K", "₹30K–60K", "₹60K+"]
)
seg_counts = segments.value_counts()
colors_pie = [RED, "#FAC775", "#EF9F27", "#9FE1CB", GREEN]
axes[1].pie(seg_counts.values, labels=seg_counts.index, colors=colors_pie,
            autopct="%1.1f%%", startangle=90, pctdistance=0.85)
axes[1].set_title("CLV Segment Distribution", fontweight="bold")

plt.suptitle("Customer Lifetime Value Analysis", fontsize=14, fontweight="bold")
plt.tight_layout()
plt.savefig(os.path.join(REPORTS, "09_clv_analysis.png"),
            dpi=150, bbox_inches="tight")
plt.close()
print("  Saved: 09_clv_analysis.png")

# ─────────────────────────────────────────────────────────────
# 3. CUSTOMER SEGMENTATION — KMeans
# ─────────────────────────────────────────────────────────────
print("\n[KMeans] Customer segmentation...")

df_cluster = policies.merge(
    customers[["customer_id", "income_bracket", "education_level", "gender"]],
    on="customer_id", how="left"
)

le = LabelEncoder()
df_cluster["income_encoded"]    = le.fit_transform(df_cluster["income_bracket"].fillna("Unknown"))
df_cluster["education_encoded"] = le.fit_transform(df_cluster["education_level"].fillna("Unknown"))
df_cluster["gender_encoded"]    = le.fit_transform(df_cluster["gender"].fillna("Unknown"))

cluster_features = ["annual_premium", "risk_score", "policy_tenure_months",
                    "income_encoded", "education_encoded"]

X_cluster = df_cluster[cluster_features].dropna()
scaler_cl = StandardScaler()
X_scaled  = scaler_cl.fit_transform(X_cluster)
print(f"  Clustering dataset: {len(X_cluster):,} records")

# Elbow method
inertias = []
sil_scores = []
K_range = range(2, 10)

for k in K_range:
    km = KMeans(n_clusters=k, random_state=42, n_init=10)
    labels = km.fit_predict(X_scaled)
    inertias.append(km.inertia_)
    sil_scores.append(silhouette_score(X_scaled, labels, sample_size=10000))

fig, axes = plt.subplots(1, 2, figsize=(14, 5))
axes[0].plot(K_range, inertias, "o-", color=PURPLE, linewidth=2, markersize=8)
axes[0].fill_between(K_range, inertias, alpha=0.1, color=PURPLE)
axes[0].axvline(x=4, color=RED, linestyle="--", linewidth=1.5, label="Optimal K=4")
axes[0].set_title("Elbow Method", fontsize=13, fontweight="bold")
axes[0].set_xlabel("Number of Clusters (K)")
axes[0].set_ylabel("Inertia")
axes[0].legend()

axes[1].plot(K_range, sil_scores, "s-", color=GREEN, linewidth=2, markersize=8)
axes[1].axvline(x=4, color=RED, linestyle="--", linewidth=1.5, label="K=4")
axes[1].set_title("Silhouette Score by K", fontsize=13, fontweight="bold")
axes[1].set_xlabel("Number of Clusters (K)")
axes[1].set_ylabel("Silhouette Score")
axes[1].legend()

plt.suptitle("Optimal K Selection — KMeans", fontsize=14, fontweight="bold")
plt.tight_layout()
plt.savefig(os.path.join(REPORTS, "10_elbow_silhouette.png"),
            dpi=150, bbox_inches="tight")
plt.close()
print("  Saved: 10_elbow_silhouette.png")

# Fit K=4
kmeans = KMeans(n_clusters=4, random_state=42, n_init=10)
X_cluster = X_cluster.copy()
X_cluster["cluster"] = kmeans.fit_predict(X_scaled)
sil_k4 = silhouette_score(X_scaled, X_cluster["cluster"], sample_size=10000)

cluster_profile = X_cluster.groupby("cluster").agg(
    avg_premium        = ("annual_premium",       "mean"),
    avg_risk_score     = ("risk_score",           "mean"),
    avg_tenure_months  = ("policy_tenure_months", "mean"),
    avg_income_code    = ("income_encoded",        "mean"),
    count              = ("annual_premium",       "count")
).round(2)

print(f"  Silhouette score (K=4): {sil_k4:.4f}")
print("\n  === CLUSTER PROFILES ===")
print(cluster_profile.to_string())

cluster_sizes = X_cluster["cluster"].value_counts().sort_index()
cluster_names = ["Cluster 0: Low Risk", "Cluster 1: High Value",
                 "Cluster 2: At Risk",  "Cluster 3: Mid Tier"]
colors_cluster = [PURPLE, GREEN, RED, AMBER]

fig, axes = plt.subplots(1, 2, figsize=(16, 6))
for i in range(4):
    mask = X_cluster["cluster"] == i
    axes[0].scatter(
        X_cluster.loc[mask, "risk_score"],
        X_cluster.loc[mask, "annual_premium"],
        c=colors_cluster[i], label=cluster_names[i], alpha=0.3, s=8
    )
axes[0].set_title("Customer Clusters: Risk Score vs Premium", fontweight="bold")
axes[0].set_xlabel("Risk Score")
axes[0].set_ylabel("Annual Premium (₹)")
axes[0].legend(markerscale=3)

bars = axes[1].bar(cluster_names, cluster_sizes.values,
                   color=colors_cluster, alpha=0.85, edgecolor="white")
axes[1].bar_label(bars, fmt="%,.0f", padding=4)
axes[1].set_title("Customer Count per Cluster", fontweight="bold")
axes[1].set_ylabel("Number of Policies")
axes[1].tick_params(axis="x", rotation=15)

plt.suptitle(f"KMeans Customer Segmentation (K=4, Silhouette={sil_k4:.3f})",
             fontsize=14, fontweight="bold")
plt.tight_layout()
plt.savefig(os.path.join(REPORTS, "11_kmeans_clusters.png"),
            dpi=150, bbox_inches="tight")
plt.close()
print("  Saved: 11_kmeans_clusters.png")

# ─────────────────────────────────────────────────────────────
# 4. LAPSE PREDICTION — Classification
# ─────────────────────────────────────────────────────────────
print("\n[Lapse] Training classification models...")

df_model = policies.merge(
    products[["product_id", "product_line"]], on="product_id", how="left"
)
df_model = df_model.merge(
    customers[["customer_id", "income_bracket", "education_level"]],
    on="customer_id", how="left"
)

le2 = LabelEncoder()
df_model["product_line_enc"] = le2.fit_transform(df_model["product_line"].fillna("Unknown"))
df_model["income_enc"]       = le2.fit_transform(df_model["income_bracket"].fillna("Unknown"))
df_model["education_enc"]    = le2.fit_transform(df_model["education_level"].fillna("Unknown"))

lapse_features = ["annual_premium", "risk_score", "policy_tenure_months",
                  "product_line_enc", "income_enc", "education_enc"]
target = "is_lapsed"

df_clean = df_model[lapse_features + [target]].dropna()
X = df_clean[lapse_features]
y = df_clean[target].astype(int)

print(f"  Dataset: {len(df_clean):,} | Lapse rate: {y.mean()*100:.1f}%")

X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42, stratify=y
)
scaler_lr = StandardScaler()
X_train_sc = scaler_lr.fit_transform(X_train)
X_test_sc  = scaler_lr.transform(X_test)

# Logistic Regression — class_weight='balanced' handles ~18% lapse imbalance
lr = LogisticRegression(random_state=42, max_iter=1000, class_weight="balanced")
lr.fit(X_train_sc, y_train)
y_pred_lr = lr.predict(X_test_sc)
y_prob_lr = lr.predict_proba(X_test_sc)[:, 1]

lr_auc = roc_auc_score(y_test, y_prob_lr)
print(f"\n  === LOGISTIC REGRESSION ===")
print(classification_report(y_test, y_pred_lr, target_names=["Not Lapsed", "Lapsed"]))
print(f"  ROC-AUC: {lr_auc:.4f}")

# Random Forest — constrained to prevent overfitting on imbalanced data
rf = RandomForestClassifier(
    n_estimators=200,
    max_depth=10,
    min_samples_leaf=50,
    random_state=42,
    n_jobs=-1,
    class_weight="balanced"
)
rf.fit(X_train, y_train)
y_pred_rf = rf.predict(X_test)
y_prob_rf  = rf.predict_proba(X_test)[:, 1]

rf_auc = roc_auc_score(y_test, y_prob_rf)
print(f"\n  === RANDOM FOREST ===")
print(classification_report(y_test, y_pred_rf, target_names=["Not Lapsed", "Lapsed"]))
print(f"  ROC-AUC: {rf_auc:.4f}")

# Feature importance
feat_imp = pd.Series(rf.feature_importances_, index=lapse_features).sort_values()
top_feature = feat_imp.index[-1]

# ROC curve + feature importance chart
fpr_lr, tpr_lr, _ = roc_curve(y_test, y_prob_lr)
fpr_rf, tpr_rf, _ = roc_curve(y_test, y_prob_rf)

fig, axes = plt.subplots(1, 2, figsize=(16, 6))

axes[0].plot(fpr_lr, tpr_lr, color=PURPLE, linewidth=2,
             label=f"Logistic Regression (AUC={lr_auc:.3f})")
axes[0].plot(fpr_rf, tpr_rf, color=GREEN, linewidth=2,
             label=f"Random Forest (AUC={rf_auc:.3f})")
axes[0].plot([0, 1], [0, 1], "k--", linewidth=1, label="Random classifier")
axes[0].set_title("ROC Curve — Lapse Prediction", fontweight="bold")
axes[0].set_xlabel("False Positive Rate")
axes[0].set_ylabel("True Positive Rate")
axes[0].legend()

feat_imp.plot(kind="barh", ax=axes[1], color=PURPLE, alpha=0.85)
axes[1].set_title("Feature Importance — Random Forest", fontweight="bold")
axes[1].set_xlabel("Importance Score")

plt.suptitle("Lapse Prediction Model Evaluation", fontsize=14, fontweight="bold")
plt.tight_layout()
plt.savefig(os.path.join(REPORTS, "12_lapse_prediction.png"),
            dpi=150, bbox_inches="tight")
plt.close()
print("  Saved: 12_lapse_prediction.png")

# ─────────────────────────────────────────────────────────────
# 5. CLV PREDICTION — Linear Regression
# FIX: removed previous_premium, new_premium, premium_change_pct
# These columns mathematically define CLV → caused R²=1.0 leakage.
# New features predict CLV from customer/risk characteristics only.
# ─────────────────────────────────────────────────────────────
print("\n[CLV] Training regression model...")

# Engineer age from date_of_birth
customers["date_of_birth"] = pd.to_datetime(customers["date_of_birth"])
customers["age"] = (
    (pd.Timestamp("2024-12-31") - customers["date_of_birth"]).dt.days / 365.25
).astype(int)

# Encode product_line for CLV model
le3 = LabelEncoder()
policies_enc = policies.copy()
policies_enc = policies_enc.merge(
    products[["product_id", "product_line"]], on="product_id", how="left"
)
policies_enc["product_line_enc"] = le3.fit_transform(
    policies_enc["product_line"].fillna("Unknown")
)

# Build CLV dataset — join renewals to policy features and customer age
df_clv = renewals[["policy_id", "clv_at_renewal"]].dropna()
df_clv = df_clv.merge(
    policies_enc[["policy_id", "risk_score", "policy_tenure_months",
                  "annual_premium", "product_line_enc", "customer_id"]],
    on="policy_id", how="left"
)
df_clv = df_clv.merge(
    customers[["customer_id", "age", "income_bracket"]],
    on="customer_id", how="left"
)

# Encode income for CLV model
le4 = LabelEncoder()
df_clv["income_enc"] = le4.fit_transform(df_clv["income_bracket"].fillna("Unknown"))
df_clv = df_clv.dropna()

# CLV features: risk characteristics + demographics only
# Deliberately excludes premium columns that define CLV mathematically
clv_features = ["risk_score", "policy_tenure_months", "product_line_enc",
                "age", "income_enc"]
clv_target   = "clv_at_renewal"

X_clv = df_clv[clv_features]
y_clv = df_clv[clv_target]

X_train_c, X_test_c, y_train_c, y_test_c = train_test_split(
    X_clv, y_clv, test_size=0.2, random_state=42
)

print(f"  CLV dataset: {len(df_clv):,} records")

reg = LinearRegression()
reg.fit(X_train_c, y_train_c)
y_pred_clv = reg.predict(X_test_c)

r2  = r2_score(y_test_c, y_pred_clv)
mae = mean_absolute_error(y_test_c, y_pred_clv)

print(f"\n  === CLV LINEAR REGRESSION ===")
print(f"  R² Score:  {r2:.4f}")
print(f"  MAE:       ₹{mae:,.0f}")
print(f"\n  Coefficients:")
for feat, coef in zip(clv_features, reg.coef_):
    print(f"    {feat:<25} {coef:>12.2f}")

# Actual vs Predicted scatter + coefficients
fig, axes = plt.subplots(1, 2, figsize=(16, 6))

sample = np.random.choice(len(y_test_c), size=min(5000, len(y_test_c)), replace=False)
axes[0].scatter(y_test_c.iloc[sample], y_pred_clv[sample],
                alpha=0.3, color=PURPLE, s=10)
mn = min(y_test_c.min(), y_pred_clv.min())
mx = max(y_test_c.max(), y_pred_clv.max())
axes[0].plot([mn, mx], [mn, mx], color=RED, linewidth=2,
             linestyle="--", label="Perfect fit")
axes[0].set_title(f"Actual vs Predicted CLV (R²={r2:.3f})", fontweight="bold")
axes[0].set_xlabel("Actual CLV (₹)")
axes[0].set_ylabel("Predicted CLV (₹)")
axes[0].legend()

coef_df = pd.Series(reg.coef_, index=clv_features).sort_values()
colors_coef = [RED if c < 0 else GREEN for c in coef_df.values]
coef_df.plot(kind="barh", ax=axes[1], color=colors_coef, alpha=0.85)
axes[1].axvline(x=0, color="black", linewidth=0.8)
axes[1].set_title("CLV Regression Coefficients", fontweight="bold")
axes[1].set_xlabel("Coefficient Value")

plt.suptitle("CLV Prediction — Linear Regression", fontsize=14, fontweight="bold")
plt.tight_layout()
plt.savefig(os.path.join(REPORTS, "14_clv_regression.png"),
            dpi=150, bbox_inches="tight")
plt.close()
print("  Saved: 14_clv_regression.png")

# ─────────────────────────────────────────────────────────────
# 6. SUMMARY
# ─────────────────────────────────────────────────────────────
engine.dispose()

print("\n" + "=" * 55)
print("   INSURANCE POLICY LIFECYCLE — ML SUMMARY")
print("=" * 55)
print()
print("EDA")
print(f"  Overall lapse rate:        {lapse_rate:.1f}%")
print(f"  Avg annual premium:        ₹{avg_premium:,.0f}")
print(f"  Avg risk score:            {avg_risk:.1f} ± {std_risk:.1f} / 100")
print()
print("Customer Segmentation (KMeans, K=4)")
print(f"  Silhouette score:          {sil_k4:.4f}")
print(f"  Largest cluster:           {cluster_sizes.max():,} policies")
print()
print("Lapse Prediction (Classification)")
print(f"  Logistic Regression AUC:   {lr_auc:.4f}")
print(f"  Random Forest AUC:         {rf_auc:.4f}")
print(f"  Top feature:               {top_feature}")
print()
print("CLV Prediction (Linear Regression)")
print(f"  R² Score:                  {r2:.4f}")
print(f"  MAE:                       ₹{mae:,.0f}")
print()
print(f"Charts saved to reports/v2_calibrated/")
print("=" * 55)
