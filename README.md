# buttplug-ai-companion

开源 AI 陪伴 / AI 女友 —— 人设 + 本地大模型 + 语音 + 玩具控制 + 中文适配，全本地运行、隐私优先。

```
┌────────────┐  WebSocket/HTTP   ┌──────────────────┐   OpenAI 兼容   ┌──────────────┐
│ 手机/电脑   │ ◄──────────────► │ FastAPI 后端      │ ◄─────────────► │ Ollama / LM  │
│ 聊天界面    │   SSE 流式聊天    │ (命令解析+安全层)  │                 │ Studio 本地  │
└────────────┘                  └────────┬─────────┘                 │ 大模型        │
                                         │ buttplug v3 JSON (ws)     └──────────────┘
                                         ▼
                                 ┌──────────────┐   蓝牙    ┌──────────┐
                                 │ Intiface     │ ◄───────► │ 玩具(振动)│
                                 │ Central      │           └──────────┘
                                 └──────────────┘
```

## 特性

- **人设卡系统**：Character Card V2 格式（与 SillyTavern 角色卡互通），**多角色切换**——往 `characters/` 目录扔 JSON 卡即自动出现在界面里
- **本地大模型**：OpenAI 兼容接口，Ollama / LM Studio / 任意云端 API 均可
- **流式命令**：模型在回复中写 `[[vibrate 60]]`、`[[pattern 心跳]]`、`[[stop]]`，边生成边执行
- **震动模式库**：心跳 / 波浪 / 脉动 / 爬升 / 颤抖 / 巡航 6 种波形，支持 `[[pattern 名称,强度,时长秒]]`
- **语音输入**：麦克风录音 → faster-whisper 本地转写（中文，隐私无忧）
- **语音输出**：edge-tts 免费中文语音合成
- **长时记忆**：LLM 自动提炼对话要点入库存档（每 8 条消息），聊天时按相关性自动回忆；记忆按角色隔离
- **多重安全层**：强度上限钳制、15 秒看门狗自动停止、安全词（red）、紧急停止按钮
- **手机友好**：响应式界面，手机浏览器局域网访问（录音/聊天/TTS 全支持）

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 本地大模型（推荐 Ollama）

```bash
# 安装 Ollama: https://ollama.com
ollama pull qwen2.5:7b-instruct-q4_K_M   # 中文角色扮演，需 ~6GB 显存
ollama serve                              # 默认监听 http://127.0.0.1:11434
```

无显卡也能跑：`ollama pull qwen2.5:3b`（较慢，体验打折）。也可用 LM Studio 或任何 OpenAI 兼容 API（如 DeepSeek）。

### 3. 玩具中继（Intiface Central）

下载安装 [Intiface Central](https://intiface.com/central/)，启动后点击 **Start**（默认 WebSocket 端口 12345）。
- **无硬件测试**：在 Intiface 设置里启用 *Simulated Devices*，会虚拟出一台可震动的设备
- 蓝牙玩具：把玩具调到配对模式，在 Intiface 中扫描连接

### 4. 启动服务

```bash
cp config.example.toml config.toml   # 按需修改
python -m app.main
```

> **中国网络提示**：语音输入首次使用会从 HuggingFace 下载 whisper 模型（约 480MB），若下载失败，启动前设置镜像：
> ```bash
> # Windows (PowerShell):  $env:HF_ENDPOINT="https://hf-mirror.com"
> # Linux/macOS:           export HF_ENDPOINT=https://hf-mirror.com
> ```

手机访问：同一局域网内，浏览器打开 `http://<电脑IP>:8000`

## 配置

见 `config.example.toml`。关键项：

| 配置 | 说明 |
|---|---|
| `llm.base_url` | Ollama: `http://127.0.0.1:11434/v1`；LM Studio: `http://127.0.0.1:1234/v1` |
| `llm.model` | 模型名（`ollama list` 查看） |
| `safety.max_intensity` | 震动强度上限 0-100，默认 80 |
| `safety.watchdog_seconds` | 无时长命令自动停止秒数，默认 15 |
| `safety.safeword` | 安全词，默认 `red` |
| `persona.character_file` | 人设卡路径，可换成 SillyTavern 角色卡 |
| `memory.enabled` | 长时记忆开关（默认开），记忆存 `memories/` 目录 |

## 自配角色（含"蒸馏"任意人物）

1. 把任意 Character Card V2 JSON 放进 `characters/` 目录（SillyTavern 角色卡可直接导入；`xiaoyu.json`/`lingyin.json` 是内置示例）
2. 界面左上角 🎭 按钮或点头像即可切换，会话历史按角色隔离

**为特定人物做"蒸馏"**：先收集其说话风格、口头禅、性格素材 → 用本地 LLM 整理成 character card 字段（description/personality/mes_example）→ 放入目录即可。示例：要模仿一个"元气学妹"人设，参考 `lingyin.json` 中 `system_prompt` 里"语气俏皮、爱用语气词"的写法。

## 命令语法（模型可用）

| 命令 | 说明 |
|---|---|
| `[[vibrate 40]]` | 振动强度 0-100 |
| `[[vibrate 1,60]]` | 指定马达 1，强度 60 |
| `[[pattern 心跳,60,20]]` | 模式（强度, 时长秒，时长可选） |
| `[[stop]]` | 全部停止 |

模式库：心跳 / 波浪 / 脉动 / 爬升 / 颤抖 / 巡航。强度上限受 `safety.max_intensity` 钳制。

## 安全设计

1. **强度钳制**：LLM 输出 `[[vibrate 100]]` 也会被钳制到 `max_intensity`
2. **看门狗**：不带时长的震动指令 15 秒后自动停止
3. **安全词**：聊天中输入 `red`，不经过 LLM 直接全部停止
4. **紧急停止**：界面常驻 STOP 按钮 + `POST /api/emergency_stop`
5. 命令只由助手消息触发，用户消息中的指令标记被忽略

## 路线图

- [x] MVP：聊天 + 流式命令 + 安全层 + 模拟设备
- [x] 震动模式库（[[pattern]] 波形执行器）
- [x] 语音输入（whisper 本地转写）
- [x] 多角色切换（自配置角色卡）
- [x] 长时记忆（LLM 提取 + 本地相似度检索）
- [ ] 记忆手动增删界面（/api/memory 已有 API）
- [ ] SillyTavern 角色卡 Web 导入
- [ ] Docker 一键部署
- [ ] 真人语音克隆 TTS（替代 edge-tts 音色）

## 免责声明

本项目仅供成人娱乐与学习研究。使用真实玩具前请确认设备兼容性（iostindex.com），并遵守当地法律与平台政策。
