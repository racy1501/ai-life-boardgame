# -*- coding: utf-8 -*-
"""最薄本地测试 MCP：直接包装 ailife.runtime.GameSession。

只做两件事：
1. 进程内维护 session_id -> GameSession；
2. 把三个 tool 的入参原样转交给 GameSession 的公开方法。

本模块不解释规则：不包装 Simulator、不调用 play_turn()、不复制 Runtime /
Engine 的判定，也不自行计算合法动作或购买/维护方案 —— 合法动作与方案全部
来自 Runtime decision 自带的 legal_actions / legal_acquisition_plans。

错误约定：
- 参数结构本身不合法（调用方 bug）抛 ValueError，由 SDK 报成 tool error；
- session_id 在进程内找不到属于正常运行时情况（server 重启后旧 ID 失效），
  返回 {'ok': False, 'error': 'unknown_session_id'}，让调用方能直接看到原因，
  而不是被 SDK 折叠成一句笼统的 tool error；
- 决策内的 stale / 非法 action 由 Runtime 自己判定，本层把它的
  {'ok': False, 'error': ...} 原样透传。

启动（本机未预装官方 SDK，用临时环境运行）：
    uv run --no-project --with mcp python runtime_mcp.py
"""
import uuid
from typing import Any, Dict, List, Optional

from ailife.runtime import GameSession

try:  # 官方 Python SDK 1.x
    from mcp.server.fastmcp import FastMCP as _McpServer
except ImportError:  # 官方 SDK 2.x：FastMCP 更名为 MCPServer
    try:
        from mcp.server.mcpserver import MCPServer as _McpServer
    except ImportError:  # 未安装官方 SDK：三个 tool 仍可直接调用与测试
        _McpServer = None


# Runtime 把同一个掷骰余额用三个别名重复暴露；MCP 只保留一个。
_CANONICAL_REROLL_KEY = 'remaining_normal_rerolls'
_REDUNDANT_REROLL_KEYS = ('normal_rerolls_remaining',
                          'normal_rerolls_available')

# 进程内 session 表：server 重启即失效，不做数据库 / 存档 / 序列化。
_SESSIONS = {}


def _validate_seed(seed):
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        raise ValueError('seed 必须是整数或 null')


def _validate_forced_goals(forced_goals):
    if forced_goals is None:
        return
    if (not isinstance(forced_goals, (list, tuple))
            or len(forced_goals) != 2
            or any(isinstance(goal, bool) or not isinstance(goal, int)
                   for goal in forced_goals)):
        raise ValueError('forced_goals 必须是两个整数，例如 [1, 2]')


def _lookup_session(session_id):
    """返回 (session, error)；session 只存在于当前进程。"""
    session = _SESSIONS.get(session_id)
    if session is None:
        return None, {'ok': False, 'error': 'unknown_session_id'}
    return session, None


def _slim_decision(decision):
    """透传 Runtime decision，只删掉确实与规范字段同值的冗余别名。"""
    if not isinstance(decision, dict):
        return decision
    canonical = decision.get(_CANONICAL_REROLL_KEY)
    return {key: value for key, value in decision.items()
            if key not in _REDUNDANT_REROLL_KEYS or value != canonical}


def _slim_result(result):
    if not isinstance(result, dict):
        return result
    slimmed = dict(result)
    if isinstance(slimmed.get('decision'), dict):
        slimmed['decision'] = _slim_decision(slimmed['decision'])
    return slimmed


def start_game(seed: Optional[int] = None,
               forced_goals: Optional[List[int]] = None) -> Dict[str, Any]:
    """开一局新游戏，返回 session_id 与首个 decision。"""
    _validate_seed(seed)
    _validate_forced_goals(forced_goals)
    session = GameSession(seed=seed,
                          forced_goals=None if forced_goals is None
                          else list(forced_goals))
    session_id = str(uuid.uuid4())
    _SESSIONS[session_id] = session
    return {'session_id': session_id,
            'decision': _slim_decision(session.current_decision())}


def current_decision(session_id: str) -> Dict[str, Any]:
    """返回指定 session 的当前 decision；重复读取不改变任何状态。"""
    session, error = _lookup_session(session_id)
    if error is not None:
        return error
    return _slim_decision(session.current_decision())


def submit_action(session_id: str, decision_id: str,
                  action: Dict[str, Any]) -> Dict[str, Any]:
    """把 action 交给 GameSession.submit_action()，返回其结果与下一 decision。"""
    session, error = _lookup_session(session_id)
    if error is not None:
        return error
    return _slim_result(session.submit_action(decision_id, action))


def _build_server():
    if _McpServer is None:
        return None
    server = _McpServer('ai-life-boardgame-runtime')
    # structured_output=False：decision dict 只经 content[0].text 一份下发，
    # 避免 SDK 同时携带 structuredContent 造成同载荷双份重复。
    server.tool(name='start_game', structured_output=False)(start_game)
    server.tool(name='current_decision', structured_output=False)(current_decision)
    server.tool(name='submit_action', structured_output=False)(submit_action)
    return server


server = _build_server()


def main():
    if server is None:
        raise SystemExit('未检测到官方 mcp SDK；请用 '
                         '`uv run --no-project --with mcp python runtime_mcp.py` 启动')
    server.run()


if __name__ == '__main__':
    main()
