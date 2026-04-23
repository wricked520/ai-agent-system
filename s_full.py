#!/usr/bin/env python3
"""
s_full.py - 完整功能的 AI 代理

特性：
- 工具调用 (bash, file operations, etc.)
- 子代理/队友管理
- 任务追踪与待办事项
- 智能上下文压缩 (Session Memory + microcompact)
- 后台任务管理
- 消息总线与团队协作
- 可选的安全模块

REPL 命令: /compact, /tasks, /team, /inbox, /security, /stats, q(exit)
"""

import json
import os
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path
from queue import Queue

import requests
from dotenv import load_dotenv

try:
    from prompt_security import PromptSecurity, OutputValidator, RiskLevel
    SECURITY_ENABLED = True
except ImportError:
    SECURITY_ENABLED = False
    print("⚠️  安全模块未找到，将在没有安全检查的情况下运行")

load_dotenv(override=True)

WORKDIR = Path.cwd()
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")

SECURITY_STRICT_MODE = os.getenv("SECURITY_STRICT", "false").lower() == "true"
SECURITY_WARNING_ONLY = os.getenv("SECURITY_WARNING_ONLY", "true").lower() == "true"


class OllamaClient:
    """Ollama 客户端包装类"""

    def __init__(self, base_url=OLLAMA_BASE_URL):
        self.base_url = base_url

    def messages_create(self, model, messages, system=None, tools=None, max_tokens=2000):
        """
        调用 Ollama API 生成响应

        返回格式模拟 Anthropic 响应结构
        """
        url = f"{self.base_url}/api/generate"

        full_prompt = ""
        if system:
            full_prompt += f"System: {system}\n\n"

        for msg in messages:
            role = msg["role"]
            content = msg["content"]

            if isinstance(content, list):
                text_parts = []
                for part in content:
                    if isinstance(part, dict):
                        if part.get("type") == "text":
                            text_parts.append(part.get("text", ""))
                        elif part.get("type") == "tool_result":
                            text_parts.append(f"[tool_result: {part.get('tool_use_id')}]\n{part.get('content', '')}")
                content = "\n".join(text_parts)

            full_prompt += f"{role.capitalize()}: {content}\n"

        full_prompt += "Assistant: "

        payload = {
            "model": model,
            "prompt": full_prompt,
            "stream": False,
            "options": {
                "num_predict": max_tokens
            }
        }

        try:
            resp = requests.post(url, json=payload, timeout=120)
            resp.raise_for_status()
            result = resp.json()
            return self._parse_response(result, tools)
        except Exception as e:
            return SimpleResponse([SimpleTextBlock(f"Error: {str(e)}")], "error")

    def _parse_response(self, result, tools):
        """解析 Ollama 响应，提取工具调用"""
        content = result.get("response", "") or result.get("message", {}).get("content", "")

        tool_blocks = []
        text_blocks = []

        tool_match = re.search(r"<\|tool_call\|>(.*?)<\|/tool_call\|>", content, re.DOTALL)
        if tool_match:
            try:
                tool_calls = json.loads(tool_match.group(1))
                if isinstance(tool_calls, list):
                    for call in tool_calls:
                        tool_blocks.append(SimpleToolUseBlock(
                            name=call.get("name"),
                            input=call.get("input", {})
                        ))
                content = re.sub(r"<\|tool_call\|>.*?<\|/tool_call\|>", "", content, flags=re.DOTALL).strip()
            except Exception:
                pass

        if content:
            text_blocks.append(SimpleTextBlock(content))

        all_blocks = text_blocks + tool_blocks
        stop_reason = "tool_use" if tool_blocks else "end_turn"

        return SimpleResponse(all_blocks, stop_reason)


class SimpleResponse:
    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason


class SimpleTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class SimpleToolUseBlock:
    def __init__(self, name, input):
        self.type = "tool_use"
        self.id = f"tool_{uuid.uuid4().hex[:8]}"
        self.name = name
        self.input = input


client = OllamaClient()
client.messages = type('', (), {})()
client.messages.create = client.messages_create

TEAM_DIR = WORKDIR / ".team"
INBOX_DIR = TEAM_DIR / "inbox"
TASKS_DIR = WORKDIR / ".tasks"
SKILLS_DIR = WORKDIR / "skills"
TRANSCRIPT_DIR = WORKDIR / ".transcripts"
MEMORY_DIR = WORKDIR / ".memory"
TOKEN_THRESHOLD = 100000
POLL_INTERVAL = 5
IDLE_TIMEOUT = 60

MICROCOMPACT_KEEP_RECENT_TOOL_RESULTS = 3
MICROCOMPACT_KEEP_RECENT_ROUNDS = 5
MICROCOMPACT_TIME_THRESHOLD_SECS = 300
MICROCOMPACT_MAX_TOOL_RESULT_LEN = 2000

VALID_MSG_TYPES = {"message", "broadcast", "shutdown_request",
                   "shutdown_response", "plan_approval_response"}


def safe_path(p: str) -> Path:
    """安全路径验证"""
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def run_bash(command: str) -> str:
    """安全执行 bash 命令"""
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(command, shell=True, cwd=WORKDIR,
                           capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"


def run_read(path: str, limit: int = None) -> str:
    """读取文件内容"""
    try:
        lines = safe_path(path).read_text().splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more)"]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Error: {e}"


def run_write(path: str, content: str) -> str:
    """写入文件内容"""
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    """编辑文件内容（查找替换）"""
    try:
        fp = safe_path(path)
        c = fp.read_text()
        if old_text not in c:
            return f"Error: Text not found in {path}"
        fp.write_text(c.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


class TodoManager:
    """待办事项管理器"""

    def __init__(self):
        self.items = []

    def update(self, items: list) -> str:
        """更新待办事项列表"""
        validated, ip = [], 0
        for i, item in enumerate(items):
            content = str(item.get("content", "")).strip()
            status = str(item.get("status", "pending")).lower()
            af = str(item.get("activeForm", "")).strip()
            if not content:
                raise ValueError(f"Item {i}: content required")
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"Item {i}: invalid status '{status}'")
            if not af:
                raise ValueError(f"Item {i}: activeForm required")
            if status == "in_progress":
                ip += 1
            validated.append({"content": content, "status": status, "activeForm": af})
        if len(validated) > 20:
            raise ValueError("Max 20 todos")
        if ip > 1:
            raise ValueError("Only one in_progress allowed")
        self.items = validated
        return self.render()

    def render(self) -> str:
        """渲染待办事项列表"""
        if not self.items:
            return "No todos."
        lines = []
        for item in self.items:
            m = {"completed": "[x]", "in_progress": "[>]", "pending": "[ ]"}.get(item["status"], "[?]")
            suffix = f" <- {item['activeForm']}" if item["status"] == "in_progress" else ""
            lines.append(f"{m} {item['content']}{suffix}")
        done = sum(1 for t in self.items if t["status"] == "completed")
        lines.append(f"\n({done}/{len(self.items)} completed)")
        return "\n".join(lines)

    def has_open_items(self) -> bool:
        """检查是否有未完成事项"""
        return any(item.get("status") != "completed" for item in self.items)


def run_subagent(prompt: str, agent_type: str = "Explore") -> str:
    """运行子代理执行任务"""
    sub_tools = [
        {"name": "bash", "description": "Run command.",
         "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
        {"name": "read_file", "description": "Read file.",
         "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    ]
    if agent_type != "Explore":
        sub_tools += [
            {"name": "write_file", "description": "Write file.",
             "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
            {"name": "edit_file", "description": "Edit file.",
             "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
        ]
    sub_handlers = {
        "bash": lambda **kw: run_bash(kw["command"]),
        "read_file": lambda **kw: run_read(kw["path"]),
        "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
        "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    }
    sub_msgs = [{"role": "user", "content": prompt}]
    resp = None
    for _ in range(30):
        resp = client.messages.create(model=MODEL, messages=sub_msgs, tools=sub_tools, max_tokens=8000)
        content_serializable = []
        for b in resp.content:
            if hasattr(b, "type") and b.type == "text":
                content_serializable.append({"type": "text", "text": b.text})
            elif hasattr(b, "type") and b.type == "tool_use":
                content_serializable.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
        sub_msgs.append({"role": "assistant", "content": content_serializable})
        if resp.stop_reason != "tool_use":
            break
        results = []
        for b in resp.content:
            if hasattr(b, "type") and b.type == "tool_use":
                h = sub_handlers.get(b.name, lambda **kw: "Unknown tool")
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": str(h(**b.input))[:50000]})
        sub_msgs.append({"role": "user", "content": results})
    if resp:
        return "".join(b.text for b in resp.content if hasattr(b, "text")) or "(no summary)"
    return "(subagent failed)"


class SkillLoader:
    """技能加载器"""

    def __init__(self, skills_dir: Path):
        self.skills = {}
        if skills_dir.exists():
            for f in sorted(skills_dir.rglob("SKILL.md")):
                text = f.read_text()
                match = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
                meta, body = {}, text
                if match:
                    for line in match.group(1).strip().splitlines():
                        if ":" in line:
                            k, v = line.split(":", 1)
                            meta[k.strip()] = v.strip()
                    body = match.group(2).strip()
                name = meta.get("name", f.parent.name)
                self.skills[name] = {"meta": meta, "body": body}

    def descriptions(self) -> str:
        """获取所有技能描述"""
        if not self.skills:
            return "(no skills)"
        return "\n".join(f"  - {n}: {s['meta'].get('description', '-')}" for n, s in self.skills.items())

    def load(self, name: str) -> str:
        """加载指定技能"""
        s = self.skills.get(name)
        if not s:
            return f"Error: Unknown skill '{name}'. Available: {', '.join(self.skills.keys())}"
        return f"<skill name=\"{name}\">\n{s['body']}\n</skill>"


def estimate_tokens(messages: list) -> int:
    """估算消息 token 数量（JSON长度/4）"""
    return len(json.dumps(messages, default=str)) // 4


def microcompact(messages: list):
    """
    智能微压缩

    策略：
    1. 时间策略：超过时间阈值的旧工具结果
    2. 轮次策略：只保留最近 N 个完整轮次内的工具结果
    3. 数量策略：最多保留最近 N 个工具结果
    4. 长度策略：超长工具结果截断
    5. 重要性策略：保留错误信息和短结果
    """
    import time
    now = time.time()

    if not messages:
        return

    last_assistant_idx = -1
    last_assistant_time = 0
    for i in reversed(range(len(messages))):
        if messages[i]["role"] == "assistant":
            last_assistant_idx = i
            last_assistant_time = messages[i].get("created_at", 0)
            break

    if last_assistant_idx < 0:
        _microcompact_simple(messages)
        return

    rounds_to_keep = MICROCOMPACT_KEEP_RECENT_ROUNDS
    round_start_idx = 0
    assistant_count = 0
    for i in reversed(range(last_assistant_idx + 1)):
        if messages[i]["role"] == "assistant":
            assistant_count += 1
            if assistant_count > rounds_to_keep:
                round_start_idx = i + 1
                break

    all_tool_results = []
    for msg_idx, msg in enumerate(messages):
        if msg["role"] == "user" and isinstance(msg.get("content"), list):
            msg_time = msg.get("created_at", 0)
            is_old_round = msg_idx < round_start_idx
            is_time_expired = (now - msg_time) > MICROCOMPACT_TIME_THRESHOLD_SECS

            for part_idx, part in enumerate(msg["content"]):
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    all_tool_results.append({
                        "msg_idx": msg_idx,
                        "part_idx": part_idx,
                        "part": part,
                        "created_at": msg_time,
                        "is_old_round": is_old_round,
                        "is_time_expired": is_time_expired
                    })

    if not all_tool_results:
        return

    to_keep = set()
    keep_count = 0
    for tr in reversed(all_tool_results):
        if keep_count < MICROCOMPACT_KEEP_RECENT_TOOL_RESULTS:
            to_keep.add((tr["msg_idx"], tr["part_idx"]))
            keep_count += 1
        elif not tr["is_old_round"] and not tr["is_time_expired"]:
            content = tr["part"].get("content", "")
            content_str = str(content).lower()
            if "error" in content_str or "exception" in content_str or "fail" in content_str:
                to_keep.add((tr["msg_idx"], tr["part_idx"]))
            elif len(str(content)) < 300:
                to_keep.add((tr["msg_idx"], tr["part_idx"]))

    for tr in all_tool_results:
        key = (tr["msg_idx"], tr["part_idx"])
        part = tr["part"]
        content = part.get("content", "")

        if key not in to_keep:
            if isinstance(content, str) and len(content) > 80:
                part["content"] = "[cleared]"
        else:
            if isinstance(content, str) and len(content) > MICROCOMPACT_MAX_TOOL_RESULT_LEN:
                part["content"] = (
                    content[:MICROCOMPACT_MAX_TOOL_RESULT_LEN // 2]
                    + "\n\n...[content truncated by microcompact]...\n\n"
                    + content[-MICROCOMPACT_MAX_TOOL_RESULT_LEN // 2:]
                )


def _microcompact_simple(messages: list):
    """简单回退策略"""
    indices = []
    for i, msg in enumerate(messages):
        if msg["role"] == "user" and isinstance(msg.get("content"), list):
            for part in msg["content"]:
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    indices.append(part)
    if len(indices) <= MICROCOMPACT_KEEP_RECENT_TOOL_RESULTS:
        return
    for part in indices[:-MICROCOMPACT_KEEP_RECENT_TOOL_RESULTS]:
        if isinstance(part.get("content"), str) and len(part["content"]) > 100:
            part["content"] = "[cleared]"


def auto_compact(messages: list) -> list:
    """
    自动压缩（Session Memory 的回退方案）

    将完整消息历史压缩成摘要，原始对话保存到 transcript 文件
    """
    TRANSCRIPT_DIR.mkdir(exist_ok=True)
    path = TRANSCRIPT_DIR / f"transcript_{int(time.time())}.jsonl"
    with open(path, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg, default=str) + "\n")
    conv_text = json.dumps(messages, default=str)[:80000]
    resp = client.messages.create(
        model=MODEL,
        messages=[{"role": "user", "content": f"Summarize for continuity:\n{conv_text}"}],
        max_tokens=2000,
    )
    summary = resp.content[0].text
    msg1 = {"role": "user", "content": f"[Compressed. Transcript: {path}]\n{summary}"}
    msg1["id"] = str(uuid.uuid4())[:12]
    msg1["created_at"] = time.time()
    msg2 = {"role": "assistant", "content": "Understood. Continuing with summary context."}
    msg2["id"] = str(uuid.uuid4())[:12]
    msg2["created_at"] = time.time()
    return [msg1, msg2]


class TaskManager:
    """任务管理器"""

    def __init__(self):
        TASKS_DIR.mkdir(exist_ok=True)

    def _next_id(self) -> int:
        ids = [int(f.stem.split("_")[1]) for f in TASKS_DIR.glob("task_*.json")]
        return max(ids, default=0) + 1

    def _load(self, tid: int) -> dict:
        p = TASKS_DIR / f"task_{tid}.json"
        if not p.exists():
            raise ValueError(f"Task {tid} not found")
        return json.loads(p.read_text())

    def _save(self, task: dict):
        (TASKS_DIR / f"task_{task['id']}.json").write_text(json.dumps(task, indent=2))

    def create(self, subject: str, description: str = "") -> str:
        """创建新任务"""
        task = {"id": self._next_id(), "subject": subject, "description": description,
                "status": "pending", "owner": None, "blockedBy": [], "blocks": []}
        self._save(task)
        return json.dumps(task, indent=2)

    def get(self, tid: int) -> str:
        """获取任务详情"""
        return json.dumps(self._load(tid), indent=2)

    def update(self, tid: int, status: str = None,
               add_blocked_by: list = None, add_blocks: list = None) -> str:
        """更新任务"""
        task = self._load(tid)
        if status:
            task["status"] = status
            if status == "completed":
                for f in TASKS_DIR.glob("task_*.json"):
                    t = json.loads(f.read_text())
                    if tid in t.get("blockedBy", []):
                        t["blockedBy"].remove(tid)
                        self._save(t)
            if status == "deleted":
                (TASKS_DIR / f"task_{tid}.json").unlink(missing_ok=True)
                return f"Task {tid} deleted"
        if add_blocked_by:
            task["blockedBy"] = list(set(task["blockedBy"] + add_blocked_by))
        if add_blocks:
            task["blocks"] = list(set(task["blocks"] + add_blocks))
        self._save(task)
        return json.dumps(task, indent=2)

    def list_all(self) -> str:
        """列出所有任务"""
        tasks = [json.loads(f.read_text()) for f in sorted(TASKS_DIR.glob("task_*.json"))]
        if not tasks:
            return "No tasks."
        lines = []
        for t in tasks:
            m = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}.get(t["status"], "[?]")
            owner = f" @{t['owner']}" if t.get("owner") else ""
            blocked = f" (blocked by: {t['blockedBy']})" if t.get("blockedBy") else ""
            lines.append(f"{m} #{t['id']}: {t['subject']}{owner}{blocked}")
        return "\n".join(lines)

    def claim(self, tid: int, owner: str) -> str:
        """认领任务"""
        task = self._load(tid)
        task["owner"] = owner
        task["status"] = "in_progress"
        self._save(task)
        return f"Claimed task #{tid} for {owner}"


class BackgroundManager:
    """后台任务管理器"""

    def __init__(self):
        self.tasks = {}
        self.notifications = Queue()

    def run(self, command: str, timeout: int = 120) -> str:
        """运行后台任务"""
        tid = str(uuid.uuid4())[:8]
        self.tasks[tid] = {"status": "running", "command": command, "result": None}
        threading.Thread(target=self._exec, args=(tid, command, timeout), daemon=True).start()
        return f"Background task {tid} started: {command[:80]}"

    def _exec(self, tid: str, command: str, timeout: int):
        try:
            r = subprocess.run(command, shell=True, cwd=WORKDIR,
                               capture_output=True, text=True, timeout=timeout)
            output = (r.stdout + r.stderr).strip()[:50000]
            self.tasks[tid].update({"status": "completed", "result": output or "(no output)"})
        except Exception as e:
            self.tasks[tid].update({"status": "error", "result": str(e)})
        self.notifications.put({"task_id": tid, "status": self.tasks[tid]["status"],
                               "result": self.tasks[tid]["result"][:500]})

    def check(self, tid: str = None) -> str:
        """检查任务状态"""
        if tid:
            t = self.tasks.get(tid)
            return f"[{t['status']}] {t.get('result', '(running)')}" if t else f"Unknown: {tid}"
        return "\n".join(f"{k}: [{v['status']}] {v['command'][:60]}" for k, v in self.tasks.items()) or "No bg tasks."

    def drain(self) -> list:
        """获取并清空所有通知"""
        notifs = []
        while not self.notifications.empty():
            notifs.append(self.notifications.get_nowait())
        return notifs


class MessageBus:
    """消息总线"""

    def __init__(self):
        INBOX_DIR.mkdir(parents=True, exist_ok=True)

    def send(self, sender: str, to: str, content: str,
             msg_type: str = "message", extra: dict = None) -> str:
        """发送消息"""
        msg = {"type": msg_type, "from": sender, "content": content,
               "timestamp": time.time()}
        if extra:
            msg.update(extra)
        with open(INBOX_DIR / f"{to}.jsonl", "a") as f:
            f.write(json.dumps(msg) + "\n")
        return f"Sent {msg_type} to {to}"

    def read_inbox(self, name: str) -> list:
        """读取收件箱"""
        path = INBOX_DIR / f"{name}.jsonl"
        if not path.exists():
            return []
        msgs = [json.loads(l) for l in path.read_text().strip().splitlines() if l]
        path.write_text("")
        return msgs

    def broadcast(self, sender: str, content: str, names: list) -> str:
        """广播消息"""
        count = 0
        for n in names:
            if n != sender:
                self.send(sender, n, content, "broadcast")
                count += 1
        return f"Broadcast to {count} teammates"


shutdown_requests = {}
plan_requests = {}


class TeammateManager:
    """队友管理器"""

    def __init__(self, bus: MessageBus, task_mgr: TaskManager):
        TEAM_DIR.mkdir(exist_ok=True)
        self.bus = bus
        self.task_mgr = task_mgr
        self.config_path = TEAM_DIR / "config.json"
        self.config = self._load()
        self.threads = {}

    def _load(self) -> dict:
        if self.config_path.exists():
            return json.loads(self.config_path.read_text())
        return {"team_name": "default", "members": []}

    def _save(self):
        self.config_path.write_text(json.dumps(self.config, indent=2))

    def _find(self, name: str) -> dict:
        for m in self.config["members"]:
            if m["name"] == name:
                return m
        return None

    def spawn(self, name: str, role: str, prompt: str) -> str:
        """生成队友代理"""
        member = self._find(name)
        if member:
            if member["status"] not in ("idle", "shutdown"):
                return f"Error: '{name}' is currently {member['status']}"
            member["status"] = "working"
            member["role"] = role
        else:
            member = {"name": name, "role": role, "status": "working"}
            self.config["members"].append(member)
        self._save()
        threading.Thread(target=self._loop, args=(name, role, prompt), daemon=True).start()
        return f"Spawned '{name}' (role: {role})"

    def _set_status(self, name: str, status: str):
        member = self._find(name)
        if member:
            member["status"] = status
            self._save()

    def _loop(self, name: str, role: str, prompt: str):
        team_name = self.config["team_name"]
        sys_prompt = (f"You are '{name}', role: {role}, team: {team_name}, at {WORKDIR}. "
                      f"Use idle when done with current work. You may auto-claim tasks.")
        messages = [{"role": "user", "content": prompt}]
        tools = [
            {"name": "bash", "description": "Run command.", "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
            {"name": "read_file", "description": "Read file.", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
            {"name": "write_file", "description": "Write file.", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
            {"name": "edit_file", "description": "Edit file.", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
            {"name": "send_message", "description": "Send message.", "input_schema": {"type": "object", "properties": {"to": {"type": "string"}, "content": {"type": "string"}}, "required": ["to", "content"]}},
            {"name": "idle", "description": "Signal no more work.", "input_schema": {"type": "object", "properties": {}}},
            {"name": "claim_task", "description": "Claim task by ID.", "input_schema": {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}},
        ]
        while True:
            for _ in range(50):
                inbox = self.bus.read_inbox(name)
                for msg in inbox:
                    if msg.get("type") == "shutdown_request":
                        self._set_status(name, "shutdown")
                        return
                    messages.append({"role": "user", "content": json.dumps(msg)})
                try:
                    response = client.messages.create(
                        model=MODEL, system=sys_prompt, messages=messages,
                        tools=tools, max_tokens=8000)
                except Exception as e:
                    print(f"  [{name}] Error: {e}")
                    self._set_status(name, "shutdown")
                    return
                content_serializable = []
                for b in response.content:
                    if hasattr(b, "type") and b.type == "text":
                        content_serializable.append({"type": "text", "text": b.text})
                    elif hasattr(b, "type") and b.type == "tool_use":
                        content_serializable.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
                messages.append({"role": "assistant", "content": content_serializable})
                if response.stop_reason != "tool_use":
                    break
                results = []
                idle_requested = False
                for block in response.content:
                    if hasattr(block, "type") and block.type == "tool_use":
                        if block.name == "idle":
                            idle_requested = True
                            output = "Entering idle phase."
                        elif block.name == "claim_task":
                            output = self.task_mgr.claim(block.input["task_id"], name)
                        elif block.name == "send_message":
                            output = self.bus.send(name, block.input["to"], block.input["content"])
                        else:
                            dispatch = {"bash": lambda **kw: run_bash(kw["command"]),
                                        "read_file": lambda **kw: run_read(kw["path"]),
                                        "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
                                        "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"])}
                            output = dispatch.get(block.name, lambda **kw: "Unknown")(**block.input)
                        print(f"  [{name}] {block.name}: {str(output)[:120]}")
                        results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(output)})
                messages.append({"role": "user", "content": results})
                if idle_requested:
                    break
            self._set_status(name, "idle")
            resume = False
            for _ in range(IDLE_TIMEOUT // max(POLL_INTERVAL, 1)):
                time.sleep(POLL_INTERVAL)
                inbox = self.bus.read_inbox(name)
                if inbox:
                    for msg in inbox:
                        if msg.get("type") == "shutdown_request":
                            self._set_status(name, "shutdown")
                            return
                        messages.append({"role": "user", "content": json.dumps(msg)})
                    resume = True
                    break
                unclaimed = []
                for f in sorted(TASKS_DIR.glob("task_*.json")):
                    t = json.loads(f.read_text())
                    if t.get("status") == "pending" and not t.get("owner") and not t.get("blockedBy"):
                        unclaimed.append(t)
                if unclaimed:
                    task = unclaimed[0]
                    self.task_mgr.claim(task["id"], name)
                    if len(messages) <= 3:
                        messages.insert(0, {"role": "user", "content":
                                           f"<identity>You are '{name}', role: {role}, team: {team_name}.</identity>"})
                        messages.insert(1, {"role": "assistant", "content": f"I am {name}. Continuing."})
                    messages.append({"role": "user", "content":
                                    f"<auto-claimed>Task #{task['id']}: {task['subject']}\n{task.get('description', '')}</auto-claimed>"})
                    messages.append({"role": "assistant", "content": f"Claimed task #{task['id']}. Working on it."})
                    resume = True
                    break
            if not resume:
                self._set_status(name, "shutdown")
                return
            self._set_status(name, "working")

    def list_all(self) -> str:
        """列出所有队友"""
        if not self.config["members"]:
            return "No teammates."
        lines = [f"Team: {self.config['team_name']}"]
        for m in self.config["members"]:
            lines.append(f"  {m['name']} ({m['role']}): {m['status']}")
        return "\n".join(lines)

    def member_names(self) -> list:
        """获取队友名称列表"""
        return [m["name"] for m in self.config["members"]]


TODO = TodoManager()
SKILLS = SkillLoader(SKILLS_DIR)
TASK_MGR = TaskManager()
BG = BackgroundManager()
BUS = MessageBus()
TEAM = TeammateManager(BUS, TASK_MGR)

if SECURITY_ENABLED:
    SECURITY = PromptSecurity()
    OUTPUT_VALIDATOR = OutputValidator()
    security_stats = {"blocked": 0, "warnings": 0}

SYSTEM = f"You are a coding agent at {WORKDIR}. Use tools to solve tasks. " \
         f"Prefer task_create/task_update/task_list for multi-step work. Use TodoWrite for short checklists. " \
         f"Use task for subagent delegation. Use load_skill for specialized knowledge. " \
         f"Skills: {SKILLS.descriptions()}"


def handle_shutdown_request(teammate: str) -> str:
    """处理关闭请求"""
    req_id = str(uuid.uuid4())[:8]
    shutdown_requests[req_id] = {"target": teammate, "status": "pending"}
    BUS.send("lead", teammate, "Please shut down.", "shutdown_request", {"request_id": req_id})
    return f"Shutdown request {req_id} sent to '{teammate}'"


def handle_plan_review(request_id: str, approve: bool, feedback: str = "") -> str:
    """处理计划审查"""
    req = plan_requests.get(request_id)
    if not req:
        return f"Error: Unknown plan request_id '{request_id}'"
    req["status"] = "approved" if approve else "rejected"
    BUS.send("lead", req["from"], feedback, "plan_approval_response",
             {"request_id": request_id, "approve": approve, "feedback": feedback})
    return f"Plan {req['status']} for '{req['from']}'"


TOOL_HANDLERS = {
    "bash": lambda **kw: run_bash(kw["command"]),
    "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    "TodoWrite": lambda **kw: TODO.update(kw["items"]),
    "task": lambda **kw: run_subagent(kw["prompt"], kw.get("agent_type", "Explore")),
    "load_skill": lambda **kw: SKILLS.load(kw["name"]),
    "compress": lambda **kw: "Compressing...",
    "background_run": lambda **kw: BG.run(kw["command"], kw.get("timeout", 120)),
    "check_background": lambda **kw: BG.check(kw.get("task_id")),
    "task_create": lambda **kw: TASK_MGR.create(kw["subject"], kw.get("description", "")),
    "task_get": lambda **kw: TASK_MGR.get(kw["task_id"]),
    "task_update": lambda **kw: TASK_MGR.update(kw["task_id"], kw.get("status"), kw.get("add_blocked_by"), kw.get("add_blocks")),
    "task_list": lambda **kw: TASK_MGR.list_all(),
    "spawn_teammate": lambda **kw: TEAM.spawn(kw["name"], kw["role"], kw["prompt"]),
    "list_teammates": lambda **kw: TEAM.list_all(),
    "send_message": lambda **kw: BUS.send("lead", kw["to"], kw["content"], kw.get("msg_type", "message")),
    "read_inbox": lambda **kw: json.dumps(BUS.read_inbox("lead"), indent=2),
    "broadcast": lambda **kw: BUS.broadcast("lead", kw["content"], TEAM.member_names()),
    "shutdown_request": lambda **kw: handle_shutdown_request(kw["teammate"]),
    "plan_approval": lambda **kw: handle_plan_review(kw["request_id"], kw["approve"], kw.get("feedback", "")),
    "idle": lambda **kw: "Lead does not idle.",
    "claim_task": lambda **kw: TASK_MGR.claim(kw["task_id"], "lead"),
}

TOOLS = [
    {"name": "bash", "description": "Run a shell command.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read file contents.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write content to file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Replace exact text in file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
    {"name": "TodoWrite", "description": "Update task tracking list.",
     "input_schema": {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {"content": {"type": "string"}, "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]}, "activeForm": {"type": "string"}}, "required": ["content", "status", "activeForm"]}}}, "required": ["items"]}},
    {"name": "task", "description": "Spawn a subagent for isolated exploration or work.",
     "input_schema": {"type": "object", "properties": {"prompt": {"type": "string"}, "agent_type": {"type": "string", "enum": ["Explore", "general-purpose"]}}, "required": ["prompt"]}},
    {"name": "load_skill", "description": "Load specialized knowledge by name.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "compress", "description": "Manually compress conversation context.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "background_run", "description": "Run command in background thread.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}, "timeout": {"type": "integer"}}, "required": ["command"]}},
    {"name": "check_background", "description": "Check background task status.",
     "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}}}},
    {"name": "task_create", "description": "Create a persistent file task.",
     "input_schema": {"type": "object", "properties": {"subject": {"type": "string"}, "description": {"type": "string"}}, "required": ["subject"]}},
    {"name": "task_get", "description": "Get task details by ID.",
     "input_schema": {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}},
    {"name": "task_update", "description": "Update task status or dependencies.",
     "input_schema": {"type": "object", "properties": {"task_id": {"type": "integer"}, "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "deleted"]}, "add_blocked_by": {"type": "array", "items": {"type": "integer"}}, "add_blocks": {"type": "array", "items": {"type": "integer"}}}, "required": ["task_id"]}},
    {"name": "task_list", "description": "List all tasks.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "spawn_teammate", "description": "Spawn a persistent autonomous teammate.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "role": {"type": "string"}, "prompt": {"type": "string"}}, "required": ["name", "role", "prompt"]}},
    {"name": "list_teammates", "description": "List all teammates.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "send_message", "description": "Send a message to a teammate.",
     "input_schema": {"type": "object", "properties": {"to": {"type": "string"}, "content": {"type": "string"}, "msg_type": {"type": "string", "enum": list(VALID_MSG_TYPES)}}, "required": ["to", "content"]}},
    {"name": "read_inbox", "description": "Read and drain the lead's inbox.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "broadcast", "description": "Send message to all teammates.",
     "input_schema": {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]}},
    {"name": "shutdown_request", "description": "Request a teammate to shut down.",
     "input_schema": {"type": "object", "properties": {"teammate": {"type": "string"}}, "required": ["teammate"]}},
    {"name": "plan_approval", "description": "Approve or reject a teammate's plan.",
     "input_schema": {"type": "object", "properties": {"request_id": {"type": "string"}, "approve": {"type": "boolean"}, "feedback": {"type": "string"}}, "required": ["request_id", "approve"]}},
    {"name": "idle", "description": "Enter idle state.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "claim_task", "description": "Claim a task from the board.",
     "input_schema": {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}},
]


def agent_loop(messages: list):
    """
    代理主循环
    """
    rounds_without_todo = 0
    while True:
        microcompact(messages)
        if estimate_tokens(messages) > TOKEN_THRESHOLD:
            compacted = None
            if MEM_STATE is not None:
                try:
                    from memory_compact import try_memory_compaction
                    compacted = try_memory_compaction(messages, MEM_STATE, MEMORY_DIR)
                except Exception as e:
                    print(f"[memory compact error] {e}")
                    compacted = None

            if compacted is not None:
                print("[memory-compact triggered]")
                messages[:] = compacted
            else:
                print("[auto-compact triggered (fallback)]")
                messages[:] = auto_compact(messages)

        notifs = BG.drain()
        if notifs:
            txt = "\n".join(f"[bg:{n['task_id']}] {n['status']}: {n['result']}" for n in notifs)
            bg_msg = {"role": "user", "content": f"<background-results>\n{txt}\n</background-results>"}
            bg_msg["id"] = str(uuid.uuid4())[:12]
            bg_msg["created_at"] = time.time()
            messages.append(bg_msg)

            bg_ack = {"role": "assistant", "content": "Noted background results."}
            bg_ack["id"] = str(uuid.uuid4())[:12]
            bg_ack["created_at"] = time.time()
            messages.append(bg_ack)

        inbox = BUS.read_inbox("lead")
        if inbox:
            inbox_msg = {"role": "user", "content": f"<inbox>{json.dumps(inbox, indent=2)}</inbox>"}
            inbox_msg["id"] = str(uuid.uuid4())[:12]
            inbox_msg["created_at"] = time.time()
            messages.append(inbox_msg)

            inbox_ack = {"role": "assistant", "content": "Noted inbox messages."}
            inbox_ack["id"] = str(uuid.uuid4())[:12]
            inbox_ack["created_at"] = time.time()
            messages.append(inbox_ack)

        print("[thinking...]", end="", flush=True)
        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )
        print("\r", end="", flush=True)

        content_serializable = []
        has_text = False
        for b in response.content:
            if hasattr(b, "type") and b.type == "text":
                content_serializable.append({"type": "text", "text": b.text})
                if b.text.strip():
                    print(f"\033[33m{b.text}\033[0m")
                    has_text = True
            elif hasattr(b, "type") and b.type == "tool_use":
                content_serializable.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})

        assistant_msg = {"role": "assistant", "content": content_serializable}
        assistant_msg["id"] = str(uuid.uuid4())[:12]
        assistant_msg["created_at"] = time.time()
        messages.append(assistant_msg)

        if response.stop_reason != "tool_use":
            if not has_text:
                print("\033[33m(no reply content)\033[0m")
            return

        results = []
        used_todo = False
        manual_compress = False
        for block in response.content:
            if hasattr(block, "type") and block.type == "tool_use":
                if block.name == "compress":
                    manual_compress = True
                handler = TOOL_HANDLERS.get(block.name)
                try:
                    output = handler(**block.input) if handler else f"Unknown tool: {block.name}"
                except Exception as e:
                    output = f"Error: {e}"
                print(f"> {block.name}: {str(output)[:200]}")
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(output)})
                if block.name == "TodoWrite":
                    used_todo = True

        rounds_without_todo = 0 if used_todo else rounds_without_todo + 1
        if TODO.has_open_items() and rounds_without_todo >= 3:
            results.insert(0, {"type": "text", "text": "<reminder>Update your todos.</reminder>"})

        user_msg = {"role": "user", "content": results}
        user_msg["id"] = str(uuid.uuid4())[:12]
        user_msg["created_at"] = time.time()
        messages.append(user_msg)

        if MEM_STATE is not None:
            try:
                from memory_extract import should_extract_memory, extract_session_memory
                state = MEM_STATE.load()
                if should_extract_memory(messages, state):
                    print("[extracting session memory...]")
                    success = extract_session_memory(messages, MEM_STATE, MEMORY_DIR, MODEL, client)
                    if success:
                        print("[session memory updated]")
            except Exception as e:
                print(f"[memory extract error] {e}")

        if manual_compress:
            print("[manual compact]")
            messages[:] = auto_compact(messages)


try:
    from memory_state import MemoryState
    MEM_STATE = MemoryState(MEMORY_DIR)
except ImportError:
    MEM_STATE = None


if __name__ == "__main__":
    print("=" * 60)
    print("  AI Agent System")
    print(f"  Security: {'✅ Enabled' if SECURITY_ENABLED else '❌ Disabled'}")
    if SECURITY_ENABLED:
        print(f"  Strict Mode: {'✅' if SECURITY_STRICT_MODE else '❌'}")
        print(f"  Mode: {'Warning Only' if SECURITY_WARNING_ONLY else 'Warning + Block'}")
    print("=" * 60)
    print("Commands: /compact, /tasks, /team, /inbox, /security, /stats, q(exit)")
    print()

    if MEM_STATE is not None:
        try:
            from memory_storage import ensure_session_memory_file
            ensure_session_memory_file(MEMORY_DIR)
            print("  [Session Memory initialized]")
        except Exception as e:
            print(f"  [Session Memory init failed] {e}")

    history = []
    while True:
        try:
            query = input("\033[36magent>> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break

        if query.strip() == "/compact":
            if history:
                print("[manual compact via /compact]")
                history[:] = auto_compact(history)
            continue
        if query.strip() == "/tasks":
            print(TASK_MGR.list_all())
            continue
        if query.strip() == "/team":
            print(TEAM.list_all())
            continue
        if query.strip() == "/inbox":
            print(json.dumps(BUS.read_inbox("lead"), indent=2))
            continue
        if query.strip() == "/security":
            if SECURITY_ENABLED:
                print("\033[32m=== Security Module Status ===\033[0m")
                print(f"Status: Running")
                print(f"Strict Mode: {SECURITY_STRICT_MODE}")
                print(f"Blocked: {security_stats['blocked']}")
                print(f"Warnings: {security_stats['warnings']}")
            else:
                print("\033[33mSecurity module not enabled\033[0m")
            continue
        if query.strip() == "/stats":
            print("\033[36m=== System Stats ===\033[0m")
            print(f"History messages: {len(history)}")
            if SECURITY_ENABLED:
                print(f"Security blocked: {security_stats['blocked']}")
                print(f"Security warnings: {security_stats['warnings']}")
            continue

        if SECURITY_ENABLED and query.strip():
            scan_result = SECURITY.scan(query)

            if not scan_result.is_safe:
                issues_str = ", ".join(scan_result.detected_issues)

                if scan_result.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
                    if SECURITY_WARNING_ONLY:
                        print(f"\033[33m⚠️  Security warning [{scan_result.risk_level.name}]: {issues_str}\033[0m")
                        confirm = input("Continue anyway? (y/N): ")
                        if confirm.lower() != 'y':
                            print("Cancelled")
                            continue
                    else:
                        print(f"\033[31m❌ Blocked [{scan_result.risk_level.name}]: {issues_str}\033[0m")
                        security_stats["blocked"] += 1
                        continue
                else:
                    print(f"\033[33m⚠️  Notice [{scan_result.risk_level.name}]: {issues_str}\033[0m")
                    security_stats["warnings"] += 1

        user_msg = {"role": "user", "content": query}
        user_msg["id"] = str(uuid.uuid4())[:12]
        user_msg["created_at"] = time.time()
        history.append(user_msg)
        agent_loop(history)
        print()
