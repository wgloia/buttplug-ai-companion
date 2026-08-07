"""临时测试用 mock LLM 服务器（OpenAI 兼容 /v1/chat/completions，流式）。"""
import json
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

mock = FastAPI()

FIXED_REPLY = "宝贝～你今天真可爱，让我陪陪你吧。[[vibrate 40]] 嗯…要不要试试心跳模式？[[pattern 心跳,60,10]] 这样舒服吗？[[stop]] 好啦，先到这。"

MEMORY_REPLY = '[{"text": "用户喜欢草莓味的甜点", "importance": 3}, {"text": "用户养了一只叫豆豆的猫", "importance": 4}]'

SELFIE_REPLY = "好呀～那你等我一下哦。[[selfie 穿着黑色睡衣躺在卧室床上]]"

DISTILL_REPLY = json.dumps({
    "spec": "chara_card_v2",
    "data": {
        "name": "小雨",
        "description": "21 岁的女孩，性格温柔害羞，轻声细语，喜欢草莓味的甜点。",
        "personality": "温柔、害羞、体贴，说话总是带着尾音。",
        "scenario": "在温馨的小房间里初次见面。",
        "first_mes": "你…你好呀，我叫小雨。初次见面，请多关照～",
        "mes_example": "<START>\n{{user}}: 你好呀\n{{char}}: 嗯嗯，你好～今天过得怎么样？",
        "system_prompt": "说话轻声细语，称呼用户为哥哥。",
        "tags": ["温柔", "害羞"],
    }
}, ensure_ascii=False)


@mock.post("/v1/chat/completions")
async def completions(request: Request):
    # 记忆提取请求：最后一条消息含"长期记忆"指令时返回记忆 JSON
    body = await request.json()
    last_msg = body["messages"][-1]["content"] if body.get("messages") else ""
    if "值得长期记住" in last_msg:
        reply = MEMORY_REPLY
    elif "Character Card V2 格式" in last_msg or "角色卡创作" in last_msg:
        reply = DISTILL_REPLY
    elif "自拍" in last_msg or "拍张照" in last_msg or "照片" in last_msg:
        reply = SELFIE_REPLY
    else:
        reply = FIXED_REPLY
    async def gen():
        yield "data: " + json.dumps({
            "id": "mock-1", "object": "chat.completion.chunk", "model": "mock",
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]
        }) + "\n\n"
        for i in range(0, len(reply), 5):
            yield "data: " + json.dumps({
                "id": "mock-1", "object": "chat.completion.chunk", "model": "mock",
                "choices": [{"index": 0, "delta": {"content": reply[i:i + 5]}, "finish_reason": None}]
            }) + "\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(mock, host="127.0.0.1", port=9999, log_level="warning")
