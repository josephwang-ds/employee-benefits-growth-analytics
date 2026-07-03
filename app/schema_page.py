# -*- coding: utf-8 -*-
"""Data Schema 页 — 高层展示：分层、漏斗、表清单、JOIN 关系。"""
import pandas as pd
import streamlit as st

# 表清单：layer, table, 一句话
TABLES = {
    "dim_": [
        ("dim_enterprise", "企业"),
        ("dim_campaign", "活动 / 预算 / 时间窗"),
        ("dim_user", "员工画像"),
        ("dim_benefit", "权益商品目录"),
    ],
    "fact_": [
        ("fact_campaign_eligibility", "活动资格"),
        ("fact_coupon_issuance", "卡券发放"),
        ("fact_touch", "消息触达"),
        ("fact_user_event", "页面行为"),
        ("fact_claim", "领取"),
        ("fact_redemption_order", "核销订单"),
        ("fact_fulfillment", "供应商履约"),
        ("fact_coupon_purchase_batch", "采购批次"),
        ("fact_coupon_inventory", "库存实例"),
        ("fact_experiment_assignment", "实验分组"),
    ],
    "dws_": [
        ("dws_user_campaign", "用户活动宽表（分析入口）"),
    ],
}

FUNNEL_STAGES = [
    ("eligible", "资格", "fact_campaign_eligibility"),
    ("issued", "发放", "fact_coupon_issuance"),
    ("reached", "触达", "fact_touch"),
    ("visited", "访问", "fact_user_event"),
    ("claimed", "领取", "fact_claim"),
    ("redeemed_14d", "14天核销", "fact_redemption_order"),
    ("fulfilled", "履约", "fact_fulfillment"),
]

# 核心 JOIN 关系（展示用）
JOIN_MAP = [
    ("fact_experiment_assignment", "user_id", "dws_user_campaign", "实验分析"),
    ("dws_user_campaign", "user_id", "fact_redemption_order", "核销速度 / 收入"),
    ("fact_redemption_order", "order_id", "fact_fulfillment", "履约监控"),
    ("fact_redemption_order", "coupon_instance_id", "fact_claim", "领取-核销校验"),
    ("fact_coupon_inventory", "benefit_id", "dim_benefit", "库存风险 / 成本"),
    ("fact_coupon_issuance", "coupon_instance_id", "fact_coupon_inventory", "发放-库存"),
    ("fact_redemption_order", "—", "dim_campaign", "CROSS JOIN · 预算使用率"),
]


def render_schema_page(q, docs_path=None):
    st.title("Data Schema")
    st.caption("15 tables · dim → fact → dws · synthetic demo data")

    # ── 三层结构 ──
    c1, c2, c3 = st.columns(3)
    c1.info("**dim_** × 4\n\nWho / What")
    c2.info("**fact_** × 10\n\nEvents & transactions")
    c3.success("**dws_** × 1\n\n`dws_user_campaign`\nOne row per user")

    st.divider()

    # ── 用户漏斗（live 数字 + 箭头）──
    st.subheader("User funnel")
    st.caption("Grain: `distinct user_id` · 主表 `dws_user_campaign`")

    row = q("""
        SELECT COUNT(*) elig,
               SUM(issued_flag) iss,
               SUM(reached_flag) rch,
               SUM(visited_flag) vis,
               SUM(claimed_flag) clm,
               SUM(redeemed_14d_flag) rdm,
               SUM(CASE WHEN redeemed_14d_flag=1 AND fulfilled_flag=1 THEN 1 ELSE 0 END) ful
        FROM dws_user_campaign
    """).iloc[0]
    vals = [int(row.elig), int(row.iss), int(row.rch), int(row.vis),
            int(row.clm), int(row.rdm), int(row.ful)]
    labels = ["Eligible", "Issued", "Reached", "Visited", "Claimed", "Redeemed", "Fulfilled"]

    cols = st.columns(len(labels))
    for i, (col, label, val) in enumerate(zip(cols, labels, vals)):
        pct = f"{val / vals[1] * 100:.0f}%" if i > 1 and vals[1] else (f"{val / vals[0] * 100:.0f}%" if i == 1 else "")
        col.metric(label, f"{val:,}", pct if pct else None, help=FUNNEL_STAGES[i][2])

    st.code(
        "eligible → issued → reached → visited → claimed → redeemed_14d → fulfilled\n"
        "                              └─ Day14: fact_experiment_assignment (visited, not redeemed)",
        language="text",
    )

    st.divider()

    # ── 表清单 ──
    st.subheader("Tables")
    t1, t2, t3 = st.columns(3)
    for col, (layer, items) in zip([t1, t2, t3], TABLES.items()):
        with col:
            st.markdown(f"**{layer}**")
            rows = []
            for tbl, desc in items:
                try:
                    n = int(q(f"SELECT COUNT(*) FROM {tbl}").iloc[0, 0])
                except Exception:
                    n = "—"
                rows.append(f"`{tbl}` · {n:,} · {desc}" if isinstance(n, int) else f"`{tbl}` · {desc}")
            st.markdown("\n\n".join(rows))

    st.divider()

    # ── JOIN 关系图（表格，不依赖 Mermaid）──
    st.subheader("How tables connect")
    st.caption("Most common joins in this project")

    join_df = pd.DataFrame(JOIN_MAP, columns=["From", "Join key", "To", "Used for"])
    st.dataframe(join_df, use_container_width=True, hide_index=True)

    st.code(
        """
dim_user ──user_id──► fact_* / dws_user_campaign
dim_benefit ──benefit_id──► fact_coupon_inventory / fact_coupon_issuance
fact_coupon_issuance ──coupon_instance_id──► fact_claim ──► fact_redemption_order
                                                              └──order_id──► fact_fulfillment
fact_experiment_assignment ──user_id──► dws_user_campaign   (A/B — not fact_touch*)
        """.strip(),
        language="text",
    )
    st.caption(
        "*Experiment groups live in `fact_experiment_assignment` (includes Control). "
        "`fact_touch` only logs messages sent — Control has no experiment touch row."
    )

    st.divider()

    # ── 两条分析线 ──
    st.subheader("Two grains")
    g1, g2 = st.columns(2)
    with g1:
        st.markdown("**User line**")
        st.markdown("`user_id` → `dws_user_campaign`")
        st.markdown("Funnel · Cohort · Experiment")
    with g2:
        st.markdown("**Coupon line**")
        st.markdown("`coupon_instance_id` → `fact_coupon_inventory`")
        st.markdown("Purchase · Aging · Capital risk")
