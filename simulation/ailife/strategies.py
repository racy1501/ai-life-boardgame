# -*- coding: utf-8 -*-
"""5 个本地 heuristic 策略 Agent。

只读取玩家正常可知的信息（市场 / 骰面 / 稳定资源 / 手牌 / CV / 两张 Goal /
牌库剩余数量），不读取牌库顺序。所有行动经引擎合法性验证。
这些策略只用于制造决策倾向差异，不代表最终 AI 玩家行为。
"""
import random  # noqa: F401  (random 策略使用 game.rng，保证可复现)
from collections import Counter

from .cards import CARDS, FATE_BY_ID, NORMAL
from . import scoring


class BaseStrategy:
    name = 'base'
    # 权重
    w_lg = 1.0        # Life Goal 边际分
    w_vp = 0.4        # 成果分
    w_engine = 0.6    # 引擎价值
    w_event = 0.7     # Event 购买意愿
    buy_threshold = 0.25
    prefer_two = 0.0
    ye04_value_threshold = 1.5

    EVENT_BASE_VALUE = {
        'YE-01': 1.0, 'YE-02': 1.0, 'YE-03': 0.8, 'YE-04': 0.6,
        'YE-05': 0.7, 'ME-01': 0.5, 'ME-02': 0.9,
    }
    CHILDHOOD_VALUE = {
        'C01': 1.0, 'C02': 1.0, 'C03': 1.0, 'C04': 1.0, 'C05': 1.1,
        'C06': 1.4, 'C07': 1.3, 'C08': 1.3, 'C09': 1.3, 'C10': 1.3,
        'C11': 1.5, 'C12': 1.2,
    }

    def __init__(self, game):
        self.g = game

    # ---------- 估值 ----------

    def turn_scale(self):
        return min(1.0, self.g.est_turns_left() / 12.0)

    def raw_engine(self, cid):
        c = CARDS[cid]
        ts = self.turn_scale()
        v = 0.9 * sum(n for _, n in c.get('provide', [])) * ts
        if c.get('flex'):
            v += 1.2 * ts + 0.3
        if c.get('extra_die'):
            v += 2.5 * ts + 0.5
        if c.get('reroll'):
            v += 1.4 * ts + 0.3
        v += 0.9 * len(c.get('sub_buy', [])) * ts
        if c.get('convert_turn'):
            v += 0.7 * ts
        if c.get('cancel_debuff_once') or c.get('shorten_debuff'):
            v += 0.8 * ts
        if c.get('bl_convert'):
            v += 0.6 * ts
        if c.get('upkeep_discount') or c.get('upkeep_sub'):
            v += 0.6 * ts
        if c.get('upkeep'):
            v -= 0.8 * sum(c['upkeep'].values()) * min(1.0, self.g.est_turns_left() / 12.0)
        return v

    def lg_marginal(self, cid):
        """按候选牌实际放置后的 active 状态计算两张 Goal 的边际得分。"""
        c = CARDS[cid]
        if c['type'] not in 'HKRWP':
            return 0.0
        g = self.g
        cv_after = {cls: list(cards) for cls, cards in g.cv.items()}
        cls = c['type']
        if cls in ('W', 'P') or g.active(cls) is None:
            cv_after[cls].append(cid)
        elif self.choose_placement(cid) == 'bury':
            cv_after[cls].insert(0, cid)
        else:
            cv_after[cls].append(cid)
        meta = g.scoring_counts()
        now = scoring.full_score(g.cv, g.goals, **meta)['lg_sum']
        after = scoring.full_score(cv_after, g.goals, **meta)['lg_sum']
        return after - now

    def fate_value(self, cid):
        """按通用效果与当前目标给 Fate 做轻量估值。"""
        c = FATE_BY_ID[cid]
        ts = self.turn_scale()
        v = 0.3
        if c.get('immediate') == 'set_active':
            v += 1.0
        if c.get('immediate') == 'replace_goal':
            v += 1.5
        v += 0.8 * c.get('reroll_delta', 0) * ts
        v += 1.0 * c.get('dice_delta', 0) * ts
        if c.get('first_discount'):
            v += 0.7 * c['first_discount']['n'] * ts
        if c.get('discount'):
            v += 0.7 * c['discount']['n'] * ts
        v += 0.7 * c.get('upkeep_reduce', 0) * ts
        if c.get('purchase_limit'):
            v -= 0.7 * ts
        if c.get('block_event'):
            v -= 0.3 * sum(CARDS[x]['type'] == 'E' for x in self.g.hand)
        if c.get('wildcard_normal'):
            # 只评价当前最终骰面中的 GL；不读取未来牌库，也不把 stable/temp GL 混入。
            gl_faces = self.g.dice.count('GL')
            ordinary_need = sum(
                1 for cid in self.g.market
                if any(sym in NORMAL for sym in CARDS[cid].get('cost', {})))
            v += 0.45 * min(gl_faces, max(1, ordinary_need)) * ts
            # 放弃 3 GL 免费取得与 Fate 的 GL 支付，是通用机会成本。
            v -= 0.30 * gl_faces
        if c.get('lock_reroll_on_bl'):
            v -= 0.5 * ts
        if c.get('extra_cost'):
            v -= 0.5 * ts
        if c.get('virtual_bl'):
            v -= 0.6 * ts

        fate_n = len(self.g.fate_stack)
        debuff_n = self.g.debuff_experienced_count()
        event_n = self.g.event_acquired_count()
        if 17 in self.g.goals:
            v += self.w_lg * 3 * ((fate_n + 1) // 2 - fate_n // 2)
        if 18 in self.g.goals:
            before = min(fate_n, debuff_n, event_n)
            after = min(fate_n + 1, debuff_n, event_n)
            v += self.w_lg * 4 * (after - before)
        return v

    def card_value(self, cid):
        c = CARDS[cid]
        if c['type'] == 'E':
            value = self.w_event * self.EVENT_BASE_VALUE.get(cid, 0.8)
            if 18 in self.g.goals:
                fate_n = len(self.g.fate_stack)
                debuff_n = self.g.debuff_experienced_count()
                event_n = self.g.event_acquired_count()
                before = min(fate_n, debuff_n, event_n)
                after = min(fate_n, debuff_n, event_n + 1)
                value += self.w_lg * 4 * (after - before)
            return value
        if c['type'] == 'C':
            return self.CHILDHOOD_VALUE.get(cid, 1.0)
        v = (self.w_lg * self.lg_marginal(cid) + self.w_vp * c['vp']
             + self.w_engine * self.raw_engine(cid))
        v -= 0.05 * sum(c['cost'].values())
        return v

    # ---------- 决策 ----------

    def pick_childhood(self, options):
        return max(options, key=self.card_value)

    def choose_flex(self, cid, allowed):
        g = self.g
        score = {s: 0.0 for s in allowed}
        top2 = sorted(g.market, key=self.card_value, reverse=True)[:3]
        for m in top2:
            mv = max(0.2, min(1.5, self.card_value(m)))
            for sym, n in CARDS[m]['cost'].items():
                if sym in score:
                    score[sym] += 0.5 * n * mv
        for cls in 'HKRWP':
            top = g.active(cls)
            if top and CARDS[top].get('upkeep'):
                for sym in CARDS[top]['upkeep']:
                    if sym in score:
                        score[sym] += 1.0
        return max(allowed, key=lambda s: (score[s], s))

    def declare_pre_roll(self):
        g = self.g
        out = {}
        out['ye05'] = 'YE-05' in g.hand
        out['c11'] = 'C11' in g.hand
        out['pre_cancel_debuff'] = g.find_hand_effect('pre_cancel_debuff')
        return out

    def use_ye03(self):
        return 'YE-03' in self.g.hand

    def _target_costs(self):
        g = self.g
        top2 = sorted(g.market, key=self.card_value, reverse=True)[:2]
        need = Counter()
        for m in top2:
            need += Counter(CARDS[m]['cost'])
        return need

    def choose_reroll(self, round_no, total_rounds):
        g = self.g
        need = self._target_costs()
        keep = set()
        used = Counter()
        for i, f in enumerate(g.dice):
            if f == 'GL':
                keep.add(i)
            elif f in need and used[f] < need[f]:
                keep.add(i)
                used[f] += 1
        return set(range(len(g.dice))) - keep

    def use_bl_convert(self):
        return True

    def use_single_reroll(self, card_cid):
        g = self.g
        need = self._target_costs()
        filt = CARDS[card_cid]['reroll']['filter']
        for i, f in enumerate(g.dice):
            if filt == 'non_bl' and f == 'BL':
                continue
            if filt == 'non_gl_bl' and f in ('GL', 'BL'):
                continue
            if f != 'GL' and f not in need:
                return i
        return None

    def use_convert_turn(self):
        g = self.g
        if g.pool.get('R', 0) < 1:
            return False
        for m in sorted(g.market, key=self.card_value, reverse=True):
            if g.try_acquire((m,)):
                continue
            pool2 = Counter(g.pool)
            pool2['R'] -= 1
            pool2['K'] += 1
            if g.try_acquire((m,), pool_override=pool2):
                return True
        return False

    def use_fallback(self):
        g = self.g
        for m in sorted(g.market, key=self.card_value, reverse=True):
            if g.try_acquire((m,)):
                continue
            cost = CARDS[m]['cost']
            deficit = {}
            for sym, n in cost.items():
                if sym in NORMAL:
                    d = n - g.pool.get(sym, 0)
                    if d > 0:
                        deficit[sym] = d
            if not deficit or sum(deficit.values()) != 1:
                continue
            target = next(iter(deficit))
            if target not in NORMAL:
                continue
            surplus = []
            for sym in NORMAL:
                if sym == target:
                    continue
                spare = g.pool.get(sym, 0) - cost.get(sym, 0)
                surplus.extend([sym] * max(0, spare))
            if len(surplus) < 2:
                continue
            pool2 = Counter(g.pool)
            for s in surplus[:2]:
                pool2[s] -= 1
            pool2[target] += 1
            if g.try_acquire((m,), pool_override=pool2):
                return (surplus[:2], target)
        return None

    def choose_plan(self, plans):
        if not plans:
            return ()
        cache = {}

        def val(cid):
            if cid not in cache:
                cache[cid] = self.card_value(cid)
            return cache[cid]

        best, bv = (), -1e9
        for p in plans:
            v = sum(val(c) for c in p) + (self.prefer_two if len(p) == 2 else 0)
            if v > bv:
                best, bv = p, v
        if bv > self.buy_threshold:
            return best
        return ()

    def choose_joint_plan(self, actions):
        """从引擎已经验证的（普通购买，Fate）联合动作中选择。"""
        best = ((), None)
        best_value = 0.0
        for cards, fate in actions:
            value = sum(self.card_value(cid) for cid in cards)
            if len(cards) == 2:
                value += self.prefer_two
            if fate:
                value += self.fate_value(fate)
            if value > best_value:
                best, best_value = (cards, fate), value
        return best if best_value > self.buy_threshold else ((), None)

    def choose_active_reset(self, options):
        """F01：从已经拥有的 H/K/R 卡中选择新的 active。"""
        return max(options, key=self.raw_engine) if options else None

    def choose_goal_replacement(self, candidates):
        """F03：只在替换能提高当前终局 Goal 分时执行。"""
        g = self.g
        meta = g.scoring_counts()
        current = scoring.full_score(g.cv, g.goals, **meta)['lg_sum']
        best = None
        best_score = current
        for new_goal in candidates:
            for index in range(len(g.goals)):
                goals = list(g.goals)
                goals[index] = new_goal
                value = scoring.full_score(g.cv, goals, **meta)['lg_sum']
                if value > best_score:
                    best = (index, new_goal)
                    best_score = value
        return best

    def debuff_response(self, cancel_options, shorten_option):
        """优先使用每局一次的 active 保护，其次童年取消，最后缩短。"""
        if cancel_options:
            active = [x for x in cancel_options if x['source'] == 'active']
            return {'cancel': (active or cancel_options)[0]['cid']}
        if shorten_option and self.g.pool.get('H', 0) >= 1:
            return {'shorten': shorten_option['cid']}
        return {}

    def choose_placement(self, cid):
        g = self.g
        cls = CARDS[cid]['type']
        cur = g.active(cls)
        if cur is None:
            return 'top'
        return 'top' if self.raw_engine(cid) >= self.raw_engine(cur) else 'bury'

    def misfortune_response(self):
        g = self.g
        actives = g.active_cards()
        if not actives:
            return {}
        deltas = {cid: g.loss_delta(cid) for cid in actives}
        victim = min(deltas, key=lambda c: (deltas[c], c))
        return {
            'victim': victim,
            'use_c06': 'C06' in g.hand,
            'use_oh01': (victim != 'OH-01' and g.active('H') == 'OH-01'
                         and g.pool.get('H', 0) >= 1 and deltas[victim] >= 1.0),
            'use_mh02': (victim != 'MH-02' and 'MH-02' in actives
                         and deltas.get('MH-02', 99) < deltas[victim]),
        }

    def ye04_target(self):
        g = self.g
        if 'YE-04' not in g.hand:
            return None
        cands = [c for c in g.market
                 if c not in g.purchased_this_turn
                 and self.card_value(c) >= self.ye04_value_threshold]
        if not cands:
            return None
        return max(cands, key=self.card_value)


class RandomLegal(BaseStrategy):
    """随机基准：在合法行动中随机（含放弃购买）。"""
    name = 'random_legal'

    def pick_childhood(self, options):
        return self.g.rng.choice(options)

    def choose_flex(self, cid, allowed):
        return self.g.rng.choice(list(allowed))

    def declare_pre_roll(self):
        r = self.g.rng.random
        pre = self.g.find_hand_effect('pre_cancel_debuff')
        return {'ye05': r() < 0.5, 'c11': r() < 0.5,
                'pre_cancel_debuff': pre if pre and r() < 0.5 else None}

    def use_ye03(self):
        return self.g.rng.random() < 0.5

    def choose_reroll(self, round_no, total_rounds):
        idxs = list(range(len(self.g.dice)))
        return {i for i in idxs if self.g.rng.random() < 0.5}

    def use_bl_convert(self):
        return self.g.rng.random() < 0.5

    def use_single_reroll(self, card_cid):
        if self.g.rng.random() < 0.5:
            return None
        filt = CARDS[card_cid]['reroll']['filter']
        elig = [i for i, f in enumerate(self.g.dice)
                if (filt == 'non_bl' and f != 'BL')
                or (filt == 'non_gl_bl' and f not in ('GL', 'BL'))]
        return self.g.rng.choice(elig) if elig else None

    def use_convert_turn(self):
        return self.g.rng.random() < 0.5

    def use_fallback(self):
        if self.g.rng.random() < 0.7:
            return None
        pool = self.g.pool
        normal_units = [s for s in NORMAL for _ in range(pool.get(s, 0))]
        if len(normal_units) < 2:
            return None
        burn = self.g.rng.sample(normal_units, 2)
        target = self.g.rng.choice(list(NORMAL))
        return (burn, target)

    def choose_plan(self, plans):
        opts = list(plans) + [()]
        return self.g.rng.choice(opts)

    def choose_joint_plan(self, actions):
        return self.g.rng.choice(list(actions))

    def choose_active_reset(self, options):
        return self.g.rng.choice(options) if options else None

    def choose_goal_replacement(self, candidates):
        if not candidates or self.g.rng.random() < 0.5:
            return None
        return (self.g.rng.randrange(len(self.g.goals)),
                self.g.rng.choice(list(candidates)))

    def debuff_response(self, cancel_options, shorten_option):
        options = [{}]
        options.extend({'cancel': x['cid']} for x in cancel_options)
        if shorten_option:
            options.append({'shorten': shorten_option['cid']})
        return self.g.rng.choice(options)

    def choose_placement(self, cid):
        return self.g.rng.choice(['top', 'bury'])

    def misfortune_response(self):
        g = self.g
        actives = g.active_cards()
        if not actives:
            return {}
        victim = g.rng.choice(actives)
        r = g.rng.random
        return {'victim': victim, 'use_c06': r() < 0.5,
                'use_oh01': r() < 0.5, 'use_mh02': r() < 0.5}

    def ye04_target(self):
        if 'YE-04' not in self.g.hand or not self.g.market:
            return None
        if self.g.rng.random() < 0.5:
            return None
        return self.g.rng.choice(self.g.market)


class GreedyAcquire(BaseStrategy):
    """偏向本轮尽可能取得更多/更高即时价值的牌。"""
    name = 'greedy_acquire'
    w_lg = 0.15
    w_vp = 1.0
    w_engine = 0.5
    w_event = 1.0
    buy_threshold = -1e9   # 有合法就买
    prefer_two = 0.3


class GoalPriority(BaseStrategy):
    """明确优先当前两张 Life Goal 有价值的卡与构筑方向。"""
    name = 'goal_priority'
    w_lg = 2.0
    w_vp = 0.25
    w_engine = 0.35
    w_event = 0.3
    buy_threshold = 0.3


class EngineGrowth(BaseStrategy):
    """优先增强未来资源能力：稳定产出/骰子/折扣/转换。"""
    name = 'engine_growth'
    w_lg = 0.25
    w_vp = 0.25
    w_engine = 1.6
    w_event = 0.6
    buy_threshold = 0.3


class Balanced(BaseStrategy):
    """Life Goal / 引擎 / 成果分 / 持续成本风险的简单综合。"""
    name = 'balanced'
    w_lg = 1.0
    w_vp = 0.5
    w_engine = 0.9
    w_event = 0.7
    buy_threshold = 0.25
    prefer_two = 0.1


STRATEGIES = {
    'random_legal': RandomLegal,
    'greedy_acquire': GreedyAcquire,
    'goal_priority': GoalPriority,
    'engine_growth': EngineGrowth,
    'balanced': Balanced,
}
