# -*- coding: utf-8 -*-
"""AI人生桌游 v0.6 —— 单人规则引擎。

职责：洗牌 / 骰子 / 重掷 / 支付合法性 / 取得 / active 堆叠 / 稳定资源 /
Event / 童年牌 / Fate / Debuff / 持续成本 / 市场淘汰补牌 / 阶段推进 / 终局计分。
策略 Agent 只从引擎验证过的合法行动中选择，不能直接改状态。

回合阶段顺序（按 2026-09-11 任务书）：
稳定资源 → 掷骰 → 重掷 → Event/童年能力 → 普通牌/Fate联合取得
→ Debuff判定 → active处理(堆叠) → 持续成本 → 市场结算(淘汰/补牌/阶段/终局检测)
"""
import hashlib
import itertools
import json
from collections import Counter

from .cards import (CARDS, DEBUFF_BY_ID, DEBUFFS, FATE_BY_ID, FATES, SYMS,
                    NORMAL, STAGE_ORDER, STAGE_DECKS)
from . import scoring
from .stats import Stats


class Config:
    """模拟配置。v0.6 正式基线为 next-turn upkeep、无 fallback、启用 Fate/Debuff。"""

    def __init__(self, upkeep_immediate=True, fallback=False, debuff=False,
                 fate=False, life_goals=16):
        self.upkeep_immediate = upkeep_immediate
        self.fallback = fallback
        self.debuff = debuff
        self.fate = fate
        self.life_goals = life_goals

    @property
    def name(self):
        if self.debuff and self.fate and self.life_goals == 18:
            return 'V06'
        return ('TEST' if self.fallback else 'BASE') + ('-A' if self.upkeep_immediate else '-B')


CONFIGS = {
    'BASE-A': Config(True, False),
    'BASE-B': Config(False, False),
    'TEST-A': Config(True, True),
    'TEST-B': Config(False, True),
    'V06': Config(False, False, True, True, 18),
}


# ----------------------------------------------------------------------
# 支付求解器（模块级纯函数，便于单元测试）
# ----------------------------------------------------------------------

def _solve_cost_solutions(cost, pool, temps, subs, wildcards=0,
                          stop_after_first=False):
    """枚举 cost 的支付结果；搜索规则与 solve_cost 共用此唯一实现。"""
    units = []
    for sym, n in cost.items():
        units.extend([sym] * n)
    units.sort(key=lambda s: 0 if s in ('GL', 'BL') else 1)

    tunits = []
    for ti, (sym, n, _cid) in enumerate(temps):
        tunits.extend((ti, ui, sym) for ui in range(n))

    p = Counter(pool)
    pool_used = Counter()
    temp_units_used = set()
    subs_used = set()
    wildcard_symbols = []
    # F10 的骰子 GL 仍在 pool['GL'] 中。它们可选择直付真实 GL，或
    # 作为一次性普通资源万能单位；两种用途共享同一批物理 GL。
    dice_gl_remaining = wildcards
    dice_gl_direct_used = 0
    results = []
    seen = set()

    def record_result():
        clean_pool = Counter({s: n for s, n in pool_used.items() if n})
        temp_indices = sorted(temp_units_used)
        result = {
            'pool_used': clean_pool,
            'temps_used': [temps[ti][2]
                           for ti in sorted({ti for ti, _ in temp_indices})],
            'temp_units_used': [temps[ti][2] for ti, _ in temp_indices],
            'subs_used': [subs[si] for si in sorted(subs_used)],
            'wildcard_symbols': list(wildcard_symbols),
            'dice_gl_direct_used': dice_gl_direct_used,
        }
        key = (
            tuple(sorted(clean_pool.items())),
            tuple(result['temp_units_used']),
            tuple(result['subs_used']),
            tuple(result['wildcard_symbols']),
            result['dice_gl_direct_used'],
        )
        if key not in seen:
            seen.add(key)
            results.append(result)

    def rec(i):
        nonlocal dice_gl_remaining, dice_gl_direct_used
        if i == len(units):
            record_result()
            return stop_after_first
        sym = units[i]
        if sym == 'GL':
            # 真实 GL 优先消耗非骰子 GL；需要时才消耗一颗 dice GL。
            if p['GL'] > dice_gl_remaining:
                p['GL'] -= 1
                pool_used['GL'] += 1
                if rec(i + 1):
                    return True
                p['GL'] += 1
                pool_used['GL'] -= 1
            if dice_gl_remaining > 0 and p['GL'] > 0:
                dice_gl_remaining -= 1
                dice_gl_direct_used += 1
                p['GL'] -= 1
                pool_used['GL'] += 1
                if rec(i + 1):
                    return True
                pool_used['GL'] -= 1
                p['GL'] += 1
                dice_gl_direct_used -= 1
                dice_gl_remaining += 1
        elif p[sym] > 0:
            p[sym] -= 1
            pool_used[sym] += 1
            if rec(i + 1):
                return True
            p[sym] += 1
            pool_used[sym] -= 1
        for ti, ui, tsym in tunits:
            if (ti, ui) not in temp_units_used and tsym == sym:
                temp_units_used.add((ti, ui))
                if rec(i + 1):
                    return True
                temp_units_used.discard((ti, ui))
        if sym not in ('GL', 'BL'):
            for si, (f, t) in enumerate(subs):
                if si in subs_used or t != sym or f in ('GL', 'BL'):
                    continue
                if p[f] > 0:
                    p[f] -= 1
                    pool_used[f] += 1
                    subs_used.add(si)
                    if rec(i + 1):
                        return True
                    subs_used.discard(si)
                    p[f] += 1
                    pool_used[f] -= 1
                for ti, ui, tsym in tunits:
                    if (ti, ui) not in temp_units_used and tsym == f:
                        temp_units_used.add((ti, ui))
                        subs_used.add(si)
                        if rec(i + 1):
                            return True
                        temp_units_used.discard((ti, ui))
                        subs_used.discard(si)
        if sym in NORMAL and dice_gl_remaining > 0 and p['GL'] > 0:
            dice_gl_remaining -= 1
            p['GL'] -= 1
            pool_used['GL'] += 1
            wildcard_symbols.append(sym)
            if rec(i + 1):
                return True
            wildcard_symbols.pop()
            pool_used['GL'] -= 1
            p['GL'] += 1
            dice_gl_remaining += 1
        return False

    rec(0)
    return results


def solve_cost(cost, pool, temps, subs, wildcards=0):
    """判定 cost 能否用 pool+temps 支付，subs 提供一次性符号替代。

    cost  : {sym: n}
    pool  : Counter（不会被修改）
    temps : [(sym, n, cid)]  一次性临时资源（按资源单位消耗）
    subs  : [(from, to)]     每条每次结算限用一次；GL/BL 需求不可被替代，
                             也不可由替代产生 GL/BL（本牌池中 from/to 均为普通符号）
    wildcards : 可支付普通 H/K/R/M 的骰子万能单位；不支付 GL/BL。
    返回 {'pool_used': Counter, 'temps_used': [cid],
          'temp_units_used': [cid 每个已消耗单位], 'subs_used': [(f,t)],
          'wildcard_symbols': [被万能单位支付的普通需求] } 或 None。
    搜索顺序偏好：池内直付 > 临时资源 > 替代；不修改入参。
    """
    results = _solve_cost_solutions(
        cost, pool, temps, subs, wildcards, stop_after_first=True)
    return results[0] if results else None


def solve_cost_all(cost, pool, temps, subs, wildcards=0):
    """返回所有正式支付结果；不暴露递归路径且不修改入参。"""
    return _solve_cost_solutions(cost, pool, temps, subs, wildcards)


def discounted_cost(cost, sym):
    """把 cost 中 1 个普通符号需求减 1（童年折扣 / ME-01 / MH-01）。

    特殊符号（GL/BL）不可被减免。
    """
    if sym not in NORMAL:
        return dict(cost)
    c2 = dict(cost)
    if c2.get(sym, 0) > 0:
        c2[sym] -= 1
        if c2[sym] == 0:
            del c2[sym]
    return c2


# ----------------------------------------------------------------------
# 游戏主类
# ----------------------------------------------------------------------

class Game:
    MAX_TURNS = 100
    DEBUFF_BAD_LUCK_THRESHOLD = 5

    def __init__(self, cfg, strat_factory, rng, stats=None, shuffle=True,
                 forced_goals=None, defer_childhood=False):
        self.cfg = cfg
        self.rng = rng
        self.stats = stats if stats is not None else Stats()
        self._forced = []  # 测试用：强制骰面队列

        # 牌库（youth 26 / middle 23 / elder 21）
        self.decks = {}
        for s in STAGE_ORDER:
            d = list(STAGE_DECKS[s])
            if shuffle:
                rng.shuffle(d)
            self.decks[s] = d

        self.market = []
        self.market_entry = {}   # cid -> 进入市场的回合
        self.stage = 'youth'
        self.turn = 0
        self.final_pending = False
        self.game_over = False

        self.goals = (list(forced_goals) if forced_goals else
                      rng.sample(range(1, cfg.life_goals + 1), 2))
        self.initial_goals = list(self.goals)
        self.cv = {c: [] for c in 'HKRWP'}   # 每类一叠，list[-1] = active
        self.hand = []
        self.abebe_held = False
        self.abebe_used = False
        self.acquired = {}  # cid -> 取得回合

        # v0.6 Debuff：唯一牌、不放回、同时最多 1 张 current。
        self.debuff_deck = [c['id'] for c in DEBUFFS] if cfg.debuff else []
        if shuffle:
            rng.shuffle(self.debuff_deck)
        self.current_debuff = None
        self.debuff_turns_remaining = 0
        self.debuff_active_from_turn = None
        self.debuff_active_turns = 0
        self.debuff_history = []
        self.debuff_durations = []
        self.used_once_cards = set()
        self.bad_luck_accumulator = 0
        self.bad_luck_recorded_turn = None

        # v0.6 Fate：独立市场；取得牌永久留在 fate_stack。
        self.fate_deck = [c['id'] for c in FATES] if cfg.fate else []
        if shuffle:
            rng.shuffle(self.fate_deck)
        self.fate_market = []
        self.fate_discard = []
        self.fate_stack = []
        self.active_fate = None
        self.fate_active_from_turn = None
        self.fate_acquired_this_turn = None
        self.real_bl_spent_on_fate = 0
        self.pending_fate_immediate = None

        # 回合内状态
        self.dice = []
        self.stable_pool = Counter()
        self.pool = Counter()
        self.flex_choice = {}
        self.temp_dice = 0
        self.dice_gl_flexible = 0
        self.pre_roll_open = False
        self.pre_debuff_cancel = None
        self.purchased_this_turn = []
        self.pending_new = []
        self.purchase_result = None
        self.oh04_used = False
        self.mh04_used = False
        self.mh04_trigger_seen = False
        self.mh04_pending = False
        self.bl_seen_this_turn = False
        self.reroll_happened_this_turn = False
        self.special_reroll_used = set()
        self.single_used = False
        self.yk05_used = False
        self.fallback_used = False
        self.maintenance_result = None
        self.market_cleanup_result = None
        self.stable_resources_finalized = False

        # 童年单人 Draft：Goal 先公开 → 3选1 → 2选1 → 随机第3张。
        # Runtime 可暂停等待外部选择；Simulator 仍由原策略同步完成同一流程。
        self.strat = strat_factory(self) if strat_factory is not None else None
        self.childhood_pool = [c['id'] for c in CARDS.values() if c['type'] == 'C']
        rng.shuffle(self.childhood_pool)
        self.childhood_kept = []
        self.childhood_draft_round = 0
        self.childhood_complete = False

        if not defer_childhood:
            if self.strat is None:
                raise ValueError('strat_factory is required for automatic childhood draft')
            while not self.childhood_complete:
                opts = self.childhood_candidates()
                pick = self.strat.pick_childhood(opts)
                if pick not in opts:
                    pick = opts[0]
                self.submit_childhood_pick(pick)

    def childhood_candidates(self):
        """返回当前童年 Draft 的合法候选；完成后为空。"""
        if self.childhood_complete:
            return ()
        count = 3 if self.childhood_draft_round == 0 else 2
        return tuple(self.childhood_pool[:count])

    def submit_childhood_pick(self, cid):
        """提交当前轮童年选择。非法选择无副作用。"""
        options = self.childhood_candidates()
        if cid not in options:
            return False
        self.childhood_kept.append(cid)
        del self.childhood_pool[:len(options)]
        self.childhood_draft_round += 1
        if self.childhood_draft_round == 2:
            self._complete_childhood_draft()
        return True

    def _complete_childhood_draft(self):
        """加入无选择的第3张童年牌，并完成成年游戏准备。"""
        self.childhood_kept.append(self.childhood_pool.pop(0))
        for cid in self.childhood_kept:
            if CARDS[cid].get('abebe'):
                self.abebe_held = True
            else:
                self.hand.append(cid)
        self.childhood_complete = True
        self._fill_market()
        self._fill_fate_market()
        self.stats.start_game(self)

    # ---------------- 基础查询 ----------------

    def active(self, cls):
        st = self.cv[cls]
        return st[-1] if st else None

    def active_cards(self):
        return [self.active(c) for c in 'HKRWP' if self.active(c)]

    def hand_effect_cards(self, effect):
        """返回当前可使用、带有指定字段的手牌。

        Event 仍受 D05/F09 等事件封锁；童年牌不属于 Event，不能被误封锁。
        """
        return [cid for cid in self.hand
                if CARDS[cid].get(effect)
                and (CARDS[cid]['type'] != 'E' or self.event_usage_allowed())]

    def find_hand_effect(self, effect):
        cards = self.hand_effect_cards(effect)
        return cards[0] if cards else None

    def _consume_hand_card(self, cid):
        """消耗一张已校验的手牌，并写入其既有统计口径。"""
        self.hand.remove(cid)
        bucket = ('event_uses' if CARDS[cid]['type'] == 'E'
                  else 'childhood_uses')
        self.stats.game[bucket][cid] += 1

    def active_debuff_card(self):
        if (self.current_debuff and self.debuff_active_from_turn is not None
                and self.turn >= self.debuff_active_from_turn):
            return DEBUFF_BY_ID[self.current_debuff]
        return None

    def active_fate_card(self):
        if (self.active_fate and self.fate_active_from_turn is not None
                and self.turn >= self.fate_active_from_turn):
            return FATE_BY_ID[self.active_fate]
        return None

    def _effect_cards(self):
        return [c for c in (self.active_debuff_card(), self.active_fate_card()) if c]

    def _event_blocked(self):
        return any(c.get('block_event') for c in self._effect_cards())

    def event_usage_allowed(self):
        """当前回合 Event 是否可使用；D05/F09 等统一从这里判断。"""
        return not self._event_blocked()

    def _blocked_type(self):
        d = self.active_debuff_card()
        return d.get('block_type') if d else None

    def debuff_experienced_count(self):
        return len(self.debuff_history) + (1 if self.current_debuff else 0)

    def event_acquired_count(self):
        return sum(self.stats.game.get('event_buys', {}).values())

    def fate_window(self):
        """Fate 只在成年回合编号为 3 的倍数时开放。"""
        return bool(self.cfg.fate and self.turn > 0 and self.turn % 3 == 0)

    def scoring_counts(self):
        return {
            'fate_count': len(self.fate_stack),
            'debuff_count': self.debuff_experienced_count(),
            'event_count': self.event_acquired_count(),
        }

    def _fill_fate_market(self):
        if not self.cfg.fate:
            return
        while len(self.fate_market) < 2:
            if not self.fate_deck:
                if not self.fate_discard:
                    break
                self.fate_deck = list(self.fate_discard)
                self.fate_discard = []
                self.rng.shuffle(self.fate_deck)
                self.stats.run['fate_deck_reshuffles'] += 1
            cid = self.fate_deck.pop(0)
            self.fate_market.append(cid)
            self.stats.fate(cid)['exposure'] += 1

    def est_turns_left(self):
        left = sum(len(self.decks[s]) for s in STAGE_ORDER) + len(self.market)
        return left // 3 + 1

    def _fill_market(self):
        while len(self.market) < 5:
            for s in STAGE_ORDER:
                if self.decks[s]:
                    cid = self.decks[s].pop(0)
                    self.market.append(cid)
                    self.market_entry[cid] = self.turn
                    self.stage = s
                    self.stats.card(cid)['exposure'] += 1
                    break
            else:
                break

    def _roll(self, n):
        def one():
            face = self._forced.pop(0) if self._forced else self.rng.choice(SYMS)
            return face
        return [one() for _ in range(n)]

    # ---------------- 支付：购买 ----------------

    def _purchase_limit(self):
        fate = self.active_fate_card()
        return fate.get('purchase_limit', 2) if fate else 2

    def _gl_take_threshold(self):
        debuff = self.active_debuff_card()
        return debuff.get('gl_threshold', 3) if debuff else 3

    def normal_rerolls_locked(self):
        fate = self.active_fate_card()
        return bool(fate and fate.get('lock_reroll_on_bl')
                    and self.bl_seen_this_turn)

    def normal_reroll_rounds(self):
        return max(0, 2 + sum(c.get('reroll_delta', 0)
                              for c in self._effect_cards()))

    def current_dice_count(self):
        fate = self.active_fate_card()
        fate_dice = fate.get('dice_delta', 0) if fate else 0
        return min(7, 4 + self._extra_die_count() + self.temp_dice + fate_dice)

    def _reset_turn_roll_state(self):
        self.temp_dice = 0
        self.dice_gl_flexible = 0
        self.pre_roll_open = True
        self.pre_debuff_cancel = None
        self.oh04_used = False
        self.mh04_used = False
        self.mh04_trigger_seen = False
        self.mh04_pending = False
        self.bl_seen_this_turn = False
        self.reroll_happened_this_turn = False
        self.special_reroll_used = set()
        self.single_used = False
        self.yk05_used = False
        self.fallback_used = False
        self.purchased_this_turn = []
        self.pending_new = []
        self.purchase_result = None
        self.maintenance_result = None
        self.market_cleanup_result = None
        self.stable_resources_finalized = False

    def flexible_stable_options(self):
        """当前 active 在回合开始需要玩家指定的稳定资源候选。"""
        return {
            cid: tuple(CARDS[cid]['flex']) for cid in self.active_cards()
            if CARDS[cid].get('flex')
        }

    def fixed_stable_resources(self):
        """不含每回合 flex 选择的稳定资源；只从当前 active 派生。"""
        pool = Counter()
        for cid in self.active_cards():
            for sym, n in CARDS[cid].get('provide', []):
                pool[sym] += n
        return pool

    def finalize_stable_resources(self, flex_choices):
        """验证并一次性固化本回合 stable_pool；非法输入不改状态。"""
        if self.stable_resources_finalized:
            return False
        options = self.flexible_stable_options()
        if set(flex_choices) != set(options):
            return False
        if any(choice not in options[cid]
               for cid, choice in flex_choices.items()):
            return False

        pool = self.fixed_stable_resources()
        self.flex_choice = dict(flex_choices)
        for cid, choice in self.flex_choice.items():
            pool[choice] += 1
            self.stats.card(cid)['flex_turns'] += 1
        self.stable_pool = pool
        self.stable_resources_finalized = True
        self.stats.run['stable_total'] += sum(pool.values())
        for sym, n in pool.items():
            self.stats.run['stable_by'][sym] += n
        return True

    def start_adult_turn(self):
        """初始化当前成年回合，并派生固定稳定资源与 flex 合法候选。"""
        if not self.childhood_complete:
            raise RuntimeError('childhood draft is not complete')
        if self.pre_roll_open or self.game_over:
            raise RuntimeError('adult turn cannot be started now')
        if self.turn == 0:
            self.turn = 1
        self.stats.game['turns'] += 1
        self.stats.game['stage_turns'][self.stage] += 1
        self.fate_acquired_this_turn = None
        self.real_bl_spent_on_fate = 0
        self._reset_turn_roll_state()
        self.stable_pool = self.fixed_stable_resources()

        fate_now = self.active_fate_card()
        if fate_now:
            self.stats.fate(fate_now['id'])['active_turns'] += 1
        if self._event_blocked():
            blocked = sum(CARDS[cid]['type'] == 'E' for cid in self.hand)
            if blocked:
                for effect in self._effect_cards():
                    if effect.get('block_event'):
                        target = (self.stats.fate(effect['id'])
                                  if effect['id'] in FATE_BY_ID
                                  else self.stats.debuff(effect['id']))
                        target['ability']['event_blocked_opportunities'] += blocked
        return self.flexible_stable_options()

    def start_first_adult_turn(self):
        """启动首个成年回合，停在首次掷骰前窗口。供 Runtime 使用。"""
        if self.turn != 0:
            raise RuntimeError('only the first adult turn may be started here')
        self.start_adult_turn()

    def pre_roll_hand_cards(self):
        """掷骰前可声明的一次性手牌；不创建新的能力系统。"""
        return [cid for cid in self.hand
                if (CARDS[cid].get('temp_dice')
                    or CARDS[cid].get('pre_cancel_debuff'))
                and (CARDS[cid]['type'] != 'E' or self.event_usage_allowed())]

    def apply_pre_roll_declarations(self, flex_choices, hand_card_ids=()):
        """原子应用首次掷骰前的资源选择与一次性声明。"""
        if not self.pre_roll_open or self.stable_resources_finalized:
            return None
        if (not isinstance(hand_card_ids, (list, tuple))
                or len(hand_card_ids) != len(set(hand_card_ids))):
            return None
        options = self.flexible_stable_options()
        if set(flex_choices) != set(options):
            return None
        if any(choice not in options[cid]
               for cid, choice in flex_choices.items()):
            return None
        eligible = self.pre_roll_hand_cards()
        if any(cid not in eligible for cid in hand_card_ids):
            return None
        pre_cancels = [cid for cid in hand_card_ids
                       if CARDS[cid].get('pre_cancel_debuff')]
        if len(pre_cancels) > 1:
            return None

        if not self.finalize_stable_resources(flex_choices):
            return None
        used = []
        for cid in hand_card_ids:
            card = CARDS[cid]
            if card.get('temp_dice'):
                self.temp_dice += card['temp_dice']
            if card.get('pre_cancel_debuff'):
                self.pre_debuff_cancel = cid
                self.stats.game['pre_debuff_cancel_declared'] += 1
            self._consume_hand_card(cid)
            used.append(cid)
        return {'flex_resource_choices': dict(flex_choices),
                'used_card_ids': used}

    def use_pre_roll_temp_dice(self, cid):
        """在首次掷骰前使用一张带 temp_dice 的合法手牌。"""
        if (not self.pre_roll_open or cid not in self.pre_roll_hand_cards()
                or not CARDS[cid].get('temp_dice')):
            return False
        self.temp_dice += CARDS[cid]['temp_dice']
        self._consume_hand_card(cid)
        return True

    def roll_first_dice(self):
        """按当前正式骰池掷首次骰；骰子随机仅由引擎 RNG 产生。"""
        if not self.pre_roll_open:
            raise RuntimeError('first-roll window is not open')
        if not self.stable_resources_finalized:
            if not self.finalize_stable_resources({}):
                raise RuntimeError('flexible stable resources must be chosen first')
        self.dice = self._roll(self.current_dice_count())
        self._after_dice_change()
        self.stats.run['dice_turns'] += 1
        self.stats.run['dice_n_sum'] += len(self.dice)
        self.pre_roll_open = False
        return list(self.dice)

    def first_normal_reroll_status(self):
        """返回首轮正常重掷的标准可重掷范围；不处理 C12 等后续特例。"""
        if self.pre_roll_open or not self.dice:
            raise RuntimeError('first dice have not been rolled')
        if self.normal_rerolls_locked():
            return {
                'remaining_rounds': self.normal_reroll_rounds(),
                'rerollable_indices': [],
                'frozen_indices': list(range(len(self.dice))),
                'freeze_reason': 'normal_rerolls_locked',
            }
        return {
            'remaining_rounds': self.normal_reroll_rounds(),
            'rerollable_indices': [i for i, face in enumerate(self.dice)
                                   if face != 'BL'],
            'frozen_indices': [i for i, face in enumerate(self.dice)
                               if face == 'BL'],
            'freeze_reason': 'bad_luck',
        }

    def post_roll_resources(self):
        """当前骰面锁定时可用于购买预览的资源，不修改正式资源池。

        YK-05 每回合一次 R→K 转换以 yk05_used 为唯一使用状态，在此按
        R-1/K+1 派生：购买、剩余资源与后续维护因此共用同一口径。
        """
        pool = Counter(self.dice) + Counter(self.stable_pool)
        if self.yk05_used and pool.get('R', 0) > 0:
            pool['R'] -= 1
            pool['K'] += 1
        return pool + Counter()

    def yk05_conversion_available(self):
        """YK-05 每回合一次 R→K 转换当前是否可发动。"""
        return (self.active('K') == 'YK-05'
                and not self.yk05_used
                and self.post_roll_resources().get('R', 0) >= 1)

    def apply_yk05_conversion(self):
        """发动 YK-05 的 R→K 转换；非法或本回合已用时无副作用返回 False。"""
        if not self.yk05_conversion_available():
            return False
        self.yk05_used = True
        self.stats.run['yk05_used'] += 1
        self.stats.card('YK-05')['ability']['convert_turn'] += 1
        return True

    def purchase_payment_resources(self, pool_override=None):
        """返回完整真实支付池及其中可选择 F10 转换的 dice GL 数。"""
        pool = Counter(pool_override if pool_override is not None else self.pool)
        wildcard_count = 0
        if (self.active_fate_card() or {}).get('wildcard_normal'):
            if pool_override is None:
                wildcard_count = self.dice_gl_flexible
            else:
                wildcard_count = min(self.dice.count('GL'), pool.get('GL', 0))
        return (Counter({s: n for s, n in pool.items() if n > 0}),
                wildcard_count)

    def use_reroll_temp_dice(self, cid):
        """在正常重掷开始前使用 temp_dice 手牌，并补掷至正式上限。"""
        if (self.pre_roll_open or not self.dice or cid not in self.hand
                or not CARDS[cid].get('temp_dice')
                or (CARDS[cid]['type'] == 'E' and not self.event_usage_allowed())):
            return False
        self.temp_dice += CARDS[cid]['temp_dice']
        added = max(0, self.current_dice_count() - len(self.dice))
        self.dice.extend(self._roll(added))
        self._after_dice_change()
        self._consume_hand_card(cid)
        return True

    def normal_reroll_is_legal(self, selected, c12_index=None):
        """校验一轮标准正常重掷；不修改骰面或童年物状态。"""
        if self.normal_rerolls_locked():
            return False
        if not isinstance(selected, (list, tuple, set)):
            return False
        selected = list(selected)
        if any(type(i) is not int for i in selected):
            return False
        if len(selected) != len(set(selected)):
            return False
        if any(not 0 <= i < len(self.dice) for i in selected):
            return False
        if c12_index is not None:
            if (type(c12_index) is not int or c12_index not in selected
                    or not self.abebe_held or self.abebe_used
                    or self.dice[c12_index] != 'BL'):
                return False
        if any(self.dice[i] == 'BL' and i != c12_index for i in selected):
            return False
        return True

    def apply_normal_reroll(self, selected, c12_index=None):
        """执行一轮标准正常重掷；仅显式指定的 C12 可解冻一颗 BL。"""
        if not self.normal_reroll_is_legal(selected, c12_index):
            return False
        count = self._apply_reroll(
            set(selected),
            abebe_index=c12_index,
            allow_abebe=c12_index is not None,
        )
        self.reroll_happened_this_turn = True
        self._after_dice_change()
        return count

    def available_special_rerolls(self):
        """active 或手牌提供的独立单骰重掷；不消耗正常轮数。"""
        choices = []
        sources = [(cid, 'active') for cid in self.active_cards()]
        sources.extend((cid, 'hand') for cid in self.hand_effect_cards('reroll'))
        for cid, source in sources:
            rule = CARDS[cid].get('reroll')
            if not rule or (source == 'active' and cid in self.special_reroll_used):
                continue
            filt = rule.get('filter')
            if filt == 'non_bl':
                indices = [i for i, face in enumerate(self.dice) if face != 'BL']
            elif filt == 'non_gl_bl':
                indices = [i for i, face in enumerate(self.dice)
                           if face not in ('GL', 'BL')]
            elif filt == 'bl':
                indices = [i for i, face in enumerate(self.dice) if face == 'BL']
            else:
                continue
            if indices:
                choices.append({'card_id': cid, 'source': source,
                                'target_indices': indices})
        return choices

    def apply_special_reroll(self, card_id, die_index):
        """执行一张 active 的独立单骰重掷；非法输入不改状态。"""
        option = next((item for item in self.available_special_rerolls()
                       if item['card_id'] == card_id), None)
        if option is None or type(die_index) is not int \
                or die_index not in option['target_indices']:
            return False
        self.dice[die_index] = self._roll(1)[0]
        if option['source'] == 'hand':
            self._consume_hand_card(card_id)
        else:
            self.special_reroll_used.add(card_id)
        if option['source'] == 'active' and card_id == 'OH-04':
            self.oh04_used = True
            self.stats.run['oh04_bl_rerolls'] += 1
        self.stats.run['single_rerolls'] += 1
        if option['source'] == 'active':
            self.stats.card(card_id)['ability']['reroll'] += 1
        self.reroll_happened_this_turn = True
        self._after_dice_change()
        return True

    def extra_reroll_hand_cards(self):
        """骰后、实际重掷前可换取额外正常重掷轮的手牌。"""
        if self.reroll_happened_this_turn:
            return []
        return self.hand_effect_cards('extra_reroll_rounds')

    def use_extra_reroll_card(self, cid):
        """消耗一张手牌，返回其增加的正常重掷轮数。"""
        if cid not in self.extra_reroll_hand_cards():
            return False
        extra = CARDS[cid]['extra_reroll_rounds']
        self._consume_hand_card(cid)
        return extra

    def _after_dice_change(self):
        """所有真实骰面变化后的共享正式检查。"""
        self.bl_seen_this_turn = self.bl_seen_this_turn or 'BL' in self.dice
        if (self.active('H') == 'MH-04' and not self.mh04_used
                and not self.mh04_trigger_seen and self.dice.count('BL') >= 2):
            self.mh04_trigger_seen = True
            self.mh04_pending = True

    def mh04_reaction_status(self):
        if not self.mh04_pending:
            return None
        return [i for i, face in enumerate(self.dice) if face == 'BL']

    def resolve_mh04_reaction(self, use=False, target_index=None):
        """处理已触发的 MH-04 反应；skip 只关闭本回合该次反应。"""
        eligible = self.mh04_reaction_status()
        if eligible is None:
            return False
        if use:
            if type(target_index) is not int or target_index not in eligible:
                return False
            self.dice[target_index] = 'H'
            self.mh04_used = True
            self.stats.run['mh04_converted'] += 1
            self.stats.card('MH-04')['ability']['bl_convert'] += 1
        self.mh04_pending = False
        return True

    def _cost_options(self, cid, seen_types, seen_total, free_base=False):
        """返回候选牌当前真实成本的所有合法普通资源减免方案。"""
        card = CARDS[cid]
        cost = {} if free_base else dict(card['cost'])
        tags = []
        for effect in self._effect_cards():
            extra = effect.get('extra_cost')
            if extra and extra['type'] == card['type']:
                cost[extra['sym']] = cost.get(extra['sym'], 0) + extra['n']
                tags.append((effect['id'], 'extra_cost'))

        discounts = []
        fate = self.active_fate_card()
        general = fate.get('discount') if fate else None
        if general and general['type'] == card['type']:
            discounts.append((general['sym'], general['n'], fate['id']))
        disc = fate.get('first_discount') if fate else None
        if disc:
            applies_type = disc['type'] is None or disc['type'] == card['type']
            is_first = seen_total == 0 if disc['type'] is None else seen_types[card['type']] == 0
            if applies_type and is_first:
                discounts.append((disc['sym'], disc['n'], fate['id']))

        options = [(cost, list(tags))]
        for sym, amount, source in discounts:
            for _ in range(amount):
                next_options = []
                for current, current_tags in options:
                    eligible = ([sym] if sym != 'any' else
                                [s for s in NORMAL if current.get(s, 0) > 0])
                    if not eligible:
                        next_options.append((current, current_tags))
                    for target in eligible:
                        if current.get(target, 0) <= 0:
                            continue
                        next_options.append((discounted_cost(current, target),
                                             current_tags + [(source, 'discount')]))
                options = next_options
        return options

    def _temps_available(self):
        out = []
        for cid in self.hand:
            c = CARDS[cid]
            if c['type'] == 'E' and self._event_blocked():
                continue
            if c.get('temp_res'):
                out.append((c['temp_res'][0], c['temp_res'][1], cid))
            elif c.get('temp_gl'):
                out.append(('GL', c['temp_gl'], cid))
        return out

    def _discounts_available(self):
        return [(cid, CARDS[cid]['discount_type']) for cid in self.hand
                if CARDS[cid].get('discount_type')]

    def _subs_for(self, ctype):
        subs = []
        for cls in 'HKRWP':
            top = self.active(cls)
            if not top:
                continue
            for opt in CARDS[top].get('sub_buy', []):
                if opt['scope'] is None or ctype in opt['scope']:
                    subs.append((opt['from'], opt['to']))
        return subs

    def _try_order(self, order, methods, base_pool, pool_override=None):
        pool = Counter(base_pool)
        temps = self._temps_available()
        temp_remaining = {cid: n for sym, n, cid in temps}
        discounts = self._discounts_available()
        pool_used = Counter()
        temps_used = []
        temp_units_used = []
        wildcard_symbols = []
        discounts_used = []
        gl_take = []
        subs_used = []
        purchase_mods = []
        seen_types = Counter()
        seen_total = 0
        dice_gl_remaining = min(self.dice_gl_flexible, pool.get('GL', 0))
        dice_gl_true_gl_used = 0

        def available_temps():
            return [(s, temp_remaining[t], t) for s, n, t in temps
                    if temp_remaining[t] > 0]

        def find_payment(cid, options, allow_childhood=True):
            subs = self._subs_for(CARDS[cid]['type'])
            for cost, tags in options:
                sol = solve_cost(cost, pool, available_temps(), subs,
                                 dice_gl_remaining)
                if sol is not None:
                    return sol, tags, None
                if not allow_childhood:
                    continue
                for d_cid, d_type in discounts:
                    if d_type != CARDS[cid]['type'] or d_cid in discounts_used:
                        continue
                    for sym in list(cost.keys()):
                        if sym not in NORMAL:
                            continue
                        sol = solve_cost(discounted_cost(cost, sym), pool,
                                          available_temps(), subs,
                                          dice_gl_remaining)
                        if sol is not None:
                            return sol, tags, d_cid
            return None

        def consume_solution(sol):
            nonlocal dice_gl_remaining, dice_gl_true_gl_used
            pool.subtract(sol['pool_used'])
            pool_used.update(sol['pool_used'])
            dice_gl_remaining -= (len(sol['wildcard_symbols'])
                                  + sol['dice_gl_direct_used'])
            dice_gl_true_gl_used += sol['dice_gl_direct_used']
            for t in sol['temp_units_used']:
                temp_remaining[t] -= 1
            temp_units_used.extend(sol['temp_units_used'])
            for t in sol['temps_used']:
                if t not in temps_used:
                    temps_used.append(t)
            subs_used.extend(sol['subs_used'])
            wildcard_symbols.extend(sol['wildcard_symbols'])

        for cid, method in zip(order, methods):
            c = CARDS[cid]
            if c['type'] == self._blocked_type():
                return None
            if method == 'gl':
                threshold = self._gl_take_threshold()
                gl_avail = pool.get('GL', 0) + sum(
                    temp_remaining[t] for s, n, t in temps
                    if s == 'GL' and temp_remaining[t] > 0)
                if gl_avail < threshold:
                    return None
                take = min(threshold, pool.get('GL', 0))
                direct_dice = max(0, take - max(0, pool.get('GL', 0)
                                                - dice_gl_remaining))
                if direct_dice > dice_gl_remaining:
                    return None
                pool['GL'] -= take
                pool_used['GL'] += take
                dice_gl_remaining -= direct_dice
                dice_gl_true_gl_used += direct_dice
                need = threshold - take
                for s, n, t in temps:
                    if need <= 0:
                        break
                    if s == 'GL' and temp_remaining[t] > 0:
                        take_temp = min(need, temp_remaining[t])
                        temp_remaining[t] -= take_temp
                        temp_units_used.extend([t] * take_temp)
                        if t not in temps_used:
                            temps_used.append(t)
                        need -= take_temp
                if need > 0:
                    return None
                extra_payment = find_payment(
                    cid, self._cost_options(cid, seen_types, seen_total,
                                            free_base=True),
                    allow_childhood=False)
                if extra_payment is None:
                    return None
                sol, tags, _ = extra_payment
                consume_solution(sol)
                purchase_mods.extend(tags)
                gl_take.append(cid)
            else:
                payment = find_payment(
                    cid, self._cost_options(cid, seen_types, seen_total))
                if payment is None:
                    return None
                sol, tags, discount_used = payment
                consume_solution(sol)
                purchase_mods.extend(tags)
                if discount_used:
                    discounts_used.append(discount_used)
            seen_types[c['type']] += 1
            seen_total += 1
        return {'cards': tuple(order), 'methods': tuple(methods),
                'pool_used': pool_used, 'temps_used': temps_used,
                'temp_units_used': temp_units_used,
                'wildcard_symbols': wildcard_symbols,
                'dice_gl_true_gl_used': dice_gl_true_gl_used,
                'discounts_used': discounts_used, 'gl_take': gl_take,
                'subs_used': subs_used, 'purchase_mods': purchase_mods}

    def try_acquire(self, cards, pool_override=None):
        """判定一组牌（0~2 张）能否取得。无副作用。

        返回支付方案 dict 或 None。偏好：最少临时资源 > 最少童年折扣 > 保留 GL
        （即同为可行时优先普通支付，把 GL 留作 3 好运免费取得）。
        """
        cards = tuple(cards)
        if not cards:
            return {'cards': (), 'methods': (), 'pool_used': Counter(),
                    'temps_used': [], 'temp_units_used': [],
                    'discounts_used': [], 'gl_take': [],
                    'subs_used': [], 'purchase_mods': [],
                    'dice_gl_true_gl_used': 0}
        base_pool = pool_override if pool_override is not None else self.pool
        results = []
        for order in itertools.permutations(cards):
            for methods in itertools.product(('res', 'gl'), repeat=len(order)):
                pl = self._try_order(order, methods, base_pool)
                if pl is not None:
                    if len(cards) == 1:
                        return pl
                    results.append(pl)
        if not results:
            return None
        results.sort(key=lambda p: (len(p['temps_used']),
                                    len(p['discounts_used']),
                                    0 if 'gl' not in p['methods'] else 1))
        return results[0]

    def affordable_plans(self, pool_override=None):
        """当前所有合法购买方案（单张 + 可同时支付的双张）。"""
        singles = [cid for cid in self.market
                   if self.try_acquire((cid,), pool_override=pool_override)]
        plans = [(c,) for c in singles]
        if self._purchase_limit() >= 2:
            for i in range(len(singles)):
                for j in range(i + 1, len(singles)):
                    if self.try_acquire((singles[i], singles[j]),
                                        pool_override=pool_override):
                        plans.append((singles[i], singles[j]))
        return plans

    def enumerate_legal_purchase_plans(self, pool_override=None):
        """纯计算普通牌 0~2 张的完整支付结果，供 Runtime 展示。

        购买排列只用于穷举共享资源，不进入返回值；本窗口取得的牌也不会
        加入支付来源。当前切片只启用已持有 Childhood、3GL 与既有替代。
        """
        base_pool, wildcard_count = self.purchase_payment_resources(pool_override)
        # 与 Simulator 的购买求解共用同一支付来源：允许使用的手牌
        # Childhood 临时资源、C05 GL 及 YE-01/YE-02 临时资源都在这里。
        temps = self._temps_available()
        discounts = []
        for cid in self.hand:
            card = CARDS[cid]
            if card['type'] != 'C':
                continue
            if card.get('discount_type'):
                discounts.append((cid, card['discount_type']))

        temp_counts = {cid: n for _sym, n, cid in temps}
        collected = {}

        def positive_dict(counter):
            return dict(sorted((s, n) for s, n in counter.items() if n > 0))

        def available_temps(remaining):
            return [(sym, remaining[cid], cid) for sym, _n, cid in temps
                    if remaining[cid] > 0]

        def add_plan(card_ids, pool, temp_remaining, spent, consumed,
                     gl_targets, discount_uses, substitution_uses,
                     purchase_modifiers, wildcard_symbols,
                     dice_gl_true_gl_used):
            event_temp_use = []
            for sym, count, cid in temps:
                used = count - temp_remaining[cid]
                if used and CARDS[cid]['type'] == 'E':
                    event_temp_use.append({'event_card_id': cid,
                                           'resource': sym, 'amount': used})
            child_consumed = sorted(cid for cid in consumed
                                    if CARDS[cid]['type'] == 'C')
            event_consumed = sorted(item['event_card_id']
                                    for item in event_temp_use)
            canonical = {
                'card_ids': list(card_ids),
                'spent_resources': positive_dict(spent),
                'consumed_childhood_card_ids': child_consumed,
                'consumed_event_card_ids': event_consumed,
                'event_temporary_resources_used': sorted(
                    event_temp_use, key=lambda x: x['event_card_id']),
                'gl_free_acquisitions': sorted(gl_targets),
                'discounts_used': sorted(
                    discount_uses,
                    key=lambda x: (x['childhood_card_id'], x['card_id'],
                                   x['symbol'])),
                'substitutions_used': sorted(
                    substitution_uses,
                    key=lambda x: (x['card_id'], x['from'], x['to'])),
                'purchase_modifiers': sorted(
                    purchase_modifiers,
                    key=lambda x: (x['source_card_id'], x['card_id'], x['kind'])),
                'f10_dice_gl_conversions': [
                    {'resource': sym} for sym in wildcard_symbols],
                'f10_dice_gl_true_gl_used': dice_gl_true_gl_used,
                'remaining_resources': positive_dict(pool),
            }
            state_key = (
                tuple(canonical['card_ids']),
                tuple(sorted(canonical['remaining_resources'].items())),
                tuple(canonical['consumed_childhood_card_ids']),
                tuple(canonical['consumed_event_card_ids']),
                tuple((e['event_card_id'], e['resource'], e['amount'])
                      for e in canonical['event_temporary_resources_used']),
                tuple(canonical['gl_free_acquisitions']),
                tuple((d['childhood_card_id'], d['card_id'])
                      for d in canonical['discounts_used']),
                tuple((s['card_id'], s['from'], s['to'])
                      for s in canonical['substitutions_used']),
                tuple((m['source_card_id'], m['card_id'], m['kind'])
                      for m in canonical['purchase_modifiers']),
                tuple(c['resource'] for c in canonical['f10_dice_gl_conversions']),
                canonical['f10_dice_gl_true_gl_used'],
            )
            if state_key in collected:
                return
            id_payload = dict(canonical)
            if not purchase_modifiers:
                id_payload.pop('purchase_modifiers')
            if not wildcard_symbols:
                id_payload.pop('f10_dice_gl_conversions')
            if not dice_gl_true_gl_used:
                id_payload.pop('f10_dice_gl_true_gl_used')
            encoded = json.dumps(id_payload, sort_keys=True,
                                 separators=(',', ':')).encode('utf-8')
            canonical['plan_id'] = 'purchase_' + hashlib.sha256(encoded).hexdigest()[:16]
            collected[state_key] = canonical

        def enumerate_order(card_ids, order):
            def visit(index, pool, temp_remaining, spent, consumed,
                      gl_targets, discount_uses, substitution_uses,
                      purchase_modifiers, wildcard_symbols,
                      dice_gl_remaining, dice_gl_true_gl_used):
                if index == len(order):
                    add_plan(card_ids, pool, temp_remaining, spent, consumed, gl_targets,
                             discount_uses, substitution_uses,
                             purchase_modifiers, wildcard_symbols,
                             dice_gl_true_gl_used)
                    return
                cid = order[index]
                card = CARDS[cid]
                if card['type'] == self._blocked_type():
                    return
                subs = self._subs_for(card['type'])

                seen_types = Counter(CARDS[item]['type'] for item in order[:index])
                cost_options = self._cost_options(cid, seen_types, index)
                discount_options = []
                already_used = {d['childhood_card_id'] for d in discount_uses}
                for current_cost, tags in cost_options:
                    discount_options.append((Counter(current_cost), None, tags))
                    for d_cid, d_type in discounts:
                        if d_type != card['type'] or d_cid in already_used:
                            continue
                        for sym in NORMAL:
                            if current_cost.get(sym, 0) > 0:
                                discount_options.append((
                                    Counter(discounted_cost(current_cost, sym)),
                                    {'childhood_card_id': d_cid,
                                     'card_id': cid, 'symbol': sym}, tags))

                for cost, discount_use, tags in discount_options:
                    solutions = solve_cost_all(
                        cost, pool, available_temps(temp_remaining), subs,
                        dice_gl_remaining)
                    for solution in solutions:
                        next_pool = Counter(pool)
                        next_pool.subtract(solution['pool_used'])
                        next_temps = dict(temp_remaining)
                        for used_cid in solution['temp_units_used']:
                            next_temps[used_cid] -= 1
                        next_spent = Counter(spent)
                        next_spent.update(solution['pool_used'])
                        next_consumed = set(consumed)
                        next_consumed.update(solution['temps_used'])
                        next_discounts = list(discount_uses)
                        if discount_use:
                            next_consumed.add(discount_use['childhood_card_id'])
                            next_discounts.append(discount_use)
                        next_subs = list(substitution_uses)
                        next_subs.extend(
                            {'card_id': cid, 'from': f, 'to': t}
                            for f, t in solution['subs_used'])
                        next_modifiers = list(purchase_modifiers)
                        next_modifiers.extend(
                            {'source_card_id': source, 'card_id': cid, 'kind': kind}
                            for source, kind in tags)
                        visit(index + 1, next_pool, next_temps, next_spent,
                              next_consumed, gl_targets, next_discounts,
                              next_subs, next_modifiers,
                              wildcard_symbols + solution['wildcard_symbols'],
                              dice_gl_remaining
                              - len(solution['wildcard_symbols'])
                              - solution['dice_gl_direct_used'],
                              dice_gl_true_gl_used
                              + solution['dice_gl_direct_used'])

                threshold = self._gl_take_threshold()
                for extra_cost, tags in self._cost_options(
                        cid, seen_types, index, free_base=True):
                    gl_cost = Counter(extra_cost)
                    gl_cost['GL'] += threshold
                    gl_solutions = solve_cost_all(
                        gl_cost, pool, available_temps(temp_remaining), subs,
                        dice_gl_remaining)
                    for solution in gl_solutions:
                        next_pool = Counter(pool)
                        next_pool.subtract(solution['pool_used'])
                        next_temps = dict(temp_remaining)
                        for used_cid in solution['temp_units_used']:
                            next_temps[used_cid] -= 1
                        next_spent = Counter(spent)
                        next_spent.update(solution['pool_used'])
                        next_consumed = set(consumed)
                        next_consumed.update(solution['temps_used'])
                        next_subs = list(substitution_uses)
                        next_subs.extend(
                            {'card_id': cid, 'from': f, 'to': t}
                            for f, t in solution['subs_used'])
                        next_modifiers = list(purchase_modifiers)
                        next_modifiers.extend(
                            {'source_card_id': source, 'card_id': cid,
                             'kind': kind} for source, kind in tags)
                        visit(index + 1, next_pool, next_temps, next_spent,
                              next_consumed, gl_targets + [cid], discount_uses,
                              next_subs, next_modifiers,
                              wildcard_symbols + solution['wildcard_symbols'],
                              dice_gl_remaining
                              - len(solution['wildcard_symbols'])
                              - solution['dice_gl_direct_used'],
                              dice_gl_true_gl_used
                              + solution['dice_gl_direct_used'])

            visit(0, Counter(base_pool), dict(temp_counts), Counter(), set(),
                  [], [], [], [], [], wildcard_count, 0)

        max_cards = min(2, self._purchase_limit())
        for count in range(max_cards + 1):
            for card_ids in itertools.combinations(self.market, count):
                if not card_ids:
                    add_plan((), base_pool, dict(temp_counts), Counter(), set(),
                             [], [], [], [], [], 0)
                    continue
                for order in itertools.permutations(card_ids):
                    enumerate_order(card_ids, order)

        return sorted(
            collected.values(),
            key=lambda p: (
                len(p['card_ids']), tuple(p['card_ids']),
                p['plan_id']))

    def apply_precomputed_purchase_plan(self, plan, pool_override=None):
        """原子兑现 Runtime 已选中的普通购买方案；不会重新求解支付。"""
        try:
            cards = list(plan['card_ids'])
            spent = Counter(plan['spent_resources'])
            remaining = Counter(plan['remaining_resources'])
            consumed = list(plan['consumed_childhood_card_ids'])
            consumed_events = list(plan.get('consumed_event_card_ids', []))
            event_temp_used = list(plan.get('event_temporary_resources_used', []))
            gl_targets = list(plan['gl_free_acquisitions'])
            discount_uses = list(plan['discounts_used'])
            substitution_uses = list(plan['substitutions_used'])
            purchase_modifiers = list(plan.get('purchase_modifiers', []))
            f10_conversions = list(plan.get('f10_dice_gl_conversions', []))
            f10_true_gl_used = plan.get('f10_dice_gl_true_gl_used', 0)
        except (KeyError, TypeError, ValueError):
            return False

        current_pool = Counter(pool_override if pool_override is not None
                               else self.pool)
        current_pool = Counter({s: n for s, n in current_pool.items() if n > 0})
        if (len(cards) > 2 or len(cards) != len(set(cards))
                or any(cid not in self.market for cid in cards)
                or any(n < 0 for n in spent.values())
                or any(n < 0 for n in remaining.values())):
            return False
        reconstructed = Counter(remaining)
        reconstructed.update(spent)
        reconstructed = Counter({s: n for s, n in reconstructed.items() if n > 0})
        if reconstructed != current_pool:
            return False
        if (len(consumed) != len(set(consumed))
                or any(cid not in self.hand or CARDS[cid]['type'] != 'C'
                       for cid in consumed)):
            return False
        if (len(consumed_events) != len(set(consumed_events))
                or (consumed_events and not self.event_usage_allowed())
                or any(cid not in self.hand or CARDS[cid]['type'] != 'E'
                       or not CARDS[cid].get('temp_res')
                       for cid in consumed_events)):
            return False
        event_usage_by_id = {}
        for use in event_temp_used:
            try:
                cid, sym, amount = (use['event_card_id'], use['resource'],
                                    use['amount'])
            except (KeyError, TypeError):
                return False
            if (cid in event_usage_by_id or cid not in consumed_events
                    or type(amount) is not int or amount <= 0
                    or CARDS[cid]['temp_res'][0] != sym
                    or amount > CARDS[cid]['temp_res'][1]):
                return False
            event_usage_by_id[cid] = amount
        if set(event_usage_by_id) != set(consumed_events):
            return False
        if (len(gl_targets) != len(set(gl_targets))
                or any(cid not in cards for cid in gl_targets)):
            return False

        for use in discount_uses:
            try:
                source = use['childhood_card_id']
                target = use['card_id']
                symbol = use['symbol']
            except (KeyError, TypeError):
                return False
            if (source not in consumed or target not in cards
                    or CARDS[source].get('discount_type') != CARDS[target]['type']
                    or symbol not in NORMAL
                    or CARDS[target]['cost'].get(symbol, 0) <= 0):
                return False
        for use in substitution_uses:
            try:
                target = use['card_id']
                sub = (use['from'], use['to'])
            except (KeyError, TypeError):
                return False
            if target not in cards or sub not in self._subs_for(CARDS[target]['type']):
                return False
        active_effect_ids = {card['id'] for card in self._effect_cards()}
        for use in purchase_modifiers:
            try:
                source, target, kind = (use['source_card_id'], use['card_id'],
                                        use['kind'])
            except (KeyError, TypeError):
                return False
            if (source not in active_effect_ids or target not in cards
                    or kind not in ('discount', 'extra_cost')):
                return False
        if (type(f10_true_gl_used) is not int or f10_true_gl_used < 0
                or len(f10_conversions) + f10_true_gl_used > self.dice_gl_flexible
                or (f10_conversions and
                    not (self.active_fate_card() or {}).get('wildcard_normal'))
                or any(item.get('resource') not in NORMAL
                       for item in f10_conversions)):
            return False

        # 至此所有会导致拒绝的条件均已验证，下面才统一提交正式状态。
        self.pool = Counter(remaining)
        self.dice_gl_flexible -= f10_true_gl_used
        self._consume_wildcards({
            'wildcard_symbols': [item['resource'] for item in f10_conversions]})
        for cid in consumed:
            self.hand.remove(cid)
            self.stats.game['childhood_uses'][cid] += 1
        for cid in consumed_events:
            self.hand.remove(cid)
            self.stats.game['event_uses'][cid] += 1
        for cid in gl_targets:
            self.stats.game['gl_takes'] += 1
            self.stats.game['gl_take_value'] += CARDS[cid]['vp'] + sum(
                n for _, n in CARDS[cid].get('provide', []))
        for use in substitution_uses:
            sub = (use['from'], use['to'])
            for cls in 'HKRWP':
                top = self.active(cls)
                if top and any(o['from'] == sub[0] and o['to'] == sub[1]
                               for o in CARDS[top].get('sub_buy', [])):
                    self.stats.card(top)['ability']['sub_buy'] += 1
                    break
        for use in purchase_modifiers:
            source, kind = use['source_card_id'], use['kind']
            if source in DEBUFF_BY_ID:
                self.stats.debuff(source)['ability'][kind + '_mattered'] += 1
            elif source in FATE_BY_ID:
                self.stats.fate(source)['ability'][kind + '_mattered'] += 1
        for cid in cards:
            self.market.remove(cid)
            self.acquired[cid] = self.turn
            self.stats.card(cid)['bought'] += 1
            self.stats.card(cid)['dwell_sum'] += (
                self.turn - self.market_entry.get(cid, self.turn))
            self.stats.card(cid)['dwell_n'] += 1
            if CARDS[cid]['type'] == 'E':
                self.hand.append(cid)
                self.stats.game['event_buys'][cid] += 1
            else:
                self.pending_new.append(cid)
                self.stats.game['buys_by_class'][CARDS[cid]['type']] += 1
        self.purchased_this_turn = cards
        self.stats.game['purchases'] += len(cards)
        self.purchase_result = {
            'purchased_card_ids': list(cards),
            'consumed_childhood_card_ids': list(consumed),
            'consumed_event_card_ids': list(consumed_events),
            'event_temporary_resources_used': [dict(use) for use in event_temp_used],
            'spent_resources': dict(sorted((s, n) for s, n in spent.items()
                                            if n > 0)),
            'remaining_resources': dict(sorted((s, n) for s, n in remaining.items()
                                                if n > 0)),
        }
        return True

    def place_pending_card(self, cid, placement='top'):
        """把一张已取得的 CV 放入正式履历堆；非法选择无副作用。"""
        if cid not in self.pending_new:
            return False
        cls = CARDS[cid]['type']
        if cls in ('W', 'P'):
            if placement != 'top':
                return False
        elif cls in ('H', 'K', 'R'):
            if placement not in ('top', 'bury'):
                return False
        else:
            return False
        if placement == 'bury':
            self.cv[cls].insert(0, cid)
            self.stats.card(cid)['placed_bury'] += 1
        else:
            self.cv[cls].append(cid)
            self.stats.card(cid)['placed_top'] += 1
        self.pending_new.remove(cid)
        return True

    def enumerate_joint_purchase_plans(self, pool_override=None):
        """纯计算 Fate Window 的完整普通牌 + Fate 联合支付方案。"""
        normal_plans = self.enumerate_legal_purchase_plans(pool_override)
        fate_options = [None]
        if self.fate_window() and self.fate_acquired_this_turn is None:
            fate_options.extend(self.fate_market)
        collected = {}

        def positive(counter):
            return dict(sorted((sym, n) for sym, n in counter.items() if n > 0))

        for normal in normal_plans:
            ordinary_ids = list(normal['card_ids'])
            for fate_cid in fate_options:
                fate_cost = Counter(FATE_BY_ID[fate_cid]['cost']) \
                    if fate_cid else Counter()
                ordinary_remaining = Counter(normal['remaining_resources'])
                fate_temps = [
                    ('GL', CARDS[cid]['temp_gl'], cid)
                    for cid in self.hand_effect_cards('temp_gl')
                    if cid not in normal['consumed_childhood_card_ids']]
                fate_dice_gl_available = max(
                    0, self.purchase_payment_resources(pool_override)[1]
                    - len(normal['f10_dice_gl_conversions'])
                    - normal.get('f10_dice_gl_true_gl_used', 0))
                fate_solutions = ([{'pool_used': Counter(), 'temps_used': [],
                                    'temp_units_used': [], 'subs_used': [],
                                    'wildcard_symbols': [],
                                    'dice_gl_direct_used': 0}]
                                  if fate_cid is None else
                                  solve_cost_all(fate_cost,
                                                  ordinary_remaining,
                                                  fate_temps, [],
                                                  fate_dice_gl_available))
                for fate_solution in fate_solutions:
                    remaining = Counter(ordinary_remaining)
                    remaining.subtract(fate_solution['pool_used'])
                    spent = Counter(normal['spent_resources'])
                    spent.update(fate_solution['pool_used'])
                    child_consumed = sorted(set(
                        normal['consumed_childhood_card_ids']) |
                        set(fate_solution['temps_used']))
                    fate_temp_used = [
                        {'childhood_card_id': cid, 'resource': 'GL',
                         'amount': fate_solution['temp_units_used'].count(cid)}
                        for cid in fate_solution['temps_used']]
                    canonical = {
                        'ordinary_card_ids': ordinary_ids,
                        'fate_card_id': fate_cid,
                        'spent_resources': positive(spent),
                        'remaining_resources': positive(remaining),
                        'consumed_childhood_card_ids': child_consumed,
                        'consumed_event_card_ids': list(
                            normal['consumed_event_card_ids']),
                        'event_temporary_resources_used': [
                            dict(item) for item in
                            normal['event_temporary_resources_used']],
                        'gl_free_acquisitions': list(
                            normal['gl_free_acquisitions']),
                        'discounts_used': [dict(item) for item in
                                           normal['discounts_used']],
                        'substitutions_used': [dict(item) for item in
                                               normal['substitutions_used']],
                        'purchase_modifiers': [dict(item) for item in
                                               normal['purchase_modifiers']],
                        'f10_dice_gl_conversions': [dict(item) for item in
                                                    normal['f10_dice_gl_conversions']],
                        'f10_dice_gl_true_gl_used': (
                            normal.get('f10_dice_gl_true_gl_used', 0)
                            + fate_solution['dice_gl_direct_used']),
                        'fate_payment': {
                            'required_resources': positive(fate_cost),
                            'spent_resources': positive(
                                Counter(fate_solution['pool_used'])),
                            'childhood_temporary_resources_used': fate_temp_used,
                        },
                    }
                    # 完整正式结果本身就是去重键；求解顺序不进入结构。
                    encoded = json.dumps(canonical, sort_keys=True,
                                         separators=(',', ':')).encode('utf-8')
                    state_key = encoded
                    if state_key in collected:
                        continue
                    canonical['plan_id'] = (
                        'joint_' + hashlib.sha256(encoded).hexdigest()[:16])
                    collected[state_key] = canonical

        return sorted(
            collected.values(),
            key=lambda p: (len(p['ordinary_card_ids']),
                           tuple(p['ordinary_card_ids']),
                           p['fate_card_id'] or '', p['plan_id']))

    @staticmethod
    def _choose_simulator_joint_payment(plans):
        """Simulator 的确定性支付偏好；不参与规则层合法性裁剪。"""
        return min(
            plans,
            key=lambda p: (
                len(p['consumed_event_card_ids']),
                len(p['consumed_childhood_card_ids']),
                len(p['discounts_used']),
                len(p['gl_free_acquisitions']),
                len(p['f10_dice_gl_conversions']),
                p['plan_id']))

    def _joint_plan(self, cards, fate_cid):
        target = (tuple(cards), fate_cid)
        matches = [plan for plan in self.enumerate_joint_purchase_plans()
                   if (tuple(plan['ordinary_card_ids']), plan['fate_card_id']) == target]
        return self._choose_simulator_joint_payment(matches) if matches else None

    def joint_plans(self):
        """列出普通 0~2 张与 Fate 0~1 张的联合合法动作。"""
        grouped = {}
        for plan in self.enumerate_joint_purchase_plans():
            target = (tuple(plan['ordinary_card_ids']), plan['fate_card_id'])
            grouped.setdefault(target, []).append(plan)
        return {target: self._choose_simulator_joint_payment(plans)
                for target, plans in grouped.items()}

    def execute_purchase(self, choice, plan=None):
        plan = plan or self.try_acquire(choice)
        if plan is None:
            return False
        if (plan.get('dice_gl_true_gl_used', 0)
                + len(plan.get('wildcard_symbols', []))
                > self.dice_gl_flexible):
            raise AssertionError('F10 dice GL double-spend detected')
        for sym, n in plan['pool_used'].items():
            if self.pool.get(sym, 0) < n:
                raise AssertionError('double-spend detected: %s' % sym)
            self.pool[sym] -= n
        self.dice_gl_flexible -= plan.get('dice_gl_true_gl_used', 0)
        self._consume_wildcards(plan)
        for t in plan['temps_used']:
            self.hand.remove(t)
            self.stats.game['event_uses' if CARDS[t]['type'] == 'E' else 'childhood_uses'][t] += 1
        for d in plan['discounts_used']:
            self.hand.remove(d)
            self.stats.game['childhood_uses'][d] += 1
        for cid in plan['gl_take']:
            self.stats.game['gl_takes'] += 1
            self.stats.game['gl_take_value'] += CARDS[cid]['vp'] + sum(
                n for _, n in CARDS[cid].get('provide', []))
        for sub in plan['subs_used']:
            # 记录替代能力实际使用
            for cls in 'HKRWP':
                top = self.active(cls)
                if top and any(o['from'] == sub[0] and o['to'] == sub[1]
                               for o in CARDS[top].get('sub_buy', [])):
                    self.stats.card(top)['ability']['sub_buy'] += 1
        for source, kind in plan.get('purchase_mods', []):
            if source in DEBUFF_BY_ID:
                self.stats.debuff(source)['ability'][kind + '_mattered'] += 1
            elif source in FATE_BY_ID:
                self.stats.fate(source)['ability'][kind + '_mattered'] += 1
        for cid in choice:
            self.market.remove(cid)
            self.acquired[cid] = self.turn
            self.stats.card(cid)['bought'] += 1
            self.stats.card(cid)['dwell_sum'] += self.turn - self.market_entry[cid]
            self.stats.card(cid)['dwell_n'] += 1
            if CARDS[cid]['type'] == 'E':
                self.hand.append(cid)
                self.stats.game['event_buys'][cid] += 1
            else:
                self.pending_new.append(cid)
                self.stats.game['buys_by_class'][CARDS[cid]['type']] += 1
        self.purchased_this_turn = list(choice)
        self.stats.game['purchases'] += len(choice)
        return True

    def _consume_wildcards(self, solution):
        symbols = solution.get('wildcard_symbols', [])
        if len(symbols) > self.dice_gl_flexible:
            raise AssertionError('F10 wildcard double-spend detected')
        for sym in symbols:
            self.stats.fate('F10')['ability']['wildcard_' + sym] += 1
        self.dice_gl_flexible -= len(symbols)

    def _prepare_fate_immediate(self, cid):
        """保存 F01/F03 的一次性即时选择；不调用 strategy。"""
        if self.pending_fate_immediate is not None:
            raise AssertionError('a Fate immediate decision is already pending')
        fate = FATE_BY_ID[cid]
        immediate = fate.get('immediate')
        if immediate == 'set_active':
            options = [card for cls in 'HKR' for card in self.cv[cls]]
            if options:
                self.pending_fate_immediate = {
                    'decision_id': 'fate_immediate:%d:%s' % (self.turn, cid),
                    'kind': 'fate_set_active',
                    'fate_card_id': cid,
                    'candidate_card_ids': list(options),
                    'legal_actions': [{'card_id': card_id}
                                      for card_id in options],
                }
        elif immediate == 'replace_goal':
            unused = [g for g in range(1, self.cfg.life_goals + 1)
                      if g not in self.goals]
            shown = self.rng.sample(unused, min(3, len(unused)))
            if shown:
                actions = [{'choice': 'keep'}]
                actions.extend(
                    {'choice': 'replace', 'goal_index': index,
                     'new_goal_id': goal_id}
                    for index in range(len(self.goals)) for goal_id in shown)
                self.pending_fate_immediate = {
                    'decision_id': 'fate_immediate:%d:%s' % (self.turn, cid),
                    'kind': 'fate_replace_goal',
                    'fate_card_id': cid,
                    'candidate_goal_ids': list(shown),
                    'current_goal_ids': list(self.goals),
                    'legal_actions': actions,
                }

    def pending_fate_immediate_decision(self):
        """返回当前即时 Fate 决策的只读副本；不会重抽 F03 候选。"""
        pending = self.pending_fate_immediate
        if pending is None:
            return None
        result = {key: value for key, value in pending.items()
                  if key != 'legal_actions'}
        for key in ('candidate_card_ids', 'candidate_goal_ids',
                    'current_goal_ids'):
            if key in result:
                result[key] = list(result[key])
        result['legal_actions'] = [dict(action)
                                   for action in pending['legal_actions']]
        return result

    def resolve_fate_immediate(self, decision_id, action):
        """原子完成已保存的 F01/F03 选择；非法或 stale 输入无副作用。"""
        pending = self.pending_fate_immediate
        if (pending is None or decision_id != pending['decision_id']
                or not isinstance(action, dict)
                or action not in pending['legal_actions']):
            return False

        cid = pending['fate_card_id']
        if pending['kind'] == 'fate_set_active':
            if set(action) != {'card_id'}:
                return False
            choice = action['card_id']
            cls = CARDS[choice]['type']
            if choice not in self.cv[cls]:
                return False
            self.cv[cls].remove(choice)
            self.cv[cls].append(choice)
            self.stats.fate(cid)['ability']['set_active'] += 1
        elif pending['kind'] == 'fate_replace_goal':
            if self.goals != pending['current_goal_ids']:
                return False
            if action == {'choice': 'keep'}:
                pass
            elif set(action) == {'choice', 'goal_index', 'new_goal_id'}:
                index, new_goal = action['goal_index'], action['new_goal_id']
                old_goal = self.goals[index]
                self.goals[index] = new_goal
                self.stats.game['goal_swapped_out'][old_goal] += 1
                self.stats.game['goal_swapped_in'][new_goal] += 1
                self.stats.fate(cid)['ability']['replace_goal'] += 1
            else:
                return False
        else:
            return False

        self.pending_fate_immediate = None
        self._settle_fate_market()
        return True

    def _resolve_simulator_fate_immediate(self):
        """Simulator 只负责选择；合法性与执行仍走共享 resolver。"""
        decision = self.pending_fate_immediate_decision()
        if decision is None:
            return True
        if decision['kind'] == 'fate_set_active':
            choice = self.strat.choose_active_reset(
                tuple(decision['candidate_card_ids']))
            action = {'card_id': choice}
        else:
            choice = self.strat.choose_goal_replacement(
                tuple(decision['candidate_goal_ids']))
            action = ({'choice': 'keep'} if choice is None else
                      {'choice': 'replace', 'goal_index': choice[0],
                       'new_goal_id': choice[1]})
        return self.resolve_fate_immediate(decision['decision_id'], action)

    def _acquire_fate(self, cid, cost):
        if not self.fate_window():
            raise AssertionError('Fate may only be acquired during a Fate Window')
        if self.fate_acquired_this_turn is not None:
            raise AssertionError('at most one Fate may be acquired per turn')
        for sym, n in cost.items():
            self.pool[sym] -= n
            if sym == 'BL':
                self.real_bl_spent_on_fate += n
                self.stats.run['real_BL_spent_on_fate'] += n
            elif sym == 'GL':
                self.stats.run['GL_spent_on_fate'] += n
        self._record_fate_acquisition(cid)

    def _record_fate_acquisition(self, cid):
        """记录已完成支付的 Fate 取得；保留既有 market/stack/immediate 流程。"""
        self.fate_market.remove(cid)
        if self.active_fate:
            self.stats.fate(self.active_fate)['times_replaced'] += 1
        self.fate_stack.append(cid)
        self.active_fate = cid
        self.fate_active_from_turn = self.turn + 1
        self.fate_acquired_this_turn = cid
        self.stats.fate(cid)['acquired'] += 1
        self.stats.game['fate_acquired'].append(cid)
        self._prepare_fate_immediate(cid)

    def _settle_fate_market(self):
        if not self.fate_window():
            return
        if self.fate_acquired_this_turn is None and self.fate_market:
            discarded = self.fate_market.pop(0)
            self.fate_discard.append(discarded)
            self.stats.run['fate_declined_turns'] += 1
            self.stats.run['fate_market_discards'] += 1
        self._fill_fate_market()

    def execute_joint(self, action, plan, locked_resources=None):
        """原子兑现一个完整 precomputed joint plan；不重新运行支付求解。"""
        try:
            cards, fate_cid = tuple(action[0]), action[1]
            plan_id = plan['plan_id']
            payload = {key: value for key, value in plan.items()
                       if key != 'plan_id'}
            encoded = json.dumps(payload, sort_keys=True,
                                 separators=(',', ':')).encode('utf-8')
            if plan_id != 'joint_' + hashlib.sha256(encoded).hexdigest()[:16]:
                return False
            if (list(cards) != plan['ordinary_card_ids']
                    or fate_cid != plan['fate_card_id']):
                return False
            spent = Counter(plan['spent_resources'])
            remaining = Counter(plan['remaining_resources'])
            consumed_children = list(plan['consumed_childhood_card_ids'])
            consumed_events = list(plan['consumed_event_card_ids'])
            event_temp_used = list(plan['event_temporary_resources_used'])
            gl_targets = list(plan['gl_free_acquisitions'])
            discounts = list(plan['discounts_used'])
            substitutions = list(plan['substitutions_used'])
            modifiers = list(plan['purchase_modifiers'])
            conversions = list(plan['f10_dice_gl_conversions'])
            f10_true_gl_used = plan.get('f10_dice_gl_true_gl_used', 0)
            fate_payment = plan['fate_payment']
        except (KeyError, TypeError, ValueError, IndexError):
            return False

        if self.pending_fate_immediate is not None:
            return False
        source_pool = locked_resources if locked_resources is not None else self.pool
        current_pool = Counter({sym: n for sym, n in Counter(source_pool).items()
                                if n > 0})
        available_dice_gl = (self.purchase_payment_resources(source_pool)[1]
                             if locked_resources is not None
                             else self.dice_gl_flexible)
        reconstructed = Counter(remaining)
        reconstructed.update(spent)
        reconstructed = Counter({sym: n for sym, n in reconstructed.items() if n > 0})
        if (len(cards) > self._purchase_limit()
                or len(cards) != len(set(cards))
                or any(cid not in self.market for cid in cards)
                or any(n < 0 for n in spent.values())
                or any(n < 0 for n in remaining.values())
                or reconstructed != current_pool):
            return False
        if fate_cid:
            if (not self.fate_window() or self.fate_acquired_this_turn is not None
                    or fate_cid not in self.fate_market):
                return False
        elif fate_payment.get('required_resources'):
            return False

        if (len(consumed_children) != len(set(consumed_children))
                or any(cid not in self.hand or CARDS[cid]['type'] != 'C'
                       for cid in consumed_children)
                or len(consumed_events) != len(set(consumed_events))
                or (consumed_events and not self.event_usage_allowed())
                or any(cid not in self.hand or CARDS[cid]['type'] != 'E'
                       or not CARDS[cid].get('temp_res')
                       for cid in consumed_events)):
            return False
        event_ids = set()
        for use in event_temp_used:
            try:
                cid, sym, amount = (use['event_card_id'], use['resource'],
                                    use['amount'])
            except (KeyError, TypeError):
                return False
            card = CARDS.get(cid, {})
            if (cid in event_ids or cid not in consumed_events
                    or type(amount) is not int or amount <= 0
                    or not card.get('temp_res') or card['temp_res'][0] != sym
                    or amount > card['temp_res'][1]):
                return False
            event_ids.add(cid)
        if event_ids != set(consumed_events):
            return False

        if (len(gl_targets) != len(set(gl_targets))
                or any(cid not in cards for cid in gl_targets)
                or type(f10_true_gl_used) is not int or f10_true_gl_used < 0
                or len(conversions) + f10_true_gl_used > available_dice_gl
                or (conversions and
                    not (self.active_fate_card() or {}).get('wildcard_normal'))
                or any(item.get('resource') not in NORMAL for item in conversions)):
            return False
        for use in discounts:
            try:
                source, target, symbol = (use['childhood_card_id'],
                                          use['card_id'], use['symbol'])
            except (KeyError, TypeError):
                return False
            if (source not in consumed_children or target not in cards
                    or CARDS[source].get('discount_type') != CARDS[target]['type']
                    or symbol not in NORMAL):
                return False
        for use in substitutions:
            try:
                target = use['card_id']
                sub = (use['from'], use['to'])
            except (KeyError, TypeError):
                return False
            if target not in cards or sub not in self._subs_for(CARDS[target]['type']):
                return False
        active_effect_ids = {card['id'] for card in self._effect_cards()}
        for use in modifiers:
            if (use.get('source_card_id') not in active_effect_ids
                    or use.get('card_id') not in cards
                    or use.get('kind') not in ('discount', 'extra_cost')):
                return False

        expected_fate = Counter(FATE_BY_ID[fate_cid]['cost']) if fate_cid else Counter()
        fate_spent = Counter(fate_payment.get('spent_resources', {}))
        fate_temps = list(fate_payment.get(
            'childhood_temporary_resources_used', []))
        fate_paid = Counter(fate_spent)
        used_fate_temp_ids = set()
        for use in fate_temps:
            try:
                source, resource, amount = (use['childhood_card_id'],
                                            use['resource'], use['amount'])
            except (KeyError, TypeError):
                return False
            if (source in used_fate_temp_ids or source not in consumed_children
                    or resource != 'GL' or type(amount) is not int or amount <= 0
                    or CARDS[source].get('temp_gl') != amount):
                return False
            used_fate_temp_ids.add(source)
            fate_paid['GL'] += amount
        if (Counter(fate_payment.get('required_resources', {})) != expected_fate
                or fate_paid != expected_fate
                or any(fate_spent[sym] > spent[sym] for sym in fate_spent)):
            return False

        # 所有拒绝条件已检查；以下统一提交，不再进入 solver。
        self.pool = Counter(remaining)
        if locked_resources is not None:
            self.dice_gl_flexible = available_dice_gl
        self.dice_gl_flexible -= f10_true_gl_used
        self._consume_wildcards({
            'wildcard_symbols': [item['resource'] for item in conversions]})
        for cid in consumed_children:
            self.hand.remove(cid)
            self.stats.game['childhood_uses'][cid] += 1
        for cid in consumed_events:
            self.hand.remove(cid)
            self.stats.game['event_uses'][cid] += 1
        for cid in gl_targets:
            self.stats.game['gl_takes'] += 1
            self.stats.game['gl_take_value'] += CARDS[cid]['vp'] + sum(
                n for _, n in CARDS[cid].get('provide', []))
        for use in substitutions:
            sub = (use['from'], use['to'])
            for cls in 'HKRWP':
                top = self.active(cls)
                if top and any(opt['from'] == sub[0] and opt['to'] == sub[1]
                               for opt in CARDS[top].get('sub_buy', [])):
                    self.stats.card(top)['ability']['sub_buy'] += 1
                    break
        for use in modifiers:
            source, kind = use['source_card_id'], use['kind']
            if source in DEBUFF_BY_ID:
                self.stats.debuff(source)['ability'][kind + '_mattered'] += 1
            elif source in FATE_BY_ID:
                self.stats.fate(source)['ability'][kind + '_mattered'] += 1
        for cid in cards:
            self.market.remove(cid)
            self.acquired[cid] = self.turn
            self.stats.card(cid)['bought'] += 1
            self.stats.card(cid)['dwell_sum'] += self.turn - self.market_entry[cid]
            self.stats.card(cid)['dwell_n'] += 1
            if CARDS[cid]['type'] == 'E':
                self.hand.append(cid)
                self.stats.game['event_buys'][cid] += 1
            else:
                self.pending_new.append(cid)
                self.stats.game['buys_by_class'][CARDS[cid]['type']] += 1
        self.purchased_this_turn = list(cards)
        self.stats.game['purchases'] += len(cards)
        self.purchase_result = {
            'purchased_card_ids': list(cards),
            'consumed_childhood_card_ids': list(consumed_children),
            'consumed_event_card_ids': list(consumed_events),
            'spent_resources': dict(spent),
            'remaining_resources': dict(remaining),
        }
        if fate_cid:
            self.real_bl_spent_on_fate += fate_spent.get('BL', 0)
            self.stats.run['real_BL_spent_on_fate'] += fate_spent.get('BL', 0)
            self.stats.run['GL_spent_on_fate'] += fate_spent.get('GL', 0) + sum(
                item['amount'] for item in fate_temps)
            self._record_fate_acquisition(fate_cid)
        if self.pending_fate_immediate is None:
            self._settle_fate_market()
        return True

    # ---------------- Debuff / 逆境 ----------------

    def lose_active(self, cid, reason='misfortune'):
        cls = CARDS[cid]['type']
        if self.cv[cls] and self.cv[cls][-1] == cid:
            self.cv[cls].pop()
        self.stats.game['losses_by_class'][cls] += 1
        self.stats.card(cid)['lost_' + reason] = self.stats.card(cid).get('lost_' + reason, 0) + 1

    def _finish_current_debuff(self, reason):
        cid = self.current_debuff
        if not cid:
            return
        self.debuff_history.append(cid)
        self.debuff_durations.append(self.debuff_active_turns)
        self.stats.debuff(cid)['duration_sum'] += self.debuff_active_turns
        self.stats.debuff(cid)['duration_n'] += 1
        self.stats.run[reason] += 1
        self.current_debuff = None
        self.debuff_turns_remaining = 0
        self.debuff_active_from_turn = None
        self.debuff_active_turns = 0

    def debuff_trigger_status(self):
        """返回跨回合厄运累计是否达到 Debuff 阈值；不修改状态。"""
        if not self.cfg.debuff:
            return {'triggered': False, 'real_bl': 0, 'virtual_bl': 0,
                    'virtual_sources': []}
        real_bl = self.bad_luck_accumulator
        virtual_sources = [c for c in self._effect_cards() if c.get('virtual_bl')]
        virtual_bl = sum(c['virtual_bl'] for c in virtual_sources)
        final_bl = real_bl + virtual_bl
        return {'triggered': final_bl >= self.DEBUFF_BAD_LUCK_THRESHOLD,
                'real_bl': real_bl,
                'virtual_bl': virtual_bl, 'virtual_sources': virtual_sources}

    def record_final_bad_luck(self):
        """每回合只记录一次最终真实骰面中的 BL，并返回本次新增数量。"""
        if not self.cfg.debuff or self.bad_luck_recorded_turn == self.turn:
            return 0
        real_bl = self.dice.count('BL')
        self.bad_luck_accumulator += real_bl
        self.bad_luck_recorded_turn = self.turn
        return real_bl

    def _record_debuff_trigger(self, status):
        self.stats.run['debuff_triggers'] += 1
        self.stats.game['debuff_triggers'] += 1
        for source in status['virtual_sources']:
            if (source['id'] == 'D08'
                    and status['real_bl'] + status['virtual_bl']
                    - source['virtual_bl'] < self.DEBUFF_BAD_LUCK_THRESHOLD):
                self.stats.run['d08_trigger_mattered'] += 1
                self.stats.debuff('D08')['ability']['virtual_bl_mattered'] += 1

    def _consume_debuff_trigger(self, status):
        """结算一次已确认触发：真实累计清零并记录统计。"""
        if not status['triggered']:
            return False
        self.bad_luck_accumulator = 0
        self._record_debuff_trigger(status)
        return True

    def _draw_next_debuff(self):
        """抽取下一张唯一 Debuff；新牌从下一完整回合开始生效。"""
        if not self.debuff_deck:
            self.stats.run['debuff_empty_triggers'] += 1
            return None
        if self.current_debuff:
            self._finish_current_debuff('replaced_early')
        cid = self.debuff_deck.pop(0)
        self.current_debuff = cid
        self.debuff_turns_remaining = 3
        self.debuff_active_from_turn = self.turn + 1
        self.debuff_active_turns = 0
        self.stats.debuff(cid)['drawn'] += 1
        self.stats.game['debuff_drawn'].append(cid)
        return cid

    def debuff_cancel_options(self):
        """当前可用的 Debuff 保护选项（手牌 / active 未用能力）。

        正式规则：仅 Debuff 牌库非空时提供——牌库已空则本次不产生
        Debuff，保护不得被消耗。调用方仍需自行确认触发条件成立。
        """
        if not self.debuff_deck:
            return []
        options = []
        options.extend({'cid': cid, 'source': 'hand'}
                       for cid in self.hand_effect_cards('cancel_debuff'))
        for cid in self.active_cards():
            if (CARDS[cid].get('cancel_debuff_once')
                    and cid not in self.used_once_cards):
                options.append({'cid': cid, 'source': 'active'})
        return options

    def consume_debuff_cancel(self, cid):
        """消耗一张已确认的 Debuff 保护；cid 不在当前选项中则无副作用。"""
        selected = next(
            (x for x in self.debuff_cancel_options() if x['cid'] == cid), None)
        if selected is None:
            return False
        if selected['source'] == 'hand':
            self.hand.remove(cid)
            self.stats.game['childhood_uses'][cid] += 1
        else:
            self.used_once_cards.add(cid)
            self.stats.card(cid)['ability']['cancel_debuff'] += 1
        self.stats.run['debuff_cancelled'] += 1
        self.stats.game['debuff_cancelled'] += 1
        return True

    def debuff_shorten_option(self):
        """当前 active 的 Debuff 缩短选项（OH-01）；无则 None。"""
        for cid in self.active_cards():
            if CARDS[cid].get('shorten_debuff'):
                return {'cid': cid, **CARDS[cid]['shorten_debuff']}
        return None

    def apply_debuff_shorten(self, cid):
        """支付代价缩短刚抽取的 Debuff 持续时间；非法时无副作用返回 False。

        仅作用于实际抽出的当前 Debuff（最低 1 回合由既有钳制保证）。
        """
        option = self.debuff_shorten_option()
        if (option is None or cid != option['cid'] or not self.current_debuff
                or any(self.pool.get(s, 0) < n
                       for s, n in option['cost'].items())):
            return False
        for sym, n in option['cost'].items():
            self.pool[sym] -= n
        self.debuff_turns_remaining = max(
            1, self.debuff_turns_remaining - option['reduce'])
        self.stats.card(cid)['ability']['shorten_debuff'] += 1
        return True

    def resolve_runtime_debuff(self, cancel_cid=None):
        """结算 Runtime 购买后的 Debuff；cancel_cid 为显式选择的保护卡。

        正式语义：保护仅在触发且牌库非空时可用；取消则不抽牌、不进入
        经历、不影响终局 Debuff 计数。
        """
        status = self.debuff_trigger_status()
        if not status['triggered']:
            return 'no_trigger'
        if cancel_cid is not None and cancel_cid not in [
                o['cid'] for o in self.debuff_cancel_options()]:
            return None
        self._consume_debuff_trigger(status)
        if self.pre_debuff_cancel:
            self.stats.run['debuff_cancelled'] += 1
            self.stats.game['debuff_cancelled'] += 1
            self.pre_debuff_cancel = None
            return 'pre_cancelled'
        if cancel_cid is not None:
            if not self.consume_debuff_cancel(cancel_cid):
                return None
            return 'cancelled'
        return 'drawn' if self._draw_next_debuff() else 'empty'

    def _debuff_check(self):
        status = self.debuff_trigger_status()
        if not status['triggered']:
            return

        self._consume_debuff_trigger(status)

        # 事前保险已在掷骰前消耗；达到阈值时优先取消，且不抽牌。
        if self.pre_debuff_cancel:
            self.stats.run['debuff_cancelled'] += 1
            self.stats.game['debuff_cancelled'] += 1
            self.pre_debuff_cancel = None
            return

        cancel_options = self.debuff_cancel_options()
        shorten_option = self.debuff_shorten_option()
        response = self.strat.debuff_response(cancel_options, shorten_option) or {}
        cancel_cid = response.get('cancel')
        selected = next((x for x in cancel_options if x['cid'] == cancel_cid), None)
        if selected and self.consume_debuff_cancel(cancel_cid):
            return

        if not self._draw_next_debuff():
            return

        shorten_cid = response.get('shorten')
        if shorten_cid is not None:
            self.apply_debuff_shorten(shorten_cid)

    def _advance_debuff(self):
        """在一个完整成年回合结束时推进当前 Debuff 的生命周期。"""
        if (not self.current_debuff or self.debuff_active_from_turn is None
                or self.turn < self.debuff_active_from_turn):
            return {'advanced': False, 'archived_card_id': None}
        cid = self.current_debuff
        self.debuff_active_turns += 1
        self.debuff_turns_remaining -= 1
        self.stats.debuff(cid)['active_turns'] += 1
        if self.debuff_turns_remaining <= 0:
            self._finish_current_debuff('natural_expiry')
            return {'advanced': True, 'archived_card_id': cid}
        return {'advanced': True, 'archived_card_id': None}

    # ---------------- 持续成本 ----------------

    def maintenance_targets(self):
        """锁定本维护窗口的 active；本回合新取得牌免维护。"""
        return tuple(
            cid for cls in 'HKRWP' for cid in [self.active(cls)]
            if cid and CARDS[cid].get('upkeep')
            and self.acquired.get(cid, -1) < self.turn
        )

    def _maintenance_cost_options(self, cid, active_snapshot):
        cost = dict(CARDS[cid]['upkeep'])
        modifiers = []
        for source in active_snapshot:
            discount = CARDS[source].get('upkeep_discount')
            if not discount or cost.get(discount['sym'], 0) <= 0:
                continue
            for _ in range(discount.get('reduce', 1)):
                cost = discounted_cost(cost, discount['sym'])
            modifiers.append({'source_card_id': source,
                              'kind': 'upkeep_discount',
                              'symbol': discount['sym']})

        fate = self.active_fate_card()
        options = [{'cost': Counter(cost), 'modifiers': modifiers,
                    'fate_reduced': False}]
        if fate and fate.get('upkeep_reduce'):
            reduced = []
            for sym in NORMAL:
                if cost.get(sym, 0) > 0:
                    reduced.append({
                        'cost': Counter(discounted_cost(cost, sym)),
                        'modifiers': modifiers + [{
                            'source_card_id': fate['id'],
                            'kind': 'fate_upkeep_reduce', 'symbol': sym}],
                        'fate_reduced': True,
                    })
            if reduced:
                options = reduced
        return options

    def _maintenance_substitutions(self, cost, active_snapshot):
        substitutions = []
        for source in active_snapshot:
            rule = CARDS[source].get('upkeep_sub')
            if not rule:
                continue
            targets = (NORMAL if rule['to'] == 'any' else rule['to'])
            if isinstance(targets, str):
                targets = (targets,)
            for target in targets:
                if (target in NORMAL and target != rule['from']
                        and cost.get(target, 0) > 0):
                    substitutions.append((rule['from'], target))
        return substitutions

    def maintenance_plans(self):
        """枚举所有最大合法维护方案；纯计算，不改变正式状态。"""
        targets = self.maintenance_targets()
        active_snapshot = tuple(self.active(cls) for cls in 'HKRWP'
                                if self.active(cls))
        base_pool = Counter({s: n for s, n in self.pool.items() if n > 0})
        temps = [(s, n, cid) for s, n, cid in self._temps_available()
                 if CARDS[cid]['type'] == 'C']
        temp_remaining = {cid: n for _s, n, cid in temps}
        me01_available = ('ME-01' in self.hand and not self._event_blocked())
        collected = {}

        def positive(counter):
            return dict(sorted((s, n) for s, n in counter.items() if n > 0))

        def available_temps(remaining):
            return [(sym, remaining[cid], cid) for sym, _n, cid in temps
                    if remaining[cid] > 0]

        def payment_options(cid, pool, remaining_temps, wildcards_left,
                            me01_used):
            for option in self._maintenance_cost_options(cid, active_snapshot):
                subs = self._maintenance_substitutions(
                    option['cost'], active_snapshot)
                for solution in solve_cost_all(
                        option['cost'], pool, available_temps(remaining_temps),
                        subs, wildcards_left):
                    yield solution, option, None
                if me01_available and me01_used is None:
                    for sym in NORMAL:
                        if option['cost'].get(sym, 0) <= 0:
                            continue
                        reduced = Counter(discounted_cost(option['cost'], sym))
                        for solution in solve_cost_all(
                                reduced, pool,
                                available_temps(remaining_temps), subs,
                                wildcards_left):
                            me01 = {'target_card_id': cid,
                                    'reduced_resource': sym}
                            reduced_option = dict(option)
                            reduced_option['cost'] = reduced
                            yield solution, reduced_option, me01

        def can_append(cid, pool, remaining_temps, wildcards_left, me01_used):
            return any(payment_options(
                cid, pool, remaining_temps, wildcards_left, me01_used))

        def record(maintained, pool, remaining_temps, spent, consumed,
                   payments, me01_used, wildcard_symbols, dice_gl_remaining):
            maintained = tuple(cid for cid in targets if cid in maintained)
            lost = tuple(cid for cid in targets if cid not in maintained)
            # 未使用 ME-01 是玩家的正式方案选择；最大性只检查该方案已经
            # 采用的支付能力，不能因“本可改用 ME-01”淘汰不使用它的方案。
            append_me01_state = (me01_used if me01_used is not None
                                 else {'unused_by_choice': True})
            if any(can_append(cid, pool, remaining_temps,
                              dice_gl_remaining,
                              append_me01_state) for cid in lost):
                return
            canonical = {
                'maintained_card_ids': list(maintained),
                'lost_card_ids': list(lost),
                'spent_resources': positive(spent),
                'remaining_resources': positive(pool),
                'consumed_childhood_card_ids': sorted(consumed),
                'payments': payments,
                'me01_used': me01_used,
            }
            key = (
                maintained, lost,
                tuple(sorted(canonical['remaining_resources'].items())),
                tuple(canonical['consumed_childhood_card_ids']),
                tuple(sorted(me01_used.items())) if me01_used else (),
                tuple((p['card_id'], p.get('dice_gl_true_gl_used', 0))
                      for p in payments),
            )
            if key in collected:
                return
            encoded = json.dumps(canonical, sort_keys=True,
                                 separators=(',', ':')).encode('utf-8')
            canonical['plan_id'] = 'maintenance_' + hashlib.sha256(encoded).hexdigest()[:16]
            collected[key] = canonical

        def visit(index, pool, remaining_temps, spent, maintained, consumed,
                  payments, me01_used, wildcard_symbols, dice_gl_remaining):
            if index == len(targets):
                record(maintained, pool, remaining_temps, spent, consumed,
                       payments, me01_used, wildcard_symbols, dice_gl_remaining)
                return
            cid = targets[index]
            # 放弃维护也必须继续枚举；叶节点会用最大合法性过滤故意少养。
            visit(index + 1, pool, remaining_temps, spent, maintained,
                  consumed, payments, me01_used, wildcard_symbols,
                  dice_gl_remaining)
            for solution, option, used_me01 in payment_options(
                    cid, pool, remaining_temps,
                    dice_gl_remaining, me01_used):
                next_pool = Counter(pool)
                next_pool.subtract(solution['pool_used'])
                next_temps = dict(remaining_temps)
                for used_cid in solution['temp_units_used']:
                    next_temps[used_cid] -= 1
                next_spent = Counter(spent)
                next_spent.update(solution['pool_used'])
                next_consumed = set(consumed)
                next_consumed.update(solution['temps_used'])
                payment = {
                    'card_id': cid,
                    'cost': positive(option['cost']),
                    'pool_used': positive(solution['pool_used']),
                    'consumed_childhood_card_ids': solution['temps_used'],
                    'substitutions_used': [
                        {'from': f, 'to': t} for f, t in solution['subs_used']],
                    'wildcard_symbols': list(solution['wildcard_symbols']),
                    'dice_gl_true_gl_used': solution['dice_gl_direct_used'],
                    'modifiers': option['modifiers'],
                }
                visit(index + 1, next_pool, next_temps, next_spent,
                       maintained | {cid}, next_consumed,
                       payments + [payment], used_me01 or me01_used,
                       wildcard_symbols + solution['wildcard_symbols'],
                       dice_gl_remaining - len(solution['wildcard_symbols'])
                       - solution['dice_gl_direct_used'])

        visit(0, Counter(base_pool), dict(temp_remaining), Counter(), set(),
              set(), [], None, [],
              min(self.dice_gl_flexible, base_pool.get('GL', 0)))
        plans = list(collected.values())
        full_without_me01 = any(
            not p['me01_used']
            and set(p['maintained_card_ids']) == set(targets) for p in plans)
        if full_without_me01:
            plans = [p for p in plans if not (
                p['me01_used']
                and set(p['maintained_card_ids']) == set(targets))]
        return sorted(plans, key=lambda p: (
            -len(p['maintained_card_ids']), tuple(p['maintained_card_ids']),
            p['plan_id']))

    def apply_maintenance_plan(self, plan):
        """原子兑现已枚举的 maintenance plan；不会重新求解支付。"""
        try:
            maintained = list(plan['maintained_card_ids'])
            lost = list(plan['lost_card_ids'])
            spent = Counter(plan['spent_resources'])
            remaining = Counter(plan['remaining_resources'])
            consumed = list(plan['consumed_childhood_card_ids'])
            payments = list(plan['payments'])
            me01_used = plan['me01_used']
        except (KeyError, TypeError, ValueError):
            return None
        targets = self.maintenance_targets()
        if (len(maintained) != len(set(maintained))
                or len(lost) != len(set(lost))
                or set(maintained) | set(lost) != set(targets)
                or set(maintained) & set(lost)
                or any(n < 0 for n in spent.values())
                or any(n < 0 for n in remaining.values())):
            return None
        reconstructed = Counter(remaining)
        reconstructed.update(spent)
        current_pool = Counter({s: n for s, n in self.pool.items() if n > 0})
        if Counter({s: n for s, n in reconstructed.items() if n > 0}) != current_pool:
            return None
        if (len(consumed) != len(set(consumed))
                or any(cid not in self.hand or CARDS[cid]['type'] != 'C'
                       for cid in consumed)):
            return None
        if me01_used:
            if (set(me01_used) != {'target_card_id', 'reduced_resource'}
                    or me01_used['target_card_id'] not in maintained
                    or me01_used['reduced_resource'] not in NORMAL
                    or 'ME-01' not in self.hand or self._event_blocked()):
                return None
        if (len(payments) != len(maintained)
                or len({p.get('card_id') for p in payments}) != len(payments)
                or {p.get('card_id') for p in payments} != set(maintained)):
            return None
        wildcard_symbols = [sym for p in payments
                            for sym in p.get('wildcard_symbols', [])]
        dice_gl_true_gl_used = sum(p.get('dice_gl_true_gl_used', 0)
                                   for p in payments)
        if (any(type(p.get('dice_gl_true_gl_used', 0)) is not int
                or p.get('dice_gl_true_gl_used', 0) < 0 for p in payments)
                or len(wildcard_symbols) + dice_gl_true_gl_used
                > self.dice_gl_flexible):
            return None

        # 所有拒绝条件已处理，以下一次性提交正式结果。
        for cid in targets:
            self.stats.card(cid)['upkeep_active'] += 1
        self.pool = Counter(remaining)
        self.dice_gl_flexible -= dice_gl_true_gl_used
        self._consume_wildcards({'wildcard_symbols': wildcard_symbols})
        for cid in consumed:
            self.hand.remove(cid)
            self.stats.game['childhood_uses'][cid] += 1
        if me01_used:
            self.hand.remove('ME-01')
            self.stats.game['event_uses']['ME-01'] += 1
        for payment in payments:
            cid = payment['card_id']
            self.stats.card(cid)['upkeep_paid'] += 1
            for modifier in payment.get('modifiers', []):
                if modifier['kind'] == 'upkeep_discount':
                    self.stats.card(modifier['source_card_id'])['ability'][
                        'upkeep_discount'] += 1
                elif modifier['kind'] == 'fate_upkeep_reduce':
                    self.stats.fate(modifier['source_card_id'])['ability'][
                        'upkeep_discount_mattered'] += 1
            for sub in payment.get('substitutions_used', []):
                for source in self.active_cards():
                    rule = CARDS[source].get('upkeep_sub')
                    if rule and rule['from'] == sub['from']:
                        self.stats.card(source)['ability']['upkeep_sub'] += 1
                        break
        for cid in lost:
            same_turn = self.acquired.get(cid) == self.turn
            self.lose_active(cid, reason='upkeep')
            self.stats.card(cid)['upkeep_failed'] += 1
            if same_turn:
                self.stats.card(cid)['same_turn_death'] += 1
        result = {
            'paid': payments,
            'maintained_card_ids': maintained,
            'lost_card_ids': lost,
            'spent_resources': dict(sorted((s, n) for s, n in spent.items()
                                            if n > 0)),
            'remaining_resources': dict(sorted(
                (s, n) for s, n in remaining.items() if n > 0)),
            'me01_used': me01_used,
        }
        self.maintenance_result = result
        return result

    def _choose_simulator_maintenance_plan(self, plans):
        """Simulator 的稳定策略层：旧类别顺序只作同分方案 tie-break。"""
        target_by_class = {CARDS[cid]['type']: cid
                           for cid in self.maintenance_targets()}

        def key(plan):
            kept = set(plan['maintained_card_ids'])
            class_preference = tuple(
                -int(target_by_class.get(cls) in kept) for cls in 'HKRWP')
            return (-len(kept), class_preference,
                    int(bool(plan['me01_used'])), plan['plan_id'])

        return min(plans, key=key)

    def _upkeep(self):
        plans = self.maintenance_plans()
        result = self.apply_maintenance_plan(
            self._choose_simulator_maintenance_plan(plans))
        if result is None:
            raise AssertionError('generated maintenance plan became invalid')

    # ---------------- 市场结算 ----------------

    def _pick_elims(self, protected, needed):
        legal = [c for c in self.market if c != protected]
        if not legal or needed <= 0:
            return []
        fixed = legal[0]
        rest = [c for c in legal if c != fixed]
        k = needed - 1
        chosen = [fixed]
        if k > 0 and rest:
            chosen += self.rng.sample(rest, min(k, len(rest)))
        return chosen

    def cleanup_normal_market(self, protected=None, protection_card_id=None):
        """结算本回合普通市场离场与补牌。

        调用方只可在市场清理窗口调用本方法。传入保护目标及其来源手牌即代表
        使用市场保护；非法目标在任何随机、资源或市场变化前被拒绝。Runtime 与
        Simulator 都通过此处共享固定淘汰、随机淘汰与补牌规则。
        """
        if protected is not None:
            if (protected not in self.market or protection_card_id is None
                    or protection_card_id not in self.hand
                    or not CARDS[protection_card_id].get('protect_market')
                    or (CARDS[protection_card_id]['type'] == 'E'
                        and not self.event_usage_allowed())):
                return None
        elif protection_card_id is not None:
            return None

        st = self.stats
        final_pending_before_cleanup = self.final_pending
        market_before = list(self.market)
        purchased = list(self.purchased_this_turn)
        needed = max(0, 3 - len(purchased))

        # 先完成所有前置校验，之后才落地市场保护的消耗与市场变更。
        if protected is not None:
            self._consume_hand_card(protection_card_id)
            if protection_card_id == 'YE-04':
                st.run['ye04_used'] += 1
            # 不为统计另做一次随机反事实淘汰；只有保护固定淘汰位时可确定
            # 它改变了原结果，且不会额外消费 Game.rng。
            if (protection_card_id == 'YE-04' and market_before
                    and market_before[0] == protected):
                st.run['ye04_mattered'] += 1

        eliminated = self._pick_elims(protected, needed)
        if len(eliminated) < needed:
            st.run['shortfalls'] += 1
        if eliminated:
            st.run['elim_fixed'] += 1
            st.run['elim_random'] += len(eliminated) - 1
        for cid in eliminated:
            self.market.remove(cid)
            cs = st.card(cid)
            cs['eliminated'] += 1
            dwell = self.turn - self.market_entry[cid]
            cs['dwell_sum'] += dwell
            cs['dwell_n'] += 1
            if dwell <= 1 and cid != eliminated[0]:
                st.run['quick_random_elim'] += 1

        market_after_elimination = list(self.market)
        refill_start = len(self.market)
        self._fill_market()
        refill = self.market[refill_start:]
        elder_exhausted_during_refill = (
            bool(refill)
            and any(CARDS[cid]['stage'] == 'elder' for cid in refill)
            and not self.decks['elder'])

        departed = len(purchased) + len(eliminated)
        st.game['turn_departures'].append(departed)
        if elder_exhausted_during_refill and not self.final_pending:
            self.final_pending = True

        self.market_cleanup_result = {
            'purchased_card_ids': purchased,
            'system_eliminated_card_ids': list(eliminated),
            'protected_card_id': protected,
            'protection_source_card_id': protection_card_id,
            'ye04_used': protection_card_id == 'YE-04',
            'market_before_cleanup': market_before,
            'market_after_elimination': market_after_elimination,
            'refill_card_ids': list(refill),
            'market_after_refill': list(self.market),
            'stage': self.stage,
            'deck_counts': {s: len(self.decks[s]) for s in STAGE_ORDER},
            'final_round_pending': self.final_pending,
            'final_round_started': (not final_pending_before_cleanup
                                    and self.final_pending),
        }
        return self.market_cleanup_result

    def _settlement(self):
        # Simulator 的市场保护使用时机仍由 strategy 决定；市场规则只在
        # cleanup_normal_market() 中执行。
        source = self.find_hand_effect('protect_market')
        tgt = self.strat.ye04_target() if source else None
        protected = tgt if tgt in self.market else None
        result = self.cleanup_normal_market(
            protected=protected, protection_card_id=source if protected else None)
        if result is None:
            raise AssertionError('simulator market protection became invalid')

    def closeout_adult_turn(self):
        """完成市场清理后的共享回合收尾，并准备下一成年回合边界。

        市场清理首次开启 final_pending 时，本回合仍正常推进到下一回合；
        若 final_pending 原本已存在，则正在完成唯一允许的最终完整回合，
        closeout 后进入 game_over。调用方只可对每回合调用一次。
        """
        if self.market_cleanup_result is None:
            raise RuntimeError('market cleanup must finish before turn closeout')

        completed_turn = self.turn
        lifecycle = self._advance_debuff()
        final_round_started = self.market_cleanup_result['final_round_started']
        game_over = self.final_pending and not final_round_started

        result = {
            'completed_turn': completed_turn,
            'next_turn': None if game_over else completed_turn + 1,
            'debuff_lifecycle': lifecycle,
            'purchase_result': self.purchase_result,
            'maintenance_result': self.maintenance_result,
            'market_cleanup_result': self.market_cleanup_result,
            'final_round_pending': self.final_pending,
            'game_over': game_over,
        }

        # 回合局部资源、骰面、能力使用标记在此结束；跨回合正式状态不动。
        self.dice = []
        self.stable_pool = Counter()
        self.pool = Counter()
        self.flex_choice = {}
        self.real_bl_spent_on_fate = 0
        self.fate_acquired_this_turn = None
        self._reset_turn_roll_state()
        self.pre_roll_open = False

        if game_over:
            self.game_over = True
        else:
            self.turn += 1
        return result

    # ---------------- 主流程 ----------------

    def play_turn(self):
        st = self.stats
        # ---- 1. 回合开始稳定资源（flex 选择 → stable_pool）
        flex_options = self.start_adult_turn()
        flex_choices = {}
        for cid, allowed in flex_options.items():
            choice = self.strat.choose_flex(cid, allowed)
            flex_choices[cid] = choice if choice in allowed else allowed[0]
        # ---- 2. 掷骰前声明（按手牌字段统一结算）
        dec = self.strat.declare_pre_roll() or {}
        if self.apply_pre_roll_declarations(
            flex_choices=flex_choices,
            hand_card_ids=dec.get('hand_card_ids', ())) is None:
            raise AssertionError('simulator pre-roll declaration became invalid')
        for cid in self.active_cards():
            if CARDS[cid].get('extra_die'):
                st.card(cid)['ability']['extra_die'] += 1

        self.roll_first_dice()
        self._offer_bl_convert()

        # ---- 3. 重掷阶段
        rounds = self.normal_reroll_rounds()
        debuff_now = self.active_debuff_card()
        if debuff_now and debuff_now.get('reroll_delta', 0) < 0:
            st.debuff(debuff_now['id'])['ability']['reroll_rounds_lost'] += \
                -debuff_now['reroll_delta']
        extra_card = self.strat.use_extra_reroll_card(
            tuple(self.extra_reroll_hand_cards()))
        if extra_card:
            added = self.use_extra_reroll_card(extra_card)
            if added:
                rounds += added
        for r in range(rounds):
            fate_now = self.active_fate_card()
            if self.normal_rerolls_locked():
                st.fate(fate_now['id'])['ability']['reroll_locked'] += 1
                break
            self._offer_bl_convert()
            sel = set(self.strat.choose_reroll(r, rounds) or ())
            rerolled = self.apply_normal_reroll(sel)
            if rerolled:
                st.run['reroll_rounds_used'] += 1
                st.run['dice_rerolled'] += rerolled
            self._offer_bl_convert()
        # 独立单骰重掷：合法性与 Runtime 共用；策略只决定是否与目标。
        for option in list(self.available_special_rerolls()):
            cid = option['card_id']
            idx = self.strat.use_single_reroll(cid)
            if idx is not None and self.apply_special_reroll(cid, idx):
                self._offer_bl_convert()
        for f in self.dice:
            st.run['dice_faces'][f] += 1
        self.record_final_bad_luck()

        # ---- 4. 锁骰后：资源池 + YK-05 转换 + 2→1 兜底
        self.dice_gl_flexible = (
            self.dice.count('GL')
            if (self.active_fate_card() or {}).get('wildcard_normal') else 0
        )
        self.pool = self.post_roll_resources()
        if self.yk05_conversion_available() and self.strat.use_convert_turn():
            if not self.apply_yk05_conversion():
                raise AssertionError('simulator yk05 conversion became invalid')
            self.pool = self.post_roll_resources()
        if self.cfg.fallback and not self.fallback_used:
            fb = self.strat.use_fallback()
            if fb:
                burn, target = fb
                if (target in NORMAL and all(self.pool.get(s, 0) >= 1 for s in burn)
                        and len(burn) == 2):
                    for s in burn:
                        self.pool[s] -= 1
                    self.pool[target] += 1
                    self.fallback_used = True
                    st.run['fallback_used'] += 1

        debuff_now = self.active_debuff_card()
        if debuff_now and debuff_now.get('block_type'):
            blocked = sum(CARDS[cid]['type'] == debuff_now['block_type']
                          for cid in self.market)
            st.debuff(debuff_now['id'])['ability']['blocked_acquire_opportunities'] += blocked
        if debuff_now and debuff_now.get('gl_threshold') == 4:
            gl_total = self.pool.get('GL', 0) + sum(
                n for s, n, cid in self._temps_available() if s == 'GL')
            if gl_total == 3:
                blocked = 0
                for cid in self.market:
                    if self._try_order((cid,), ('res',), self.pool) is None:
                        blocked += 1
                st.debuff(debuff_now['id'])['ability']['gl_threshold_mattered'] += blocked

        # ---- 5. 联合购买（普通 0~2 张 + Fate 0~1 张，共享真实资源）
        if self.cfg.fate:
            if self.fate_window():
                st.game['fate_windows'] += 1
            plans = self.joint_plans()
            action = self.strat.choose_joint_plan(tuple(plans))
            if action not in plans:
                action = ((), None)
            if not self.execute_joint(action, plans[action]):
                raise AssertionError('simulator joint plan became invalid')
            if not self._resolve_simulator_fate_immediate():
                raise AssertionError('simulator Fate immediate choice became invalid')
            choice = action[0]
        else:
            plans = self.affordable_plans()
            choice = tuple(self.strat.choose_plan(plans))
            if choice not in plans:
                choice = ()
            if choice:
                self.execute_purchase(choice)
        st.game['hist'][len(choice)] += 1
        if len(choice) == 0:
            has_legal = (any(cards or fate for cards, fate in plans)
                         if self.cfg.fate else bool(plans))
            if has_legal:
                st.game['zero_declined'] += 1
            else:
                st.game['zero_no_legal'] += 1

        # ---- 6. 以锁定骰面累计的厄运值 → 每回合至多触发 1 张 Debuff
        self._debuff_check()

        # ---- 7. active 处理（堆叠）
        for cid in list(self.pending_new):
            cls = CARDS[cid]['type']
            if cls in ('W', 'P'):
                self.place_pending_card(cid, 'top')
            else:
                place = self.strat.choose_placement(cid)
                self.place_pending_card(
                    cid, 'bury' if place == 'bury' else 'top')

        # ---- 8. 持续成本
        self._upkeep()

        # ---- 9. 市场结算
        st.run['waste_normal'] += sum(self.pool.get(s, 0) for s in NORMAL)
        st.run['waste_gl'] += self.pool.get('GL', 0)
        self._settlement()
        self.closeout_adult_turn()

    def _extra_die_count(self):
        n = 0
        for cls in 'HKRWP':
            top = self.active(cls)
            if top and CARDS[top].get('extra_die'):
                n += CARDS[top]['extra_die']
        return n

    def _offer_bl_convert(self):
        """MH-04：本回合出现第 2 个厄运时，可将其中 1 个厄运改为健康（每回合限 1 次）。"""
        eligible = self.mh04_reaction_status()
        if eligible is None:
            return
        if self.strat.use_bl_convert():
            self.resolve_mh04_reaction(True, eligible[0])
        else:
            self.resolve_mh04_reaction(False)

    def _apply_reroll(self, sel, abebe_index=None, allow_abebe=True):
        """执行一轮正常重掷：BL 仅能由本轮显式指定的 C12 解冻。"""
        count = 0
        for i in sorted(sel):
            if not (0 <= i < len(self.dice)):
                continue
            if self.dice[i] == 'BL':
                if abebe_index is not None and i != abebe_index:
                    continue
                if not (allow_abebe and self.abebe_held and not self.abebe_used):
                    continue
                self.abebe_used = True
                self.stats.run['abebe_used'] += 1
                self.dice[i] = self._roll(1)[0]
                count += 1
            else:
                self.dice[i] = self._roll(1)[0]
                count += 1
        return count

    # ---------------- 终局 ----------------

    def run(self):
        guard = 0
        while not self.game_over:
            self.play_turn()
            guard += 1
            if guard > self.MAX_TURNS:
                raise RuntimeError('turn limit exceeded (infinite loop?)')
        self.stats.finish_game(self)
        return self.stats.rows[-1]

    # ---------------- 供策略使用的估值辅助 ----------------

    def loss_delta(self, cid):
        """失去某张 active 的估计损失（分数 + 简单引擎价值）。"""
        cls = CARDS[cid]['type']
        cv2 = {c: list(l) for c, l in self.cv.items()}
        if cid in cv2[cls]:
            cv2[cls].remove(cid)
        s0 = scoring.full_score(self.cv, self.goals)
        s1 = scoring.full_score(cv2, self.goals)
        c = CARDS[cid]
        eng = 0.3 * sum(n for _, n in c.get('provide', []))
        if c.get('extra_die'):
            eng += 0.5
        return (s0['total'] - s1['total']) + eng * min(1.0, self.est_turns_left() / 10)
