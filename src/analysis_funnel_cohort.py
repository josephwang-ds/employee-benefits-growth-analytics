# -*- coding: utf-8 -*-
"""
Step 3-4: Funnel 与 Cohort 分析（全部基于 DuckDB SQL, 统一口径）
用法: python src/analysis_funnel_cohort.py [--db data/benefits.duckdb]
"""
import argparse
import duckdb
import pandas as pd

pd.set_option("display.width", 160)
pd.set_option("display.max_columns", 30)


def funnel(con):
    """七步 Funnel: 阶段转化 + 累计转化（口径: 分母=成功发放用户, distinct user）"""
    q = """
    WITH base AS (
        SELECT
            COUNT(*)                              AS eligible,
            SUM(issued_flag)                      AS issued,
            SUM(reached_flag)                     AS reached,
            SUM(visited_flag)                     AS visited,
            SUM(claimed_flag)                     AS claimed,
            SUM(redeemed_14d_flag)                AS redeemed_14d,
            SUM(CASE WHEN redeemed_14d_flag=1 AND fulfilled_flag=1 THEN 1 ELSE 0 END) AS fulfilled
        FROM dws_user_campaign
    )
    SELECT * FROM base
    """
    b = con.execute(q).df().iloc[0]
    stages = ["eligible", "issued", "reached", "visited", "claimed", "redeemed_14d", "fulfilled"]
    rows = []
    for i, s in enumerate(stages):
        n = int(b[s])
        rows.append(dict(
            stage=s, users=n,
            stage_conv=None if i == 0 else round(n / b[stages[i - 1]] * 100, 1),   # 阶段转化
            cum_conv_vs_issued=None if i == 0 else round(n / b["issued"] * 100, 1)  # 累计转化(分母=发放成功)
        ))
    df = pd.DataFrame(rows)
    print("\n========== 七步 Funnel ==========")
    print(df.to_string(index=False))
    print("""
口径说明:
- 累计转化分母统一为“成功发放用户”(issued), 与客户结算口径一致
- 用户级 distinct user_id, 不是卡券级/订单级
- fulfilled 指 14 天核销且最终履约成功（北极星: 有效履约用户率）""")
    return df


def funnel_by(con, dim):
    q = f"""
    SELECT {dim},
           SUM(issued_flag) AS issued,
           ROUND(SUM(reached_flag)  * 100.0 / SUM(issued_flag), 1) AS reach_pct,
           ROUND(SUM(visited_flag)  * 100.0 / SUM(issued_flag), 1) AS visit_pct,
           ROUND(SUM(claimed_flag)  * 100.0 / SUM(issued_flag), 1) AS claim_pct,
           ROUND(SUM(redeemed_14d_flag) * 100.0 / SUM(issued_flag), 1) AS redeem14_pct
    FROM dws_user_campaign
    GROUP BY 1 ORDER BY issued DESC
    """
    df = con.execute(q).df()
    print(f"\n========== Funnel 按 {dim} 拆解 ==========")
    print(df.to_string(index=False))
    return df


def cohort_activity(con):
    """历史活跃度 Cohort: 分层行为差异（High 分群基线核销高 → Sure Thing）"""
    q = """
    SELECT activity_segment,
           COUNT(*) AS users,
           ROUND(SUM(visited_flag) * 100.0 / SUM(issued_flag), 1) AS visit_pct,
           ROUND(SUM(redeemed_14d_flag) * 100.0 / SUM(issued_flag), 1) AS redeem14_pct,
           ROUND(AVG(historical_redemption_count), 2) AS hist_redeem_avg
    FROM dws_user_campaign
    WHERE issued_flag = 1
    GROUP BY 1
    ORDER BY CASE activity_segment WHEN 'New' THEN 1 WHEN 'Low' THEN 2
             WHEN 'Medium' THEN 3 ELSE 4 END
    """
    df = con.execute(q).df()
    print("\n========== 历史活跃度 Cohort ==========")
    print(df.to_string(index=False))
    return df


def cohort_speed(con):
    """核销速度曲线 D1/D3/D7/D14（按活跃度分群）— 回答“不同人群核销多快”"""
    q = """
    WITH r AS (
        SELECT d.user_id, d.activity_segment,
               DATE_DIFF('hour', c.start_time, o.redemption_time) / 24.0 AS rday
        FROM dws_user_campaign d
        JOIN fact_redemption_order o ON d.user_id = o.user_id AND o.redemption_status = 'success'
        CROSS JOIN dim_campaign c
        WHERE d.redeemed_14d_flag = 1
    )
    SELECT activity_segment,
           COUNT(*) AS redeemers,
           ROUND(SUM(CASE WHEN rday <= 1  THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) AS d1_pct,
           ROUND(SUM(CASE WHEN rday <= 3  THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) AS d3_pct,
           ROUND(SUM(CASE WHEN rday <= 7  THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) AS d7_pct,
           ROUND(SUM(CASE WHEN rday <= 14 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) AS d14_pct
    FROM r GROUP BY 1
    ORDER BY CASE activity_segment WHEN 'New' THEN 1 WHEN 'Low' THEN 2
             WHEN 'Medium' THEN 3 ELSE 4 END
    """
    df = con.execute(q).df()
    print("\n========== 核销速度 Cohort (占最终核销者比例) ==========")
    print(df.to_string(index=False))
    return df


def benefit_cohort(con):
    """权益类型 Cohort + 供应商履约（关注: 核销上升但投诉/退款同步上升的权益）"""
    q = """
    SELECT d.benefit_type,
           f.supplier_id,
           COUNT(DISTINCT o.order_id) AS orders,
           ROUND(SUM(CASE WHEN f.fulfillment_status='success' THEN 1 ELSE 0 END) * 100.0
                 / COUNT(*), 1) AS fulfill_pct,
           ROUND(SUM(f.refund_flag) * 100.0 / COUNT(*), 1) AS refund_pct,
           ROUND(SUM(f.complaint_flag) * 100.0 / COUNT(*), 1) AS complaint_pct
    FROM fact_redemption_order o
    JOIN fact_fulfillment f ON o.order_id = f.order_id
    JOIN dws_user_campaign d ON o.user_id = d.user_id
    GROUP BY 1, 2 ORDER BY fulfill_pct
    """
    df = con.execute(q).df()
    print("\n========== 权益类型 × 供应商履约 ==========")
    print(df.to_string(index=False))
    return df


def data_quality(con):
    """数据质量检查: 主键唯一、时间逻辑、状态一致性"""
    checks = {
        "订单主键唯一":
            "SELECT COUNT(*) - COUNT(DISTINCT order_id) FROM fact_redemption_order",
        "卡券主键唯一":
            "SELECT COUNT(*) - COUNT(DISTINCT coupon_instance_id) FROM fact_coupon_issuance",
        "实验分组唯一(experiment_id+user_id)":
            """SELECT COUNT(*) FROM (SELECT experiment_id, user_id, COUNT(*) c
               FROM fact_experiment_assignment GROUP BY 1,2 HAVING c > 1)""",
        "核销早于领取(应为0)":
            """SELECT COUNT(*) FROM fact_redemption_order o JOIN fact_claim c
               ON o.coupon_instance_id = c.coupon_instance_id
               WHERE o.redemption_time < c.claim_time""",
        "核销但未发放(应为0)":
            """SELECT COUNT(*) FROM dws_user_campaign
               WHERE redeemed_14d_flag = 1 AND issued_flag = 0""",
        "Control组收到实验提醒(应为0)":
            """SELECT COUNT(*) FROM fact_touch t
               JOIN fact_experiment_assignment e ON t.user_id = e.user_id
               WHERE e.group_name = 'Control' AND t.experiment_id IS NOT NULL""",
        "履约无对应订单(应为0)":
            """SELECT COUNT(*) FROM fact_fulfillment f
               LEFT JOIN fact_redemption_order o ON f.order_id = o.order_id
               WHERE o.order_id IS NULL""",
    }
    print("\n========== 数据质量检查 ==========")
    for name, q in checks.items():
        n = con.execute(q).fetchone()[0]
        print(f"  [{'PASS' if n == 0 else 'FAIL'}] {name}: {n}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/benefits.duckdb")
    args = ap.parse_args()
    con = duckdb.connect(args.db, read_only=True)
    data_quality(con)
    funnel(con)
    funnel_by(con, "department_group")
    funnel_by(con, "benefit_type")
    cohort_activity(con)
    cohort_speed(con)
    benefit_cohort(con)
    con.close()
