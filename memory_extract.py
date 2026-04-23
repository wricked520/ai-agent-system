"""
memory_extract.py - 摘要生产端

负责判断是否需要更新摘要，提取增量消息，调用模型更新 memory
"""
import json
import time
from pathlib import Path
from typing import Optional

# 导入存储和模板模块
from memory_storage import (
    read_session_memory, write_session_memory, is_default_template
)
from memory_prompt import build_memory_update_prompt
from memory_state import MemoryState

# 触发阈值
MIN_TOTAL_TOKENS_FOR_EXTRACT = 8000  # 总 token 少于这个不提取
DELTA_TOKEN_THRESHOLD = 5000  # 上次摘要后新增这么多 token 才提取
DELTA_TOOL_CALL_THRESHOLD = 3  # 或新增这么多工具调用

def estimate_tokens(messages: list) -> int:
    """估计消息的 token 数（简化版，与 s_full.py 保持一致）"""
    return len(json.dumps(messages, default=str)) // 4

def _count_tool_calls(messages: list) -> int:
    """统计消息中的 tool_use 数量"""
    count = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "tool_use":
                    count += 1
    return count

def preprocess_delta_messages(delta_messages: list) -> list:
    """预处理增量消息，截断过长内容"""
    processed = []
    for msg in delta_messages:
        # 浅拷贝消息，不要修改原对象
        msg_copy = dict(msg)
        content = msg_copy.get("content")

        if isinstance(content, list):
            new_content = []
            for part in content:
                if isinstance(part, dict):
                    part_copy = dict(part)
                    if part_copy.get("type") == "tool_result":
                        # 截断长 tool_result
                        tool_content = str(part_copy.get("content", ""))
                        if len(tool_content) > 800:
                            part_copy["content"] = (
                                tool_content[:400]
                                + "\n\n...[content truncated for summarization]...\n\n"
                                + tool_content[-200:]
                            )
                    new_content.append(part_copy)
                else:
                    new_content.append(part)
            msg_copy["content"] = new_content
        else:
            # 截断长文本
            content_str = str(content)
            if len(content_str) > 2000:
                msg_copy["content"] = (
                    content_str[:1200]
                    + "\n\n...[content truncated for summarization]...\n\n"
                    + content_str[-400:]
                )

        processed.append(msg_copy)
    return processed

def should_extract_memory(messages: list, state: dict) -> bool:
    """判断是否需要更新 session memory"""
    total_tokens = estimate_tokens(messages)

    # 总 token 太少，不提取
    if total_tokens < MIN_TOTAL_TOKENS_FOR_EXTRACT:
        return False

    # 如果正在提取中，不要重复提取
    if state.get("extraction_in_progress"):
        return False

    last_summarized_id = state.get("last_summarized_message_id")
    last_extracted_tokens = state.get("last_extracted_token_count", 0)

    # 如果还没有提取过，检查是否需要第一次提取
    if last_summarized_id is None:
        # 总 token 足够多了，可以第一次提取
        return total_tokens >= MIN_TOTAL_TOKENS_FOR_EXTRACT

    # 找到上次摘要后的增量消息
    delta_start = 0
    for i, msg in enumerate(messages):
        if msg.get("id") == last_summarized_id:
            delta_start = i + 1
            break

    if delta_start >= len(messages):
        # 没有新消息
        return False

    delta_messages = messages[delta_start:]
    delta_tokens = estimate_tokens(delta_messages)
    delta_tools = _count_tool_calls(delta_messages)

    # 增量足够多了
    return delta_tokens >= DELTA_TOKEN_THRESHOLD or delta_tools >= DELTA_TOOL_CALL_THRESHOLD

def extract_session_memory(
    messages: list,
    state: MemoryState,
    memory_dir: Path,
    model: str = "qwen3:8b",
    client=None
) -> bool:
    """
    执行一次 session memory 更新

    Args:
        messages: 完整消息列表
        state: MemoryState 实例
        memory_dir: 记忆目录
        model: 模型名称
        client: Ollama 客户端实例，如果为 None 会尝试从 s_full 导入

    Returns:
        是否成功更新
    """
    if not messages:
        return False

    # 懒加载 client
    if client is None:
        try:
            from s_full import client as s_full_client, MODEL
            client = s_full_client
            model = MODEL
        except ImportError:
            return False

    current_state = state.load()
    last_summarized_id = current_state.get("last_summarized_message_id")

    # 找到增量消息
    delta_start = 0
    if last_summarized_id:
        for i, msg in enumerate(messages):
            if msg.get("id") == last_summarized_id:
                delta_start = i + 1
                break

    delta_messages = messages[delta_start:]
    if not delta_messages:
        return False  # 没有新消息

    # 标记正在提取
    state.set_extraction_in_progress(True)

    try:
        # 读取现有 memory
        existing_memory = read_session_memory(memory_dir)

        # 预处理增量消息
        processed_delta = preprocess_delta_messages(delta_messages)

        # 构建 prompt
        prompt = build_memory_update_prompt(existing_memory, processed_delta)

        # 调用模型
        response = client.messages_create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000
        )

        # 提取结果
        new_memory = ""
        if hasattr(response, "content"):
            for block in response.content:
                if hasattr(block, "type") and block.type == "text":
                    new_memory += block.text
        elif isinstance(response, str):
            new_memory = response

        if not new_memory or len(new_memory.strip()) < 50:
            # 结果太短，可能出错了
            return False

        # 写回 memory
        write_session_memory(memory_dir, new_memory)

        # 更新状态：记录最后一条摘要到的消息 ID
        last_msg_id = messages[-1].get("id")
        if last_msg_id:
            state.set_last_summarized_id(last_msg_id, estimate_tokens(messages))

        return True

    except Exception as e:
        print(f"[memory extract error] {e}")
        return False
    finally:
        state.set_extraction_in_progress(False)
