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
- **语音输出**：真人语音克隆（GPT-SoVITS v4，5 秒参考音频零样本克隆）+ edge-tts 免费合成回退；界面 🗣 按钮切换音色（`voices/` 目录扔 wav+txt 即自动出现）
- **长时记忆**：LLM 自动提炼对话要点入库存档（每 8 条消息），聊天时按相关性自动回忆；记忆按角色隔离
- **多重安全层**：强度上限钳制、15 秒看门狗自动停止、安全词（red）、紧急停止按钮
- **手机友好**：响应式界面，手机浏览器局域网访问（录音/聊天/TTS 全支持）

## 项目架构

```
┌──────────────────────────────────────────────────────────────┐
│ 客户端层（浏览器 / 手机，web/index.html）                      │
│   聊天 UI · 麦克风录音 · 角色切换 · 紧急停止按钮              │
└──────────────────────────┬───────────────────────────────────┘
                           │ SSE 流式事件（delta/cmd/error） + REST API
┌──────────────────────────▼───────────────────────────────────┐
│ 服务层（Python FastAPI，app/）                                │
│   main.py      路由、会话管理（按角色隔离）、SSE 流、编排      │
│   llm.py       LLM 客户端（OpenAI 兼容，流式）                │
│   commands.py  [[命令]] 解析 + 安全钳制 + 看门狗              │
│   patterns.py  震动模式引擎（波形调度，10Hz 驱动）            │
│   memory.py    长时记忆（LLM 提取 + 本地相似度检索 + 持久化）  │
│   persona.py   人设卡加载（Character Card V2）                │
│   toy_control.py  Intiface 客户端（buttplug v3 协议）         │
└───────┬──────────────┬──────────────────┬────────────────────┘
        │              │                  │
   OpenAI 兼容 API   WebSocket JSON     edge-tts（合成）
   （Ollama/LM Studio/（玩具控制）      faster-whisper（识别）
   云端任意）            │
                   Intiface Central
                        │ 蓝牙
                    玩具设备
```

**数据流**：用户消息 →（语音则先 STT 转文字）→ 注入长期记忆与系统提示词 → LLM 流式生成 → 实时解析 `[[命令]]` → 安全钳制 → 执行（振动/模式/停止）→ 文本流回前端（可 TTS 播报）。

**安全层位置**：所有命令在到达玩具前必须经过 `commands.py` 的钳制（强度上限）与看门狗（超时自动停），安全词与紧急停止直接绕过 LLM 通道。

## 技术栈与第三方依赖

| 类别 | 技术/依赖 | 用途 |
|---|---|---|
| 语言 | Python 3.9+ | 后端 |
| Web 框架 | FastAPI + uvicorn | REST/SSE 服务 |
| WebSocket | websockets | 与 Intiface Central 通信 |
| LLM | openai SDK（OpenAI 兼容接口） | 接入 Ollama / LM Studio / 任意云端模型 |
| 语音合成 | edge-tts | 中文 TTS 播报（免费，微软语音） |
| 语音识别 | faster-whisper | 本地中文转写（CTranslate2 加速，无需 GPU） |
| 配置 | tomli / tomllib | TOML 配置解析 |
| 前端 | 原生 HTML/CSS/JS | 单页响应式，零前端构建链 |

**外部程序（需自行安装）**：

| 程序 | 用途 | 获取方式 |
|---|---|---|
| Ollama（或 LM Studio） | 本地大模型运行时 | ollama.com |
| Intiface Central | 玩具中继（蓝牙管理 + WebSocket 端口） | intiface.com/central |
| ffmpeg | faster-whisper 解码音频 | winget / 官网 |

**可选**：无显卡时也可用任意 OpenAI 兼容云端 API（如 DeepSeek）替代本地模型。

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

1. 把任意 Character Card V2 JSON 放进 `characters/` 目录（SillyTavern 角色卡可直接导入；`xiaoyu.json`/`lingyin.json` 是内置示例），或在界面 🎭 菜单里点 **"＋ 导入角色卡"** 直接上传
2. 界面左上角 🎭 按钮或点头像即可切换，会话历史按角色隔离

**一键蒸馏工具**（把收集的素材交给本地 LLM 自动生成完整角色卡）：

```bash
# 先收集公开素材（采访、直播文字稿、粉丝观察记录等）到 .txt/.md 文件
python -m tools.distill_persona --name "星野" --input 素材.txt
# 生成 characters/星野.json，刷新页面即出现在角色列表
# 更多选项：--output 自定义路径 / --no-save 只打印不落盘
```

> 蒸馏只基于素材中提供的信息，不会编造素材没有的个人细节；请仅使用公开信息或已获授权的素材。手工微调方法：把素材用本地 LLM 整理成 character card 字段（description/personality/mes_example）→ 放入目录即可。示例：要模仿一个"元气学妹"人设，参考 `lingyin.json` 中 `system_prompt` 里"语气俏皮、爱用语气词"的写法。

**记忆连续性**：记忆按角色隔离持久化（`memories/` 目录），切换角色再切回，该角色的记忆完整保留；界面右上角 🧠 按钮可查看/删除/清空记忆。

## 真人语音克隆（GPT-SoVITS）

零样本克隆只需 **5~10 秒参考音频**，无需训练，全本地运行：

1. **部署 GPT-SoVITS v4**（一次性）：参考 [官方仓库](https://github.com/RVC-Boss/GPT-SoVITS)，需 Python 3.10~3.12 + 任意 4GB+ 显存显卡；预训练模型约 1.7GB
2. **启动 API 服务**：
   ```bash
   cd GPT-SoVITS
   python api.py -s GPT_SoVITS/pretrained_models/gsv-v4-pretrained/s2Gv4.pth \
                 -g GPT_SoVITS/pretrained_models/s1v3.ckpt \
                 -dr ref.wav -dt "参考音频文本" -dl zh -p 9880
   ```
3. **添加音色**：把目标声音的 wav（5~10 秒，清晰人声）+ 同名 txt（该音频的逐字文本）放进本项目 `voices/` 目录，界面 🗣 按钮里即可切换
4. 本项目的 `[tts]` 配置：`engine = "auto"`（GPT-SoVITS 不可用时自动回退 edge-tts）

> Windows 提示：若 `pip install torchaudio` 后报 TorchCodec 错误，请用 `pip install torch==2.7.0 torchaudio==2.7.0 --index-url https://download.pytorch.org/whl/cu128` 降级（torchaudio 2.9+ 在 Windows 强制要求无 Windows 版的 torchcodec）。

## 支持的国产品牌（采购参考）

本项目（通过 Buttplug 生态）对国产品牌支持良好，约 1/3 的协议实现来自中国厂商。国产品牌性价比高、淘宝/京东官方旗舰店直购，无需海淘。

### 第一档：生态最完善（价格中等，支持最稳）

| 品牌 | 产地 | 支持程度 | 代表型号 | 价格区间 |
|---|---|---|---|---|
| **Lovense（恋梦）** | 深圳 | 全生态最佳（协议族最全，App/API 完整） | Max 2（飞机杯）、Lush 3、Nora、Edge | ¥300-700 |
| **Hismith（嗨丝）** | 深圳 | 完善（含 Mini 系列） | 炮机/台机全系、KGoal | ¥600-3000 |

> Lovense 虽是深圳公司但按国际定价，不算"便宜"。它是**支持最稳**的选择：社区逆向投入最多、固件更新最勤、功能最全（旋转/充气/加速度传感器/远程模式全支持）。

### 第二档：真·性价比（¥50-300，社区逆向成熟）

| 品牌 | 产地 | 代表型号 | 价格区间 |
|---|---|---|---|
| **Magic Motion（魔动）** | 深圳 | Zenith、Kegel Master、Ponder | ¥50-200 |
| **Svakom（司沃康）** | 深圳 | 各系列振动器 | ¥100-300 |
| **Zalo** | 深圳 | 设计款振动器 | ¥100-300 |
| **Utimi（优提咪）** | 中国 | 振动器 | ¥50-150 |
| **TryFun（趣玩）** | 中国 | 振动器 | ¥50-150 |
| **Xibao（喜宝）** | 中国 | 振动器 | ¥50-150 |
| **Galaku** | 深圳 | 振动器 | ¥100-300 |
| **Mizz Zee** | 深圳 | 振动器 | ¥50-150 |
| **PicoBong（彼趣）** | 中国 | 设计款 | ¥100-300 |
| **Lovedistance（爱距离）** | 上海 | 远程情侣玩具 | ¥200-400 |
| **Deepsire（深润）** | 深圳 | 飞机杯 | ¥100-300 |
| **Sensee（申势）** | 上海 | 振动器 | ¥100-300 |

### 第三档：炮机/特殊品类

Sexverse、Fredoritch、KGoal（凯格尔训练）、Omobo、Cupido、Ankni 等——均有协议实现，但社区资料较少。

### 购买注意事项

- **协议代际**：国产玩具固件更新频繁，同一型号新旧批次可能使用不同协议版本（如 Magic Motion v1→v4），购买时尽量选近期批次
- **型号级核对**：下单前到 [iostindex.com](https://iostindex.com) 搜索型号，确认 "Buttplug.io Support" 状态
- **避坑**：杂牌贴牌型号逆向质量参差，优先表格中的成熟品牌
- **零成本试跑**：先用 Intiface Central 内置模拟设备跑通全流程，再购买

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
- [x] 记忆管理界面（查看/删除/清空/手动添加）
- [x] SillyTavern 角色卡 Web 导入（界面 🎭 → 导入角色卡）
- [x] 真人语音克隆 TTS（GPT-SoVITS v4 集成，voices/ 音色管理，edge-tts 回退）
- [ ] Docker 一键部署

## 免责声明

本项目仅供成人娱乐与学习研究。使用真实玩具前请确认设备兼容性（iostindex.com），并遵守当地法律与平台政策。
