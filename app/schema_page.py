# -*- coding: utf-8 -*-
"""Streamlit page: interactive data schema (15 tables + diagrams + live explorer)."""
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

FUNNEL_MERMAID = """
flowchart TD
    A["Eligible<br/>fact_campaign_eligibility<br/>5,000"] --> B["Issued<br/>fact_coupon_issuance"]
    B --> C["Reached<br/>fact_touch"]
    C --> D["Visited<br/>fact_user_event"]
    D --> E["Claimed<br/>fact_claim"]
    E --> F["Redeemed 14d<br/>fact_redemption_order"]
    F --> G["Fulfilled<br/>fact_fulfillment"]
    D --> H{"Day 14:<br/>visited not redeemed?"}
    H -->|"~1,500"| I["fact_experiment_assignment<br/>Control / Reminder / Reminder+Reward"]
    I --> J["exp_redeemed_flag<br/>dws_user_campaign"]
"""

ER_MERMAID = """
erDiagram
    dim_enterprise ||--o{ dim_campaign : enterprise_id
    dim_enterprise ||--o{ dim_user : enterprise_id
    dim_campaign ||--o{ fact_campaign_eligibility : campaign_id
    dim_user ||--o{ fact_coupon_issuance : user_id
    dim_benefit ||--o{ fact_coupon_issuance : benefit_id
    dim_benefit ||--o{ fact_coupon_inventory : benefit_id
    fact_coupon_purchase_batch ||--o{ fact_coupon_inventory : purchase_batch_id
    fact_coupon_issuance ||--|| fact_coupon_inventory : coupon_instance_id
    dim_user ||--o{ fact_touch : user_id
    dim_user ||--o{ fact_user_event : user_id
    fact_coupon_issuance ||--o| fact_claim : coupon_instance_id
    fact_coupon_issuance ||--o| fact_redemption_order : coupon_instance_id
    fact_redemption_order ||--|| fact_fulfillment : order_id
    dim_user ||--o{ fact_experiment_assignment : user_id
    dim_user ||--|| dws_user_campaign : user_id
"""

TABLE_CATALOG = [
    dict(layer="DIM", table="dim_enterprise", grain="Enterprise", pk="enterprise_id",
         desc="One B2B client (industry, settlement model)"),
    dict(layer="DIM", table="dim_campaign", grain="Campaign", pk="campaign_id",
         desc="One benefits campaign (dates, budget, target headcount)"),
    dict(layer="DIM", table="dim_user", grain="User", pk="user_id",
         desc="One employee (dept, region, historical activity segment)"),
    dict(layer="DIM", table="dim_benefit", grain="Benefit SKU", pk="benefit_id",
         desc="One coupon product (face value, cost, refundable flag)"),
    dict(layer="FACT", table="fact_campaign_eligibility", grain="User × campaign",
         pk="user_id + campaign_id", desc="HR eligibility list"),
    dict(layer="FACT", table="fact_coupon_issuance", grain="Coupon instance",
         pk="coupon_instance_id", desc="Coupon issued (or failed) to a user"),
    dict(layer="FACT", table="fact_touch", grain="One message send", pk="touch_id",
         desc="Notification: first / routine / experiment (Control has no experiment row)"),
    dict(layer="FACT", table="fact_user_event", grain="One app event", pk="event_id",
         desc="Page view, benefit view, click-claim"),
    dict(layer="FACT", table="fact_claim", grain="One claim", pk="claim_id",
         desc="User claims coupon into wallet"),
    dict(layer="FACT", table="fact_redemption_order", grain="One order", pk="order_id",
         desc="Redemption with settlement amount"),
    dict(layer="FACT", table="fact_fulfillment", grain="One fulfillment", pk="fulfillment_id",
         desc="Supplier delivery + complaint / refund flags"),
    dict(layer="FACT", table="fact_coupon_purchase_batch", grain="Purchase batch",
         pk="purchase_batch_id", desc="Upfront buy from supplier"),
    dict(layer="FACT", table="fact_coupon_inventory", grain="Coupon instance", pk="coupon_instance_id",
         desc="Stock: available / assigned / claimed / redeemed"),
    dict(layer="FACT", table="fact_experiment_assignment", grain="User × experiment",
         pk="experiment_id + user_id", desc="Random group at Day 14 — includes Control"),
    dict(layer="DWS", table="dws_user_campaign", grain="User × campaign", pk="user_id + campaign_id",
         desc="Funnel flags + experiment + finance — start here for most analysis"),
]

JOIN_PATTERNS = [
    ("Funnel (no join)", """SELECT SUM(issued_flag), SUM(visited_flag), SUM(redeemed_14d_flag)
FROM dws_user_campaign"""),
    ("Experiment lift", """SELECT e.group_name, COUNT(*) n, AVG(d.exp_redeemed_flag) rate
FROM fact_experiment_assignment e
JOIN dws_user_campaign d ON e.user_id = d.user_id
GROUP BY 1"""),
    ("Redemption speed", """SELECT d.activity_segment,
       DATE_DIFF('hour', c.start_time, o.redemption_time) / 24.0 AS days
FROM dws_user_campaign d
JOIN fact_redemption_order o ON d.user_id = o.user_id AND o.redemption_status = 'success'
CROSS JOIN dim_campaign c
WHERE d.redeemed_14d_flag = 1"""),
    ("Fulfillment × benefit", """SELECT d.benefit_type, f.supplier_id, COUNT(*) orders
FROM fact_redemption_order o
JOIN fact_fulfillment f ON o.order_id = f.order_id
JOIN dws_user_campaign d ON o.user_id = d.user_id
GROUP BY 1, 2"""),
    ("Inventory risk", """SELECT i.benefit_id, d.refundable_flag, COUNT(*) un_redeemed
FROM fact_coupon_inventory i
JOIN dim_benefit d ON i.benefit_id = d.benefit_id
WHERE i.coupon_status IN ('available','assigned','claimed')
GROUP BY 1, 2"""),
    ("Budget utilization", """SELECT ROUND(SUM(o.settlement_amount)*100.0/MAX(c.benefit_budget),1) pct
FROM fact_redemption_order o
CROSS JOIN dim_campaign c
WHERE o.redemption_status = 'success'"""),
]


def _mermaid(diagram: str, height: int = 420) -> None:
    """在 Streamlit 中渲染 Mermaid 图（CDN，兼容 Streamlit Cloud）。"""
    safe = diagram.strip().replace("`", "")
    components.html(
        f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"/>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
</head><body>
<div class="mermaid">{safe}</div>
<script>mermaid.initialize({{startOnLoad:true, theme:'neutral'}});</script>
</body></html>""",
        height=height,
        scrolling=True,
    )


def render_schema_page(q, docs_path=None):
    """渲染 Data Schema 页面。q 为执行 SQL 的 callable。"""
    st.title("Data Schema — 15 Tables")
    st.caption(
        "DuckDB 数据模型一览：维表 → 事实表 → 宽表。"
        "完整 Markdown 文档见仓库 `docs/DATA_SCHEMA.md`。"
    )

    tab_ov, tab_diag, tab_tbl, tab_join, tab_exp = st.tabs(
        ["总览", "关系图", "表目录", "JOIN 模板", "实验表说明"]
    )

    with tab_ov:
        st.markdown("""
**三层结构**

| Layer | Prefix | Count | Role |
|-------|--------|------:|------|
| Dimension | `dim_` | 4 | Who / what (enterprise, campaign, user, benefit) |
| Fact | `fact_` | 10 | What happened (issue → touch → redeem → inventory → experiment) |
| Wide | `dws_` | 1 | One row per user × campaign for analysis |

**分析入口：** `dws_user_campaign` — 多数 Funnel / Cohort 查询无需 JOIN。
""")
        st.subheader("业务时间线")
        st.code("""Day -10   采购入库     fact_coupon_purchase_batch, fact_coupon_inventory
Day 0     活动开始     fact_coupon_issuance, fact_touch (首次通知)
Day 0-14  基线窗口     fact_user_event, fact_claim, fact_redemption_order
Day 7     常规提醒     fact_touch
Day 14    实验分组     fact_experiment_assignment (+ 实验触达写入 fact_touch)
Day 14-28 实验观察     exp_redeemed_flag in dws_user_campaign
Day 45    卡券过期     dim_campaign.redemption_deadline""", language="text")

        st.subheader("分析粒度（不可混用）")
        grains = q("""
            SELECT 'User funnel' AS question, 'distinct user_id' AS grain_key,
                   'dws_user_campaign' AS main_tables
            UNION ALL SELECT 'Coupon / inventory', 'coupon_instance_id',
                   'fact_coupon_inventory'
            UNION ALL SELECT 'Orders / revenue', 'order_id', 'fact_redemption_order'
            UNION ALL SELECT 'Experiment lift', 'experiment_id × user_id',
                   'fact_experiment_assignment + dws_user_campaign'
            UNION ALL SELECT 'Supplier quality', 'order_id',
                   'fact_fulfillment + fact_redemption_order'
        """)
        st.dataframe(grains, use_container_width=True, hide_index=True)

        st.subheader("实时行数（当前库）")
        counts = q("""
            SELECT table_name, estimated_size AS rows
            FROM duckdb_tables()
            ORDER BY rows DESC
        """)
        st.dataframe(counts, use_container_width=True, hide_index=True)

    with tab_diag:
        st.subheader("用户漏斗（distinct user_id）")
        _mermaid(FUNNEL_MERMAID, height=480)
        st.caption(
            "库存漏斗（coupon_instance_id 粒度）在 fact_coupon_inventory："
            "purchased → assigned → claimed → redeemed。与用户漏斗分开讲。"
        )
        st.subheader("实体关系图（ER）")
        _mermaid(ER_MERMAID, height=520)

    with tab_tbl:
        layer_filter = st.multiselect(
            "筛选层级", ["DIM", "FACT", "DWS"], default=["DIM", "FACT", "DWS"]
        )
        catalog = [t for t in TABLE_CATALOG if t["layer"] in layer_filter]
        overview = []
        for t in catalog:
            try:
                n = int(q(f"SELECT COUNT(*) FROM {t['table']}").iloc[0, 0])
            except Exception:
                n = None
            overview.append(dict(
                Layer=t["layer"], Table=t["table"], Rows=n,
                Grain=t["grain"], PK=t["pk"], Description=t["desc"],
            ))
        st.dataframe(
            pd.DataFrame(overview),
            use_container_width=True, hide_index=True,
        )

        st.subheader("表结构 & 样例数据")
        table_names = [t["table"] for t in catalog]
        selected = st.selectbox("选择表", table_names, index=table_names.index("dws_user_campaign"))
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**`DESCRIBE {selected}`**")
            st.dataframe(q(f"DESCRIBE {selected}"), use_container_width=True, hide_index=True)
        with c2:
            st.markdown("**前 5 行**")
            st.dataframe(q(f"SELECT * FROM {selected} LIMIT 5"), use_container_width=True, hide_index=True)

    with tab_join:
        st.markdown("项目中常用的 JOIN 模式（白名单 SQL 与 `analysis_*.py` 一致）。")
        for title, sql in JOIN_PATTERNS:
            with st.expander(title, expanded=(title == "Experiment lift")):
                st.code(sql.strip(), language="sql")
                try:
                    st.dataframe(q(sql), use_container_width=True, hide_index=True)
                except Exception as e:
                    st.warning(f"执行失败: {e}")

    with tab_exp:
        st.warning(
            "**Assignment ≠ Intervention。** "
            "分组结果存在 `fact_experiment_assignment`；是否发消息记录在 `fact_touch`。"
        )
        cmp = q("""
            WITH touch_groups AS (
                SELECT group_name, COUNT(DISTINCT user_id) AS n_touch
                FROM fact_touch
                WHERE experiment_id IS NOT NULL
                GROUP BY 1
            ),
            assign_groups AS (
                SELECT group_name, COUNT(*) AS n_assign
                FROM fact_experiment_assignment
                GROUP BY 1
            )
            SELECT COALESCE(a.group_name, t.group_name) AS group_name,
                   a.n_assign, t.n_touch
            FROM assign_groups a
            FULL OUTER JOIN touch_groups t USING (group_name)
            ORDER BY 1
        """)
        st.markdown("**对照：assignment 表 vs touch 表（按组人数）**")
        st.dataframe(cmp, use_container_width=True, hide_index=True)
        st.markdown("""
| Group | Day 14 intervention | Row in `fact_touch` with `experiment_id`? |
|-------|---------------------|---------------------------------------------|
| Control | Nothing sent | **No** — still in assignment table |
| Reminder | Expiry reminder | Yes |
| Reminder+Reward | Reminder + ¥5 reward | Yes |

若只用 `fact_touch WHERE experiment_id IS NOT NULL` 认组别，**Control 会消失**（约 500 人），无法计算 Lift。
""")
        st.code("""-- 正确：实验分析
FROM fact_experiment_assignment e
JOIN dws_user_campaign d ON e.user_id = d.user_id

-- 错误：对照组不在触达表里
FROM fact_touch WHERE experiment_id = 'EXP001'  -- 缺 Control""", language="sql")

    if docs_path and docs_path.exists():
        with st.expander("查看完整 Markdown 文档"):
            st.markdown(docs_path.read_text(encoding="utf-8"))
