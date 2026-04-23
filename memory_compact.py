"""
memory_compact.py - 摘要消费端

负责读取 session memory，计算要保留的最近消息窗口，生成压缩后的消息列表
"""
import json
from pathlib import Path
from typing import Optional, List

from memory_storage import read_session_memory, is_default_template
from memory_state import MemoryState
from message_invariants import adjust_index_to_preserve_invariants

# 保留策略
MIN_TOKENS_TO_KEEP = 8000  # 至少保留的 token 数
MAX_TOKENS_TO_KEEP = 35000  # 最多保留的 token 数
MIN_MESSAGES_TO_KEEP = 5  # 至少保留的消息数

def estimate_tokens(messages: list) -> int:
    """估计消息的 token 数（简化版，与 s_full.py 保持一致）"""
    return len(json.dumps(messages, default=str)) // 4

def calculate_messages_to_keep_index(
    messages: list,
    last_summarized_id: Optional[str]
) -> int:
    """
    计算要保留的最近消息的起始索引

    Returns:
        应该保留的消息的起始索引（即从此索引到末尾都保留）
    """
    if not messages:
        return 0

    # 先从上次摘要的位置开始
    start_idx = 0
    if last_summarized_id:
        for i, msg in enumerate(messages):
            if msg.get("id") == last_summarized_id:
                start_idx = i + 1
                break

    # 确保至少保留 MIN_MESSAGES_TO_KEEP
    start_idx = min(start_idx, max(0, len(messages) - MIN_MESSAGES_TO_KEEP))

    # 调整 token 范围
    # 从后往前累加，直到达到 MIN_TOKENS_TO_KEEP 或起点
    token_sum = 0
    final_start = len(messages)
    for i in range(len(messages) - 1, -1, -1):
        msg_tokens = estimate_tokens([messages[i]])
        if token_sum + msg_tokens > MAX_TOKENS_TO_KEEP and i < len(messages) - 1:
            break  # 超过最大值了，不能再往前
        token_sum += msg_tokens
        final_start = i
        if token_sum >= MIN_TOKENS_TO_KEEP and i <= start_idx:
            break  # 达到最小值且覆盖了上次摘要后的内容

    # 最终取两者中更靠前的，确保覆盖上次摘要后的内容
    final_start = min(final_start, start_idx)
    final_start = max(0, final_start)

    return final_start

def try_memory_compaction(
    messages: list,
    state: MemoryState,
    memory_dir: Path
) -> Optional[List[dict]]:
    """
    尝试使用 session memory 进行压缩

    Returns:
        压缩后的消息列表，如果不能用 memory compact 则返回 None
    """
    # 读取 memory
    memory_content = read_session_memory(memory_dir)

    # 如果还是默认模板，不能用
    if is_default_template(memory_content):
        return None

    # 等待正在进行的提取（简单等待，最多 0.5 秒）
    import time
    wait_count = 0
    while state.is_extraction_in_progress() and wait_count < 5:
        time.sleep(0.1)
        wait_count += 1

    # 读取状态
    mem_state = state.load()
    last_summarized_id = mem_state.get("last_summarized_message_id")

    # 计算保留窗口
    start_idx = calculate_messages_to_keep_index(messages, last_summarized_id)

    # 调整边界，确保不切断 tool_use/tool_result
    start_idx = adjust_index_to_preserve_invariants(messages, start_idx)

    # 构建压缩后的消息列表
    compacted = []

    # 1. 添加 memory 摘要消息
    compacted.append({
        "role": "user",
        "content": f"<session-memory>\n{memory_content}\n</session-memory>\n\n(Note: Older conversation history has been summarized above. The messages below are the most recent ones.)",
        "id": f"mem-{int(time.time())}"
    })

    compacted.append({
        "role": "assistant",
        "content": "Understood. I'll keep the session memory in mind and continue the conversation based on the recent messages.",
        "id": f"mem-ack-{int(time.time())}"
    })

    # 2. 添加保留的最近消息
    compacted.extend(messages[start_idx:])

    return compacted
