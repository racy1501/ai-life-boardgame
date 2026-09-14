# -*- coding: utf-8 -*-
"""temp_res Event 一次性消耗展示定向测试。

运行：
  cd simulation && python -m unittest tests.test_event_temp_res_text -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ailife.cards import CARDS
from ailife.runtime import _card_effect_summary

DISCARD_CLAUSE = '一次性手牌，使用后弃置，未用部分随本次结算作废'


class TestEventTempResText(unittest.TestCase):

    def test_temp_res_events_carry_full_discard_clause(self):
        for cid in ('YE-01', 'YE-02'):
            sym, n = CARDS[cid]['temp_res']
            text = _card_effect_summary(CARDS[cid])
            self.assertEqual(
                text,
                '使用后本次购买结算临时提供 %s×%d；%s' % (sym, n,
                                                        DISCARD_CLAUSE),
                cid)
            # 数值由 temp_res 数据派生，非手抄。
            self.assertIn('%s×%d' % (sym, n), text)

    def test_childhood_one_time_text_unchanged(self):
        # 童年牌沿用文末既有一次性子句，措辞与数量不得受本次改动影响。
        self.assertEqual(
            _card_effect_summary(CARDS['C01']),
            '使用后本次购买结算临时提供 M×1；一次性手牌：实际使用后弃置')
        self.assertEqual(
            _card_effect_summary(CARDS['C03']),
            '使用后本次购买结算临时提供 R×1；一次性手牌：实际使用后弃置')
        self.assertEqual(
            _card_effect_summary(CARDS['C10']),
            '取得 P 类牌时，其成本中 1 个普通符号 -1；一次性手牌：实际使用后弃置')
        for cid in ('C01', 'C03'):
            self.assertNotIn('未用部分', _card_effect_summary(CARDS[cid]))

    def test_non_temp_res_events_not_affected(self):
        self.assertNotIn(DISCARD_CLAUSE,
                         _card_effect_summary(CARDS['YE-03']))
        self.assertNotIn(DISCARD_CLAUSE,
                         _card_effect_summary(CARDS['YE-04']))
        self.assertNotIn(DISCARD_CLAUSE,
                         _card_effect_summary(CARDS['YE-05']))
        self.assertNotIn(DISCARD_CLAUSE,
                         _card_effect_summary(CARDS['ME-01']))
        # ME-02 保留自带的"用后弃置"表述，不叠加新子句。
        self.assertEqual(
            _card_effect_summary(CARDS['ME-02']),
            '掷骰前声明使用：本回合 Debuff 不触发（不抽牌），用后弃置')

    def test_no_duplicate_discard_clause_on_childhood_temp_res(self):
        # C01-C04 同走 temp_res 分支，不得出现两句弃置声明。
        for cid in ('C01', 'C02', 'C03', 'C04'):
            text = _card_effect_summary(CARDS[cid])
            self.assertEqual(text.count('弃置'), 1, cid)


if __name__ == '__main__':
    unittest.main()
