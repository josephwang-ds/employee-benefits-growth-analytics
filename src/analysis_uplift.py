# -*- coding: utf-8 -*-
"""
Step 7: Uplift 可信实施计划 (v2 口径) — 阶段三/四的离线原型
四阶段: (1)分层异质性分析[见 analysis_experiment.py] -> (2)积累多批次实验标签
        -> (3)基线: 随机触达 + Propensity Model -> (4)T-Learner 离线原型 + Qini/AUUC
定位说明: 单次 1,500 人实验不足以支撑生产级 Uplift 系统。本脚本是离线原型,
本模块定位为离线设计与评估方法; 上线需新活动 Holdout 验证。
用法: python src/analysis_uplift.py [--db data/yuyue.duckdb]
"""
import argparse
import duckdb
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import train_test_split
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

FEATURES = ["historical_redemption_count", "historical_campaign_count",
            "historical_redemption_amount", "first_visit_day",
            "seg_code", "benefit_code", "dept_code"]


def load(con):
    df = con.execute("""
        SELECT e.user_id, e.group_name, d.exp_redeemed_flag AS y,
               d.activity_segment, d.benefit_type, d.department_group,
               d.historical_redemption_count, d.historical_campaign_count,
               d.historical_redemption_amount, d.first_visit_day
        FROM fact_experiment_assignment e
        JOIN dws_user_campaign d ON e.user_id = d.user_id
        WHERE e.group_name IN ('Control', 'Reminder')   -- 只用 Reminder 实验对训练 uplift
    """).df()
    df["seg_code"] = df.activity_segment.map({"New": 0, "Low": 1, "Medium": 2, "High": 3})
    df["benefit_code"] = df.benefit_type.astype("category").cat.codes
    df["dept_code"] = df.department_group.astype("category").cat.codes
    df["first_visit_day"] = df.first_visit_day.fillna(df.first_visit_day.median())
    df["treated"] = (df.group_name == "Reminder").astype(int)
    return df


def t_learner(df, seed=42):
    """阶段四 T-Learner: 分别对 Treatment / Control 建模, uplift = P(y|T=1) - P(y|T=0)
    (生产环境应按活动批次/时间切分; Demo 只有单活动, 用随机切分并明确说明局限)"""
    tr, te = train_test_split(df, test_size=0.35, random_state=seed, stratify=df.treated)
    mt = GradientBoostingClassifier(max_depth=3, n_estimators=120, learning_rate=0.05,
                                    random_state=seed)
    mc = GradientBoostingClassifier(max_depth=3, n_estimators=120, learning_rate=0.05,
                                    random_state=seed)
    mt.fit(tr[tr.treated == 1][FEATURES], tr[tr.treated == 1].y)
    mc.fit(tr[tr.treated == 0][FEATURES], tr[tr.treated == 0].y)
    te = te.copy()
    te["uplift_score"] = mt.predict_proba(te[FEATURES])[:, 1] - mc.predict_proba(te[FEATURES])[:, 1]
    return te, mt, mc


def propensity_baseline(te, tr_df, seed=42):
    """阶段三 Baseline B: Propensity Model (预测谁会核销, 不考虑增量)
    目的: 证明"高核销概率 ≠ 高增量" — Propensity 排序选出的多是 Sure Thing"""
    mp = GradientBoostingClassifier(max_depth=3, n_estimators=120, learning_rate=0.05,
                                    random_state=seed)
    ctrl = tr_df[tr_df.treated == 0]
    mp.fit(ctrl[FEATURES], ctrl.y)
    te = te.copy()
    te["propensity_score"] = mp.predict_proba(te[FEATURES])[:, 1]
    n = len(te)
    print("\n========== 阶段三: Propensity vs Uplift 排序对比 (Top 20%) ==========")
    for name, col in [("Propensity 排序", "propensity_score"), ("Uplift 排序", "uplift_score")]:
        top = te.sort_values(col, ascending=False).head(int(n * 0.2))
        t, c = top[top.treated == 1], top[top.treated == 0]
        if len(t) < 10 or len(c) < 10:
            continue
        lift = t.y.mean() - c.y.mean()
        print(f"  {name:<14} Top20% 实际实验Lift = {lift*100:+.1f}pp  "
              f"(高分人群 Control 核销率 {c.y.mean():.1%})")
    print("  预期: Propensity 高分人群 Control 核销率本来就高(Sure Thing), 增量反而低")
    return te


def qini(te, out_png="reports/qini_curve.png"):
    """Qini 曲线: 按 uplift 分排序, 累积增量 vs 随机"""
    d = te.sort_values("uplift_score", ascending=False).reset_index(drop=True)
    n = len(d)
    n_t_all, n_c_all = d.treated.sum(), (1 - d.treated).sum()
    qini_y, auuc = [0.0], 0.0
    for k in range(1, n + 1):
        top = d.iloc[:k]
        nt, nc = top.treated.sum(), k - top.treated.sum()
        yt = top[top.treated == 1].y.sum()
        yc = top[top.treated == 0].y.sum()
        q = yt - yc * (nt / max(nc, 1))  # Qini 定义
        qini_y.append(q)
    qini_y = np.array(qini_y)
    # 随机基线: 线性到终点
    rand = np.linspace(0, qini_y[-1], n + 1)
    auuc = (qini_y - rand).sum() / n
    x = np.arange(n + 1) / n

    plt.figure(figsize=(7, 4.5))
    plt.plot(x, qini_y, label="T-Learner Uplift")
    plt.plot(x, rand, "--", label="Random")
    plt.xlabel("Targeted fraction")
    plt.ylabel("Cumulative incremental conversions (Qini)")
    plt.title(f"Qini Curve  (AUUC vs random = {auuc:.2f})")
    plt.legend(); plt.grid(alpha=0.3); plt.tight_layout()
    plt.savefig(out_png, dpi=120)
    print(f"\nQini 曲线已保存: {out_png}   AUUC(vs random) = {auuc:.2f}")
    return qini_y, auuc


def topk_policy(te, margin=13.0, msg_cost=0.05):
    """Top-k 触达策略模拟: 只提醒 uplift 最高的 k% 用户"""
    d = te.sort_values("uplift_score", ascending=False).reset_index(drop=True)
    print("\n========== Top-k 策略模拟 (离线, 基于测试集) ==========")
    print(f"{'策略':<12}{'覆盖人数':>8}{'实际增量(est)':>14}{'触达成本':>10}{'净贡献(元)':>12}")
    n = len(d)
    for pct in [0.1, 0.2, 0.3, 0.5, 1.0]:
        k = int(n * pct)
        top = d.iloc[:k]
        t, c = top[top.treated == 1], top[top.treated == 0]
        if len(t) < 10 or len(c) < 10:
            continue
        inc_rate = t.y.mean() - c.y.mean()          # 该子群的实际实验增量
        inc_users = inc_rate * k
        net = inc_users * margin - k * msg_cost
        print(f"Top {pct:>4.0%}   {k:>8d}   {inc_users:>10.1f} 人   {k*msg_cost:>8.1f}   {net:>10.0f}")
    print("""
使用限制:
- 这是基于单次实验的离线模拟, 展示的是资源分配思路;
- 上线前需要新一轮 Holdout 验证, 不能把模拟 ROI 说成已发生业绩;
- 样本量 ~1000 (两组), 分位内实验增量估计噪声大, 只作方向判断。""")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/yuyue.duckdb")
    args = ap.parse_args()
    con = duckdb.connect(args.db, read_only=True)
    df = load(con)
    con.close()
    tr, _ = train_test_split(df, test_size=0.35, random_state=42, stratify=df.treated)
    te, mt, mc = t_learner(df)
    print("========== 阶段四: Uplift Score 分布 (测试集) ==========")
    print(te.groupby("activity_segment").uplift_score.describe()[["count", "mean", "50%"]].round(3).to_string())
    print("  预期: Low/Medium 分群 uplift 最高 (Persuadable), High 最低 (Sure Thing)")
    te = propensity_baseline(te, tr)
    qini(te)
    topk_policy(te)
