# -*- coding: utf-8 -*-
import asyncio
import os
import sys
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import runtime_mcp
from ailife.runtime import GameSession


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ALIAS_REROLL_KEYS = ('normal_rerolls_remaining', 'normal_rerolls_available')
CANONICAL_REROLL_KEY = 'remaining_normal_rerolls'

# legal_actions 本身就是可直接提交的完整 action 的决策种类。
# post_roll_decision 的 legal_actions 只是描述符（normal_reroll 还要求 indices）；
# purchase_ready L1 的提交字段在 purchase_targets 条目上（Payload Slim v1 起不再
# 下发 legal_actions，lockstep 的空 legal_actions 守卫会安全停走），因此
# lockstep 测试到它为止。
DIRECTLY_SUBMITTABLE_KINDS = (
    'childhood_pick_1', 'childhood_pick_2', 'pre_roll_c11',
    'purchase_ready', 'maintenance_decision', 'placement_decision',
    'debuff_protection_decision', 'market_protection_decision',
    'mh04_decision',
)


def input_schema(tool):
    for attribute in ('input_schema', 'inputSchema'):
        if hasattr(tool, attribute):
            return getattr(tool, attribute)
    raise AssertionError('tool 没有 input schema')


def result_is_error(result):
    for attribute in ('isError', 'is_error'):
        if hasattr(result, attribute):
            return getattr(result, attribute)
    return False


def result_text(result):
    return result.content[0].text


class RuntimeMcpTestCase(unittest.TestCase):
    def setUp(self):
        runtime_mcp._SESSIONS.clear()


class TestStartGame(RuntimeMcpTestCase):
    def test_returns_unpredictable_session_id_and_first_decision(self):
        result = runtime_mcp.start_game(seed=0, forced_goals=[1, 2])
        self.assertEqual(set(result), {'session_id', 'decision'})
        session_id = result['session_id']
        self.assertEqual(uuid.UUID(session_id).version, 4)
        self.assertEqual(result['decision']['kind'], 'childhood_pick_1')
        self.assertTrue(result['decision']['legal_actions'])
        self.assertIsInstance(runtime_mcp._SESSIONS[session_id], GameSession)

    def test_two_sessions_get_distinct_ids(self):
        first = runtime_mcp.start_game(seed=0, forced_goals=[1, 2])
        second = runtime_mcp.start_game(seed=0, forced_goals=[1, 2])
        self.assertNotEqual(first['session_id'], second['session_id'])
        self.assertEqual(len(runtime_mcp._SESSIONS), 2)

    def test_same_seed_reproduces_same_opening(self):
        first = runtime_mcp.start_game(seed=5, forced_goals=[1, 2])
        second = runtime_mcp.start_game(seed=5, forced_goals=[1, 2])
        self.assertEqual(first['decision'], second['decision'])

    def test_forced_goals_reach_the_underlying_game(self):
        result = runtime_mcp.start_game(seed=5, forced_goals=[3, 7])
        session = runtime_mcp._SESSIONS[result['session_id']]
        self.assertEqual(session.game.goals, [3, 7])

    def test_seed_and_forced_goals_are_optional(self):
        result = runtime_mcp.start_game()
        self.assertEqual(result['decision']['kind'], 'childhood_pick_1')

    def test_rejects_invalid_arguments(self):
        invalid = ({'seed': 'x'}, {'seed': True}, {'seed': 1.5},
                   {'forced_goals': [1]}, {'forced_goals': [1, 2, 3]},
                   {'forced_goals': 'ab'}, {'forced_goals': [1, 'b']})
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    runtime_mcp.start_game(**kwargs)
        self.assertEqual(runtime_mcp._SESSIONS, {})


class TestCurrentDecision(RuntimeMcpTestCase):
    def test_is_read_only_and_matches_direct_game_session(self):
        session_id = runtime_mcp.start_game(seed=0, forced_goals=[1, 2])['session_id']
        reference = GameSession(seed=0, forced_goals=[1, 2])
        first = runtime_mcp.current_decision(session_id)
        second = runtime_mcp.current_decision(session_id)
        self.assertEqual(first, second)
        self.assertEqual(first, reference.current_decision())

        # 多次读取后仍然能提交同一个 decision。
        action = {'card_id': first['legal_actions'][0]['card_id']}
        self.assertTrue(runtime_mcp.submit_action(
            session_id, first['decision_id'], action)['ok'])

    def test_unknown_session_is_reported_without_raising(self):
        result = runtime_mcp.current_decision(str(uuid.uuid4()))
        self.assertEqual(result, {'ok': False, 'error': 'unknown_session_id'})

    def test_session_id_dies_with_the_registry(self):
        # 模拟 server 重启：旧 session_id 失效，重新 start_game 即可继续。
        result = runtime_mcp.start_game(seed=0, forced_goals=[1, 2])
        runtime_mcp._SESSIONS.clear()
        self.assertEqual(runtime_mcp.current_decision(result['session_id']),
                         {'ok': False, 'error': 'unknown_session_id'})
        self.assertEqual(
            runtime_mcp.start_game(seed=0, forced_goals=[1, 2])['decision'],
            result['decision'])


class TestSubmitAction(RuntimeMcpTestCase):
    def start(self):
        result = runtime_mcp.start_game(seed=0, forced_goals=[1, 2])
        return result['session_id'], result['decision']

    def test_legal_action_advances_and_returns_next_decision(self):
        session_id, decision = self.start()
        action = {'card_id': decision['legal_actions'][0]['card_id']}
        result = runtime_mcp.submit_action(session_id, decision['decision_id'],
                                           action)
        self.assertTrue(result['ok'])
        self.assertEqual(result['accepted_action'], action)
        self.assertEqual(result['decision']['kind'], 'childhood_pick_2')
        self.assertEqual(result['decision'],
                         runtime_mcp.current_decision(session_id))

    def test_stale_or_unknown_decision_id_is_rejected_without_state_change(self):
        session_id, decision = self.start()
        action = {'card_id': decision['legal_actions'][0]['card_id']}
        self.assertTrue(runtime_mcp.submit_action(
            session_id, decision['decision_id'], action)['ok'])
        current = runtime_mcp.current_decision(session_id)

        for decision_id in (decision['decision_id'], 'childhood_pick_1:99'):
            with self.subTest(decision_id=decision_id):
                result = runtime_mcp.submit_action(session_id, decision_id, action)
                self.assertFalse(result['ok'])
                self.assertEqual(result['error'], 'stale_or_unknown_decision_id')
                self.assertEqual(result['decision'], current)
                self.assertEqual(runtime_mcp.current_decision(session_id), current)

        # 状态没有被 stale 提交破坏：当前 decision 仍可正常推进。
        self.assertTrue(runtime_mcp.submit_action(
            session_id, current['decision_id'],
            {'card_id': current['legal_actions'][0]['card_id']})['ok'])

    def test_illegal_action_is_rejected_without_state_change(self):
        session_id, decision = self.start()
        before = runtime_mcp.current_decision(session_id)
        illegal = ({'card_id': 'C99'},
                   {'card_id': before['legal_actions'][0]['card_id'], 'x': 1},
                   {},
                   {'choice': 'skip'},
                   [])
        for action in illegal:
            with self.subTest(action=action):
                result = runtime_mcp.submit_action(session_id,
                                                   decision['decision_id'], action)
                self.assertFalse(result['ok'])
                self.assertIn(result['error'],
                              ('invalid_action', 'illegal_action'))
                self.assertEqual(result['decision'], before)
                self.assertEqual(runtime_mcp.current_decision(session_id), before)

    def test_unknown_session_is_reported_without_raising(self):
        result = runtime_mcp.submit_action(str(uuid.uuid4()), 'childhood_pick_1:0',
                                           {'card_id': 'C08'})
        self.assertEqual(result, {'ok': False, 'error': 'unknown_session_id'})
        self.assertNotIn('decision', result)


class TestRuntimePassthrough(RuntimeMcpTestCase):
    def test_decisions_stay_in_lockstep_with_runtime(self):
        session_id = runtime_mcp.start_game(seed=0, forced_goals=[1, 2])['session_id']
        reference = GameSession(seed=0, forced_goals=[1, 2])
        kinds = []

        for _ in range(12):
            expected = reference.current_decision()
            actual = runtime_mcp.current_decision(session_id)
            kinds.append(expected['kind'])

            # 只裁同值冗余别名，其余字段逐个透传。
            dropped = {key for key in ALIAS_REROLL_KEYS if key in expected}
            self.assertEqual(set(expected) - set(actual), dropped)
            for key, value in actual.items():
                self.assertEqual(value, expected[key], key)
            if dropped:
                self.assertIn(CANONICAL_REROLL_KEY, actual)
                self.assertEqual(actual[CANONICAL_REROLL_KEY],
                                 expected[CANONICAL_REROLL_KEY])

            if (expected['kind'] not in DIRECTLY_SUBMITTABLE_KINDS
                    or not expected['legal_actions']):
                break
            action = expected['legal_actions'][0]
            mcp_result = runtime_mcp.submit_action(session_id,
                                                   expected['decision_id'], action)
            direct_result = reference.submit_action(expected['decision_id'], action)
            self.assertEqual(mcp_result['ok'], direct_result['ok'])
            if not direct_result['ok']:
                self.assertEqual(mcp_result['error'], direct_result['error'])
                break
            self.assertEqual(mcp_result['accepted_action'],
                             direct_result['accepted_action'])

        self.assertIn('post_roll_decision', kinds)
        expected = reference.current_decision()
        actual = runtime_mcp.current_decision(session_id)
        self.assertTrue(all(key in expected for key in ALIAS_REROLL_KEYS))
        self.assertTrue(all(key not in actual for key in ALIAS_REROLL_KEYS))

    def test_slim_decision_only_drops_identical_aliases(self):
        decision = {'kind': 'post_roll_decision',
                    CANONICAL_REROLL_KEY: 2,
                    'normal_rerolls_remaining': 2,
                    'normal_rerolls_available': 2}
        self.assertEqual(runtime_mcp._slim_decision(decision),
                         {'kind': 'post_roll_decision',
                          CANONICAL_REROLL_KEY: 2})

    def test_slim_decision_keeps_different_values(self):
        decision = {CANONICAL_REROLL_KEY: 2,
                    'normal_rerolls_remaining': 1,
                    'normal_rerolls_available': 0}
        self.assertEqual(runtime_mcp._slim_decision(decision), decision)

    def test_slim_decision_leaves_other_decisions_untouched(self):
        decision = {'kind': 'childhood_pick_1', 'legal_actions': []}
        self.assertEqual(runtime_mcp._slim_decision(decision), decision)


@unittest.skipIf(runtime_mcp.server is None,
                 '未安装官方 mcp SDK：请用 uv run --no-project --with mcp 运行')
class TestServerWiring(RuntimeMcpTestCase):
    def test_exposes_only_the_three_tools(self):
        tools = asyncio.run(runtime_mcp.server.list_tools())
        self.assertEqual(sorted(tool.name for tool in tools),
                         ['current_decision', 'start_game', 'submit_action'])

    def test_tool_schemas_match_the_wrapper_signatures(self):
        tools = {tool.name: input_schema(tool)
                 for tool in asyncio.run(runtime_mcp.server.list_tools())}
        self.assertEqual(sorted(tools['start_game']['properties']),
                         ['forced_goals', 'seed'])
        self.assertEqual(tools['start_game'].get('required', []), [])
        self.assertEqual(sorted(tools['current_decision']['required']),
                         ['session_id'])
        self.assertEqual(sorted(tools['submit_action']['properties']),
                         ['action', 'decision_id', 'session_id'])
        self.assertEqual(sorted(tools['submit_action']['required']),
                         ['action', 'decision_id', 'session_id'])

    def test_unknown_session_reason_survives_the_transport(self):
        result = asyncio.run(runtime_mcp.server.call_tool(
            'current_decision', {'session_id': 'nope'}))
        self.assertFalse(result_is_error(result))
        self.assertIn('unknown_session_id', result_text(result))


if __name__ == '__main__':
    unittest.main()
