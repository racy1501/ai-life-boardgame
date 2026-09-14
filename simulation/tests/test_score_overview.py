# -*- coding: utf-8 -*-
"""score_overview 一次性 JIT hint 定向测试。

运行：
  cd simulation && python -m unittest tests.test_score_overview -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ailife.runtime import GameSession
from ailife.scoring import CURVE_A, curve_a


def make_session():
    return GameSession(seed=1, shuffle=False, forced_goals=[1, 2])


def overview_of(decision):
    return decision.get('rule_hints', {}).get('score_overview')


class TestScoreOverviewJIT(unittest.TestCase):

    def test_first_decision_carries_overview(self):
        decision = make_session().current_decision()
        self.assertEqual(decision['kind'], 'childhood_pick_1')
        self.assertIn('score_overview', decision.get('rule_hints', {}))

    def test_curve_numbers_derived_from_scoring_fact_source(self):
        text = overview_of(make_session().current_decision())
        # 正式 0..13 全表与 scoring.CURVE_A 逐值一致（防手抄漂移）。
        self.assertIn('0..%d 张依次为 %s'
                      % (len(CURVE_A) - 1,
                         '、'.join(str(v) for v in CURVE_A)), text)
        self.assertIn('41、48、55', text)
        # n>13 的 SA-CURVE 外推假设不向 AI 展示。
        for banned in ('外推', '假设', '+6'):
            self.assertNotIn(banned, text)

    def test_semantics_p_w_debuff(self):
        text = overview_of(make_session().current_decision())
        self.assertIn('P 按卡面 vp，持有即计、无需 active', text)
        self.assertIn('W 无统一基础分', text)
        self.assertIn('每张真实经历的 Debuff +1（被完全取消的触发不计）', text)

    def test_no_live_score_or_prediction_leak(self):
        text = overview_of(make_session().current_decision())
        for banned in ('当前总分', '预计', '最优', '建议'):
            self.assertNotIn(banned, text)

    def test_readonly_rereads_do_not_consume_or_duplicate(self):
        session = make_session()
        first = session.current_decision()
        second = session.current_decision()
        self.assertEqual(first, second)
        self.assertIn('score_overview', first.get('rule_hints', {}))

    def test_no_repeat_after_first_submit(self):
        session = make_session()
        first = session.current_decision()
        self.assertIn('score_overview', first.get('rule_hints', {}))
        result = session.submit_action(first['decision_id'],
                                       first['legal_actions'][0])
        self.assertTrue(result['ok'])
        second = session.current_decision()
        self.assertEqual(second['kind'], 'childhood_pick_2')
        self.assertIsNone(overview_of(second))
        # 推进到首个成年决策同样不重复（无论是否带 pre_roll_c11）。
        while second['kind'].startswith('childhood_pick'):
            result = session.submit_action(second['decision_id'],
                                           second['legal_actions'][0])
            self.assertTrue(result['ok'])
            second = session.current_decision()
        self.assertNotIn('score_overview', second.get('rule_hints', {}))


if __name__ == '__main__':
    unittest.main()
