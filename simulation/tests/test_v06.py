# -*- coding: utf-8 -*-
import os
import random
import sys
import unittest
from collections import Counter
from copy import deepcopy
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ailife.cards import DEBUFFS, FATES
from ailife.engine import CONFIGS, Game
from ailife.strategies import BaseStrategy, STRATEGIES
from ailife import scoring


class V06Scripted(BaseStrategy):
    def __init__(self, game):
        super().__init__(game)
        self.joint_choice = ((), None)
        self.debuff_choice = {}
        self.active_choice = None
        self.goal_choice = None
        self.reroll_choice = set()

    def declare_pre_roll(self):
        return {}

    def use_ye03(self):
        return False

    def choose_joint_plan(self, actions):
        return self.joint_choice if self.joint_choice in actions else ((), None)

    def debuff_response(self, cancel_options, shorten_option):
        return dict(self.debuff_choice)

    def choose_active_reset(self, options):
        return self.active_choice

    def choose_goal_replacement(self, candidates):
        if self.goal_choice == 'first' and candidates:
            return (0, candidates[0])
        return self.goal_choice

    def choose_reroll(self, round_no, total_rounds):
        return set(self.reroll_choice)


def make_v06(seed=7, strategy=V06Scripted, goals=(1, 2), shuffle=False):
    return Game(CONFIGS['V06'], strategy, random.Random(seed), shuffle=shuffle,
                forced_goals=list(goals))


def activate_debuff(game, cid, remaining=3):
    game.current_debuff = cid
    game.debuff_active_from_turn = game.turn
    game.debuff_turns_remaining = remaining
    game.debuff_active_turns = 0
    if cid in game.debuff_deck:
        game.debuff_deck.remove(cid)


def activate_fate(game, cid):
    game.fate_stack = [cid]
    game.active_fate = cid
    game.fate_active_from_turn = game.turn
    for zone in (game.fate_deck, game.fate_market, game.fate_discard):
        if cid in zone:
            zone.remove(cid)


class TestDebuffV06(unittest.TestCase):
    def test_final_real_bl_accumulates_across_turns(self):
        g = make_v06()
        for turn, dice, total in ((1, ['BL', 'BL'], 2),
                                  (2, ['BL', 'BL'], 4)):
            g.turn = turn
            g.dice = dice
            self.assertEqual(g.record_final_bad_luck(), 2)
            self.assertEqual(g.bad_luck_accumulator, total)
            g._debuff_check()
            self.assertIsNone(g.current_debuff)
        g.turn = 3
        g.dice = ['BL']
        self.assertEqual(g.record_final_bad_luck(), 1)
        g._debuff_check()
        self.assertEqual(g.current_debuff, 'D01')
        self.assertEqual(g.bad_luck_accumulator, 0)

    def test_rerolled_away_bl_is_not_recorded(self):
        g = make_v06()
        g.turn = 1
        g.abebe_held = True
        g.dice = ['BL', 'H']
        g._forced = ['K']
        self.assertEqual(g.apply_normal_reroll({0}, c12_index=0), 1)
        self.assertEqual(g.dice, ['K', 'H'])
        self.assertEqual(g.record_final_bad_luck(), 0)
        self.assertEqual(g.bad_luck_accumulator, 0)

    def test_final_bl_is_recorded_only_once_per_turn(self):
        g = make_v06()
        g.turn = 1
        g.dice = ['BL'] * 6
        self.assertEqual(g.record_final_bad_luck(), 6)
        self.assertEqual(g.record_final_bad_luck(), 0)
        g._debuff_check()
        g._debuff_check()
        self.assertEqual(g.stats.game['debuff_drawn'], ['D01'])

    def test_five_accumulated_bl_triggers_one_debuff_and_clears(self):
        g = make_v06()
        g.bad_luck_accumulator = 5
        g._debuff_check()
        self.assertEqual(g.current_debuff, 'D01')
        self.assertEqual(len(g.debuff_deck), 11)
        self.assertEqual(g.stats.game['debuff_triggers'], 1)
        self.assertEqual(g.bad_luck_accumulator, 0)

    def test_six_bl_still_triggers_one_debuff(self):
        g = make_v06()
        g.bad_luck_accumulator = 6
        g._debuff_check()
        self.assertEqual(g.current_debuff, 'D01')
        self.assertEqual(g.stats.game['debuff_drawn'], ['D01'])
        self.assertEqual(g.bad_luck_accumulator, 0)

    def test_draw_without_replacement_and_no_reshuffle_when_empty(self):
        g = make_v06()
        g.bad_luck_accumulator = 5
        g._debuff_check()
        g.turn += 1
        g.bad_luck_accumulator = 5
        g._debuff_check()
        self.assertEqual(g.debuff_history, ['D01'])
        self.assertEqual(g.current_debuff, 'D02')
        self.assertNotIn('D01', g.debuff_deck)
        self.assertNotIn('D02', g.debuff_deck)
        g.debuff_deck = []
        g._finish_current_debuff('replaced_early')
        g.turn += 1
        g.bad_luck_accumulator = 5
        g._debuff_check()
        self.assertIsNone(g.current_debuff)
        self.assertEqual(g.debuff_deck, [])

    def test_starts_next_turn_and_lasts_exactly_three_complete_turns(self):
        g = make_v06()
        g.turn = 1
        g.bad_luck_accumulator = 5
        g._debuff_check()
        self.assertIsNone(g.active_debuff_card())
        for turn, remaining in ((2, 2), (3, 1)):
            g.turn = turn
            self.assertEqual(g.active_debuff_card()['id'], 'D01')
            g._advance_debuff()
            self.assertEqual(g.debuff_turns_remaining, remaining)
        g.turn = 4
        g._advance_debuff()
        self.assertIsNone(g.current_debuff)
        self.assertEqual(g.debuff_history, ['D01'])
        self.assertEqual(g.stats.run['natural_expiry'], 1)

    def test_new_debuff_replaces_old_and_old_enters_history(self):
        g = make_v06()
        activate_debuff(g, 'D01')
        g.bad_luck_accumulator = 5
        g._debuff_check()
        self.assertEqual(g.debuff_history, ['D01'])
        self.assertEqual(g.current_debuff, 'D02')
        self.assertEqual(g.stats.run['replaced_early'], 1)

    def test_cancel_before_draw_changes_no_debuff_zone(self):
        g = make_v06()
        before = list(g.debuff_deck)
        g.hand = ['C06']
        g.strat.debuff_choice = {'cancel': 'C06'}
        g.bad_luck_accumulator = 5
        g._debuff_check()
        self.assertEqual(g.debuff_deck, before)
        self.assertIsNone(g.current_debuff)
        self.assertEqual(g.debuff_history, [])
        self.assertNotIn('C06', g.hand)

    def test_mh02_cancels_once_and_me02_pre_cancel_consumes_without_draw(self):
        g = make_v06()
        g.cv['H'] = ['MH-02']
        g.strat.debuff_choice = {'cancel': 'MH-02'}
        g.bad_luck_accumulator = 5
        g._debuff_check()
        self.assertIn('MH-02', g.used_once_cards)
        g.turn += 1
        g.bad_luck_accumulator = 5
        g._debuff_check()
        self.assertIsNotNone(g.current_debuff)

        h = make_v06()
        h.pre_debuff_cancel = 'ME-02'
        before = list(h.debuff_deck)
        h.bad_luck_accumulator = 5
        h._debuff_check()
        self.assertEqual(h.debuff_deck, before)
        self.assertIsNone(h.current_debuff)

    def test_oh01_can_shorten_to_two_turns(self):
        g = make_v06()
        g.cv['H'] = ['OH-01']
        g.strat.debuff_choice = {'shorten': 'OH-01'}
        g.bad_luck_accumulator = 5
        g.pool = Counter({'H': 1})
        g._debuff_check()
        self.assertEqual(g.debuff_turns_remaining, 2)
        self.assertEqual(g.pool['H'], 0)

    def test_current_at_game_end_counts_as_experienced_and_scores_one(self):
        g = make_v06(goals=(1, 2))
        activate_debuff(g, 'D01')
        self.assertEqual(g.debuff_experienced_count(), 1)
        score = scoring.full_score(g.cv, g.goals, **g.scoring_counts())
        self.assertEqual(score['debuff_vp'], 1)
        self.assertEqual(score['total'], 1)

    def test_d08_virtual_bl_triggers_but_cannot_pay(self):
        g = make_v06()
        activate_debuff(g, 'D08')
        g.bad_luck_accumulator = 4
        g._debuff_check()
        self.assertIsNotNone(g.current_debuff)
        self.assertEqual(g.stats.run['d08_trigger_mattered'], 1)
        self.assertEqual(g.bad_luck_accumulator, 0)

        h = make_v06()
        activate_debuff(h, 'D08')
        h.pool = Counter()
        self.assertIsNone(h._joint_plan((), 'F07'))

    def test_f11_virtual_bl_can_trigger_debuff_but_cannot_pay(self):
        g = make_v06()
        activate_fate(g, 'F11')
        g.bad_luck_accumulator = 4
        g._debuff_check()
        self.assertIsNotNone(g.current_debuff)
        self.assertEqual(g.bad_luck_accumulator, 0)

        h = make_v06()
        activate_fate(h, 'F11')
        h.pool = Counter()
        self.assertIsNone(h._joint_plan((), 'F07'))

    def test_virtual_bl_does_not_enter_accumulator_below_threshold(self):
        g = make_v06()
        activate_debuff(g, 'D08')
        g.bad_luck_accumulator = 3
        status = g.debuff_trigger_status()
        self.assertEqual(status['virtual_bl'], 1)
        self.assertFalse(status['triggered'])
        g._debuff_check()
        self.assertEqual(g.bad_luck_accumulator, 3)


class TestFateV06(unittest.TestCase):
    def test_fate_windows_open_only_every_third_turn(self):
        g = make_v06()
        for turn in (1, 2, 4, 5):
            g.turn = turn
            self.assertFalse(g.fate_window())
            self.assertEqual(set(action[1] for action in g.joint_plans()), {None})
        for turn in (3, 6):
            g.turn = turn
            g.pool = Counter({'GL': 2})
            self.assertTrue(g.fate_window())
            self.assertEqual(set(action[1] for action in g.joint_plans()),
                             {None, *g.fate_market})

    def test_non_window_does_not_move_fate_market(self):
        g = make_v06()
        g.turn = 1
        before = (list(g.fate_market), list(g.fate_deck), list(g.fate_discard))
        g.fate_acquired_this_turn = None
        g._settle_fate_market()
        self.assertEqual((g.fate_market, g.fate_deck, g.fate_discard), before)

    def test_fate_window_acquire_and_decline_flow(self):
        g = make_v06()
        g.turn = 3
        g.fate_market = ['F01', 'F02']
        g.pool = Counter({'GL': 1})
        plan = g._joint_plan((), 'F01')
        g.execute_joint(((), 'F01'), plan)
        self.assertEqual(g.fate_market, ['F02', 'F03'])

        h = make_v06()
        h.turn = 6
        h.fate_acquired_this_turn = None
        h.fate_market = ['F01', 'F02']
        h._settle_fate_market()
        self.assertEqual(h.fate_discard, ['F01'])
        self.assertEqual(h.fate_market, ['F02', 'F03'])

    def test_fate_window_keeps_normal_two_card_limit_and_joint_payment(self):
        g = make_v06()
        g.turn = 3
        g.market = ['YH-01', 'YK-03']
        g.market_entry = {cid: 0 for cid in g.market}
        g.fate_market = ['F01']
        g.pool = Counter({'H': 2, 'K': 2, 'GL': 1})
        plans = g.joint_plans()
        self.assertIn((('YH-01', 'YK-03'), 'F01'), plans)
        self.assertFalse(any(len(action[0]) > 2 for action in plans))

    def test_fate_bl_payment_does_not_reduce_recorded_bad_luck(self):
        g = make_v06()
        g.turn = 3
        g.fate_market = ['F07']
        g.dice = ['BL'] * 5
        g.pool = Counter({'BL': 5})
        g.record_final_bad_luck()
        plan = g._joint_plan((), 'F07')
        g.execute_joint(((), 'F07'), plan)
        g._debuff_check()
        self.assertIsNotNone(g.current_debuff)
        self.assertEqual(g.pool['BL'], 4)
        self.assertEqual(g.bad_luck_accumulator, 0)

    def test_initial_market_has_two_unique_fates(self):
        g = make_v06()
        self.assertEqual(g.fate_market, ['F01', 'F02'])
        all_ids = [c['id'] for c in FATES]
        self.assertEqual(len(all_ids), len(set(all_ids)))

    def test_joint_action_can_buy_two_normal_and_one_fate(self):
        g = make_v06()
        g.turn = 3
        g.market = ['YH-01', 'YK-03']
        g.market_entry = {cid: 0 for cid in g.market}
        g.fate_market = ['F01']
        g.pool = Counter({'H': 2, 'K': 2, 'GL': 1})
        plan = g._joint_plan(('YH-01', 'YK-03'), 'F01')
        self.assertIsNotNone(plan)
        g.execute_joint((('YH-01', 'YK-03'), 'F01'), plan)
        self.assertEqual(len(g.purchased_this_turn), 2)
        self.assertEqual(g.fate_stack, ['F01'])

    def test_at_most_one_fate_can_be_acquired_per_turn(self):
        g = make_v06()
        g.turn = 3
        g.fate_market = ['F01', 'F02']
        g.pool = Counter({'GL': 2})
        first = g._joint_plan((), 'F01')
        g.execute_joint(((), 'F01'), first)
        self.assertIsNone(g._joint_plan((), 'F02'))

    def test_fate_and_normal_purchase_cannot_double_spend(self):
        g = make_v06()
        g.turn = 3
        g.market = ['YR-03']  # R1 + GL1
        g.market_entry = {'YR-03': 0}
        g.hand = []
        g.pool = Counter({'R': 1, 'GL': 1})
        self.assertIsNone(g._joint_plan(('YR-03',), 'F01'))

    def test_fate_payment_prevented_debuff_stat_stops_growing(self):
        g = make_v06()
        g.turn = 3
        g.fate_market = ['F07']
        g.dice = ['BL'] * 5
        g.pool = Counter({'BL': 5})
        g.record_final_bad_luck()
        plan = g._joint_plan((), 'F07')
        g.execute_joint(((), 'F07'), plan)
        g._debuff_check()
        self.assertIsNotNone(g.current_debuff)
        self.assertEqual(g.pool['BL'], 4)
        self.assertEqual(g.stats.run['fate_payment_prevented_debuff'], 0)

    def test_decline_discards_left_and_acquire_shifts_then_fills(self):
        g = make_v06()
        g.turn = 3
        g.fate_acquired_this_turn = None
        g._settle_fate_market()
        self.assertEqual(g.fate_discard, ['F01'])
        self.assertEqual(g.fate_market, ['F02', 'F03'])

        h = make_v06()
        h.turn = 3
        h.pool = Counter({'GL': 1})
        plan = h._joint_plan((), 'F01')
        h.execute_joint(((), 'F01'), plan)
        self.assertEqual(h.fate_market, ['F02', 'F03'])
        self.assertNotIn('F01', h.fate_deck + h.fate_market + h.fate_discard)

    def test_empty_deck_reshuffles_only_discard(self):
        g = make_v06()
        g.fate_market = []
        g.fate_deck = []
        g.fate_discard = ['F03', 'F04']
        g.fate_stack = ['F01']
        g._fill_fate_market()
        self.assertEqual(set(g.fate_market), {'F03', 'F04'})
        self.assertNotIn('F01', g.fate_market + g.fate_deck)
        self.assertEqual(g.stats.run['fate_deck_reshuffles'], 1)

    def test_only_top_fate_is_active_and_new_fate_waits_until_next_turn(self):
        g = make_v06()
        activate_fate(g, 'F02')
        g.fate_stack.append('F11')
        g.active_fate = 'F11'
        g.fate_active_from_turn = g.turn + 1
        self.assertIsNone(g.active_fate_card())
        g.turn += 1
        self.assertEqual(g.active_fate_card()['id'], 'F11')

    def test_f01_and_f03_immediate_effects_execute_once(self):
        g = make_v06(goals=(1, 2))
        g.turn = 3
        g.cv['H'] = ['YH-01', 'YH-03']
        g.strat.active_choice = 'YH-01'
        g.fate_market = ['F01', 'F03']
        g.pool = Counter({'GL': 3})
        g._acquire_fate('F01', Counter({'GL': 1}))
        self.assertTrue(g._resolve_simulator_fate_immediate())
        self.assertEqual(g.active('H'), 'YH-01')
        self.assertEqual(g.stats.fate('F01')['ability']['set_active'], 1)

        g.turn = 6
        g.fate_acquired_this_turn = None
        g.strat.goal_choice = 'first'
        g._acquire_fate('F03', Counter({'GL': 2}))
        self.assertTrue(g._resolve_simulator_fate_immediate())
        self.assertNotEqual(g.goals[0], 1)
        self.assertEqual(g.stats.fate('F03')['ability']['replace_goal'], 1)
        self.assertEqual(g.stats.fate('F01')['ability']['set_active'], 1)

    def test_f10_auto_reroll_is_not_a_normal_reroll(self):
        g = make_v06()
        activate_fate(g, 'F10')
        g._forced = ['GL', 'H']
        self.assertEqual(g._roll(1), ['GL'])
        self.assertEqual(g.stats.fate('F10')['ability'], {})
        self.assertEqual(g.stats.run['reroll_rounds_used'], 0)

    def test_f10_dice_gl_can_pay_each_normal_resource(self):
        cases = {
            'H': ('YH-02', {'K': 1, 'GL': 1}),
            'K': ('YH-02', {'H': 1, 'GL': 1}),
            'R': ('YR-04', {'R': 1, 'M': 1, 'GL': 1}),
            'M': ('YP-02', {'M': 1, 'K': 1, 'GL': 1}),
        }
        for sym, (card, pool) in cases.items():
            with self.subTest(sym=sym):
                g = make_v06()
                g.turn = 3
                activate_fate(g, 'F10')
                g.hand = []
                g.dice_gl_flexible = 1
                g.pool = Counter(pool)
                plan = g.try_acquire((card,))
                self.assertIsNotNone(plan)
                self.assertEqual(plan['wildcard_symbols'], [sym])

    def test_f10_one_dice_gl_cannot_be_reused(self):
        g = make_v06()
        g.turn = 3
        activate_fate(g, 'F10')
        g.hand = []
        g.dice_gl_flexible = 1
        g.pool = Counter({'K': 0})
        self.assertIsNone(g.try_acquire(('YH-02',)))

    def test_f10_dice_gl_can_remain_true_gl_for_fate_and_free_take(self):
        g = make_v06()
        g.turn = 3
        activate_fate(g, 'F10')
        g.hand = []
        g.fate_market = ['F01']
        g.dice_gl_flexible = 1
        g.pool = Counter({'GL': 1})
        fate_plan = g._joint_plan((), 'F01')
        self.assertIsNotNone(fate_plan)
        self.assertEqual(fate_plan['f10_dice_gl_conversions'], [])
        self.assertEqual(fate_plan['f10_dice_gl_true_gl_used'], 1)
        self.assertEqual(fate_plan['fate_payment']['spent_resources'], {'GL': 1})

        g.fate_market = []
        g.market = ['YH-01']
        g.market_entry = {'YH-01': 0}
        g.dice_gl_flexible = 3
        g.pool = Counter({'GL': 3})
        plans = [p for p in g.enumerate_joint_purchase_plans()
                 if p['ordinary_card_ids'] == ['YH-01']]
        self.assertTrue(plans)
        self.assertTrue(any(p['gl_free_acquisitions'] == ['YH-01']
                            and not p['f10_dice_gl_conversions']
                            and p['f10_dice_gl_true_gl_used'] == 3
                            for p in plans))

        activate_debuff(g, 'D07')
        g.dice_gl_flexible = 4
        g.pool = Counter({'GL': 4})
        d07_plans = [p for p in g.enumerate_joint_purchase_plans()
                     if p['ordinary_card_ids'] == ['YH-01']]
        self.assertTrue(any(p['gl_free_acquisitions'] == ['YH-01']
                            and not p['f10_dice_gl_conversions']
                            for p in d07_plans))

    def test_f10_conversion_cannot_double_spend_dice_gl_with_three_gl_take(self):
        g = make_v06()
        g.turn = 3
        activate_fate(g, 'F10')
        g.market = ['YH-02', 'YH-01']
        g.market_entry = {'YH-02': 0, 'YH-01': 0}
        g.hand = []
        g.fate_market = []
        g.dice_gl_flexible = 3
        g.pool = Counter({'K': 1, 'GL': 3})
        self.assertFalse(any(
            p['ordinary_card_ids'] == ['YH-02', 'YH-01']
            and p['gl_free_acquisitions']
            and p['f10_dice_gl_conversions']
            for p in g.enumerate_joint_purchase_plans()))

        g.dice_gl_flexible = 4
        g.pool = Counter({'K': 1, 'GL': 4})
        combined = [p for p in g.enumerate_joint_purchase_plans()
                    if p['ordinary_card_ids'] == ['YH-02', 'YH-01']]
        self.assertTrue(combined)
        self.assertTrue(any(
            p['gl_free_acquisitions'] == ['YH-01']
            and p['f10_dice_gl_conversions'] == [{'resource': 'H'}]
            for p in combined))

    def test_f10_does_not_convert_stable_or_temporary_gl(self):
        g = make_v06()
        g.turn = 3
        activate_fate(g, 'F10')
        g.hand = ['C05']
        g.dice_gl_flexible = 1
        g.pool = Counter({'GL': 2})
        stable = g.try_acquire(('YR-03',))
        self.assertIsNotNone(stable)
        self.assertEqual(stable['pool_used']['GL'], 2)
        self.assertEqual(stable['wildcard_symbols'], ['R'])

        h = make_v06()
        h.turn = 3
        activate_fate(h, 'F10')
        h.hand = ['C05']
        h.dice_gl_flexible = 1
        h.pool = Counter({'GL': 1})
        temporary = h.try_acquire(('YR-03',))
        self.assertIsNotNone(temporary)
        self.assertEqual(temporary['temps_used'], ['C05'])
        self.assertEqual(temporary['wildcard_symbols'], ['R'])

    def test_f10_is_delayed_and_loses_effect_when_covered(self):
        g = make_v06()
        g.turn = 3
        g.fate_market = ['F10']
        g.pool = Counter({'BL': 2})
        plan = g._joint_plan((), 'F10')
        g.execute_joint(((), 'F10'), plan)
        self.assertEqual(g.fate_active_from_turn, 4)
        self.assertIsNone(g.active_fate_card())
        g.turn = 4
        self.assertEqual(g.active_fate_card()['id'], 'F10')
        g.active_fate = 'F02'
        self.assertEqual(g.active_fate_card()['id'], 'F02')

    def test_f10_joint_purchase_and_fate_payment_do_not_double_spend(self):
        g = make_v06()
        g.turn = 3
        activate_fate(g, 'F10')
        g.market = ['YH-01']
        g.market_entry = {'YH-01': 0}
        g.fate_market = ['F01']
        g.hand = []
        g.dice_gl_flexible = 1
        g.pool = Counter({'H': 1, 'GL': 1})
        self.assertIsNone(g._joint_plan(('YH-01',), 'F01'))

        # 再增加 1 个非骰子 GL 后，骰子 GL 才能只负责普通成本，
        # 另一个真实 GL 独立支付 Fate。
        g.dice = ['GL']
        g.pool = Counter({'H': 1, 'GL': 2})
        plan = g._joint_plan(('YH-01',), 'F01')
        self.assertIsNotNone(plan)
        self.assertEqual(plan['f10_dice_gl_conversions'], [{'resource': 'H'}])
        self.assertEqual(plan['fate_payment']['spent_resources'], {'GL': 1})
        g.execute_joint((('YH-01',), 'F01'), plan)
        self.assertEqual(g.dice_gl_flexible, 0)
        self.assertEqual(g.pool['GL'], 0)

    def test_joint_generator_keeps_state_distinct_payments_for_same_target(self):
        g = make_v06()
        g.turn = 3
        g.fate_market = ['F01']
        g.hand = ['C05']
        g.pool = Counter({'GL': 1})
        plans = [p for p in g.enumerate_joint_purchase_plans()
                 if p['ordinary_card_ids'] == [] and p['fate_card_id'] == 'F01']
        self.assertEqual(len(plans), 2)
        self.assertEqual({tuple(p['consumed_childhood_card_ids']) for p in plans},
                         {(), ('C05',)})

    def test_c05_can_pay_fate_and_competes_with_three_gl_take(self):
        g = make_v06()
        g.turn = 3
        g.market = ['YH-01']
        g.market_entry = {'YH-01': 0}
        g.fate_market = ['F01']
        g.hand = ['C05']
        g.pool = Counter({'GL': 2})
        fate_only = [p for p in g.enumerate_joint_purchase_plans()
                     if p['ordinary_card_ids'] == [] and p['fate_card_id'] == 'F01']
        self.assertTrue(any('C05' in p['consumed_childhood_card_ids']
                            for p in fate_only))
        self.assertFalse(any(p['ordinary_card_ids'] == ['YH-01']
                             and p['fate_card_id'] == 'F01'
                             for p in g.enumerate_joint_purchase_plans()))

        g.pool = Counter({'GL': 3})
        combined = [p for p in g.enumerate_joint_purchase_plans()
                    if p['ordinary_card_ids'] == ['YH-01']
                    and p['fate_card_id'] == 'F01']
        self.assertEqual(len(combined), 2)
        self.assertTrue(all(p['gl_free_acquisitions'] == ['YH-01']
                            for p in combined))
        self.assertEqual({p['fate_payment']['spent_resources'].get('GL', 0)
                          for p in combined}, {0, 1})

    def test_joint_fate_bl_payment_reduces_remaining_resources(self):
        g = make_v06()
        g.turn = 3
        g.fate_market = ['F07']
        g.pool = Counter({'BL': 3})
        plan = g._joint_plan((), 'F07')
        self.assertEqual(plan['fate_payment']['spent_resources'], {'BL': 1})
        self.assertEqual(plan['remaining_resources'], {'BL': 2})

    def test_non_dice_gl_cannot_be_f10_wildcard(self):
        g = make_v06()
        g.turn = 3
        activate_fate(g, 'F10')
        g.market = ['YH-01']
        g.market_entry = {'YH-01': 0}
        g.dice = []
        g.dice_gl_flexible = 0
        g.pool = Counter({'H': 1, 'GL': 1})
        self.assertFalse(any(p['ordinary_card_ids'] == ['YH-01']
                             for p in g.enumerate_joint_purchase_plans()))

    def test_event_temporary_resources_only_pay_ordinary_costs(self):
        g = make_v06()
        g.turn = 3
        g.market = ['YP-01']
        g.market_entry = {'YP-01': 0}
        g.fate_market = ['F01']
        g.hand = ['YE-01']
        g.pool = Counter({'GL': 1})
        combined = [p for p in g.enumerate_joint_purchase_plans()
                    if p['ordinary_card_ids'] == ['YP-01']
                    and p['fate_card_id'] == 'F01']
        self.assertTrue(combined)
        self.assertTrue(all(p['consumed_event_card_ids'] == ['YE-01']
                            for p in combined))

        g.market = []
        g.pool = Counter()
        self.assertFalse(any(p['fate_card_id'] == 'F01'
                             for p in g.enumerate_joint_purchase_plans()))

    def test_simulator_chooser_only_returns_shared_generator_plans(self):
        g = make_v06()
        g.turn = 3
        g.fate_market = ['F01']
        g.hand = ['C05']
        g.pool = Counter({'GL': 1})
        generated = g.enumerate_joint_purchase_plans()
        generated_ids = {p['plan_id'] for p in generated}
        chosen = g.joint_plans()
        self.assertTrue(chosen)
        self.assertTrue(all(plan['plan_id'] in generated_ids
                            for plan in chosen.values()))

    def test_joint_generator_is_pure_and_stable(self):
        g = make_v06()
        g.turn = 3
        g.fate_market = ['F01']
        g.hand = ['C05', 'YE-01']
        g.pool = Counter({'GL': 3, 'M': 1})
        before = deepcopy((g.market, g.fate_market, g.hand, g.pool,
                           g.fate_stack, g.rng.getstate()))
        first = g.enumerate_joint_purchase_plans()
        second = g.enumerate_joint_purchase_plans()
        after = deepcopy((g.market, g.fate_market, g.hand, g.pool,
                          g.fate_stack, g.rng.getstate()))
        self.assertEqual(first, second)
        self.assertEqual(before, after)

    def test_execute_joint_consumes_precomputed_plan_without_resolving(self):
        g = make_v06()
        g.turn = 3
        g.fate_market = ['F01']
        g.hand = ['C05']
        g.pool = Counter()
        plan = g._joint_plan((), 'F01')
        with mock.patch('ailife.engine.solve_cost_all',
                        side_effect=AssertionError('solver called during execution')):
            self.assertTrue(g.execute_joint(((), 'F01'), plan))
        self.assertNotIn('C05', g.hand)
        self.assertEqual(g.active_fate, 'F01')

    def test_execute_joint_pauses_f01_without_calling_strategy(self):
        g = make_v06()
        g.turn = 3
        g.cv['H'] = ['YH-01', 'YH-03']
        g.fate_market = ['F01', 'F02']
        g.pool = Counter({'GL': 1})
        plan = g._joint_plan((), 'F01')
        with mock.patch.object(
                g.strat, 'choose_active_reset',
                side_effect=AssertionError('strategy called inside executor')):
            self.assertTrue(g.execute_joint(((), 'F01'), plan))
        self.assertEqual(g.active('H'), 'YH-03')
        first = g.pending_fate_immediate_decision()
        second = g.pending_fate_immediate_decision()
        self.assertEqual(first, second)
        self.assertEqual(first['kind'], 'fate_set_active')
        before = deepcopy((g.cv, g.fate_market, g.stats.game))
        self.assertFalse(g.resolve_fate_immediate(
            first['decision_id'], {'card_id': 'YK-01'}))
        self.assertEqual(before, (g.cv, g.fate_market, g.stats.game))
        self.assertTrue(g.resolve_fate_immediate(
            first['decision_id'], {'card_id': 'YH-01'}))
        self.assertEqual(g.active('H'), 'YH-01')
        self.assertIsNone(g.pending_fate_immediate_decision())
        self.assertFalse(g.resolve_fate_immediate(
            first['decision_id'], {'card_id': 'YH-03'}))

    def test_f03_candidates_are_sampled_once_and_saved_until_resolved(self):
        g = make_v06(goals=(1, 2))
        g.turn = 3
        g.fate_market = ['F03', 'F04']
        g.pool = Counter({'GL': 2})
        plan = g._joint_plan((), 'F03')
        self.assertTrue(g.execute_joint(((), 'F03'), plan))
        state_after_draw = g.rng.getstate()
        first = g.pending_fate_immediate_decision()
        second = g.pending_fate_immediate_decision()
        self.assertEqual(first, second)
        self.assertEqual(g.rng.getstate(), state_after_draw)
        self.assertEqual(len(first['candidate_goal_ids']), 3)
        self.assertFalse(g.resolve_fate_immediate(
            'stale', {'choice': 'keep'}))
        self.assertEqual(g.rng.getstate(), state_after_draw)
        action = {'choice': 'replace', 'goal_index': 0,
                  'new_goal_id': first['candidate_goal_ids'][0]}
        self.assertTrue(g.resolve_fate_immediate(first['decision_id'], action))
        self.assertEqual(g.goals[0], action['new_goal_id'])
        self.assertEqual(g.rng.getstate(), state_after_draw)

    def test_execute_joint_accepts_official_locked_resources_without_pool_patch(self):
        g = make_v06()
        g.turn = 3
        g.fate_market = ['F01', 'F02']
        g.cv['H'] = ['YH-01']
        g.dice = ['GL']
        g.pool = Counter()
        locked = g.post_roll_resources()
        plan = next(p for p in g.enumerate_joint_purchase_plans(locked)
                    if p['ordinary_card_ids'] == []
                    and p['fate_card_id'] == 'F01'
                    and p['fate_payment']['spent_resources'] == {'GL': 1})
        g.strat = None
        self.assertTrue(g.execute_joint(
            ((), 'F01'), plan, locked_resources=locked))
        self.assertEqual(g.pool, Counter())
        self.assertEqual(g.pending_fate_immediate_decision()['kind'],
                         'fate_set_active')

    def test_invalid_precomputed_joint_plan_is_rejected_atomically(self):
        g = make_v06()
        g.turn = 3
        g.fate_market = ['F01']
        g.hand = ['C05']
        g.pool = Counter()
        plan = deepcopy(g._joint_plan((), 'F01'))
        plan['remaining_resources'] = {'GL': 99}
        before = deepcopy((g.pool, g.hand, g.fate_market, g.fate_stack,
                           g.stats.game, g.rng.getstate()))
        self.assertFalse(g.execute_joint(((), 'F01'), plan))
        after = deepcopy((g.pool, g.hand, g.fate_market, g.fate_stack,
                          g.stats.game, g.rng.getstate()))
        self.assertEqual(before, after)

    def test_f11_virtual_bl_cannot_pay_and_f12_locks_normal_rerolls(self):
        g = make_v06()
        activate_fate(g, 'F11')
        g.pool = Counter()
        self.assertIsNone(g._joint_plan((), 'F07'))

        h = make_v06()
        activate_fate(h, 'F12')
        h.dice = ['H', 'BL', 'K', 'R']
        h._after_dice_change()
        self.assertTrue(h.normal_rerolls_locked())
        h.dice = ['H', 'M', 'K', 'R']
        self.assertTrue(h.normal_rerolls_locked())


class TestV06CardEffects(unittest.TestCase):
    def test_all_symmetric_debuff_cost_and_block_effects(self):
        cost_cases = {
            'D02': ('YP-01', 'M', 2),
            'D03': ('YK-03', 'K', 2),
            'D04': ('YR-01', 'R', 2),
        }
        for debuff, (card, sym, base) in cost_cases.items():
            with self.subTest(debuff=debuff):
                g = make_v06()
                g.market = [card]
                g.market_entry = {card: 0}
                g.hand = []
                activate_debuff(g, debuff)
                g.pool = Counter({sym: base})
                self.assertIsNone(g.try_acquire((card,)))
                g.pool[sym] += 1
                self.assertIsNotNone(g.try_acquire((card,)))

        block_cases = {'D10': 'YK-03', 'D11': 'YP-01', 'D12': 'YH-01'}
        for debuff, card in block_cases.items():
            with self.subTest(debuff=debuff):
                g = make_v06()
                g.market = [card]
                g.market_entry = {card: 0}
                activate_debuff(g, debuff)
                g.pool = Counter({'H': 9, 'K': 9, 'R': 9, 'M': 9})
                self.assertIsNone(g.try_acquire((card,)))

    def test_debuff_cost_block_event_reroll_and_gl_threshold_effects(self):
        g = make_v06()
        activate_debuff(g, 'D01')
        g.pool = Counter({'H': 2})
        self.assertIsNone(g.try_acquire(('YH-01',)))
        g.pool['H'] = 3
        self.assertIsNotNone(g.try_acquire(('YH-01',)))

        activate_debuff(g, 'D05')
        g.hand = ['YE-01', 'C01']
        self.assertEqual([x[2] for x in g._temps_available()], ['C01'])

        activate_debuff(g, 'D06')
        self.assertEqual(g.normal_reroll_rounds(), 1)

        activate_debuff(g, 'D07')
        g.hand = []
        g.pool = Counter({'GL': 3})
        self.assertIsNone(g.try_acquire(('YP-01',)))
        g.pool['GL'] = 4
        self.assertIsNotNone(g.try_acquire(('YP-01',)))

        activate_debuff(g, 'D09')
        g.pool = Counter({'R': 9, 'H': 9, 'M': 9})
        self.assertIsNone(g.try_acquire(('YR-01',)))

    def test_f04_f06_f07_f08_purchase_modifiers(self):
        g = make_v06()
        activate_fate(g, 'F04')
        g.pool = Counter({'K': 1})
        self.assertIsNotNone(g.try_acquire(('YW-01',)))

        activate_fate(g, 'F06')
        g.pool = Counter({'M': 1})
        self.assertIsNotNone(g.try_acquire(('YP-01',)))

        activate_fate(g, 'F07')
        g.pool = Counter()
        self.assertIsNotNone(g.try_acquire(('YH-01',)))
        self.assertFalse(any(len(p) == 2 for p in g.affordable_plans()))

        activate_fate(g, 'F08')
        g.pool = Counter({'H': 2})
        self.assertIsNone(g.try_acquire(('YH-01',)))
        g.pool = Counter({'H': 3})
        self.assertIsNotNone(g.try_acquire(('YH-01',)))
        g.pool = Counter({'K': 1, 'M': 1})
        self.assertIsNotNone(g.try_acquire(('YK-01',)))

    def test_f05_upkeep_and_f09_f11_f12_turn_modifiers(self):
        g = make_v06()
        activate_fate(g, 'F05')
        g.turn = 4
        g.cv['W'] = ['YW-02']
        g.acquired['YW-02'] = 1
        g.pool = Counter()
        g._upkeep()
        self.assertEqual(g.cv['W'], ['YW-02'])

        activate_fate(g, 'F09')
        g.hand = ['YE-01']
        g.temp_dice = 0
        self.assertTrue(g._event_blocked())
        self.assertEqual(g.current_dice_count(), 5)

        activate_fate(g, 'F11')
        self.assertEqual(g.normal_reroll_rounds(), 4)

        activate_fate(g, 'F12')
        g.temp_dice = 0
        self.assertEqual(g.current_dice_count(), 6)

    def test_goal_priority_values_lg17_and_lg18_fate_progress(self):
        g = make_v06(strategy=STRATEGIES['goal_priority'], goals=(17, 1))
        g.fate_stack = ['F02']
        self.assertGreater(g.strat.fate_value('F04'), 0)

        h = make_v06(strategy=STRATEGIES['goal_priority'], goals=(18, 1))
        h.current_debuff = 'D01'
        h.stats.game['event_buys']['YE-01'] = 1
        self.assertGreater(h.strat.fate_value('F04'), 0)

        e = make_v06(strategy=STRATEGIES['goal_priority'], goals=(18, 1))
        e.current_debuff = 'D01'
        e.fate_stack = ['F02']
        self.assertGreater(e.strat.card_value('YE-01'), 4)


class TestGoalsAndRegressionV06(unittest.TestCase):
    def test_lg17_and_lg18_formulas(self):
        counts = {c: 0 for c in 'HKRWP'}
        self.assertEqual(scoring.lg_score(17, counts, 0, [], fate_count=5), 6)
        self.assertEqual(scoring.lg_score(
            18, counts, 0, [], fate_count=4, debuff_count=2, event_count=3), 8)

    def test_used_event_still_counts_and_cancelled_debuff_does_not(self):
        g = make_v06(goals=(18, 1))
        g.stats.game['event_buys']['YE-01'] = 1
        g.stats.game['event_uses']['YE-01'] = 1
        self.assertEqual(g.event_acquired_count(), 1)
        g.hand = ['C06']
        g.strat.debuff_choice = {'cancel': 'C06'}
        g.bad_luck_accumulator = 5
        g._debuff_check()
        self.assertEqual(g.debuff_experienced_count(), 0)
        self.assertEqual(scoring.full_score(g.cv, g.goals, **g.scoring_counts())['lg_scores'][0], 0)

    def test_f03_replacement_scores_only_final_goals(self):
        g = make_v06(goals=(1, 2))
        g.cv['H'] = ['YH-01']
        g.goals[0] = 17
        score = scoring.full_score(g.cv, g.goals, fate_count=2)
        self.assertEqual(score['lg_ids'], [17, 2])
        self.assertEqual(score['lg_scores'][0], 3)

    def test_v06_fixed_config_and_full_games_finish(self):
        cfg = CONFIGS['V06']
        self.assertFalse(cfg.upkeep_immediate)
        self.assertFalse(cfg.fallback)
        self.assertTrue(cfg.debuff)
        self.assertTrue(cfg.fate)
        self.assertEqual(cfg.life_goals, 18)
        for strategy in STRATEGIES.values():
            g = Game(cfg, strategy, random.Random(19), forced_goals=[17, 18])
            row = g.run()
            self.assertEqual(row['turns'], 23)
            self.assertTrue(g.game_over)
            self.assertTrue(all(n == 3 for n in g.stats.game['turn_departures']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
