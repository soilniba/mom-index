"""
semantic_classifier.py — DeepSeek 语义分类器

把每个板块的帖子批量发给 deepseek-v4-flash 做语义分类，返回结构化结果。
任何失败（无 key/网络/解析）返回 None，调用方回退关键词规则，不影响采集。

key 与 base_url 从环境变量读取（~/.config/mom-index/env 或 shell 环境）：
  DEEPSEEK_API_KEY / DEEPSEEK_OPENAI_URL / LLM_MODEL
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

API_KEY = ""
BASE_URL = ""
MODEL = ""

ENV_FILE = Path("~/.config/mom-index/env").expanduser()
KEY_VAR = "DEEPSEEK_API_KEY"
URL_VAR = "DEEPSEEK_OPENAI_URL"
MODEL_VAR = "LLM_MODEL"
DEFAULT_MODEL = "deepseek-v4-flash"

BATCH_SIZE = 30       # 每请求帖子数（控制输出 token）
MAX_CONTENT = 200     # 帖子正文截断长度
TIMEOUT = 120         # 单请求超时（秒）


def _load_config() -> None:
    """从环境变量或 env 文件加载配置（只调用一次）。"""
    global API_KEY, BASE_URL, MODEL
    if API_KEY:
        return
    API_KEY = os.environ.get(KEY_VAR, "")
    BASE_URL = os.environ.get(URL_VAR, "https://api.deepseek.com")
    MODEL = os.environ.get(MODEL_VAR, DEFAULT_MODEL)
    if API_KEY:
        return
    # env 文件兜底（systemd 定时任务无 shell 环境）
    r = subprocess.run(
        ["bash", "-c", f"source {ENV_FILE} 2>/dev/null; printf '%s' \"${KEY_VAR}\""],
        capture_output=True, text=True,
    )
    API_KEY = r.stdout.strip()


SECTOR_DESC = {
    "nasdaq": "美股/纳斯达克/纳指",
    "gold": "黄金/金条/积存金",
    "cpo": "CPO/光模块/通信",
    "semiconductor": "芯片/半导体",
}

SYSTEM_PROMPT = """你是「宝妈指数」项目的帖子分类器：判断中文投资/理财帖子反映的是「散户小白情绪」还是「专业分析/内容创作」，输出结构化 JSON。

判定分数 newbie_score（0-100），与阈值强相关：
- >=50：明确小白——不懂、焦虑、依赖他人决策（"还能买吗""该不该买""亏了怎么办""新手怎么买"）
- 20-49：有小白特征但不明显
- <20：非小白——专业分析、教学/攻略/科普内容创作、新闻资讯

最重要规则（务必遵守）：
0. **禁止编造**：只能根据提供的文本（title/content）判断。正文为空时只依据标题，不得想象或推断标题中不存在的细节（如"收评""资讯""教学"等字眼必须是标题里真实出现的）。reasoning 必须能被标题文字支持。
1. 区分作者身份：博主写「教学/攻略/入门/科普/解读/拆解/一图流/保姆级/讲明白/先听懂」是内容创作（低分），散户提问/倾诉是小白情绪（高分）。标题里的"新手/小白"常是教程的目标读者，不是作者身份。
2. **情绪宣泄 ≠ 小白**：骂街/抱怨/诅咒（"烂鳖玩意""恶心""赶紧解散吧""杀猪盘""给我涨上去"）是有一定认知的人在发泄，不是小白。小白必须是**认知层面不懂**：问基础概念（"为什么停牌""有手续费吗""是买是卖"）、依赖他人决策（"还能买吗""该不该卖"）、跟风（"听说/博主说"）、被套不知怎么办。情绪宣泄的帖子 newbie_score 压低，情绪记入 sentiment 字段。
3. 隐含求助信号：短问句（"企稳了吗""还能涨吗"）、"还能不能/要不要/该不该/能不能"、"亏了/割肉/套牢"、"跟别人买/听说/博主说"。但这些必须与「作者是否懂行情」结合判断：懂行情的人在发泄（低分），不懂的人在求助（高分）。
4. 专业术语+分析口吻（PE/定投/仓位/基本面/财报/加息/资产配置/溢价）降分。
5. 垃圾帖（广告/签到/领金条/打卡/无意义）→ newbie_score=0 且 is_spam=true。

判别口诀（对每个帖子先回答：作者是「求助/倾诉/教别人/发泄」哪一类？）：
- 求助（认知不懂）= 小白高分："怎么买呀各位大佬"、"还能买吗"、"为什么停牌了"、"这个有手续费吗"
- 倾诉个人经历/困惑（认知不懂）= 小白高分："亏麻了怎么办"、"新手第2天"、"要不要止损"
- 教别人 = 内容创作低分："新手小白买黄金必看攻略"、"三步教你定投"、"一篇讲明白CPO"、"先听懂10个词"
- 发泄（懂行情的人在骂）= 低分："快砸下来！砸到溢价低于10个点"（懂溢价率）、"烂鳖玩意"、"杀猪盘"
- 即使标题不含问句，只要口吻是个人困惑/经历也算小白；口吻是客观教学/资讯/发泄就是内容创作或非小白

sentiment：-1（恐慌）~ +1（贪婪）。
intent：buy/sell/neutral，intent_strength 0-1。
reasoning：一句中文，说明判定核心原因。
key_signals：1-3 个中文特征短语（前端展示用，如"身份自述""决策依赖""教学科普内容"）。"""

USER_TMPL = """板块：{sector}（关注 {desc}）

帖子列表（JSON 数组，每元素 {{"id": 帖子id, "title": 标题, "content": 正文截断}}）：
{posts}

只输出 JSON（不要 markdown 代码块、不要其他文字），格式：
{{"posts": [{{"id": "帖子id", "newbie_score": 0-100, "is_spam": true或false, "sentiment": -1到1, "intent": "buy|sell|neutral", "intent_strength": 0-1, "reasoning": "一句中文原因", "key_signals": ["特征1", "特征2"]}}]}}"""


def _call_llm(batch: List[Dict], sector: str) -> Optional[List[Dict]]:
    """单次 LLM 调用。返回与 batch 等长的分类结果，失败返回 None。"""
    import requests

    _load_config()
    if not API_KEY:
        print("[semantic] 未配置 DEEPSEEK_API_KEY", file=sys.stderr)
        return None

    payload = json.dumps([
        {"id": p.get("id", ""), "title": p.get("title", ""),
         "content": (p.get("content") or "")[:MAX_CONTENT]}
        for p in batch
    ], ensure_ascii=False)

    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_TMPL.format(
                sector=sector,
                desc=SECTOR_DESC.get(sector, sector),
                posts=payload,
            )},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    try:
        resp = requests.post(
            BASE_URL.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}"},
            json=body, timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        parsed = json.loads(text)
        items = parsed.get("posts")
        if not isinstance(items, list):
            print("[semantic] LLM 返回格式异常: posts 非数组", file=sys.stderr)
            return None
        return items
    except Exception as e:
        print(f"[semantic] LLM 调用失败: {e}", file=sys.stderr)
        return None


def classify_sector(posts: List[Dict], sector: str) -> Optional[List[Dict]]:
    """批量分类一个板块。返回与 posts 等长的 [{id, newbie_score, ...}]，任何失败返回 None。

    结果按输入顺序排列；LLM 缺失项用该帖降级为 0 分占位（调用方回退规则时只认 None）。
    """
    if not posts:
        return []
    results: Dict[str, Dict] = {}
    for i in range(0, len(posts), BATCH_SIZE):
        batch = posts[i:i + BATCH_SIZE]
        items = _call_llm(batch, sector)
        if items is None:
            return None
        for item in items:
            if isinstance(item, dict) and item.get("id"):
                results[str(item["id"])] = item
    out = []
    for p in posts:
        item = results.get(str(p.get("id", "")))
        if item is None:
            return None  # 有帖缺失 → 整板块回退
        out.append(item)
    return out
