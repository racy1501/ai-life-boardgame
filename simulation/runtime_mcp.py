# -*- coding: utf-8 -*-
"""最薄本地测试 MCP：直接包装 ailife.runtime.GameSession。

只做三件事：
1. 进程内维护 session_id -> GameSession；
2. 把三个 tool 的入参原样转交给 GameSession 的公开方法。
3. 在同一进程提供只读 spectator snapshot 的 loopback HTTP GET。

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
import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

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
_SPECTATOR_HOST = '127.0.0.1'
_SPECTATOR_PORT = 8765


def _spectator_port():
    raw_port = os.environ.get('AILIFE_SPECTATOR_PORT')
    if raw_port is None:
        return _SPECTATOR_PORT
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError('AILIFE_SPECTATOR_PORT 必须是 1 到 65535 的整数') from exc
    if not 1 <= port <= 65535:
        raise ValueError('AILIFE_SPECTATOR_PORT 必须是 1 到 65535 的整数')
    return port


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


def _with_session_lock(session_id, operation):
    """在同一 GameSession 的访问锁内执行完整 Runtime 操作。"""
    session, error = _lookup_session(session_id)
    if error is not None:
        return None, error
    with session._access_lock:
        return operation(session), None


class _SpectatorRequestHandler(BaseHTTPRequestHandler):
    """仅暴露正式 spectator snapshot 的 loopback HTTP handler。"""

    def log_message(self, format, *args):
        # stdio MCP 进程不应因浏览器 polling 产生额外日志噪声。
        return

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        parts = path.split('/')
        if (len(parts) != 4 or parts[:3] != ['', 'spectator', 'sessions']
                or not parts[3]):
            self._send_json(404, {'ok': False, 'error': 'not_found'})
            return

        snapshot, error = _with_session_lock(
            parts[3], lambda session: session.spectator_snapshot())
        if error is not None:
            self._send_json(404, error)
            return
        self._send_json(200, snapshot)

    def do_POST(self):
        self._send_json(404, {'ok': False, 'error': 'not_found'})


def _build_spectator_http_server(port):
    return HTTPServer((_SPECTATOR_HOST, port), _SpectatorRequestHandler)


def _start_spectator_http_server(port):
    httpd = _build_spectator_http_server(port)
    thread = threading.Thread(target=httpd.serve_forever,
                              name='ailife-spectator-http', daemon=True)
    thread.start()
    return httpd, thread


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
    decision, _ = _with_session_lock(
        session_id, lambda active_session: active_session.current_decision())
    return {'session_id': session_id, 'decision': _slim_decision(decision)}


def current_decision(session_id: str) -> Dict[str, Any]:
    """返回指定 session 的当前 decision；重复读取不改变任何状态。"""
    decision, error = _with_session_lock(
        session_id, lambda session: session.current_decision())
    if error is not None:
        return error
    return _slim_decision(decision)


def submit_action(session_id: str, decision_id: str,
                  action: Dict[str, Any]) -> Dict[str, Any]:
    """把 action 交给 GameSession.submit_action()，返回其结果与下一 decision。"""
    result, error = _with_session_lock(
        session_id, lambda session: session.submit_action(decision_id, action))
    if error is not None:
        return error
    return _slim_result(result)


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
    try:
        port = _spectator_port()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    httpd, thread = _start_spectator_http_server(port)
    try:
        server.run()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


if __name__ == '__main__':
    main()
