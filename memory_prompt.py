"""
memory_prompt.py - 摘要模板管理

提供固定的 session memory 模板和更新 prompt
"""
from pathlib import Path
from typing import Optional

def build_initial_memory_template() -> str:
    """构建初始的 memory 模板"""
    return """# Session Memory

## Current objective
[一句话说明当前正在完成的目标]

## Current state
[目前已完成的工作，尚未完成的工作]

## Decisions made
[已确定的实现方案、约束条件、用户偏好]

## Important files
- path/to/file: 文件作用描述

## Open issues
- [问题1]
- [问题2]

## Recent results
- [最近获得的结果或完成的动作]

## Next steps
- [接下来最合理的下一步]

## Task board state
[当前任务状态：进行中的、已完成的、阻塞的]
"""

def _message_to_str(msg: dict) -> str:
    """将一条消息转换为适合摘要的字符串表示"""
    role = msg.get("role", "unknown")
    content = msg.get("content", "")

    if isinstance(content, list):
        # 处理多部分内容
        parts = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    parts.append(f"[text] {part.get('text', '')[:500]}")
                elif part.get("type") == "tool_use":
                    parts.append(f"[tool_use:{part.get('name')}] id={part.get('id')}")
                elif part.get("type") == "tool_result":
                    tool_content = str(part.get("content", ""))
                    # 截断长 tool_result
                    if len(tool_content) > 500:
                        tool_content = tool_content[:300] + "\n...[truncated]..." + tool_content[-100:]
                    parts.append(f"[tool_result:{part.get('tool_use_id')}] {tool_content}")
        return f"{role}: " + "\n".join(parts)
    else:
        # 普通文本内容
        content_str = str(content)
        if len(content_str) > 1000:
            content_str = content_str[:600] + "\n...[truncated]..." + content_str[-200:]
        return f"{role}: {content_str}"

def build_memory_update_prompt(
    existing_memory: str,
    delta_messages: list,
    transcript_path: Optional[Path] = None
) -> str:
    """构建用于更新 memory 的 prompt"""

    delta_str = "\n\n".join(_message_to_str(m) for m in delta_messages)

    transcript_note = ""
    if transcript_path:
        transcript_note = f"\n\nFull transcript available at: {transcript_path}"

    return f"""Your task is to update the Session Memory based on the conversation history that has happened since the last summary.

## Existing Session Memory
{existing_memory}

## New Messages Since Last Summary
{delta_str}
{transcript_note}

## Instructions
Update the Session Memory by:
1. Keep the same structure (Current objective, Current state, etc.)
2. Update each section based on the new messages
3. Keep it concise but informative
4. If the existing memory is the default template, replace it with actual content from the conversation
5. Focus on what's still relevant - remove outdated information
6. In "Important files", only list files that are actively relevant to current work
7. In "Recent results", only keep the most recent and important results
8. "Task board state" should summarize task progress if any tasks were mentioned

Please output ONLY the updated Session Memory in Markdown format, no other text.
"""
