"""长时记忆：LLM 提取 + 本地相似度检索，JSON 持久化。

记忆按角色隔离存储（memories/<角色文件>.json）。
检索用字符 bigram 集合相似度（对中文友好，零外部依赖）。
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

log = logging.getLogger(__name__)


def _grams(text: str) -> set[str]:
    """字符 bigram 集合（去空白）。"""
    s = re.sub(r"\s+", "", text)
    return {s[i:i + 2] for i in range(max(0, len(s) - 1))}


def sim(a: str, b: str) -> float:
    """Dice 系数：0-1 文本相似度。短文本（<2 字）退化为单字符匹配。"""
    ga, gb = _grams(a), _grams(b)
    if not ga or not gb:
        ca, cb = set(re.sub(r"\s+", "", a)), set(re.sub(r"\s+", "", b))
        if not ca or not cb:
            return 0.0
        return 2.0 * len(ca & cb) / (len(ca) + len(cb))
    return 2.0 * len(ga & gb) / (len(ga) + len(gb))


class MemoryStore:
    def __init__(self, path: Path):
        self.path = path
        self.items: list[dict] = []   # [{"text", "ts", "importance"}]
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self.items = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                log.warning("记忆文件损坏，重置: %s", self.path)
                self.items = []

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.items, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, text: str, importance: int = 3) -> bool:
        """添加记忆；与已有记忆高度相似（>0.8）时合并为更新重要度。返回是否新增。"""
        text = text.strip().strip("。，")
        if not text:
            return False
        new_grams = _grams(text)
        for it in self.items:
            old_grams = _grams(it["text"])
            if not old_grams:
                continue
            coverage = len(new_grams & old_grams) / max(1, len(new_grams))
            if coverage > 0.8 or sim(text, it["text"]) > 0.8:
                it["importance"] = max(it["importance"], importance)
                it["text"] = text if len(text) > len(it["text"]) else it["text"]
                self.save()
                return False
        self.items.append({
            "text": text,
            "ts": time.time(),
            "importance": max(1, min(5, int(importance))),
        })
        self.save()
        return True

    def retrieve(self, query: str, top_k: int = 3) -> list[dict]:
        """按相似度 + 重要度加权检索。"""
        if not query or not self.items:
            return []
        scored = [(sim(query, it["text"]) * (0.6 + 0.1 * it["importance"]), it)
                  for it in self.items]
        scored.sort(key=lambda x: -x[0])
        return [it for s, it in scored[:top_k] if s > 0.08]

    def __len__(self) -> int:
        return len(self.items)


EXTRACT_PROMPT = """从以上对话中提取值得长期记住的用户信息：偏好、习惯、重要事件、关系细节、用户提到的重要事实。
规则：
- 不要提取对话中的寒暄、临时指令、玩具控制命令。
- 每条记忆是完整的一句话（中文）。
- 输出严格 JSON 数组，每项 {"text": "记忆内容", "importance": 1-5}。importance 5 表示非常重要（如健康、重大事件）。
- 只输出 JSON 数组本身，不要多余文字。
"""


async def extract_memories(llm, store: MemoryStore, messages: list[dict]) -> int:
    """让 LLM 从最近对话中提取记忆并入库存。返回新增条数。"""
    if not messages:
        return 0
    try:
        reply = ""
        async for delta in llm.stream_chat(messages + [{"role": "user", "content": EXTRACT_PROMPT}]):
            reply += delta
        # 容错解析：剥离 ```json 围栏，截取首个 [ 到最后的 ]
        text = reply.strip()
        if "```" in text:
            text = re.sub(r"^.*?```(?:json)?\s*", "", text, flags=re.S)
            text = re.sub(r"\s*```.*$", "", text, flags=re.S)
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end == -1:
            log.warning("记忆提取返回格式异常: %s", reply[:200])
            return 0
        items = json.loads(text[start:end + 1])
        added = 0
        for item in items:
            if store.add(item.get("text", "").strip(), int(item.get("importance", 3))):
                added += 1
        log.info("记忆提取完成：新增 %d 条（共 %d 条）", added, len(store))
        return added
    except Exception as exc:
        log.warning("记忆提取失败: %s", exc)
        return 0
