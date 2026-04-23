"""
memory_storage.py - 记忆文件存储模块

负责读写 session_memory.md，确保目录存在
"""
from pathlib import Path

DEFAULT_TEMPLATE = """# Session Memory

## Current objective
[No objective yet]

## Current state
[No state yet]

## Decisions made
[No decisions yet]

## Important files
[No important files yet]

## Open issues
[No open issues yet]

## Recent results
[No recent results yet]

## Next steps
[No next steps yet]

## Task board state
[No tasks yet]
"""

def get_session_memory_path(memory_dir: Path) -> Path:
    """获取 session_memory.md 的路径"""
    return memory_dir / "session_memory.md"

def ensure_session_memory_file(memory_dir: Path) -> Path:
    """确保 session_memory.md 存在，如果不存在则创建默认模板"""
    memory_dir.mkdir(parents=True, exist_ok=True)
    path = get_session_memory_path(memory_dir)
    if not path.exists():
        path.write_text(DEFAULT_TEMPLATE, encoding="utf-8")
    return path

def read_session_memory(memory_dir: Path) -> str:
    """读取 session_memory.md 内容"""
    path = get_session_memory_path(memory_dir)
    if not path.exists():
        return DEFAULT_TEMPLATE
    return path.read_text(encoding="utf-8")

def write_session_memory(memory_dir: Path, content: str):
    """写入 session_memory.md 内容"""
    memory_dir.mkdir(parents=True, exist_ok=True)
    path = get_session_memory_path(memory_dir)
    path.write_text(content, encoding="utf-8")

def is_default_template(content: str) -> bool:
    """判断内容是否是默认模板（还没有实际摘要）"""
    # 比较去除空白后的内容是否与默认模板相似
    def clean(s: str) -> str:
        return "".join(s.split())
    return clean(content[:200]) == clean(DEFAULT_TEMPLATE[:200])
