"""临时测试用 mock LLM 服务器（OpenAI 兼容 /v1/chat/completions，流式）。"""
import json
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

mock = FastAPI()

FIXED_REPLY = "宝贝～你今天真可爱，让我陪陪你吧。[[vibrate 40]] 嗯…要不要试试心跳模式？[[pattern 心跳,60,10]] 这样舒服吗？[[stop]] 好啦，先到这。"


@mock.post("/v1/chat/completions")
async def completions():
    async def gen():
        yield "data: " + json.dumps({
            "id": "mock-1", "object": "chat.completion.chunk", "model": "mock",
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]
        }) + "\n\n"
        for i in range(0, len(FIXED_REPLY), 5):
            yield "data: " + json.dumps({
                "id": "mock-1", "object": "chat.completion.chunk", "model": "mock",
                "choices": [{"index": 0, "delta": {"content": FIXED_REPLY[i:i + 5]}, "finish_reason": None}]
            }) + "\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(mock, host="127.0.0.1", port=9999, log_level="warning")
