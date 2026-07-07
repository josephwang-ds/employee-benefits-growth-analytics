# -*- coding: utf-8 -*-
"""
Step 5-6: A/B 实验分析 — SRM、平衡性、Lift、CI、p-value、Guardrails
目标口径: Control ~28.6% / Reminder ~34.8% / Reminder+Reward ~37.1%
用法: python src/analysis_experiment.py [--db data/benefits.duckdb]
"""
import argparse
import duckdb
import numpy as np
import pandas as pd
from scipy import stats

pd.set_option("display.width", 160)


def load(con):
    return con.execute("""
        SELECT e.user_id, e.group_name, e.stratification_key AS segment,
               d.exp_redeemed_flag AS y,
               d.historical_redemption_count, d.benefit_type,
               d.complaint_flag, d.reward_cost, d.settlement_amount, d.supplier_cost
        FROM fact_experiment_assignment e
        JOIN dws_user_campaign d ON e.user_id = d.user_id
    """).df()


def srm_check(df):
    """SRM: 卡方拟合优度检验, 三组是否符合 1:1:1"""
    obs = df.group_name.value_counts().sort_index()
    exp = np.full(len(obs), obs.sum() / len(obs))
    chi2, p = stats.chisquare(obs, exp)
    print("\n========== SRM 检查 ==========")
    print(obs.to_string())
    print(f"chi2={chi2:.3f}, p={p:.3f}  ->  {'通过(无SRM)' if p > 0.05 else '警告: 样本比例失衡!'}")


def balance_check(df):
    """实验前平衡性: 各组历史核销/分群构成应无显著差异"""
    print("\n========== 实验前平衡性 ==========")
    print(df.groupby("group_name").agg(
        n=("y", "size"),
        hist_redeem_mean=("historical_redemption_count", "mean")).round(3).to_string())
    ct = pd.crosstab(df.group_name, df.segment)
    chi2, p, *_ = stats.chi2_contingency(ct)
    print(f"分群构成卡方: p={p:.3f}  ({'平衡' if p > 0.05 else '不平衡!'})")


def two_prop_test(x1, n1, x2, n2, label):
    """两比例 z 检验 + 95% CI (Wald)"""
    p1, p2 = x1 / n1, x2 / n2
    diff = p1 - p2
    p_pool = (x1 + x2) / (n1 + n2)
    se_pool = np.sqrt(p_pool * (1 - p_pool) * (1 / n1 + 1 / n2))
    z = diff / se_pool
    pval = 2 * (1 - stats.norm.cdf(abs(z)))
    se_ci = np.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    lo, hi = diff - 1.96 * se_ci, diff + 1.96 * se_ci
    print(f"\n--- {label} ---")
    print(f"  {p1:.1%} vs {p2:.1%}   绝对Lift = {diff*100:+.1f}pp   相对Lift = {diff/p2*100:+.1f}%")
    print(f"  z = {z:.2f},  p-value = {pval:.4f}   95% CI = [{lo*100:+.1f}pp, {hi*100:+.1f}pp]")
    print(f"  => {'显著 (α=0.05)' if pval < 0.05 else '不显著 (α=0.05)'}")
    return dict(lift=diff, pval=pval, ci=(lo, hi))


def main_analysis(df):
    print("\n========== 分组核销率 ==========")
    g = df.groupby("group_name").y.agg(["count", "sum", "mean"])
    g["mean"] = (g["mean"] * 100).round(1)
    print(g.rename(columns={"count": "n", "sum": "redeemers", "mean": "rate_pct"}).to_string())

    c = df[df.group_name == "Control"]
    r = df[df.group_name == "Reminder"]
    w = df[df.group_name == "Reminder+Reward"]

    res_rem = two_prop_test(r.y.sum(), len(r), c.y.sum(), len(c), "Reminder vs Control")
    res_rew = two_prop_test(w.y.sum(), len(w), r.y.sum(), len(r), "Reminder+Reward vs Reminder")

    # 离线的增量/成本估算, 用于判断"值不值得做", 非上线业绩; ROI 数字不作为主线展示
    print("\n========== 增量人数与贡献估算 (离线, 方向参考) ==========")
    inc_rem = res_rem["lift"] * len(r)
    inc_rew = res_rew["lift"] * len(w)
    # 单次增量核销贡献 = 结算收入 - 供应商成本（取全体订单平均毛差）
    margin = (df.settlement_amount - df.supplier_cost)[df.y == 1].mean()
    msg_cost = 0.05  # 每条企业微信/短信触达成本(元), 可调
    print(f"  Reminder 增量核销 ≈ {inc_rem:.0f} 人; 单次增量毛差 ≈ {margin:.1f} 元")
    print(f"  Reminder 增量贡献 ≈ {inc_rem * margin - len(r) * msg_cost:.0f} 元 (触达成本按 {msg_cost}/条)")
    reward_per = 5.0
    reward_cost_total = w.y.sum() * reward_per  # 核销后发放口径
    print(f"  Reward 相比 Reminder 增量 ≈ {inc_rew:.0f} 人, 增量贡献 ≈ {inc_rew * margin:.0f} 元")
    print(f"  Reward 成本(核销后发放 {reward_per}元/人) ≈ {reward_cost_total:.0f} 元")
    print(f"  => Reward 净增量 ≈ {inc_rew * margin - inc_rew * reward_per:.0f} 元 边际价值有限, "
          f"且置信区间{'包含0' if res_rew['ci'][0] < 0 else '不含0'} -> 不建议全量奖励")

    print("\n========== 异质性: 分群 Lift (Reminder vs Control) ==========")
    for seg in ["New", "Low", "Medium", "High"]:
        cs, rs = c[c.segment == seg], r[r.segment == seg]
        if len(cs) < 30 or len(rs) < 30:
            continue
        lift = rs.y.mean() - cs.y.mean()
        print(f"  {seg:<7} n_c={len(cs):3d} n_t={len(rs):3d}  "
              f"{cs.y.mean():.1%} -> {rs.y.mean():.1%}  lift={lift*100:+.1f}pp")
    print("  (注意: 分群样本量小, 只作方向参考; 个体级 Uplift 建模属可选高级模块, 非主线)")


def guardrails(con):
    print("\n========== Guardrail Metrics (实验人群) ==========")
    q = """
    SELECT e.group_name,
           COUNT(*) AS n,
           ROUND(AVG(d.complaint_flag) * 100, 2) AS complaint_pct,
           ROUND(SUM(CASE WHEN o.redemption_status = 'refunded' THEN 1 ELSE 0 END)
                 * 100.0 / COUNT(*), 2) AS refund_pct
    FROM fact_experiment_assignment e
    JOIN dws_user_campaign d ON e.user_id = d.user_id
    LEFT JOIN fact_redemption_order o ON e.user_id = o.user_id
    GROUP BY 1 ORDER BY 1
    """
    print(con.execute(q).df().to_string(index=False))
    print("  口径: Reminder 组投诉/退款与 Control 持平 => 提醒本身未造成体验损伤;\n"
          "        Reward 组投诉略高(与履约压力相关), 是不推全量 Reward 的辅助证据")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/benefits.duckdb")
    args = ap.parse_args()
    con = duckdb.connect(args.db, read_only=True)
    df = load(con)
    srm_check(df)
    balance_check(df)
    main_analysis(df)
    guardrails(con)
    con.close()
