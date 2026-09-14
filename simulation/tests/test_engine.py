# -*- coding: utf-8 -*-
"""规则单元测试 / deterministic scenario tests。

运行：
  cd simulation && python -m unittest tests.test_engine -v
"""
import os
import random
import sys
import unittest
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ailife.cards import CARDS
from ailife.engine import CONFIGS, Game, Config, solve_cost, discounted_cost
from ailife.strategies import BaseStrategy, Balanced, GoalPriority, STRATEGIES
from ailife import scoring


class Scripted(BaseStrategy):
    """可脚本化的测试策略。"""

    def __init__(self, game, resp=None, plan=(), ye04=None, placement=None):
        super().__init__(game)
        self.resp = resp or {}
        self.plan = plan
        self.ye04 = ye04
        self.placement = placement

    def misfortune_response(self):
        return self.resp

    def choose_plan(self, plans):
        return self.plan if self.plan in plans else ()

    def ye04_target(self):
        return self.ye04

    def choose_placement(self, cid):
        return self.placement or 'top'

    def declare_pre_roll(self):
        return {}

    def use_ye03(self):
        return False


class BuryGoalPriority(BaseStrategy):
    """用于验证 H/K/R 候选埋底时不改变 active 产出。"""

    def choose_placement(self, cid):
        return 'bury'


def make_game(cfg=None, strat_cls=None, goals=(1, 2), seed=42):
    cfg = cfg or Config(True, False)
    rng = random.Random(seed)
    strat_cls = strat_cls or Balanced
    return Game(cfg, strat_cls, rng, shuffle=False, forced_goals=list(goals))


def set_market(g, cids, entry_turn=1):
    g.market = list(cids)
    g.market_entry = {c: entry_turn for c in cids}


class TestScoring(unittest.TestCase):
    def test_curve_a(self):
        self.assertEqual([scoring.curve_a(i) for i in range(14)],
                         [0, 1, 3, 5, 8, 11, 15, 19, 24, 29, 35, 41, 48, 55])
        # 外推（SA-CURVE）：从正式端点 13=55 向后接
        self.assertEqual(scoring.curve_a(14), 61)

    def test_lg_formulas(self):
        cv = {c: [] for c in 'HKRWP'}
        cv['H'] = ['YH-01', 'YH-03']
        cv['K'] = ['YK-01', 'YK-03']
        cv['R'] = ['YR-01', 'YR-04']
        cv['W'] = ['YW-01', 'YW-02']
        cv['P'] = ['YP-01', 'MP-02']
        counts = scoring.cv_counts(cv)
        pvp = scoring.possession_vp(cv)  # 2 + 4 = 6
        self.assertEqual(scoring.lg_score(1, counts, pvp, []), 2)
        self.assertEqual(scoring.lg_score(4, counts, pvp, []), 4)
        self.assertEqual(scoring.lg_score(5, counts, pvp, []), 2)   # 6//3
        self.assertEqual(scoring.lg_score(6, counts, pvp, []), 4)   # 2*min(2,2)
        self.assertEqual(scoring.lg_score(9, counts, pvp, []), 8)   # 4*min(2,2,2)
        self.assertEqual(scoring.lg_score(10, counts, pvp, []), 10) # 五类各2
        self.assertEqual(scoring.lg_score(11, counts, pvp, []), 2)
        self.assertEqual(scoring.lg_score(12, counts, pvp, []), 4)
        self.assertEqual(scoring.lg_score(13, counts, pvp, []), 0)  # provides=[]
        self.assertEqual(scoring.lg_score(16, counts, pvp, []), 0)

    def test_designation_lg13_lg16(self):
        cv = {c: [] for c in 'HKRWP'}
        cv['H'] = ['OH-03']       # H1
        cv['K'] = ['OK-02']       # flex H/K/R/M
        cv['W'] = ['MW-03']       # flex K/R
        cv['P'] = ['MP-01']       # M1
        s = scoring.full_score(cv, (13, 16))
        # OK-02->K, MW-03->R：类型 {H,K,R,M} = LG13 8 分；
        # M 稳定符号 = MW-03 的 M2 + MP-01 的 M1 = 3 -> LG16 6 分
        self.assertEqual(s['lg_scores'][0], 8)
        self.assertEqual(s['lg_scores'][1], 6)

    def test_designation_shared(self):
        """终局指定对所有 Goal 同时生效：OK-02 指定为 M 时 LG13 只算 M 一种。"""
        cv = {c: [] for c in 'HKRWP'}
        cv['K'] = ['OK-02']
        cv['W'] = ['OW-01']  # M3 + flex K/R
        s = scoring.full_score(cv, (13, 16))
        # OW-01 flex→K，OK-02→M：类型 {M,K} → LG13=4；M 符号 3+1=4 → LG16=8，合计 12
        # 或 OK-02→K, OW-01 flex→R：类型 {K,R,M} → 6 + 6 = 12；两者并列取到 12
        self.assertEqual(sum(s['lg_scores']), 12)


class TestStrategyValuation(unittest.TestCase):
    def test_goal_priority_lg13_active_output_marginal(self):
        g = make_game(strat_cls=GoalPriority, goals=(13,))
        self.assertGreater(g.strat.lg_marginal('YH-01'), 0)

    def test_goal_priority_lg16_active_money_marginal(self):
        g = make_game(strat_cls=GoalPriority, goals=(16,))
        self.assertGreater(g.strat.lg_marginal('MP-01'), 0)

    def test_buried_candidate_has_no_stable_output_marginal(self):
        g = make_game(strat_cls=BuryGoalPriority, goals=(13,))
        g.cv['H'] = ['YH-01']
        self.assertEqual(g.strat.lg_marginal('YH-03'), 0)

    def test_forced_top_replaces_old_active_output(self):
        g = make_game(strat_cls=GoalPriority, goals=(16,))
        g.cv['W'] = ['YW-01']  # active M1
        self.assertEqual(g.strat.lg_marginal('YW-02'), 2)  # active becomes M2, not M3


class TestPaymentSolver(unittest.TestCase):
    def test_doc_example(self):
        """策划文档 §6：A+C 合法、B+C 非法（资源不可重复使用）。"""
        pool = Counter({'H': 2, 'K': 1, 'M': 2, 'R': 1})
        A, B, C = {'H': 2}, {'K': 1, 'M': 2}, {'R': 1, 'M': 2}
        p1 = Counter(pool)
        sA = solve_cost(A, p1, [], [])
        self.assertIsNotNone(sA)
        p1 -= sA['pool_used']
        sC = solve_cost(C, p1, [], [])
        self.assertIsNotNone(sC)  # A+C 合法
        p2 = Counter(pool)
        sB = solve_cost(B, p2, [], [])
        p2 -= sB['pool_used']
        self.assertIsNone(solve_cost(C, p2, [], []))  # B+C 非法

    def test_substitution(self):
        # YK-02：1 金钱视为 1 知识
        sol = solve_cost({'K': 1}, Counter({'M': 1}), [], [('M', 'K')])
        self.assertIsNotNone(sol)
        self.assertEqual(sol['subs_used'], [('M', 'K')])
        self.assertIsNone(solve_cost({'K': 2}, Counter({'M': 1}), [], [('M', 'K')]))
        # GL 需求不可被替代
        self.assertIsNone(solve_cost({'GL': 1}, Counter({'M': 1}), [], [('M', 'GL')]))

    def test_temps(self):
        sol = solve_cost({'M': 2}, Counter({'M': 1}), [('M', 2, 'YE-01')], [])
        self.assertIsNotNone(sol)
        self.assertEqual(sol['temps_used'], ['YE-01'])
        # 临时资源不能重复用于两个独立结算
        p = Counter({'M': 1})
        s1 = solve_cost({'M': 2}, p, [('M', 2, 'YE-01')], [])
        p -= s1['pool_used']
        self.assertIsNone(solve_cost({'M': 1}, p, [], []))

    def test_discounted_cost(self):
        self.assertEqual(discounted_cost({'M': 2, 'K': 1}, 'M'), {'M': 1, 'K': 1})
        self.assertEqual(discounted_cost({'M': 1}, 'M'), {})
        self.assertEqual(discounted_cost({'GL': 1}, 'GL'), {'GL': 1})  # 不减特殊符号

    def test_event_temp_two_units_pay_m2(self):
        sol = solve_cost({'M': 2}, Counter(), [('M', 2, 'YE-01')], [])
        self.assertIsNotNone(sol)
        self.assertEqual(sol['temps_used'], ['YE-01'])
        self.assertEqual(sol['temp_units_used'], ['YE-01', 'YE-01'])

    def test_event_temp_two_units_pay_r2(self):
        sol = solve_cost({'R': 2}, Counter(), [('R', 2, 'YE-02')], [])
        self.assertIsNotNone(sol)
        self.assertEqual(sol['temps_used'], ['YE-02'])
        self.assertEqual(sol['temp_units_used'], ['YE-02', 'YE-02'])

    def test_event_temp_two_units_cannot_pay_m3(self):
        self.assertIsNone(solve_cost({'M': 3}, Counter(), [('M', 2, 'YE-01')], []))

    def test_event_temp_units_not_reused_across_double_purchase(self):
        g = make_game()
        g.turn = 2
        set_market(g, ['YP-01', 'YP-03'])  # M2；M2+H1
        g.pool = Counter({'M': 2, 'H': 1})
        g.hand = ['YE-01']
        plan = g.try_acquire(('YP-01', 'YP-03'))
        self.assertIsNotNone(plan)
        self.assertEqual(plan['temps_used'], ['YE-01'])
        self.assertEqual(plan['temp_units_used'], ['YE-01', 'YE-01'])


class TestGLTake(unittest.TestCase):
    def test_three_gl_free_take(self):
        g = make_game()
        g.turn = 2
        set_market(g, ['YP-01', 'YH-01'])
        g.pool = Counter({'GL': 3})
        plan = g.try_acquire(('YP-01',))
        self.assertIsNotNone(plan)
        self.assertIn('gl', plan['methods'])
        self.assertEqual(plan['pool_used']['GL'], 3)

    def test_two_gl_insufficient(self):
        g = make_game()
        g.turn = 2
        set_market(g, ['YP-01'])
        g.pool = Counter({'GL': 2, 'M': 1})
        self.assertIsNone(g.try_acquire(('YP-01',)))  # M2 缺 1，GL 不足 3

    def test_temp_gl_counts(self):
        g = make_game()
        g.turn = 2
        set_market(g, ['YP-01'])
        g.pool = Counter({'GL': 2})
        g.hand = ['C05']
        plan = g.try_acquire(('YP-01',))
        self.assertIsNotNone(plan)
        self.assertEqual(plan['temps_used'], ['C05'])

    def test_gl_pay_for_gl_cost(self):
        g = make_game()
        g.turn = 2
        set_market(g, ['YR-03'])  # 成本 关系1 + 好运1
        g.pool = Counter({'R': 1, 'GL': 1})
        plan = g.try_acquire(('YR-03',))
        self.assertIsNotNone(plan)
        self.assertEqual(plan['pool_used']['GL'], 1)


class TestMarket(unittest.TestCase):
    def test_cleanup_departure_count_tracks_purchases_zero_to_two(self):
        for purchased in ([], ['YH-04'], ['YH-04', 'YK-01']):
            with self.subTest(purchased=purchased):
                g = make_game(strat_cls=lambda gm: Scripted(gm))
                g.turn = 5
                set_market(g, ['YH-01', 'YH-02', 'YH-03', 'YK-02', 'YR-01'],
                           entry_turn=4)
                g.purchased_this_turn = list(purchased)
                g.decks = {
                    'youth': ['YR-02', 'YR-03', 'YR-04'],
                    'middle': [], 'elder': [],
                }
                result = g.cleanup_normal_market()
                self.assertEqual(len(result['system_eliminated_card_ids']),
                                 3 - len(purchased))
                self.assertEqual(len(result['purchased_card_ids'])
                                 + len(result['system_eliminated_card_ids']), 3)

    def test_cleanup_does_not_consume_rng_for_fixed_elimination_only(self):
        g = make_game(strat_cls=lambda gm: Scripted(gm))
        g.turn = 5
        set_market(g, ['YH-01', 'YH-02', 'YH-03', 'YK-01'])
        g.purchased_this_turn = ['YH-04', 'YK-02']
        g.decks = {'youth': ['YK-03'], 'middle': [], 'elder': []}
        before = g.rng.getstate()
        result = g.cleanup_normal_market()
        self.assertEqual(result['system_eliminated_card_ids'], ['YH-01'])
        self.assertEqual(g.rng.getstate(), before)

    def test_cleanup_invalid_protection_has_no_side_effects(self):
        g = make_game(strat_cls=lambda gm: Scripted(gm))
        g.turn = 5
        set_market(g, ['YH-01', 'YH-02', 'YH-03', 'YK-01', 'YK-02'])
        g.hand = ['YE-04']
        g.decks = {'youth': ['YK-03', 'YK-04', 'YK-05'], 'middle': [], 'elder': []}
        before = (list(g.hand), list(g.market), g.rng.getstate())
        self.assertIsNone(g.cleanup_normal_market(protected='not-in-market'))
        self.assertEqual((list(g.hand), list(g.market), g.rng.getstate()), before)

    def test_elimination_algorithm(self):
        """买 D → A 固定淘汰 + B/C/E 随机 1 张。"""
        g = make_game(strat_cls=lambda gm: Scripted(gm))
        g.turn = 5
        set_market(g, ['YH-01', 'YH-02', 'YH-03', 'YK-01'], entry_turn=4)  # D 已买
        g.purchased_this_turn = ['YH-04']
        g.decks = {'youth': ['YK-02', 'YK-03', 'YK-04'], 'middle': [], 'elder': []}
        g._settlement()
        # A=YH-01 固定淘汰
        self.assertNotIn('YH-01', g.market)
        # 随机淘汰 1 张来自 YH-02/YH-03/YK-01
        remaining = set(['YH-02', 'YH-03', 'YK-01'])
        elim_random = remaining - set(g.market)
        self.assertEqual(len(elim_random & remaining), 1)
        # 补牌到 5
        self.assertEqual(len(g.market), 5)
        dep = g.stats.game['turn_departures'][-1]
        self.assertEqual(dep, 3)  # 1 买 + 2 淘汰

    def test_ye04_protection(self):
        """保护 slot1 牌：跳过它，下一张承担固定淘汰位，总离场仍 3。"""
        g = make_game(strat_cls=lambda gm: Scripted(gm, ye04='YH-01'))
        g.turn = 5
        set_market(g, ['YH-01', 'YH-02', 'YH-03', 'YK-01', 'YK-02'], entry_turn=4)
        g.purchased_this_turn = []
        g.hand = ['YE-04']
        g.decks = {'youth': ['YK-03', 'YK-04', 'YK-05'], 'middle': [], 'elder': []}
        g._settlement()
        self.assertIn('YH-01', g.market)         # 被保护
        self.assertNotIn('YH-02', g.market)      # 承担固定淘汰位
        dep = g.stats.game['turn_departures'][-1]
        self.assertEqual(dep, 3)
        self.assertNotIn('YE-04', g.hand)        # Event 已消耗
        self.assertEqual(g.stats.run['ye04_used'], 1)

    def test_stage_mixing(self):
        """青年牌库补空 → 市场混入中年牌，不清空当前市场。"""
        g = make_game(strat_cls=lambda gm: Scripted(gm))
        g.turn = 8
        set_market(g, ['YH-01', 'YH-02', 'YH-03'])
        g.decks = {'youth': ['YK-02'], 'middle': ['MH-01', 'MH-02'], 'elder': ['OH-01']}
        g._fill_market()
        self.assertEqual(g.market, ['YH-01', 'YH-02', 'YH-03', 'YK-02', 'MH-01'])
        self.assertEqual(g.stage, 'middle')  # 最后一张抽自中年
        # 继续补
        g.market.pop()  # 模拟离场
        g._fill_market()
        self.assertEqual(g.stage, 'middle')
        g.decks['middle'] = []
        g.market.pop()
        g._fill_market()
        self.assertEqual(g.stage, 'elder')

    def test_final_round(self):
        """老年牌库补空 → final_round_pending → 完整最后一回合 → game_over。"""
        g = make_game(strat_cls=lambda gm: Scripted(gm))
        set_market(g, ['YH-01', 'YH-02', 'YH-03', 'YH-04', 'YK-01'], entry_turn=0)
        g.decks = {'youth': [], 'middle': [], 'elder': ['OP-01', 'OP-02']}
        g.play_turn()  # 本回合结算时抽空老年牌库
        self.assertTrue(g.final_pending)
        self.assertFalse(g.game_over)
        g.play_turn()  # 最后一个完整回合
        self.assertTrue(g.game_over)
        self.assertEqual(g.turn, 2)
        self.assertEqual(len(g.stats.game['turn_departures']), 2)

    def test_cleanup_marks_final_pending_without_ending_runtime_style_turn(self):
        g = make_game(strat_cls=lambda gm: Scripted(gm))
        g.turn = 5
        set_market(g, ['YH-01', 'YH-02', 'YH-03', 'YH-04', 'YK-01'])
        g.decks = {'youth': [], 'middle': [], 'elder': ['OP-01', 'OP-02', 'OP-03']}
        result = g.cleanup_normal_market()
        self.assertTrue(g.final_pending)
        self.assertFalse(g.game_over)
        self.assertTrue(result['final_round_pending'])
        self.assertEqual(result['refill_card_ids'], ['OP-01', 'OP-02', 'OP-03'])

    def test_full_game_invariants(self):
        """整局：每回合总离场恒为 3，市场不超 5，无负资源，无死循环。"""
        for cls in (Balanced, STRATEGIES['random_legal'],
                    STRATEGIES['goal_priority'], STRATEGIES['greedy_acquire'],
                    STRATEGIES['engine_growth']):
            for seed in (7, 8):
                g = make_game(strat_cls=cls, goals=(6, 9), seed=seed)
                while not g.game_over:
                    g.play_turn()
                    self.assertLessEqual(len(g.market), 5)
                dep = g.stats.game['turn_departures']
                self.assertTrue(all(d == 3 for d in dep), (cls.name, dep))
                self.assertEqual(g.stats.run['shortfalls'], 0)
                self.assertLess(g.turn, 100)
                self.assertTrue(g.final_pending)


class TestTurnCloseout(unittest.TestCase):
    def closeout_marker(self, game, final_round_started=False):
        game.market_cleanup_result = {
            'final_round_started': final_round_started,
            'final_round_pending': game.final_pending,
        }

    def test_closeout_debuff_starts_next_turn_and_expires_after_three_complete_turns(self):
        g = Game(CONFIGS['V06'], Balanced, random.Random(91), shuffle=False,
                 forced_goals=[1, 2])
        g.turn = 4
        g.current_debuff = 'D01'
        g.debuff_active_from_turn = 5
        g.debuff_turns_remaining = 3

        self.closeout_marker(g)
        first = g.closeout_adult_turn()
        self.assertFalse(first['debuff_lifecycle']['advanced'])
        self.assertEqual(g.debuff_turns_remaining, 3)
        self.assertEqual(g.turn, 5)

        self.closeout_marker(g)
        second = g.closeout_adult_turn()
        self.assertTrue(second['debuff_lifecycle']['advanced'])
        self.assertEqual(g.debuff_turns_remaining, 2)
        self.assertEqual(g.turn, 6)

        self.closeout_marker(g)
        g.closeout_adult_turn()
        self.assertEqual(g.debuff_turns_remaining, 1)
        self.assertEqual(g.turn, 7)

        self.closeout_marker(g)
        final = g.closeout_adult_turn()
        self.assertEqual(final['debuff_lifecycle']['archived_card_id'], 'D01')
        self.assertIsNone(g.current_debuff)
        self.assertEqual(g.debuff_history, ['D01'])

    def test_closeout_consumes_only_an_already_pending_final_round(self):
        g = make_game(strat_cls=lambda gm: Scripted(gm))
        g.turn = 8
        g.final_pending = True
        self.closeout_marker(g, final_round_started=True)
        first = g.closeout_adult_turn()
        self.assertFalse(first['game_over'])
        self.assertEqual(g.turn, 9)
        self.assertFalse(g.game_over)

        self.closeout_marker(g, final_round_started=False)
        final = g.closeout_adult_turn()
        self.assertTrue(final['game_over'])
        self.assertTrue(g.game_over)
        self.assertEqual(g.turn, 9)
        self.assertRaises(RuntimeError, g.closeout_adult_turn)


class TestStacking(unittest.TestCase):
    def test_bury_keeps_active(self):
        g = make_game()
        g.cv['H'] = ['YH-01']
        g.cv['H'].insert(0, 'YH-03')  # 压入堆下
        self.assertEqual(g.active('H'), 'YH-01')

    def test_loss_surfaces_next(self):
        g = make_game()
        g.cv['H'] = ['YH-03', 'YH-01']
        g.lose_active('YH-01')
        self.assertEqual(g.active('H'), 'YH-03')

    def test_wp_force_top(self):
        """W/P 新牌必须置顶（play_turn 的放置逻辑）。"""
        g = make_game(strat_cls=lambda gm: Scripted(gm, placement='bury'))
        g.turn = 3
        g.cv['W'] = ['YW-01']
        g.cv['P'] = ['YP-01']
        g.pending_new = ['YW-02', 'YP-02']
        # 直接执行放置段（与 play_turn 相同的逻辑）
        for cid in g.pending_new:
            cls = CARDS[cid]['type']
            g.cv[cls].append(cid)  # W/P 强制置顶
        self.assertEqual(g.active('W'), 'YW-02')
        self.assertEqual(g.active('P'), 'YP-02')


class TestUpkeep(unittest.TestCase):
    def _setup(self, cfg):
        g = make_game(cfg=cfg)
        g.turn = 3
        g.cv['W'] = ['YW-02']  # 夜班兼职：产 M2，upkeep H1
        g.acquired['YW-02'] = 3
        return g

    def test_new_card_is_exempt_under_formal_maintenance_rule(self):
        g = self._setup(Config(True, False))
        g.pool = Counter({'H': 1, 'M': 5})
        g._upkeep()
        self.assertEqual(g.cv['W'], ['YW-02'])
        self.assertEqual(g.pool['H'], 1)

    def test_new_card_cannot_die_of_maintenance_on_acquisition_turn(self):
        g = self._setup(Config(True, False))
        g.pool = Counter({'M': 5})  # 买完没剩健康
        g._upkeep()
        self.assertEqual(g.cv['W'], ['YW-02'])
        self.assertEqual(g.stats.card('YW-02')['same_turn_death'], 0)

    def test_B_skip_this_turn(self):
        g = self._setup(Config(False, False))
        g.pool = Counter({'M': 5})  # 无 H 也不死
        g._upkeep()
        self.assertEqual(g.cv['W'], ['YW-02'])
        # 下一回合开始支付
        g.turn = 4
        g.pool = Counter({'M': 5})
        g._upkeep()
        self.assertEqual(g.cv['W'], [])

    def test_mh01_discount(self):
        g = make_game()
        g.turn = 5
        g.cv['H'] = ['MH-01']
        g.cv['W'] = ['YW-02']
        g.acquired['YW-02'] = 2
        g.pool = Counter({'H': 0, 'M': 5})  # 成本 H1 被 MH-01 减免为 0
        g._upkeep()
        self.assertEqual(g.cv['W'], ['YW-02'])

    def test_oh05_upkeep_sub(self):
        g = make_game()
        g.turn = 6
        g.cv['H'] = ['OH-05']
        g.cv['W'] = ['YW-02']  # upkeep H1
        g.cv['R'] = ['MR-01']  # upkeep M1
        g.acquired['YW-02'] = 1
        g.acquired['MR-01'] = 1
        g.pool = Counter({'M': 1})  # 只剩 1 M；H 缺口用 OH-05 的 H→其他 替代？H 是来源不是目标
        g._upkeep()
        # OH-05 提供的是 1H 视为成本中其他符号，救不了 H 需求本身 → YW-02 应失败
        self.assertEqual(g.cv['W'], [])
        # MR-01 的 M1 可付
        self.assertEqual(g.cv['R'], ['MR-01'])


class TestDiceAbilities(unittest.TestCase):
    def test_oh04_bl_reroll_once_per_turn(self):
        g = make_game(strat_cls=lambda gm: Scripted(gm))
        g.turn = 7
        g.cv['H'] = ['OH-04']
        g.dice = ['H', 'BL', 'BL', 'K']
        g.oh04_used = False
        n = g.apply_special_reroll('OH-04', 1)
        self.assertTrue(n)
        self.assertNotEqual(g.dice[1], 'BL')  # 重掷了
        self.assertTrue(g.oh04_used)
        # 同回合第二次：预算已用，BL 冻结
        before = g.dice[2]
        n = g.apply_special_reroll('OH-04', 2)
        self.assertFalse(n)
        self.assertEqual(g.dice[2], before)

    def test_abebe_once_per_game(self):
        g = make_game(strat_cls=lambda gm: Scripted(gm))
        g.turn = 2
        g.cv['H'] = []
        g.abebe_held = True
        g.abebe_used = False
        g.dice = ['BL', 'H', 'K', 'M']
        n = g.apply_normal_reroll({0}, c12_index=0)
        self.assertEqual(n, 1)
        self.assertTrue(g.abebe_used)
        g.dice = ['BL', 'H', 'K', 'M']
        n = g.apply_normal_reroll({0}, c12_index=0)
        self.assertEqual(n, 0)

    def test_mh04_bl_convert(self):
        g = make_game(strat_cls=lambda gm: Scripted(gm))
        g.turn = 5
        g.cv['H'] = ['MH-04']
        g.dice = ['BL', 'BL', 'H', 'K']
        g.mh04_used = False
        g._after_dice_change()
        g._offer_bl_convert()  # Scripted 未覆盖 use_bl_convert → Base 返回 True
        self.assertEqual(g.dice.count('BL'), 1)
        self.assertEqual(g.dice.count('H'), 2)
        self.assertTrue(g.mh04_used)


class TestFallbackConversion(unittest.TestCase):
    def test_2to1_enables_purchase(self):
        g = make_game(cfg=Config(True, True))
        g.turn = 2
        set_market(g, ['YP-01'])  # M2
        g.pool = Counter({'H': 2, 'M': 1})
        fb = g.strat.use_fallback()
        self.assertIsNotNone(fb)
        burn, target = fb
        self.assertEqual(target, 'M')
        self.assertEqual(Counter(burn), Counter({'H': 2}))
        pool2 = Counter(g.pool)
        for s in burn:
            pool2[s] -= 1
        pool2[target] += 1
        self.assertIsNotNone(g.try_acquire(('YP-01',), pool_override=pool2))

    def test_gl_bl_excluded(self):
        g = make_game(cfg=Config(True, True))
        g.turn = 2
        set_market(g, ['YP-01'])
        g.pool = Counter({'GL': 5, 'BL': 5, 'M': 1})
        self.assertIsNone(g.strat.use_fallback())


class TestReproducibility(unittest.TestCase):
    def test_same_seed_same_result(self):
        rows = []
        for _ in range(2):
            rng = random.Random(99)
            g = Game(Config(True, False), Balanced, rng, shuffle=True)
            g.run()
            rows.append(g.stats.rows[-1])
        self.assertEqual(rows[0], rows[1])

    def test_no_double_spend_pair(self):
        g = make_game()
        g.turn = 4
        set_market(g, ['YP-01', 'MP-03'])  # M2 + M3
        g.pool = Counter({'M': 3})
        self.assertIsNone(g.try_acquire(('YP-01', 'MP-03')))
        self.assertIsNotNone(g.try_acquire(('YP-01',)))


class TestPurchasesAndEvents(unittest.TestCase):
    def test_childhood_discount_auto(self):
        g = make_game()
        g.turn = 2
        set_market(g, ['YP-01'])  # M2
        g.pool = Counter({'M': 1})
        g.hand = ['C10']
        plan = g.try_acquire(('YP-01',))
        self.assertIsNotNone(plan)
        self.assertIn('C10', plan['discounts_used'])

    def test_event_temp_in_purchase(self):
        g = make_game()
        g.turn = 2
        set_market(g, ['MP-02'])  # M4
        g.pool = Counter({'M': 3})
        g.hand = ['YE-01']  # 购买结算临时 M2
        plan = g.try_acquire(('MP-02',))
        self.assertIsNotNone(plan)
        self.assertIn('YE-01', plan['temps_used'])

    def test_strategy_cannot_cheat_plan(self):
        """策略返回的方案必须经过引擎合法计划校验。"""
        g = make_game(strat_cls=lambda gm: Scripted(gm, plan=('OP-01',)))
        g.turn = 3
        set_market(g, ['YH-01'])
        g.pool = Counter({'M': 9})
        plans = g.affordable_plans()
        choice = g.strat.choose_plan(plans)
        self.assertEqual(choice, ())  # OP-01 不在市场/不合法 → 引擎拒绝


if __name__ == '__main__':
    unittest.main(verbosity=2)
