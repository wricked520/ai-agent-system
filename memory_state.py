"""
memory_state.py - 会话记忆元状态管理

负责读写 session_memory_state.json，追踪摘要进度
"""
import json
import time
from pathlib import Path
from typing import Optional, Any

DEFAULT_STATE = {
    "last_summarized_message_id": None,
    "last_extracted_token_count": 0,
    "extraction_in_progress": False,
    "last_extraction_ts": 0
}

class MemoryState:
    def __init__(self, memory_dir: Path):
        self.memory_dir = memory_dir
        self.state_path = memory_dir / "session_memory_state.json"

    def load(self) -> dict:
        """加载状态，如果文件不存在则返回默认状态"""
        if not self.state_path.exists():
            return DEFAULT_STATE.copy()
        try:
            content = self.state_path.read_text(encoding="utf-8")
            state = json.loads(content)
            # 合并默认值，确保所有键都存在
            result = DEFAULT_STATE.copy()
            result.update(state)
            return result
        except (json.JSONDecodeError, IOError):
            return DEFAULT_STATE.copy()

    def save(self, state: dict):
        """保存状态到文件"""
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        # 只保存需要的键
        to_save = {k: v for k, v in state.items() if k in DEFAULT_STATE}
        self.state_path.write_text(json.dumps(to_save, indent=2, ensure_ascii=False), encoding="utf-8")

    def is_extraction_in_progress(self) -> bool:
        """检查是否正在进行摘要提取"""
        state = self.load()
        return state.get("extraction_in_progress", False)

    def set_extraction_in_progress(self, val: bool):
        """设置提取进行中标志"""
        state = self.load()
        state["extraction_in_progress"] = val
        if val:
            state["last_extraction_ts"] = time.time()
        self.save(state)

    def get_last_summarized_id(self) -> Optional[str]:
        """获取上次摘要到的消息 ID"""
        state = self.load()
        return state.get("last_summarized_message_id")

    def set_last_summarized_id(self, msg_id: str, token_count: int = 0):
        """设置上次摘要到的消息 ID 和对应的 token 数"""
        state = self.load()
        state["last_summarized_message_id"] = msg_id
        state["last_extracted_token_count"] = token_count
        state["last_extraction_ts"] = time.time()
        self.save(state)

    def get_last_extracted_token_count(self) -> int:
        """获取上次摘要时的总 token 数"""
        state = self.load()
        return state.get("last_extracted_token_count", 0)
