
# AI Agent System

一个功能完整的 AI 代理系统，支持工具调用、子代理、任务管理、智能上下文压缩、团队协作等功能。

## 特性

- **工具调用**：bash、文件读写编辑等
- **子代理/队友管理**：支持生成自主队友协同工作
- **任务管理**：待办事项、持久化任务跟踪
- **智能上下文压缩**：Session Memory + microcompact 智能压缩
- **后台任务管理**：异步任务执行和状态跟踪
- **消息总线**：团队内部消息通信
- **可选安全模块**：提示词安全扫描
- **技能系统**：可加载的专业领域知识

## 快速开始

```bash
# 安装依赖
pip install requests python-dotenv

# 配置环境变量
cp .env.example .env
# 编辑 .env 设置 Ollama 地址和模型

# 运行
python s_full.py
```

## 环境变量

- `OLLAMA_BASE_URL`：Ollama 服务地址，默认 `http://localhost:11434`
- `OLLAMA_MODEL`：使用的模型，默认 `qwen3:8b`
- `SECURITY_STRICT`：是否启用严格模式，默认 `false`
- `SECURITY_WARNING_ONLY`：是否仅警告模式，默认 `true`

## REPL 命令

- `/compact` - 手动压缩上下文
- `/tasks` - 查看任务列表
- `/team` - 查看队友状态
- `/inbox` - 查看收件箱
- `/security` - 查看安全模块状态
- `/stats` - 查看系统统计
- `q` / `exit` - 退出

## 项目结构

```
.
├── s_full.py              # 主程序入口
├── message_invariants.py   # 消息完整性校验
├── prompt_security.py       # 提示词安全模块
├── memory_*.py              # 记忆相关模块
├── skills/                 # 技能目录
│   ├── agent-builder/    # 代理构建技能
│   ├── code-review/      # 代码审查技能
│   ├── pdf/               # PDF 处理技能
│   └── mcp-builder/     # MCP 构建技能
└── .claude/                # Claude 配置目录
```

## 核心模块说明

### s_full.py
主程序，包含：
- Ollama 客户端封装
- 工具调用系统
- 子代理系统
- 任务管理器
- 队友管理器
- 消息总线
- 后台任务管理
- 上下文压缩

### memory_*.py
记忆系统模块：
- `memory_storage.py` - 记忆存储
- `memory_state.py` - 记忆状态管理
- `memory_extract.py` - 记忆提取
- `memory_compact.py` - 记忆压缩
- `memory_prompt.py` - 记忆提示词

### message_invariants.py
确保工具调用和结果配对的完整性校验。

### prompt_security.py
提示词安全扫描，过滤高风险输入。

## 技能系统

技能放置在 `skills/` 目录下，每个技能是一个包含 `SKILL.md` 的文件夹。

## License

MIT
