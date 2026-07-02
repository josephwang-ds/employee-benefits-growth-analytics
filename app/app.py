# -*- coding: utf-8 -*-
"""
御越企业福利增长分析 Demo — Streamlit Dashboard + 受控 ChatBI (v2.1)
运行: streamlit run app/app.py
v2.1: 新增指标字典页; Funnel 漏点自动诊断; ChatBI 升级为
      同义词检索打分 -> 白名单模板 -> 只读执行 -> 结果校验 -> 查询日志
"""
import sys
import os
import json
import duckdb
import numpy as np
import pandas as pd
import streamlit as st
from scipy import stats

sys.path.insert(0, os.path.dirname(__file__))
from metrics_dict import METRICS  # noqa: E402

DB = "data/yuyue.duckdb"
st.set_page_config(page_title="御越福利增长分析 Demo", layout="wide")


@st.cache_resource
def get_con():
    return duckdb.connect(DB, read_only=True)


def q(sql):
    return get_con().execute(sql).df()


PAGES = ["1 Executive Overview", "2 指标字典 (口径)", "3 Funnel 诊断", "4 Cohort 分析",
         "5 用户分层", "6 实验结果", "7 ROI Simulator", "8 采购与库存",
         "9 供应商履约", "10 ChatBI (受控)"]
page = st.sidebar.radio("页面", PAGES)
st.sidebar.caption("Demo 数据为按真实项目口径校准的模拟数据（企业/员工信息已脱敏）")

# ---------------------------------------------------------------- Page 1
if page == PAGES[0]:
    st.title("Executive Overview — 2026春季员工关怀福利活动")
    k = q("""
        SELECT COUNT(*) elig, SUM(issued_flag) iss, SUM(reached_flag) rch,
               SUM(visited_flag) vis, SUM(claimed_flag) clm,
               SUM(redeemed_14d_flag) rdm,
               SUM(CASE WHEN redeemed_14d_flag=1 AND fulfilled_flag=1 THEN 1 ELSE 0 END) ful,
               SUM(settlement_amount) settled, SUM(supplier_cost) sup_cost,
               SUM(reward_cost) rew_cost
        FROM dws_user_campaign""").iloc[0]
    budget = q("SELECT benefit_budget FROM dim_campaign").iloc[0, 0]
    c = st.columns(4)
    c[0].metric("Eligible Employees", f"{int(k.elig):,}",
                help="资格名单人数, 漏斗起点。来源: fact_campaign_eligibility")
    c[1].metric("发放成功率", f"{k.iss/k.elig:.1%}",
                help="成功发放用户/符合资格用户 (用户级 distinct)。失败原因: 账号异常/接口超时/名单缺失")
    c[2].metric("触达率", f"{k.rch/k.iss:.1%}",
                help="至少一条消息送达的用户/成功发放用户。发送成功≠送达, 打开≠访问")
    c[3].metric("累计访问率", f"{k.vis/k.iss:.1%}",
                help="进入活动页的用户/成功发放用户。触达→访问≈69%, 是最大掉点之一")
    c = st.columns(4)
    c[0].metric("累计领取率", f"{k.clm/k.iss:.1%}",
                help="成功领取用户/成功发放用户。领取≠兑换≠履约")
    c[1].metric("14天核销率", f"{k.rdm/k.iss:.1%}",
                help="14天内有效核销用户/成功发放用户。排除测试/重复/取消/退款/补单; 首次有效核销")
    c[2].metric("有效履约用户率", f"{k.ful/k.iss:.1%}",
                help="北极星: 核销且履约成功/成功发放。核销了没拿到权益等于没发")
    c[3].metric("预算使用率", f"{k.settled/budget:.1%}",
                help="已结算金额/批准预算。客户续约判断的核心之一")
    c = st.columns(3)
    c[0].metric("企业结算收入", f"¥{k.settled:,.0f}",
                help="有效核销订单 settlement_amount 合计")
    c[1].metric("采购成本(已消耗)", f"¥{k.sup_cost:,.0f}",
                help="v2口径: 已核销卡券对应的采购成本")
    c[2].metric("贡献毛利", f"¥{k.settled - k.sup_cost - k.rew_cost:,.0f}",
                help="收入-已消耗采购成本-奖励。完整口径还需减过期损失和消息成本, 见指标字典")
    st.info("Executive Summary: 发放与触达正常; 主要流失在 触达→访问 与 领取→核销; "
            "到期提醒实验显示 +6pp 增量, 额外奖励边际价值有限, 建议扩大分层提醒而非全量补贴。")
    st.caption("每个卡片右上角 ? 号有完整口径; 更详细的分子/分母/排除规则见「2 指标字典」页")

# ---------------------------------------------------------------- Page 2 指标字典
elif page == PAGES[1]:
    st.title("指标字典 — 统一口径的唯一来源")
    st.caption("背景: 报表不一致的根因通常是口径不统一, 所以第一步是建指标字典+标准SQL。"
               "本页每个指标含定义/分子/分母/粒度/排除规则/来源表/业务意义, 带实时值的直接从库里算。")
    cats = ["全部"] + sorted(set(m["类别"] for m in METRICS),
                             key=["北极星", "Funnel", "实验", "财务", "库存", "护栏", "长期"].index)
    c1, c2 = st.columns([1, 2])
    cat = c1.selectbox("类别", cats)
    kw = c2.text_input("搜索指标名/定义关键词", "")
    shown = [m for m in METRICS
             if (cat == "全部" or m["类别"] == cat)
             and (not kw or kw in m["指标"] or kw in m["定义"] or kw in m["业务意义"])]
    # 汇总表
    st.dataframe(pd.DataFrame([{k: m[k] for k in ["类别", "指标", "定义", "粒度"]} for m in shown]),
                 use_container_width=True, hide_index=True)
    st.subheader("逐项口径卡片")
    for m in shown:
        live = ""
        if m.get("live_sql"):
            try:
                v = q(m["live_sql"]).iloc[0, 0]
                live = f"  ·  当前值: **{v}**"
            except Exception:
                live = ""
        with st.expander(f"[{m['类别']}] {m['指标']}{live.replace('*','')}"):
            st.markdown(f"""
| 项 | 内容 |
|---|---|
| 定义 | {m['定义']} |
| 分子 | {m['分子']} |
| 分母 | {m['分母']} |
| 粒度 | {m['粒度']} |
| 排除/规则 | {m['排除规则']} |
| 来源表 | `{m['来源表']}` |
| 业务意义 | {m['业务意义']} |
""")
            if m.get("live_sql"):
                st.code(m["live_sql"].strip(), language="sql")

# ---------------------------------------------------------------- Page 3 Funnel
elif page == PAGES[2]:
    st.title("Funnel 诊断")
    dim = st.selectbox("拆解维度", ["总体", "department_group", "benefit_type",
                                    "activity_segment", "region"])
    where = "1=1"
    if dim != "总体":
        val = st.selectbox("取值", q(f"SELECT DISTINCT {dim} FROM dws_user_campaign").iloc[:, 0])
        where = f"{dim} = '{val}'"
    b = q(f"""SELECT COUNT(*) eligible, SUM(issued_flag) issued, SUM(reached_flag) reached,
              SUM(visited_flag) visited, SUM(claimed_flag) claimed,
              SUM(redeemed_14d_flag) redeemed_14d,
              SUM(CASE WHEN redeemed_14d_flag=1 AND fulfilled_flag=1 THEN 1 ELSE 0 END) fulfilled
              FROM dws_user_campaign WHERE {where}""").iloc[0]
    stages = ["eligible", "issued", "reached", "visited", "claimed", "redeemed_14d", "fulfilled"]
    mode = st.radio("口径", ["Stage Conversion", "Cumulative (vs issued)"], horizontal=True)
    rows = []
    for i, s in enumerate(stages):
        n = int(b[s])
        conv = (n / b[stages[i-1]] if i else 1) if mode == "Stage Conversion" else \
               (n / b["issued"] if b["issued"] else 0)
        rows.append(dict(stage=s, users=n, conversion=round(conv * 100, 1),
                         loss=int(b[stages[i-1]] - n) if i else 0))
    fdf = pd.DataFrame(rows)
    st.bar_chart(fdf.set_index("stage")["users"])
    st.dataframe(fdf, use_container_width=True, hide_index=True)

    # --- 漏点自动诊断 (参考 growth-funnel-agent 的 biggest-leak 思路) ---
    st.subheader("漏点自动诊断")
    BENCH = {  # 各阶段的"健康线"与建议动作 
        "issued":       (97.0, "名单错误/账号异常/接口失败", "清洗名单、失败重试、失败原因分布"),
        "reached":      (90.0, "联系方式无效、渠道限制", "更换渠道、联系方式更新"),
        "visited":      (65.0, "文案弱、入口不清晰、发送时间差", "消息模板/发送时间 A/B 实验"),
        "claimed":      (70.0, "权益不匹配、页面复杂", "权益推荐、页面简化"),
        "redeemed_14d": (60.0, "遗忘、有效期不清楚", "到期提醒实验(本项目结果: +6.2pp)"),
        "fulfilled":    (95.0, "供应商接口/库存问题", "供应商 SLA、容量护栏"),
    }
    diag = []
    for i in range(1, len(stages)):
        s = stages[i]
        stage_conv = b[s] / b[stages[i-1]] * 100 if b[stages[i-1]] else 0
        bench, cause, action = BENCH[s]
        gap = stage_conv - bench
        diag.append(dict(阶段=f"{stages[i-1]} → {s}", 阶段转化=round(stage_conv, 1),
                         健康线=bench, 差距pp=round(gap, 1),
                         流失人数=int(b[stages[i-1]] - b[s]),
                         可能原因=cause, 建议动作=action))
    ddf = pd.DataFrame(diag).sort_values("差距pp")
    st.dataframe(ddf, use_container_width=True, hide_index=True)
    worst = ddf.iloc[0]
    st.warning(f"最大漏点: **{worst['阶段']}**（阶段转化 {worst['阶段转化']}% vs 健康线 "
               f"{worst['健康线']}%, 流失 {worst['流失人数']:,} 人）→ {worst['建议动作']}")
    st.caption("健康线为项目内部基准(历史批次/相似活动), 不是行业标准。"
               "诊断给的是'去哪找原因', 因果结论仍要实验验证。")

    # --- 条件转化率速览 ---
    st.subheader("条件转化率 (剥离上游影响)")
    cc = st.columns(3)
    cc[0].metric("触达 → 访问", f"{b.visited/b.reached:.1%}",
                 help="消息模板A/B的主指标, 剥离通道问题")
    cc[1].metric("访问 → 领取", f"{b.claimed/b.visited:.1%}",
                 help="权益匹配度与页面体验")
    cc[2].metric("领取 → 核销", f"{b.redeemed_14d/b.claimed:.1%}",
                 help="'领了忘用'的量化, 到期提醒实验的动机")

# ---------------------------------------------------------------- Page 4 Cohort
elif page == PAGES[3]:
    st.title("Cohort 分析")
    st.subheader("历史活跃度 Cohort")
    st.dataframe(q("""
        SELECT activity_segment, COUNT(*) users,
               ROUND(SUM(visited_flag)*100.0/SUM(issued_flag),1) visit_pct,
               ROUND(SUM(claimed_flag)*100.0/SUM(issued_flag),1) claim_pct,
               ROUND(SUM(redeemed_14d_flag)*100.0/SUM(issued_flag),1) redeem14_pct
        FROM dws_user_campaign WHERE issued_flag=1 GROUP BY 1
        ORDER BY CASE activity_segment WHEN 'New' THEN 1 WHEN 'Low' THEN 2
                 WHEN 'Medium' THEN 3 ELSE 4 END"""),
        use_container_width=True, hide_index=True)
    st.subheader("核销速度 (D1/D3/D7/D14 占最终核销者比例)")
    st.dataframe(q("""
        WITH r AS (SELECT d.activity_segment,
                   DATE_DIFF('hour', c.start_time, o.redemption_time)/24.0 rday
                   FROM dws_user_campaign d
                   JOIN fact_redemption_order o ON d.user_id=o.user_id
                        AND o.redemption_status='success'
                   CROSS JOIN dim_campaign c WHERE d.redeemed_14d_flag=1)
        SELECT activity_segment, COUNT(*) redeemers,
               ROUND(SUM(CASE WHEN rday<=1 THEN 1 ELSE 0 END)*100.0/COUNT(*),1) d1,
               ROUND(SUM(CASE WHEN rday<=3 THEN 1 ELSE 0 END)*100.0/COUNT(*),1) d3,
               ROUND(SUM(CASE WHEN rday<=7 THEN 1 ELSE 0 END)*100.0/COUNT(*),1) d7,
               ROUND(SUM(CASE WHEN rday<=14 THEN 1 ELSE 0 END)*100.0/COUNT(*),1) d14
        FROM r GROUP BY 1"""), use_container_width=True, hide_index=True)
    st.caption("注意: Cohort 描述差异, 不做因果判断; 因果结论以实验为准。"
               "High 分群本次核销率最高=Sure Thing 证据, 是 Uplift 建模动机。")

# ---------------------------------------------------------------- Page 6 实验
# ---------------------------------------------------------------- Page 5 用户分层
elif page == PAGES[4]:
    st.title("用户分层 — 规则层 (行为状态 → 运营动作)")
    st.caption("两层体系: 静态层(历史活跃度 New/Low/Medium/High)回答'这是什么人', 用于分层随机与 Cohort; "
               "规则层(本页)回答'现在对他做什么', 每层直接映射一个运营动作。分层时点=Day14, 即实验分组的输入。")
    SEG_SQL = """
    SELECT *,
      CASE
        WHEN (redeemed_14d_flag=1 OR exp_redeemed_flag=1)
             AND (fulfilled_flag=0 OR complaint_flag=1)      THEN '6_Fulfillment Risk'
        WHEN redeemed_14d_flag=1 AND first_visit_day<=3      THEN '1_Natural Active'
        WHEN redeemed_14d_flag=1                             THEN '2_Standard Redeemer'
        WHEN visited_flag=1                                  THEN '3_Visited Not Redeemed'
        WHEN reached_flag=1 AND historical_campaign_count>=2
             AND historical_redemption_count=0               THEN '5_Dormant'
        WHEN reached_flag=1                                  THEN '4_Reached Not Visited'
        WHEN issued_flag=1                                   THEN '7_Not Reached'
        ELSE '8_Issue Failed'
      END AS rule_segment
    FROM dws_user_campaign"""
    ACTIONS = {
        "1_Natural Active":       ("不补贴, 普通提醒", "补贴他们 = 补贴 Sure Thing"),
        "2_Standard Redeemer":    ("维持现状", "已完成转化, 关注下一活动参与"),
        "3_Visited Not Redeemed": ("到期提醒实验(已验证 +6.2pp)", "意愿明确, 增量潜力最大"),
        "4_Reached Not Visited":  ("消息模板/渠道/时间 A/B", "问题在内容不在通道"),
        "5_Dormant":              ("降低打扰, 测权益偏好", "反复推送有退订风险"),
        "6_Fulfillment Risk":     ("优先修复履约+补偿", "先修体验再谈运营"),
        "7_Not Reached":          ("更新联系方式/换渠道", "先修数据"),
        "8_Issue Failed":         ("失败原因分类+重试", "名单/账号/接口问题"),
    }
    seg = q(f"""WITH s AS ({SEG_SQL})
        SELECT rule_segment, COUNT(*) users,
               ROUND(AVG(historical_redemption_count),2) hist_redeem_avg
        FROM s GROUP BY 1 ORDER BY 1""")
    seg["建议动作"] = seg.rule_segment.map(lambda s: ACTIONS[s][0])
    seg["理由"] = seg.rule_segment.map(lambda s: ACTIONS[s][1])
    st.bar_chart(seg.set_index("rule_segment")["users"])
    st.dataframe(seg, use_container_width=True, hide_index=True)
    st.subheader("规则分层 × 历史活跃度 交叉表")
    cross = q(f"""WITH s AS ({SEG_SQL})
        SELECT rule_segment, activity_segment, COUNT(*) n FROM s GROUP BY 1,2""")
    st.dataframe(cross.pivot(index="rule_segment", columns="activity_segment",
                             values="n").fillna(0)[["New","Low","Medium","High"]].astype(int),
                 use_container_width=True)
    st.info("Visited Not Redeemed(~1,600人) 就是三组实验的人群来源(再叠加未过期/无投诉等资格条件取~1,500)。"
            "Reward Sensitive 层需要历史奖励活动数据标注, 实验 Reward 组的响应是第一批标注来源, 属后续迭代。")

elif page == PAGES[5]:
    st.title("实验结果 — 到期提醒 A/B/C")
    df = q("""SELECT e.group_name, e.stratification_key segment, d.exp_redeemed_flag y
              FROM fact_experiment_assignment e
              JOIN dws_user_campaign d ON e.user_id=d.user_id""")
    g = df.groupby("group_name").y.agg(n="size", redeemers="sum", rate="mean").reset_index()
    g["rate"] = (g.rate * 100).round(1)
    c = st.columns(3)
    for i, row in g.iterrows():
        c[i].metric(row.group_name, f"{row.rate}%", f"n={row.n}",
                    help="实验期14天核销率(Day14-28窗口)。分母=该组有效实验用户")
    chi2, p_srm = stats.chisquare(g.n, np.full(3, g.n.sum() / 3))
    st.write(f"**SRM 检查**: chi2={chi2:.2f}, p={p_srm:.3f} -> {'通过' if p_srm > 0.05 else '失衡!'}")

    def ztest(a, b):
        xa, na = df[df.group_name == a].y.sum(), (df.group_name == a).sum()
        xb, nb = df[df.group_name == b].y.sum(), (df.group_name == b).sum()
        pa, pb = xa / na, xb / nb
        pp = (xa + xb) / (na + nb)
        z = (pa - pb) / np.sqrt(pp * (1 - pp) * (1 / na + 1 / nb))
        pv = 2 * (1 - stats.norm.cdf(abs(z)))
        se = np.sqrt(pa * (1 - pa) / na + pb * (1 - pb) / nb)
        return pa - pb, pv, (pa - pb - 1.96 * se, pa - pb + 1.96 * se)

    for a, b in [("Reminder", "Control"), ("Reminder+Reward", "Reminder")]:
        lift, pv, ci = ztest(a, b)
        sig = "显著 ✅" if pv < 0.05 else "不显著 ⚠️"
        st.write(f"**{a} vs {b}**: 绝对Lift = {lift*100:+.1f}pp, "
                 f"p = {pv:.3f}, 95% CI = [{ci[0]*100:+.1f}, {ci[1]*100:+.1f}]pp -> {sig}")
    st.caption("设计口径: 500/组、基线28.6%、α=0.05、Power 80% → MDE≈8pp。"
               "Reward 的 +2.3pp 远小于 MDE, 单次实验本就测不出——这是不全量推 Reward 的统计依据之一。")
    st.subheader("分群 Lift (Reminder vs Control)")
    het = []
    for s in ["New", "Low", "Medium", "High"]:
        sub = df[df.segment == s]
        cc_ = sub[sub.group_name == "Control"].y.mean()
        tt = sub[sub.group_name == "Reminder"].y.mean()
        het.append(dict(segment=s, control=round(cc_ * 100, 1), reminder=round(tt * 100, 1),
                        lift_pp=round((tt - cc_) * 100, 1)))
    st.dataframe(pd.DataFrame(het), use_container_width=True, hide_index=True)
    st.info("结论: Reminder 显著提升; Reward 边际增量小且不显著 -> 扩大分层提醒, 不做全量补贴。"
            "High 分群零增量 = Sure Thing, 是 Uplift 建模的直接动机。")

# ---------------------------------------------------------------- Page 6 ROI
elif page == PAGES[6]:
    st.title("ROI Simulator")
    c1, c2 = st.columns(2)
    n_target = c1.number_input("目标触达人数", 100, 5000, 1500, 100)
    rev = c1.number_input("单次核销企业结算收入(元)", 10.0, 200.0, 85.0)
    sup = c1.number_input("单次采购成本(元)", 5.0, 180.0, 73.0)
    msg = c1.number_input("单条消息成本(元)", 0.01, 1.0, 0.05)
    lift = c2.slider("预计绝对Lift(pp)", 0.0, 15.0, 6.2, 0.1) / 100
    reward = c2.number_input("单人奖励金额(元, 核销后发放)", 0.0, 50.0, 0.0)
    base_rate = c2.slider("基线核销率(%)", 5.0, 60.0, 28.6) / 100
    margin = rev - sup
    inc_users = n_target * lift
    redeemers = n_target * (base_rate + lift)
    cost = n_target * msg + redeemers * reward
    inc_value = inc_users * margin
    net = inc_value - cost
    c = st.columns(4)
    c[0].metric("增量核销人数", f"{inc_users:.0f}", help="目标人数 × 预计Lift")
    c[1].metric("增量收入(毛差)", f"¥{inc_value:,.0f}", help="增量人数 × (结算收入-采购成本)")
    c[2].metric("总成本", f"¥{cost:,.0f}", help="消息成本 + 核销后奖励成本")
    c[3].metric("净增量贡献", f"¥{net:,.0f}", delta=f"ROI={net/cost:.2f}" if cost else "",
                help="增量收入 - 总成本; 为负说明干预不划算")
    be = inc_value / max(redeemers, 1)
    st.write(f"**Break-even Reward** ≈ ¥{be:.2f}/人 (超过则奖励不划算)")
    st.caption("口径: 增量贡献 = 增量核销 × (结算收入 - 采购成本) - 消息成本 - 奖励成本。"
               "试一试: Lift 拉到多大时 5 元奖励才划算? (≈15pp, 不现实 → 结论稳健)")

# ---------------------------------------------------------------- Page 7 库存
elif page == PAGES[7]:
    st.title("卡券采购与库存 (v2)")
    AS_OF = "2026-04-05"
    b = q("""SELECT COUNT(*) purchased,
             SUM(CASE WHEN coupon_status != 'available' THEN 1 ELSE 0 END) assigned,
             SUM(CASE WHEN coupon_status IN ('claimed','redeemed') THEN 1 ELSE 0 END) claimed,
             SUM(CASE WHEN coupon_status = 'redeemed' THEN 1 ELSE 0 END) redeemed
             FROM fact_coupon_inventory""").iloc[0]
    c = st.columns(4)
    c[0].metric("采购卡券", f"{int(b.purchased):,}", help="Σ purchase_quantity, 含~11%缓冲")
    c[1].metric("分配率", f"{b.assigned/b.purchased:.1%}", help="已分配/已采购 (卡券级)")
    c[2].metric("卡券领取率(/已分配)", f"{b.claimed/b.assigned:.1%}",
                help="注意分母是已分配卡券, 与用户级口径(47.6%)不同")
    c[3].metric("卡券核销率(/已分配)", f"{b.redeemed/b.assigned:.1%}",
                help="券级消化效率; HR报表差异的根源就是券级vs人级")
    st.caption("卡券库存 Funnel 用 coupon_instance_id 粒度; 用户 Funnel 用 distinct user_id, 两条线分开")
    st.subheader("库存老化与不可退风险")
    st.dataframe(q(f"""
        SELECT i.benefit_id, d.benefit_category, d.refundable_flag,
               COUNT(*) FILTER (WHERE i.coupon_status IN ('available','assigned','claimed'))
                   un_redeemed,
               COUNT(*) FILTER (WHERE i.coupon_status IN ('available','assigned','claimed')
                   AND i.expiry_time <= TIMESTAMP '{AS_OF}' + INTERVAL 14 DAY) expiring_14d,
               ROUND(SUM(CASE WHEN i.coupon_status IN ('available','assigned','claimed')
                   AND NOT d.refundable_flag
                   AND i.expiry_time <= TIMESTAMP '{AS_OF}' + INTERVAL 30 DAY
                   THEN d.standard_purchase_cost ELSE 0 END),0) at_risk_cost
        FROM fact_coupon_inventory i JOIN dim_benefit d ON i.benefit_id = d.benefit_id
        GROUP BY 1,2,3 ORDER BY at_risk_cost DESC"""),
        use_container_width=True, hide_index=True)
    st.warning("行动: 临期卡券优先用于高Lift人群提醒或转入后续活动; "
               "不可退短效期权益(电影/健康)压缩采购上限 。")

# ---------------------------------------------------------------- Page 8 履约
elif page == PAGES[8]:
    st.title("供应商履约监控")
    st.dataframe(q("""
        SELECT f.supplier_id, d.benefit_type,
               COUNT(*) orders,
               ROUND(SUM(CASE WHEN f.fulfillment_status='success' THEN 1 ELSE 0 END)*100.0
                     /COUNT(*),1) fulfill_pct,
               ROUND(SUM(f.refund_flag)*100.0/COUNT(*),1) refund_pct,
               ROUND(SUM(f.complaint_flag)*100.0/COUNT(*),1) complaint_pct
        FROM fact_fulfillment f
        JOIN fact_redemption_order o ON f.order_id=o.order_id
        JOIN dws_user_campaign d ON o.user_id=d.user_id
        GROUP BY 1,2 ORDER BY fulfill_pct"""),
        use_container_width=True, hide_index=True)
    st.dataframe(q("""
        SELECT failure_reason, COUNT(*) cnt FROM fact_fulfillment
        WHERE fulfillment_status='failed' GROUP BY 1 ORDER BY 2 DESC"""),
        use_container_width=True, hide_index=True)
    st.warning("SUP04(电影) 履约率最低, 主因库存不足 -> 活动前确认供应商容量, 设履约护栏。")

# ---------------------------------------------------------------- Page 9 ChatBI
elif page == PAGES[9]:
    st.title("ChatBI (DeepSeek + 受控SQL)")
    st.caption("管线: DeepSeek 解析业务问题 → 匹配指标字典/白名单模板 → 只读SQL → 结果校验 → 业务解释 → 查询日志。"
               "LLM 只负责选模板, 不生成自由 SQL; 高频查询和无 API Key 场景自动回退到同义词检索。")

    # ---- 同义词字典 (参考 chatbi/ragbi.py 的 metric dictionary 思路) ----
    SYNONYMS = {
        "流失": ["funnel", "掉点", "漏斗", "转化"], "漏斗": ["funnel", "流失", "转化"],
        "触达": ["reach", "送达", "消息", "通知"], "访问": ["visit", "页面", "进入"],
        "领取": ["claim"], "核销": ["redeem", "兑换", "使用"],
        "履约": ["fulfill", "供应商", "交付"], "供应商": ["supplier", "履约"],
        "失败": ["failed", "异常"], "退款": ["refund"], "投诉": ["complaint"],
        "部门": ["department"], "渠道": ["channel"], "地区": ["region"],
        "实验": ["experiment", "ab", "测试"], "提醒": ["reminder", "实验"],
        "奖励": ["reward", "补贴"], "对照": ["control"], "提升": ["lift", "增量"],
        "显著": ["pvalue", "置信"], "置信区间": ["ci", "置信"], "srm": ["srm", "样本", "比例"],
        "预算": ["budget", "结算"], "收入": ["revenue", "结算", "毛利"],
        "毛利": ["margin", "收入", "成本"], "库存": ["inventory", "卡券", "采购"],
        "临期": ["expiring", "过期", "老化"], "过期": ["expiring", "临期"],
        "采购": ["purchase", "库存", "批次"], "分层": ["segment", "分群", "活跃"],
        "活跃": ["segment", "分群"], "权益": ["benefit", "卡券"],
    }
    TEMPLATES = [
        dict(name="漏斗各步用户数(哪一步流失最大)",
             tags=["漏斗", "流失", "掉点", "funnel", "转化", "哪一步", "各步"],
             sql="""SELECT SUM(issued_flag) issued, SUM(reached_flag) reached,
                    SUM(visited_flag) visited, SUM(claimed_flag) claimed,
                    SUM(redeemed_14d_flag) redeemed
                    FROM dws_user_campaign WHERE enterprise_id='ENT001'""",
             explain="按用户级口径, 最大流失在 触达→访问 与 领取→核销 两段; 因果原因需实验验证。"),
        dict(name="按部门访问率排名",
             tags=["部门", "访问", "排名", "最低", "department"],
             sql="""SELECT department_group,
                    ROUND(SUM(visited_flag)*100.0/SUM(issued_flag),1) visit_pct
                    FROM dws_user_campaign WHERE enterprise_id='ENT001'
                    GROUP BY 1 ORDER BY visit_pct""",
             explain="仅描述差异, 不解释原因; 部门差异可能与设备/年龄结构/权益偏好相关, 需进一步实验。"),
        dict(name="按渠道触达/打开情况",
             tags=["渠道", "触达", "打开", "channel", "短信", "企业微信"],
             sql="""SELECT channel, COUNT(DISTINCT user_id) reached_users,
                    ROUND(SUM(CASE WHEN open_time IS NOT NULL THEN 1 ELSE 0 END)*100.0
                          /COUNT(*),1) open_pct
                    FROM fact_touch WHERE delivery_status='delivered'
                    GROUP BY 1 ORDER BY open_pct DESC""",
             explain="渠道对比只在'首次通知'语义下可比; 打开≠访问, 访问以页面事件为准。"),
        dict(name="实验三组核销率",
             tags=["实验", "提醒", "reminder", "对照", "control", "提升", "组"],
             sql="""SELECT e.group_name, COUNT(*) n,
                    ROUND(AVG(d.exp_redeemed_flag)*100,1) redeem_pct
                    FROM fact_experiment_assignment e
                    JOIN dws_user_campaign d ON e.user_id=d.user_id
                    GROUP BY 1 ORDER BY 1""",
             explain="Reminder vs Control 差值即绝对Lift(+6.5pp, p≈0.03显著); "
                     "Reward 额外+2.3pp 不显著。显著性与CI详见实验页。"),
        dict(name="实验SRM检查(三组样本量)",
             tags=["srm", "样本", "比例", "分组", "1:1:1"],
             sql="""SELECT group_name, COUNT(*) n FROM fact_experiment_assignment
                    GROUP BY 1 ORDER BY 1""",
             explain="三组接近1:1:1; 正式判定用卡方拟合优度检验(实验页 p=0.99, 通过)。"),
        dict(name="供应商履约失败率排名",
             tags=["供应商", "履约", "失败", "supplier", "最高"],
             sql="""SELECT supplier_id,
                    ROUND(SUM(CASE WHEN fulfillment_status='failed' THEN 1 ELSE 0 END)*100.0
                    /COUNT(*),1) fail_pct, COUNT(*) orders
                    FROM fact_fulfillment GROUP BY 1 ORDER BY fail_pct DESC""",
             explain="供应商失败侵蚀北极星(有效履约用户率); SUP04 主因库存不足, 建议容量护栏。"),
        dict(name="退款集中在哪种权益",
             tags=["退款", "权益", "refund", "集中"],
             sql="""SELECT d.benefit_type, COUNT(*) orders,
                    ROUND(SUM(f.refund_flag)*100.0/COUNT(*),1) refund_pct
                    FROM fact_fulfillment f
                    JOIN fact_redemption_order o ON f.order_id=o.order_id
                    JOIN dws_user_campaign d ON o.user_id=d.user_id
                    GROUP BY 1 ORDER BY refund_pct DESC""",
             explain="退款率与履约失败率联看; 高退款权益优先查供应商而非用户。"),
        dict(name="领取后未核销人数(实验目标人群)",
             tags=["领取", "未核销", "人数", "目标人群"],
             sql="""SELECT COUNT(*) cnt FROM dws_user_campaign
                    WHERE claimed_flag=1 AND redeemed_14d_flag=0""",
             explain="到期提醒实验目标人群来源之一(还需叠加'已访问''未过期''无投诉'等资格条件)。"),
        dict(name="临期未核销库存(30天)",
             tags=["库存", "临期", "过期", "老化", "卡券", "风险"],
             sql="""SELECT i.benefit_id, d.benefit_category, d.refundable_flag,
                    COUNT(*) FILTER (WHERE i.coupon_status IN ('available','assigned','claimed')
                        AND i.expiry_time <= TIMESTAMP '2026-04-05' + INTERVAL 30 DAY) expiring_30d
                    FROM fact_coupon_inventory i JOIN dim_benefit d USING(benefit_id)
                    GROUP BY 1,2,3 ORDER BY expiring_30d DESC""",
             explain="不可退临期库存是唯一直接变损失的部分; 行动: 优先用于高Lift人群或转入后续活动。"),
        dict(name="预算使用率",
             tags=["预算", "结算", "使用率", "budget"],
             sql="""SELECT ROUND(SUM(o.settlement_amount)*100.0/MAX(c.benefit_budget),1) budget_pct
                    FROM fact_redemption_order o CROSS JOIN dim_campaign c
                    WHERE o.redemption_status='success'""",
             explain="已结算金额/批准预算=68.5%; 过低影响客户续约判断。"),
        dict(name="分群(活跃度)核销率对比",
             tags=["分层", "分群", "活跃", "segment", "对比"],
             sql="""SELECT activity_segment, COUNT(*) users,
                    ROUND(SUM(redeemed_14d_flag)*100.0/SUM(issued_flag),1) redeem_pct
                    FROM dws_user_campaign WHERE issued_flag=1 GROUP BY 1 ORDER BY redeem_pct""",
             explain="High 分群本来就高=Sure Thing; 干预价值要看增量(Uplift), 不是基线高低。"),
    ]
    BLOCKED = [
        ("手机号|身份证|姓名|电话", "涉及个人敏感字段, 字段白名单外, 拒绝返回。"),
        ("其他企业|别的公司|跨企业", "跨企业数据被 enterprise_id 强制隔离, 拒绝返回。"),
        ("为什么|原因是什么", "因果类问题不由 ChatBI 直接回答——描述性指标可查, 原因请看实验模块或做新实验。"),
    ]

    def tokenize(text):
        toks = set()
        text = text.lower()
        for w, syns in SYNONYMS.items():
            if w in text:
                toks.add(w); toks.update(syns)
        for t in ["funnel", "srm", "control", "reminder", "reward", "lift", "roi"]:
            if t in text:
                toks.add(t)
        return toks

    def retrieve(question, top_n=3):
        qt = tokenize(question)
        scored = []
        for t in TEMPLATES:
            score = len(qt & set(x.lower() for x in t["tags"]))
            score += sum(2 for tag in t["tags"] if tag in question)  # 原文命中加权
            if score > 0:
                scored.append((score, t))
        scored.sort(key=lambda x: -x[0])
        return scored[:top_n]

    def secret_value(name, default=None):
        try:
            return st.secrets.get(name, os.environ.get(name, default))
        except Exception:
            return os.environ.get(name, default)

    def deepseek_select_template(question):
        api_key = secret_value("DEEPSEEK_API_KEY")
        if not api_key:
            return None, "未配置 DEEPSEEK_API_KEY"

        try:
            from openai import OpenAI
        except Exception:
            return None, "未安装 openai 依赖"

        template_brief = [
            dict(id=i, name=t["name"], tags=t["tags"], explanation=t["explain"])
            for i, t in enumerate(TEMPLATES)
        ]
        system_prompt = (
            "你是企业福利增长分析的 ChatBI 路由器。"
            "你的任务不是写 SQL, 而是把用户问题匹配到一个已审核的白名单模板。"
            "如果问题涉及个人敏感信息、跨企业数据、或因果为什么类问题, 返回 blocked=true。"
            "如果没有合适模板, 返回 template_id=null。"
            "只输出 JSON, 格式: "
            "{\"blocked\": false, \"template_id\": 0, \"confidence\": 0.0, \"reason\": \"简短中文原因\"}"
        )
        client = OpenAI(
            api_key=api_key,
            base_url=secret_value("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        )
        response = client.chat.completions.create(
            model=secret_value("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps({
                    "question": question,
                    "templates": template_brief,
                }, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            stream=False,
        )
        raw = response.choices[0].message.content or "{}"
        decision = json.loads(raw)
        return decision, "DeepSeek"

    if "chatbi_log" not in st.session_state:
        st.session_state.chatbi_log = []

    llm_ready = bool(secret_value("DEEPSEEK_API_KEY"))
    st.info("DeepSeek 路由: 已启用" if llm_ready else
            "DeepSeek 路由: 未配置 DEEPSEEK_API_KEY, 当前使用本地同义词检索 fallback")
    st.write("试试: `哪一步流失最大` / `reminder 提升多少` / `哪个供应商失败率最高` / "
             "`临期库存有多少` / `按部门看访问率` / `为什么核销率低`(演示拒答)")
    question = st.text_input("输入业务问题")
    if question:
        import re as _re
        blocked = next(((pat, msg) for pat, msg in BLOCKED if _re.search(pat, question)), None)
        if blocked:
            st.error(f"ChatBI 拒绝回答: {blocked[1]}")
            st.session_state.chatbi_log.append(dict(问题=question, 状态="拒答", 模板="—"))
        else:
            cands = []
            llm_note = None
            if llm_ready:
                try:
                    decision, source = deepseek_select_template(question)
                    llm_note = decision if decision else source
                    if decision and decision.get("blocked"):
                        st.error("ChatBI 拒绝回答: " + decision.get("reason", "问题不在安全范围内。"))
                        st.session_state.chatbi_log.append(dict(问题=question, 状态="拒答", 模板="—"))
                        st.stop()
                    template_id = decision.get("template_id") if decision else None
                    confidence = float(decision.get("confidence", 0)) if decision else 0
                    if isinstance(template_id, int) and 0 <= template_id < len(TEMPLATES) and confidence >= 0.35:
                        cands = [(round(confidence * 10, 1), TEMPLATES[template_id])]
                except Exception as e:
                    llm_note = f"DeepSeek 调用失败, 已回退本地检索: {e}"
            if not cands:
                cands = retrieve(question)
            if not cands:
                st.warning("未匹配到白名单指标模板。受控 ChatBI 只回答指标字典内的问题——这是有意设计: "
                           "防止生成未经口径校验的 SQL。可换个说法, 或从上面示例问题开始。")
                st.session_state.chatbi_log.append(dict(问题=question, 状态="未命中", 模板="—"))
            else:
                # 检索透明化: 展示候选模板与得分 (参考 chatbi 的 retrieval 可视化)
                with st.expander("Step 1 — 意图检索 (候选模板与相关度)", expanded=False):
                    if llm_note:
                        st.write(llm_note)
                    st.table(pd.DataFrame([dict(模板=t["name"], 相关度=s) for s, t in cands]))
                score, t = cands[0]
                st.markdown(f"**命中模板**: {t['name']}")
                st.code(t["sql"].strip(), language="sql")
                try:
                    res = q(t["sql"])
                    # 结果校验: 非空 / 非全NULL
                    if res.empty:
                        st.warning("结果校验: 返回为空, 请检查口径或时间范围。")
                        status = "空结果"
                    elif res.isna().all().all():
                        st.warning("结果校验: 全为 NULL, 疑似口径问题, 不做业务解释。")
                        status = "校验失败"
                    else:
                        st.dataframe(res, use_container_width=True, hide_index=True)
                        st.success("业务解释: " + t["explain"])
                        status = "成功"
                except Exception as e:
                    st.error(f"执行失败: {e}")
                    status = "执行失败"
                st.session_state.chatbi_log.append(
                    dict(问题=question, 状态=status, 模板=t["name"]))
    if st.session_state.chatbi_log:
        with st.expander(f"查询日志 ({len(st.session_state.chatbi_log)} 条)"):
            st.dataframe(pd.DataFrame(st.session_state.chatbi_log),
                         use_container_width=True, hide_index=True)
    st.caption("权限规则: 企业隔离(enterprise_id 强制过滤) / 只读 / 字段白名单(无PII) / "
               "限流 / 结果与标准KPI表校验 / SQL日志。完整 LLM 版见我的 ChatBI 项目"
               "(RAG检索 + sqlglot AST 安全门 + DeepSeek 生成)。")
