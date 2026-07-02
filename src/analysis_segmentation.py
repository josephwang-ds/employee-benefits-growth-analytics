# -*- coding: utf-8 -*-
"""
用户规则分层 (v2 方案 11.1) — 基于行为的动态运营分层
与静态历史活跃度分层(activity_segment)是两层体系:
  静态层: New/Low/Medium/High  —— 谁是什么样的人(历史)
  规则层: 本次活动中的行为状态  —— 现在该对谁做什么(动作)
用法: python src/analysis_segmentation.py [--db data/yuyue.duckdb]
"""
import argparse
import duckdb
import pandas as pd

pd.set_option("display.width", 160)

# 分层判定 SQL: CASE WHEN 从上到下按优先级互斥判定
SEGMENT_SQL = """
SELECT d.*,
  CASE
    -- 1) 履约风险: 已核销但履约失败或投诉 -> 先修服务, 不推销
    WHEN (redeemed_14d_flag = 1 OR exp_redeemed_flag = 1)
         AND (fulfilled_flag = 0 OR complaint_flag = 1)         THEN '6_Fulfillment Risk'
    -- 2) 自然活跃: 快速访问(3天内)且已核销 -> 不补贴, 普通提醒即可
    WHEN redeemed_14d_flag = 1 AND first_visit_day <= 3         THEN '1_Natural Active'
    -- 3) 常规核销: 核销了但不算快 -> 维持现状
    WHEN redeemed_14d_flag = 1                                  THEN '2_Standard Redeemer'
    -- 4) 已访问未核销: 意愿明确但没完成 -> 到期提醒实验的目标人群
    WHEN visited_flag = 1                                       THEN '3_Visited Not Redeemed'
    -- 5) 沉默: 多次活动无兑换且本次也没访问 -> 降低打扰, 测权益偏好
    WHEN reached_flag = 1 AND historical_campaign_count >= 2
         AND historical_redemption_count = 0                    THEN '5_Dormant'
    -- 6) 已触达未访问: 收到消息没进来 -> 测文案/渠道/发送时间
    WHEN reached_flag = 1                                       THEN '4_Reached Not Visited'
    -- 7) 未触达/发放失败 -> 修数据和通道
    WHEN issued_flag = 1                                        THEN '7_Not Reached'
    ELSE                                                             '8_Issue Failed'
  END AS rule_segment
FROM dws_user_campaign d
"""

ACTIONS = {
    "1_Natural Active":        ("不补贴, 普通提醒", "补贴他们=补贴 Sure Thing, 纯浪费"),
    "2_Standard Redeemer":     ("维持现状", "已完成转化, 关注下一活动参与"),
    "3_Visited Not Redeemed":  ("到期提醒实验(本项目已验证 +6.2pp)", "意愿明确, 增量潜力最大的人群"),
    "4_Reached Not Visited":   ("消息模板/渠道/发送时间 A/B", "问题在内容不在通道"),
    "5_Dormant":               ("降低打扰频率, 测权益偏好", "反复推送有退订/投诉风险"),
    "6_Fulfillment Risk":      ("优先修复履约问题+主动补偿", "先修体验再谈运营"),
    "7_Not Reached":           ("更新联系方式, 更换渠道", "运营动作到不了, 先修数据"),
    "8_Issue Failed":          ("失败原因分类+重试", "名单/账号/接口问题"),
}


def segment_summary(con):
    df = con.execute(f"""
        WITH s AS ({SEGMENT_SQL})
        SELECT rule_segment, COUNT(*) AS users,
               ROUND(AVG(historical_redemption_count), 2) AS hist_redeem_avg,
               ROUND(SUM(CASE WHEN activity_segment='High' THEN 1 ELSE 0 END)*100.0
                     / COUNT(*), 1) AS high_activity_pct
        FROM s GROUP BY 1 ORDER BY 1
    """).df()
    df["建议动作"] = df.rule_segment.map(lambda s: ACTIONS[s][0])
    df["理由"] = df.rule_segment.map(lambda s: ACTIONS[s][1])
    print("========== 规则分层结果 (互斥, 按优先级判定) ==========")
    print(df.to_string(index=False))
    return df


def cross_check(con):
    """规则分层 × 静态活跃度分层 交叉表: 验证两层体系互补而非重复"""
    df = con.execute(f"""
        WITH s AS ({SEGMENT_SQL})
        SELECT rule_segment, activity_segment, COUNT(*) AS n
        FROM s GROUP BY 1, 2
    """).df()
    pivot = df.pivot(index="rule_segment", columns="activity_segment", values="n").fillna(0)
    pivot = pivot[["New", "Low", "Medium", "High"]].astype(int)
    print("\n========== 规则分层 × 历史活跃度 交叉表 ==========")
    print(pivot.to_string())
    print("""
两层体系的分工:
- 静态层回答"这是什么人"(历史), 用于分层随机和 Cohort;
- 规则层回答"现在对他做什么"(本次行为), 直接映射运营动作;
- 例: 同是 Visited Not Redeemed, High 活跃的人提醒即可, New 用户可能要搭配权益推荐。
Reward Sensitive 层说明: 需要历史奖励活动的响应数据才能标注;
本数据只有单次活动, 实验 Reward 组的响应可作为第一批标注来源, 属于后续迭代。""")
    return pivot


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/yuyue.duckdb")
    args = ap.parse_args()
    con = duckdb.connect(args.db, read_only=True)
    segment_summary(con)
    cross_check(con)
    con.close()
