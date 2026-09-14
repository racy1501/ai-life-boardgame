# -*- coding: utf-8 -*-
import asyncio
import copy
import http.client
import json
import os
import sys
import threading
import unittest
import uuid
from unittest.mock import Mock, patch

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

    def start_spectator_server(self):
        httpd, thread = runtime_mcp._start_spectator_http_server(0)
        self.addCleanup(httpd.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(httpd.shutdown)
        return httpd.server_address[1]

    def spectator_get(self, port, path):
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
        connection.request('GET', path)
        response = connection.getresponse()
        body = response.read()
        headers = dict(response.getheaders())
        connection.close()
        return response.status, headers, json.loads(body.decode('utf-8'))

    def spectator_state(self, session):
        game = session.game
        return copy.deepcopy((
            game.turn, game.stage, game.dice, game.market, game.fate_market,
            game.cv, session._decision_revision, session._recent_events,
            session._next_recent_event_seq,
        ))


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


class TestSpectatorHttpBridge(RuntimeMcpTestCase):
    def test_get_returns_same_session_snapshot_with_browser_headers(self):
        started = runtime_mcp.start_game(seed=0, forced_goals=[1, 2])
        session_id = started['session_id']
        session = runtime_mcp._SESSIONS[session_id]
        port = self.start_spectator_server()

        status, headers, payload = self.spectator_get(
            port, '/spectator/sessions/' + session_id)

        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Type'],
                         'application/json; charset=utf-8')
        self.assertEqual(headers['Access-Control-Allow-Origin'], '*')
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(payload, session.spectator_snapshot())
        self.assertIn('recent_events', payload)
        self.assertTrue(hasattr(session, '_access_lock'))

    def test_unknown_session_and_other_paths_are_structured_404(self):
        port = self.start_spectator_server()
        status, _, payload = self.spectator_get(
            port, '/spectator/sessions/' + str(uuid.uuid4()))
        self.assertEqual(status, 404)
        self.assertEqual(payload, {'ok': False, 'error': 'unknown_session_id'})

        status, _, payload = self.spectator_get(port, '/not-a-route')
        self.assertEqual(status, 404)
        self.assertEqual(payload, {'ok': False, 'error': 'not_found'})

    def test_repeated_gets_are_read_only_and_see_submit_update(self):
        started = runtime_mcp.start_game(seed=0, forced_goals=[1, 2])
        session_id, decision = started['session_id'], started['decision']
        session = runtime_mcp._SESSIONS[session_id]
        port = self.start_spectator_server()
        before = self.spectator_state(session)

        first = self.spectator_get(port, '/spectator/sessions/' + session_id)
        second = self.spectator_get(port, '/spectator/sessions/' + session_id)
        self.assertEqual(first[2], second[2])
        self.assertEqual(self.spectator_state(session), before)

        first_pick = runtime_mcp.submit_action(
            session_id, decision['decision_id'], decision['legal_actions'][0])
        second_pick = runtime_mcp.submit_action(
            session_id, first_pick['decision']['decision_id'],
            first_pick['decision']['legal_actions'][0])
        self.assertTrue(second_pick['ok'])

        status, _, updated = self.spectator_get(
            port, '/spectator/sessions/' + session_id)
        self.assertEqual(status, 200)
        self.assertTrue(updated['dice']['values'])
        self.assertTrue(any(event['type'] == 'dice_rolled'
                            for event in updated['recent_events']))

    def test_get_waits_for_locked_runtime_action_without_duplicate_advance(self):
        started = runtime_mcp.start_game(seed=0, forced_goals=[1, 2])
        session_id, decision = started['session_id'], started['decision']
        session = runtime_mcp._SESSIONS[session_id]
        port = self.start_spectator_server()
        action_entered = threading.Event()
        release_action = threading.Event()
        snapshot_entered = threading.Event()
        submit_result = []
        get_result = []
        original_submit = session.submit_action
        original_snapshot = session.spectator_snapshot

        def paused_submit(*args, **kwargs):
            result = original_submit(*args, **kwargs)
            action_entered.set()
            self.assertTrue(release_action.wait(2))
            return result

        def observed_snapshot():
            snapshot_entered.set()
            return original_snapshot()

        with patch.object(session, 'submit_action', side_effect=paused_submit), \
             patch.object(session, 'spectator_snapshot', side_effect=observed_snapshot):
            action_thread = threading.Thread(
                target=lambda: submit_result.append(runtime_mcp.submit_action(
                    session_id, decision['decision_id'],
                    decision['legal_actions'][0])))
            action_thread.start()
            self.assertTrue(action_entered.wait(2))

            get_thread = threading.Thread(
                target=lambda: get_result.append(self.spectator_get(
                    port, '/spectator/sessions/' + session_id)))
            get_thread.start()
            self.assertFalse(snapshot_entered.wait(0.1))
            release_action.set()
            action_thread.join(2)
            get_thread.join(2)

        self.assertFalse(action_thread.is_alive())
        self.assertFalse(get_thread.is_alive())
        self.assertEqual(len(submit_result), 1)
        self.assertTrue(submit_result[0]['ok'])
        self.assertEqual(session.game.childhood_draft_round, 1)
        self.assertEqual(len(get_result), 1)
        self.assertEqual(get_result[0][0], 200)
        self.assertTrue(snapshot_entered.is_set())

    def test_port_validation_and_main_closes_listener_on_return_or_error(self):
        with patch.dict(os.environ, {'AILIFE_SPECTATOR_PORT': '4321'}):
            self.assertEqual(runtime_mcp._spectator_port(), 4321)
        with patch.dict(os.environ, {'AILIFE_SPECTATOR_PORT': 'invalid'}):
            with self.assertRaises(ValueError):
                runtime_mcp._spectator_port()

        for side_effect in (None, RuntimeError('mcp stopped')):
            with self.subTest(side_effect=side_effect):
                fake_httpd = Mock()
                fake_thread = Mock()
                fake_server = Mock()
                fake_server.run.side_effect = side_effect
                with patch.object(runtime_mcp, 'server', fake_server), \
                     patch.object(runtime_mcp, '_start_spectator_http_server',
                                  return_value=(fake_httpd, fake_thread)):
                    if side_effect is None:
                        runtime_mcp.main()
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'mcp stopped'):
                            runtime_mcp.main()
                fake_httpd.shutdown.assert_called_once_with()
                fake_httpd.server_close.assert_called_once_with()
                fake_thread.join.assert_called_once_with()


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
