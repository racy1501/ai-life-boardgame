# -*- coding: utf-8 -*-
"""终局 flex 最终指定（final_flex_designation）定向测试。

运行：
  cd simulation && python -m unittest tests.test_final_flex_designation -v
"""
import copy
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ailife.cards import CARDS
from ailife.runtime import GameSession, _card_effect_summary
from ailife.scoring import flex_actives, full_score


def closeout_session(cv, goals, turn=23):
    """合成『最终回合 closeout 已完成』的 Runtime 状态（真实派发路径）。"""
    session = GameSession(seed=1, shuffle=False, forced_goals=list(goals))
    game = session.game
    game.childhood_complete = True
    game.turn = turn
    game.hand = []
    game.cv = {cls: list(cards) for cls, cards in cv.items()}
    game.goals = list(goals)
    game.game_over = True
    session._turn_closeout_resolved = True
    session._previous_turn_result = {
        'completed_turn': turn, 'next_turn': None,
        'maintenance_result': {'paid': []},
        'market_cleanup_result': {'purchased_card_ids': []},
    }
    return session


class TestFinalFlexDesignation(unittest.TestCase):
    """协议测试：合成终局板面驱动真实 _decision_kind / submit 链路。"""

    FLEX_CV = {
        'H': ['YH-01'], 'K': ['YK-01'], 'R': [],
        'W': ['MW-03'], 'P': ['YP-01'],
    }

    def flex_session(self, goals=(1, 2)):
        session = closeout_session(self.FLEX_CV, goals)
        decision = session.current_decision()
        self.assertEqual(decision['kind'], 'final_flex_designation')
        return session, decision

    def test_decision_payload_and_action_format(self):
        session, decision = self.flex_session()
        self.assertEqual(decision['decision_id'], 'final_flex_designation:23')
        self.assertEqual(decision['active_flex_cards'],
                         [{'card_id': 'MW-03', 'name': '独立接案',
                           'allowed': ['K', 'R']}])
        self.assertEqual(decision['action_format'],
                         {'designations': {'MW-03': 'K'}})
        self.assertEqual(decision['legal_actions'], [])
        self.assertTrue(decision['life_goals'])

    def test_current_decision_readonly(self):
        session, decision = self.flex_session()
        rng_state = session.game.rng.getstate()
        again = session.current_decision()
        self.assertEqual(again['decision_id'], decision['decision_id'])
        self.assertEqual(again['kind'], 'final_flex_designation')
        self.assertIsNone(session._final_flex_designation)
        self.assertEqual(session.game.rng.getstate(), rng_state)

    def test_invalid_designations_rejected_without_state_change(self):
        bad_actions = [
            'not-a-dict',
            {},
            {'designations': 'MW-03'},
            {'designations': {}},                      # 漏卡
            {'designations': {'MW-03': 'R', 'OK-02': 'K'}},  # 多卡
            {'designations': {'MW-03': 'MONEY'}},      # 非法符号
            {'designations': {'MW-03': ['R']}},        # 非字符串符号
            {'choice': 'skip'},                        # 形状错误
        ]
        for action in bad_actions:
            session, decision = self.flex_session()
            result = session.submit_action(decision['decision_id'], action)
            self.assertEqual(result['ok'], False, action)
            self.assertIn(result['error'],
                          ('invalid_action', 'illegal_action'), action)
            self.assertIsNone(session._final_flex_designation)
            self.assertEqual(session.current_decision()['kind'],
                             'final_flex_designation')

    def test_submit_then_game_over_uses_designation(self):
        session, decision = self.flex_session()
        result = session.submit_action(
            decision['decision_id'], {'designations': {'MW-03': 'R'}})
        self.assertTrue(result['ok'])
        final = session.current_decision()
        self.assertEqual(final['kind'], 'game_over')
        self.assertTrue(final['scoring_ready'])
        self.assertEqual(final['score']['designation'], {'MW-03': 'R'})
        # 显式指定进入 provides；与本板面 oracle（取首符号 K）可区分。
        self.assertIn(('R', 1), [tuple(p) for p in final['score']['provides']])
        # game_over 后不可再提交任何动作。
        refused = session.submit_action(
            final['decision_id'], {'choice': 'skip'})
        self.assertEqual(refused['ok'], False)
        self.assertEqual(refused['error'], 'no_action_expected')

    def test_stale_decision_id_rejected(self):
        session, decision = self.flex_session()
        result = session.submit_action(
            decision['decision_id'] + ':x', {'designations': {'MW-03': 'R'}})
        self.assertEqual(result['ok'], False)
        self.assertEqual(result['error'], 'stale_or_unknown_decision_id')
        self.assertIsNone(session._final_flex_designation)

    def test_flex_card_text_mentions_final_designation(self):
        text = _card_effect_summary(CARDS['MW-03'])
        self.assertIn('终局计分前会对仍生效的本卡做一次最终指定', text)
        self.assertNotIn('终局按最终指定计分', text)


class TestFinalFlexScoringImpact(unittest.TestCase):
    """LG13/LG16 板面：显式指定实际改变 Life Goal 分数。"""

    CV = {
        # active 产出：YH-03(H1,R1) + YR-04(R1,M1) + MW-03(M2, flex)
        'H': ['YH-03'], 'K': [], 'R': ['YR-04'],
        'W': ['MW-03'], 'P': [],
    }

    def test_lg13_explicit_designation_changes_score(self):
        session = closeout_session(self.CV, goals=[13, 3])
        decision = session.current_decision()
        self.assertEqual(decision['kind'], 'final_flex_designation')
        result = session.submit_action(
            decision['decision_id'], {'designations': {'MW-03': 'K'}})
        self.assertTrue(result['ok'])
        score_k = session.current_decision()['score']
        # K → 产出含 K：种类 {H,R,K,M}=4 → LG13 = 2×4 = 8
        self.assertEqual(score_k['lg_scores'][0], 8)
        self.assertEqual(score_k['designation'], {'MW-03': 'K'})

        session_r = closeout_session(self.CV, goals=[13, 3])
        decision_r = session_r.current_decision()
        result_r = session_r.submit_action(
            decision_r['decision_id'], {'designations': {'MW-03': 'R'}})
        self.assertTrue(result_r['ok'])
        score_r = session_r.current_decision()['score']
        # R → 种类只有 {H,R,M}=3 → LG13 = 6
        self.assertEqual(score_r['lg_scores'][0], 6)
        self.assertEqual(score_r['total'], score_k['total'] - 2)

    def test_lg16_explicit_designation_changes_score(self):
        # MK-04 flex=(K,R,M) 含 M：flex=M 时 M 产出 +1，LG16 可分差。
        cv = {'H': ['YH-03'], 'K': ['MK-04'], 'R': ['YR-04'],
              'W': [], 'P': []}
        session = closeout_session(cv, goals=[16, 3])
        decision = session.current_decision()
        session.submit_action(decision['decision_id'],
                              {'designations': {'MK-04': 'M'}})
        score_m = session.current_decision()['score']
        # M → M 产出 = YR-04 的 1 + flex 1 = 2 → LG16 = 4
        self.assertEqual(score_m['lg_scores'][0], 4)

        session_k = closeout_session(cv, goals=[16, 3])
        decision_k = session_k.current_decision()
        session_k.submit_action(decision_k['decision_id'],
                                {'designations': {'MK-04': 'K'}})
        score_k = session_k.current_decision()['score']
        # K → M 产出只剩 YR-04 的 1 → LG16 = 2
        self.assertEqual(score_k['lg_scores'][0], 2)

    def test_oracle_path_unchanged_without_designation(self):
        # full_score 不传 flex_designation → best_designation oracle（取最优）。
        score = full_score({cls: list(cards)
                            for cls, cards in self.CV.items()}, [13, 3])
        self.assertEqual(score['designation'], {'MW-03': 'K'})
        self.assertEqual(score['lg_scores'][0], 8)


class TestNoFlexGameOverUnchanged(unittest.TestCase):
    """无 active flex：不插入决策，game_over 路径与分数完全不变。"""

    CV = {
        'H': ['YH-01'], 'K': ['YK-01'], 'R': [],
        'W': ['YW-01'], 'P': ['YP-01'],
    }

    def test_game_over_reached_directly(self):
        session = closeout_session(self.CV, goals=[1, 2])
        decision = session.current_decision()
        self.assertEqual(decision['kind'], 'game_over')
        self.assertTrue(decision['scoring_ready'])
        self.assertEqual(
            decision['score'],
            full_score(session.game.cv, session.game.goals,
                       **session.game.scoring_counts()))
        refused = session.submit_action(
            decision['decision_id'], {'designations': {}})
        self.assertEqual(refused['error'], 'no_action_expected')

    def test_flex_actives_empty_for_no_flex_cv(self):
        self.assertEqual(flex_actives(
            {cls: list(cards) for cls, cards in self.CV.items()}), [])


class TestFullPlaythroughSmoke(unittest.TestCase):
    """真实 GameSession 整局 auto-pilot 冒烟：无 flex 终局直达 game_over。"""

    def _pre_roll_action(self, decision):
        choices = {item['card_id']: item['allowed_resources'][0]
                   for item in decision.get('flexible_resource_choices', [])}
        return {'flex_resource_choices': choices}

    def _drive(self, session):
        final_flex_seen = 0
        for _ in range(4000):
            decision = session.current_decision()
            kind = decision['kind']
            if kind == 'game_over':
                return final_flex_seen, decision
            if kind.startswith('childhood_pick'):
                action = decision['legal_actions'][0]
            elif kind == 'pre_roll_decision':
                action = self._pre_roll_action(decision)
            elif kind == 'post_roll_decision':
                action = {'choice': 'proceed_to_purchase'}
            elif kind == 'purchase_ready':
                # 永不购买：确保 cv 恒为空、终局必无 active flex。
                action = {'ordinary_card_ids': [], 'fate_card_id': None}
            elif kind == 'final_flex_designation':
                final_flex_seen += 1
                action = decision['action_format']
            elif decision.get('legal_actions'):
                action = next(
                    (a for a in decision['legal_actions']
                     if a == {'choice': 'skip'} or a.get('placement') == 'top'),
                    decision['legal_actions'][0])
            else:
                self.fail('no legal action for kind %s' % kind)
            result = session.submit_action(decision['decision_id'], action)
            self.assertTrue(result['ok'],
                            '%s -> %r' % (kind, action))
        self.fail('game did not terminate')

    def test_playthrough_reaches_game_over_without_final_flex(self):
        session = GameSession(seed=42, shuffle=False, forced_goals=[1, 2])
        final_flex_seen, decision = self._drive(session)
        self.assertEqual(final_flex_seen, 0)
        self.assertEqual(decision['kind'], 'game_over')
        self.assertEqual({c: len(session.game.cv[c]) for c in 'HKRWP'},
                         {c: 0 for c in 'HKRWP'})


if __name__ == '__main__':
    unittest.main()
