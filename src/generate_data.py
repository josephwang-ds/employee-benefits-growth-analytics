# -*- coding: utf-8 -*-
"""
Benefits Growth Analytics Demo — 合成数据生成器
=============================================
生成 11 张核心表（ODS/DIM/FACT）+ 1 张 dws 用户活动宽表，写入 DuckDB。

口径校准目标（复现某企业福利活动的真实指标结构, 已脱敏）:
  发放成功率 98.4% | 触达率 92.1% | 累计访问率 63.8% | 累计领取率 47.6%
  14 天核销率 31.2% | 履约成功率 96.5% | 预算使用率 68.4%
  实验: ~1,500 人 3 组 | Control 28.6% | Reminder 34.8% | Reminder+Reward 37.1%

时间线设定:
  Day 0        活动开始, 卡券发放 + 首次通知
  Day 0-14     基线观察窗口（14 天核销率的口径窗口）
  Day 14       实验分组: 已访问未核销 & 卡券未过期 & 无投诉
  Day 14-28    实验观察窗口（实验的 14 天核销率）
  Day 45       卡券过期

用法: python src/generate_data.py [--seed 42] [--db data/benefits.duckdb]
"""
import argparse
import numpy as np
import pandas as pd
import duckdb
from datetime import datetime, timedelta

BASE = datetime(2026, 3, 2, 9, 0, 0)  # Day 0: 活动开始
EXPIRY_DAY = 45
ASSIGN_DAY = 14

# ---------------- 用户分群（历史活跃度）与转化参数 ----------------
# 每个分群: (占比, 触达率, 访问|触达, 领取|访问, 14天核销|领取)
SEGMENTS = {
    "New":    dict(w=0.20, reach=0.885, visit=0.500, claim=0.620, redeem=0.500),
    "Low":    dict(w=0.30, reach=0.910, visit=0.640, claim=0.680, redeem=0.570),
    "Medium": dict(w=0.30, reach=0.930, visit=0.755, claim=0.770, redeem=0.660),
    "High":   dict(w=0.20, reach=0.955, visit=0.855, claim=0.830, redeem=0.750),
}
# 实验期参数: (Control 基线核销率, Reminder 增量, Reward 额外增量对奖励敏感者)
EXP_PARAMS = {
    "New":    dict(base=0.22, rem_lift=0.040, rew_extra=0.075),
    "Low":    dict(base=0.28, rem_lift=0.065, rew_extra=0.085),
    "Medium": dict(base=0.33, rem_lift=0.065, rew_extra=0.085),
    "High":   dict(base=0.40, rem_lift=0.020, rew_extra=0.030),  # Sure thing 多
}
REWARD_SENSITIVE_SHARE = 0.28  # 奖励敏感用户占比

BENEFITS = [
    # benefit_id, 类别, 面值, 企业结算价, 供应商成本, 供应商, 履约成功率
    ("B001", "商超", 100, 100, 88, "SUP01", 0.990),
    ("B002", "餐饮", 80,  80,  68, "SUP02", 0.985),
    ("B003", "出行", 60,  60,  50, "SUP03", 0.990),
    ("B004", "电影", 50,  50,  40, "SUP04", 0.930),  # 问题供应商: 库存不足
    ("B005", "电商", 100, 100, 90, "SUP05", 0.990),
    ("B006", "健康", 120, 120, 100, "SUP06", 0.985),
]
DEPTS = ["研发", "销售", "生产", "职能", "客服"]
REGIONS = ["华东", "华北", "华南", "西部"]
CHANNELS = ["企业微信", "短信", "邮件"]


def ts(day, jitter_h=10):
    """Day N 加随机小时抖动的时间戳"""
    return BASE + timedelta(days=float(day), hours=float(np.random.uniform(0, jitter_h)))


def main(seed=42, db_path="data/benefits.duckdb", n_users=5000):
    rng = np.random.default_rng(seed)
    np.random.seed(seed)

    # ---------- dim_enterprise ----------
    dim_enterprise = pd.DataFrame([dict(
        enterprise_id="ENT001", enterprise_name_masked="某制造业企业A",
        industry="制造业", employee_size_band="3000-10000",
        contract_start_date="2025-12-01", settlement_model="固定服务费+按兑换结算",
        client_status="active")])

    # ---------- dim_campaign ----------
    budget = 251000.0  # 校准后使预算使用率≈68.4%
    dim_campaign = pd.DataFrame([dict(
        campaign_id="CMP2026Q1", enterprise_id="ENT001",
        campaign_name="2026春季员工关怀福利活动", campaign_type="节日福利",
        start_time=BASE, end_time=BASE + timedelta(days=EXPIRY_DAY),
        redemption_deadline=BASE + timedelta(days=EXPIRY_DAY),
        benefit_budget=budget, target_employee_count=n_users,
        settlement_model="固定服务费+按兑换结算")])

    # ---------- dim_user ----------
    seg_names = list(SEGMENTS)
    seg_w = [SEGMENTS[s]["w"] for s in seg_names]
    segs = rng.choice(seg_names, n_users, p=seg_w)
    hist_map = {"New": (0, 0), "Low": (1, 1), "Medium": (3, 2), "High": (6, 4)}
    users = pd.DataFrame({
        "user_id": [f"U{i:05d}" for i in range(n_users)],
        "enterprise_id": "ENT001",
        "department_group": rng.choice(DEPTS, n_users, p=[.3, .2, .25, .15, .1]),
        "region": rng.choice(REGIONS, n_users, p=[.4, .25, .2, .15]),
        "employment_type": rng.choice(["正式", "外包"], n_users, p=[.85, .15]),
        "activity_segment": segs,
    })
    users["historical_campaign_count"] = [hist_map[s][0] + int(rng.integers(0, 2)) for s in segs]
    users["historical_redemption_count"] = [
        max(0, hist_map[s][1] + int(rng.integers(-1, 2))) if s != "New" else 0 for s in segs]
    users["historical_redemption_amount"] = users["historical_redemption_count"] * rng.uniform(50, 120, n_users)
    users["reward_sensitive_latent"] = rng.random(n_users) < REWARD_SENSITIVE_SHARE  # 生成用,不入正式维表
    users["join_month"] = pd.to_datetime(
        rng.choice(pd.date_range("2018-01-01", "2025-12-01", freq="MS"), n_users))

    # ---------- fact_campaign_eligibility ----------
    elig = users[["user_id"]].copy()
    elig["campaign_id"] = "CMP2026Q1"
    elig["eligible_flag"] = 1
    elig["eligibility_time"] = BASE - timedelta(days=3)
    elig["benefit_quota"] = 1
    elig["eligibility_source"] = "HR名单"
    elig["exclusion_reason"] = None

    # ---------- fact_coupon_issuance (发放成功率 98.4%) ----------
    issued_flag = rng.random(n_users) < 0.984
    fail_reasons = rng.choice(["账号异常", "接口超时", "名单信息缺失"], n_users, p=[.45, .35, .2])
    pref_idx = rng.integers(0, len(BENEFITS), n_users)
    coupons = pd.DataFrame({
        "coupon_instance_id": [f"CPN{i:06d}" for i in range(n_users)],
        "campaign_id": "CMP2026Q1",
        "user_id": users["user_id"],
        "benefit_id": [BENEFITS[i][0] for i in pref_idx],
        "issue_time": [ts(0, 4) for _ in range(n_users)],
        "issue_status": np.where(issued_flag, "success", "failed"),
        "failure_reason": np.where(issued_flag, None, fail_reasons),
        "face_value": [BENEFITS[i][2] for i in pref_idx],
        "expiry_time": BASE + timedelta(days=EXPIRY_DAY),
    })

    # ---------- 基线行为链路: 触达→访问→领取→核销 ----------
    seg_p = users["activity_segment"].map(lambda s: SEGMENTS[s])
    reached = issued_flag & (rng.random(n_users) < np.array([p["reach"] for p in seg_p]))
    visited = reached & (rng.random(n_users) < np.array([p["visit"] for p in seg_p]))
    claimed = visited & (rng.random(n_users) < np.array([p["claim"] for p in seg_p]))
    redeemed_base = claimed & (rng.random(n_users) < np.array([p["redeem"] for p in seg_p]))

    visit_day = np.clip(rng.exponential(2.5, n_users) + 0.2, 0.2, 13.5)
    claim_day = np.minimum(visit_day + np.clip(rng.exponential(0.8, n_users), 0.01, 3), 13.6)
    redeem_day = np.clip(claim_day + rng.exponential(3.5, n_users), claim_day + 0.05, 13.8)

    # ---------- fact_touch: 首次通知 ----------
    touch_rows = []
    for i in range(n_users):
        if not issued_flag[i]:
            continue
        ch = CHANNELS[int(rng.integers(0, 3))]
        touch_rows.append(dict(
            touch_id=f"T0_{i:05d}", campaign_id="CMP2026Q1", user_id=users.user_id[i],
            channel=ch, touch_type="首次通知", template_id="TPL001",
            send_time=ts(0.2, 3),
            delivery_status="delivered" if reached[i] else "failed",
            open_time=ts(visit_day[i] * 0.6) if visited[i] else None,
            experiment_id=None, group_name=None))
    # Day 7 常规提醒（非实验）: 给所有已触达用户
    for i in range(n_users):
        if reached[i] and rng.random() < 0.9:
            touch_rows.append(dict(
                touch_id=f"T7_{i:05d}", campaign_id="CMP2026Q1", user_id=users.user_id[i],
                channel="企业微信", touch_type="常规提醒", template_id="TPL002",
                send_time=ts(7, 3), delivery_status="delivered", open_time=None,
                experiment_id=None, group_name=None))

    # ---------- fact_user_event ----------
    ev_rows = []
    for i in range(n_users):
        if not visited[i]:
            continue
        uid = users.user_id[i]
        sess = f"S{i:05d}_1"
        dev = "mobile" if rng.random() < 0.8 else "pc"
        src = "企业微信" if rng.random() < 0.7 else "短信链接"
        t0 = ts(visit_day[i], 0)
        ev_rows.append(dict(event_id=f"E{i:05d}_pv", campaign_id="CMP2026Q1", user_id=uid,
                            session_id=sess, event_type="page_view", event_time=t0,
                            benefit_id=None, device_type=dev, source_channel=src))
        n_bv = int(rng.integers(1, 4))
        for k in range(n_bv):
            ev_rows.append(dict(event_id=f"E{i:05d}_bv{k}", campaign_id="CMP2026Q1", user_id=uid,
                                session_id=sess, event_type="benefit_view",
                                event_time=t0 + timedelta(minutes=float(2 + 3 * k)),
                                benefit_id=BENEFITS[int(rng.integers(0, 6))][0],
                                device_type=dev, source_channel=src))
        if claimed[i]:
            ev_rows.append(dict(event_id=f"E{i:05d}_cc", campaign_id="CMP2026Q1", user_id=uid,
                                session_id=sess, event_type="click_claim",
                                event_time=t0 + timedelta(minutes=12),
                                benefit_id=coupons.benefit_id[i], device_type=dev, source_channel=src))

    # ---------- fact_claim ----------
    claim_rows = []
    for i in range(n_users):
        if not claimed[i]:
            continue
        claim_rows.append(dict(
            claim_id=f"CLM{i:06d}", coupon_instance_id=coupons.coupon_instance_id[i],
            campaign_id="CMP2026Q1", user_id=users.user_id[i],
            benefit_id=coupons.benefit_id[i], claim_time=ts(claim_day[i], 0),
            claim_status="success", failure_reason=None))

    # ---------- 实验: Day 14 对已访问未核销用户分组 ----------
    pool_mask = visited & (~redeemed_base)
    pool_idx = np.where(pool_mask)[0]
    rng.shuffle(pool_idx)
    n_exp = min(1500, len(pool_idx) // 3 * 3)
    exp_idx = np.sort(pool_idx[:n_exp])
    # 按分群分层随机 1:1:1
    groups = np.empty(n_exp, dtype=object)
    exp_segs = users.activity_segment.values[exp_idx]
    order = np.argsort(exp_segs, kind="stable")
    cyc = np.tile(["Control", "Reminder", "Reminder+Reward"], n_exp // 3 + 1)[:n_exp]
    rng.shuffle(cyc)  # 打乱后按分层内轮转
    for s in np.unique(exp_segs):
        m = exp_segs == s
        k = m.sum()
        g = np.tile(["Control", "Reminder", "Reminder+Reward"], k // 3 + 1)[:k]
        rng.shuffle(g)
        groups[m] = g

    exp_assign = pd.DataFrame({
        "experiment_id": "EXP001", "campaign_id": "CMP2026Q1",
        "user_id": users.user_id.values[exp_idx],
        "group_name": groups,
        "assignment_time": BASE + timedelta(days=ASSIGN_DAY, hours=10),
        "random_seed": seed,
        "stratification_key": exp_segs,
        "eligible_at_assignment": 1})

    # 实验期核销结果
    exp_redeem = np.zeros(n_exp, dtype=bool)
    for j, i in enumerate(exp_idx):
        p = EXP_PARAMS[users.activity_segment[i]]
        prob = p["base"]
        if groups[j] in ("Reminder", "Reminder+Reward"):
            prob += p["rem_lift"]
        if groups[j] == "Reminder+Reward" and users.reward_sensitive_latent[i]:
            prob += p["rew_extra"]
        exp_redeem[j] = rng.random() < prob
    exp_redeem_day = ASSIGN_DAY + np.clip(rng.exponential(4, n_exp) + 0.3, 0.3, 13.5)

    # 实验触达记录（Reminder / Reward 组）
    for j, i in enumerate(exp_idx):
        if groups[j] == "Control":
            continue
        tt = "到期提醒" if groups[j] == "Reminder" else "提醒+奖励"
        touch_rows.append(dict(
            touch_id=f"TX_{i:05d}", campaign_id="CMP2026Q1", user_id=users.user_id[i],
            channel="企业微信", touch_type=tt,
            template_id="TPL101" if groups[j] == "Reminder" else "TPL102",
            send_time=BASE + timedelta(days=ASSIGN_DAY, hours=11),
            delivery_status="delivered",
            open_time=ts(ASSIGN_DAY + 1) if rng.random() < 0.75 else None,
            experiment_id="EXP001", group_name=groups[j]))

    # ---------- 实验期未领取者补领取 + 汇总核销订单 ----------
    ben_lookup = {b[0]: b for b in BENEFITS}
    order_rows, fulfill_rows = [], []
    exp_map = dict(zip(exp_idx, range(n_exp)))
    oid = 0
    for i in range(n_users):
        base_r = bool(redeemed_base[i])
        in_exp = i in exp_map
        exp_r = bool(exp_redeem[exp_map[i]]) if in_exp else False
        if not (base_r or exp_r):
            continue
        r_day = redeem_day[i] if base_r else exp_redeem_day[exp_map[i]]
        # 实验期核销但此前未领取的用户: 补一条领取记录（提醒后先领取再核销）
        if exp_r and not claimed[i]:
            claim_rows.append(dict(
                claim_id=f"CLMX{i:06d}", coupon_instance_id=coupons.coupon_instance_id[i],
                campaign_id="CMP2026Q1", user_id=users.user_id[i],
                benefit_id=coupons.benefit_id[i],
                claim_time=ts(r_day - 0.2, 0), claim_status="success", failure_reason=None))
        b = ben_lookup[coupons.benefit_id[i]]
        reward_cost = 5.0 if (in_exp and groups[exp_map[i]] == "Reminder+Reward" and exp_r) else 0.0
        status = "success"
        if rng.random() < 0.015:
            status = "refunded"  # 少量退款
        order_rows.append(dict(
            order_id=f"ORD{oid:06d}", coupon_instance_id=coupons.coupon_instance_id[i],
            campaign_id="CMP2026Q1", user_id=users.user_id[i], benefit_id=b[0],
            redemption_time=ts(r_day, 0), redemption_status=status,
            settlement_amount=float(b[3]), supplier_cost=float(b[4]),
            reward_cost=reward_cost, platform_revenue=float(b[3] - b[4] - reward_cost)))
        # 履约
        ok = rng.random() < b[6]
        fulfill_rows.append(dict(
            fulfillment_id=f"FUL{oid:06d}", order_id=f"ORD{oid:06d}", supplier_id=b[5],
            fulfillment_status="success" if ok and status == "success" else "failed",
            fulfillment_time=ts(r_day + 0.1, 0),
            failure_reason=None if ok else rng.choice(["库存不足", "兑换码延迟", "接口异常"], p=[.5, .3, .2]),
            complaint_flag=int((not ok) and rng.random() < 0.4),
            refund_flag=int(status == "refunded" or ((not ok) and rng.random() < 0.6))))
        oid += 1

    fact_touch = pd.DataFrame(touch_rows)
    fact_event = pd.DataFrame(ev_rows)
    fact_claim = pd.DataFrame(claim_rows)
    fact_order = pd.DataFrame(order_rows)
    fact_fulfill = pd.DataFrame(fulfill_rows)

    # ---------- dws_user_campaign 宽表 ----------
    redeemed_14d = redeemed_base  # 14 天口径 = 基线窗口内核销
    dws = users[["user_id", "enterprise_id", "department_group", "region",
                 "employment_type", "activity_segment",
                 "historical_campaign_count", "historical_redemption_count",
                 "historical_redemption_amount"]].copy()
    dws["campaign_id"] = "CMP2026Q1"
    dws["eligible_flag"] = 1
    dws["issued_flag"] = issued_flag.astype(int)
    dws["reached_flag"] = reached.astype(int)
    dws["visited_flag"] = visited.astype(int)
    dws["first_visit_day"] = np.where(visited, visit_day.round(2), np.nan)
    dws["claimed_flag"] = claimed.astype(int)
    dws["redeemed_14d_flag"] = redeemed_14d.astype(int)
    dws["benefit_id"] = coupons["benefit_id"]
    dws["benefit_type"] = dws["benefit_id"].map(lambda b: ben_lookup[b][1])
    # 实验字段
    dws["experiment_id"] = None
    dws["group_name"] = None
    dws["exp_redeemed_flag"] = 0
    dws.loc[exp_idx, "experiment_id"] = "EXP001"
    dws.loc[exp_idx, "group_name"] = groups
    dws.loc[exp_idx, "exp_redeemed_flag"] = exp_redeem.astype(int)
    # 财务与履约（有效订单口径）
    ord_ok = fact_order[fact_order.redemption_status == "success"]
    dws = dws.merge(ord_ok[["user_id", "settlement_amount", "supplier_cost", "reward_cost"]],
                    on="user_id", how="left")
    ful = fact_fulfill.merge(fact_order[["order_id", "user_id"]], on="order_id")
    dws = dws.merge(ful.groupby("user_id").agg(
        fulfilled_flag=("fulfillment_status", lambda s: int((s == "success").any())),
        complaint_flag=("complaint_flag", "max")).reset_index(), on="user_id", how="left")
    for c in ["settlement_amount", "supplier_cost", "reward_cost", "fulfilled_flag", "complaint_flag"]:
        dws[c] = dws[c].fillna(0)

    # ---------- 卡券采购批次 + 库存实例 (独立随机流, 不影响已校准口径) ----------
    rng2 = np.random.default_rng(seed + 7777)
    REFUNDABLE = {"B001": True, "B002": True, "B003": True,
                  "B004": False, "B005": True, "B006": False}  # 电影/健康 不可退
    dim_benefit = pd.DataFrame([dict(
        benefit_id=b[0], supplier_id=b[5], benefit_name=f"{b[1]}卡券{b[2]}元",
        benefit_category=b[1], face_value=b[2], standard_purchase_cost=b[4],
        enterprise_settlement_price=b[3], validity_days=EXPIRY_DAY,
        refundable_flag=REFUNDABLE[b[0]], transferable_flag=REFUNDABLE[b[0]])
        for b in BENEFITS])

    redeemed_user_set = set(fact_order[fact_order.redemption_status == "success"].user_id)
    claimed_user_set = set(fact_claim[fact_claim.claim_status == "success"].user_id)
    batch_rows, inv_rows = [], []
    aid = 0
    for b in BENEFITS:
        bmask = coupons.benefit_id == b[0]
        n_assigned = int(bmask.sum())
        # 按预计领取率+缓冲采购, 不可退权益缓冲更小（控制资金占用与过期风险）
        buffer = float(rng2.uniform(1.04, 1.08)) if not REFUNDABLE[b[0]] \
            else float(rng2.uniform(1.10, 1.18))
        n_purchased = int(np.ceil(n_assigned * buffer))
        for bi, (share, pdate, edate) in enumerate(
                [(0.7, -10, EXPIRY_DAY), (0.3, 5, EXPIRY_DAY + 30)]):
            qty = int(n_purchased * share)
            batch_rows.append(dict(
                purchase_batch_id=f"PB_{b[0]}_{bi+1}", supplier_id=b[5], benefit_id=b[0],
                purchase_date=(BASE + timedelta(days=pdate)).date(),
                purchase_quantity=qty, unit_cost=float(b[4]),
                total_cost=float(qty * b[4]),
                expiry_date=(BASE + timedelta(days=edate)).date(),
                refundable_flag=REFUNDABLE[b[0]], transferable_flag=REFUNDABLE[b[0]],
                payment_status="已付款"))
        # 已分配卡券实例（复用 issuance 的 coupon_instance_id）
        for i in np.where(bmask)[0]:
            uid = users.user_id[i]
            if uid in redeemed_user_set:
                status = "redeemed"
            elif uid in claimed_user_set:
                status = "claimed"
            else:
                status = "assigned"
            inv_rows.append(dict(
                coupon_instance_id=coupons.coupon_instance_id[i],
                purchase_batch_id=f"PB_{b[0]}_1", benefit_id=b[0],
                coupon_status=status,
                inventory_time=BASE - timedelta(days=9),
                assigned_campaign_id="CMP2026Q1", assigned_user_id=uid,
                assigned_time=coupons.issue_time[i],
                expiry_time=BASE + timedelta(days=EXPIRY_DAY)))
        # 未分配库存（available, 部分临期）
        for k in range(n_purchased - n_assigned):
            from_b2 = k % 3 != 0
            inv_rows.append(dict(
                coupon_instance_id=f"CPNA_{b[0]}_{aid}",
                purchase_batch_id=f"PB_{b[0]}_2" if from_b2 else f"PB_{b[0]}_1",
                benefit_id=b[0], coupon_status="available",
                inventory_time=BASE + timedelta(days=5 if from_b2 else -9),
                assigned_campaign_id=None, assigned_user_id=None, assigned_time=None,
                expiry_time=BASE + timedelta(days=EXPIRY_DAY + 30 if from_b2 else EXPIRY_DAY)))
            aid += 1
    fact_purchase_batch = pd.DataFrame(batch_rows)
    fact_inventory = pd.DataFrame(inv_rows)

    # ---------- 写入 DuckDB ----------
    def sanitize(df):
        df = df.copy()
        for c in df.columns:
            if df[c].dtype == object:
                nonnull = df[c].dropna()
                if len(nonnull) and isinstance(nonnull.iloc[0], (datetime, pd.Timestamp)):
                    df[c] = pd.to_datetime(df[c])
                else:
                    df[c] = df[c].apply(lambda x: None if pd.isna(x) else str(x)).astype("string")
        return df

    con = duckdb.connect(db_path)
    users_out = users.drop(columns=["reward_sensitive_latent"])
    for name, df in [
        ("dim_enterprise", dim_enterprise), ("dim_campaign", dim_campaign),
        ("dim_user", users_out), ("dim_benefit", dim_benefit),
        ("fact_coupon_purchase_batch", fact_purchase_batch),
        ("fact_coupon_inventory", fact_inventory),
        ("fact_campaign_eligibility", elig),
        ("fact_coupon_issuance", coupons), ("fact_touch", fact_touch),
        ("fact_user_event", fact_event), ("fact_claim", fact_claim),
        ("fact_redemption_order", fact_order), ("fact_fulfillment", fact_fulfill),
        ("fact_experiment_assignment", exp_assign), ("dws_user_campaign", dws),
    ]:
        con.execute(f"DROP TABLE IF EXISTS {name}")
        try:
            con.register("tmp_df", sanitize(df))
            con.execute(f"CREATE TABLE {name} AS SELECT * FROM tmp_df")
            con.unregister("tmp_df")
        except Exception:
            print(f"[ERROR] 写表失败: {name}")
            print(sanitize(df).dtypes)
            raise
    con.close()

    # ---------- 校准输出 ----------
    n_iss = issued_flag.sum()
    settled = ord_ok["settlement_amount"].sum()
    print(f"users={n_users}  issued={n_iss}")
    print(f"发放成功率 = {n_iss/n_users:.1%}   (目标 98.4%)")
    print(f"触达率     = {reached.sum()/n_iss:.1%}   (目标 92.1%)")
    print(f"累计访问率 = {visited.sum()/n_iss:.1%}   (目标 63.8%)")
    print(f"累计领取率 = {claimed.sum()/n_iss:.1%}   (目标 47.6%)")
    print(f"14天核销率 = {redeemed_14d.sum()/n_iss:.1%}   (目标 31.2%)")
    fr = (fact_fulfill.fulfillment_status == "success").mean()
    print(f"履约成功率 = {fr:.1%}   (目标 96.5%)")
    print(f"预算使用率 = {settled/budget:.1%}   (目标 68.4%)")
    print(f"实验样本   = {n_exp}")
    for g in ["Control", "Reminder", "Reminder+Reward"]:
        m = groups == g
        print(f"  {g:<16} n={m.sum():4d}  核销率={exp_redeem[m].mean():.1%}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=9)  # seed=9 经校准使各指标最贴近目标口径
    ap.add_argument("--db", default="data/benefits.duckdb")
    args = ap.parse_args()
    main(seed=args.seed, db_path=args.db)
