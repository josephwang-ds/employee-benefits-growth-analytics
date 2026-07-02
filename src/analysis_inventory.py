# -*- coding: utf-8 -*-
"""
卡券采购与库存分析
两条 Funnel 的第二条: 卡券库存 Funnel (coupon_instance_id 粒度)
回答: 采购回来的卡券在哪一步没有被消化; 到期/资金风险有多大 
用法: python src/analysis_inventory.py [--db data/yuyue.duckdb]
"""
import argparse
import duckdb
import pandas as pd

pd.set_option("display.width", 160)
AS_OF = "2026-04-05"  # 观察时点: 活动 Day 34 (实验结束后一周, 复盘时点; 卡券 4/16 到期)


def inventory_funnel(con):
    q = """
    SELECT COUNT(*) purchased,
           SUM(CASE WHEN coupon_status != 'available' THEN 1 ELSE 0 END) assigned,
           SUM(CASE WHEN coupon_status IN ('claimed','redeemed') THEN 1 ELSE 0 END) claimed,
           SUM(CASE WHEN coupon_status = 'redeemed' THEN 1 ELSE 0 END) redeemed
    FROM fact_coupon_inventory
    """
    b = con.execute(q).df().iloc[0]
    print("========== 卡券库存 Funnel (卡券实例粒度) ==========")
    print(f"  Purchased  {int(b.purchased):5d}")
    print(f"  Assigned   {int(b.assigned):5d}   分配率   = {b.assigned/b.purchased:.1%}")
    print(f"  Claimed    {int(b.claimed):5d}   领取率   = {b.claimed/b.assigned:.1%} (口径: /已分配)")
    print(f"  Redeemed   {int(b.redeemed):5d}   卡券核销率 = {b.redeemed/b.assigned:.1%} (口径: /已分配)")
    print("  注意: 用户 Funnel 用 distinct user_id, 库存 Funnel 用 coupon_instance_id, 两条线分开讲")


def aging_and_risk(con):
    q = f"""
    SELECT i.benefit_id, d.benefit_category, d.refundable_flag,
           COUNT(*) FILTER (WHERE i.coupon_status IN ('available','assigned','claimed'))
               AS un_redeemed,
           COUNT(*) FILTER (WHERE i.coupon_status IN ('available','assigned','claimed')
               AND i.expiry_time <= TIMESTAMP '{AS_OF}' + INTERVAL 7 DAY)  AS expiring_7d,
           COUNT(*) FILTER (WHERE i.coupon_status IN ('available','assigned','claimed')
               AND i.expiry_time <= TIMESTAMP '{AS_OF}' + INTERVAL 14 DAY) AS expiring_14d,
           COUNT(*) FILTER (WHERE i.coupon_status IN ('available','assigned','claimed')
               AND i.expiry_time <= TIMESTAMP '{AS_OF}' + INTERVAL 30 DAY) AS expiring_30d,
           ROUND(SUM(CASE WHEN i.coupon_status IN ('available','assigned','claimed')
               AND NOT d.refundable_flag
               AND i.expiry_time <= TIMESTAMP '{AS_OF}' + INTERVAL 30 DAY
               THEN d.standard_purchase_cost ELSE 0 END), 0) AS at_risk_cost
    FROM fact_coupon_inventory i
    JOIN dim_benefit d ON i.benefit_id = d.benefit_id
    GROUP BY 1, 2, 3 ORDER BY at_risk_cost DESC
    """
    df = con.execute(q).df()
    print(f"\n========== 库存老化与过期风险 (观察时点 {AS_OF}) ==========")
    print(df.to_string(index=False))
    total_risk = df.at_risk_cost.sum()
    print(f"\n  不可退临期(30天)库存风险敞口 ≈ ¥{total_risk:,.0f}")
    print("  行动: 临期卡券优先用于高Lift人群提醒/转入后续活动; 不可退短效期权益压缩采购上限")


def capital_usage(con):
    q = """
    SELECT ROUND(SUM(CASE WHEN i.coupon_status != 'redeemed'
                     THEN d.standard_purchase_cost ELSE 0 END), 0) AS unconsumed_capital,
           ROUND(SUM(d.standard_purchase_cost), 0) AS total_purchase_cost,
           ROUND(SUM(CASE WHEN i.coupon_status = 'redeemed'
                     THEN d.standard_purchase_cost ELSE 0 END), 0) AS consumed_cost
    FROM fact_coupon_inventory i JOIN dim_benefit d ON i.benefit_id = d.benefit_id
    """
    b = con.execute(q).df().iloc[0]
    print("\n========== 资金占用 ==========")
    print(f"  总采购成本      ¥{b.total_purchase_cost:,.0f}")
    print(f"  已消耗(核销)    ¥{b.consumed_cost:,.0f}")
    print(f"  未消化库存资金  ¥{b.unconsumed_capital:,.0f}  "
          f"({b.unconsumed_capital/b.total_purchase_cost:.0%})")
    print("""
  贡献毛利口径:
  贡献毛利 = 企业结算收入 - 已消耗卡券采购成本 - 过期/不可退损失 - 奖励 - 消息成本
  => 低核销不只是收入未实现, 还有真金白银的库存损失, 这是做增量运营的商业动机""")


def purchase_coverage(con):
    q = """
    WITH p AS (SELECT benefit_id, SUM(purchase_quantity) purchased
               FROM fact_coupon_purchase_batch GROUP BY 1),
         a AS (SELECT benefit_id,
                      COUNT(*) FILTER (WHERE coupon_status != 'available') assigned
               FROM fact_coupon_inventory GROUP BY 1)
    SELECT p.benefit_id, d.benefit_category, d.refundable_flag,
           p.purchased, a.assigned,
           ROUND(a.assigned * 100.0 / p.purchased, 1) alloc_pct
    FROM p JOIN a USING (benefit_id) JOIN dim_benefit d USING (benefit_id)
    ORDER BY alloc_pct
    """
    print("\n========== 采购覆盖与分配率 (按权益) ==========")
    print(con.execute(q).df().to_string(index=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/yuyue.duckdb")
    args = ap.parse_args()
    con = duckdb.connect(args.db, read_only=True)
    inventory_funnel(con)
    purchase_coverage(con)
    aging_and_risk(con)
    capital_usage(con)
    con.close()
