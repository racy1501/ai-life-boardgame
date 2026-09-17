# -*- coding: utf-8 -*-
import os
import json
import random
import sys
import unittest
import copy
from collections import Counter
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ailife.cards import CARDS, LG_NAMES
from ailife.engine import CONFIGS, Game
from ailife.runtime import (GameSession, _CARD_CATALOG_RULE_FIELDS,
                            _SPECIAL_NOTES, _card_effect_summary,
                            card_catalog)
from ailife.scoring import (LG_RULES, flex_actives, full_score, lg_score,
                            lg_scoring_text)
from ailife.strategies import Balanced


class TestChildhoodRuntime(unittest.TestCase):
    def make_session(self):
        return GameSession(seed=1, shuffle=False, forced_goals=[1, 2])

    def test_starts_at_first_pick_with_three_candidates(self):
        session = self.make_session()
        decision = session.current_decision()
        self.assertEqual(decision['kind'], 'childhood_pick_1')
        self.assertEqual([c['id'] for c in decision['candidates']],
                         ['C08', 'C12', 'C01'])
        self.assertEqual(len(decision['legal_actions']), 3)
        self.assertEqual(session.current_decision()['decision_id'],
                         decision['decision_id'])

    def finish_draft_without_c11(self, forced_dice=None):
        session = self.make_session()
        first = session.current_decision()
        result = session.submit_action(first['decision_id'], {'card_id': 'C12'})
        second = result['decision']
        if forced_dice is not None:
            session.game._forced = list(forced_dice)
        return session, session.submit_action(second['decision_id'],
                                              {'card_id': 'C09'})

    def test_draft_auto_starts_first_adult_turn_without_c11(self):
        session, result = self.finish_draft_without_c11(['H', 'K', 'R', 'M'])
        self.assertTrue(result['ok'])
        self.assertEqual(result['decision']['kind'], 'post_roll_decision')
        self.assertEqual(result['decision']['dice'], ['H', 'K', 'R', 'M'])
        self.assertEqual(result['decision']['dice_count'], 4)
        self.assertEqual(result['decision']['remaining_normal_rerolls'], 2)
        self.assertEqual(session.game.childhood_kept, ['C12', 'C09', 'C07'])
        self.assertEqual(session.game.hand, ['C09', 'C07'])
        self.assertTrue(session.game.abebe_held)
        self.assertEqual(len(session.game.market), 5)
        self.assertEqual(session.game.turn, 1)

    def test_post_roll_decision_has_unified_preview_without_side_effects(self):
        session, result = self.finish_draft_without_c11(['H', 'K', 'R', 'M'])
        decision = result['decision']
        self.assertTrue({'dice', 'stable_resources', 'total_available_resources',
                         'current_opportunities', 'legal_acquisition_plans',
                         'remaining_normal_rerolls', 'rerollable_indices',
                         'frozen_indices'} <= set(decision))
        opportunity_ids = [card['id'] for card in decision['current_opportunities']]
        self.assertEqual(len(opportunity_ids), 5)
        self.assertEqual(len(opportunity_ids), len(set(opportunity_ids)))
        self.assertEqual(decision['total_available_resources'],
                         {'H': 1, 'K': 1, 'M': 1, 'R': 1})
        self.assertTrue(all(set(plan) == {'card_ids'}
                            for plan in decision['legal_acquisition_plans']))
        self.assertTrue(all('name' not in plan and 'cost' not in plan
                            for plan in decision['legal_acquisition_plans']))
        before = (list(session.game.market), dict(session.game.pool),
                  dict(session.game.acquired), list(session.game.hand))
        session.current_decision()
        after = (list(session.game.market), dict(session.game.pool),
                 dict(session.game.acquired), list(session.game.hand))
        self.assertEqual(after, before)

    def finish_draft_with_c11(self):
        session = GameSession(seed=3, shuffle=False, forced_goals=[1, 2])
        first = session.current_decision()
        self.assertEqual([c['id'] for c in first['candidates']],
                         ['C02', 'C08', 'C11'])
        second = session.submit_action(first['decision_id'],
                                       {'card_id': 'C11'})['decision']
        self.assertEqual(second['kind'], 'childhood_pick_2')
        self.assertEqual([c['id'] for c in second['candidates']], ['C01', 'C07'])
        return session, second

    def test_c11_stops_at_pre_roll_window(self):
        session, second = self.finish_draft_with_c11()
        result = session.submit_action(second['decision_id'], {'card_id': 'C01'})
        self.assertTrue(result['ok'])
        self.assertEqual(result['decision']['kind'], 'pre_roll_c11')
        self.assertIn('C11', session.game.hand)
        self.assertEqual(result['decision']['legal_actions'],
                         [{'choice': 'use'}, {'choice': 'skip'}])

    def test_pre_roll_c11_carries_opening_long_term_view(self):
        session, second = self.finish_draft_with_c11()
        result = session.submit_action(second['decision_id'],
                                       {'card_id': 'C01'})
        decision = result['decision']
        self.assertEqual(decision['kind'], 'pre_roll_c11')
        self.assertEqual([g['id'] for g in decision['life_goals']], [1, 2])
        self.assertEqual([c['card_id'] for c in decision['childhood_cards']],
                         ['C11', 'C01', 'C12'])
        self.assertEqual([c['status'] for c in decision['childhood_cards']],
                         ['available', 'available', 'held'])
        # 成年阶段不再携带 draft 溯源；仍可用牌的效果由 CARDS 纯派生可读
        self.assertNotIn('childhood_draft_result', decision)
        for c in decision['childhood_cards']:
            self.assertEqual(c['effect_summary'],
                             _SPECIAL_NOTES.get(c['card_id'])
                             or _card_effect_summary(CARDS[c['card_id']]))

    def test_using_c11_adds_two_dice_and_consumes_it(self):
        session, second = self.finish_draft_with_c11()
        session.game._forced = ['H'] * 6
        pre_roll = session.submit_action(second['decision_id'],
                                         {'card_id': 'C01'})['decision']
        result = session.submit_action(pre_roll['decision_id'], {'choice': 'use'})
        self.assertTrue(result['ok'])
        self.assertEqual(result['decision']['kind'], 'post_roll_decision')
        self.assertNotIn('C11', session.game.hand)
        self.assertEqual(result['decision']['dice_count'], 6)
        self.assertLessEqual(result['decision']['dice_count'], 7)
        self.assertEqual(result['decision']['dice'], ['H'] * 6)

    def test_skipping_c11_keeps_it_for_later_reroll_windows(self):
        session, second = self.finish_draft_with_c11()
        session.game._forced = ['K'] * 4
        pre_roll = session.submit_action(second['decision_id'],
                                         {'card_id': 'C01'})['decision']
        result = session.submit_action(pre_roll['decision_id'], {'choice': 'skip'})
        self.assertTrue(result['ok'])
        self.assertEqual(result['decision']['kind'], 'post_roll_decision')
        self.assertIn('C11', session.game.hand)
        self.assertEqual(result['decision']['dice_count'], 4)

    def test_illegal_c11_action_does_not_roll_or_consume(self):
        session, second = self.finish_draft_with_c11()
        pre_roll = session.submit_action(second['decision_id'],
                                         {'card_id': 'C01'})['decision']
        before = (list(session.game.hand), list(session.game.dice),
                  session.game.temp_dice, session.game.pre_roll_open)
        result = session.submit_action(pre_roll['decision_id'], {'choice': 'later'})
        after = (list(session.game.hand), list(session.game.dice),
                 session.game.temp_dice, session.game.pre_roll_open)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'illegal_action')
        self.assertEqual(after, before)

    def test_reroll_view_marks_bad_luck_as_frozen(self):
        _, result = self.finish_draft_without_c11(['BL', 'H', 'GL', 'BL'])
        decision = result['decision']
        self.assertEqual(decision['rerollable_indices'], [1, 2])
        self.assertEqual(decision['frozen_indices'], [0, 3])
        self.assertEqual(decision['freeze_reason'], 'bad_luck')

    def test_illegal_action_does_not_change_state(self):
        session = self.make_session()
        decision = session.current_decision()
        before = (list(session.game.childhood_pool),
                  list(session.game.childhood_kept),
                  list(session.game.hand),
                  session.game.childhood_draft_round)
        result = session.submit_action(decision['decision_id'],
                                       {'card_id': 'C02'})
        after = (list(session.game.childhood_pool),
                 list(session.game.childhood_kept),
                 list(session.game.hand),
                 session.game.childhood_draft_round)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'illegal_action')
        self.assertEqual(after, before)

    def test_old_decision_id_cannot_be_submitted_twice(self):
        session = self.make_session()
        first = session.current_decision()
        self.assertTrue(session.submit_action(
            first['decision_id'], {'card_id': 'C08'})['ok'])
        before = (list(session.game.childhood_pool),
                  list(session.game.childhood_kept),
                  session.game.childhood_draft_round)
        repeated = session.submit_action(first['decision_id'], {'card_id': 'C08'})
        after = (list(session.game.childhood_pool),
                 list(session.game.childhood_kept),
                 session.game.childhood_draft_round)
        self.assertFalse(repeated['ok'])
        self.assertEqual(repeated['error'], 'stale_or_unknown_decision_id')
        self.assertEqual(after, before)

    def test_first_reroll_recalculates_post_roll_decision(self):
        session, result = self.finish_draft_without_c11(['H', 'K', 'R', 'M'])
        session.game._forced = ['GL']
        rerolled = session.submit_action(
            result['decision']['decision_id'],
            {'choice': 'reroll', 'indices': [0]},
        )
        self.assertTrue(rerolled['ok'])
        self.assertEqual(rerolled['decision']['kind'], 'post_roll_decision')
        self.assertEqual(rerolled['decision']['dice'], ['GL', 'K', 'R', 'M'])
        self.assertEqual(rerolled['decision']['remaining_normal_rerolls'], 1)
        self.assertEqual(rerolled['decision']['total_available_resources'],
                         {'GL': 1, 'K': 1, 'M': 1, 'R': 1})

    def test_second_reroll_automatically_enters_purchase_ready(self):
        session, result = self.finish_draft_without_c11(['H', 'K', 'R', 'M'])
        session.game._forced = ['GL']
        first = session.submit_action(result['decision']['decision_id'],
                                      {'choice': 'reroll', 'indices': [0]})
        session.game._forced = ['H']
        second = session.submit_action(first['decision']['decision_id'],
                                       {'choice': 'reroll', 'indices': [1]})
        self.assertTrue(second['ok'])
        self.assertEqual(second['decision']['kind'], 'purchase_ready')
        self.assertEqual(second['decision']['remaining_normal_rerolls'], 0)
        targets = second['decision']['purchase_targets']
        # Payload Slim v1：L1 不再重复下发 legal_actions，
        # purchase_targets 条目自身即提交字段。
        self.assertNotIn('legal_actions', second['decision'])
        self.assertTrue(targets)

    def test_proceed_to_purchase_locks_dice_after_first_or_second_window(self):
        session, result = self.finish_draft_without_c11(['H', 'K', 'R', 'M'])
        first_ready = session.submit_action(
            result['decision']['decision_id'], {'choice': 'proceed_to_purchase'})
        self.assertTrue(first_ready['ok'])
        self.assertEqual(first_ready['decision']['kind'], 'purchase_ready')

        session, result = self.finish_draft_without_c11(['H', 'K', 'R', 'M'])
        session.game._forced = ['GL']
        after_reroll = session.submit_action(result['decision']['decision_id'],
                                             {'choice': 'reroll', 'indices': [0]})
        second_ready = session.submit_action(
            after_reroll['decision']['decision_id'], {'choice': 'proceed_to_purchase'})
        self.assertTrue(second_ready['ok'])
        self.assertEqual(second_ready['decision']['kind'], 'purchase_ready')

    def test_c11_can_be_used_before_either_normal_reroll(self):
        session, second = self.finish_draft_with_c11()
        session.game._forced = ['K'] * 4
        pre = session.submit_action(second['decision_id'], {'card_id': 'C01'})
        post = session.submit_action(pre['decision']['decision_id'], {'choice': 'skip'})
        session.game._forced = ['R', 'M', 'GL']
        first = session.submit_action(
            post['decision']['decision_id'],
            {'choice': 'reroll', 'indices': [0], 'use_c11': True},
        )
        self.assertTrue(first['ok'])
        self.assertNotIn('C11', session.game.hand)
        self.assertEqual(first['decision']['dice_count'], 6)

        session, second = self.finish_draft_with_c11()
        session.game._forced = ['K'] * 4
        pre = session.submit_action(second['decision_id'], {'card_id': 'C01'})
        post = session.submit_action(pre['decision']['decision_id'], {'choice': 'skip'})
        session.game._forced = ['H']
        first = session.submit_action(post['decision']['decision_id'],
                                      {'choice': 'reroll', 'indices': [0]})
        session.game._forced = ['R', 'M', 'GL']
        second = session.submit_action(
            first['decision']['decision_id'],
            {'choice': 'reroll', 'indices': [1], 'use_c11': True},
        )
        self.assertTrue(second['ok'])
        self.assertNotIn('C11', session.game.hand)
        self.assertEqual(second['decision']['dice_count'], 6)

    def test_c12_only_unfreezes_one_selected_bad_luck(self):
        session, result = self.finish_draft_without_c11(['BL', 'BL', 'H', 'K'])
        session.game._forced = ['R', 'M']
        rerolled = session.submit_action(
            result['decision']['decision_id'],
            {'choice': 'reroll', 'indices': [0, 2], 'c12_index': 0},
        )
        self.assertTrue(rerolled['ok'])
        self.assertTrue(session.game.abebe_used)
        self.assertEqual(session.game.dice[1], 'BL')

    def test_plain_bad_luck_and_old_post_roll_id_are_rejected_without_change(self):
        session, result = self.finish_draft_without_c11(['BL', 'H', 'R', 'M'])
        decision = result['decision']
        before = (list(session.game.dice), session._rerolls_remaining,
                  session.game.abebe_used)
        invalid_index = session.submit_action(
            decision['decision_id'], {'choice': 'reroll', 'indices': [99]})
        self.assertFalse(invalid_index['ok'])
        self.assertEqual(invalid_index['error'], 'illegal_action')
        illegal = session.submit_action(decision['decision_id'],
                                        {'choice': 'reroll', 'indices': [0]})
        after = (list(session.game.dice), session._rerolls_remaining,
                 session.game.abebe_used)
        self.assertFalse(illegal['ok'])
        self.assertEqual(illegal['error'], 'illegal_action')
        self.assertEqual(after, before)
        ready = session.submit_action(decision['decision_id'],
                                      {'choice': 'proceed_to_purchase'})
        stale = session.submit_action(decision['decision_id'],
                                      {'choice': 'proceed_to_purchase'})
        self.assertTrue(ready['ok'])
        self.assertFalse(stale['ok'])
        self.assertEqual(stale['error'], 'stale_or_unknown_decision_id')

    def test_existing_simulator_construction_still_completes_draft(self):
        game = Game(CONFIGS['V06'], Balanced, random.Random(7), shuffle=False,
                    forced_goals=[1, 2])
        self.assertTrue(game.childhood_complete)
        self.assertEqual(len(game.childhood_kept), 3)
        self.assertEqual(len(game.hand) + int(game.abebe_held), 3)
        self.assertEqual(len(game.market), 5)


class TestRuntimePurchasePlanGeneration(unittest.TestCase):
    def make_game(self, market, resources, hand=()):
        game = Game(CONFIGS['V06'], None, random.Random(19), shuffle=False,
                    forced_goals=[1, 2], defer_childhood=True)
        game.market = list(market)
        game.hand = list(hand)
        return game, Counter(resources)

    def plans_for(self, market, resources, hand=()):
        game, pool = self.make_game(market, resources, hand)
        return game, pool, game.enumerate_legal_purchase_plans(pool)

    def test_enumerates_zero_one_and_two_card_plans(self):
        _, _, plans = self.plans_for(
            ['YH-01', 'YK-01'], {'H': 2, 'K': 1, 'M': 1})
        card_sets = {tuple(plan['card_ids']) for plan in plans}
        self.assertEqual(card_sets, {
            (), ('YH-01',), ('YK-01',), ('YH-01', 'YK-01')})

    def test_keeps_different_payment_outcomes_for_same_cards(self):
        _, _, plans = self.plans_for(['YH-01'], {'H': 2, 'GL': 3})
        bought = [p for p in plans if p['card_ids'] == ['YH-01']]
        self.assertEqual(len(bought), 2)
        self.assertEqual({tuple(sorted(p['remaining_resources'].items()))
                          for p in bought},
                         {(('GL', 3),), (('H', 2),)})

    def test_equivalent_solver_paths_are_deduplicated(self):
        _, _, plans = self.plans_for(['YH-01'], {'H': 2})
        bought = [p for p in plans if p['card_ids'] == ['YH-01']]
        self.assertEqual(len(bought), 1)

    def test_childhood_resources_are_consumed_only_when_used(self):
        cases = [
            ('C01', 'YP-01', {'M': 1}),
            ('C02', 'YK-01', {'M': 1}),
            ('C03', 'YR-01', {'R': 1}),
            ('C04', 'YH-01', {'H': 1}),
        ]
        for childhood_id, card_id, resources in cases:
            with self.subTest(childhood_id=childhood_id):
                _, _, required = self.plans_for(
                    [card_id], resources, [childhood_id])
                paid = next(p for p in required
                            if p['card_ids'] == [card_id])
                self.assertEqual(paid['consumed_childhood_card_ids'],
                                 [childhood_id])
                self.assertEqual(paid['remaining_resources'], {})

        _, _, optional = self.plans_for(['YH-01'], {'H': 2}, ['C04'])
        outcomes = {(tuple(p['consumed_childhood_card_ids']),
                     tuple(sorted(p['remaining_resources'].items())))
                    for p in optional if p['card_ids'] == ['YH-01']}
        self.assertIn(((), ()), outcomes)
        self.assertIn((('C04',), (('H', 1),)), outcomes)

    def test_c05_is_normal_gl_for_three_gl_and_only_consumed_if_used(self):
        _, _, required = self.plans_for(['YH-01'], {'GL': 2}, ['C05'])
        paid = next(p for p in required if p['card_ids'] == ['YH-01'])
        self.assertEqual(paid['gl_free_acquisitions'], ['YH-01'])
        self.assertEqual(paid['consumed_childhood_card_ids'], ['C05'])

        _, _, optional = self.plans_for(['YH-01'], {'GL': 3}, ['C05'])
        gl_plans = [p for p in optional if p['gl_free_acquisitions']]
        self.assertTrue(any('C05' not in p['consumed_childhood_card_ids']
                            for p in gl_plans))
        self.assertTrue(any('C05' in p['consumed_childhood_card_ids']
                            for p in gl_plans))

    def test_childhood_discount_records_card_and_target(self):
        cases = [
            ('C07', 'YH-01', {'H': 1}, 'H'),
            ('C08', 'YK-01', {'M': 1}, 'K'),
            ('C09', 'YR-01', {'R': 1}, 'R'),
            ('C10', 'YP-01', {'M': 1}, 'M'),
        ]
        for childhood_id, card_id, resources, symbol in cases:
            with self.subTest(childhood_id=childhood_id):
                _, _, plans = self.plans_for(
                    [card_id], resources, [childhood_id])
                paid = next(p for p in plans
                            if p['card_ids'] == [card_id])
                self.assertEqual(paid['consumed_childhood_card_ids'],
                                 [childhood_id])
                self.assertEqual(paid['discounts_used'], [{
                    'childhood_card_id': childhood_id,
                    'card_id': card_id,
                    'symbol': symbol,
                }])

    def test_three_gl_target_and_remaining_resources_are_explicit(self):
        _, _, plans = self.plans_for(['YH-01'], {'GL': 3, 'M': 1})
        paid = next(p for p in plans if p['card_ids'] == ['YH-01'])
        self.assertEqual(paid['gl_free_acquisitions'], ['YH-01'])
        self.assertEqual(paid['spent_resources'], {'GL': 3})
        self.assertEqual(paid['remaining_resources'], {'M': 1})

    def test_existing_active_payment_substitution_is_reused(self):
        game, pool = self.make_game(['YK-01'], {'M': 2})
        game.cv['K'] = ['YK-02']
        plans = game.enumerate_legal_purchase_plans(pool)
        paid = next(p for p in plans if p['card_ids'] == ['YK-01'])
        self.assertEqual(paid['substitutions_used'], [{
            'card_id': 'YK-01', 'from': 'M', 'to': 'K'}])
        self.assertEqual(paid['remaining_resources'], {})

    def test_new_event_cannot_pay_for_another_card_in_same_window(self):
        _, _, plans = self.plans_for(['YE-01', 'YP-01'], {'M': 1})
        self.assertNotIn(('YE-01', 'YP-01'),
                         {tuple(p['card_ids']) for p in plans})

    def test_repeated_generation_is_pure_and_plan_ids_are_stable(self):
        game, pool = self.make_game(['YH-01', 'YK-01'],
                                    {'H': 2, 'K': 1, 'M': 1}, ['C04'])
        before = (list(game.market), list(game.hand), list(game.dice),
                  dict(game.stable_pool), game.rng.getstate())
        first = game.enumerate_legal_purchase_plans(pool)
        second = game.enumerate_legal_purchase_plans(pool)
        after = (list(game.market), list(game.hand), list(game.dice),
                 dict(game.stable_pool), game.rng.getstate())
        self.assertEqual(first, second)
        self.assertEqual(before, after)
        self.assertEqual([p['plan_id'] for p in first],
                         [p['plan_id'] for p in second])

    def test_purchase_ready_exposes_complete_plans_as_actions(self):
        session = GameSession(seed=1, shuffle=False, forced_goals=[1, 2])
        first = session.current_decision()
        second = session.submit_action(first['decision_id'],
                                       {'card_id': 'C12'})['decision']
        session.game._forced = ['H', 'K', 'R', 'M']
        post = session.submit_action(second['decision_id'],
                                     {'card_id': 'C09'})['decision']
        ready = session.submit_action(
            post['decision_id'], {'choice': 'proceed_to_purchase'})['decision']
        self.assertEqual(ready['kind'], 'purchase_ready')
        self.assertIn('purchase_targets', ready)
        self.assertNotIn('legal_acquisition_plans', ready)
        self.assertTrue(ready['purchase_targets'])
        cache = session._purchase_plan_cache
        groups = {}
        for p in cache:
            groups.setdefault((tuple(p['card_ids']), None), []).append(p)
        self.assertEqual(
            {(tuple(t['ordinary_card_ids']),
              t['fate_card_id']): t['payment_option_count']
             for t in ready['purchase_targets']},
            {k: len(v) for k, v in groups.items()})
        for t in ready['purchase_targets']:
            self.assertNotIn('plan_id', t)
            if t['payment_option_count'] > 1:
                self.assertTrue(all(
                    {'spent_resources', 'remaining_resources'} <= set(o)
                    for o in t['payment_options']))
            else:
                self.assertNotIn('payment_options', t)
        # Payload Slim v1：L1 组合集合只出现一次（purchase_targets），
        # 提交动作直接由条目字段构造并被 submit 校验接受。
        self.assertNotIn('legal_actions', ready)

    def test_event_temporary_resources_make_target_affordable_and_are_explicit(self):
        _, _, plans = self.plans_for(['YP-01'], {'M': 1}, ['YE-01'])
        paid = next(p for p in plans if p['card_ids'] == ['YP-01'])
        self.assertEqual(paid['consumed_event_card_ids'], ['YE-01'])
        self.assertEqual(paid['event_temporary_resources_used'], [{
            'event_card_id': 'YE-01', 'resource': 'M', 'amount': 1}])
        self.assertEqual(paid['remaining_resources'], {})

    def test_event_is_not_offered_when_disabled_and_unused_event_is_not_consumed(self):
        game, pool = self.make_game(['YP-01'], {'M': 1}, ['YE-01'])
        game.current_debuff = 'D05'
        game.debuff_active_from_turn = 1
        game.turn = 2
        self.assertEqual({tuple(p['card_ids'])
                          for p in game.enumerate_legal_purchase_plans(pool)}, {()})

        _, _, plans = self.plans_for(['YH-01'], {'H': 2}, ['YE-01'])
        self.assertTrue(all(not p['consumed_event_card_ids'] for p in plans))


class TestRuntimePurchaseExecution(unittest.TestCase):
    def ready_session(self, dice, market=None, extra_hand=(), stable=None,
                      prior_bad_luck=0):
        session = GameSession(seed=1, shuffle=False, forced_goals=[1, 2])
        first = session.current_decision()
        second = session.submit_action(first['decision_id'],
                                       {'card_id': 'C12'})['decision']
        session.game._forced = list(dice)
        post = session.submit_action(second['decision_id'],
                                     {'card_id': 'C09'})['decision']
        if market is not None:
            session.game.market = list(market)
            session.game.market_entry = {
                cid: session.game.turn for cid in session.game.market}
        for cid in extra_hand:
            if cid not in session.game.hand:
                session.game.hand.append(cid)
        if stable:
            session.game.stable_pool = Counter(stable)
        session.game.bad_luck_accumulator = prior_bad_luck
        ready = session.submit_action(
            post['decision_id'], {'choice': 'proceed_to_purchase'})['decision']
        self.assertEqual(ready['kind'], 'purchase_ready')
        return session, ready

    def find_plan(self, session, card_ids, predicate=None):
        """从共享 generator 的缓存 plans 中按目标与条件定位正式 plan。"""
        matches = [p for p in session._purchase_plan_cache
                   if (p['ordinary_card_ids']
                       if 'ordinary_card_ids' in p else p['card_ids'])
                   == list(card_ids)
                   and (predicate is None or predicate(p))]
        self.assertTrue(matches, card_ids)
        return matches[0]

    def submit_plan(self, session, ready, plan):
        """两层流程提交指定正式 plan：必要时先选目标，再提交支付。"""
        current = ready
        result = None
        if 'purchase_targets' in current:
            result = session.submit_action(current['decision_id'], {
                'ordinary_card_ids': list(plan['ordinary_card_ids'])
                if 'ordinary_card_ids' in plan else list(plan['card_ids']),
                'fate_card_id': plan.get('fate_card_id')})
            if not result['ok']:
                return result
            current = result['decision']
        if 'selected_purchase_target' in current:
            return session.submit_action(current['decision_id'],
                                         {'plan_id': plan['plan_id']})
        return result

    def test_selected_event_payment_is_consumed_atomically_and_reported(self):
        session, ready = self.ready_session(
            ['M', 'BL', 'BL', 'BL'], ['YP-01'], extra_hand=['YE-01'])
        plan = self.find_plan(
            session, ('YP-01',),
            lambda p: p['consumed_event_card_ids'] == ['YE-01'])
        result = self.submit_plan(session, ready, plan)
        self.assertTrue(result['ok'])
        self.assertNotIn('YE-01', session.game.hand)
        purchase = result['decision']['previous_turn_result']['purchase_result']
        self.assertEqual(purchase['consumed_event_card_ids'], ['YE-01'])
        self.assertEqual(purchase[
            'event_temporary_resources_used'][0]['amount'], 2)

    def snapshot(self, session):
        game = session.game
        return copy.deepcopy((
            game.pool, game.hand, game.market, game.cv, game.acquired,
            game.pending_new, game.purchased_this_turn, game.stats.game,
            game.current_debuff, game.debuff_deck, game.debuff_history,
            game.debuff_active_from_turn, game.rng.getstate(),
            session._purchase_ready, session._purchase_target,
            session._purchase_executed,
            session._debuff_resolved, game.maintenance_result,
            session._maintenance_resolved, game.market_cleanup_result,
            session._market_cleanup_resolved,
        ))

    def test_zero_card_plan_resolves_without_changing_market(self):
        session, ready = self.ready_session(['H', 'K', 'R', 'M'])
        plan = self.find_plan(session, ())
        result = self.submit_plan(session, ready, plan)
        self.assertTrue(result['ok'])
        self.assertEqual(result['decision']['kind'], 'post_roll_decision')
        self.assertEqual(result['decision']['previous_turn_result']['market_cleanup_result']
                         ['purchased_card_ids'], [])
        self.assertEqual(dict(session.game.pool), {})
        self.assertEqual(session.game.purchased_this_turn, [])

    def test_one_card_plan_executes_exact_remaining_resources(self):
        session, ready = self.ready_session(
            ['H', 'H', 'K', 'M'], ['YH-01', 'YK-01'])
        plan = self.find_plan(session, ('YK-01',))
        rng_state = session.game.rng.getstate()
        result = self.submit_plan(session, ready, plan)
        self.assertTrue(result['ok'])
        self.assertEqual(session.game.rng.getstate(), rng_state)
        self.assertEqual(dict(session.game.pool), plan['remaining_resources'])
        self.assertNotIn('YK-01', session.game.market)
        self.assertIn('YH-01', session.game.market)
        self.assertEqual(result['decision']['kind'], 'placement_decision')

    def test_two_card_plan_removes_only_bought_cards(self):
        session, ready = self.ready_session(
            ['H', 'H', 'K', 'M'], ['YH-01', 'YK-01', 'YE-01'])
        plan = self.find_plan(session, ('YH-01', 'YK-01'))
        result = self.submit_plan(session, ready, plan)
        self.assertTrue(result['ok'])
        self.assertEqual(dict(session.game.pool), plan['remaining_resources'])
        self.assertEqual(session.game.market, ['YE-01'])
        self.assertEqual(session.game.purchased_this_turn,
                         ['YH-01', 'YK-01'])

    def test_childhood_and_gl_outcome_follow_selected_plan_without_resolve(self):
        session, ready = self.ready_session(
            ['GL', 'GL', 'GL', 'BL'], ['YH-01'],
            extra_hand=['C05'], stable={'H': 2})
        gl_plan = self.find_plan(
            session, ('YH-01',),
            lambda p: p['gl_free_acquisitions'] == ['YH-01']
            and 'C05' not in p['consumed_childhood_card_ids'])
        with patch('ailife.engine.solve_cost_all',
                   side_effect=AssertionError('executor must not solve')):
            result = self.submit_plan(session, ready, gl_plan)
        self.assertTrue(result['ok'])
        self.assertIn('C05', session.game.hand)
        self.assertEqual(dict(session.game.pool),
                         gl_plan['remaining_resources'])

    def test_declared_discount_is_consumed_even_when_full_cost_is_affordable(self):
        session, ready = self.ready_session(
            ['H', 'H', 'BL', 'BL'], ['YH-01'], extra_hand=['C07'])
        plan = self.find_plan(
            session, ('YH-01',), lambda p: bool(p['discounts_used']))
        result = self.submit_plan(session, ready, plan)
        self.assertTrue(result['ok'])
        self.assertNotIn('C07', session.game.hand)
        self.assertEqual(dict(session.game.pool), plan['remaining_resources'])

    def test_invalid_plan_id_and_state_mismatch_have_no_side_effects(self):
        session, ready = self.ready_session(
            ['H', 'H', 'K', 'M'], ['YH-01', 'YK-01'])
        before = self.snapshot(session)
        invalid = session.submit_action(ready['decision_id'],
                                        {'plan_id': 'purchase_not_real'})
        self.assertFalse(invalid['ok'])
        self.assertEqual(invalid['error'], 'illegal_plan_id')
        self.assertEqual(self.snapshot(session), before)

        # 多支付目标的 plan_id 未在第一层公布，不能直接提交
        session, ready = self.ready_session(
            ['H', 'H', 'H', 'BL'], ['YH-01'], extra_hand=['C07'])
        multi = self.find_plan(session, ('YH-01',),
                               lambda p: bool(p['discounts_used']))
        before = self.snapshot(session)
        unpublished = session.submit_action(ready['decision_id'],
                                            {'plan_id': multi['plan_id']})
        self.assertFalse(unpublished['ok'])
        self.assertEqual(unpublished['error'], 'illegal_plan_id')
        self.assertEqual(self.snapshot(session), before)

        # 第二层：骰面被改后提交正式 plan → plan_state_mismatch，待选目标保留
        second = session.submit_action(ready['decision_id'], {
            'ordinary_card_ids': ['YH-01'], 'fate_card_id': None})['decision']
        plan = self.find_plan(session, ('YH-01',),
                              lambda p: not p['discounts_used'])
        session.game.dice[0] = 'R'
        changed = self.snapshot(session)
        mismatch = session.submit_action(second['decision_id'],
                                         {'plan_id': plan['plan_id']})
        self.assertFalse(mismatch['ok'])
        self.assertEqual(mismatch['error'], 'plan_state_mismatch')
        self.assertEqual(self.snapshot(session), changed)

        # 第二层：市场缺货 → plan_state_mismatch
        session, ready = self.ready_session(
            ['H', 'H', 'H', 'BL'], ['YH-01'], extra_hand=['C07'])
        second = session.submit_action(ready['decision_id'], {
            'ordinary_card_ids': ['YH-01'], 'fate_card_id': None})['decision']
        plan = self.find_plan(session, ('YH-01',))
        session.game.market.remove('YH-01')
        changed = self.snapshot(session)
        mismatch = session.submit_action(second['decision_id'],
                                         {'plan_id': plan['plan_id']})
        self.assertFalse(mismatch['ok'])
        self.assertEqual(mismatch['error'], 'plan_state_mismatch')
        self.assertEqual(self.snapshot(session), changed)

    def test_event_enters_hand_and_cannot_reenter_finished_payment(self):
        session, ready = self.ready_session(
            ['M', 'BL', 'BL', 'BL'], ['YE-01', 'YP-01'])
        plan = self.find_plan(session, ('YE-01',))
        result = self.submit_plan(session, ready, plan)
        self.assertTrue(result['ok'])
        self.assertEqual(result['decision']['kind'], 'post_roll_decision')
        self.assertIn('YE-01', session.game.hand)
        self.assertNotIn('YP-01', session.game.purchased_this_turn)
        self.assertEqual(dict(session.game.pool), {})

    def test_work_and_possession_are_automatically_placed_top(self):
        session, ready = self.ready_session(
            ['K', 'K', 'M', 'M'], ['YW-01', 'YP-01'])
        plan = self.find_plan(session, ('YW-01', 'YP-01'))
        result = self.submit_plan(session, ready, plan)
        self.assertTrue(result['ok'])
        self.assertEqual(result['decision']['kind'], 'post_roll_decision')
        self.assertEqual(session.game.active('W'), 'YW-01')
        self.assertEqual(session.game.active('P'), 'YP-01')
        self.assertEqual(session.game.pending_new, [])

    def test_hkr_placement_top_and_bury(self):
        session, ready = self.ready_session(
            ['H', 'H', 'BL', 'BL'], ['YH-01'])
        session.game.cv['H'] = ['YH-03']
        plan = self.find_plan(session, ('YH-01',))
        placed = self.submit_plan(session, ready, plan)
        decision = placed['decision']
        self.assertEqual(decision['kind'], 'placement_decision')
        self.assertEqual(decision['card_id'], 'YH-01')
        self.assertEqual(decision['current_stack'], ['YH-03'])
        self.assertEqual(decision['legal_actions'],
                         [{'placement': 'top'}, {'placement': 'bury'}])
        top = session.submit_action(decision['decision_id'],
                                    {'placement': 'top'})
        self.assertEqual(top['decision']['kind'], 'post_roll_decision')
        self.assertEqual(session.game.cv['H'], ['YH-03', 'YH-01'])

        session, ready = self.ready_session(
            ['H', 'H', 'BL', 'BL'], ['YH-01'])
        session.game.cv['H'] = ['YH-03']
        plan = self.find_plan(session, ('YH-01',))
        decision = self.submit_plan(session, ready, plan)['decision']
        bury = session.submit_action(decision['decision_id'],
                                     {'placement': 'bury'})
        self.assertEqual(bury['decision']['kind'], 'post_roll_decision')
        self.assertEqual(session.game.cv['H'], ['YH-01', 'YH-03'])

    def test_multiple_hkr_cards_are_placed_one_at_a_time_in_stable_order(self):
        session, ready = self.ready_session(
            ['H', 'H', 'H', 'K'], ['YH-01', 'YH-02'])
        plan = self.find_plan(session, ('YH-01', 'YH-02'))
        first = self.submit_plan(session, ready, plan)['decision']
        self.assertEqual(first['card_id'], 'YH-01')
        second = session.submit_action(first['decision_id'],
                                       {'placement': 'top'})['decision']
        self.assertEqual(second['kind'], 'placement_decision')
        self.assertEqual(second['card_id'], 'YH-02')
        done = session.submit_action(second['decision_id'],
                                     {'placement': 'bury'})
        self.assertEqual(done['decision']['kind'], 'post_roll_decision')
        self.assertEqual(session.game.cv['H'], ['YH-02', 'YH-01'])

    def test_stale_and_duplicate_purchase_submissions_are_rejected(self):
        session, ready = self.ready_session(
            ['M', 'BL', 'BL', 'BL'], ['YE-01'])
        plan = self.find_plan(session, ('YE-01',))
        old_other_plan = self.find_plan(session, ())
        first = self.submit_plan(session, ready, plan)
        self.assertTrue(first['ok'])
        after = self.snapshot(session)
        duplicate = self.submit_plan(session, ready, plan)
        self.assertFalse(duplicate['ok'])
        self.assertEqual(duplicate['error'], 'stale_or_unknown_decision_id')
        self.assertEqual(self.snapshot(session), after)

        stale = self.submit_plan(session, ready, old_other_plan)
        self.assertFalse(stale['ok'])
        self.assertEqual(stale['error'], 'stale_or_unknown_decision_id')
        self.assertEqual(self.snapshot(session), after)

    def test_cv_stays_pending_until_debuff_protection_is_resolved(self):
        session, ready = self.ready_session(
            ['H', 'H', 'BL', 'BL'], ['YH-01'],
            extra_hand=['C06'], stable={'BL': 1}, prior_bad_luck=3)
        self.assertEqual(session.game.bad_luck_accumulator, 5)
        plan = self.find_plan(session, ('YH-01',))
        result = self.submit_plan(session, ready, plan)
        self.assertTrue(result['ok'])
        self.assertEqual(result['decision']['kind'],
                         'debuff_protection_decision')
        self.assertEqual(result['decision']['remaining_bad_luck'], 5)
        self.assertEqual(session.game.pending_new, ['YH-01'])
        self.assertIsNone(session.game.active('H'))
        self.assertIsNone(session.game.current_debuff)

    def test_below_threshold_skips_debuff_and_enters_placement(self):
        session, ready = self.ready_session(
            ['H', 'H', 'BL', 'BL'], ['YH-01'])
        plan = self.find_plan(session, ('YH-01',))
        result = self.submit_plan(session, ready, plan)
        self.assertEqual(result['decision']['kind'], 'placement_decision')
        self.assertIsNone(session.game.current_debuff)
        self.assertEqual(session.game.stats.game['debuff_triggers'], 0)

    def test_five_accumulated_bad_luck_without_c06_draws_once_next_turn_active(self):
        session, ready = self.ready_session(
            ['H', 'H', 'BL', 'BL'], ['YH-01'], stable={'BL': 2},
            prior_bad_luck=3)
        plan = self.find_plan(session, ('YH-01',))
        result = self.submit_plan(session, ready, plan)
        self.assertEqual(result['decision']['kind'], 'placement_decision')
        self.assertEqual(session.game.stats.game['debuff_triggers'], 1)
        self.assertEqual(len(session.game.stats.game['debuff_drawn']), 1)
        self.assertEqual(len(session.game.debuff_deck), 11)
        self.assertIsNone(session.game.active_debuff_card())
        self.assertEqual(session.game.debuff_active_from_turn,
                         session.game.turn + 1)

    def test_c06_use_cancels_without_draw_or_adversity_history(self):
        session, ready = self.ready_session(
            ['H', 'H', 'BL', 'BL'], ['YH-01'],
            extra_hand=['C06'], stable={'BL': 1}, prior_bad_luck=3)
        plan = self.find_plan(session, ('YH-01',))
        protection = self.submit_plan(session, ready, plan)['decision']
        deck_before = list(session.game.debuff_deck)
        result = session.submit_action(protection['decision_id'],
                                       {'choice': 'use'})
        self.assertTrue(result['ok'])
        self.assertEqual(result['decision']['kind'], 'placement_decision')
        self.assertNotIn('C06', session.game.hand)
        self.assertEqual(session.game.debuff_deck, deck_before)
        self.assertIsNone(session.game.current_debuff)
        self.assertEqual(session.game.debuff_history, [])

    def test_c06_skip_keeps_card_and_draws_debuff_before_placement(self):
        session, ready = self.ready_session(
            ['H', 'H', 'BL', 'BL'], ['YH-01'],
            extra_hand=['C06'], stable={'BL': 1}, prior_bad_luck=3)
        plan = self.find_plan(session, ('YH-01',))
        protection = self.submit_plan(session, ready, plan)['decision']
        result = session.submit_action(protection['decision_id'],
                                       {'choice': 'skip'})
        self.assertTrue(result['ok'])
        self.assertEqual(result['decision']['kind'], 'placement_decision')
        self.assertIn('C06', session.game.hand)
        self.assertIsNotNone(session.game.current_debuff)
        self.assertEqual(session.game.pending_new, ['YH-01'])

    def test_debuff_resolution_precedes_forced_work_placement(self):
        session, ready = self.ready_session(
            ['K', 'K', 'BL', 'BL'], ['YW-01'],
            extra_hand=['C06'], stable={'BL': 1}, prior_bad_luck=3)
        plan = self.find_plan(session, ('YW-01',))
        protection = self.submit_plan(session, ready, plan)['decision']
        self.assertEqual(protection['kind'], 'debuff_protection_decision')
        self.assertEqual(session.game.pending_new, ['YW-01'])
        self.assertIsNone(session.game.active('W'))
        done = session.submit_action(protection['decision_id'],
                                     {'choice': 'skip'})
        self.assertEqual(done['decision']['kind'], 'post_roll_decision')
        self.assertEqual(session.game.active('W'), 'YW-01')

    def test_debuff_result_reports_c06_as_cancel_source(self):
        session, ready = self.ready_session(
            ['H', 'H', 'BL', 'BL'], ['YH-01'],
            extra_hand=['C06'], stable={'BL': 1}, prior_bad_luck=3)
        plan = self.find_plan(session, ('YH-01',))
        protection = self.submit_plan(session, ready, plan)['decision']
        done = session.submit_action(protection['decision_id'],
                                     {'choice': 'use',
                                      'source_card_id': 'C06'})
        self.assertTrue(done['ok'])
        placed = session.submit_action(done['decision']['decision_id'],
                                       {'placement': 'top'})
        self.assertTrue(placed['ok'])
        previous = placed['decision']['previous_turn_result']
        self.assertEqual(previous['debuff_result'], {
            'outcome': 'cancelled', 'drawn_card_id': None,
            'cancelled_by_card_id': 'C06',
        })

    def test_debuff_result_reports_mh02_as_cancel_source(self):
        session, ready = self.ready_session(
            ['H', 'H', 'BL', 'BL'], ['YH-01'], stable={'BL': 1},
            prior_bad_luck=3)
        session.game.cv['H'] = ['MH-02']
        plan = self.find_plan(session, ('YH-01',))
        protection = self.submit_plan(session, ready, plan)['decision']
        self.assertEqual(protection['kind'], 'debuff_protection_decision')
        done = session.submit_action(protection['decision_id'],
                                     {'choice': 'use',
                                      'source_card_id': 'MH-02'})
        self.assertTrue(done['ok'])
        placed = session.submit_action(done['decision']['decision_id'],
                                       {'placement': 'top'})
        self.assertTrue(placed['ok'])
        previous = placed['decision']['previous_turn_result']
        self.assertEqual(previous['debuff_result'], {
            'outcome': 'cancelled', 'drawn_card_id': None,
            'cancelled_by_card_id': 'MH-02',
        })

    def test_debuff_result_has_no_cancel_source_when_skipped(self):
        session, ready = self.ready_session(
            ['H', 'H', 'BL', 'BL'], ['YH-01'],
            extra_hand=['C06'], stable={'BL': 1}, prior_bad_luck=3)
        plan = self.find_plan(session, ('YH-01',))
        protection = self.submit_plan(session, ready, plan)['decision']
        done = session.submit_action(protection['decision_id'],
                                     {'choice': 'skip'})
        self.assertTrue(done['ok'])
        placed = session.submit_action(done['decision']['decision_id'],
                                       {'placement': 'top'})
        self.assertTrue(placed['ok'])
        previous = placed['decision']['previous_turn_result']
        self.assertEqual(previous['debuff_result'], {
            'outcome': 'drawn', 'drawn_card_id': 'D01',
            'cancelled_by_card_id': None,
        })

    def test_multiple_placements_follow_debuff_and_protection_actions_are_safe(self):
        session, ready = self.ready_session(
            ['H', 'H', 'H', 'K'], ['YH-01', 'YH-02'],
            extra_hand=['C06'], stable={'BL': 3}, prior_bad_luck=5)
        plan = self.find_plan(session, ('YH-01', 'YH-02'))
        protection = self.submit_plan(session, ready, plan)['decision']
        before = self.snapshot(session)
        invalid = session.submit_action(protection['decision_id'],
                                        {'choice': 'later'})
        self.assertFalse(invalid['ok'])
        self.assertEqual(self.snapshot(session), before)
        first = session.submit_action(protection['decision_id'],
                                      {'choice': 'use'})
        self.assertEqual(first['decision']['kind'], 'placement_decision')
        stale = session.submit_action(protection['decision_id'],
                                      {'choice': 'skip'})
        self.assertFalse(stale['ok'])
        self.assertEqual(stale['error'], 'stale_or_unknown_decision_id')
        second = session.submit_action(first['decision']['decision_id'],
                                       {'placement': 'top'})
        self.assertEqual(second['decision']['kind'], 'placement_decision')
        done = session.submit_action(second['decision']['decision_id'],
                                     {'placement': 'bury'})
        self.assertEqual(done['decision']['kind'], 'post_roll_decision')
        self.assertEqual(session.game.stats.game['debuff_triggers'], 1)
        self.assertEqual(session.game.stats.game['debuff_drawn'], [])

    def maintenance_session(self, dice, stacks=(), stable=None, hand=()):
        session, ready = self.ready_session(dice, [], extra_hand=hand,
                                            stable=stable)
        for cls, cards in stacks:
            session.game.cv[cls] = list(cards)
            for cid in cards:
                session.game.acquired[cid] = 0
        empty_plan = self.find_plan(session, ())
        result = self.submit_plan(session, ready, empty_plan)
        return session, result['decision']

    def test_no_upkeep_and_new_card_are_automatically_exempt(self):
        session, decision = self.maintenance_session(['H', 'K', 'R', 'M'])
        self.assertEqual(decision['kind'], 'post_roll_decision')
        self.assertEqual(decision['previous_turn_result']['maintenance_result']
                         ['maintained_card_ids'], [])
        self.assertEqual(decision['previous_turn_result']['maintenance_result']
                         ['lost_card_ids'], [])

        session, ready = self.ready_session(
            ['H', 'R', 'K', 'M'], ['YW-02'])
        plan = self.find_plan(session, ('YW-02',))
        decision = self.submit_plan(session, ready, plan)['decision']
        self.assertEqual(decision['kind'], 'post_roll_decision')
        self.assertEqual(session.game.active('W'), 'YW-02')
        self.assertEqual(decision['previous_turn_result']['maintenance_result']
                         ['maintained_card_ids'], [])

    def test_non_active_card_is_not_in_maintenance_targets(self):
        session, decision = self.maintenance_session(
            ['K', 'R', 'GL', 'BL'], [('W', ['YW-02', 'YW-01'])])
        self.assertEqual(decision['kind'], 'post_roll_decision')
        self.assertEqual(
            decision['previous_turn_result']['maintenance_result']['lost_card_ids'], [])
        self.assertEqual(session.game.stats.card('YW-02')['upkeep_active'], 0)

    def test_sufficient_resources_force_full_maintenance_and_auto_result(self):
        session, decision = self.maintenance_session(
            ['H', 'M', 'K', 'R'], [('R', ['MR-01']), ('W', ['YW-02'])])
        self.assertEqual(decision['kind'], 'post_roll_decision')
        result = decision['previous_turn_result']['maintenance_result']
        self.assertEqual(result['maintained_card_ids'], ['MR-01', 'YW-02'])
        self.assertEqual(result['lost_card_ids'], [])
        self.assertEqual(result['spent_resources'], {'H': 1, 'M': 1})
        self.assertEqual(result['remaining_resources'], {'K': 1, 'R': 1})

    def test_only_one_maximal_result_is_automatic(self):
        session, decision = self.maintenance_session(
            ['K', 'R', 'GL', 'BL'], [('R', ['MR-01'])])
        self.assertEqual(decision['kind'], 'post_roll_decision')
        self.assertEqual(decision['previous_turn_result']['maintenance_result']
                         ['lost_card_ids'],
                         ['MR-01'])
        self.assertEqual(session.game.active('R'), None)

    def test_runtime_and_simulator_share_two_oh05_maintenance_plans(self):
        stacks = [('H', ['OH-05']), ('R', ['MR-01']), ('W', ['YW-02'])]
        session, decision = self.maintenance_session(
            ['H', 'K', 'R', 'GL'], stacks)
        self.assertEqual(decision['kind'], 'maintenance_decision')
        runtime_plans = decision['legal_maintenance_plans']
        outcomes = {(tuple(p['maintained_card_ids']), tuple(p['lost_card_ids']))
                    for p in runtime_plans}
        self.assertEqual(outcomes, {
            (('MR-01',), ('YW-02',)),
            (('YW-02',), ('MR-01',)),
        })
        mr_plan = next(p for p in runtime_plans
                       if p['maintained_card_ids'] == ['MR-01'])
        self.assertEqual(mr_plan['payments'][0]['substitutions_used'],
                         [{'from': 'H', 'to': 'M'}])

        simulator = Game(CONFIGS['V06'], Balanced, random.Random(31),
                         shuffle=False, forced_goals=[1, 2])
        simulator.turn = session.game.turn
        simulator.pool = Counter(session.game.pool)
        simulator.hand = list(session.game.hand)
        for cls, cards in stacks:
            simulator.cv[cls] = list(cards)
            for cid in cards:
                simulator.acquired[cid] = 0
        simulator_plans = simulator.maintenance_plans()
        self.assertEqual(runtime_plans, simulator_plans)
        simulator._upkeep()
        self.assertEqual(simulator.active('R'), 'MR-01')
        self.assertIsNone(simulator.active('W'))
        self.assertEqual(simulator.maintenance_result['maintained_card_ids'],
                         ['MR-01'])

    def test_runtime_can_choose_either_maximal_maintenance_outcome(self):
        stacks = [('H', ['OH-05']), ('R', ['MR-01']), ('W', ['YW-02'])]
        session, decision = self.maintenance_session(
            ['H', 'K', 'R', 'GL'], stacks)
        keep_r = next(p for p in decision['legal_maintenance_plans']
                      if p['maintained_card_ids'] == ['MR-01'])
        result = session.submit_action(decision['decision_id'],
                                       {'plan_id': keep_r['plan_id']})
        self.assertEqual(result['decision']['kind'], 'post_roll_decision')
        self.assertEqual(session.game.active('R'), 'MR-01')
        self.assertIsNone(session.game.active('W'))
        self.assertEqual(
            set(result['decision']['previous_turn_result']['maintenance_result']),
            {'paid', 'maintained_card_ids', 'lost_card_ids',
             'spent_resources', 'remaining_resources', 'me01_used'})

        session, decision = self.maintenance_session(
            ['H', 'K', 'R', 'GL'], stacks)
        keep_w = next(p for p in decision['legal_maintenance_plans']
                      if p['maintained_card_ids'] == ['YW-02'])
        result = session.submit_action(decision['decision_id'],
                                       {'plan_id': keep_w['plan_id']})
        self.assertEqual(result['decision']['kind'], 'post_roll_decision')
        self.assertEqual(session.game.active('W'), 'YW-02')
        self.assertIsNone(session.game.active('R'))

    def test_me01_options_and_execution_are_part_of_maintenance_plans(self):
        stacks = [('R', ['MR-01']), ('W', ['YW-02'])]
        session, decision = self.maintenance_session(
            ['H', 'K', 'R', 'GL'], stacks, hand=['ME-01'])
        self.assertEqual(decision['kind'], 'maintenance_decision')
        plans = decision['legal_maintenance_plans']
        self.assertTrue(any(p['me01_used'] is None for p in plans))
        self.assertEqual({p['me01_used']['target_card_id'] for p in plans
                          if p['me01_used']}, {'MR-01', 'YW-02'})
        keep_both = next(p for p in plans
                         if len(p['maintained_card_ids']) == 2)
        result = session.submit_action(decision['decision_id'],
                                       {'plan_id': keep_both['plan_id']})
        summary = result['decision']['previous_turn_result']['maintenance_result']
        self.assertEqual(result['decision']['kind'], 'post_roll_decision')
        self.assertNotIn('ME-01', session.game.hand)
        self.assertEqual(summary['me01_used'], keep_both['me01_used'])

    def test_sufficient_full_maintenance_does_not_waste_me01(self):
        stacks = [('R', ['MR-01']), ('W', ['YW-02'])]
        session, decision = self.maintenance_session(
            ['H', 'M', 'K', 'R'], stacks, hand=['ME-01'])
        self.assertEqual(decision['kind'], 'post_roll_decision')
        self.assertIn('ME-01', session.game.hand)
        self.assertIsNone(decision['previous_turn_result']['maintenance_result']
                          ['me01_used'])

    def test_lost_active_does_not_charge_newly_exposed_card_this_window(self):
        session, decision = self.maintenance_session(
            ['K', 'R', 'GL', 'BL'], [('W', ['YW-02', 'MW-02'])])
        self.assertEqual(decision['kind'], 'post_roll_decision')
        self.assertEqual(decision['previous_turn_result']['maintenance_result']
                         ['lost_card_ids'],
                         ['MW-02'])
        self.assertEqual(session.game.active('W'), 'YW-02')
        self.assertEqual(session.game.stats.card('YW-02')['upkeep_active'], 0)

    def test_maintenance_plan_rejects_invalid_stale_and_duplicate_submission(self):
        stacks = [('H', ['OH-05']), ('R', ['MR-01']), ('W', ['YW-02'])]
        session, decision = self.maintenance_session(
            ['H', 'K', 'R', 'GL'], stacks)
        before = self.snapshot(session)
        invalid = session.submit_action(decision['decision_id'],
                                        {'plan_id': 'maintenance_invalid'})
        self.assertFalse(invalid['ok'])
        self.assertEqual(self.snapshot(session), before)
        plan = decision['legal_maintenance_plans'][0]
        accepted = session.submit_action(decision['decision_id'],
                                         {'plan_id': plan['plan_id']})
        self.assertTrue(accepted['ok'])
        after = self.snapshot(session)
        duplicate = session.submit_action(decision['decision_id'],
                                          {'plan_id': plan['plan_id']})
        self.assertFalse(duplicate['ok'])
        self.assertEqual(duplicate['error'], 'stale_or_unknown_decision_id')
        self.assertEqual(self.snapshot(session), after)

    def market_protection_session(self, hand=('YE-04',)):
        market = ['YH-01', 'YH-02', 'YH-03', 'YK-01', 'YK-02']
        session, ready = self.ready_session(
            ['H', 'K', 'R', 'M'], market, extra_hand=hand)
        session.game.decks = {
            'youth': ['YK-03', 'YK-04', 'YK-05'],
            'middle': [], 'elder': [],
        }
        plan = self.find_plan(session, ())
        return session, self.submit_plan(session, ready, plan)['decision']

    def test_market_cleanup_without_ye04_is_automatic_and_reports_result(self):
        session, decision = self.market_protection_session(hand=())
        self.assertEqual(decision['kind'], 'post_roll_decision')
        result = decision['previous_turn_result']['market_cleanup_result']
        self.assertEqual(result['purchased_card_ids'], [])
        self.assertEqual(len(result['system_eliminated_card_ids']), 3)
        self.assertEqual(result['system_eliminated_card_ids'][0], 'YH-01')
        self.assertEqual(len(result['market_after_refill']), 5)
        self.assertEqual(result['market_after_elimination'], [
            cid for cid in result['market_before_cleanup']
            if cid not in result['system_eliminated_card_ids']])
        self.assertFalse(result['ye04_used'])
        self.assertFalse(result['final_round_pending'])

    def test_ye04_protection_requires_current_market_target_and_is_single_use(self):
        session, decision = self.market_protection_session()
        self.assertEqual(decision['kind'], 'market_protection_decision')
        self.assertEqual(decision['system_elimination_count'], 3)
        self.assertEqual(
            [action['target_card_id'] for action in decision['legal_actions'][1:]],
            ['YH-01', 'YH-02', 'YH-03', 'YK-01', 'YK-02'])
        before = self.snapshot(session)
        invalid = session.submit_action(
            decision['decision_id'], {'choice': 'use', 'target_card_id': 'YH-04'})
        self.assertFalse(invalid['ok'])
        self.assertEqual(self.snapshot(session), before)

        accepted = session.submit_action(
            decision['decision_id'],
            {'choice': 'use', 'target_card_id': 'YH-01'})
        self.assertTrue(accepted['ok'])
        resolved = accepted['decision']
        self.assertEqual(resolved['kind'], 'post_roll_decision')
        result = resolved['previous_turn_result']['market_cleanup_result']
        self.assertTrue(result['ye04_used'])
        self.assertEqual(result['protected_card_id'], 'YH-01')
        self.assertNotIn('YH-01', result['system_eliminated_card_ids'])
        self.assertEqual(result['system_eliminated_card_ids'][0], 'YH-02')
        self.assertEqual(len(result['system_eliminated_card_ids']), 3)
        self.assertNotIn('YE-04', session.game.hand)

        after = self.snapshot(session)
        duplicate = session.submit_action(
            decision['decision_id'], {'choice': 'skip'})
        self.assertFalse(duplicate['ok'])
        self.assertEqual(duplicate['error'], 'stale_or_unknown_decision_id')
        self.assertEqual(self.snapshot(session), after)

    def test_ye04_skip_keeps_card_and_uses_shared_cleanup_rule(self):
        session, decision = self.market_protection_session()
        before_rng = session.game.rng.getstate()
        accepted = session.submit_action(decision['decision_id'], {'choice': 'skip'})
        self.assertTrue(accepted['ok'])
        result = accepted['decision']['previous_turn_result']['market_cleanup_result']
        self.assertIn('YE-04', session.game.hand)
        self.assertFalse(result['ye04_used'])
        self.assertEqual(result['system_eliminated_card_ids'][0], 'YH-01')
        self.assertNotEqual(session.game.rng.getstate(), before_rng)

    def test_closeout_auto_starts_next_turn_and_keeps_previous_turn_result(self):
        session, decision = self.market_protection_session(hand=())
        self.assertEqual(decision['kind'], 'post_roll_decision')
        self.assertEqual(decision['turn'], 2)
        self.assertEqual(session.game.turn, 2)
        self.assertEqual(dict(session.game.pool), {})
        self.assertEqual(session.game.dice, decision['dice'])
        self.assertEqual(len(session.game.dice), 4)
        previous = decision['previous_turn_result']
        self.assertEqual(previous['completed_turn'], 1)
        self.assertEqual(previous['next_turn'], 2)
        self.assertIn('maintenance_result', previous)
        self.assertIn('market_cleanup_result', previous)
        self.assertEqual(previous['maintenance_result'], {
            'paid': [], 'maintained_card_ids': [], 'lost_card_ids': [],
            'spent_resources': {},
            'remaining_resources': {'H': 1, 'K': 1, 'M': 1, 'R': 1},
            'me01_used': None,
        })
        self.assertEqual(previous['market_cleanup_result']['purchased_card_ids'], [])

        before = self.snapshot(session)
        again = session.current_decision()
        self.assertEqual(again, decision)
        self.assertEqual(self.snapshot(session), before)
        rejected = session.submit_action(decision['decision_id'], {'choice': 'continue'})
        self.assertFalse(rejected['ok'])
        self.assertEqual(rejected['error'], 'illegal_action')

    def test_runtime_new_debuff_is_reported_but_not_advanced_during_draw_turn(self):
        session, ready = self.ready_session(
            ['H', 'K', 'BL', 'BL'], ['YH-01'], stable={'BL': 1},
            prior_bad_luck=3)
        plan = self.find_plan(session, ())
        result = self.submit_plan(session, ready, plan)
        self.assertEqual(result['decision']['kind'], 'post_roll_decision')
        previous = result['decision']['previous_turn_result']
        self.assertEqual(previous['debuff_result'], {
            'outcome': 'drawn', 'drawn_card_id': 'D01',
            'cancelled_by_card_id': None,
        })
        self.assertFalse(previous['debuff_lifecycle']['advanced'])
        self.assertEqual(session.game.current_debuff, 'D01')
        self.assertEqual(session.game.debuff_active_from_turn, 2)
        self.assertEqual(session.game.debuff_turns_remaining, 3)
        self.assertEqual(result['decision']['current_debuff']['card_id'], 'D01')

    def test_runtime_final_pending_first_closeout_starts_allowed_final_turn(self):
        market = ['YH-01', 'YH-02', 'YH-03', 'YH-04', 'YK-01']
        session, ready = self.ready_session(['H', 'K', 'R', 'M'], market)
        session.game.decks = {
            'youth': [], 'middle': [], 'elder': ['OP-01', 'OP-02', 'OP-03'],
        }
        result = self.submit_plan(session, ready, self.find_plan(session, ()))
        decision = result['decision']
        self.assertEqual(decision['kind'], 'post_roll_decision')
        self.assertEqual(decision['turn'], 2)
        self.assertTrue(session.game.final_pending)
        self.assertTrue(
            decision['previous_turn_result']['market_cleanup_result']
            ['final_round_started'])
        self.assertFalse(session.game.game_over)

    def next_turn_session(self, stacks=(), hand=(), debuff=None):
        session = GameSession(seed=19, shuffle=False, forced_goals=[1, 2])
        game = session.game
        game.childhood_complete = True
        game.turn = 2
        game.hand = list(hand)
        for cls, cards in stacks:
            game.cv[cls] = list(cards)
        if debuff:
            game.current_debuff = debuff
            game.debuff_active_from_turn = 2
            game.debuff_turns_remaining = 3
        session._turn_closeout_resolved = True
        session._previous_turn_result = {
            'completed_turn': 1, 'next_turn': 2,
            'maintenance_result': {'paid': []},
            'market_cleanup_result': {'purchased_card_ids': []},
        }
        return session, session.current_decision()

    def submit_pre_roll(self, session, decision, flex=None, c11=False,
                        ye05=False, me02=False):
        return session.submit_action(decision['decision_id'], {
            'flex_resource_choices': flex or {},
            'use_c11': c11,
            'use_ye05': ye05,
            'use_me02': me02,
        })

    def test_multiple_flex_cards_share_one_pre_roll_decision(self):
        session, decision = self.next_turn_session(
            stacks=[('K', ['MK-04']), ('W', ['OW-01'])])
        self.assertEqual(decision['kind'], 'pre_roll_decision')
        self.assertEqual(decision['fixed_stable_resources'], {'M': 3})
        self.assertEqual(decision['flexible_resource_choices'], [
            {'card_id': 'MK-04', 'allowed_resources': ['K', 'R', 'M']},
            {'card_id': 'OW-01', 'allowed_resources': ['K', 'R']},
        ])
        # pre_roll 是本回合唯一携带上一回合结果者（有 pre-roll 时）
        self.assertEqual(decision['previous_turn_result']['completed_turn'], 1)
        result = self.submit_pre_roll(
            session, decision, {'MK-04': 'R', 'OW-01': 'K'})
        self.assertTrue(result['ok'])
        rolled = result['decision']
        self.assertEqual(rolled['kind'], 'post_roll_decision')
        self.assertEqual(rolled['stable_resources'], {'K': 1, 'M': 3, 'R': 1})
        self.assertNotIn('previous_turn_result', rolled)

    def test_ok02_flex_and_invalid_choice_are_validated_without_side_effects(self):
        session, decision = self.next_turn_session(stacks=[('K', ['OK-02'])])
        self.assertEqual(decision['kind'], 'pre_roll_decision')
        before = self.snapshot(session)
        invalid = self.submit_pre_roll(session, decision, {'OK-02': 'MONEY'})
        self.assertFalse(invalid['ok'])
        self.assertEqual(self.snapshot(session), before)
        result = self.submit_pre_roll(session, decision, {'OK-02': 'H'})
        self.assertTrue(result['ok'])
        self.assertEqual(result['decision']['stable_resources'], {'H': 1})

    def test_c11_and_ye05_stack_under_seven_die_cap_and_skip_preserves_cards(self):
        session, decision = self.next_turn_session(
            stacks=[('H', ['YH-02'])], hand=['C11', 'YE-05'])
        self.assertEqual(decision['base_dice_count'], 5)
        result = self.submit_pre_roll(session, decision, c11=True, ye05=True)
        self.assertTrue(result['ok'])
        self.assertEqual(result['decision']['dice_count'], 7)
        self.assertNotIn('C11', session.game.hand)
        self.assertNotIn('YE-05', session.game.hand)
        self.assertEqual(result['decision']['pre_roll_effects_used']['used_card_ids'],
                         ['C11', 'YE-05'])

        skipped, decision = self.next_turn_session(hand=['C11', 'YE-05'])
        result = self.submit_pre_roll(skipped, decision)
        self.assertTrue(result['ok'])
        self.assertIn('C11', skipped.game.hand)
        self.assertIn('YE-05', skipped.game.hand)

    def test_me02_arms_pre_debuff_cancel_and_d05_blocks_only_events(self):
        session, decision = self.next_turn_session(hand=['ME-02'])
        result = self.submit_pre_roll(session, decision, me02=True)
        self.assertTrue(result['ok'])
        self.assertNotIn('ME-02', session.game.hand)
        self.assertEqual(session.game.pre_debuff_cancel, 'ME-02')
        session.game.bad_luck_accumulator = 5
        self.assertEqual(session.game.resolve_runtime_debuff(), 'pre_cancelled')
        self.assertIsNone(session.game.current_debuff)

        blocked, decision = self.next_turn_session(
            hand=['C11', 'YE-05', 'ME-02'], debuff='D05')
        self.assertEqual(decision['kind'], 'pre_roll_decision')
        self.assertEqual([c['card_id'] for c in decision['available_pre_roll_cards']],
                         ['C11'])
        result = self.submit_pre_roll(blocked, decision, c11=True)
        self.assertTrue(result['ok'])
        self.assertIn('YE-05', blocked.game.hand)
        self.assertIn('ME-02', blocked.game.hand)

    def test_d06_and_active_stable_resources_apply_to_single_initial_roll(self):
        session, decision = self.next_turn_session(
            stacks=[('H', ['YH-01'])], debuff='D06')
        self.assertEqual(decision['kind'], 'post_roll_decision')
        self.assertEqual(decision['stable_resources'], {'H': 1})
        self.assertEqual(decision['normal_rerolls_available'], 1)
        before = self.snapshot(session)
        again = session.current_decision()
        self.assertEqual(again, decision)
        self.assertEqual(self.snapshot(session), before)

    def test_pre_roll_accepts_flex_only_submission(self):
        session, decision = self.next_turn_session(
            stacks=[('K', ['MK-04']), ('W', ['OW-01'])])
        result = self.submit_pre_roll(
            session, decision, {'MK-04': 'R', 'OW-01': 'K'})
        self.assertTrue(result['ok'])
        self.assertEqual(result['decision']['kind'], 'post_roll_decision')
        self.assertEqual(result['decision']['stable_resources'],
                         {'K': 1, 'M': 3, 'R': 1})

    def test_pre_roll_accepts_single_boolean_submission(self):
        session, decision = self.next_turn_session(
            stacks=[('H', ['YH-02'])], hand=['C11', 'YE-05'])
        session.game._forced = ['H'] * 20
        result = session.submit_action(decision['decision_id'],
                                       {'use_c11': True})
        self.assertTrue(result['ok'])
        self.assertEqual(result['decision']['kind'], 'post_roll_decision')
        self.assertEqual(result['decision']['dice_count'], 7)
        self.assertNotIn('C11', session.game.hand)
        self.assertIn('YE-05', session.game.hand)

    def test_pre_roll_empty_action_rolls_without_optional_choices(self):
        # 持有可选能力但全部不用：空 action 即"跳过全部 pre-roll 选项"
        session, decision = self.next_turn_session(hand=['C11'])
        session.game._forced = ['H', 'K', 'R', 'M'] + ['H'] * 20
        result = session.submit_action(decision['decision_id'], {})
        self.assertTrue(result['ok'])
        self.assertEqual(result['decision']['kind'], 'post_roll_decision')
        self.assertEqual(result['decision']['dice'], ['H', 'K', 'R', 'M'])
        self.assertIn('C11', session.game.hand)

    def test_pre_roll_full_legacy_shape_still_accepted(self):
        session, decision = self.next_turn_session(
            stacks=[('H', ['YH-02'])], hand=['C11', 'YE-05'])
        session.game._forced = ['H'] * 20
        result = self.submit_pre_roll(session, decision, {}, c11=True,
                                      ye05=True, me02=False)
        self.assertTrue(result['ok'])
        self.assertEqual(result['decision']['dice_count'], 7)
        self.assertNotIn('C11', session.game.hand)
        self.assertNotIn('YE-05', session.game.hand)

    def test_pre_roll_illegal_partial_submissions_are_rejected(self):
        # 有 flex 卡时，空 action / 缺 flex 选择 → illegal_action
        session, decision = self.next_turn_session(stacks=[('K', ['MK-04'])])
        before = self.snapshot(session)
        for action in ({}, {'use_c11': True}, {'use_c11': 'yes'},
                       {'flex_resource_choices': {'MK-04': 'MONEY'}},
                       {'use_c99': True}):
            with self.subTest(action=action):
                result = session.submit_action(decision['decision_id'], action)
                self.assertFalse(result['ok'])
                self.assertIn(result['error'],
                              ('illegal_action', 'invalid_action'))
                self.assertEqual(self.snapshot(session), before)

    def turn2_purchase_session(self, dice, market, hand=()):
        session = GameSession(seed=19, shuffle=False, forced_goals=[1, 2])
        game = session.game
        game.childhood_complete = True
        game.turn = 2
        game.hand = list(hand)
        game.market = list(market)
        game.market_entry = {cid: game.turn for cid in game.market}
        session._turn_closeout_resolved = True
        session._previous_turn_result = {
            'completed_turn': 1, 'next_turn': 2,
            'maintenance_result': {'paid': []},
            'market_cleanup_result': {'purchased_card_ids': []},
        }
        game._forced = list(dice) + ['H'] * 20
        decision = session.current_decision()
        self.assertEqual(decision['kind'], 'post_roll_decision')
        self.assertEqual(decision['turn'], 2)
        ready = session.submit_action(
            decision['decision_id'],
            {'choice': 'proceed_to_purchase'})['decision']
        self.assertEqual(ready['kind'], 'purchase_ready')
        return session, ready

    def test_turn2_nonempty_plan_cannot_reenter_purchase_window(self):
        session, ready = self.turn2_purchase_session(
            ['H', 'H', 'K', 'M'], ['YH-01', 'YK-01', 'YR-01'])
        plan = self.find_plan(session, ('YH-01',))
        result = self.submit_plan(session, ready, plan)
        self.assertTrue(result['ok'])
        decision = result['decision']
        self.assertEqual(decision['kind'], 'placement_decision')
        self.assertEqual(decision['card_id'], 'YH-01')
        self.assertEqual(session.game.purchased_this_turn, ['YH-01'])
        before = self.snapshot(session)
        rejected = session.submit_action(decision['decision_id'],
                                         {'choice': 'proceed_to_purchase'})
        self.assertFalse(rejected['ok'])
        self.assertEqual(self.snapshot(session), before)

    def test_turn2_cannot_exceed_two_normal_cards_per_turn(self):
        session, ready = self.turn2_purchase_session(
            ['H', 'H', 'K', 'M'], ['YH-01', 'YK-01', 'YR-01'])
        self.assertTrue(all(len(p['card_ids']) <= 2
                            for p in session._purchase_plan_cache))
        plan = self.find_plan(session, ('YH-01', 'YK-01'))
        result = self.submit_plan(session, ready, plan)
        self.assertTrue(result['ok'])
        first = result['decision']
        self.assertEqual(first['kind'], 'placement_decision')
        second = session.submit_action(first['decision_id'],
                                       {'placement': 'top'})
        self.assertEqual(second['decision']['kind'], 'placement_decision')
        self.assertEqual(second['decision']['card_id'], 'YK-01')
        third = session.submit_action(second['decision']['decision_id'],
                                      {'placement': 'bury'})
        final = third['decision']
        self.assertEqual(final['kind'], 'post_roll_decision')
        self.assertEqual(final['turn'], 3)
        previous = final['previous_turn_result']
        self.assertEqual(previous['completed_turn'], 2)
        self.assertEqual(previous['purchase_result']['purchased_card_ids'],
                         ['YH-01', 'YK-01'])
        self.assertEqual(session.game.stats.game['purchases'], 2)
        self.assertEqual(session.game.purchased_this_turn, [])

    def test_turn2_empty_plan_ends_purchase_and_enters_followup_chain(self):
        session, ready = self.turn2_purchase_session(
            ['H', 'K', 'R', 'M'],
            ['YH-01', 'YH-02', 'YH-03', 'YK-01', 'YK-02'],
            hand=['YE-04'])
        session.game.decks = {
            'youth': ['YK-03', 'YK-04', 'YK-05'], 'middle': [], 'elder': []}
        plan = self.find_plan(session, ())
        result = self.submit_plan(session, ready, plan)
        self.assertTrue(result['ok'])
        protection = result['decision']
        self.assertEqual(protection['kind'], 'market_protection_decision')
        self.assertEqual(protection['system_elimination_count'], 3)
        rejected = session.submit_action(protection['decision_id'],
                                         {'plan_id': 'purchase_again'})
        self.assertFalse(rejected['ok'])
        done = session.submit_action(protection['decision_id'],
                                     {'choice': 'skip'})
        self.assertTrue(done['ok'])
        final = done['decision']
        self.assertEqual(final['kind'], 'post_roll_decision')
        self.assertEqual(final['turn'], 3)
        previous = final['previous_turn_result']
        self.assertEqual(previous['completed_turn'], 2)
        self.assertEqual(previous['purchase_result']['purchased_card_ids'], [])
        self.assertFalse(previous['market_cleanup_result']['ye04_used'])

    def test_turn2_new_event_cannot_fund_second_purchase_window(self):
        session, ready = self.turn2_purchase_session(
            ['M', 'M', 'BL', 'BL'], ['YE-01', 'YP-01'])
        plan = self.find_plan(session, ('YE-01',))
        result = self.submit_plan(session, ready, plan)
        self.assertTrue(result['ok'])
        final = result['decision']
        self.assertEqual(final['kind'], 'post_roll_decision')
        self.assertEqual(final['turn'], 3)
        previous = final['previous_turn_result']
        self.assertEqual(previous['completed_turn'], 2)
        self.assertEqual(previous['purchase_result']['purchased_card_ids'],
                         ['YE-01'])
        self.assertIn('YE-01', session.game.hand)
        self.assertIsNone(session.game.acquired.get('YP-01'))
        self.assertEqual(session.game.stats.game['purchases'], 1)

    def test_turn3_fate_joint_purchase_keeps_immediate_chain(self):
        session = GameSession(seed=19, shuffle=False, forced_goals=[1, 2])
        game = session.game
        game.childhood_complete = True
        game.turn = 3
        game.hand = []
        game.cv['H'] = ['YH-01', 'YH-03']
        for cid in ('YH-01', 'YH-03'):
            game.acquired[cid] = 0
        game.fate_market = ['F01', 'F07']
        session._turn_closeout_resolved = True
        session._previous_turn_result = {
            'completed_turn': 2, 'next_turn': 3,
            'maintenance_result': {'paid': []},
            'market_cleanup_result': {'purchased_card_ids': []},
        }
        game._forced = ['GL', 'BL', 'H', 'K'] + ['H'] * 20
        decision = session.current_decision()
        self.assertEqual(decision['kind'], 'post_roll_decision')
        ready = session.submit_action(
            decision['decision_id'],
            {'choice': 'proceed_to_purchase'})['decision']
        self.assertEqual(ready['kind'], 'purchase_ready')
        fate_plan = next(p for p in session._purchase_plan_cache
                         if p['ordinary_card_ids'] == []
                         and p['fate_card_id'] == 'F01')
        result = self.submit_plan(session, ready, fate_plan)
        self.assertTrue(result['ok'])
        immediate = result['decision']
        self.assertEqual(immediate['kind'], 'fate_immediate_decision')
        self.assertEqual(immediate['fate_card_id'], 'F01')
        self.assertEqual([c['id'] for c in immediate['candidate_cards']],
                         ['YH-01', 'YH-03'])
        resolved = session.submit_action(immediate['decision_id'],
                                         {'card_id': 'YH-01'})
        self.assertTrue(resolved['ok'])
        self.assertEqual(session.game.active('H'), 'YH-01')
        self.assertIsNone(session.game.pending_fate_immediate_decision())
        final = resolved['decision']
        self.assertEqual(final['kind'], 'post_roll_decision')
        self.assertEqual(final['turn'], 4)
        self.assertEqual(final['previous_turn_result']['completed_turn'], 3)

    def test_single_payment_target_executes_directly_in_one_stage(self):
        session, ready = self.ready_session(['H', 'H', 'K', 'M'],
                                            ['YH-01', 'YK-01'])
        entry = next(t for t in ready['purchase_targets']
                     if t['ordinary_card_ids'] == ['YK-01'])
        self.assertEqual(entry['payment_option_count'], 1)
        self.assertNotIn('plan_id', entry)
        self.assertNotIn('payment_options', entry)
        action = {'ordinary_card_ids': ['YK-01'], 'fate_card_id': None}
        self.assertNotIn('legal_actions', ready)
        self.assertIn((action['ordinary_card_ids'], action['fate_card_id']),
                      [(t['ordinary_card_ids'], t['fate_card_id'])
                       for t in ready['purchase_targets']])
        result = session.submit_action(ready['decision_id'], action)
        self.assertTrue(result['ok'])
        self.assertEqual(result['decision']['kind'], 'placement_decision')
        self.assertTrue(session._purchase_executed)
        self.assertIsNone(session._purchase_target)
        self.assertEqual(session.game.purchased_this_turn, ['YK-01'])

    def test_multi_payment_target_enters_second_stage_and_shows_only_that_group(self):
        session, ready = self.ready_session(
            ['H', 'H', 'H', 'BL'], ['YH-01'], extra_hand=['C07'])
        entry = next(t for t in ready['purchase_targets']
                     if t['ordinary_card_ids'] == ['YH-01'])
        self.assertEqual(entry['payment_option_count'], 2)
        self.assertNotIn('plan_id', entry)
        self.assertEqual(len(entry['payment_options']), 2)
        self.assertTrue(all({'spent_resources', 'remaining_resources'} <= set(o)
                            for o in entry['payment_options']))
        result = session.submit_action(ready['decision_id'], {
            'ordinary_card_ids': ['YH-01'], 'fate_card_id': None})
        self.assertTrue(result['ok'])
        second = result['decision']
        self.assertEqual(second['kind'], 'purchase_ready')
        self.assertEqual(second['selected_purchase_target'],
                         {'ordinary_card_ids': ['YH-01'],
                          'fate_card_id': None})
        self.assertNotIn('purchase_targets', second)
        self.assertEqual(
            [p['card_ids'] for p in second['legal_acquisition_plans']],
            [['YH-01'], ['YH-01']])
        self.assertEqual(
            {p['plan_id'] for p in second['legal_acquisition_plans']},
            {a['plan_id'] for a in second['legal_actions']})
        self.assertNotEqual(second['decision_id'], ready['decision_id'])
        again = session.current_decision()
        self.assertEqual(again, second)

    def test_purchase_two_stage_instructions_keep_latest_decision_and_plan_scope_clear(self):
        session, ready = self.ready_session(
            ['H', 'H', 'H', 'BL'], ['YH-01'], extra_hand=['C07'])
        self.assertIn('第一阶段只提交 ordinary_card_ids 和 fate_card_id',
                      ready['action_instruction'])
        self.assertIn('payment_options 仅是支付摘要', ready['action_instruction'])
        target = {'ordinary_card_ids': ['YH-01'], 'fate_card_id': None}
        selected = session.submit_action(ready['decision_id'], target)
        self.assertTrue(selected['ok'])
        second = selected['decision']
        self.assertNotEqual(second['decision_id'], ready['decision_id'])
        self.assertIn('第二阶段只从本次返回的 legal_acquisition_plans',
                      second['action_instruction'])
        self.assertIn('本阶段最新的 decision_id', second['action_instruction'])
        self.assertIn('不能复用上阶段的 decision_id 或 plan_id',
                      second['action_instruction'])
        self.assertIn('current_decision() 只读取当前状态',
                      second['action_instruction'])
        self.assertEqual(session.current_decision(), second)

    def test_first_layer_payment_options_expose_childhood_discount_source(self):
        session, ready = self.ready_session(
            ['H', 'H', 'H', 'BL'], ['YH-01'], extra_hand=['C07'])
        entry = next(t for t in ready['purchase_targets']
                     if t['ordinary_card_ids'] == ['YH-01'])
        self.assertEqual(entry['payment_option_count'], 2)
        sourced = [o for o in entry['payment_options']
                   if 'payment_sources' in o]
        self.assertEqual(len(sourced), 1)
        text = '；'.join(sourced[0]['payment_sources'])
        self.assertIn('C07', text)
        self.assertIn('YH-01', text)
        self.assertIn('折扣', text)
        # C07 是一次性购买折扣，不得被标成临时资源
        self.assertNotIn('临时资源', text)
        self.assertEqual(sourced[0]['payment_sources'],
                         ['以 C07 折扣取得 YH-01'])

    def test_first_layer_payment_options_expose_childhood_temp_resource(self):
        # C01（临时 M×1）作为购买资源来源时，显示真实临时资源贡献
        session, ready = self.ready_session(
            ['K', 'K', 'K', 'M'], ['YK-02'], extra_hand=['C01'])
        entry = next(t for t in ready['purchase_targets']
                     if t['ordinary_card_ids'] == ['YK-02'])
        sourced = [o for o in entry['payment_options']
                   if 'payment_sources' in o]
        self.assertEqual(len(sourced), 1)
        self.assertEqual(sourced[0]['payment_sources'],
                         ['消耗童年牌 C01（临时 M×1）'])
        plain = next(o for o in entry['payment_options']
                     if 'payment_sources' not in o)
        self.assertEqual(plain['spent_resources'], {'K': 1, 'M': 1})

    def test_first_layer_payment_options_expose_event_temp_resource_source(self):
        session, ready = self.ready_session(
            ['M', 'K', 'K'], ['YP-01'], extra_hand=['YE-01'], stable={'M': 1})
        entry = next(t for t in ready['purchase_targets']
                     if t['ordinary_card_ids'] == ['YP-01'])
        self.assertEqual(entry['payment_option_count'], 3)
        sourced = [o for o in entry['payment_options']
                   if 'payment_sources' in o]
        self.assertEqual(len(sourced), 2)
        for opt in sourced:
            self.assertTrue(any('YE-01' in s for s in opt['payment_sources']))
        plain = next(o for o in entry['payment_options']
                     if o['spent_resources'] == {'M': 2})
        self.assertNotIn('payment_sources', plain)

    def test_first_layer_normal_full_payment_has_no_source_field(self):
        # 默认童年牌含 C07（H 折扣），选 W 类市场卡避免折扣适用。
        session, ready = self.ready_session(['K', 'K', 'M', 'M'], ['YW-01'])
        for target in ready['purchase_targets']:
            self.assertNotIn('payment_sources', target)
            if target.get('payment_method') == 'normal':
                self.assertNotIn('payment_options', target)
                if target['ordinary_card_ids']:
                    self.assertTrue(
                        target['payment_summary'].startswith('支付'))

    def test_payment_method_classifies_childhood_discount(self):
        # C07 折扣是机制分类，不是临时资源；骰子仅 1H 使折扣成为唯一方案
        session, ready = self.ready_session(
            ['H', 'K', 'R', 'M'], ['YH-01'], extra_hand=['C07'])
        entry = next(t for t in ready['purchase_targets']
                     if t['ordinary_card_ids'] == ['YH-01'])
        self.assertEqual(entry['payment_option_count'], 1)
        self.assertEqual(entry['payment_method'], 'childhood_discount')
        self.assertEqual(entry['payment_summary'],
                         '以 C07 折扣取得 YH-01；支付 H×1')

    def test_payment_method_classifies_childhood_temp_resource(self):
        # C01（临时 M×1）补足 YP-01 的 M×2：唯一方案按机制归临时资源
        session, ready = self.ready_session(
            ['M', 'K', 'R', 'BL'], ['YP-01'], extra_hand=['C01'])
        entry = next(t for t in ready['purchase_targets']
                     if t['ordinary_card_ids'] == ['YP-01'])
        self.assertEqual(entry['payment_option_count'], 1)
        self.assertEqual(entry['payment_method'], 'childhood_temp_resource')
        self.assertIn('消耗童年牌 C01（临时 M×1）', entry['payment_summary'])

    def test_payment_method_classifies_event_temp_resource(self):
        # YE-01（临时 M×2）单独凑足 YP-01 的 M×2：归事件临时资源
        session, ready = self.ready_session(
            ['K', 'R', 'BL', 'BL'], ['YP-01'], extra_hand=['YE-01'])
        entry = next(t for t in ready['purchase_targets']
                     if t['ordinary_card_ids'] == ['YP-01'])
        self.assertEqual(entry['payment_option_count'], 1)
        self.assertEqual(entry['payment_method'], 'event_temp_resource')
        self.assertIn('消耗事件 YE-01 的临时 M×2', entry['payment_summary'])

    def test_payment_method_normal_stays_normal(self):
        # 无折扣适用（W 类）且骰子足够：普通支付不漂移
        session, ready = self.ready_session(['K', 'K', 'M', 'M'], ['YW-01'])
        entry = next(t for t in ready['purchase_targets']
                     if t['ordinary_card_ids'] == ['YW-01'])
        self.assertEqual(entry['payment_method'], 'normal')
        self.assertEqual(entry['payment_summary'], '支付 K×2')

    def test_second_layer_plan_source_fields_unchanged(self):
        session, ready = self.ready_session(
            ['H', 'H', 'H', 'BL'], ['YH-01'], extra_hand=['C07'])
        result = session.submit_action(ready['decision_id'], {
            'ordinary_card_ids': ['YH-01'], 'fate_card_id': None})
        second = result['decision']
        self.assertEqual(second['kind'], 'purchase_ready')
        plans = second['legal_acquisition_plans']
        self.assertEqual(len(plans), 2)
        discounted = next(p for p in plans if p['discounts_used'])
        self.assertEqual(discounted['discounts_used'],
                         [{'childhood_card_id': 'C07', 'card_id': 'YH-01',
                           'symbol': 'H'}])
        self.assertIn('spent_resources', discounted)
        self.assertIn('remaining_resources', discounted)
        self.assertIn('plan_id', discounted)

    def test_unpublished_plan_id_and_illegal_target_are_rejected_without_side_effects(self):
        session, ready = self.ready_session(
            ['H', 'H', 'H', 'BL'], ['YH-01'], extra_hand=['C07'])
        multi = self.find_plan(session, ('YH-01',),
                               lambda p: bool(p['discounts_used']))
        before = self.snapshot(session)
        single = self.find_plan(session, ('YH-01',),
                                lambda p: not p['discounts_used'])
        for action in ({'plan_id': multi['plan_id']},
                       {'plan_id': single['plan_id']},
                       {'plan_id': 'not-a-plan'},
                       {'ordinary_card_ids': ['YH-99'], 'fate_card_id': None},
                       {'ordinary_card_ids': 'YH-01', 'fate_card_id': None},
                       {'ordinary_card_ids': ['YH-01'], 'fate_card_id': 'F01'}):
            with self.subTest(action=action):
                result = session.submit_action(ready['decision_id'], action)
                self.assertFalse(result['ok'])
                self.assertIn(result['error'],
                              ('illegal_plan_id', 'illegal_action'))
                self.assertEqual(self.snapshot(session), before)

    def test_second_stage_can_switch_to_another_published_target(self):
        session, ready = self.ready_session(
            ['H', 'H', 'H', 'M'], ['YH-01', 'YK-01'], extra_hand=['C07'],
            stable={'K': 1})
        yh_plan = self.find_plan(session, ('YH-01',))
        first = session.submit_action(ready['decision_id'], {
            'ordinary_card_ids': ['YH-01'], 'fate_card_id': None})
        self.assertTrue(first['ok'])
        self.assertIn('selected_purchase_target', first['decision'])
        switched = session.submit_action(first['decision']['decision_id'], {
            'ordinary_card_ids': ['YK-01'], 'fate_card_id': None})
        self.assertTrue(switched['ok'])
        self.assertEqual(switched['decision']['kind'], 'placement_decision')
        self.assertIsNone(session._purchase_target)
        self.assertEqual(session.game.purchased_this_turn, ['YK-01'])
        stale = session.submit_action(first['decision']['decision_id'],
                                      {'plan_id': yh_plan['plan_id']})
        self.assertFalse(stale['ok'])
        self.assertEqual(stale['error'], 'stale_or_unknown_decision_id')

    def test_second_stage_plan_submission_consumes_window_once(self):
        session, ready = self.ready_session(
            ['H', 'H', 'H', 'BL'], ['YH-01'], extra_hand=['C07'])
        second = session.submit_action(ready['decision_id'], {
            'ordinary_card_ids': ['YH-01'], 'fate_card_id': None})['decision']
        plan = self.find_plan(session, ('YH-01',),
                              lambda p: bool(p['discounts_used']))
        result = session.submit_action(second['decision_id'],
                                       {'plan_id': plan['plan_id']})
        self.assertTrue(result['ok'])
        self.assertTrue(session._purchase_executed)
        self.assertIsNone(session._purchase_target)
        self.assertNotIn('C07', session.game.hand)
        self.assertEqual(session.game.purchased_this_turn, ['YH-01'])
        duplicate = session.submit_action(second['decision_id'],
                                          {'plan_id': plan['plan_id']})
        self.assertFalse(duplicate['ok'])
        self.assertEqual(duplicate['error'], 'stale_or_unknown_decision_id')


class TestUnifiedPostRollController(unittest.TestCase):
    def _session_at_post_roll(self, dice=('H', 'K', 'R', 'M')):
        session = GameSession(seed=1, shuffle=False, forced_goals=[1, 2])
        first = session.current_decision()
        second = session.submit_action(first['decision_id'],
                                       {'card_id': 'C12'})['decision']
        session.game._forced = list(dice) + ['H'] * 20
        return session, session.submit_action(second['decision_id'],
                                              {'card_id': 'C09'})['decision']

    def test_ye03_extends_only_first_reroll_window(self):
        session, decision = self._session_at_post_roll()
        session.game.hand.append('YE-03')
        decision = session.current_decision()
        self.assertTrue(decision['ye03_available'])
        result = session.submit_action(decision['decision_id'], {
            'choice': 'normal_reroll', 'indices': [0], 'use_ye03': True,
            'use_c11': False, 'c12_index': None})
        self.assertTrue(result['ok'])
        self.assertEqual(result['decision']['normal_rerolls_remaining'], 2)
        self.assertNotIn('YE-03', session.game.hand)
        self.assertFalse(result['decision']['ye03_available'])

    def test_special_reroll_keeps_normal_budget_and_stale_id_is_rejected(self):
        session, decision = self._session_at_post_roll()
        session.game.cv['K'] = ['MK-02']
        decision = session.current_decision()
        old_id = decision['decision_id']
        result = session.submit_action(old_id, {
            'choice': 'special_reroll', 'ability_card_id': 'MK-02',
            'die_index': 0, 'use_ye03': False})
        self.assertTrue(result['ok'])
        self.assertEqual(result['decision']['normal_rerolls_remaining'], 2)
        duplicate = session.submit_action(old_id, {
            'choice': 'special_reroll', 'ability_card_id': 'MK-02',
            'die_index': 0, 'use_ye03': False})
        self.assertFalse(duplicate['ok'])
        self.assertEqual(duplicate['error'], 'stale_or_unknown_decision_id')

    def test_mh04_reaction_precedes_post_roll_and_can_skip(self):
        session, _ = self._session_at_post_roll(('BL', 'BL', 'H', 'K'))
        session.game.cv['H'] = ['MH-04']
        session.game.mh04_trigger_seen = False
        session.game._after_dice_change()
        decision = session.current_decision()
        self.assertEqual(decision['kind'], 'mh04_decision')
        result = session.submit_action(decision['decision_id'], {'choice': 'skip'})
        self.assertTrue(result['ok'])
        self.assertEqual(result['decision']['kind'], 'post_roll_decision')
        self.assertEqual(session.game.dice.count('BL'), 2)

    def test_ye03_market_view_uses_unambiguous_reroll_round_field(self):
        session, decision = self._session_at_post_roll()
        session.game.market.append('YE-03')
        decision = session.current_decision()
        entry = next(o for o in decision['current_opportunities']
                     if o['id'] == 'YE-03')
        self.assertEqual(entry['extra_reroll_rounds'], 1)
        self.assertNotIn('extra_round', entry)

    def test_ye03_post_roll_ability_entry_states_reroll_round_not_turn(self):
        session, decision = self._session_at_post_roll()
        session.game.hand.append('YE-03')
        decision = session.current_decision()
        self.assertTrue(decision['ye03_available'])
        ability = next(a for a in decision['available_abilities']
                       if a['card_id'] == 'YE-03')
        self.assertEqual(ability['kind'], 'extra_normal_reroll_round')
        self.assertEqual(ability['extra_reroll_rounds'], 1)
        self.assertIn('正常重掷', ability['note'])
        self.assertIn('不是额外游戏回合', ability['note'])
        result = session.submit_action(decision['decision_id'], {
            'choice': 'normal_reroll', 'indices': [0], 'use_ye03': True,
            'use_c11': False, 'c12_index': None})
        self.assertTrue(result['ok'])
        self.assertEqual(result['decision']['normal_rerolls_remaining'], 2)
        self.assertFalse(result['decision']['ye03_available'])
        self.assertTrue(all(a['card_id'] != 'YE-03'
                            for a in result['decision']['available_abilities']))

class TestRuntimeFatePreview(unittest.TestCase):
    def _session_at_fate_post_roll(self):
        session = GameSession(seed=1, shuffle=False, forced_goals=[1, 2])
        first = session.current_decision()
        second = session.submit_action(first['decision_id'], {
            'card_id': 'C12'})['decision']
        session.game._forced = ['GL', 'BL', 'H', 'K']
        session.submit_action(second['decision_id'], {'card_id': 'C09'})
        session.game.turn = 3
        session.game.fate_market = ['F01', 'F07']
        return session

    def test_runtime_uses_engine_fate_window_schedule(self):
        session = self._session_at_fate_post_roll()
        for turn in range(1, 22):
            session.game.turn = turn
            self.assertEqual(session.game.fate_window(), turn % 3 == 0)

    def test_non_fate_window_hides_fate_market_and_keeps_normal_plans(self):
        session = GameSession(seed=1, shuffle=False, forced_goals=[1, 2])
        first = session.current_decision()
        second = session.submit_action(first['decision_id'], {
            'card_id': 'C12'})['decision']
        session.game._forced = ['H', 'K', 'R', 'M']
        decision = session.submit_action(second['decision_id'], {
            'card_id': 'C09'})['decision']
        self.assertFalse(session.game.fate_window())
        self.assertTrue(all(card['type'] != 'F'
                            for card in decision['current_opportunities']))
        self.assertTrue(all('card_ids' in plan
                            for plan in decision['legal_acquisition_plans']))

    def test_fate_window_exposes_unified_market_and_joint_preview(self):
        session = self._session_at_fate_post_roll()
        before = copy.deepcopy((session.game.market, session.game.fate_market,
                                session.game.hand, session.game.rng.getstate()))
        decision = session.current_decision()
        after = copy.deepcopy((session.game.market, session.game.fate_market,
                               session.game.hand, session.game.rng.getstate()))
        self.assertTrue(session.game.fate_window())
        self.assertEqual([card['id'] for card in decision['current_opportunities']],
                         session.game.market + ['F01', 'F07'])
        self.assertEqual({card['type'] for card in decision['current_opportunities']
                          if card['id'] in ('F01', 'F07')}, {'F'})
        self.assertTrue(any(plan['fate_card_id'] == 'F01'
                            for plan in decision['legal_acquisition_plans']))
        self.assertTrue(all(set(plan) == {'ordinary_card_ids', 'fate_card_id'}
                            for plan in decision['legal_acquisition_plans']))
        self.assertEqual(before, after)

    def test_fate_window_purchase_ready_returns_shared_joint_target_groups(self):
        session = self._session_at_fate_post_roll()
        session._enter_purchase_ready()
        decision = session.current_decision()
        self.assertEqual(decision['kind'], 'purchase_ready')
        self.assertIn('purchase_targets', decision)
        self.assertNotIn('legal_acquisition_plans', decision)
        joint = session.game.enumerate_joint_purchase_plans(
            pool_override=session.game.post_roll_resources())
        groups = {}
        for plan in joint:
            groups.setdefault((tuple(plan['ordinary_card_ids']),
                               plan['fate_card_id']), []).append(plan)
        self.assertEqual(
            {(tuple(t['ordinary_card_ids']),
              t['fate_card_id']): t['payment_option_count']
             for t in decision['purchase_targets']},
            {key: len(plans) for key, plans in groups.items()})
        self.assertTrue(all('plan_id' not in t
                            for t in decision['purchase_targets']))
        self.assertTrue(any(t['fate_card_id'] == 'F01'
                            for t in decision['purchase_targets']))

    def test_fate_joint_plan_pauses_for_f01_and_resolves_through_engine(self):
        session = self._session_at_fate_post_roll()
        session.game.cv['H'] = ['YH-01', 'YH-03']
        session._enter_purchase_ready()
        decision = session.current_decision()
        fate_plan = next(plan for plan in session._purchase_plan_cache
                         if (plan['ordinary_card_ids'] == []
                             and plan['fate_card_id'] == 'F01'))
        before = copy.deepcopy((session.game.pool, session.game.hand,
                                session.game.market, session.game.fate_market,
                                session.game.fate_stack, session.game.rng.getstate()))
        invalid = session.submit_action(decision['decision_id'], {
            'plan_id': 'not-a-current-plan'})
        self.assertFalse(invalid['ok'])
        self.assertEqual(invalid['error'], 'illegal_plan_id')
        self.assertEqual(before, (session.game.pool, session.game.hand,
                                  session.game.market, session.game.fate_market,
                                  session.game.fate_stack, session.game.rng.getstate()))
        result = session.submit_action(decision['decision_id'], {
            'ordinary_card_ids': [], 'fate_card_id': 'F01'})
        self.assertTrue(result['ok'])
        immediate = result['decision']
        self.assertEqual(immediate['kind'], 'fate_immediate_decision')
        self.assertEqual(immediate['fate_card_id'], 'F01')
        self.assertEqual([card['id'] for card in immediate['candidate_cards']],
                         ['YH-01', 'YH-03'])
        self.assertEqual(session.game.fate_stack, ['F01'])
        self.assertFalse(session._debuff_resolved)

        before_invalid = copy.deepcopy((session.game.cv, session.game.pool,
                                        session.game.fate_market,
                                        session.game.rng.getstate()))
        invalid = session.submit_action(immediate['decision_id'], {
            'card_id': 'YK-01'})
        self.assertFalse(invalid['ok'])
        self.assertEqual(before_invalid, (session.game.cv, session.game.pool,
                                          session.game.fate_market,
                                          session.game.rng.getstate()))
        resolved = session.submit_action(immediate['decision_id'], {
            'card_id': 'YH-01'})
        self.assertTrue(resolved['ok'])
        self.assertEqual(session.game.active('H'), 'YH-01')
        self.assertIsNone(session.game.pending_fate_immediate_decision())
        stale = session.submit_action(immediate['decision_id'], {
            'card_id': 'YH-03'})
        self.assertFalse(stale['ok'])
        self.assertEqual(stale['error'], 'stale_or_unknown_decision_id')

    def test_f03_runtime_candidates_are_stable_until_keep_is_submitted(self):
        session = self._session_at_fate_post_roll()
        session.game.dice = ['GL', 'GL']
        session.game.stable_pool = Counter()
        session.game.fate_market = ['F03', 'F07']
        session._enter_purchase_ready()
        purchase = session.current_decision()
        result = session.submit_action(purchase['decision_id'], {
            'ordinary_card_ids': [], 'fate_card_id': 'F03'})
        self.assertTrue(result['ok'])
        first = result['decision']
        state_after_sample = session.game.rng.getstate()
        second = session.current_decision()
        self.assertEqual(first, second)
        self.assertEqual(first['kind'], 'fate_immediate_decision')
        self.assertEqual(first['fate_card_id'], 'F03')
        self.assertEqual(len(first['candidate_goal_ids']), 3)
        self.assertEqual(session.game.rng.getstate(), state_after_sample)
        resolved = session.submit_action(first['decision_id'], {'choice': 'keep'})
        self.assertTrue(resolved['ok'])
        self.assertIsNone(session.game.pending_fate_immediate_decision())

    def test_fate_without_immediate_continues_into_purchase_follow_up_chain(self):
        session = self._session_at_fate_post_roll()
        session.game.fate_market = ['F07', 'F01']
        session._enter_purchase_ready()
        decision = session.current_decision()
        result = session.submit_action(decision['decision_id'], {
            'ordinary_card_ids': [], 'fate_card_id': 'F07'})
        self.assertTrue(result['ok'])
        self.assertEqual(session.game.fate_stack, ['F07'])
        self.assertIsNone(session.game.pending_fate_immediate_decision())
        self.assertIn(result['decision']['kind'],
                      ('debuff_protection_decision', 'placement_decision',
                       'maintenance_decision', 'market_protection_decision',
                       'market_cleanup_resolved', 'next_turn_ready',
                       'pre_roll_decision', 'post_roll_decision'))


class TestRuntimeLongTermView(unittest.TestCase):
    def drafted_session(self, dice, market=None, extra_hand=()):
        session = GameSession(seed=1, shuffle=False, forced_goals=[1, 2])
        first = session.current_decision()
        second = session.submit_action(first['decision_id'],
                                       {'card_id': 'C12'})['decision']
        session.game._forced = list(dice)
        done = session.submit_action(second['decision_id'],
                                     {'card_id': 'C09'})['decision']
        if market is not None:
            session.game.market = list(market)
            session.game.market_entry = {
                cid: session.game.turn for cid in session.game.market}
        for cid in extra_hand:
            if cid not in session.game.hand:
                session.game.hand.append(cid)
        return session, done

    def childhood_status(self, decision):
        return {c['card_id']: c['status'] for c in decision['childhood_cards']}

    def test_first_childhood_decision_exposes_life_goals_with_rules(self):
        session = GameSession(seed=1, shuffle=False, forced_goals=[1, 2])
        decision = session.current_decision()
        self.assertEqual(decision['kind'], 'childhood_pick_1')
        # Draft 阶段为目标首次公开：scoring_rule 之外附同源口径句
        self.assertEqual(decision['life_goals'], [
            {'id': 1, 'name': LG_NAMES[1],
             'scoring_rule': dict(LG_RULES[1]),
             'scoring_text': lg_scoring_text(LG_RULES[1])},
            {'id': 2, 'name': LG_NAMES[2],
             'scoring_rule': dict(LG_RULES[2]),
             'scoring_text': lg_scoring_text(LG_RULES[2])},
        ])
        self.assertEqual(decision['life_goals'][0]['scoring_rule'],
                         {'kind': 'cv_count', 'class': 'H'})
        self.assertIn('全部张数', decision['life_goals'][0]['scoring_text'])
        self.assertIn('含被顶替/埋藏', decision['life_goals'][0]['scoring_text'])

    def test_second_childhood_pick_still_exposes_life_goals(self):
        session = GameSession(seed=1, shuffle=False, forced_goals=[1, 2])
        first = session.current_decision()
        second = session.submit_action(first['decision_id'],
                                       {'card_id': 'C12'})['decision']
        self.assertEqual(second['kind'], 'childhood_pick_2')
        self.assertEqual([g['id'] for g in second['life_goals']], [1, 2])
        self.assertEqual([g['scoring_rule']['kind']
                          for g in second['life_goals']],
                         ['cv_count', 'cv_count'])

    def test_draft_completion_exposes_all_three_childhood_cards(self):
        session, done = self.drafted_session(['H', 'K', 'R', 'M'])
        self.assertEqual(done['kind'], 'post_roll_decision')

        def entry(cid, status):
            item = {'card_id': cid, 'name': CARDS[cid]['name'],
                    'status': status}
            effect = (_SPECIAL_NOTES.get(cid)
                      or _card_effect_summary(CARDS[cid]))
            if effect:
                item['effect_summary'] = effect
            return item

        self.assertEqual(done['childhood_cards'], [
            entry('C12', 'held'),
            entry('C09', 'available'),
            entry('C07', 'available'),
        ])
        self.assertNotIn('childhood_draft_result', done)
        self.assertEqual([g['id'] for g in done['life_goals']], [1, 2])

    def test_spectator_snapshot_keeps_childhood_history_and_draft_events(self):
        session, _ = self.drafted_session(['H', 'K', 'R', 'M'])
        snapshot = session.spectator_snapshot()
        self.assertEqual([item['card_id'] for item in snapshot['childhood_cards']],
                         session.game.childhood_kept)
        draft_events = [event for event in session._recent_events
                        if event['type'].startswith('childhood_')]
        self.assertEqual([event['type'] for event in draft_events],
                         ['childhood_pick', 'childhood_pick',
                          'childhood_draft_completed'])
        self.assertEqual(draft_events[-1]['details']['card_ids'],
                         session.game.childhood_kept)

    def test_spectator_childhood_history_marks_consumed_card_used(self):
        session, post = self.drafted_session(
            ['H', 'H', 'BL', 'BL'], market=['YH-01', 'YK-01'],
            extra_hand=['C07'])
        ready = session.submit_action(
            post['decision_id'], {'choice': 'proceed_to_purchase'})['decision']
        plan = next(p for p in session._purchase_plan_cache
                    if p['card_ids'] == ['YH-01'] and p['discounts_used'])
        stage = session.submit_action(ready['decision_id'], {
            'ordinary_card_ids': ['YH-01'], 'fate_card_id': None})
        result = session.submit_action(stage['decision']['decision_id'],
                                       {'plan_id': plan['plan_id']})
        final = session.submit_action(result['decision']['decision_id'],
                                      {'placement': 'top'})
        history = {item['card_id']: item['status'] for item in
                   session.spectator_snapshot()['childhood_cards']}
        self.assertEqual(history['C07'], 'used')
        self.assertEqual(set(history), set(session.game.childhood_kept))

    def test_childhood_card_status_tracks_consumption(self):
        session, post = self.drafted_session(
            ['H', 'H', 'BL', 'BL'], market=['YH-01', 'YK-01'],
            extra_hand=['C07'])
        ready = session.submit_action(
            post['decision_id'], {'choice': 'proceed_to_purchase'})['decision']
        plan = next(p for p in session._purchase_plan_cache
                    if p['card_ids'] == ['YH-01'] and p['discounts_used'])
        stage = session.submit_action(ready['decision_id'], {
            'ordinary_card_ids': ['YH-01'], 'fate_card_id': None})
        self.assertTrue(stage['ok'])
        self.assertIn('selected_purchase_target', stage['decision'])
        result = session.submit_action(stage['decision']['decision_id'],
                                       {'plan_id': plan['plan_id']})
        self.assertTrue(result['ok'])
        placement = result['decision']
        self.assertEqual(placement['kind'], 'placement_decision')
        final = session.submit_action(placement['decision_id'],
                                      {'placement': 'top'})
        self.assertEqual(final['decision']['kind'], 'post_roll_decision')
        # Payload Slim v1：已消耗的 C07 不再重复展示
        self.assertEqual(self.childhood_status(final['decision']),
                         {'C12': 'held', 'C09': 'available'})

    def test_abebe_status_reflects_held_and_used(self):
        session, post = self.drafted_session(['BL', 'BL', 'H', 'K'])
        self.assertEqual(self.childhood_status(post),
                         {'C12': 'held', 'C09': 'available',
                          'C07': 'available'})
        rerolled = session.submit_action(
            post['decision_id'],
            {'choice': 'reroll', 'indices': [0, 2], 'c12_index': 0})
        self.assertTrue(rerolled['ok'])
        self.assertTrue(session.game.abebe_used)
        self.assertEqual(self.childhood_status(rerolled['decision']),
                         {'C12': 'used', 'C09': 'available',
                          'C07': 'available'})

    def test_adult_key_decisions_keep_life_goals_visible(self):
        session, post = self.drafted_session(['H', 'K', 'R', 'M'])
        ready = session.submit_action(
            post['decision_id'], {'choice': 'proceed_to_purchase'})['decision']
        self.assertEqual(ready['kind'], 'purchase_ready')
        self.assertEqual([g['id'] for g in ready['life_goals']], [1, 2])
        self.assertTrue(all('scoring_rule' in g for g in ready['life_goals']))
        self.assertEqual(self.childhood_status(ready),
                         {'C12': 'held', 'C09': 'available',
                          'C07': 'available'})

        session2 = GameSession(seed=19, shuffle=False, forced_goals=[1, 2])
        game = session2.game
        game.childhood_complete = True
        game.turn = 2
        game.hand = []
        game.cv['K'] = ['MK-04']
        game.acquired['MK-04'] = 0
        session2._turn_closeout_resolved = True
        session2._previous_turn_result = {
            'completed_turn': 1, 'next_turn': 2,
            'maintenance_result': {'paid': []},
            'market_cleanup_result': {'purchased_card_ids': []},
        }
        pre = session2.current_decision()
        self.assertEqual(pre['kind'], 'pre_roll_decision')
        self.assertEqual([g['id'] for g in pre['life_goals']], [1, 2])
        self.assertEqual(pre['childhood_cards'], [])

    def test_f03_goal_replacement_updates_long_term_view(self):
        session = GameSession(seed=19, shuffle=False, forced_goals=[1, 2])
        game = session.game
        game.childhood_complete = True
        game.turn = 3
        game.hand = []
        game.fate_market = ['F03', 'F07']
        session._turn_closeout_resolved = True
        session._previous_turn_result = {
            'completed_turn': 2, 'next_turn': 3,
            'maintenance_result': {'paid': []},
            'market_cleanup_result': {'purchased_card_ids': []},
        }
        game._forced = ['GL', 'GL', 'H', 'K'] + ['H'] * 20
        decision = session.current_decision()
        self.assertEqual(decision['kind'], 'post_roll_decision')
        ready = session.submit_action(
            decision['decision_id'],
            {'choice': 'proceed_to_purchase'})['decision']
        result = session.submit_action(ready['decision_id'], {
            'ordinary_card_ids': [], 'fate_card_id': 'F03'})
        self.assertTrue(result['ok'])
        immediate = result['decision']
        self.assertEqual(immediate['kind'], 'fate_immediate_decision')
        self.assertEqual(immediate['immediate_kind'], 'fate_replace_goal')
        new_goal = immediate['candidate_goal_ids'][0]
        self.assertNotIn(new_goal, [1, 2])
        self.assertEqual(immediate['current_goal_ids'], [1, 2])
        self.assertEqual([g['id'] for g in immediate['life_goals']], [1, 2])
        self.assertTrue(all('scoring_rule' in g
                            for g in immediate['life_goals']))
        self.assertEqual(immediate['childhood_cards'], [])
        resolved = session.submit_action(immediate['decision_id'], {
            'choice': 'replace', 'goal_index': 0, 'new_goal_id': new_goal})
        self.assertTrue(resolved['ok'])
        self.assertEqual(session.game.goals, [new_goal, 2])
        final = resolved['decision']
        self.assertEqual(final['kind'], 'post_roll_decision')
        self.assertEqual([g['id'] for g in final['life_goals']],
                         [new_goal, 2])

    def test_debuff_protection_decision_exposes_long_term_view(self):
        session, post = self.drafted_session(
            ['H', 'H', 'BL', 'BL'], market=['YH-01'], extra_hand=['C06'])
        session.game.stable_pool = Counter({'BL': 1})
        session.game.bad_luck_accumulator = 3
        ready = session.submit_action(
            post['decision_id'], {'choice': 'proceed_to_purchase'})['decision']
        plan = next(p for p in session._purchase_plan_cache
                    if p['card_ids'] == ['YH-01']
                    and not p['discounts_used']
                    and not p['consumed_childhood_card_ids'])
        stage = session.submit_action(ready['decision_id'], {
            'ordinary_card_ids': ['YH-01'], 'fate_card_id': None})
        self.assertTrue(stage['ok'])
        if 'selected_purchase_target' in stage['decision']:
            protection = session.submit_action(
                stage['decision']['decision_id'],
                {'plan_id': plan['plan_id']})['decision']
        else:
            protection = stage['decision']
        self.assertEqual(protection['kind'], 'debuff_protection_decision')
        self.assertEqual([g['id'] for g in protection['life_goals']], [1, 2])
        self.assertTrue(all('scoring_rule' in g
                            for g in protection['life_goals']))
        self.assertEqual(self.childhood_status(protection),
                         {'C12': 'held', 'C09': 'available',
                          'C07': 'available'})

    def test_placement_decision_exposes_long_term_view(self):
        session, post = self.drafted_session(
            ['H', 'H', 'K', 'M'], market=['YH-01', 'YK-01'])
        ready = session.submit_action(
            post['decision_id'], {'choice': 'proceed_to_purchase'})['decision']
        plan = next(p for p in session._purchase_plan_cache
                    if p['card_ids'] == ['YH-01']
                    and not p['discounts_used']
                    and not p['consumed_childhood_card_ids'])
        stage = session.submit_action(ready['decision_id'], {
            'ordinary_card_ids': ['YH-01'], 'fate_card_id': None})
        self.assertTrue(stage['ok'])
        if 'selected_purchase_target' in stage['decision']:
            placement = session.submit_action(
                stage['decision']['decision_id'],
                {'plan_id': plan['plan_id']})['decision']
        else:
            placement = stage['decision']
        self.assertEqual(placement['kind'], 'placement_decision')
        self.assertEqual([g['id'] for g in placement['life_goals']], [1, 2])
        self.assertTrue(all('scoring_rule' in g
                            for g in placement['life_goals']))
        self.assertEqual(self.childhood_status(placement),
                         {'C12': 'held', 'C09': 'available',
                          'C07': 'available'})

    def test_maintenance_decision_exposes_long_term_view(self):
        session, post = self.drafted_session(['H', 'K', 'R', 'GL'])
        for cls, cards in (('H', ['OH-05']), ('R', ['MR-01']),
                           ('W', ['YW-02'])):
            session.game.cv[cls] = list(cards)
            for cid in cards:
                session.game.acquired[cid] = 0
        ready = session.submit_action(
            post['decision_id'], {'choice': 'proceed_to_purchase'})['decision']
        stage = session.submit_action(ready['decision_id'], {
            'ordinary_card_ids': [], 'fate_card_id': None})
        self.assertTrue(stage['ok'])
        decision = stage['decision']
        self.assertEqual(decision['kind'], 'maintenance_decision')
        self.assertEqual([g['id'] for g in decision['life_goals']], [1, 2])
        self.assertTrue(all('scoring_rule' in g
                            for g in decision['life_goals']))
        self.assertEqual(self.childhood_status(decision),
                         {'C12': 'held', 'C09': 'available',
                          'C07': 'available'})

    def test_market_protection_decision_exposes_long_term_view(self):
        session, post = self.drafted_session(
            ['H', 'K', 'R', 'M'],
            market=['YH-01', 'YH-02', 'YH-03', 'YK-01', 'YK-02'],
            extra_hand=['YE-04'])
        session.game.decks = {
            'youth': ['YK-03', 'YK-04', 'YK-05'], 'middle': [], 'elder': []}
        ready = session.submit_action(
            post['decision_id'], {'choice': 'proceed_to_purchase'})['decision']
        stage = session.submit_action(ready['decision_id'], {
            'ordinary_card_ids': [], 'fate_card_id': None})
        self.assertTrue(stage['ok'])
        decision = stage['decision']
        self.assertEqual(decision['kind'], 'market_protection_decision')
        self.assertEqual([g['id'] for g in decision['life_goals']], [1, 2])
        self.assertTrue(all('scoring_rule' in g
                            for g in decision['life_goals']))
        self.assertEqual(self.childhood_status(decision),
                         {'C12': 'held', 'C09': 'available',
                          'C07': 'available'})

    def test_lg_score_results_unchanged_by_shared_rule_source(self):
        self.assertEqual(set(LG_RULES), set(range(1, 19)))
        context = {
            'counts': {'H': 3, 'K': 2, 'R': 1, 'W': 2, 'P': 2}, 'pvp': 7,
            'provides': [('H', 2), ('R', 1), ('GL', 1), ('M', 1)],
            'fate_count': 3, 'debuff_count': 1, 'event_count': 2,
        }
        self.assertEqual([lg_score(lg, **context) for lg in range(1, 19)],
                         [3, 2, 1, 4, 2, 4, 4, 4, 4, 8, 3, 2, 6, 4, 2, 2, 3, 4])
        zero = {
            'counts': {'H': 0, 'K': 0, 'R': 0, 'W': 0, 'P': 0}, 'pvp': 0,
            'provides': [], 'fate_count': 1, 'debuff_count': 0,
            'event_count': 5,
        }
        self.assertEqual([lg_score(lg, **zero) for lg in range(1, 19)],
                         [0] * 18)
        with self.assertRaises(ValueError):
            lg_score(99, **context)


class TestChildhoodDraftPhaseView(unittest.TestCase):
    def test_draft_decisions_carry_phase_round_and_action_markers(self):
        session = GameSession(seed=20260913)
        d1 = session.current_decision()
        self.assertEqual(d1['kind'], 'childhood_pick_1')
        self.assertEqual(d1['phase'], 'childhood_draft')
        self.assertEqual(d1['draft_round'], 1)
        self.assertEqual([c['id'] for c in d1['candidates']],
                         ['C12', 'C10', 'C11'])
        self.assertIn('3 张候选', d1['draft_action'])
        self.assertIn('life_goals', d1)
        self.assertNotIn('childhood_draft_result', d1)
        self.assertNotIn('childhood_cards', d1)

        result = session.submit_action(d1['decision_id'], {'card_id': 'C12'})
        d2 = result['decision']
        self.assertEqual(d2['kind'], 'childhood_pick_2')
        self.assertEqual(d2['phase'], 'childhood_draft')
        self.assertEqual(d2['draft_round'], 2)
        self.assertEqual([c['id'] for c in d2['candidates']],
                         ['C06', 'C07'])
        self.assertTrue(set(c['id'] for c in d2['candidates']).isdisjoint(
            c['id'] for c in d1['candidates']))
        self.assertIn('重新抽取的 2 张', d2['draft_action'])
        self.assertNotIn('childhood_draft_result', d2)

    def test_first_adult_decision_keeps_childhood_effects_readable(self):
        session = GameSession(seed=20260913)
        d1 = session.current_decision()
        session.submit_action(d1['decision_id'], {'card_id': 'C12'})
        d2 = session.current_decision()
        session.submit_action(d2['decision_id'], {'card_id': 'C06'})
        d3 = session.current_decision()
        self.assertEqual(d3['kind'], 'post_roll_decision')
        # Payload Slim v1：draft 溯源不再重复下发；仍可用牌自带 CARDS 派生效果
        self.assertNotIn('childhood_draft_result', d3)
        self.assertEqual([c['card_id'] for c in d3['childhood_cards']],
                         session.game.childhood_kept)
        for card in d3['childhood_cards']:
            self.assertTrue(card['effect_summary'])
            self.assertEqual(card['effect_summary'],
                             _SPECIAL_NOTES.get(card['card_id'])
                             or _card_effect_summary(CARDS[card['card_id']]))
        self.assertEqual([g['id'] for g in d3['life_goals']], [5, 17])
        rng_state = session.game.rng.getstate()
        again = session.current_decision()
        self.assertEqual(again, d3)
        self.assertEqual(session.game.rng.getstate(), rng_state)


class TestPreviousTurnResultGating(unittest.TestCase):
    def turn2_session(self, stacks=(), hand=(), forced=None, fate_lock=False):
        session = GameSession(seed=19, shuffle=False, forced_goals=[1, 2])
        game = session.game
        game.childhood_complete = True
        game.turn = 2
        game.hand = list(hand)
        for cls, cards in stacks:
            game.cv[cls] = list(cards)
        if fate_lock:
            game.fate_stack = ['F12']
            game.active_fate = 'F12'
            game.fate_active_from_turn = 2
        session._turn_closeout_resolved = True
        session._previous_turn_result = {
            'completed_turn': 1, 'next_turn': 2,
            'maintenance_result': {'paid': []},
            'market_cleanup_result': {'purchased_card_ids': []},
        }
        dice = list(forced) if forced is not None else ['H', 'K', 'R', 'M']
        if fate_lock:
            dice = ['BL', 'BL', 'K', 'R', 'M', 'H']
        game._forced = dice + ['H'] * 20
        return session, session.current_decision()

    def test_pre_roll_skipped_abilities_single_carrier(self):
        session, decision = self.turn2_session(
            stacks=[('K', ['MK-04'])], hand=['C11'])
        self.assertEqual(decision['kind'], 'pre_roll_decision')
        self.assertEqual(decision['previous_turn_result']['completed_turn'], 1)
        result = session.submit_action(decision['decision_id'], {
            'flex_resource_choices': {'MK-04': 'K'},
            'use_c11': False, 'use_ye05': False, 'use_me02': False})
        rolled = result['decision']
        self.assertEqual(rolled['kind'], 'post_roll_decision')
        self.assertNotIn('previous_turn_result', rolled)
        self.assertEqual(session.current_decision(), rolled)

    def test_pre_roll_used_ability_single_carrier(self):
        session, decision = self.turn2_session(
            stacks=[('H', ['YH-02'])], hand=['C11'])
        self.assertEqual(decision['previous_turn_result']['completed_turn'], 1)
        result = session.submit_action(decision['decision_id'],
                                       {'use_c11': True})
        rolled = result['decision']
        self.assertEqual(rolled['kind'], 'post_roll_decision')
        # C11 已消耗使 _pre_roll_has_choices 变 False，
        # used_card_ids 交集保证仍判定为 surfaced，不重复携带。
        self.assertNotIn('previous_turn_result', rolled)
        self.assertEqual(session.current_decision(), rolled)

    def test_no_pre_roll_first_post_roll_carries_then_stops(self):
        session, decision = self.turn2_session()
        self.assertEqual(decision['kind'], 'post_roll_decision')
        self.assertEqual(decision['previous_turn_result']['completed_turn'], 1)
        session.game._forced = ['GL']
        rerolled = session.submit_action(decision['decision_id'],
                                         {'choice': 'reroll', 'indices': [0]})
        self.assertTrue(rerolled['ok'])
        second = rerolled['decision']
        self.assertEqual(second['kind'], 'post_roll_decision')
        self.assertNotIn('previous_turn_result', second)
        self.assertEqual(session.current_decision(), second)

    def test_auto_locked_purchase_l1_carries_as_fallback(self):
        session, decision = self.turn2_session(fate_lock=True)
        self.assertEqual(decision['kind'], 'purchase_ready')
        self.assertIn('purchase_targets', decision)
        self.assertIn('previous_turn_result', decision)
        self.assertEqual(decision['previous_turn_result']['completed_turn'], 1)
        self.assertEqual(session.current_decision(), decision)

    def test_purchase_layers_do_not_repeat_after_post_roll(self):
        session, decision = self.turn2_session(
            hand=['C07'], forced=['H', 'H', 'K', 'M'])
        session.game.market = ['YH-01', 'YK-01']
        session.game.market_entry = {'YH-01': 2, 'YK-01': 2}
        self.assertIn('previous_turn_result', decision)
        ready = session.submit_action(
            decision['decision_id'],
            {'choice': 'proceed_to_purchase'})['decision']
        self.assertEqual(ready['kind'], 'purchase_ready')
        self.assertNotIn('previous_turn_result', ready)
        second = session.submit_action(ready['decision_id'], {
            'ordinary_card_ids': ['YH-01'], 'fate_card_id': None})['decision']
        self.assertIn('selected_purchase_target', second)
        self.assertNotIn('previous_turn_result', second)

    def test_game_over_keeps_final_closeout_result(self):
        session, decision = self.turn2_session()
        session._previous_turn_result = {
            'completed_turn': 2, 'next_turn': None,
            'maintenance_result': {'paid': []},
            'market_cleanup_result': {'purchased_card_ids': []},
        }
        session.game.game_over = True
        final = session.current_decision()
        self.assertEqual(final['kind'], 'game_over')
        self.assertEqual(final['previous_turn_result']['completed_turn'], 2)


class TestRuntimeGameOverScore(unittest.TestCase):
    GAME_OVER_CV = {
        'H': ['YH-01', 'YH-02'],
        'K': ['YK-01', 'MK-04'],
        'R': [],
        'W': ['YW-01'],
        'P': ['YP-01', 'YP-02'],
    }

    def game_over_session(self):
        session = GameSession(seed=1, shuffle=False, forced_goals=[5, 17])
        game = session.game
        game.childhood_complete = True
        game.turn = 20
        game.hand = []
        game.cv = {cls: list(cards)
                   for cls, cards in self.GAME_OVER_CV.items()}
        game.goals = [5, 17]
        game.fate_stack = ['F05', 'F01']
        game.debuff_history = ['D01']
        game.current_debuff = None
        game.stats.game['event_buys'] = Counter({'YE-01': 1, 'YE-03': 2})
        game.game_over = True
        session._turn_closeout_resolved = True
        session._previous_turn_result = {
            'completed_turn': 20, 'next_turn': None,
            'maintenance_result': {'paid': []},
            'market_cleanup_result': {'purchased_card_ids': []},
        }
        # 新协议：active flex 卡须先做终局最终指定才是正式 game_over；
        # 此处取首个合法符号（与旧 oracle 平局裁决一致），指定不影响
        # LG5/LG17 分数。
        flex_ids = flex_actives(game.cv)
        if flex_ids and session._final_flex_designation is None:
            session._final_flex_designation = {
                cid: CARDS[cid]['flex'][0] for cid in flex_ids}
        return session, session.current_decision()

    def test_game_over_decision_exposes_full_official_score(self):
        session, decision = self.game_over_session()
        self.assertEqual(decision['kind'], 'game_over')
        self.assertTrue(decision['scoring_ready'])
        score = decision['score']
        self.assertTrue({'total', 'base_sum', 'pvp', 'lg_ids', 'lg_scores',
                         'lg_sum', 'debuff_vp'} <= set(score))
        self.assertEqual(score['total'],
                         score['base_sum'] + score['pvp']
                         + score['lg_sum'] + score['debuff_vp'])
        self.assertEqual(score, full_score(
            session.game.cv, session.game.goals,
            **session.game.scoring_counts()))
        rng_state = session.game.rng.getstate()
        again = session.current_decision()
        self.assertEqual(again, decision)
        self.assertEqual(session.game.rng.getstate(), rng_state)

    def test_game_over_score_matches_simulator_scoring_pipeline(self):
        session, decision = self.game_over_session()
        sim = Game(CONFIGS['V06'], None, random.Random(5), shuffle=False,
                   forced_goals=[5, 17], defer_childhood=True)
        sim.cv = {cls: list(cards)
                  for cls, cards in self.GAME_OVER_CV.items()}
        sim.goals = [5, 17]
        sim.fate_stack = ['F05', 'F01']
        sim.debuff_history = ['D01']
        sim.current_debuff = None
        sim.stats.game['event_buys'] = Counter({'YE-01': 1, 'YE-03': 2})
        sim.stats.finish_game(sim)
        row_score = sim.stats.rows[-1]['score']
        for key in ('total', 'base_sum', 'pvp', 'debuff_vp', 'lg_sum',
                    'lg_ids', 'lg_scores', 'counts'):
            self.assertEqual(decision['score'][key], row_score[key], key)


class TestYK05RuntimeConversion(unittest.TestCase):
    """YK-05 每回合一次 R→K 转换接入 Runtime 的定向回归。"""

    def make_session(self, stacks=(), dice=('R', 'H', 'M', 'K'), market=None):
        session = GameSession(seed=19, shuffle=False, forced_goals=[1, 2])
        game = session.game
        game.childhood_complete = True
        game.turn = 2
        game.hand = []
        for cls, cards in stacks:
            game.cv[cls] = list(cards)
        session._turn_closeout_resolved = True
        session._previous_turn_result = {
            'completed_turn': 1, 'next_turn': 2,
            'maintenance_result': {'paid': []},
            'market_cleanup_result': {'purchased_card_ids': []},
        }
        game._forced = list(dice) + ['H'] * 20
        if market is not None:
            game.market = list(market)
        return session

    def post_roll(self, session):
        decision = session.current_decision()
        self.assertEqual(decision['kind'], 'post_roll_decision')
        return decision

    def test_availability_matrix(self):
        game = self.make_session().game
        # YK-05 未激活：不可用
        self.assertFalse(game.yk05_conversion_available())
        # 激活 + 池中有 R（骰子来源）：可用
        game.cv['K'] = ['YK-05']
        game.dice = ['R', 'H', 'M', 'K']
        self.assertTrue(game.yk05_conversion_available())
        # 已使用：不可用；重复 apply 无副作用
        self.assertTrue(game.apply_yk05_conversion())
        self.assertFalse(game.apply_yk05_conversion())
        self.assertFalse(game.yk05_conversion_available())
        # 下回合复位后恢复可用（stable R 亦可作来源）
        game.dice = []
        game.cv['R'] = ['YR-01']
        game._forced = ['H', 'H', 'K', 'M']
        game.pre_roll_open = False
        game.start_adult_turn()
        game.roll_first_dice()
        self.assertTrue(game.yk05_conversion_available())

    def test_conversion_derives_into_post_roll_resources(self):
        session = self.make_session(stacks=[('K', ['YK-05'])])
        decision = self.post_roll(session)
        self.assertTrue(decision['yk05_conversion_available'])
        self.assertEqual(decision['stable_resources'], {'K': 1})
        self.assertEqual(decision['total_available_resources'],
                         {'H': 1, 'K': 2, 'M': 1, 'R': 1})
        result = session.submit_action(decision['decision_id'], {
            'choice': 'proceed_to_purchase', 'use_yk05': True})
        self.assertTrue(result['ok'])
        ready = result['decision']
        self.assertEqual(ready['kind'], 'purchase_ready')
        self.assertEqual(ready['total_available_resources'],
                         {'H': 1, 'K': 3, 'M': 1})

    def test_stable_r_is_convertible(self):
        session = self.make_session(
            stacks=[('K', ['YK-05']), ('R', ['YR-01'])],
            dice=('H', 'H', 'M', 'K'))
        decision = self.post_roll(session)
        self.assertTrue(decision['yk05_conversion_available'])
        self.assertEqual(decision['total_available_resources'],
                         {'H': 2, 'K': 2, 'M': 1, 'R': 1})
        result = session.submit_action(decision['decision_id'], {
            'choice': 'proceed_to_purchase', 'use_yk05': True})
        self.assertEqual(result['decision']['total_available_resources'],
                         {'H': 2, 'K': 3, 'M': 1})

    def test_skip_leaves_pool_unchanged(self):
        session = self.make_session(stacks=[('K', ['YK-05'])])
        decision = self.post_roll(session)
        result = session.submit_action(decision['decision_id'],
                                       {'choice': 'proceed_to_purchase'})
        self.assertTrue(result['ok'])
        self.assertEqual(result['decision']['total_available_resources'],
                         {'H': 1, 'K': 2, 'M': 1, 'R': 1})

    def test_conversion_synchronizes_into_purchase_plans(self):
        # YK-03 需 K2：转换前只有 stable K1 不可购，转换后 K2 进入合法目标
        session = self.make_session(stacks=[('K', ['YK-05'])],
                                    dice=('R', 'H', 'M', 'M'),
                                    market=['YK-03', 'YH-01'])
        decision = self.post_roll(session)
        self.assertNotIn(
            ['YK-03'],
            [p.get('ordinary_card_ids', p.get('card_ids'))
             for p in decision['legal_acquisition_plans']])
        result = session.submit_action(decision['decision_id'], {
            'choice': 'proceed_to_purchase', 'use_yk05': True})
        ready = result['decision']
        targets = [t['ordinary_card_ids'] for t in ready['purchase_targets']]
        self.assertIn(['YK-03'], targets)

    def test_f12_auto_lock_still_offers_conversion(self):
        session = self.make_session(
            stacks=[('K', ['YK-05'])],
            dice=('BL', 'R', 'K', 'M', 'H', 'H'))
        game = session.game
        game.fate_stack = ['F12']
        game.active_fate = 'F12'
        game.fate_active_from_turn = 2
        decision = self.post_roll(session)
        # F12 锁重掷但 YK-05 可用：不得静默跳过骰后决策
        self.assertTrue(decision['normal_rerolls_locked'])
        self.assertTrue(decision['yk05_conversion_available'])
        result = session.submit_action(decision['decision_id'], {
            'choice': 'proceed_to_purchase', 'use_yk05': True})
        self.assertTrue(result['ok'])
        self.assertEqual(
            result['decision']['total_available_resources']['K'], 3)
        self.assertNotIn('R',
                         result['decision']['total_available_resources'])

    def test_illegal_and_stale_submissions_are_side_effect_free(self):
        session = self.make_session()  # 无 YK-05：不可用
        decision = self.post_roll(session)
        self.assertFalse(decision['yk05_conversion_available'])
        before = (list(session.game.dice), dict(session.game.stable_pool))
        # 非法：不可用时声明转换
        result = session.submit_action(decision['decision_id'], {
            'choice': 'proceed_to_purchase', 'use_yk05': True})
        self.assertEqual(result['error'], 'illegal_action')
        # 非法：非布尔 use_yk05 / 未知键
        result = session.submit_action(decision['decision_id'], {
            'choice': 'proceed_to_purchase', 'use_yk05': 'yes'})
        self.assertEqual(result['error'], 'invalid_action')
        result = session.submit_action(decision['decision_id'], {
            'choice': 'proceed_to_purchase', 'use_yk05': False, 'x': 1})
        self.assertEqual(result['error'], 'invalid_action')
        # stale decision_id
        result = session.submit_action('post_roll_decision:9:9:9', {
            'choice': 'proceed_to_purchase'})
        self.assertEqual(result['error'], 'stale_or_unknown_decision_id')
        self.assertEqual((list(session.game.dice),
                          dict(session.game.stable_pool)), before)
        self.assertEqual(session.current_decision()['kind'],
                         'post_roll_decision')

    def test_state_resets_and_pools_stay_synchronized_next_turn(self):
        session = self.make_session(stacks=[('K', ['YK-05'])])
        decision = self.post_roll(session)
        session.submit_action(decision['decision_id'], {
            'choice': 'proceed_to_purchase', 'use_yk05': True})
        ready = session.current_decision()
        # 第 3 回合的骰子在本次提交的自动推进链中掷出，先备好带 R 的骰序
        session.game._forced = ['R', 'H', 'M', 'K'] + ['H'] * 16
        # 空购买：剩余资源即转换后池，经购买执行写入 game.pool 供维护共用
        result = session.submit_action(ready['decision_id'], {
            'ordinary_card_ids': [], 'fate_card_id': None})
        self.assertTrue(result['ok'])
        purchase = session._previous_turn_result['purchase_result']
        self.assertEqual(purchase['purchased_card_ids'], [])
        self.assertEqual(purchase['remaining_resources'],
                         {'H': 1, 'K': 3, 'M': 1})
        # 推进到下一回合：yk05_used 复位，转换再次可用
        for _ in range(10):
            state = session.current_decision()
            if state['kind'] == 'post_roll_decision':
                break
            if state['kind'] == 'purchase_ready':
                session.submit_action(state['decision_id'], {
                    'ordinary_card_ids': [], 'fate_card_id': None})
            elif state['kind'] == 'pre_roll_decision':
                session.submit_action(state['decision_id'], {})
            else:
                break
        self.assertEqual(session.game.yk05_used, False)
        self.assertTrue(session.game.yk05_conversion_available())

    def test_simulator_pipeline_uses_shared_method(self):
        game = Game(CONFIGS['V06'], Balanced, random.Random(7))
        game.run()
        # 模拟器与 Runtime 共用 apply_yk05_conversion：两处计数必须同源
        self.assertEqual(
            game.stats.run['yk05_used'],
            game.stats.card('YK-05')['ability']['convert_turn'])


class TestMH02DebuffProtection(unittest.TestCase):
    """MH-02「年度体检」接入 Runtime 的 Debuff 保护定向回归。"""

    def make_engine_game(self, hand=(), stacks=(), deck=('D01', 'D02'),
                         pool=None, bad_luck=0):
        game = Game(CONFIGS['V06'], Balanced, random.Random(3))
        game.childhood_complete = True
        game.turn = 2
        game.pre_roll_open = False
        game.hand = list(hand)
        for cls, cards in stacks:
            game.cv[cls] = list(cards)
        game.debuff_deck = list(deck)
        if pool is not None:
            game.pool = Counter(pool)
        game.bad_luck_accumulator = bad_luck
        return game

    def ready_session(self, dice, market=None, extra_hand=(), stable=None,
                      prior_bad_luck=0):
        session = GameSession(seed=1, shuffle=False, forced_goals=[1, 2])
        first = session.current_decision()
        second = session.submit_action(first['decision_id'],
                                       {'card_id': 'C12'})['decision']
        session.game._forced = list(dice)
        post = session.submit_action(second['decision_id'],
                                     {'card_id': 'C09'})['decision']
        if market is not None:
            session.game.market = list(market)
            session.game.market_entry = {
                cid: session.game.turn for cid in session.game.market}
        for cid in extra_hand:
            if cid not in session.game.hand:
                session.game.hand.append(cid)
        if stable:
            session.game.stable_pool = Counter(stable)
        session.game.bad_luck_accumulator = prior_bad_luck
        ready = session.submit_action(
            post['decision_id'], {'choice': 'proceed_to_purchase'})['decision']
        self.assertEqual(ready['kind'], 'purchase_ready')
        return session, ready

    def find_plan(self, session, card_ids):
        matches = [p for p in session._purchase_plan_cache
                   if (p['ordinary_card_ids']
                       if 'ordinary_card_ids' in p else p['card_ids'])
                   == list(card_ids)]
        self.assertTrue(matches, card_ids)
        return matches[0]

    def submit_plan(self, session, ready, plan):
        result = session.submit_action(ready['decision_id'], {
            'ordinary_card_ids': list(plan['ordinary_card_ids'])
            if 'ordinary_card_ids' in plan else list(plan['card_ids']),
            'fate_card_id': plan.get('fate_card_id')})
        if not result['ok']:
            return result
        current = result['decision']
        if 'selected_purchase_target' in current:
            return session.submit_action(current['decision_id'],
                                         {'plan_id': plan['plan_id']})
        return result

    def triggered_session(self, extra_hand=(), stable=None, deck=None):
        """真实购买流触发 Debuff（既有累计 3 + 最终骰面 BL2）。"""
        session, ready = self.ready_session(
            ['H', 'H', 'BL', 'BL'], ['YH-01'],
            extra_hand=extra_hand, stable=stable, prior_bad_luck=3)
        session.game.cv['H'] = ['MH-02']
        if deck is not None:
            session.game.debuff_deck = list(deck)
        return session, ready

    # ---------- 引擎层：选项矩阵与消耗路径 ----------

    def test_options_matrix(self):
        game = self.make_engine_game(pool={'BL': 3})
        # 无任何保护
        self.assertEqual(game.debuff_cancel_options(), [])
        # 仅 C06
        game.hand = ['C06']
        self.assertEqual(game.debuff_cancel_options(),
                         [{'cid': 'C06', 'source': 'hand'}])
        # 双选（C06 在前，active MH-02 在后）
        game.cv['H'] = ['MH-02']
        self.assertEqual([o['cid'] for o in game.debuff_cancel_options()],
                         ['C06', 'MH-02'])
        # MH-02 已用：只剩 C06
        game.used_once_cards = {'MH-02'}
        self.assertEqual([o['cid'] for o in game.debuff_cancel_options()],
                         ['C06'])
        # MH-02 被顶替（非 active）：只剩 C06
        game.used_once_cards = set()
        game.cv['H'] = ['MH-02', 'OH-03']
        self.assertEqual([o['cid'] for o in game.debuff_cancel_options()],
                         ['C06'])
        # 牌库空：一律不提供（正式规则修正）
        game.debuff_deck = []
        game.cv['H'] = ['MH-02']
        self.assertEqual(game.debuff_cancel_options(), [])

    def test_mh02_use_keeps_active_and_provides(self):
        game = self.make_engine_game(stacks=[('H', ['MH-02'])],
                                     pool={'BL': 3})
        self.assertTrue(game.consume_debuff_cancel('MH-02'))
        self.assertIn('MH-02', game.used_once_cards)
        self.assertEqual(game.active('H'), 'MH-02')
        self.assertIn(('H', 1), game.fixed_stable_resources().items())
        # 第二次触发不再可用
        self.assertNotIn('MH-02',
                         [o['cid'] for o in game.debuff_cancel_options()])
        self.assertFalse(game.consume_debuff_cancel('MH-02'))

    def test_c06_consume_path_differs_from_mh02(self):
        game = self.make_engine_game(hand=['C06'],
                                     stacks=[('H', ['MH-02'])],
                                     pool={'BL': 3})
        uses_before = game.stats.game['childhood_uses']['C06']
        self.assertTrue(game.consume_debuff_cancel('C06'))
        self.assertNotIn('C06', game.hand)
        self.assertEqual(game.stats.game['childhood_uses']['C06'],
                         uses_before + 1)
        self.assertNotIn('MH-02', game.used_once_cards)
        # 非法 cid 无副作用
        self.assertFalse(game.consume_debuff_cancel('C99'))
        self.assertEqual(game.stats.game['debuff_cancelled'], 1)

    def test_cancel_means_no_draw_no_experience_no_final_count(self):
        game = self.make_engine_game(hand=['C06'],
                                     stacks=[('H', ['MH-02'])],
                                     deck=['D01', 'D02'],
                                     bad_luck=5)
        outcome = game.resolve_runtime_debuff(cancel_cid='MH-02')
        self.assertEqual(outcome, 'cancelled')
        self.assertIsNone(game.current_debuff)
        self.assertEqual(game.debuff_history, [])
        self.assertEqual(game.debuff_deck, ['D01', 'D02'])
        self.assertEqual(game.scoring_counts()['debuff_count'], 0)
        self.assertIn('MH-02', game.used_once_cards)
        self.assertEqual(game.bad_luck_accumulator, 0)

    def test_deck_empty_skips_protection_without_consumption(self):
        game = self.make_engine_game(hand=['C06'],
                                     stacks=[('H', ['MH-02'])],
                                     deck=[], bad_luck=5)
        self.assertIsNone(game.resolve_runtime_debuff(cancel_cid='C06'))
        self.assertIn('C06', game.hand)
        self.assertNotIn('MH-02', game.used_once_cards)
        self.assertEqual(game.resolve_runtime_debuff(), 'empty')
        self.assertIn('C06', game.hand)
        self.assertIsNone(game.current_debuff)
        self.assertEqual(game.stats.run['debuff_empty_triggers'], 1)
        self.assertEqual(game.bad_luck_accumulator, 0)

    def test_no_protection_draws_normally(self):
        game = self.make_engine_game(hand=[], deck=['D07'], bad_luck=5)
        self.assertEqual(game.resolve_runtime_debuff(), 'drawn')
        self.assertEqual(game.current_debuff, 'D07')
        self.assertEqual(game.debuff_active_from_turn, game.turn + 1)
        self.assertEqual(game.debuff_deck, [])

    def test_invalid_cancel_cid_is_rejected_before_trigger_record(self):
        game = self.make_engine_game(hand=['C06'], deck=['D01'],
                                     bad_luck=5)
        triggers_before = game.stats.game['debuff_triggers']
        self.assertIsNone(game.resolve_runtime_debuff(cancel_cid='C99'))
        self.assertEqual(game.stats.game['debuff_triggers'], triggers_before)
        self.assertIn('C06', game.hand)
        self.assertEqual(game.debuff_deck, ['D01'])

    # ---------- 模拟器对齐：_debuff_check 走共享正式语义 ----------

    def test_simulator_debuff_check_prefers_active_protection(self):
        game = self.make_engine_game(stacks=[('H', ['MH-02'])],
                                     deck=['D01'], bad_luck=5)
        game._debuff_check()
        # heuristic 策略层偏好 active 保护；引擎只按选择消耗
        self.assertIn('MH-02', game.used_once_cards)
        self.assertIsNone(game.current_debuff)
        self.assertEqual(game.debuff_deck, ['D01'])
        self.assertEqual(game.scoring_counts()['debuff_count'], 0)

    def test_simulator_debuff_check_skips_protection_on_empty_deck(self):
        game = self.make_engine_game(hand=['C06'],
                                     stacks=[('H', ['MH-02'])],
                                     deck=[], bad_luck=5)
        game._debuff_check()
        self.assertEqual(game.used_once_cards, set())
        self.assertIn('C06', game.hand)
        self.assertIsNone(game.current_debuff)
        self.assertEqual(game.stats.run['debuff_empty_triggers'], 1)

    # ---------- Runtime：决策协议 ----------

    def test_runtime_both_options_require_explicit_source(self):
        session, ready = self.triggered_session(extra_hand=['C06'],
                                                stable={'BL': 1})
        result = self.submit_plan(session, ready,
                                  self.find_plan(session, ('YH-01',)))
        protection = result['decision']
        self.assertEqual(protection['kind'], 'debuff_protection_decision')
        self.assertEqual([c['id'] for c in protection['candidates']],
                         ['C06', 'MH-02'])
        self.assertEqual(protection['legal_actions'], [
            {'choice': 'skip'},
            {'choice': 'use', 'source_card_id': 'C06'},
            {'choice': 'use', 'source_card_id': 'MH-02'}])
        # 双选时 bare use 必须被拒且无副作用
        before = (list(session.game.hand), set(session.game.used_once_cards),
                  list(session.game.debuff_deck))
        rejected = session.submit_action(protection['decision_id'],
                                         {'choice': 'use'})
        self.assertEqual(rejected['error'], 'illegal_action')
        self.assertEqual((list(session.game.hand),
                          set(session.game.used_once_cards),
                          list(session.game.debuff_deck)), before)

    def test_runtime_explicit_c06_consumption(self):
        session, ready = self.triggered_session(extra_hand=['C06'],
                                                stable={'BL': 1})
        protection = self.submit_plan(
            session, ready, self.find_plan(session, ('YH-01',)))['decision']
        result = session.submit_action(protection['decision_id'], {
            'choice': 'use', 'source_card_id': 'C06'})
        self.assertTrue(result['ok'])
        self.assertNotIn('C06', session.game.hand)
        self.assertEqual(session.game.used_once_cards, set())
        self.assertEqual(session.game.active('H'), 'MH-02')
        self.assertEqual(session.game.debuff_deck[0], 'D01')
        self.assertIsNone(session.game.current_debuff)
        self.assertEqual(result['decision']['kind'], 'placement_decision')

    def test_runtime_explicit_mh02_consumption(self):
        session, ready = self.triggered_session(extra_hand=['C06'],
                                                stable={'BL': 1})
        protection = self.submit_plan(
            session, ready, self.find_plan(session, ('YH-01',)))['decision']
        result = session.submit_action(protection['decision_id'], {
            'choice': 'use', 'source_card_id': 'MH-02'})
        self.assertTrue(result['ok'])
        self.assertIn('C06', session.game.hand)
        self.assertIn('MH-02', session.game.used_once_cards)
        self.assertEqual(session.game.active('H'), 'MH-02')
        self.assertEqual(session.game.debuff_deck[0], 'D01')
        self.assertIsNone(session.game.current_debuff)

    def test_runtime_single_option_allows_legacy_bare_use(self):
        # 无 C06、仅 MH-02：单候选兼容旧 bare use 协议
        session, ready = self.triggered_session(stable={'BL': 1})
        protection = self.submit_plan(
            session, ready, self.find_plan(session, ('YH-01',)))['decision']
        self.assertEqual([c['id'] for c in protection['candidates']],
                         ['MH-02'])
        result = session.submit_action(protection['decision_id'],
                                       {'choice': 'use'})
        self.assertTrue(result['ok'])
        self.assertIn('MH-02', session.game.used_once_cards)
        self.assertEqual(session.game.active('H'), 'MH-02')

    def test_runtime_deck_empty_no_decision_and_no_consumption(self):
        session, ready = self.triggered_session(extra_hand=['C06'],
                                                stable={'BL': 1},
                                                deck=[])
        result = self.submit_plan(session, ready,
                                  self.find_plan(session, ('YH-01',)))
        # 不打开保护决策：自动按"无 Debuff 可抽"继续，且不消耗任何保护
        self.assertEqual(result['decision']['kind'], 'placement_decision')
        self.assertIsNone(session.game.current_debuff)
        self.assertIn('C06', session.game.hand)
        self.assertEqual(session.game.used_once_cards, set())
        self.assertEqual(session.game.stats.run['debuff_empty_triggers'], 1)

    def test_runtime_stale_and_wrong_source_are_side_effect_free(self):
        session, ready = self.triggered_session(extra_hand=['C06'],
                                                stable={'BL': 1})
        protection = self.submit_plan(
            session, ready, self.find_plan(session, ('YH-01',)))['decision']
        before = (list(session.game.hand), set(session.game.used_once_cards),
                  list(session.game.debuff_deck))
        # 不存在的来源
        result = session.submit_action(protection['decision_id'], {
            'choice': 'use', 'source_card_id': 'C99'})
        self.assertEqual(result['error'], 'illegal_action')
        # 多余键
        result = session.submit_action(protection['decision_id'], {
            'choice': 'use', 'source_card_id': 'C06', 'x': 1})
        self.assertEqual(result['error'], 'illegal_action')
        # stale decision_id
        result = session.submit_action('debuff_protection_decision:9:9:9', {
            'choice': 'use', 'source_card_id': 'C06'})
        self.assertEqual(result['error'], 'stale_or_unknown_decision_id')
        self.assertEqual((list(session.game.hand),
                          set(session.game.used_once_cards),
                          list(session.game.debuff_deck)), before)


class TestOH01DebuffShorten(unittest.TestCase):
    """OH-01「长期健康管理」缩短 Debuff 接入 Runtime 的定向回归。"""

    def make_engine_game(self, hand=(), stacks=(), pool=None, deck=('D01',),
                         bad_luck=0):
        game = Game(CONFIGS['V06'], Balanced, random.Random(3))
        game.childhood_complete = True
        game.turn = 2
        game.pre_roll_open = False
        game.hand = list(hand)
        for cls, cards in stacks:
            game.cv[cls] = list(cards)
        game.debuff_deck = list(deck)
        if pool is not None:
            game.pool = Counter(pool)
        game.bad_luck_accumulator = bad_luck
        return game

    def shorten_session(self, stable=None, market=('YH-01',)):
        """真实购买流：既有累计 3 + 最终骰面 BL2 触发，OH-01 已 active。"""
        session = GameSession(seed=1, shuffle=False, forced_goals=[1, 2])
        first = session.current_decision()
        second = session.submit_action(first['decision_id'],
                                       {'card_id': 'C12'})['decision']
        session.game._forced = ['H', 'H', 'BL', 'BL']
        post = session.submit_action(second['decision_id'],
                                     {'card_id': 'C09'})['decision']
        session.game.market = list(market)
        session.game.market_entry = {
            cid: session.game.turn for cid in session.game.market}
        session.game.cv['H'] = ['OH-01']
        if stable is not None:
            session.game.stable_pool = Counter(stable)
        session.game.bad_luck_accumulator = 3
        ready = session.submit_action(
            post['decision_id'], {'choice': 'proceed_to_purchase'})['decision']
        self.assertEqual(ready['kind'], 'purchase_ready')
        return session, ready

    def find_plan(self, session, card_ids):
        # 显式选无折扣支付（本 seed 随机第 3 张 C07 为 H 折扣，会分叉出
        # 折扣 plan）；测试关注的是足额 H 剩余的确定口径。
        matches = [p for p in session._purchase_plan_cache
                   if (p['ordinary_card_ids']
                       if 'ordinary_card_ids' in p else p['card_ids'])
                   == list(card_ids) and not p['discounts_used']]
        self.assertTrue(matches, card_ids)
        return matches[0]

    def submit_plan(self, session, ready, plan):
        result = session.submit_action(ready['decision_id'], {
            'ordinary_card_ids': list(plan['ordinary_card_ids'])
            if 'ordinary_card_ids' in plan else list(plan['card_ids']),
            'fate_card_id': plan.get('fate_card_id')})
        if not result['ok']:
            return result
        current = result['decision']
        if 'selected_purchase_target' in current:
            return session.submit_action(current['decision_id'],
                                         {'plan_id': plan['plan_id']})
        return result

    # ---------- 引擎层 ----------

    def test_option_matrix(self):
        game = self.make_engine_game()
        self.assertIsNone(game.debuff_shorten_option())  # 无 OH-01
        game.cv['H'] = ['OH-01']
        option = game.debuff_shorten_option()
        self.assertEqual(option['cid'], 'OH-01')
        self.assertEqual(option['cost'], {'H': 1})
        self.assertEqual(option['reduce'], 1)
        game.cv['H'] = ['OH-01', 'OH-03']  # 被顶替：非 active
        self.assertIsNone(game.debuff_shorten_option())

    def test_apply_pays_and_shortens_with_min_clamp(self):
        game = self.make_engine_game(stacks=[('H', ['OH-01'])],
                                     pool={'H': 2})
        game.current_debuff = 'D01'
        game.debuff_turns_remaining = 3
        self.assertTrue(game.apply_debuff_shorten('OH-01'))
        self.assertEqual(game.pool.get('H', 0), 1)
        self.assertEqual(game.debuff_turns_remaining, 2)
        self.assertEqual(
            game.stats.card('OH-01')['ability']['shorten_debuff'], 1)
        # 最低 1 回合钳制：duration 已为 1 时缩短仍合法但不再减少
        game.debuff_turns_remaining = 1
        self.assertTrue(game.apply_debuff_shorten('OH-01'))
        self.assertEqual(game.debuff_turns_remaining, 1)
        self.assertEqual(game.pool.get('H', 0), 0)

    def test_apply_rejections_are_side_effect_free(self):
        game = self.make_engine_game(stacks=[('H', ['OH-01'])],
                                     pool={'H': 2})
        # 无 current Debuff
        self.assertFalse(game.apply_debuff_shorten('OH-01'))
        game.current_debuff = 'D01'
        game.debuff_turns_remaining = 3
        # cid 不匹配
        self.assertFalse(game.apply_debuff_shorten('OH-03'))
        # H 不足
        game.pool = Counter({'K': 2})
        self.assertFalse(game.apply_debuff_shorten('OH-01'))
        self.assertEqual(game.debuff_turns_remaining, 3)
        self.assertEqual(game.pool, Counter({'K': 2}))

    def test_newly_bought_oh01_is_not_active_before_placement(self):
        game = self.make_engine_game(pool={'H': 2})
        game.pending_new = ['OH-01']
        self.assertIsNone(game.debuff_shorten_option())

    # ---------- 模拟器共享实现 ----------

    def test_simulator_debuff_check_applies_shorten(self):
        game = self.make_engine_game(stacks=[('H', ['OH-01'])],
                                     pool={'H': 2}, bad_luck=5)
        game._debuff_check()
        self.assertEqual(game.current_debuff, 'D01')
        self.assertEqual(game.debuff_turns_remaining, 2)
        self.assertEqual(game.pool.get('H', 0), 1)
        self.assertEqual(
            game.stats.card('OH-01')['ability']['shorten_debuff'], 1)

    def test_simulator_debuff_check_skips_without_h(self):
        game = self.make_engine_game(stacks=[('H', ['OH-01'])],
                                     bad_luck=5)
        game._debuff_check()
        self.assertEqual(game.current_debuff, 'D01')
        self.assertEqual(game.debuff_turns_remaining, 3)
        self.assertEqual(
            game.stats.card('OH-01')['ability']['shorten_debuff'], 0)

    def test_cancel_path_excludes_shorten_window(self):
        game = self.make_engine_game(hand=['C06'],
                                     stacks=[('H', ['OH-01'])],
                                     pool={'H': 2}, bad_luck=5)
        game._debuff_check()
        # 取消与缩短按触发互斥：取消后不抽牌、不支付、不缩短
        self.assertIsNone(game.current_debuff)
        self.assertNotIn('C06', game.hand)
        self.assertEqual(game.pool.get('H', 0), 2)
        self.assertEqual(
            game.stats.card('OH-01')['ability']['shorten_debuff'], 0)

    # ---------- Runtime 协议 ----------

    def test_runtime_shorten_decision_appears_after_draw(self):
        session, ready = self.shorten_session(stable={'BL': 1, 'H': 1})
        result = self.submit_plan(session, ready,
                                  self.find_plan(session, ('YH-01',)))
        shorten = result['decision']
        self.assertEqual(shorten['kind'], 'debuff_shorten_decision')
        self.assertEqual(shorten['debuff']['id'], 'D01')
        self.assertEqual(shorten['shorten_card_id'], 'OH-01')
        self.assertEqual(shorten['cost'], {'H': 1})
        self.assertEqual(shorten['duration_before'], 3)
        self.assertEqual(shorten['duration_after'], 2)
        self.assertEqual(shorten['legal_actions'],
                         [{'choice': 'skip'}, {'choice': 'pay'}])
        self.assertEqual(shorten['pool'], {'BL': 3, 'H': 1})

    def test_shorten_decision_effect_hint_is_explicit(self):
        session, ready = self.shorten_session(stable={'BL': 1, 'H': 1})
        shorten = self.submit_plan(
            session, ready, self.find_plan(session, ('YH-01',)))['decision']
        # 玩家侧直述效果：支付 H×1、减少 1 回合、最低 1 回合三要素齐备
        hint = shorten['effect_hint']
        self.assertIn('支付 H×1', hint)
        self.assertIn('持续时间减少 1 回合', hint)
        self.assertIn('最低 1 回合', hint)

    def test_runtime_pay_flows_into_maintenance(self):
        session, ready = self.shorten_session(stable={'BL': 1, 'H': 1})
        shorten = self.submit_plan(
            session, ready, self.find_plan(session, ('YH-01',)))['decision']
        result = session.submit_action(shorten['decision_id'],
                                       {'choice': 'pay'})
        self.assertTrue(result['ok'])
        self.assertEqual(session.game.debuff_turns_remaining, 2)
        self.assertEqual(session.game.pool.get('H', 0), 0)
        # placement 在缩短之后
        self.assertEqual(result['decision']['kind'], 'placement_decision')
        done = session.submit_action(result['decision']['decision_id'],
                                     {'placement': 'top'})
        # 推进到回合末：维护窗口继承扣款后的 pool（无 H）
        ptr = session._previous_turn_result
        self.assertEqual(ptr['maintenance_result']['remaining_resources'],
                         {'BL': 3})

    def test_runtime_skip_keeps_duration_and_pool(self):
        session, ready = self.shorten_session(stable={'BL': 1, 'H': 1})
        shorten = self.submit_plan(
            session, ready, self.find_plan(session, ('YH-01',)))['decision']
        result = session.submit_action(shorten['decision_id'],
                                       {'choice': 'skip'})
        self.assertTrue(result['ok'])
        self.assertEqual(session.game.debuff_turns_remaining, 3)
        self.assertEqual(session.game.pool.get('H', 0), 1)
        self.assertEqual(result['decision']['kind'], 'placement_decision')
        done = session.submit_action(result['decision']['decision_id'],
                                     {'placement': 'top'})
        ptr = session._previous_turn_result
        self.assertEqual(ptr['maintenance_result']['remaining_resources'],
                         {'BL': 3, 'H': 1})

    def test_runtime_no_decision_without_h(self):
        # H 全部用于购买：无缩短决策，自动按跳过继续
        session, ready = self.shorten_session(stable={'BL': 1})
        result = self.submit_plan(session, ready,
                                  self.find_plan(session, ('YH-01',)))
        self.assertNotEqual(result['decision']['kind'],
                            'debuff_shorten_decision')
        self.assertEqual(session.game.debuff_turns_remaining, 3)

    def test_shorten_pending_requires_fresh_draw_this_turn(self):
        session = GameSession(seed=1, shuffle=False, forced_goals=[1, 2])
        game = session.game
        game.childhood_complete = True
        game.turn = 2
        game.pre_roll_open = False
        game.cv['H'] = ['OH-01']
        game.pool = Counter({'H': 2})
        session._purchase_executed = True
        session._debuff_resolved = True
        # 上一回合抽取（本回合生效）：不提供缩短
        game.current_debuff = 'D01'
        game.debuff_active_from_turn = 2
        self.assertFalse(session._debuff_shorten_pending())
        # 本回合刚抽取：提供
        game.debuff_active_from_turn = 3
        self.assertTrue(session._debuff_shorten_pending())
        # H 不足：不提供
        game.pool = Counter({'K': 2})
        self.assertFalse(session._debuff_shorten_pending())
        # 已处理过：不提供
        game.pool = Counter({'H': 2})
        session._debuff_shorten_resolved = True
        self.assertFalse(session._debuff_shorten_pending())

    def test_runtime_stale_illegal_and_repeat_are_side_effect_free(self):
        session, ready = self.shorten_session(stable={'BL': 1, 'H': 1})
        shorten = self.submit_plan(
            session, ready, self.find_plan(session, ('YH-01',)))['decision']
        before = (dict(session.game.pool), session.game.debuff_turns_remaining)
        # 非法动作
        result = session.submit_action(shorten['decision_id'],
                                       {'choice': 'use'})
        self.assertEqual(result['error'], 'illegal_action')
        # stale decision_id
        result = session.submit_action('debuff_shorten_decision:9:9:9',
                                       {'choice': 'pay'})
        self.assertEqual(result['error'], 'stale_or_unknown_decision_id')
        self.assertEqual((dict(session.game.pool),
                          session.game.debuff_turns_remaining), before)
        # 正常 pay 后重复提交同一动作 → stale
        result = session.submit_action(shorten['decision_id'],
                                       {'choice': 'pay'})
        self.assertTrue(result['ok'])
        result = session.submit_action(shorten['decision_id'],
                                       {'choice': 'pay'})
        self.assertEqual(result['error'], 'stale_or_unknown_decision_id')
        self.assertEqual(session.game.debuff_turns_remaining, 2)
        self.assertEqual(session.game.pool.get('H', 0), 0)


class TestJITPresentation(unittest.TestCase):
    """JIT 展示层：首次遇见说明 / seen_rule_hints / 支付 provenance。"""

    def make_turn_session(self, turn=2, dice=('H', 'K', 'R', 'M'), stacks=(),
                          hand=(), market=None, market_entry=None,
                          fate=None, current_debuff=None):
        session = GameSession(seed=19, shuffle=False, forced_goals=[1, 2])
        game = session.game
        game.childhood_complete = True
        game.turn = turn
        game.hand = list(hand)
        for cls, cards in stacks:
            game.cv[cls] = list(cards)
        session._turn_closeout_resolved = True
        session._next_turn_started = True
        game.pre_roll_open = False
        game.dice = list(dice)
        session._previous_turn_result = {
            'completed_turn': turn - 1, 'next_turn': turn,
            'maintenance_result': {'paid': []},
            'market_cleanup_result': {'purchased_card_ids': []},
        }
        if market is not None:
            game.market = list(market)
        if market_entry is not None:
            game.market_entry = dict(market_entry)
        if fate is not None:
            game.fate_stack = [fate]
            game.active_fate = fate
            game.fate_active_from_turn = turn
        if current_debuff is not None:
            game.current_debuff = current_debuff
            game.debuff_active_from_turn = turn
            game.debuff_turns_remaining = 3
        game._forced = list(dice) + ['H'] * 20
        session._rerolls_remaining = 2
        return session

    def post_roll(self, session):
        decision = session.current_decision()
        self.assertEqual(decision['kind'], 'post_roll_decision')
        return decision

    # ---------- 市场牌首见 / 驻留 ----------

    def test_market_summary_only_on_entry_turn(self):
        session = self.make_turn_session(
            turn=2, market=['YH-01', 'YH-02'],
            market_entry={'YH-01': 1, 'YH-02': 0})
        cards = {c['id']: c for c in self.post_roll(session)[
            'current_opportunities']}
        # YH-01 本回合进场（entry == turn-1）：附效果说明
        self.assertIn('effect_summary', cards['YH-01'])
        self.assertIn('稳定产出', cards['YH-01']['effect_summary'])
        # YH-02 上回合已在场：驻留期不重复说明
        self.assertNotIn('effect_summary', cards['YH-02'])

    def test_c12_reroll_instruction_exposes_complete_action_shape(self):
        session = self.make_turn_session(dice=('BL', 'H', 'K', 'M'), hand=('C12',))
        session.game.abebe_held = True
        decision = self.post_roll(session)
        instruction = decision['action_instruction']
        for text in ('normal_reroll', 'indices 是本次完整重掷集合',
                     'c12_index 必须同时出现在 indices',
                     '指向被冻结的 BL', '其他普通非 BL 骰',
                     'special_reroll 不适用于 C12',
                     '{"choice":"normal_reroll","indices":[0,2],"c12_index":0}'):
            self.assertIn(text, instruction)
        normal = next(action for action in decision['legal_actions']
                      if action['choice'] == 'normal_reroll')
        self.assertIn('c12_note', normal)
        self.assertIn('c12_index 必须在 indices 中', normal['c12_note'])
        self.assertIn('c12_reroll', decision['rule_hints'])
        self.assertEqual(session.current_decision(), decision)

    def test_draft_candidates_and_random_third_carry_effects(self):
        # 自然 seed：目标即 [16, 9]，且 pick_1 候选 C10/C04/C07
        session = GameSession(seed=20260914)
        decision = session.current_decision()
        self.assertEqual(decision['kind'], 'childhood_pick_1')
        for card in decision['candidates']:
            self.assertIn('effect_summary', card)
        # 目标首次公开：Draft 阶段即带口径句
        text_by_id = {g['id']: g.get('scoring_text')
                      for g in decision['life_goals']}
        self.assertIn('只读 active 卡', text_by_id[16])
        self.assertIn('资源库存不计', text_by_id[16])
        session.submit_action(decision['decision_id'], {'card_id': 'C10'})
        second = session.current_decision()
        session.submit_action(second['decision_id'], {'card_id': 'C09'})
        third_id = session.game.childhood_kept[2]
        done = session.current_decision()
        # Payload Slim v1：随机第三张的效果改由 childhood_cards 条目可读
        third = next(c for c in done['childhood_cards']
                     if c['card_id'] == third_id)
        self.assertTrue(third['effect_summary'])

    def test_lg09_and_lg16_scoring_text(self):
        session = GameSession(seed=20260914, forced_goals=[9, 16])
        decision = session.current_decision()
        text = {g['id']: g['scoring_text'] for g in decision['life_goals']}
        # LG9 cv_min：计全部持卡（含被顶替/埋藏）
        self.assertIn('最小值', text[9])
        self.assertIn('含被顶替/埋藏', text[9])
        # LG16 provide_amount：只读终局 active 顶卡产出，库存不计
        self.assertIn('只读 active 卡', text[16])
        self.assertIn('资源库存不计', text[16])

    def test_lg_text_only_on_first_publication(self):
        first = self.make_turn_session(turn=1)
        d1 = self.post_roll(first)
        self.assertIn('scoring_text', d1['life_goals'][0])
        later = self.make_turn_session(turn=2)
        d2 = self.post_roll(later)
        self.assertNotIn('scoring_text', d2['life_goals'][0])
        self.assertIn('scoring_rule', d2['life_goals'][0])

    # ---------- 通用机制 hint once ----------

    def test_gl_bl_and_lifetime_hints_appear_once(self):
        session = self.make_turn_session(dice=('GL', 'K', 'H', 'M'))
        decision = self.post_roll(session)
        self.assertEqual(sorted(decision['rule_hints']),
                         ['gl_bl', 'market_flow', 'resource_lifetime'])
        self.assertEqual(session.seen_rule_hints, set())
        # 重读不消耗：两次读取逐字段一致
        again = session.current_decision()
        self.assertEqual(again, decision)
        self.assertEqual(session.seen_rule_hints, set())
        # 非法提交不标记
        bad = session.submit_action('post_roll_decision:9:9:9',
                                    {'choice': 'proceed_to_purchase'})
        self.assertEqual(bad['error'], 'stale_or_unknown_decision_id')
        self.assertEqual(session.seen_rule_hints, set())
        # 成功提交后才标 seen
        ok = session.submit_action(decision['decision_id'],
                                   {'choice': 'proceed_to_purchase'})
        self.assertTrue(ok['ok'])
        self.assertEqual(session.seen_rule_hints,
                         {'gl_bl', 'market_flow', 'resource_lifetime'})
        # purchase_ready 不再重复
        self.assertNotIn('rule_hints', ok['decision'])

    def test_dice_without_gl_bl_shows_only_lifetime_hint(self):
        session = self.make_turn_session(dice=('H', 'K', 'R', 'M'))
        decision = self.post_roll(session)
        self.assertEqual(sorted(decision['rule_hints']),
                         ['market_flow', 'resource_lifetime'])

    def test_market_flow_hint_is_explicit_and_consumed_once(self):
        session = self.make_turn_session(dice=('H', 'K', 'R', 'M'))
        decision = self.post_roll(session)
        text = decision['rule_hints']['market_flow']
        self.assertEqual(
            text,
            '普通市场每回合总离场固定3张（含本轮购买）；因此买0/1/2张时，'
            '系统还会淘汰3/2/1张。具体淘汰对象由后台裁决。')
        self.assertNotIn('随机', text)
        self.assertNotIn('市场顺序', text)
        self.assertNotIn('概率', text)
        self.assertEqual(session.current_decision(), decision)
        self.assertNotIn('market_flow', session.seen_rule_hints)

        accepted = session.submit_action(
            decision['decision_id'], {'choice': 'proceed_to_purchase'})
        self.assertTrue(accepted['ok'])
        self.assertIn('market_flow', session.seen_rule_hints)
        self.assertNotIn('market_flow', accepted['decision'].get('rule_hints', {}))

    def test_market_flow_hint_appears_when_purchase_is_immediate(self):
        session = self.make_turn_session(dice=('H', 'K', 'R', 'M'))
        session._rerolls_remaining = 0
        decision = session.current_decision()
        self.assertEqual(decision['kind'], 'purchase_ready')
        self.assertIn('market_flow', decision['rule_hints'])
        self.assertIn('买0/1/2张时', decision['rule_hints']['market_flow'])

    def test_placement_hint_once(self):
        session = self.make_turn_session(turn=2, dice=('H', 'K', 'R', 'M'))
        game = session.game
        session._purchase_executed = True
        session._debuff_resolved = True
        game.pending_new = ['YH-01']
        first = session.current_decision()
        self.assertEqual(first['kind'], 'placement_decision')
        self.assertEqual(list(first['rule_hints']), ['placement'])
        self.assertIn('effect_summary', first['card'])
        self.assertTrue(session.submit_action(first['decision_id'],
                                              {'placement': 'top'})['ok'])
        self.assertIn('placement', session.seen_rule_hints)
        # 第二张：重建同时点状态（seen 保持），验证 hint 只出现一次
        game.turn = 2
        game.pending_new = ['YH-03']
        session._purchase_executed = True
        session._debuff_resolved = True
        session._debuff_shorten_resolved = False
        session._maintenance_resolved = False
        session._market_cleanup_resolved = False
        second = session.current_decision()
        self.assertEqual(second['kind'], 'placement_decision')
        self.assertNotIn('rule_hints', second)

    def test_maintenance_hint_once(self):
        # MW-02(H1) 与 YR-05(M1) 只付得起一个：OH-05 的维护替代让两条
        # 部分方案并存 → 真正的 maintenance_decision 而非自动执行
        session = self.make_turn_session(
            turn=2,
            stacks=[('W', ['MW-02']), ('H', ['OH-05']), ('R', ['YR-05'])])
        game = session.game
        game.pool = Counter({'H': 1})
        session._purchase_executed = True
        session._debuff_resolved = True
        decision = session.current_decision()
        self.assertEqual(decision['kind'], 'maintenance_decision')
        self.assertEqual(list(decision['rule_hints']), ['maintenance'])
        self.assertGreater(len(decision['legal_maintenance_plans']), 1)
        plan = decision['legal_maintenance_plans'][0]
        self.assertTrue(session.submit_action(decision['decision_id'],
                                              {'plan_id': plan['plan_id']})['ok'])
        self.assertIn('maintenance', session.seen_rule_hints)

    def test_fate_active_slot_hint_and_persistent_visibility(self):
        session = self.make_turn_session(fate='F06')
        decision = self.post_roll(session)
        self.assertEqual(decision['active_fate']['id'], 'F06')
        self.assertIn('effect_summary', decision['active_fate'])
        self.assertEqual(sorted(decision['rule_hints']),
                         ['fate_active_slot', 'market_flow', 'resource_lifetime'])
        ok = session.submit_action(decision['decision_id'],
                                   {'choice': 'proceed_to_purchase'})
        self.assertTrue(ok['ok'])
        self.assertIn('fate_active_slot', session.seen_rule_hints)
        # hint 不再出现，但 active Fate 身份持续可见
        self.assertNotIn('rule_hints', ok['decision'])
        self.assertEqual(ok['decision']['active_fate']['id'], 'F06')

    def test_current_debuff_always_exposes_card_and_effects(self):
        session = self.make_turn_session(current_debuff='D09')
        decision = self.post_roll(session)
        debuff = decision['current_debuff']
        self.assertEqual(debuff['card_id'], 'D09')
        self.assertEqual(debuff['card']['id'], 'D09')
        self.assertIn('无法取得', debuff['card']['effect_summary'])

    def test_special_notes_f10_and_c12(self):
        session = self.make_turn_session(turn=3, fate='F10',
                                         market_entry={})
        session.game.fate_market = ['F10']
        presented = session._presented_card('F10')
        self.assertIn('特殊：', presented['special_note'])
        self.assertIn('3 GL', presented['special_note'])
        presented_c12 = session._presented_card('C12')
        self.assertIn('一生一次', presented_c12['special_note'])
        # Fate 市场牌常驻效果说明 + 特注
        cards = {c['id']: c for c in self.post_roll(session)[
            'current_opportunities']}
        self.assertIn('special_note', cards['F10'])
        self.assertIn('effect_summary', cards['F10'])

    # ---------- gl_bl 触发范围扩展 ----------

    def test_gl_bl_fires_for_c05_before_any_gl_die(self):
        # C05 在手（效果涉及 GL）、骰面无 GL/BL：仍应触发
        session = self.make_turn_session(hand=['C05'],
                                         dice=('H', 'K', 'R', 'M'))
        decision = self.post_roll(session)
        self.assertIn('gl_bl', decision['rule_hints'])

    def test_gl_bl_fires_on_fate_market_cost(self):
        # turn 3 Fate 窗口，F01 成本 GL×1，骰面与池均无 GL/BL
        session = self.make_turn_session(turn=3, market_entry={},
                                         dice=('H', 'K', 'R', 'M'))
        session.game.fate_market = ['F01']
        decision = self.post_roll(session)
        self.assertIn('gl_bl', decision['rule_hints'])

    def test_gl_bl_fires_for_stable_gl(self):
        # YR-02 生效提供 GL×1 → 可用资源含 GL
        session = self.make_turn_session(stacks=[('R', ['YR-02'])],
                                         dice=('H', 'K', 'R', 'M'))
        session.game.stable_pool = Counter({'GL': 1})
        decision = self.post_roll(session)
        self.assertIn('gl_bl', decision['rule_hints'])

    def test_gl_bl_fires_for_c05_draft_candidate(self):
        # seed 12：pick_1 候选即含 C05
        session = GameSession(seed=12)
        decision = session.current_decision()
        self.assertIn('C05', [c['id'] for c in decision['candidates']])
        self.assertIn('gl_bl', decision['rule_hints'])
        result = session.submit_action(decision['decision_id'],
                                       {'card_id': 'C05'})
        self.assertIn('gl_bl', session.seen_rule_hints)
        self.assertNotIn('gl_bl', result['decision'].get('rule_hints', {}))

    def test_gl_bl_seen_not_repeated_for_c05(self):
        session = self.make_turn_session(hand=['C05'],
                                         dice=('H', 'K', 'R', 'M'))
        first = self.post_roll(session)
        self.assertIn('gl_bl', first['rule_hints'])
        ok = session.submit_action(first['decision_id'],
                                   {'choice': 'proceed_to_purchase'})
        self.assertIn('gl_bl', session.seen_rule_hints)
        # 已 seen：C05 仍在手也不重复
        self.assertNotIn('gl_bl', ok['decision'].get('rule_hints', {}))

    def test_gl_bl_absent_when_nothing_gl_bl_visible(self):
        session = self.make_turn_session(dice=('H', 'K', 'R', 'M'))
        decision = self.post_roll(session)
        self.assertNotIn('gl_bl', decision['rule_hints'])
        self.assertIn('resource_lifetime', decision['rule_hints'])


    # ---------- L1 支付 provenance ----------

    def test_l1_payment_provenance_gl_free_with_c05(self):
        # 复现 seed 20260914 第 1 回合：2 骰 GL + C05 临时 GL → 3GL 免费取得
        session = GameSession(seed=20260914)
        first = session.current_decision()
        session.submit_action(first['decision_id'], {'card_id': 'C10'})
        second = session.current_decision()
        session.submit_action(second['decision_id'], {'card_id': 'C09'})
        d = session.current_decision()
        self.assertEqual(list(d['dice']), ['H', 'BL', 'M', 'M'])
        r = session.submit_action(d['decision_id'],
                                  {'choice': 'normal_reroll', 'indices': [0, 2, 3]})
        d = r['decision']
        r = session.submit_action(d['decision_id'],
                                  {'choice': 'normal_reroll', 'indices': [0, 2, 3]})
        ready = r['decision']
        self.assertEqual(ready['kind'], 'purchase_ready')
        targets = {tuple(t['ordinary_card_ids']): t
                   for t in ready['purchase_targets']}
        yw03 = targets[('YW-03',)]
        self.assertEqual(yw03['payment_method'], 'gl_free_acquisition')
        self.assertIn('3 GL 免费取得', yw03['payment_summary'])
        self.assertIn('C05', yw03['payment_summary'])

class TestSpectatorSnapshot(unittest.TestCase):
    def make_session(self):
        session = GameSession(seed=19, shuffle=False, forced_goals=[1, 2])
        game = session.game
        game.childhood_complete = True
        game.stage = 'middle'
        game.turn = 10
        game.pre_roll_open = False
        game.dice = ['H', 'BL', 'K', 'M']
        game.market = ['MH-01', 'ME-01']
        game.fate_market = ['F01']
        game.hand = ['YE-01']
        game.current_debuff = 'D06'
        game.debuff_active_from_turn = 10
        game.debuff_turns_remaining = 2
        game.cv['H'] = ['YH-01', 'YH-02']
        game.cv['K'] = ['YK-01']
        session._previous_turn_result = {'completed_turn': 9}
        session._turn_closeout_resolved = True
        session._next_turn_started = True
        session._rerolls_remaining = 2
        return session

    def runtime_state(self, session):
        game = session.game
        return copy.deepcopy((
            game.turn, game.stage, game.game_over, game.dice, game.market,
            game.fate_market, game.hand, game.cv, game.current_debuff,
            game.debuff_turns_remaining, game.rng.getstate(),
            session._decision_revision, session._dice_roll_revision,
            session._previous_turn_result,
            session._rerolls_remaining, session._purchase_ready,
            session._purchase_executed, session._turn_closeout_resolved,
            session._next_turn_started,
            session._recent_events, session._next_recent_event_seq,
        ))

    def test_opening_snapshot_is_json_ready_and_never_enters_decision_flow(self):
        session = GameSession(seed=1, shuffle=False, forced_goals=[1, 2])
        before = self.runtime_state(session)
        with patch.object(session, 'current_decision',
                          side_effect=AssertionError('must not be called')), \
             patch.object(session, '_auto_advance',
                          side_effect=AssertionError('must not be called')):
            first = session.spectator_snapshot()
            second = session.spectator_snapshot()

        self.assertEqual(first, second)
        self.assertEqual(self.runtime_state(session), before)
        self.assertEqual(first['status'], 'in_progress')
        self.assertFalse(first['game_over'])
        self.assertEqual(first['current_turn'], 0)
        self.assertEqual(first['completed_turn'], 0)
        self.assertEqual(first['player_identity'], {
            'name': 'AI玩家', 'emoji': '🤖'})
        self.assertEqual(first['dice']['values'], [])
        self.assertEqual(first['dice']['frozen_indices'], [])
        self.assertEqual(first['dice']['roll_revision'], 0)
        self.assertEqual(first['recent_events'], [{
            'seq': 1, 'type': 'game_started', 'turn': 0,
            'stage': 'youth', 'text': '游戏开始',
        }])
        self.assertEqual(json.loads(json.dumps(first)), first)

    def test_snapshot_projects_display_only_player_identity(self):
        session = GameSession(seed=1, shuffle=False, forced_goals=[1, 2],
                              player_name='阿屿', player_emoji='🦊')
        before = self.runtime_state(session)

        snapshot = session.spectator_snapshot()

        self.assertEqual(snapshot['player_identity'], {
            'name': '阿屿', 'emoji': '🦊'})
        self.assertEqual(self.runtime_state(session), before)

    def test_snapshot_projects_live_market_hand_debuff_dice_and_cv_stack(self):
        session = self.make_session()
        before = self.runtime_state(session)
        snapshot = session.spectator_snapshot()

        self.assertEqual(snapshot['stage'], 'middle')
        self.assertEqual(snapshot['current_turn'], 10)
        self.assertEqual(snapshot['completed_turn'], 9)
        self.assertEqual([card['card_id'] for card in
                          snapshot['opportunity_market']], ['MH-01', 'ME-01'])
        self.assertEqual(len(snapshot['opportunity_market']), 2)
        self.assertEqual([card['card_id'] for card in
                          snapshot['fate_market']['cards']], ['F01'])
        self.assertFalse(snapshot['fate_market']['is_open'])
        self.assertEqual([card['card_id'] for card in snapshot['event_hand']],
                         ['YE-01'])
        self.assertTrue(snapshot['event_hand'][0]['effect_summary'])
        self.assertEqual(snapshot['current_debuff']['card_id'], 'D06')
        self.assertEqual(snapshot['current_debuff']['name'], '睡眠不足')
        self.assertEqual(snapshot['current_debuff']['active_from_turn'], 10)
        self.assertEqual(snapshot['current_debuff']['turns_remaining'], 2)
        self.assertIn('正常重掷轮数 -1',
                      snapshot['current_debuff']['effect_summary'])
        self.assertEqual(snapshot['dice']['values'], ['H', 'BL', 'K', 'M'])
        self.assertEqual(snapshot['dice']['max_dice_count'], 7)
        self.assertEqual(snapshot['dice']['frozen_indices'], [1])
        self.assertEqual(snapshot['cv']['H']['top_card_id'], 'YH-02')
        self.assertEqual([card['card_id'] for card in
                          snapshot['cv']['H']['stack']], ['YH-01', 'YH-02'])
        self.assertIsNone(snapshot['cv']['R']['top_card_id'])
        self.assertEqual(snapshot['cv']['R']['stack'], [])
        self.assertEqual(snapshot['recent_events'][0]['type'], 'game_started')
        self.assertEqual([goal['id'] for goal in snapshot['life_goals']], [1, 2])
        self.assertTrue(all(goal['scoring_text'] for goal in
                            snapshot['life_goals']))
        self.assertEqual(self.runtime_state(session), before)
        self.assertEqual(json.loads(json.dumps(snapshot)), snapshot)

    def test_non_reroll_state_has_no_effective_frozen_indices(self):
        session = self.make_session()
        session._purchase_ready = True
        snapshot = session.spectator_snapshot()

        self.assertEqual(snapshot['dice']['values'], ['H', 'BL', 'K', 'M'])
        self.assertEqual(snapshot['dice']['frozen_indices'], [])


class TestSpectatorDiceRollRevision(unittest.TestCase):
    def _post_roll_session(self, dice=('H', 'K', 'R', 'M')):
        session = GameSession(seed=1, shuffle=False, forced_goals=[1, 2])
        first = session.current_decision()
        second = session.submit_action(first['decision_id'], {
            'card_id': 'C12'})['decision']
        session.game._forced = list(dice) + ['H'] * 20
        result = session.submit_action(second['decision_id'], {'card_id': 'C09'})
        self.assertTrue(result['ok'])
        return session, result['decision']

    def test_snapshot_starts_at_zero_and_initial_roll_increments_once(self):
        session = GameSession(seed=1, shuffle=False, forced_goals=[1, 2])
        self.assertEqual(session.spectator_snapshot()['dice']['roll_revision'], 0)

        first = session.current_decision()
        second = session.submit_action(first['decision_id'], {
            'card_id': 'C12'})['decision']
        session.game._forced = ['H', 'K', 'R', 'M']
        result = session.submit_action(second['decision_id'], {'card_id': 'C09'})

        self.assertTrue(result['ok'])
        self.assertEqual(session.spectator_snapshot()['dice']['roll_revision'], 1)

    def test_normal_reroll_increments_even_when_face_is_unchanged(self):
        session, decision = self._post_roll_session()
        before = list(session.game.dice)
        session.game._forced = [before[0]]

        result = session.submit_action(decision['decision_id'], {
            'choice': 'normal_reroll', 'indices': [0], 'use_ye03': False,
            'use_c11': False, 'c12_index': None})

        self.assertTrue(result['ok'])
        self.assertEqual(session.game.dice, before)
        self.assertEqual(session.spectator_snapshot()['dice']['roll_revision'], 2)

    def test_rejected_reroll_does_not_increment(self):
        session, decision = self._post_roll_session()
        result = session.submit_action(decision['decision_id'], {
            'choice': 'normal_reroll', 'indices': [99], 'use_ye03': False,
            'use_c11': False, 'c12_index': None})

        self.assertFalse(result['ok'])
        self.assertEqual(session.spectator_snapshot()['dice']['roll_revision'], 1)

    def test_non_random_and_deterministic_dice_actions_do_not_increment(self):
        session, decision = self._post_roll_session()
        result = session.submit_action(decision['decision_id'], {
            'choice': 'proceed_to_purchase'})
        self.assertTrue(result['ok'])
        self.assertEqual(session.spectator_snapshot()['dice']['roll_revision'], 1)

        session, _ = self._post_roll_session(('BL', 'BL', 'H', 'K'))
        session.game.cv['H'] = ['MH-04']
        session.game.mh04_trigger_seen = False
        session.game._after_dice_change()
        reaction = session.current_decision()
        self.assertEqual(reaction['kind'], 'mh04_decision')
        result = session.submit_action(reaction['decision_id'], {
            'choice': 'use', 'target_index': 0})

        self.assertTrue(result['ok'])
        self.assertEqual(session.game.dice[0], 'H')
        self.assertEqual(session.spectator_snapshot()['dice']['roll_revision'], 1)

    def test_special_reroll_and_c11_added_dice_each_increment_once(self):
        session, decision = self._post_roll_session()
        session.game.cv['K'] = ['MK-02']
        session.game._forced = ['H']
        result = session.submit_action(decision['decision_id'], {
            'choice': 'special_reroll', 'ability_card_id': 'MK-02',
            'die_index': 0, 'use_ye03': False})

        self.assertTrue(result['ok'])
        self.assertEqual(session.spectator_snapshot()['dice']['roll_revision'], 2)

        session, decision = self._post_roll_session()
        session.game.hand.append('C11')
        session.game._forced = ['H', 'K']
        result = session.submit_action(decision['decision_id'], {
            'choice': 'normal_reroll', 'indices': [], 'use_ye03': False,
            'use_c11': True, 'c12_index': None})

        self.assertTrue(result['ok'])
        self.assertEqual(len(session.game.dice), 6)
        self.assertEqual(session.spectator_snapshot()['dice']['roll_revision'], 2)


class TestSpectatorRecentEvents(unittest.TestCase):
    def _adult_post_roll_session(self):
        session = GameSession(seed=1, shuffle=False, forced_goals=[1, 2])
        first = session.current_decision()
        second = session.submit_action(first['decision_id'], {
            'card_id': 'C12'})['decision']
        session.game._forced = ['H', 'K', 'R', 'M']
        post_roll = session.submit_action(second['decision_id'], {
            'card_id': 'C09'})['decision']
        return session, post_roll

    def test_game_started_snapshot_copy_and_bounded_monotonic_buffer(self):
        session = GameSession(seed=1, shuffle=False, forced_goals=[1, 2])
        self.assertEqual([event['type'] for event in session._recent_events],
                         ['game_started'])
        before = copy.deepcopy((session._recent_events,
                                session._next_recent_event_seq))
        first = session.spectator_snapshot()
        second = session.spectator_snapshot()
        self.assertEqual(first['recent_events'], second['recent_events'])
        self.assertEqual((session._recent_events, session._next_recent_event_seq),
                         before)
        first['recent_events'][0]['text'] = 'mutated outside'
        self.assertEqual(session._recent_events[0]['text'], '游戏开始')

        for index in range(22):
            session._append_recent_event('turn_completed', 'event %d' % index)
        self.assertEqual(len(session._recent_events), 20)
        self.assertEqual([event['seq'] for event in session._recent_events],
                         list(range(4, 24)))

    def test_first_roll_and_successful_reroll_each_record_once(self):
        session, post_roll = self._adult_post_roll_session()
        self.assertEqual([event['type'] for event in session._recent_events].count(
            'dice_rolled'), 1)
        result = session.submit_action(post_roll['decision_id'], {
            'choice': 'normal_reroll', 'indices': [0], 'use_ye03': False,
            'use_c11': False, 'c12_index': None})
        self.assertTrue(result['ok'])
        rerolls = [event for event in session._recent_events
                   if event['type'] == 'rerolled']
        self.assertEqual(len(rerolls), 1)
        self.assertEqual(rerolls[0]['details']['reroll_count'], 1)
        before = copy.deepcopy(session._recent_events)
        rejected = session.submit_action(post_roll['decision_id'], {
            'choice': 'normal_reroll', 'indices': [0], 'use_ye03': False,
            'use_c11': False, 'c12_index': None})
        self.assertFalse(rejected['ok'])
        self.assertEqual(session._recent_events, before)

    def test_debuff_draw_and_replacement_record_one_event_each(self):
        session = GameSession(seed=1, shuffle=False, forced_goals=[1, 2])
        game = session.game
        game.childhood_complete = True
        game.turn = 2
        game.pre_roll_open = False
        game.bad_luck_accumulator = 5
        game.debuff_deck = ['D01', 'D02']

        self.assertEqual(session._resolve_runtime_debuff(), 'drawn')
        first = session._recent_events[-1]
        self.assertEqual(first['type'], 'debuff_started')
        self.assertEqual(first['details']['card_id'], 'D01')
        self.assertNotIn('replaced_card_id', first['details'])

        game.bad_luck_accumulator = 5
        self.assertEqual(session._resolve_runtime_debuff(), 'drawn')
        second = session._recent_events[-1]
        self.assertEqual(second['type'], 'debuff_started')
        self.assertEqual(second['details'], {
            'card_id': 'D02', 'replaced_card_id': 'D01'})
        self.assertEqual([event['type'] for event in session._recent_events].count(
            'debuff_started'), 2)

    def test_real_purchase_and_non_immediate_fate_each_record_once(self):
        session = GameSession(seed=1, shuffle=False, forced_goals=[1, 2])
        first = session.current_decision()
        second = session.submit_action(first['decision_id'], {
            'card_id': 'C12'})['decision']
        session.game._forced = ['H', 'H', 'BL', 'BL']
        post_roll = session.submit_action(second['decision_id'], {
            'card_id': 'C09'})['decision']
        session.game.market = ['YH-01']
        session.game.market_entry = {'YH-01': session.game.turn}
        ready = session.submit_action(post_roll['decision_id'], {
            'choice': 'proceed_to_purchase'})['decision']
        purchased = session.submit_action(ready['decision_id'], {
            'ordinary_card_ids': ['YH-01'], 'fate_card_id': None})
        if 'selected_purchase_target' in purchased['decision']:
            plan = next(plan for plan in session._purchase_plan_cache
                        if plan.get('ordinary_card_ids', plan.get('card_ids'))
                        == ['YH-01'])
            purchased = session.submit_action(
                purchased['decision']['decision_id'], {'plan_id': plan['plan_id']})
        self.assertTrue(purchased['ok'])
        card_events = [event for event in session._recent_events
                       if event['type'] == 'card_acquired']
        self.assertEqual(len(card_events), 1)
        self.assertEqual(card_events[0]['details']['card_ids'], ['YH-01'])

        fate_session = GameSession(seed=1, shuffle=False, forced_goals=[1, 2])
        first = fate_session.current_decision()
        second = fate_session.submit_action(first['decision_id'], {
            'card_id': 'C12'})['decision']
        fate_session.game._forced = ['GL', 'BL', 'H', 'K']
        fate_session.submit_action(second['decision_id'], {'card_id': 'C09'})
        fate_session.game.turn = 3
        fate_session.game.fate_market = ['F07']
        fate_session._enter_purchase_ready()
        ready = fate_session.current_decision()
        resolved = fate_session.submit_action(ready['decision_id'], {
            'ordinary_card_ids': [], 'fate_card_id': 'F07'})
        self.assertTrue(resolved['ok'])
        fate_events = [event for event in fate_session._recent_events
                       if event['type'] == 'fate_resolved']
        self.assertEqual(len(fate_events), 1)
        self.assertEqual(fate_events[0]['details']['card_id'], 'F07')

        immediate_session = GameSession(seed=1, shuffle=False,
                                        forced_goals=[1, 2])
        first = immediate_session.current_decision()
        second = immediate_session.submit_action(first['decision_id'], {
            'card_id': 'C12'})['decision']
        immediate_session.game._forced = ['GL', 'BL', 'H', 'K']
        immediate_session.submit_action(second['decision_id'], {
            'card_id': 'C09'})
        immediate_session.game.turn = 3
        immediate_session.game.fate_market = ['F01']
        immediate_session.game.cv['H'] = ['YH-01']
        immediate_session._enter_purchase_ready()
        ready = immediate_session.current_decision()
        pending = immediate_session.submit_action(ready['decision_id'], {
            'ordinary_card_ids': [], 'fate_card_id': 'F01'})['decision']
        self.assertEqual(pending['kind'], 'fate_immediate_decision')
        self.assertEqual([event['type'] for event in
                          immediate_session._recent_events].count(
            'fate_resolved'), 0)
        completed = immediate_session.submit_action(pending['decision_id'], {
            'card_id': 'YH-01'})
        self.assertTrue(completed['ok'])
        self.assertEqual([event['type'] for event in
                          immediate_session._recent_events].count(
            'fate_resolved'), 1)

    def test_stage_change_and_closeout_events_use_completed_results_once(self):
        session = GameSession(seed=1, shuffle=False, forced_goals=[1, 2])
        game = session.game
        game.stage = 'youth'
        with patch.object(game, 'cleanup_normal_market') as cleanup:
            def change_stage(*args, **kwargs):
                game.stage = 'middle'
                return {}
            cleanup.side_effect = change_stage
            self.assertEqual(session._cleanup_normal_market(), {})
        stage_events = [event for event in session._recent_events
                        if event['type'] == 'stage_changed']
        self.assertEqual(stage_events[-1]['details'], {
            'from_stage': 'youth', 'to_stage': 'middle'})

        previous = {
            'completed_turn': 23,
            'next_turn': None,
            'debuff_lifecycle': {'archived_card_id': 'D01'},
            'game_over': True,
        }
        with patch.object(game, 'closeout_adult_turn', return_value=previous):
            game.game_over = True
            session._market_cleanup_resolved = True
            session._auto_advance()
        types = [event['type'] for event in session._recent_events]
        self.assertEqual(types[-3:], ['debuff_expired', 'turn_completed',
                                      'game_over'])
        before = copy.deepcopy(session._recent_events)
        session._auto_advance()
        self.assertEqual(session._recent_events, before)


class TestCardCatalog(unittest.TestCase):
    """正式 Card Catalog 只读投影：唯一来源 CARDS，展示专用，无副作用。"""

    def setUp(self):
        self.catalog = card_catalog()
        self.cards = self.catalog['cards']
        self.by_id = {card['card_id']: card for card in self.cards}
        self.base_fields = {'card_id', 'name', 'type', 'stage', 'cost',
                            'vp', 'effect_summary', 'details'}

    def test_catalog_is_derived_from_official_cards_exactly(self):
        self.assertEqual(len(CARDS), 106)
        self.assertEqual(len(self.cards), 106)
        ids = [card['card_id'] for card in self.cards]
        self.assertEqual(len(set(ids)), 106)
        self.assertEqual(set(ids), set(CARDS))
        for card in self.cards:
            original = CARDS[card['card_id']]
            self.assertEqual(card['name'], original['name'])
            self.assertEqual(card['type'], original['type'])
            self.assertEqual(card['stage'], original['stage'])
            self.assertEqual(card['cost'], original['cost'])
            self.assertEqual(card['vp'], original['vp'])

    def test_all_nine_types_are_present(self):
        self.assertEqual({card['type'] for card in self.cards},
                         {'H', 'K', 'R', 'W', 'P', 'E', 'C', 'D', 'F'})

    def test_every_card_has_uniform_base_schema(self):
        for card in self.cards:
            self.assertEqual(set(card), self.base_fields)

    def test_vp_zero_is_kept_instead_of_dropped(self):
        zero_vp = [card for card in self.cards if card['vp'] == 0]
        self.assertTrue(zero_vp)
        for card in zero_vp:
            self.assertIn('vp', card)
            self.assertEqual(card['vp'], 0)

    def test_missing_stage_is_returned_as_null(self):
        self.assertIsNone(self.by_id['C01']['stage'])
        self.assertIsNone(self.by_id['D01']['stage'])
        self.assertIsNone(self.by_id['F01']['stage'])
        for card in self.cards:
            if CARDS[card['card_id']]['stage'] is None:
                self.assertIn('stage', card)
                self.assertIsNone(card['stage'])

    def test_effect_summary_reuses_official_shared_logic(self):
        for card in self.cards:
            self.assertEqual(card['effect_summary'],
                             _card_effect_summary(CARDS[card['card_id']]))

    def test_details_only_project_official_rule_fields(self):
        for card in self.cards:
            original = CARDS[card['card_id']]
            whitelist = _CARD_CATALOG_RULE_FIELDS[card['type']]
            # 只包含白名单 ∩ 真实存在字段：不发明字段，也不给缺失字段补 null。
            self.assertEqual(set(card['details']),
                             set(whitelist) & set(original))
            for key, value in card['details'].items():
                self.assertIsNotNone(value)

    def test_details_match_respective_type_sets(self):
        self.assertEqual(set(self.by_id['YH-01']['details']), {'provide'})
        self.assertEqual(set(self.by_id['YK-05']['details']),
                         {'provide', 'convert_turn'})
        self.assertEqual(set(self.by_id['YE-01']['details']), {'temp_res'})
        self.assertEqual(set(self.by_id['YE-03']['details']),
                         {'extra_reroll_rounds'})
        self.assertEqual(set(self.by_id['C05']['details']), {'temp_gl'})
        self.assertEqual(set(self.by_id['C12']['details']), {'abebe'})
        self.assertEqual(set(self.by_id['D01']['details']), {'extra_cost'})
        self.assertEqual(set(self.by_id['D09']['details']), {'block_type'})
        self.assertEqual(set(self.by_id['F10']['details']),
                         {'wildcard_normal'})
        self.assertEqual(set(self.by_id['F04']['details']),
                         {'first_discount'})

    def test_catalog_is_json_ready(self):
        text = json.dumps(self.catalog, ensure_ascii=False)
        self.assertIn('card_id', text)

    def test_catalog_is_pure_read_only_projection(self):
        before = copy.deepcopy(CARDS)
        again = card_catalog()
        self.assertEqual(again, self.catalog)
        self.assertEqual(CARDS, before)
        # 改动返回的投影不得泄漏回正式 CARDS（cost / details 均为独立拷贝）。
        self.catalog['cards'][0]['cost']['H'] = 999
        self.catalog['cards'][0]['details'].clear()
        self.assertEqual(CARDS, before)
        self.assertEqual(card_catalog(), again)


if __name__ == '__main__':
    unittest.main()
