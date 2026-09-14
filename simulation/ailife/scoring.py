# -*- coding: utf-8 -*-
"""终局计分：Curve A（H/K/R）+ 财产卡面分 + Life Goal 公式。

Life Goal 公式的唯一事实源是下方 LG_RULES 结构化规则表：lg_score 按表解释
计分，Runtime 直接展示同一张表，展示规则与正式计分不会漂移。

Curve A 基线（正式 0..13 张，v0.6 同步版锁定）：
张数 0..13 -> 0,1,3,5,8,11,15,19,24,29,35,41,48,55
超过 13 张的部分按每张 +6 线性外推（simulation assumption SA-CURVE，
从正式端点 13=55 向后接；牌池单类最多 13 张，外推仅为防御性兜底）。
"""
from .cards import CARDS, NORMAL

CURVE_A = [0, 1, 3, 5, 8, 11, 15, 19, 24, 29, 35, 41, 48, 55]


def curve_a(n):
    if n <= 13:
        return CURVE_A[n]
    return CURVE_A[13] + 6 * (n - 13)  # SA-CURVE：n>13 外推，非正式锁定值


def cv_counts(cv):
    return {c: len(cv[c]) for c in 'HKRWP'}


def possession_vp(cv):
    return sum(CARDS[cid]['vp'] for cid in cv['P'])


def active_provides(cv, flex_designation):
    """终局当前 active 的稳定产出列表 [(sym, n)]，flex 卡按终局指定。"""
    out = []
    for cls in 'HKRWP':
        if not cv[cls]:
            continue
        cid = cv[cls][-1]
        c = CARDS[cid]
        out.extend(c.get('provide', []))
        if c.get('flex'):
            out.append((flex_designation.get(cid, c['flex'][0]), 1))
    return out


# Life Goal 计分事实源：结构化规则表，覆盖全部 18 张 Goal。
# lg_score 只按此表解释计分；Runtime 展示同一结构，不存在第二套公式。
LG_RULES = {
    1: {'kind': 'cv_count', 'class': 'H'},
    2: {'kind': 'cv_count', 'class': 'K'},
    3: {'kind': 'cv_count', 'class': 'R'},
    4: {'kind': 'cv_count', 'class': 'W', 'mult': 2},
    5: {'kind': 'possession_share', 'divisor': 3},
    6: {'kind': 'cv_min', 'classes': ['H', 'K'], 'mult': 2},
    7: {'kind': 'cv_min', 'classes': ['K', 'W'], 'mult': 2},
    8: {'kind': 'cv_min', 'classes': ['P', 'W'], 'mult': 2},
    9: {'kind': 'cv_min', 'classes': ['W', 'H', 'R'], 'mult': 4},
    10: {'kind': 'diverse_classes', 'min_count': 2, 'mult': 2},
    11: {'kind': 'cv_max', 'classes': ['H', 'K', 'R']},
    12: {'kind': 'cv_min', 'classes': ['R', 'P'], 'mult': 2},
    13: {'kind': 'provide_variety', 'mult': 2},
    14: {'kind': 'cv_min', 'classes': ['H', 'P'], 'mult': 2},
    15: {'kind': 'cv_min', 'classes': ['K', 'R'], 'mult': 2},
    16: {'kind': 'provide_amount', 'symbol': 'M', 'mult': 2},
    17: {'kind': 'fate_pairs', 'per_pair': 3},
    18: {'kind': 'fate_debuff_event_min', 'mult': 4},
}


_SYM_NAMES = {'H': '健康', 'K': '知识', 'R': '关系', 'W': '工作',
              'P': '财产'}


def _cls_names(classes):
    return '、'.join('%s(%s)' % (_SYM_NAMES[c], c) for c in classes)


def lg_scoring_text(rule):
    """LG_RULES 条目 → 一句话计分口径。

    与 lg_score 同表解释的唯一展示层：措辞只重述该条目的 kind/参数，
    不引入第二套公式；口径细节（cv_min 计全部持卡、provide_amount 只读
    终局 active 顶卡产出）与 lg_score / cv_counts / active_provides 一致。
    """
    kind = rule['kind']
    mult = rule.get('mult', 1)
    if kind == 'cv_count':
        return ('计分 = 终局持有%s类履历的全部张数 ×%d；张数按全部持有卡计，'
                '含被顶替/埋藏的历史卡' % (_SYM_NAMES[rule['class']], mult))
    if kind == 'cv_min':
        return ('计分 = %d × 各类持有张数的最小值（%s）；张数按全部持有卡计，'
                '含被顶替/埋藏的历史卡' % (mult, _cls_names(rule['classes'])))
    if kind == 'cv_max':
        return '计分 = %s 中持有张数最多一类的张数' % _cls_names(rule['classes'])
    if kind == 'possession_share':
        return ('计分 = 终局全部持有财产卡 vp 总和 ÷ %d（向下取整）；'
                '持有即计，无需 active' % rule['divisor'])
    if kind == 'diverse_classes':
        return ('计分 = %d × 持有至少 %d 张的履历类别数（H/K/R/W/P 五类）'
                % (mult, rule['min_count']))
    if kind == 'provide_variety':
        return ('计分 = %d × 终局生效卡（各类栈顶）稳定产出的不同普通资源'
                '符号数；只读 active 卡' % mult)
    if kind == 'provide_amount':
        return ('计分 = %d × 终局生效卡（各类栈顶）稳定产出中 %s 的总点数；'
                '只读 active 卡的 provide，资源库存不计' % (mult, rule['symbol']))
    if kind == 'fate_pairs':
        return '计分 = %d × (取得的 Fate 牌数 ÷ 2，向下取整)' % rule['per_pair']
    if kind == 'fate_debuff_event_min':
        return ('计分 = %d × min(Fate 牌数, Debuff 经历数, 事件牌取得数)'
                % mult)
    raise ValueError(rule['kind'])


def lg_score(lg, counts, pvp, provides, fate_count=0, debuff_count=0,
             event_count=0):
    """单张 Life Goal 得分；公式唯一来源是 LG_RULES。

    provides = 终局 active 稳定产出（含终局指定）。
    """
    if lg not in LG_RULES:
        raise ValueError(lg)
    rule = LG_RULES[lg]
    kind = rule['kind']
    if kind == 'cv_count':
        return rule.get('mult', 1) * counts[rule['class']]
    if kind == 'cv_min':
        return rule.get('mult', 1) * min(counts[c] for c in rule['classes'])
    if kind == 'cv_max':
        return max(counts[c] for c in rule['classes'])
    if kind == 'possession_share':
        return pvp // rule['divisor']
    if kind == 'diverse_classes':
        return rule['mult'] * sum(
            1 for c in 'HKRWP' if counts[c] >= rule['min_count'])
    if kind == 'provide_variety':
        return rule['mult'] * len({s for s, _ in provides if s in NORMAL})
    if kind == 'provide_amount':
        return rule['mult'] * sum(n for s, n in provides
                                  if s == rule['symbol'])
    if kind == 'fate_pairs':
        return rule['per_pair'] * (fate_count // 2)
    if kind == 'fate_debuff_event_min':
        return rule['mult'] * min(fate_count, debuff_count, event_count)
    raise ValueError(rule['kind'])


def flex_actives(cv):
    """当前 active 中的 flex 卡（终局需要统一指定）。"""
    out = []
    for cls in 'HKRWP':
        if cv[cls]:
            cid = cv[cls][-1]
            if CARDS[cid].get('flex'):
                out.append(cid)
    return out


def best_designation(cv, goals, fate_count=0, debuff_count=0, event_count=0):
    """终局统一指定：枚举 flex active 的指定组合，最大化两张 Goal 合计分。

    指定对所有 Goal 同时生效（v0.5 §14.5）。这是后台求最优的 oracle 式结算，
    对 heuristic strategy 一视同仁。
    """
    flex = flex_actives(cv)
    if not flex:
        return {}, active_provides(cv, {})

    best = None
    def rec(i, desig):
        nonlocal best
        if i == len(flex):
            provides = active_provides(cv, desig)
            sc = sum(lg_score(g, cv_counts(cv), possession_vp(cv), provides,
                              fate_count, debuff_count, event_count)
                     for g in goals)
            if best is None or sc > best[0]:
                best = (sc, dict(desig), provides)
            return
        cid = flex[i]
        for sym in CARDS[cid]['flex']:
            desig[cid] = sym
            rec(i + 1, desig)
            del desig[cid]

    rec(0, {})
    return best[1], best[2]


def full_score(cv, goals, fate_count=0, debuff_count=0, event_count=0,
               flex_designation=None):
    """终局完整计分。返回分项 dict。

    flex_designation 显式给出时按该指定结算（Production Runtime 的终局
    最终指定）；None 时回退 best_designation oracle（Simulator 路径不变）。
    """
    counts = cv_counts(cv)
    pvp = possession_vp(cv)
    if flex_designation is None:
        desig, provides = best_designation(
            cv, goals, fate_count, debuff_count, event_count)
    else:
        desig = dict(flex_designation)
        provides = active_provides(cv, desig)
    lg = [lg_score(g, counts, pvp, provides, fate_count, debuff_count,
                   event_count) for g in goals]
    base = {'H': curve_a(counts['H']), 'K': curve_a(counts['K']),
            'R': curve_a(counts['R'])}
    return {
        'counts': counts, 'pvp': pvp, 'base': base,
        'base_sum': sum(base.values()),
        'lg_ids': list(goals), 'lg_scores': lg, 'lg_sum': sum(lg),
        'debuff_vp': debuff_count,
        'total': sum(base.values()) + pvp + sum(lg) + debuff_count,
        'designation': desig, 'provides': provides,
    }
