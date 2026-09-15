# -*- coding: utf-8 -*-
"""Production Runtime 的最小可暂停垂直切片。"""
import copy
import random
import threading

from .cards import CARDS, LG_NAMES
from .engine import CONFIGS, Game
from .scoring import (CURVE_A, LG_RULES, flex_actives, full_score,
                      lg_scoring_text)


def _res_text(items):
    return '、'.join('%s×%d' % (s, n) for s, n in items)


_SPECIAL_NOTES = {
    'F10': ('特殊：生效期间，仅骰子最终结果中的真实 GL 可视作 H/K/R/M 任意 '
            '1 点用于普通资源支付（该骰即被消耗）；这些骰子 GL 因此不能再用于 '
            '3 GL 免费取得、支付 Fate 的 GL 成本或满足真实 GL 条件。'
            '手牌/卡牌来源的 GL 不受影响。'),
    'C12': ('特殊：阿贝贝一生一次。正常重掷阶段可指定 1 颗被冻结的 BL 骰'
            '参与该轮重掷；使用后能力失效，但牌仍留在桌面。'),
}


def _card_effect_summary(card):
    """由正式卡牌字段统一模板化的一句话效果说明；纯展示，不参与规则。"""
    parts = []
    ctype = card['type']
    if card.get('provide') and ctype in 'HKRWP':
        parts.append('生效时每回合稳定产出 %s' % _res_text(card['provide']))
    if card.get('flex'):
        parts.append('每回合开始从 %s 中指定 1 种作为稳定产出'
                     '（终局计分前会对仍生效的本卡做一次最终指定）'
                     % '/'.join(card['flex']))
    if card.get('upkeep'):
        parts.append('生效期间每回合维护支付 %s；付不出则该卡从履历弃置'
                     % _res_text(sorted(card['upkeep'].items())))
    for opt in card.get('sub_buy', []):
        scope = ('任意类' if opt['scope'] is None
                 else '/'.join(opt['scope']) + ' 类')
        parts.append('购买%s牌时可用 1×%s 替代 1×%s（每次购买每条限一次）'
                     % (scope, opt['from'], opt['to']))
    if card.get('extra_die'):
        parts.append('每回合额外掷 %d 骰（上限 7）' % card['extra_die'])
    if card.get('reroll'):
        text = {'non_bl': '1 颗非 BL 骰', 'bl': '1 颗 BL 骰',
                'non_gl_bl': '1 颗非 GL/BL 骰'}[card['reroll']['filter']]
        parts.append('每回合可额外重掷 %s（不耗正常重掷轮数）' % text)
    if card.get('upkeep_discount'):
        d = card['upkeep_discount']
        parts.append('生效期间支付维护费时 %s 需求 -%d' % (d['sym'], d['reduce']))
    if card.get('upkeep_sub'):
        r = card['upkeep_sub']
        to = '任意普通资源' if r['to'] == 'any' else '/'.join(r['to'])
        parts.append('生效期间付维护费可用 1×%s 替代 1×%s' % (r['from'], to))
    if card.get('convert_turn'):
        c = card['convert_turn']
        parts.append('每回合一次：锁骰后可将 1×%s 转换为 1×%s'
                     % (c['from'], c['to']))
    if card.get('shorten_debuff'):
        sd = card['shorten_debuff']
        parts.append('获得 Debuff 时可支付 %s 使其持续 -%d 回合（最低 1）'
                     % (_res_text(sorted(sd['cost'].items())), sd['reduce']))
    if card.get('cancel_debuff_once'):
        parts.append('生效期间可取消一次 Debuff 触发（每局一次，不抽牌）')
    if card.get('bl_convert'):
        parts.append('本回合第 2 颗 BL 出现时，可将其中 1 颗 BL 改为 H')
    if card.get('vp'):
        parts.append('终局持有计 %d vp（无需 active）' % card['vp'])
    if card.get('discount_type'):
        parts.append('取得 %s 类牌时，其成本中 1 个普通符号 -1'
                     % card['discount_type'])
    if card.get('temp_res'):
        parts.append('使用后本次购买结算临时提供 %s×%d' % card['temp_res'])
        # 童年牌由文末统一的一次性子句覆盖；事件手牌在此声明整张弃置与
        # 未用部分作废，防止部分用量被误读为可留存。
        if ctype != 'C':
            parts.append('一次性手牌，使用后弃置，未用部分随本次结算作废')
    if card.get('temp_gl'):
        parts.append('本次购买结算临时提供 GL×%d（属正常 GL，可计入 3 GL '
                     '免费取得）' % card['temp_gl'])
    if card.get('temp_dice'):
        parts.append('使用后本回合临时 +%d 骰（上限 7）' % card['temp_dice'])
    if card.get('cancel_debuff'):
        parts.append('可取消一次 Debuff 触发（不抽牌）')
    if card.get('protect_market'):
        parts.append('使用后本次市场清理可保护 1 张牌不被淘汰')
    if card.get('upkeep_reduce'):
        parts.append('使用后本回合维护费中 1 个普通符号 -1')
    if card.get('pre_cancel_debuff'):
        parts.append('掷骰前声明使用：本回合 Debuff 不触发（不抽牌），用后弃置')
    if card.get('extra_reroll_rounds'):
        parts.append('本回合额外 +%d 轮正常重掷' % card['extra_reroll_rounds'])
    if ctype == 'D':
        parts.append('持续 3 回合，自下一完整回合开始生效')
        ec = card.get('extra_cost')
        if ec:
            parts.append('生效期间购买 %s 类牌须额外支付 %s×%d'
                         % (ec['type'], ec['sym'], ec['n']))
        if card.get('block_type'):
            parts.append('生效期间无法取得 %s 类牌' % card['block_type'])
        if card.get('block_event'):
            parts.append('生效期间无法使用事件牌')
        if card.get('reroll_delta'):
            parts.append('生效期间正常重掷轮数 %+d' % card['reroll_delta'])
        if card.get('gl_threshold'):
            parts.append('生效期间 GL 免费取得门槛升为 %d'
                         % card['gl_threshold'])
        if card.get('virtual_bl'):
            parts.append('生效期间视为额外有 %d 颗 BL 参与触发判定'
                         % card['virtual_bl'])
    if ctype == 'F':
        parts.append('支付成本后永久生效；生效中的 Fate 唯一，新取得的会替换它')
        imm = card.get('immediate')
        if imm == 'set_active':
            parts.append('取得后立即将一张你持有的非生效履历卡置顶生效')
        elif imm == 'replace_goal':
            parts.append('取得后立即用一张新人生目标替换当前一张')
        if card.get('reroll_delta'):
            parts.append('生效期间正常重掷轮数 %+d' % card['reroll_delta'])
        if card.get('dice_delta'):
            parts.append('生效期间每回合 +%d 骰（上限 7）' % card['dice_delta'])
        if card.get('purchase_limit'):
            parts.append('生效期间每次购买最多取得 %d 张普通牌'
                         % card['purchase_limit'])
        fd = card.get('first_discount')
        if fd:
            tgt = '任意类' if fd['type'] is None else fd['type'] + ' 类'
            if fd['sym'] == 'any':
                cost_part = '普通资源总成本 -%d' % fd['n']
            else:
                cost_part = '%s 成本 -%d' % (fd['sym'], fd['n'])
            parts.append('每回合购买结算中：本回合取得的1张%s牌，%s（最低0）'
                         % (tgt, cost_part))
        ec = card.get('extra_cost')
        if ec:
            parts.append('生效期间购买 %s 类牌须额外支付 %s×%d'
                         % (ec['type'], ec['sym'], ec['n']))
        disc = card.get('discount')
        if disc:
            parts.append('生效期间购买 %s 类牌成本中 %s -%d'
                         % (disc['type'], disc['sym'], disc['n']))
        if card.get('block_event'):
            parts.append('生效期间无法使用事件牌')
        if card.get('upkeep_reduce'):
            parts.append('生效期间维护费中 1 个普通符号 -1')
        if card.get('lock_reroll_on_bl'):
            parts.append('生效期间本回合一旦出现 BL，正常重掷被锁定')
        if card.get('virtual_bl'):
            parts.append('生效期间视为额外有 %d 颗 BL 参与触发判定'
                         % card['virtual_bl'])
        if card.get('wildcard_normal'):
            parts.append('生效期间骰子 GL 可当普通资源支付（见特殊说明）')
    if ctype == 'C' and not card.get('abebe'):
        parts.append('一次性手牌：实际使用后弃置')
    return '；'.join(parts) or None


def _card_display_summary(cid):
    """单卡统一展示投影：spectator snapshot 与 card catalog 共享同一事实源。

    纯读正式 CARDS + _card_effect_summary 派生，不复制第二份 summary 实现。
    stage / vp / effect_summary 恒存在（无值即 null / 0），由调用方按协议省略。
    """
    card = CARDS[cid]
    return {
        'card_id': cid,
        'name': card['name'],
        'type': card['type'],
        'stage': card['stage'],
        'cost': dict(card['cost']),
        'vp': card['vp'],
        'effect_summary': _card_effect_summary(card),
    }


# catalog details 白名单：按 type 只投影正式 CARDS 中真实存在的规则字段；
# 不发明统一字段、不给缺失字段补 null、不暴露 Runtime 私有状态。
_CATALOG_CV_RULE_FIELDS = (
    'provide', 'extra_die', 'reroll', 'upkeep', 'sub_buy', 'upkeep_discount',
    'upkeep_sub', 'shorten_debuff', 'cancel_debuff_once', 'bl_convert',
    'convert_turn', 'flex')
_CATALOG_EVENT_RULE_FIELDS = (
    'temp_res', 'extra_reroll_rounds', 'protect_market', 'temp_dice',
    'upkeep_reduce', 'pre_cancel_debuff')
_CATALOG_CHILDHOOD_RULE_FIELDS = (
    'temp_res', 'temp_gl', 'cancel_debuff', 'discount_type', 'temp_dice',
    'abebe')
_CATALOG_DEBUFF_RULE_FIELDS = (
    'extra_cost', 'block_event', 'reroll_delta', 'gl_threshold',
    'virtual_bl', 'block_type')
_CATALOG_FATE_RULE_FIELDS = (
    'immediate', 'reroll_delta', 'first_discount', 'upkeep_reduce',
    'purchase_limit', 'extra_cost', 'discount', 'block_event', 'dice_delta',
    'virtual_bl', 'lock_reroll_on_bl', 'wildcard_normal')
_CARD_CATALOG_RULE_FIELDS = {
    'H': _CATALOG_CV_RULE_FIELDS, 'K': _CATALOG_CV_RULE_FIELDS,
    'R': _CATALOG_CV_RULE_FIELDS, 'W': _CATALOG_CV_RULE_FIELDS,
    'P': _CATALOG_CV_RULE_FIELDS, 'E': _CATALOG_EVENT_RULE_FIELDS,
    'C': _CATALOG_CHILDHOOD_RULE_FIELDS, 'D': _CATALOG_DEBUFF_RULE_FIELDS,
    'F': _CATALOG_FATE_RULE_FIELDS,
}


def _json_ready(value):
    """最小安全投影：tuple 转 list，使 catalog 严格等于其 JSON 序列化形态。"""
    if isinstance(value, (tuple, list)):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    return value


def card_catalog():
    """正式 Card Catalog 只读投影：唯一来源 CARDS，JSON-ready，无副作用。

    仅作展示，不参与任何规则判定；每次调用重新派生，不维护任何缓存状态。
    """
    cards = []
    for cid in sorted(CARDS):
        entry = _card_display_summary(cid)
        card = CARDS[cid]
        entry['details'] = {
            key: _json_ready(copy.deepcopy(card[key]))
            for key in _CARD_CATALOG_RULE_FIELDS[card['type']]
            if key in card
        }
        cards.append(entry)
    return {'cards': cards}


def _payment_source_parts(plan):
    """从唯一支付 plan 派生改变其成本来源的极短说明；纯读 plan 字段。"""
    parts = []
    gl_free = plan.get('gl_free_acquisitions') or []
    if gl_free:
        parts.append('%s 以 3 GL 免费取得' % '、'.join(gl_free))
    for cid in plan.get('consumed_childhood_card_ids', []):
        # 童年牌按正式机制分类：C01-C05 是临时资源（temp_res/temp_gl）；
        # C07-C10 是一次性购买折扣（来源由 discounts_used 短句说明），
        # C06 保护 / C11 额外骰 / C12 非一次性不构成购买资源来源，不产生短句。
        card = CARDS.get(cid) or {}
        if card.get('temp_res'):
            sym, n = card['temp_res']
            parts.append('消耗童年牌 %s（临时 %s×%d）' % (cid, sym, n))
        elif card.get('temp_gl'):
            parts.append('消耗童年牌 %s（临时 GL×%d）'
                         % (cid, card['temp_gl']))
    for item in plan.get('event_temporary_resources_used', []):
        parts.append('消耗事件 %s 的临时 %s×%d' % (
            item['event_card_id'], item['resource'], item['amount']))
    for d in plan.get('discounts_used', []):
        parts.append('以 %s 折扣取得 %s' % (d['childhood_card_id'],
                                            d['card_id']))
    for sub in plan.get('substitutions_used', []):
        parts.append('以 %s×1 替代 %s×1' % (sub['from'], sub['to']))
    for m in plan.get('purchase_modifiers', []):
        parts.append('受 %s 效果修正' % m['source_card_id'])
    return parts


def _payment_summary(plan):
    """从唯一支付 plan 派生极短支付说明；纯读 plan 字段，不复制 solver。"""
    parts = _payment_source_parts(plan)
    spent = plan.get('spent_resources') or {}
    if spent:
        parts.append('支付 %s' % _res_text(sorted(spent.items())))
    # method 按 plan 结构字段分类，不按 consumed_childhood_card_ids 粗分类：
    # C01-C05 与 C07-C10 同入 consumed 列表但机制不同。童年牌按 CARDS 机制
    # 字段细分（与 _payment_source_parts 同一事实源）；C05 临时 GL 只流向
    # 3 GL 免费取得（命中 gl_free_acquisitions）或直接支付 GL 成本，后者单列。
    child_ids = plan.get('consumed_childhood_card_ids') or []
    child_temp_gl = [cid for cid in child_ids
                     if (CARDS.get(cid) or {}).get('temp_gl')]
    child_temp_res = [cid for cid in child_ids
                      if (CARDS.get(cid) or {}).get('temp_res')]
    if plan.get('gl_free_acquisitions'):
        method = 'gl_free_acquisition'
    elif child_temp_gl:
        method = 'childhood_temp_gl'
    elif child_temp_res:
        method = 'childhood_temp_resource'
    elif plan.get('event_temporary_resources_used'):
        method = 'event_temp_resource'
    elif plan.get('discounts_used'):
        method = 'childhood_discount'
    elif plan.get('substitutions_used'):
        method = 'substitution'
    elif plan.get('purchase_modifiers'):
        method = 'fate_modifier'
    else:
        method = 'normal'
    return method, '；'.join(parts) or '无支付'


# 终局计分速查 overview：数值全部由 scoring 事实源插值派生（CURVE_A），
# 不在文案里手抄第二份数值；n>13 的 SA-CURVE 外推假设不向 AI 展示
# （单类牌池上限 13 张，正式区间内不触达）。仅作展示，不参与任何规则判定。
_CURVE_TOP = len(CURVE_A) - 1
_SCORE_OVERVIEW = (
    '终局计分：H/K/R 按终局持有张数查累计分表，0..%d 张依次为 %s；'
    'P 按卡面 vp，持有即计、无需 active；W 无统一基础分；每张真实经历的 '
    'Debuff +1（被完全取消的触发不计）；另加两张 Life Goal（见各自 '
    'scoring_text）。'
) % (_CURVE_TOP, '、'.join(str(v) for v in CURVE_A))

_RULE_HINTS = {
    'score_overview': _SCORE_OVERVIEW,
    'resource_lifetime':
        'total_available_resources = 稳定收入 stable_resources + 本回合骰子；'
        '回合结束时未花费的骰子资源消失，只有 stable 结转到下一回合。',
    'market_flow':
        '普通市场每回合总离场固定3张（含本轮购买）；因此买0/1/2张时，'
        '系统还会淘汰3/2/1张。具体淘汰对象由后台裁决。',
    'gl_bl':
        'GL（好运）不是 H/K/R/M 百搭：用途是支付 Fate 的 GL 成本，或凑满 3 个'
        '免费取得市场牌一张（Debuff D07 生效时门槛为 4）。BL（厄运）会冻结'
        '该骰（不可重掷），也可用于支付 Fate 的 BL 成本。',
    'placement':
        'top = 置顶并成为该类唯一生效卡（active，提供稳定产出）；bury = 埋入'
        '堆底、不生效。两种放法都计入按张数的 Life Goal（cv_min 等计全部'
        '持卡）。W/P 类购入后自动置顶，没有 placement 决策。',
    'maintenance':
        '维护决策要为带 upkeep 的生效卡支付维护费；放弃支付的卡会从履历堆'
        '弃置（不只是停用），也不再计入按张数的 Life Goal。',
    'fate_active_slot':
        '生效中的 Fate 唯一：新取得的 Fate 会替换当前生效者（旧牌仍留在'
        '命运堆、计入 LG17 配对数）。Fate 一经取得永久有效。',
}


class GameSession:
    """暂停并推进一局游戏；当前走到首个成年回合的结束边界。"""

    def __init__(self, seed=None, rng=None, cfg=None, shuffle=True,
                 forced_goals=None, player_name='AI玩家', player_emoji='🤖'):
        if seed is not None and rng is not None:
            raise ValueError('seed and rng are mutually exclusive')
        self.rng = rng if rng is not None else random.Random(seed)
        # 仅用于 spectator 展示，不属于 Engine 规则状态或决策输入。
        self.player_identity = {
            'name': player_name if isinstance(player_name, str) and player_name
            else 'AI玩家',
            'emoji': player_emoji if isinstance(player_emoji, str) and player_emoji
            else '🤖',
        }
        # 仅供同一正式局在 MCP worker 与 HTTP listener 间互斥访问；
        # 不属于规则状态，不参与任何决策、序列化或 spectator 投影。
        self._access_lock = threading.RLock()
        self.game = Game(
            cfg or CONFIGS['V06'],
            None,
            self.rng,
            shuffle=shuffle,
            forced_goals=forced_goals,
            defer_childhood=True,
        )
        self._rerolls_remaining = None
        self._purchase_ready = False
        self._purchase_plan_cache = None
        self._purchase_target = None
        self._purchase_executed = False
        self._debuff_resolved = False
        self._debuff_shorten_resolved = False
        self._maintenance_plan_cache = None
        self._maintenance_resolved = False
        self._market_cleanup_resolved = False
        self._turn_closeout_resolved = False
        # 终局最终指定：final_flex_designation 决策提交后固化，仅入终局计分；
        # 与 engine.flex_choice（每回合清空的回合内指定）互不通用。
        self._final_flex_designation = None
        self._previous_turn_result = None
        self._debuff_outcome = None
        self._debuff_drawn_card_id = None
        self._debuff_cancelled_by = None
        self._next_turn_started = False
        self._initial_roll_resolved = False
        self._pre_roll_effects_used = None
        self._decision_revision = 0
        # 围观层的正式随机骰果序列；不复用通用 decision revision。
        self._dice_roll_revision = 0
        # 展示层专用：已展示过的通用机制 hint key。只在 submit_action
        # 成功接受后更新；current_decision 纯读，不参与任何规则判定。
        self.seen_rule_hints = set()
        # 仅供围观页显示的有界结果日志；不属于 Engine 规则状态，也不参与
        # 任何决策或裁决。事件只在 Runtime 已确认正式状态变更后写入。
        self._recent_events = []
        self._next_recent_event_seq = 1
        self._append_recent_event('game_started', '游戏开始')

    def _append_recent_event(self, event_type, text, details=None):
        """追加一条展示事件；不读取或改变任何 Engine 规则状态。"""
        event = {
            'seq': self._next_recent_event_seq,
            'type': event_type,
            'turn': self.game.turn,
            'stage': self.game.stage,
            'text': text,
        }
        if details:
            event['details'] = copy.deepcopy(details)
        self._next_recent_event_seq += 1
        self._recent_events.append(event)
        if len(self._recent_events) > 20:
            del self._recent_events[:-20]

    def _record_fate_resolved(self, card_id):
        self._append_recent_event(
            'fate_resolved', '命运「%s」已结算' % CARDS[card_id]['name'],
            {'card_id': card_id})

    def _resolve_runtime_debuff(self, cancel_cid=None):
        """统一记录已完成的 Debuff 抽取，供自动与显式路径共用。"""
        replaced_card_id = self.game.current_debuff
        outcome = self.game.resolve_runtime_debuff(cancel_cid=cancel_cid)
        if outcome == 'drawn':
            card_id = self.game.current_debuff
            details = {'card_id': card_id}
            if replaced_card_id:
                details['replaced_card_id'] = replaced_card_id
            self._append_recent_event(
                'debuff_started', '逆境「%s」开始生效' % CARDS[card_id]['name'],
                details)
        return outcome

    def _cleanup_normal_market(self, protected=None):
        """复用正式市场清理，并且只在真实阶段切换后记录展示事件。"""
        old_stage = self.game.stage
        result = self.game.cleanup_normal_market(protected=protected)
        if result is not None and old_stage != self.game.stage:
            self._append_recent_event(
                'stage_changed', '进入%s期' % {
                    'youth': '青年', 'middle': '中年', 'elder': '老年',
                }.get(self.game.stage, self.game.stage),
                {'from_stage': old_stage, 'to_stage': self.game.stage})
        return result

    def _decision_kind(self):
        if self._turn_closeout_resolved:
            if self.game.game_over:
                if self._final_flex_designation is None \
                        and flex_actives(self.game.cv):
                    return 'final_flex_designation'
                return 'game_over'
            if not self._next_turn_started:
                return 'next_turn_ready'
            if self.game.pre_roll_open:
                return 'pre_roll_decision'
            # 首掷窗口关闭后必须落入下方统一回合内状态链，
            # 保证普通购买窗口每回合只被消费一次。
        if not self.game.childhood_complete:
            return ('childhood_pick_1' if self.game.childhood_draft_round == 0
                    else 'childhood_pick_2')
        if self.game.pre_roll_open:
            return 'pre_roll_c11'
        if self._purchase_executed:
            if self.game.pending_fate_immediate is not None:
                return 'fate_immediate_decision'
            if not self._debuff_resolved:
                return 'debuff_protection_decision'
            if self._debuff_shorten_pending():
                return 'debuff_shorten_decision'
            if self.game.pending_new:
                return 'placement_decision'
            if not self._maintenance_resolved:
                return 'maintenance_decision'
            if not self._market_cleanup_resolved:
                return 'market_protection_decision'
            return 'market_cleanup_resolved'
        if self.game.dice:
            if self.game.mh04_reaction_status() is not None:
                return 'mh04_decision'
            return 'purchase_ready' if self._purchase_ready else 'post_roll_decision'
        return 'ready_for_adult_turn'

    def _decision_id(self):
        if not self.game.childhood_complete:
            return '%s:%d' % (self._decision_kind(),
                               self.game.childhood_draft_round)
        if self._decision_kind() == 'final_flex_designation':
            return 'final_flex_designation:%d' % self.game.turn
        if self._decision_kind() == 'fate_immediate_decision':
            return self.game.pending_fate_immediate['decision_id']
        if self._decision_kind() == 'placement_decision':
            return 'placement_decision:%d:%d:%s' % (
                self.game.turn, len(self.game.pending_new),
                self.game.pending_new[0])
        return '%s:%d:%d:%d' % (self._decision_kind(), self.game.turn,
                                self._rerolls_remaining or 0,
                                self._decision_revision)

    def _finish_first_roll(self):
        self.game.roll_first_dice()
        self._dice_roll_revision += 1
        self._rerolls_remaining = self.game.normal_reroll_rounds()
        self._append_recent_event(
            'dice_rolled', '掷出 %d 颗骰子' % len(self.game.dice),
            {'dice_count': len(self.game.dice)})

    def _has_dice_post_actions(self):
        return (((self._rerolls_remaining or 0) > 0
                 and not self.game.normal_rerolls_locked())
                or bool(self.game.available_special_rerolls())
                or self.game.yk05_conversion_available())

    def _advance_after_dice_change(self):
        """骰面变动后，先留出强制反应；无任何骰后选择时自动锁骰。"""
        if self.game.mh04_reaction_status() is not None:
            return
        if (self.game.dice and not self._purchase_ready
                and not self._purchase_executed and not self._has_dice_post_actions()):
            self._enter_purchase_ready()

    def _auto_advance(self):
        """自动执行不需要 AI 选择的首回合启动与首次掷骰。"""
        if self.game.childhood_complete and self.game.turn == 0:
            self.game.start_first_adult_turn()
        if (self.game.pre_roll_open and not self._next_turn_started
                and 'C11' not in self.game.hand):
            self._finish_first_roll()
            self._advance_after_dice_change()
        if (self._purchase_executed and not self._debuff_resolved
                and self.game.pending_fate_immediate is None):
            if self._debuff_protection_is_pending():
                return
            outcome = self._resolve_runtime_debuff(cancel_cid=None)
            self._debuff_resolved = True
            self._debuff_outcome = outcome
            if outcome == 'drawn':
                self._debuff_drawn_card_id = self.game.current_debuff
            self._place_forced_cards()
        if (self._purchase_executed and self._debuff_resolved
                and self._debuff_shorten_pending()):
            # 抽到 Debuff 且 OH-01 可用：暂停等待 AI 决定是否支付缩短，
            # 扣款后的 pool 必须先于维护窗口定格。
            return
        if (self._purchase_executed and self._debuff_resolved
                and not self.game.pending_new and not self._maintenance_resolved):
            plans = self._maintenance_plans(cache=True)
            targets = set(self.game.maintenance_targets())
            full_without_me01 = any(
                set(plan['maintained_card_ids']) == targets
                and not plan['me01_used'] for plan in plans)
            if full_without_me01 or len(plans) == 1:
                result = self.game.apply_maintenance_plan(
                    self._maintenance_plan_cache[0])
                if result is None:
                    raise RuntimeError('cached maintenance plan became invalid')
                self._maintenance_plan_cache = None
                self._maintenance_resolved = True
        if (self._purchase_executed and self._debuff_resolved
                and not self.game.pending_new and self._maintenance_resolved
                and not self._market_cleanup_resolved):
            if self._market_protection_card() is None:
                result = self._cleanup_normal_market()
                if result is None:
                    raise RuntimeError('automatic market cleanup became invalid')
                self._market_cleanup_resolved = True
        if self._market_cleanup_resolved:
            # 每回合恰好 closeout 一次；_turn_closeout_resolved 保持 True，
            # 仅作为 _decision_kind 区分首回合与新回合 pre_roll 的路由标记。
            previous = self.game.closeout_adult_turn()
            previous['debuff_result'] = {
                'outcome': self._debuff_outcome,
                'drawn_card_id': self._debuff_drawn_card_id,
                'cancelled_by_card_id': self._debuff_cancelled_by,
            }
            self._previous_turn_result = copy.deepcopy(previous)
            archived_card_id = previous['debuff_lifecycle']['archived_card_id']
            if archived_card_id:
                self._append_recent_event(
                    'debuff_expired', '逆境「%s」已结束' % CARDS[archived_card_id]['name'],
                    {'card_id': archived_card_id})
            self._append_recent_event(
                'turn_completed', '完成第 %d 回合' % previous['completed_turn'],
                {'completed_turn': previous['completed_turn'],
                 'next_turn': previous['next_turn']})
            if previous['game_over']:
                self._append_recent_event('game_over', '本局游戏结束')
            self._turn_closeout_resolved = True
            self._next_turn_started = False
            self._rerolls_remaining = None
            self._purchase_ready = False
            self._purchase_plan_cache = None
            self._purchase_target = None
            self._purchase_executed = False
            self._debuff_resolved = False
            self._debuff_shorten_resolved = False
            self._maintenance_plan_cache = None
            self._maintenance_resolved = False
            self._market_cleanup_resolved = False
            self._debuff_outcome = None
            self._debuff_drawn_card_id = None
            self._debuff_cancelled_by = None
        if self._turn_closeout_resolved and not self.game.game_over \
                and not self._next_turn_started:
            self.game.start_adult_turn()
            self._next_turn_started = True
            self._initial_roll_resolved = False
            self._pre_roll_effects_used = None
        if (self._next_turn_started and not self._initial_roll_resolved
                and self.game.pre_roll_open and not self._pre_roll_has_choices()):
            effects = self.game.apply_pre_roll_declarations({})
            if effects is None:
                raise RuntimeError('automatic pre-roll declaration became invalid')
            self._pre_roll_effects_used = effects
            self._finish_first_roll()
            self._initial_roll_resolved = True
        if self._initial_roll_resolved:
            # initial_roll_resolved 是内部桥：首次骰结果直接进入统一骰后控制器。
            self._initial_roll_resolved = False
            self._advance_after_dice_change()
        if (self.game.dice and not self._purchase_ready
                and not self._purchase_executed):
            self._advance_after_dice_change()

    def _current_opportunities(self):
        card_ids = list(self.game.market)
        if self.game.fate_window():
            card_ids.extend(self.game.fate_market)
        cards = []
        for cid in card_ids:
            card = copy.deepcopy(CARDS[cid])
            if CARDS[cid]['type'] == 'F':
                # Fate 市场牌可跨窗口驻留且无 entry 追踪：效果说明常驻。
                summary = _card_effect_summary(card)
                if summary:
                    card['effect_summary'] = summary
            elif self.game.market_entry.get(cid, -1) == self.game.turn - 1:
                # 市场牌仅在进场回合（首次可见）附效果说明，驻留期不重复。
                summary = _card_effect_summary(card)
                if summary:
                    card['effect_summary'] = summary
            if cid in _SPECIAL_NOTES:
                card['special_note'] = _SPECIAL_NOTES[cid]
            cards.append(card)
        return cards

    def _legal_acquisition_plans(self, pool, cache=False):
        generator = (self.game.enumerate_joint_purchase_plans
                     if self.game.fate_window()
                     else self.game.enumerate_legal_purchase_plans)
        if cache and self._purchase_plan_cache is None:
            self._purchase_plan_cache = generator(pool_override=pool)
        plans = (self._purchase_plan_cache if cache else
                 generator(pool_override=pool))
        return copy.deepcopy(plans)

    def _enter_purchase_ready(self):
        self._purchase_ready = True
        self._purchase_plan_cache = None
        self._purchase_target = None

    def _purchase_target_groups(self):
        """把缓存中的正式 payment/joint plans 按 (ordinary, fate) 目标组合分组。"""
        groups = {}
        for plan in self._purchase_plan_cache:
            ordinary = (plan['ordinary_card_ids']
                        if 'ordinary_card_ids' in plan else plan['card_ids'])
            key = (tuple(ordinary), plan.get('fate_card_id'))
            groups.setdefault(key, []).append(plan)
        return groups

    def _purchase_targets_view(self):
        """第一层视图：只公布目标组合，AI 一律先选目标；plan_id 仅存在于第二层。

        目标条目的 ordinary_card_ids/fate_card_id 即提交动作的完整字段；
        提交合法性由 submit 基于内部 target groups 校验，不依赖展示层。
        """
        entries = []
        for (ordinary, fate), plans in self._purchase_target_groups().items():
            entry = {'ordinary_card_ids': list(ordinary),
                     'fate_card_id': fate,
                     'payment_option_count': len(plans)}
            if len(plans) > 1:
                entry['payment_options'] = [
                    {'spent_resources': dict(p['spent_resources']),
                     'remaining_resources': dict(p['remaining_resources'])}
                    for p in plans]
                # 多支付方案：仅对确有成本来源（折扣/临时资源/替代/修正）的
                # 方案补一条极短来源，解释"为何比卡面成本少付"；普通方案不加键。
                for opt, plan in zip(entry['payment_options'], plans):
                    sources = _payment_source_parts(plan)
                    if sources:
                        opt['payment_sources'] = sources
            else:
                # 单支付目标会被直接自动执行：从唯一 plan 派生极短支付说明，
                # 让 3GL 免费 / 临时资源 / 折扣 / 替代在第一层即可见。
                method, summary = _payment_summary(plans[0])
                entry['payment_method'] = method
                entry['payment_summary'] = summary
            entries.append(entry)
        return entries

    def _published_plan_ids(self):
        """允许直接提交的 plan_id：仅第二层选中目标的支付 plans。"""
        if self._purchase_target is None:
            return set()
        return {p['plan_id']
                for p in self._purchase_target_groups()[
                    self._purchase_target]}

    def _match_purchase_target(self, action):
        ordinary = action['ordinary_card_ids']
        fate = action['fate_card_id']
        if not isinstance(ordinary, list) or not all(
                isinstance(cid, str) for cid in ordinary):
            return None
        if fate is not None and not isinstance(fate, str):
            return None
        key = (tuple(ordinary), fate)
        return key if key in self._purchase_target_groups() else None

    def _execute_purchase_plan(self, selected):
        if self.game.fate_window():
            success = self.game.execute_joint(
                (tuple(selected['ordinary_card_ids']), selected['fate_card_id']),
                selected, locked_resources=self.game.post_roll_resources())
        else:
            success = self.game.apply_precomputed_purchase_plan(
                selected, pool_override=self.game.post_roll_resources())
        if not success:
            return False
        card_ids = list((self.game.purchase_result or {}).get(
            'purchased_card_ids', []))
        if card_ids:
            names = '、'.join(CARDS[cid]['name'] for cid in card_ids)
            self._append_recent_event(
                'card_acquired', '获得「%s」' % names, {'card_ids': card_ids})
        fate_card_id = self.game.fate_acquired_this_turn
        if fate_card_id and self.game.pending_fate_immediate is None:
            self._record_fate_resolved(fate_card_id)
        return True

    def _place_forced_cards(self):
        for cid in list(self.game.pending_new):
            if CARDS[cid]['type'] in ('W', 'P'):
                self.game.place_pending_card(cid, 'top')

    def _debuff_protection_is_pending(self):
        """触发且无事前取消、存在可用保护卡时，暂停等待 AI 显式决策。"""
        status = self.game.debuff_trigger_status()
        return (status['triggered'] and not self.game.pre_debuff_cancel
                and bool(self.game.debuff_cancel_options()))

    def _debuff_shorten_pending(self):
        """本回合刚抽到 Debuff 且 OH-01 active、池足额时，缩短决策待处理。

        "刚抽到"从既有状态派生：本回合抽取的 Debuff 其
        active_from_turn 恒为下一回合；非本回合抽取则不提供缩短。
        """
        if self._debuff_shorten_resolved:
            return False
        game = self.game
        option = game.debuff_shorten_option()
        if option is None or not game.current_debuff:
            return False
        if game.debuff_active_from_turn != game.turn + 1:
            return False
        return all(game.pool.get(s, 0) >= n
                   for s, n in option['cost'].items())

    def _maintenance_plans(self, cache=False):
        if cache and self._maintenance_plan_cache is None:
            self._maintenance_plan_cache = self.game.maintenance_plans()
        plans = (self._maintenance_plan_cache if cache else
                 self.game.maintenance_plans())
        return copy.deepcopy(plans)

    def _market_protection_card(self):
        """返回当前市场清理窗口可合法使用的 YE-04，或 None。"""
        if not self.game.market:
            return None
        return self.game.find_hand_effect('protect_market')

    def _pre_roll_event_ids(self):
        if not self.game.event_usage_allowed():
            return []
        return [cid for cid in ('YE-05', 'ME-02') if cid in self.game.hand]

    def _pre_roll_has_choices(self):
        return bool(self.game.flexible_stable_options()
                    or 'C11' in self.game.hand
                    or self._pre_roll_event_ids())

    def _pre_roll_surfaced(self):
        """本回合是否已出现/将有 pre_roll_decision；纯派生，无新状态。

        _pre_roll_has_choices() 覆盖未消耗的选择（flex 选项跨回合稳定、
        跳过的开关仍在手）；_pre_roll_effects_used['used_card_ids'] 覆盖
        声明时用掉的 C11/YE-05/ME-02。turn 1 两者皆空且无本 kind，取 False。
        """
        if self._pre_roll_has_choices():
            return True
        effects = self._pre_roll_effects_used or {}
        return bool(set(effects.get('used_card_ids', []))
                    & {'C11', 'YE-05', 'ME-02'})

    def _expose_previous_turn_result(self, stage):
        """上一回合结果每回合至多一个携带者的门控（纯派生）。"""
        if self._previous_turn_result is None:
            return False
        if stage == 'pre_roll':
            return True
        if self._pre_roll_surfaced():
            return False
        if stage == 'post_roll':
            return not self.game.reroll_happened_this_turn
        if stage == 'purchase_l1':
            # 有可用重掷/特殊重掷时骰后决策必然渲染过；全无即自动锁死兜底。
            return not (self.game.reroll_happened_this_turn
                        or self._has_dice_post_actions())
        return False

    def _attach_previous_turn_result(self, payload, stage):
        if self._expose_previous_turn_result(stage):
            payload['previous_turn_result'] = copy.deepcopy(
                self._previous_turn_result)
        return payload

    def _card_summary(self, cid):
        card = CARDS[cid]
        return {'card_id': cid, 'name': card['name'], 'type': card['type']}

    def _presented_card(self, cid):
        """完整卡牌数据 + 派生效果说明 + 特殊注；纯展示，不改变卡牌字段。"""
        card = copy.deepcopy(CARDS[cid])
        summary = _card_effect_summary(card)
        if summary:
            card['effect_summary'] = summary
        if cid in _SPECIAL_NOTES:
            card['special_note'] = _SPECIAL_NOTES[cid]
        return card

    def _spectator_card_summary(self, cid):
        """围观桌面所需的最小单卡展示投影；不下发原始完整规则字段。

        与 card catalog 共享 _card_display_summary 同一事实源；此处仅按
        紧凑协议省略 null / 零值展示字段以控制 payload 体积。
        """
        summary = _card_display_summary(cid)
        for key in ('stage', 'vp', 'effect_summary'):
            if not summary[key]:
                del summary[key]
        return summary

    def _life_goals_summary(self, with_text=False):
        """当前 2 张 Life Goal；scoring_rule 直接取自 scoring.LG_RULES 共享事实源。

        with_text 时附 lg_scoring_text 口径句（目标首次公开：Draft、首个
        成年决策、F03 换目标上下文）；其余决策维持裸 rule 以控制体积。
        """
        goals = []
        for gid in self.game.goals:
            entry = {'id': gid, 'name': LG_NAMES[gid],
                     'scoring_rule': copy.deepcopy(LG_RULES[gid])}
            if with_text:
                entry['scoring_text'] = lg_scoring_text(LG_RULES[gid])
            goals.append(entry)
        return goals

    def _childhood_summary(self):
        """成年阶段仍在桌面的童年牌；效果由 _SPECIAL_NOTES/_card_effect_summary 纯派生。

        已消耗的 C01-C11 不再重复展示；C12 阿贝贝即便能力已用仍留在桌面，
        以 used 状态继续可见（正式规则：牌仍留在桌面）。不重复下发 draft 溯源。
        """
        items = []
        for cid in self.game.childhood_kept:
            card = CARDS[cid]
            if card.get('abebe'):
                status = 'used' if self.game.abebe_used else 'held'
            elif cid in self.game.hand:
                status = 'available'
            else:
                continue
            item = {'card_id': cid, 'name': card['name'], 'status': status}
            effect = _SPECIAL_NOTES.get(cid) or _card_effect_summary(card)
            if effect:
                item['effect_summary'] = effect
            items.append(item)
        return items

    def _debuff_summary(self):
        if not self.game.current_debuff:
            return None
        return {
            'card_id': self.game.current_debuff,
            'active_from_turn': self.game.debuff_active_from_turn,
            'turns_remaining': self.game.debuff_turns_remaining,
            'card': self._presented_card(self.game.current_debuff),
        }

    def spectator_snapshot(self):
        """返回当前正式局面的纯读取围观投影。

        此方法不得调用 current_decision() 或 _auto_advance()：围观刷新不能
        启动回合、掷骰、补市场或改变任何 Runtime 暂停状态。
        """
        game = self.game
        completed_turn = (self._previous_turn_result or {}).get(
            'completed_turn', 0)
        dice = list(game.dice)
        frozen_indices = []
        # 只有统一骰后控制器实际处于可重掷阶段时，冻结索引才有展示语义。
        # _decision_kind() 与 first_normal_reroll_status() 均只读取当前状态。
        if (dice and self._decision_kind() in ('post_roll_decision',
                                               'mh04_decision')):
            frozen_indices = list(
                game.first_normal_reroll_status()['frozen_indices'])

        current_debuff = self._debuff_summary()
        if current_debuff is not None:
            card = current_debuff['card']
            current_debuff = {
                'card_id': current_debuff['card_id'],
                'name': card['name'],
                'active_from_turn': current_debuff['active_from_turn'],
                'turns_remaining': current_debuff['turns_remaining'],
                'effect_summary': card.get('effect_summary', ''),
            }

        cv = {}
        for cls in 'HKRWP':
            stack = list(game.cv[cls])
            cv[cls] = {
                'top_card_id': game.active(cls),
                # stack 内所有牌都仍是当前持有履历，而非历史记录。
                'stack': [self._spectator_card_summary(cid) for cid in stack],
            }

        goals = []
        for goal in self._life_goals_summary(with_text=True):
            goals.append({
                'id': goal['id'],
                'name': goal['name'],
                'scoring_text': goal['scoring_text'],
            })

        return {
            'status': 'game_over' if game.game_over else 'in_progress',
            'game_over': game.game_over,
            'stage': game.stage,
            'current_turn': game.turn,
            'completed_turn': completed_turn,
            'player_identity': copy.deepcopy(self.player_identity),
            'opportunity_market': [
                self._spectator_card_summary(cid) for cid in game.market],
            'fate_market': {
                'is_open': game.fate_window(),
                'cards': [self._spectator_card_summary(cid)
                          for cid in game.fate_market],
            },
            'life_goals': goals,
            'event_hand': [
                self._spectator_card_summary(cid) for cid in game.hand
                if CARDS[cid]['type'] == 'E'],
            'current_debuff': current_debuff,
            'dice': {
                'values': dice,
                'max_dice_count': 7,
                'frozen_indices': frozen_indices,
                'roll_revision': self._dice_roll_revision,
            },
            'cv': cv,
            'recent_events': copy.deepcopy(self._recent_events),
        }

    def _gl_bl_visible(self, kind):
        """GL/BL 是否已成为当前决策中真实可见、可用于策略判断的信息。

        仅基于结构化字段判断（骰面 / 可用资源池 / 童年牌效果 / Fate 成本 /
        唯一支付 plan），不做任何序列化文本扫描。
        """
        game = self.game
        if 'GL' in game.dice or 'BL' in game.dice:
            return True
        pool = game.post_roll_resources()
        if pool.get('GL', 0) > 0 or pool.get('BL', 0) > 0:
            return True
        if kind.startswith('childhood_pick'):
            if any(CARDS[cid].get('temp_gl')
                   for cid in game.childhood_candidates()):
                return True
        elif any(CARDS[cid].get('temp_gl') for cid in game.hand):
            return True
        if game.fate_window() and any(
                'GL' in CARDS[cid].get('cost', {})
                or 'BL' in CARDS[cid].get('cost', {})
                for cid in game.fate_market):
            return True
        if (kind == 'purchase_ready' and self._purchase_target is None
                and self._purchase_plan_cache is not None):
            for plans in self._purchase_target_groups().values():
                if len(plans) != 1:
                    continue
                plan = plans[0]
                if (plan.get('gl_free_acquisitions')
                        or plan.get('spent_resources', {}).get('GL')
                        or plan.get('spent_resources', {}).get('BL')):
                    return True
        return False

    def _rule_hint_keys(self, kind):
        """当前决策本应展示的通用机制 hint key；纯派生，与 seen 无关。"""
        keys = set()
        if kind == 'childhood_pick_1':
            # 终局计分骨架：只在本局第一条决策一次性公开，之后靠 seen 集合不重复。
            keys.add('score_overview')
        if kind in ('post_roll_decision', 'purchase_ready'):
            keys.add('resource_lifetime')
            keys.add('market_flow')
        if kind in ('post_roll_decision', 'purchase_ready',
                    'childhood_pick_1', 'childhood_pick_2') \
                and self._gl_bl_visible(kind):
            keys.add('gl_bl')
        if kind == 'placement_decision':
            keys.add('placement')
        if kind == 'maintenance_decision':
            keys.add('maintenance')
        if (kind in ('pre_roll_decision', 'post_roll_decision',
                     'purchase_ready', 'maintenance_decision',
                     'market_protection_decision')
                and self.game.active_fate_card() is not None):
            keys.add('fate_active_slot')
        return keys

    def _pending_rule_hints(self, kind):
        """尚未展示过的 hint 文案；只读 seen_rule_hints，不修改。"""
        pending = self._rule_hint_keys(kind) - self.seen_rule_hints
        return {key: _RULE_HINTS[key] for key in sorted(pending)}

    def _active_fate_view(self):
        fate = self.game.active_fate_card()
        return self._presented_card(fate['id']) if fate else None


    def _pre_roll_decision(self):
        flex_options = self.game.flexible_stable_options()
        event_ids = self._pre_roll_event_ids()
        decision = {
            'decision_id': self._decision_id(),
            'kind': 'pre_roll_decision',
            'turn': self.game.turn,
            'life_goals': self._life_goals_summary(
                with_text=(self.game.turn == 1)),
            'childhood_cards': self._childhood_summary(),
                        'active_cards': [self._card_summary(cid)
                             for cid in self.game.active_cards()],
            'active_fate': self._active_fate_view(),
            'current_debuff': self._debuff_summary(),
            'fixed_stable_resources': dict(sorted(self.game.stable_pool.items())),
            'flexible_resource_choices': [
                {'card_id': cid, 'allowed_resources': list(allowed)}
                for cid, allowed in sorted(flex_options.items())],
            'available_pre_roll_cards': [self._card_summary(cid)
                                         for cid in (['C11'] if 'C11' in self.game.hand else [])
                                         + event_ids],
            'base_dice_count': self.game.current_dice_count(),
            'max_dice_count': 7,
            'legal_action_schema': {
                'flex_resource_choices': {
                    cid: list(allowed) for cid, allowed in flex_options.items()},
                'use_c11': 'boolean',
                'use_ye05': 'boolean',
                'use_me02': 'boolean',
            },
        }
        hints = self._pending_rule_hints('pre_roll_decision')
        if hints:
            decision['rule_hints'] = hints
        return self._attach_previous_turn_result(decision, 'pre_roll')

    def _initial_roll_decision(self):
        pool = self.game.post_roll_resources()
        return {
            'decision_id': self._decision_id(),
            'kind': 'initial_roll_resolved',
            'turn': self.game.turn,
            'dice': list(self.game.dice),
            'dice_count': len(self.game.dice),
            'stable_resources': dict(sorted(self.game.stable_pool.items())),
            'total_available_resources': dict(sorted(pool.items())),
            'normal_rerolls_available': self.game.normal_reroll_rounds(),
            'active_cards': [self._card_summary(cid)
                             for cid in self.game.active_cards()],
            'current_debuff': self._debuff_summary(),
            'pre_roll_effects_used': copy.deepcopy(self._pre_roll_effects_used),
            'current_opportunities': self._current_opportunities(),
            'candidates': [],
            'legal_actions': [],
        }

    def _post_roll_decision(self, kind):
        status = self.game.first_normal_reroll_status()
        pool = self.game.post_roll_resources()
        ye03_available = (not self.game.reroll_happened_this_turn
                          and 'YE-03' in self.game.hand
                          and self.game.event_usage_allowed())
        abilities = []
        if 'C11' in self.game.hand:
            abilities.append({'card_id': 'C11', 'kind': 'add_two_dice'})
        if self.game.abebe_held and not self.game.abebe_used:
            abilities.append({'card_id': 'C12', 'kind': 'unfreeze_one_bad_luck'})
        special = self.game.available_special_rerolls()
        for item in special:
            abilities.append({'card_id': item['card_id'], 'kind': 'special_reroll',
                              'target_indices': list(item['target_indices'])})
        if ye03_available:
            extra_rounds = CARDS['YE-03']['extra_reroll_rounds']
            abilities.append({
                'card_id': 'YE-03',
                'kind': 'extra_normal_reroll_round',
                'extra_reroll_rounds': extra_rounds,
                # note 的数值取自卡面 extra_reroll_rounds；措辞用于消除
                # 旧键名 extra_round 曾造成的"额外游戏回合"歧义。
                'note': '为骰后阶段额外增加 %d 轮正常重掷'
                        '（normal reroll round），不是额外游戏回合'
                        % extra_rounds,
            })
        full_plans = self._legal_acquisition_plans(
            pool, cache=(kind == 'purchase_ready'))
        if kind == 'purchase_ready':
            if self._purchase_target is None:
                # 第一层：只公布目标组合；单支付目标携带可直接执行的 plan_id，
                # 多支付目标只附支付摘要（spent/remaining），完整 plans 留给第二层。
                # 目标条目自身即提交字段（ordinary_card_ids/fate_card_id），
                # 不再重复下发同一组合集的 legal_actions。
                legal_actions = ()
                purchase_view = {'purchase_targets':
                                 self._purchase_targets_view()}
            else:
                groups = self._purchase_target_groups()
                if self._purchase_target not in groups:
                    raise RuntimeError('pending purchase target disappeared')
                plans = copy.deepcopy(groups[self._purchase_target])
                legal_actions = [{'plan_id': p['plan_id']} for p in plans]
                purchase_view = {
                    'selected_purchase_target': {
                        'ordinary_card_ids': list(self._purchase_target[0]),
                        'fate_card_id': self._purchase_target[1]},
                    'legal_acquisition_plans': plans,
                }
        else:
            # 骰后只预览可取得目标集合；支付来源与方案分支仅在购买节点公开。
            seen = set()
            compact_plans = []
            for plan in full_plans:
                if self.game.fate_window():
                    target = (tuple(plan['ordinary_card_ids']),
                              plan['fate_card_id'])
                    compact = {
                        'ordinary_card_ids': list(target[0]),
                        'fate_card_id': target[1],
                    }
                else:
                    target = tuple(plan['card_ids'])
                    compact = {'card_ids': list(target)}
                if target not in seen:
                    seen.add(target)
                    compact_plans.append(compact)
            purchase_view = {'legal_acquisition_plans': compact_plans}
            legal_actions = [
                {'choice': 'normal_reroll', 'requires': 'indices',
                 'optional': ['use_ye03', 'use_c11', 'c12_index']},
                {'choice': 'special_reroll', 'requires': ['ability_card_id',
                 'die_index'], 'optional': ['use_ye03']},
                {'choice': 'proceed_to_purchase', 'optional': ['use_yk05']},
            ]
        decision = {
            'decision_id': self._decision_id(),
            'kind': kind,
            'turn': self.game.turn,
            'life_goals': self._life_goals_summary(
                with_text=(self.game.turn == 1)),
            'childhood_cards': self._childhood_summary(),
                        'dice': list(self.game.dice),
            'dice_count': len(self.game.dice),
            'active_card_ids': list(self.game.active_cards()),
            'active_fate': self._active_fate_view(),
            'stable_resources': dict(sorted(self.game.stable_pool.items())),
            'total_available_resources': dict(sorted(pool.items())),
            'current_opportunities': self._current_opportunities(),
            'normal_rerolls_remaining': self._rerolls_remaining,
            'remaining_normal_rerolls': self._rerolls_remaining,
            'normal_rerolls_available': self._rerolls_remaining,
            'normal_rerolls_locked': self.game.normal_rerolls_locked(),
            'rerollable_indices': status['rerollable_indices'],
            'frozen_indices': status['frozen_indices'],
            'freeze_reason': status['freeze_reason'],
            'available_abilities': abilities,
            'ye03_available': ye03_available,
            'c11_available': 'C11' in self.game.hand,
            'c12_available': self.game.abebe_held and not self.game.abebe_used,
            'pre_roll_effects_used': copy.deepcopy(self._pre_roll_effects_used),
            'current_debuff': self._debuff_summary(),
        }
        hints = self._pending_rule_hints(kind)
        if hints:
            decision['rule_hints'] = hints
        if kind == 'post_roll_decision':
            decision['yk05_conversion_available'] = \
                self.game.yk05_conversion_available()
        decision.update(purchase_view)
        # purchase L1 不携带 legal_actions：purchase_targets 条目即提交字段；
        # post_roll / purchase L2 维持各自原有 legal_actions。
        if legal_actions:
            decision['legal_actions'] = legal_actions
        stage = 'post_roll' if kind == 'post_roll_decision' else (
            'purchase_l1' if self._purchase_target is None
            else 'purchase_l2')
        return self._attach_previous_turn_result(decision, stage)

    def current_decision(self):
        """返回当前玩家可见决策；重复读取不会改变 decision_id 或状态。"""
        self._auto_advance()
        kind = self._decision_kind()
        if kind.startswith('childhood_pick'):
            card_ids = self.game.childhood_candidates()
            decision = {
                'decision_id': self._decision_id(),
                'kind': kind,
                'phase': 'childhood_draft',
                'draft_round': self.game.childhood_draft_round + 1,
                'draft_action': ('从 3 张候选中选 1 张加入童年牌'
                                 if self.game.childhood_draft_round == 0
                                 else '从重新抽取的 2 张候选中选 1 张加入童年牌'),
                'life_goals': self._life_goals_summary(with_text=True),
                'candidates': [self._presented_card(cid) for cid in card_ids],
                'legal_actions': [{'card_id': cid} for cid in card_ids],
            }
            hints = self._pending_rule_hints(kind)
            if hints:
                decision['rule_hints'] = hints
            return decision
        if kind == 'pre_roll_c11':
            return {
                'decision_id': self._decision_id(),
                'kind': kind,
                'life_goals': self._life_goals_summary(),
                'childhood_cards': self._childhood_summary(),
                                'candidates': [copy.deepcopy(CARDS['C11'])],
                'legal_actions': [{'choice': 'use'}, {'choice': 'skip'}],
            }
        if kind == 'pre_roll_decision':
            return self._pre_roll_decision()
        if kind == 'initial_roll_resolved':
            return self._initial_roll_decision()
        if kind == 'mh04_decision':
            eligible = self.game.mh04_reaction_status()
            return {
                'decision_id': self._decision_id(),
                'kind': kind,
                'turn': self.game.turn,
                'dice': list(self.game.dice),
                'eligible_bl_indices': list(eligible),
                'legal_actions': ([{'choice': 'skip'}]
                                  + [{'choice': 'use', 'target_index': i}
                                     for i in eligible]),
            }
        if kind == 'debuff_protection_decision':
            options = self.game.debuff_cancel_options()
            return {
                'decision_id': self._decision_id(),
                'kind': kind,
                'life_goals': self._life_goals_summary(),
                'childhood_cards': self._childhood_summary(),
                            'remaining_bad_luck': self.game.pool.get('BL', 0),
                'candidates': [self._presented_card(o['cid'])
                               for o in options],
                'legal_actions': ([{'choice': 'skip'}]
                                  + [{'choice': 'use',
                                      'source_card_id': o['cid']}
                                     for o in options]),
            }
        if kind == 'debuff_shorten_decision':
            option = self.game.debuff_shorten_option()
            return {
                'decision_id': self._decision_id(),
                'kind': kind,
                'life_goals': self._life_goals_summary(),
                'childhood_cards': self._childhood_summary(),
                            'debuff': self._presented_card(self.game.current_debuff),
                'shorten_card_id': option['cid'],
                'effect_hint': '支付 %s：当前 Debuff 持续时间减少 %d 回合'
                               '（最低 1 回合）。'
                               % (_res_text(sorted(option['cost'].items())),
                                  option['reduce']),
                'cost': dict(option['cost']),
                'duration_before': self.game.debuff_turns_remaining,
                'duration_after': max(
                    1, self.game.debuff_turns_remaining - option['reduce']),
                'pool': dict(sorted(
                    (s, n) for s, n in self.game.pool.items() if n > 0)),
                'legal_actions': [{'choice': 'skip'}, {'choice': 'pay'}],
            }
        if kind == 'fate_immediate_decision':
            decision = self.game.pending_fate_immediate_decision()
            if decision is None:
                raise RuntimeError('Fate immediate decision disappeared')
            decision['immediate_kind'] = decision['kind']
            decision['kind'] = 'fate_immediate_decision'
            decision['life_goals'] = self._life_goals_summary(with_text=True)
            decision['childhood_cards'] = self._childhood_summary()
            if decision['immediate_kind'] == 'fate_set_active':
                decision['candidate_cards'] = [
                    self._presented_card(cid)
                    for cid in decision['candidate_card_ids']]
            return decision
        if kind == 'maintenance_decision':
            plans = self._maintenance_plans(cache=True)
            decision = {
                'decision_id': self._decision_id(),
                'kind': kind,
                'life_goals': self._life_goals_summary(),
                'childhood_cards': self._childhood_summary(),
                            'active_fate': self._active_fate_view(),
                'legal_maintenance_plans': plans,
                'legal_actions': [{'plan_id': p['plan_id']} for p in plans],
            }
            hints = self._pending_rule_hints(kind)
            if hints:
                decision['rule_hints'] = hints
            return decision
        if kind in ('post_roll_decision', 'purchase_ready'):
            return self._post_roll_decision(kind)
        if kind == 'placement_decision':
            cid = self.game.pending_new[0]
            cls = CARDS[cid]['type']
            decision = {
                'decision_id': self._decision_id(),
                'kind': kind,
                'life_goals': self._life_goals_summary(),
                'childhood_cards': self._childhood_summary(),
                            'card_id': cid,
                'card': self._presented_card(cid),
                'cv_type': cls,
                'current_stack': list(self.game.cv[cls]),
                'legal_actions': [{'placement': 'top'},
                                  {'placement': 'bury'}],
            }
            hints = self._pending_rule_hints(kind)
            if hints:
                decision['rule_hints'] = hints
            return decision
        if kind == 'market_protection_decision':
            ye04 = self._market_protection_card()
            # _auto_advance() 已保证没有可用 YE-04 时会直接完成清理。
            if ye04 is None:
                raise RuntimeError('market protection decision has no YE-04')
            decision = {
                'decision_id': self._decision_id(),
                'kind': kind,
                'life_goals': self._life_goals_summary(),
                'childhood_cards': self._childhood_summary(),
                            'active_fate': self._active_fate_view(),
                'ye04': self._presented_card(ye04),
                'current_market': self._current_opportunities(),
                'system_elimination_count': max(
                    0, 3 - len(self.game.purchased_this_turn)),
                'legal_actions': ([{'choice': 'skip'}]
                                  + [{'choice': 'use', 'target_card_id': cid}
                                     for cid in self.game.market]),
            }
            hints = self._pending_rule_hints(kind)
            if hints:
                decision['rule_hints'] = hints
            return decision
        if kind == 'next_turn_ready':
            return {
                'decision_id': self._decision_id(),
                'kind': kind,
                'completed_turn': self._previous_turn_result['completed_turn'],
                'next_turn': self._previous_turn_result['next_turn'],
                'current_stage': self.game.stage,
                'market': self._current_opportunities(),
                'active_cards': [self._card_summary(cid)
                                 for cid in self.game.active_cards()],
                'event_hand': [self._card_summary(cid) for cid in self.game.hand
                               if CARDS[cid]['type'] == 'E'],
                'current_debuff': self._debuff_summary(),
                'debuff_history_count': len(self.game.debuff_history),
                'final_round_pending': self.game.final_pending,
                'candidates': [],
                'legal_actions': [],
            }
        if kind == 'final_flex_designation':
            flex_ids = flex_actives(self.game.cv)
            return {
                'decision_id': self._decision_id(),
                'kind': kind,
                'turn': self.game.turn,
                'life_goals': self._life_goals_summary(with_text=True),
                'active_flex_cards': [
                    {'card_id': cid,
                     'name': CARDS[cid]['name'],
                     'allowed': list(CARDS[cid]['flex'])}
                    for cid in flex_ids],
                # 单次提交覆盖全部 active flex 卡；不枚举笛卡尔积。
                'action_format': {
                    'designations': {cid: CARDS[cid]['flex'][0]
                                     for cid in flex_ids}},
                'candidates': [],
                'legal_actions': [],
            }
        if kind == 'game_over':
            return {
                'decision_id': self._decision_id(),
                'kind': kind,
                'scoring_ready': True,
                'score': full_score(self.game.cv, self.game.goals,
                                    **self.game.scoring_counts(),
                                    flex_designation=self._final_flex_designation),
                'completed_turn': self._previous_turn_result['completed_turn'],
                'previous_turn_result': copy.deepcopy(self._previous_turn_result),
                'candidates': [],
                'legal_actions': [],
            }
        return {
            'decision_id': self._decision_id(),
            'kind': kind,
            'candidates': [],
            'legal_actions': [],
        }

    def submit_action(self, decision_id, action):
        """校验并提交当前已实现的决策；失败时不改变正式游戏状态。"""
        current = self.current_decision()
        # hint 快照取自提交前状态：本次决策展示过什么，成功后就标记什么
        #（提交后候选/缓存可能已推进，不能再现）。
        displayed_hints = self._rule_hint_keys(current['kind'])
        if decision_id != current['decision_id']:
            return {'ok': False, 'error': 'stale_or_unknown_decision_id',
                    'decision': current}
        if current['kind'] in ('ready_for_adult_turn', 'next_turn_ready',
                               'initial_roll_resolved', 'game_over'):
            return {'ok': False, 'error': 'no_action_expected',
                    'decision': current}
        if current['kind'].startswith('childhood_pick'):
            if not isinstance(action, dict) or set(action) != {'card_id'}:
                return {'ok': False, 'error': 'invalid_action',
                        'decision': current}
            card_id = action['card_id']
            if {'card_id': card_id} not in current['legal_actions']:
                return {'ok': False, 'error': 'illegal_action',
                        'decision': current}
            if not self.game.submit_childhood_pick(card_id):
                return {'ok': False, 'error': 'illegal_action',
                        'decision': self.current_decision()}
            accepted = {'card_id': card_id}
        elif current['kind'] == 'pre_roll_c11':
            if (not isinstance(action, dict) or set(action) != {'choice'}
                    or action not in current['legal_actions']):
                return {'ok': False, 'error': 'illegal_action',
                        'decision': current}
            if action['choice'] == 'use' and not self.game.use_pre_roll_c11():
                return {'ok': False, 'error': 'illegal_action',
                        'decision': self.current_decision()}
            self._finish_first_roll()
            accepted = dict(action)
        elif current['kind'] == 'pre_roll_decision':
            allowed = {'flex_resource_choices', 'use_c11', 'use_ye05',
                       'use_me02'}
            if (not isinstance(action, dict) or not set(action) <= allowed
                    or not isinstance(action.get('flex_resource_choices',
                                                  {}), dict)):
                return {'ok': False, 'error': 'invalid_action',
                        'decision': current}
            # 未提交的开关默认 false、flex 默认空选择；正式规则
            # （窗口/持有/事件封锁）仍全部由 apply_pre_roll_declarations 校验。
            effects = self.game.apply_pre_roll_declarations(
                action.get('flex_resource_choices', {}),
                action.get('use_c11', False),
                action.get('use_ye05', False),
                action.get('use_me02', False))
            if effects is None:
                return {'ok': False, 'error': 'illegal_action',
                        'decision': current}
            self._pre_roll_effects_used = effects
            self._finish_first_roll()
            self._initial_roll_resolved = True
            accepted = copy.deepcopy(action)
        elif current['kind'] == 'purchase_ready':
            if not isinstance(action, dict):
                return {'ok': False, 'error': 'invalid_action',
                        'decision': current}
            if set(action) == {'plan_id'}:
                # 仅当前层公布过的 plan_id 可直接执行：第二层为选中目标的
                # 支付 plans；第一层为单支付目标携带的 plan_id。
                selected = next(
                    (p for p in self._purchase_plan_cache
                     if p['plan_id'] == action['plan_id']
                     and p['plan_id'] in self._published_plan_ids()), None)
                if selected is None:
                    return {'ok': False, 'error': 'illegal_plan_id',
                            'decision': current}
                if not self._execute_purchase_plan(selected):
                    return {'ok': False, 'error': 'plan_state_mismatch',
                            'decision': current}
                self._purchase_ready = False
                self._purchase_plan_cache = None
                self._purchase_target = None
                self._purchase_executed = True
                accepted = dict(action)
            elif set(action) == {'ordinary_card_ids', 'fate_card_id'}:
                target = self._match_purchase_target(action)
                if target is None:
                    return {'ok': False, 'error': 'illegal_action',
                            'decision': current}
                plans = self._purchase_target_groups()[target]
                if len(plans) == 1:
                    if not self._execute_purchase_plan(plans[0]):
                        return {'ok': False, 'error': 'plan_state_mismatch',
                                'decision': current}
                    self._purchase_ready = False
                    self._purchase_plan_cache = None
                    self._purchase_target = None
                    self._purchase_executed = True
                else:
                    # 多支付目标：进入第二层，只记录待选择目标，不消费窗口。
                    self._purchase_target = target
                accepted = dict(action)
            else:
                return {'ok': False, 'error': 'invalid_action',
                        'decision': current}
        elif current['kind'] == 'fate_immediate_decision':
            if (not isinstance(action, dict)
                    or action not in current['legal_actions']):
                return {'ok': False, 'error': 'illegal_action',
                        'decision': current}
            fate_card_id = self.game.pending_fate_immediate['fate_card_id']
            if not self.game.resolve_fate_immediate(decision_id, action):
                return {'ok': False, 'error': 'immediate_state_mismatch',
                        'decision': self.current_decision()}
            self._record_fate_resolved(fate_card_id)
            accepted = copy.deepcopy(action)
        elif current['kind'] == 'debuff_protection_decision':
            if not isinstance(action, dict):
                return {'ok': False, 'error': 'invalid_action',
                        'decision': current}
            options = self.game.debuff_cancel_options()
            if action == {'choice': 'skip'}:
                cancel_cid = None
            elif (action.get('choice') == 'use' and len(options) == 1
                    and set(action) == {'choice'}):
                # 单候选时兼容旧 bare use 协议；双候选必须显式指定来源
                cancel_cid = options[0]['cid']
            elif (set(action) == {'choice', 'source_card_id'}
                    and action.get('choice') == 'use'
                    and action in current['legal_actions']):
                cancel_cid = action['source_card_id']
            else:
                return {'ok': False, 'error': 'illegal_action',
                        'decision': current}
            outcome = self._resolve_runtime_debuff(cancel_cid=cancel_cid)
            if outcome is None:
                return {'ok': False, 'error': 'illegal_action',
                        'decision': current}
            self._debuff_resolved = True
            self._debuff_outcome = outcome
            # 仅显式选择的保护卡计入来源；skip/未触发/ME-02 事前保险不得伪造。
            self._debuff_cancelled_by = cancel_cid if outcome == 'cancelled' else None
            if outcome == 'drawn':
                self._debuff_drawn_card_id = self.game.current_debuff
            self._place_forced_cards()
            accepted = dict(action)
        elif current['kind'] == 'debuff_shorten_decision':
            if (not isinstance(action, dict)
                    or action not in current['legal_actions']):
                return {'ok': False, 'error': 'illegal_action',
                        'decision': current}
            if action['choice'] == 'skip':
                self._debuff_shorten_resolved = True
            else:  # pay
                option = self.game.debuff_shorten_option()
                if option is None or not self.game.apply_debuff_shorten(
                        option['cid']):
                    return {'ok': False, 'error': 'illegal_action',
                            'decision': current}
                self._debuff_shorten_resolved = True
            accepted = dict(action)
        elif current['kind'] == 'maintenance_decision':
            if not isinstance(action, dict) or set(action) != {'plan_id'}:
                return {'ok': False, 'error': 'invalid_action',
                        'decision': current}
            selected = next((p for p in self._maintenance_plan_cache
                             if p['plan_id'] == action['plan_id']), None)
            if selected is None:
                return {'ok': False, 'error': 'illegal_plan_id',
                        'decision': current}
            if self.game.apply_maintenance_plan(selected) is None:
                return {'ok': False, 'error': 'plan_state_mismatch',
                        'decision': current}
            self._maintenance_plan_cache = None
            self._maintenance_resolved = True
            accepted = dict(action)
        elif current['kind'] == 'mh04_decision':
            if (not isinstance(action, dict) or action not in current['legal_actions']):
                return {'ok': False, 'error': 'illegal_action',
                        'decision': current}
            if not self.game.resolve_mh04_reaction(
                    action['choice'] == 'use', action.get('target_index')):
                return {'ok': False, 'error': 'illegal_action',
                        'decision': self.current_decision()}
            accepted = dict(action)
        elif current['kind'] == 'market_protection_decision':
            if not isinstance(action, dict):
                return {'ok': False, 'error': 'invalid_action',
                        'decision': current}
            if action == {'choice': 'skip'}:
                result = self._cleanup_normal_market()
            elif (set(action) == {'choice', 'target_card_id'}
                    and action.get('choice') == 'use'
                    and action in current['legal_actions']):
                result = self._cleanup_normal_market(
                    protected=action['target_card_id'])
            else:
                return {'ok': False, 'error': 'illegal_action',
                        'decision': current}
            if result is None:
                return {'ok': False, 'error': 'market_state_mismatch',
                        'decision': current}
            self._market_cleanup_resolved = True
            accepted = dict(action)
        elif current['kind'] == 'placement_decision':
            if (not isinstance(action, dict)
                    or set(action) != {'placement'}
                    or action not in current['legal_actions']):
                return {'ok': False, 'error': 'illegal_action',
                        'decision': current}
            if not self.game.place_pending_card(
                    current['card_id'], action['placement']):
                return {'ok': False, 'error': 'illegal_action',
                        'decision': current}
            self._place_forced_cards()
            accepted = dict(action)
        elif current['kind'] == 'final_flex_designation':
            if not isinstance(action, dict) or set(action) != {'designations'}:
                return {'ok': False, 'error': 'invalid_action',
                        'decision': current}
            designations = action['designations']
            flex_ids = flex_actives(self.game.cv)
            if (not isinstance(designations, dict)
                    or set(designations) != set(flex_ids)
                    or any(designations.get(cid) not in CARDS[cid]['flex']
                           for cid in flex_ids)):
                return {'ok': False, 'error': 'illegal_action',
                        'decision': current}
            self._final_flex_designation = dict(designations)
            accepted = dict(action)
        else:  # post_roll_decision
            if not isinstance(action, dict) or 'choice' not in action:
                return {'ok': False, 'error': 'invalid_action',
                        'decision': current}
            if action.get('choice') == 'proceed_to_purchase':
                if set(action) not in ({'choice'}, {'choice', 'use_yk05'}):
                    return {'ok': False, 'error': 'invalid_action',
                            'decision': current}
                use_yk05 = action.get('use_yk05', False)
                if not isinstance(use_yk05, bool):
                    return {'ok': False, 'error': 'invalid_action',
                            'decision': current}
                if use_yk05 and not self.game.apply_yk05_conversion():
                    return {'ok': False, 'error': 'illegal_action',
                            'decision': current}
                self._enter_purchase_ready()
                accepted = dict(action)
            elif action.get('choice') in ('normal_reroll', 'reroll'):
                allowed = {'choice', 'indices', 'use_ye03', 'use_c11', 'c12_index'}
                if (set(action) - allowed or 'indices' not in action
                        or not isinstance(action['indices'], list)):
                    return {'ok': False, 'error': 'invalid_action',
                            'decision': current}
                use_c11 = action.get('use_c11', False)
                use_ye03 = action.get('use_ye03', False)
                c12_index = action.get('c12_index')
                if (not isinstance(use_c11, bool) or not isinstance(use_ye03, bool)
                        or (self._rerolls_remaining or 0) <= 0):
                    return {'ok': False, 'error': 'invalid_action',
                        'decision': current}
                if (use_c11 and 'C11' not in self.game.hand) or not self.game.normal_reroll_is_legal(
                        action['indices'], c12_index):
                    return {'ok': False, 'error': 'illegal_action',
                        'decision': current}
                if use_ye03 and not self.game.use_ye03():
                    return {'ok': False, 'error': 'illegal_action',
                            'decision': current}
                if use_ye03:
                    self._rerolls_remaining += 1
                c11_added = False
                if use_c11:
                    dice_count_before_c11 = len(self.game.dice)
                    if not self.game.use_reroll_c11():
                        return {'ok': False, 'error': 'illegal_action',
                                'decision': self.current_decision()}
                    c11_added = len(self.game.dice) > dice_count_before_c11
                rerolled = self.game.apply_normal_reroll(action['indices'], c12_index)
                if rerolled is False:
                    return {'ok': False, 'error': 'illegal_action',
                            'decision': self.current_decision()}
                if c11_added:
                    self._dice_roll_revision += 1
                if rerolled:
                    self._dice_roll_revision += 1
                    self.game.stats.run['reroll_rounds_used'] += 1
                    self.game.stats.run['dice_rerolled'] += rerolled
                    self._append_recent_event(
                        'rerolled', '重掷 %d 颗骰子' % rerolled,
                        {'reroll_count': rerolled})
                self._rerolls_remaining -= 1
                self._advance_after_dice_change()
                accepted = dict(action)
            elif action.get('choice') == 'special_reroll':
                allowed = {'choice', 'ability_card_id', 'die_index', 'use_ye03'}
                if set(action) - allowed or not all(k in action for k in
                        ('ability_card_id', 'die_index')) or not isinstance(
                            action.get('use_ye03', False), bool):
                    return {'ok': False, 'error': 'invalid_action',
                            'decision': current}
                if action.get('use_ye03', False):
                    option = next((item for item in self.game.available_special_rerolls()
                                   if item['card_id'] == action['ability_card_id']
                                   and action['die_index'] in item['target_indices']), None)
                    if option is None or not self.game.use_ye03():
                        return {'ok': False, 'error': 'illegal_action',
                                'decision': current}
                    self._rerolls_remaining += 1
                if not self.game.apply_special_reroll(
                        action['ability_card_id'], action['die_index']):
                    return {'ok': False, 'error': 'illegal_action',
                            'decision': current}
                self._dice_roll_revision += 1
                self._append_recent_event(
                    'rerolled', '重掷 1 颗骰子',
                    {'reroll_count': 1,
                     'ability_card_id': action['ability_card_id']})
                self._advance_after_dice_change()
                accepted = dict(action)
            else:
                return {'ok': False, 'error': 'illegal_action',
                        'decision': current}
        # 展示层 seen 状态只在动作被成功接受后更新；被拒提交不消费 hint。
        self.seen_rule_hints |= displayed_hints
        self._decision_revision += 1
        return {'ok': True, 'accepted_action': accepted,
                'decision': self.current_decision()}
