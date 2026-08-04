"""Character Card V2 人设卡加载（兼容 SillyTavern 角色卡格式）。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Persona:
    """解析后的人设数据。"""

    name: str
    description: str = ""
    personality: str = ""
    scenario: str = ""
    first_mes: str = ""
    mes_example: str = ""
    system_prompt: str = ""
    post_history_instructions: str = ""
    alternate_greetings: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    def build_system_prompt(self) -> str:
        """将人设字段组装成发给 LLM 的系统提示词。"""
        parts = [f"你的角色名是「{self.name}」。"]
        if self.description:
            parts.append(f"【角色设定】{self.description}")
        if self.personality:
            parts.append(f"【性格】{self.personality}")
        if self.scenario:
            parts.append(f"【当前场景】{self.scenario}")
        if self.system_prompt:
            parts.append(self.system_prompt)
        if self.post_history_instructions:
            parts.append(self.post_history_instructions)
        return "\n\n".join(parts)


def load_persona(path: str | Path) -> Persona:
    """从 Character Card V2 JSON 文件加载人设。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"人设卡文件不存在: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))

    # 支持 v2 (data 字段) 与旧版扁平格式
    card = data.get("data", data) if data.get("spec", "").startswith("chara_card") else data

    return Persona(
        name=card.get("name", "未命名"),
        description=card.get("description", ""),
        personality=card.get("personality", ""),
        scenario=card.get("scenario", ""),
        first_mes=card.get("first_mes", ""),
        mes_example=card.get("mes_example", ""),
        system_prompt=card.get("system_prompt", ""),
        post_history_instructions=card.get("post_history_instructions", ""),
        alternate_greetings=card.get("alternate_greetings", []),
        tags=card.get("tags", []),
        raw=card,
    )
