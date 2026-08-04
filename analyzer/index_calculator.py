"""
宝妈指数计算引擎
四个板块独立计算，各自有完整的历史曲线
"""
from datetime import datetime, date
from typing import Dict, List
import json
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

SECTOR_NAMES = {
    "nasdaq": "纳斯达克",
    "gold": "黄金",
    "cpo": "CPO通信",
    "semiconductor": "半导体",
}

# 平台代表度先验权重（与帖子数无关）。股吧日采集 ~300 条、小红书 ~80、微博 ~60，
# 若帖子直接合并，股吧（小白占比天然低 5-20%，README 已记录）会主导占比类指标。
# 权重依据：小红书小白密度最高（泛用户平台+关键词命中≈小白，最贴近宝妈画像）；
# 微博有完整正文走 LLM 语义分类；股吧股民聚集、小白占比天然低。
PLATFORM_WEIGHTS = {
    "xiaohongshu": 0.40,
    "weibo": 0.35,
    "guba": 0.25,
}


def _platform_metrics(posts: List) -> Dict:
    """单个平台内聚合四个维度 + 买卖分量。无有效帖（全是垃圾帖）返回 None。"""
    valid = [r for r in posts if r.level != "垃圾帖"]
    if not valid:
        return None
    newbie = [r for r in valid if r.newbie_score >= 20]
    pure = [r for r in valid if r.newbie_score >= 50]
    buy = [r for r in newbie if r.intent == "buy"]
    sell = [r for r in newbie if r.intent == "sell"]
    n = len(newbie)
    return {
        "count": n,
        "newbie_ratio": len(newbie) / len(valid) * 100,
        "avg_score": sum(r.newbie_score for r in newbie) / max(n, 1),
        "avg_sentiment": sum(abs(r.sentiment_score) for r in newbie) / max(n, 1) * 100,
        "purity": len(pure) / max(n, 1) * 100,
        "buy_ratio": len(buy) / max(n, 1),
        "sell_ratio": len(sell) / max(n, 1),
        "buy_intensity": sum(r.intent_strength for r in buy) / max(len(buy), 1),
        "sell_intensity": sum(r.intent_strength for r in sell) / max(len(sell), 1),
    }


def compute_sector_index(analysis_results: List) -> Dict:
    """
    计算单个板块的宝妈指数 (0-100)

    平台加权：各平台先独立聚合四个维度，再按 PLATFORM_WEIGHTS（代表度先验）
    加权合成；某平台当天无有效帖时权重归一化到其余平台。

    四个维度:
    1. 小白占比 (40%) — 平台加权后的小白帖比例
    2. 小白强度 (25%) — 小白帖的平均得分
    3. 情绪极端度 (20%) — 贪婪/恐慌的情绪极端程度
    4. 热度信号 (15%) — 小白帖占比越高 + 纯小白越多 = 信号越强
    """
    if not analysis_results:
        return {
            "index": 0,
            "interpretation": "无数据",
            "details": {}
        }

    total = len(analysis_results)

    # 平台分组聚合（未知平台 weight 按 0，不参与加权；计数仍计入 total）
    groups: Dict[str, List] = {}
    for r in analysis_results:
        groups.setdefault(r.platform, []).append(r)
    metrics = {p: _platform_metrics(posts) for p, posts in groups.items()}

    # 有效平台（有非垃圾帖）参与加权，缺失平台权重归一化
    valid_plats = {p for p, m in metrics.items() if m is not None}
    weights = {p: PLATFORM_WEIGHTS.get(p, 0.0) for p in valid_plats}
    total_w = sum(weights.values())
    if not weights or total_w == 0:
        return {"index": 0, "interpretation": "无数据", "details": {}}

    def blend(key: str, plat_set: set) -> float:
        """指标在给定平台集合内按权重混合（集合外平台不参与，集合内归一化）。

        占比类指标（newbie_ratio 等）用 valid_plats + 0 填充——平台无小白则
        占比就是 0；强度类指标（avg_score、intensity 等）只用有样本的平台——
        平均分是"均值"不是"占比"，无样本平台应缺席而非拉低均值。
        """
        sub = {p: w for p, w in weights.items() if p in plat_set}
        tw = sum(sub.values())
        if not sub or tw == 0:
            return 0.0
        return sum(m[key] * sub[p] / tw for p, m in metrics.items() if p in sub)

    # 维度1-4: 平台加权合成
    newbie_plats = {p for p in valid_plats if metrics[p]["count"] > 0}
    newbie_ratio = blend("newbie_ratio", valid_plats)
    avg_newbie_score = blend("avg_score", newbie_plats)
    avg_sentiment = blend("avg_sentiment", newbie_plats)
    purity_signal = blend("purity", newbie_plats)

    # 板块级计数（展示用，不参与指数）
    valid_posts = [r for r in analysis_results if r.level != "垃圾帖"]
    spam_count = total - len(valid_posts)
    newbie_count = len([r for r in valid_posts if r.newbie_score >= 20])
    pure_newbie = len([r for r in valid_posts if r.newbie_score >= 50])
    activity_signal = min(100, len(valid_posts) / 80 * 100)  # 80条为满热度

    # 综合指数
    index = (
        newbie_ratio * 0.40 +
        avg_newbie_score * 0.25 +
        avg_sentiment * 0.20 +
        purity_signal * 0.15
    )

    index = round(min(100, index), 1)

    # ---- 买入/卖出子指数（平台加权）----
    buy_ratio = blend("buy_ratio", valid_plats)
    sell_ratio = blend("sell_ratio", valid_plats)
    buy_intensity = blend("buy_intensity",
                          {p for p in valid_plats if metrics[p]["buy_ratio"] > 0})
    sell_intensity = blend("sell_intensity",
                           {p for p in valid_plats if metrics[p]["sell_ratio"] > 0})
    newbie_buy = [r for r in valid_posts if r.intent == "buy"]
    newbie_sell = [r for r in valid_posts if r.intent == "sell"]

    # 买入指数: 小白买入占比(50%) + 小白热度(30%) + 买入强度(20%)
    mom_buy_index = round(min(100, (
        buy_ratio * 100 * 0.50 +
        (avg_newbie_score / 100) * buy_ratio * 30 * 0.30 +
        buy_intensity * 100 * 0.20
    )), 1)

    # 卖出指数: 小白卖出占比(50%) + 小白热度(30%) + 卖出强度(20%)
    mom_sell_index = round(min(100, (
        sell_ratio * 100 * 0.50 +
        (avg_newbie_score / 100) * sell_ratio * 30 * 0.30 +
        sell_intensity * 100 * 0.20
    )), 1)

    # 买卖比: >1 表示买入情绪占优, <1 表示恐慌卖出占优（加权占比比）
    if newbie_count == 0 or (buy_ratio <= 0 and sell_ratio <= 0):
        buy_sell_ratio = 0.0  # 无小白，或全为观望意图 → 无买卖倾向
    elif sell_ratio <= 0:
        buy_sell_ratio = 99.9  # 卖出为 0 且买入 > 0 → 买入绝对占优，封顶防除零
    else:
        buy_sell_ratio = round(min(99.9, buy_ratio / sell_ratio), 1)

    return {
        "index": index,
        "interpretation": interpret_index(index),
        "details": {
            "total_posts": total,
            "valid_posts": len(valid_posts),
            "spam_posts": spam_count,
            "newbie_posts": newbie_count,
            "pure_newbie": pure_newbie,
            "newbie_ratio": round(newbie_ratio, 1),
            "avg_newbie_score": round(avg_newbie_score, 1),
            "avg_sentiment": round(avg_sentiment, 1),
            "purity_signal": round(purity_signal, 1),
            "activity": round(activity_signal, 1),
            # 买入/卖出子指数
            "mom_buy_index": mom_buy_index,
            "mom_sell_index": mom_sell_index,
            "buy_sell_ratio": buy_sell_ratio,
            "buy_count": len(newbie_buy),
            "sell_count": len(newbie_sell),
        },
        "top_newbie_posts": [
            {
                "title": r.title[:60],
                "url": r.url,
                "date": r.date,
                "score": r.newbie_score,
                "level": r.level,
                "reasoning": r.reasoning[:150],
                "sentiment": r.sentiment_score,
                "intent": r.intent,
                "intent_label": {"buy": "🟢 买入", "sell": "🔴 卖出", "neutral": "⚪ 观望"}.get(r.intent, ""),
                "key_signals": r.key_signals[:2],
            }
            for r in sorted(valid_posts, key=lambda x: x.newbie_score, reverse=True)
            if r.newbie_score >= 20
        ][:5],
    }


def interpret_index(index: float) -> str:
    if index >= 75:
        return "🔴 极度狂热 — 擦鞋童时刻！小白情绪爆表，历史级别的危险信号"
    elif index >= 60:
        return "🟠 高度警惕 — 小白大量涌入，市场情绪过热，建议大幅减仓"
    elif index >= 40:
        return "🟡 开始升温 — 小白活跃度明显上升，需保持关注"
    elif index >= 20:
        return "🟢 正常区间 — 小白参与度适中，无需特别操作"
    else:
        return "🔵 极度冷清 — 小白沉默不语，可能是市场底部信号"


def load_history() -> Dict:
    """加载历史数据"""
    history_file = os.path.join(DATA_DIR, "history.json")
    if os.path.exists(history_file):
        with open(history_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"records": []}


def save_history(history: Dict):
    """保存历史数据"""
    history_file = os.path.join(DATA_DIR, "history.json")
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(history_file, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def add_record(sector_indices: Dict[str, Dict], analysis_results: Dict):
    """添加一条历史记录"""
    history = load_history()
    
    record = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "timestamp": datetime.now().isoformat(),
        "sectors": sector_indices,
    }
    
    # 如果今天已有记录，更新而非新增
    today = record["date"]
    existing = [r for r in history["records"] if r["date"] == today]
    if existing:
        history["records"] = [r for r in history["records"] if r["date"] != today]
    
    history["records"].append(record)
    history["records"].sort(key=lambda r: r["date"])
    save_history(history)


def get_dashboard_data() -> Dict:
    """获取前端所需的完整数据"""
    history = load_history()
    records = history.get("records", [])
    
    # 最新一条
    latest = records[-1] if records else None
    
    # 为每个板块准备历史曲线数据
    sector_history = {
        "nasdaq": [],
        "gold": [],
        "cpo": [],
        "semiconductor": [],
    }
    
    for r in records:
        for sector, data in r.get("sectors", {}).items():
            if sector in sector_history:
                sector_history[sector].append({
                    "date": r["date"],
                    "index": data["index"],
                })
    
    return {
        "latest": latest,
        "sector_history": sector_history,
        "record_count": len(records),
    }
