"""网络明星/公开人物人设蒸馏工具。

把收集到的素材文本（公开采访、直播文字稿、粉丝观察记录、外观与性格描述等）
交给本地大模型，蒸馏成 Character Card V2 人设卡（与 SillyTavern 兼容），
输出到 characters/ 目录后，前端角色列表自动出现。

用法:
    python -m tools.distill_persona --name "星野" --input 素材.txt
    python -m tools.distill_persona --name "星野" --input 素材.md --output characters/xingye.json

注意:
    - 仅使用公开信息或已获授权的素材，不要编造素材中未提供的个人信息。
    - LLM 配置读取项目 config.toml（base_url / model）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 项目根目录

from app.llm import LLMClient, LLMConfig  # noqa: E402


DISTILL_PROMPT = """你是角色卡创作大师，擅长把人物素材蒸馏成高质量的中文 AI 女友角色卡。

请根据以下素材，创作一张 Character Card V2 角色卡。角色名：「{name}」。

素材内容：
=====
{material}
=====

创作要求：
1. 只基于素材中提供的信息提炼，不要编造素材没有的个人细节。
2. 角色卡面向"AI 女友/陪伴"场景：设定一段与用户的亲密关系（如青梅竹马、邻居、同学、同事等，从素材线索中合理推断）。
3. 说话风格要鲜明（口头禅、语气词、称呼用户的习惯），使用自然的中文口语。
4. first_mes 是初次见面开场白，2-4 句，体现性格与关系基调，不要自我介绍式罗列。
5. 若素材提及玩具/身体反应相关的内容，保持含蓄描写。

只输出 JSON（Character Card V2 格式），不要其他文字：
{{
  "spec": "chara_card_v2",
  "data": {{
    "name": "角色显示名（中文昵称）",
    "description": "外貌+背景+与用户的关系设定，200-400 字",
    "personality": "性格特点描述，150-300 字",
    "scenario": "当前互动场景设定，50-150 字",
    "first_mes": "开场白，2-4 句",
    "mes_example": "对话示例（3 轮，用 <START> 分隔用户和角色的消息）",
    "system_prompt": "给 LLM 的补充指令：说话风格、称呼、亲密度等级等，80-200 字",
    "tags": ["中文标签", "如 温柔/傲娇/青梅竹马"]
  }}
}}
"""


async def distill(llm: LLMClient, name: str, material: str) -> dict:
    """调用 LLM 蒸馏角色卡。"""
    reply = ""
    async for delta in llm.stream_chat([
        {"role": "system",
         "content": "你是角色卡创作大师。你输出结构严格的 JSON，不做多余说明。"},
        {"role": "user", "content": DISTILL_PROMPT.format(name=name, material=material)},
    ]):
        reply += delta
    return parse_card(reply)


def parse_card(text: str) -> dict:
    """容错解析 LLM 输出中的 JSON。"""
    t = text.strip()
    if "```" in t:
        t = re.sub(r"^.*?```(?:json)?\s*", "", t, flags=re.S)
        t = re.sub(r"\s*```.*$", "", t, flags=re.S)
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"未找到 JSON：{text[:200]}")
    return json.loads(t[start:end + 1])


def validate(card: dict) -> None:
    """校验角色卡必需字段。"""
    data = card.get("data", card)
    missing = [f for f in ("name", "description", "personality", "first_mes")
               if not str(data.get(f, "")).strip()]
    if missing:
        raise ValueError(f"角色卡缺少必需字段: {', '.join(missing)}")


def slugify(name: str) -> str:
    """生成文件名（保留中文与字母数字）。"""
    s = re.sub(r"[^\w\u4e00-\u9fff-]", "", name)
    return s or "persona"


def load_llm_config() -> LLMConfig:
    """读取 config.toml 的 LLM 配置。"""
    config_path = Path(__file__).resolve().parent.parent / "config.toml"
    if not config_path.exists():
        raise FileNotFoundError(
            "缺少 config.toml（先 cp config.example.toml config.toml 并配置 LLM 地址）")
    import tomllib
    with open(config_path, "rb") as f:
        cfg = tomllib.load(f)
    llm_cfg = cfg.get("llm", {})
    return LLMConfig(
        base_url=llm_cfg.get("base_url", "http://127.0.0.1:11434/v1"),
        api_key=llm_cfg.get("api_key", "ollama"),
        model=llm_cfg.get("model", "qwen2.5:7b-instruct-q4_K_M"),
        temperature=llm_cfg.get("temperature", 0.9),
        max_tokens=llm_cfg.get("max_tokens", 4096),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="网络明星/公开人物人设蒸馏工具")
    parser.add_argument("--name", required=True, help="角色名（中文昵称）")
    parser.add_argument("--input", required=True, help="素材文本文件路径（.txt/.md）")
    parser.add_argument("--output", default=None, help="输出 JSON 路径（默认 characters/<拼音>.json）")
    parser.add_argument("--no-save", action="store_true", help="只打印角色卡不写入文件")
    args = parser.parse_args()

    material = Path(args.input).read_text(encoding="utf-8")
    if len(material) < 50:
        print("⚠ 素材过短（<50 字），蒸馏效果可能很差，建议补充公开资料")

    config = load_llm_config()
    print(f"使用模型: {config.model}（{config.base_url}）蒸馏「{args.name}」…")
    card = asyncio.run(distill(LLMClient(config), args.name, material))
    validate(card)

    output = json.dumps(card, ensure_ascii=False, indent=2)
    if args.no_save:
        print(output)
        return

    out_path = Path(args.output) if args.output else \
        Path(__file__).resolve().parent.parent / "characters" / f"{slugify(args.name)}.json"
    out_path.write_text(output + "\n", encoding="utf-8")
    name = card.get("data", card).get("name", args.name)
    print(f"✓ 角色卡已生成: {out_path}")
    print(f"  角色名: {name} | 标签: {card.get('data', card).get('tags', [])}")
    print("  刷新前端界面即可在角色列表看到新角色。")


if __name__ == "__main__":
    main()
