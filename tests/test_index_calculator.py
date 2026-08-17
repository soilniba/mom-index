"""
指数计算：平台加权聚合测试

背景：股吧日采集 ~300 条，小红书 ~80、微博 ~60，帖子数合并会让股吧
（小白占比天然低 5-20%）主导占比类指标。方案：平台内先聚合，再按
PLATFORM_WEIGHTS（代表度先验）加权合成；缺失平台权重归一化。
"""
import pytest

from analyzer.index_calculator import compute_sector_index, PLATFORM_WEIGHTS
from analyzer.llm_analyzer import AnalysisResult

_counter = 0


def make_post(platform, score, intent="neutral", strength=0.0,
              sentiment=0.0, level="偏小白"):
    """构造一条分析结果（默认非垃圾帖、非小白阈值以下可控）。"""
    global _counter
    _counter += 1
    return AnalysisResult(
        post_id=f"p{_counter}", title="t", platform=platform, sector="nasdaq",
        newbie_score=score, level=level, sentiment_score=sentiment,
        intent=intent, intent_strength=strength,
    )


def test_权重常量总和为1():
    assert sum(PLATFORM_WEIGHTS.values()) == 1.0


def test_单平台结果与原公式一致():
    """单平台数据时加权退化为平台内聚合，指数与原公式完全一致（回归保证）。"""
    posts = [make_post("xiaohongshu", 40, sentiment=0.5) for _ in range(5)]
    posts += [make_post("xiaohongshu", 10) for _ in range(5)]
    r = compute_sector_index(posts)
    d = r["details"]
    assert d["newbie_ratio"] == 50.0
    assert d["avg_newbie_score"] == 40.0
    assert d["avg_sentiment"] == 50.0
    assert d["purity_signal"] == 0.0  # score=40 非纯小白(>=50)
    # 50×0.40 + 40×0.25 + 50×0.20 + purity(0)×0.15 = 40.0
    assert r["index"] == 40.0


def test_股吧数量不再主导占比():
    """股吧 200 条(5% 小白) + 小红书 20 条(50% 小白)：
    旧合并法 ratio=9.1（股吧数量主导）；新加权法 = (0.25×5+0.40×50)/0.65 = 32.7。"""
    posts = [make_post("guba", 5) for _ in range(190)]
    posts += [make_post("guba", 60) for _ in range(10)]
    posts += [make_post("xiaohongshu", 5) for _ in range(10)]
    posts += [make_post("xiaohongshu", 60) for _ in range(10)]
    d = compute_sector_index(posts)["details"]
    assert d["newbie_ratio"] == 32.7
    assert d["newbie_ratio"] > 25  # 明显高于旧合并法 9.1，防止实现回退


def test_缺失平台权重归一化():
    """无微博数据时，股吧/小红书权重归一为 0.25/0.65、0.40/0.65。"""
    posts = [make_post("guba", 5) for _ in range(100)]
    posts += [make_post("xiaohongshu", 5) for _ in range(5)]
    posts += [make_post("xiaohongshu", 60) for _ in range(5)]
    d = compute_sector_index(posts)["details"]
    # (0.25×0 + 0.40×50) / 0.65 = 30.8
    assert d["newbie_ratio"] == 30.8


def test_无小白平台占比0但强度缺席():
    """小红书 10 条全是非小白：占比贡献 0（0 填充），但强度样本缺席——
    avg_score 只在有小白样本的平台间加权，不被无样本平台拉低。"""
    posts = [make_post("xiaohongshu", 5) for _ in range(10)]
    posts += [make_post("guba", 5) for _ in range(5)]
    posts += [make_post("guba", 60) for _ in range(5)]
    d = compute_sector_index(posts)["details"]
    # (0.25×50 + 0.40×0) / 0.65 = 19.2
    assert d["newbie_ratio"] == 19.2
    # 唯一有小白样本的平台是 guba → 强度不被 xhs 的 0 稀释
    assert d["avg_newbie_score"] == 60.0


def test_垃圾帖不计入有效样本():
    posts = [make_post("guba", 60) for _ in range(10)]
    posts += [make_post("guba", 5, level="垃圾帖") for _ in range(5)]
    r = compute_sector_index(posts)
    d = r["details"]
    assert d["total_posts"] == 15
    assert d["valid_posts"] == 10
    assert d["newbie_ratio"] == 100.0  # 单平台，垃圾帖不稀释


def test_空数据返回无数据():
    r = compute_sector_index([])
    assert r["index"] == 0
    assert r["interpretation"] == "无数据"


def test_买卖子指数平台聚合():
    """XHS 小白全 buy、股吧小白全 sell：买入/卖出按平台权重合成，计数不淹没。"""
    posts = [make_post("xiaohongshu", 60, intent="buy", strength=0.8)
             for _ in range(10)]
    posts += [make_post("guba", 60, intent="sell", strength=0.6)
              for _ in range(10)]
    d = compute_sector_index(posts)["details"]
    assert d["mom_buy_index"] == 50.1
    assert d["mom_sell_index"] == 33.3
    assert d["buy_sell_ratio"] == 1.2


def test_卖出为零时买卖比使用中性先验():
    posts = [make_post("xiaohongshu", 60, intent="buy", strength=0.5)
             for _ in range(5)]
    posts += [make_post("guba", 60, intent="buy", strength=0.5)
              for _ in range(5)]
    d = compute_sector_index(posts)["details"]
    assert d["buy_sell_ratio"] == 2.0  # sell=0 → 仅表达偏买入，不再返回99.9


def test_买入为零时买卖比使用中性先验():
    posts = [make_post("xiaohongshu", 60, intent="sell", strength=0.5)
             for _ in range(5)]
    posts += [make_post("guba", 60, intent="sell", strength=0.5)
              for _ in range(5)]
    d = compute_sector_index(posts)["details"]
    assert d["buy_sell_ratio"] == 0.5  # buy=0 → 对称地表达偏卖出


def test_无小白时买卖比为零():
    posts = [make_post("guba", 5) for _ in range(10)]
    d = compute_sector_index(posts)["details"]
    assert d["buy_sell_ratio"] == 0.0


def test_小白全观望时买卖比为零():
    """有小白但全是 neutral 意图：无买卖倾向，不应误报买入占优。"""
    posts = [make_post("xiaohongshu", 60, intent="neutral") for _ in range(10)]
    posts += [make_post("guba", 60, intent="neutral") for _ in range(10)]
    d = compute_sector_index(posts)["details"]
    assert d["buy_count"] == 0
    assert d["sell_count"] == 0
    assert d["buy_sell_ratio"] == 0.0


def test_非小白买卖帖不计入买卖计数():
    """展示口径与加权指数一致：buy_count/sell_count 只统计小白帖。"""
    posts = [make_post("guba", 60, intent="buy", strength=0.5) for _ in range(3)]
    posts += [make_post("guba", 5, intent="buy", strength=0.5) for _ in range(7)]
    posts += [make_post("guba", 5, intent="sell", strength=0.5) for _ in range(7)]
    d = compute_sector_index(posts)["details"]
    assert d["buy_count"] == 3  # 非小白(score=5)的 buy/sell 帖不计入
    assert d["sell_count"] == 0
