"""
message_invariants.py - 消息完整性校验

确保不切断 tool_use/tool_result 配对
"""
from typing import List, Set

def get_tool_result_ids(message: dict) -> List[str]:
    """从消息中提取所有 tool_result 的 tool_use_id"""
    ids = []
    content = message.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "tool_result":
                tool_id = part.get("tool_use_id")
                if tool_id:
                    ids.append(tool_id)
    return ids

def has_tool_use_with_id(message: dict, tool_use_id: str) -> bool:
    """检查消息是否包含指定 id 的 tool_use"""
    content = message.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "tool_use":
                if part.get("id") == tool_use_id:
                    return True
    return False

def adjust_index_to_preserve_invariants(messages: list, start_index: int) -> int:
    """
    调整起始索引，确保不切断 tool_use/tool_result 配对

    从 start_index 开始向前查找，确保：
    - 如果保留范围内有 tool_result，它对应的 tool_use 也在范围内
    - 如果保留范围内有 tool_use，它对应的 tool_result 不应该在范围内（但这没关系）
    """
    if start_index <= 0:
        return 0

    # 收集保留范围内的所有 tool_result id
    required_tool_use_ids: Set[str] = set()
    for i in range(start_index, len(messages)):
        required_tool_use_ids.update(get_tool_result_ids(messages[i]))

    if not required_tool_use_ids:
        return start_index

    # 向前查找这些 tool_use_id
    new_start = start_index
    for i in range(start_index - 1, -1, -1):
        found_in_this_message = False
        for tool_id in required_tool_use_ids:
            if has_tool_use_with_id(messages[i], tool_id):
                new_start = i
                found_in_this_message = True
                # 这个 tool_use 找到了，不需要再找它
                required_tool_use_ids.remove(tool_id)
                if not required_tool_use_ids:
                    break  # 都找到了
        if not required_tool_use_ids:
            break  # 都找到了

    return new_start
