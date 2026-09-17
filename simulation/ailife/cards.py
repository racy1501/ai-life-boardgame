# -*- coding: utf-8 -*-
"""AI人生桌游 v0.6 —— 完整卡牌数据（纯数据，数据驱动，无逻辑）。

基础 CV / Event / 童年牌来源：《AI人生桌游-完整卡牌表-v0.5.md》；
Debuff / Fate / LG17-LG18 为 v0.6 新增。
符号：H=健康 K=知识 R=关系 M=金钱 GL=好运 BL=厄运
普通资源 = H/K/R/M；GL/BL 为骰制特殊符号。

effect primitive 字段约定：
  provide        : [(sym, n), ...]  稳定产出（计入 LG13/LG16）
  flex           : (sym, ...)       每回合开始任选一种稳定提供 ×1（终局统一指定）
  upkeep         : {sym: n}         持续成本（仅 active 时支付）
  sub_buy        : [{'from','to','scope'}]  购买支付替代（每次购买每条限一次）
  convert_turn   : {'from','to'}    每回合一次资源转换（YK-05）
  extra_die      : int              额外骰（受上限 7 约束）
  reroll         : {'filter': 'non_bl'|'bl'|'non_gl_bl'}  每回合一次单骰重掷
  bl_convert     : True             MH-04：第2个厄运出现时改 1 厄运为健康
  cancel_debuff_once : True         每局一次取消 Debuff 触发
  shorten_debuff : {'cost','reduce'} 支付资源缩短新 Debuff
  upkeep_discount: {'sym','reduce'} MH-01：付持续成本时该符号需求 -1（若成本包含）
  upkeep_sub     : {'from','to'}    OH-05/OR-02：付持续成本时 1 符号替代另 1 符号
  temp_res       : (sym, n)         Event/童年：本次结算临时资源
  temp_gl        : int              童年：本次购买结算临时 GL
  temp_dice      : int              临时额外骰
  extra_reroll_rounds : int         本回合额外 1 轮正常重掷
  protect_market : True             市场保护
  upkeep_reduce  : True             ME-01 持续成本临时减免
  pre_cancel_debuff : True          事前取消本回合 Debuff 触发
  cancel_debuff  : True             达到阈值后取消一次 Debuff 触发
  discount_type  : 'H'/'K'/'R'/'P'  童年类别购买折扣
  abebe          : True             C12 特殊童年物
"""

NORMAL = ('H', 'K', 'R', 'M')
SYMS = ('H', 'K', 'R', 'M', 'GL', 'BL')


def _mk(cid, name, ctype, stage=None, cost=None, vp=0, **kw):
    d = {'id': cid, 'name': name, 'type': ctype, 'stage': stage,
         'cost': cost or {}, 'vp': vp}
    d.update(kw)
    return d


CV = [
    # ---------------- 青年 H ----------------
    _mk('YH-01', '邻里健康互助', 'H', 'youth', {'H': 1, 'R': 1},
        provide=[('H', 1), ('R', 1)]),
    _mk('YH-02', '规律作息', 'H', 'youth', {'H': 1, 'K': 1}, extra_die=1),
    _mk('YH-03', '周末球局', 'H', 'youth', {'H': 1, 'R': 1}, provide=[('H', 1), ('R', 1)]),
    _mk('YH-04', '健康饮食习惯', 'H', 'youth', {'H': 1, 'M': 1}, provide=[('H', 1)]),
    # ---------------- 青年 K ----------------
    _mk('YK-01', '职业技能证书', 'K', 'youth', {'K': 1, 'M': 1}, provide=[('K', 1)]),
    _mk('YK-02', '线上课程订阅', 'K', 'youth', {'K': 1, 'M': 1},
        sub_buy=[{'from': 'M', 'to': 'K', 'scope': None}]),
    _mk('YK-03', '外语学习', 'K', 'youth', {'K': 2}, provide=[('K', 1), ('R', 1)]),
    _mk('YK-04', '生活预算', 'K', 'youth', {'K': 1, 'M': 1},
        sub_buy=[{'from': 'K', 'to': 'M', 'scope': ('P',)}]),
    _mk('YK-05', '学习方法', 'K', 'youth', {'K': 1, 'H': 1}, provide=[('K', 1)],
        convert_turn={'from': 'R', 'to': 'K'}),
    # ---------------- 青年 R ----------------
    _mk('YR-01', '老友圈', 'R', 'youth', {'R': 2}, provide=[('R', 1)]),
    _mk('YR-02', '兴趣社群', 'R', 'youth', {'R': 1, 'H': 1}, provide=[('GL', 1)]),
    _mk('YR-03', '稳定交往', 'R', 'youth', {'R': 1, 'GL': 1}, provide=[('R', 1), ('GL', 1)]),
    _mk('YR-04', '共同生活', 'R', 'youth', {'R': 2, 'M': 1}, provide=[('R', 1), ('M', 1)]),
    _mk('YR-05', '育儿生活', 'R', 'youth', {'R': 1, 'M': 1}, provide=[('R', 1)], upkeep={'M': 1}),
    # ---------------- 青年 W ----------------
    _mk('YW-01', '项目助理', 'W', 'youth', {'K': 2}, provide=[('M', 1), ('K', 1)]),
    _mk('YW-02', '夜班兼职', 'W', 'youth', {'H': 1, 'R': 1}, provide=[('M', 2)], upkeep={'H': 1}),
    _mk('YW-03', '活动执行', 'W', 'youth', {'R': 2}, provide=[('M', 1), ('R', 1)]),
    _mk('YW-04', '户外领队', 'W', 'youth', {'H': 2}, provide=[('M', 1), ('H', 1)]),
    # ---------------- 青年 P ----------------
    _mk('YP-01', '二手相机', 'P', 'youth', {'M': 2}, vp=2),
    _mk('YP-02', '第一台个人电脑', 'P', 'youth', {'M': 2, 'K': 1}, vp=1, provide=[('K', 1)]),
    _mk('YP-03', '露营装备', 'P', 'youth', {'M': 2, 'H': 1}, vp=2),
    # ---------------- 中年 H ----------------
    _mk('MH-01', '恢复训练', 'H', 'middle', {'H': 2, 'K': 1}, provide=[('H', 1)],
        upkeep_discount={'sym': 'H', 'reduce': 1}),
    _mk('MH-02', '年度体检', 'H', 'middle', {'H': 2, 'M': 1}, provide=[('H', 1)],
        cancel_debuff_once=True),
    _mk('MH-03', '力量训练', 'H', 'middle', {'H': 2, 'M': 1}, provide=[('H', 1)]),
    _mk('MH-04', '压力管理', 'H', 'middle', {'H': 1, 'K': 2}, bl_convert=True),
    # ---------------- 中年 K ----------------
    _mk('MK-01', '专业进修', 'K', 'middle', {'K': 2, 'M': 1}, provide=[('K', 1)],
        sub_buy=[{'from': 'H', 'to': 'K', 'scope': None}, {'from': 'R', 'to': 'K', 'scope': None}]),
    _mk('MK-02', '工作流程优化', 'K', 'middle', {'K': 2, 'R': 1}, provide=[('K', 1)],
        reroll={'filter': 'non_bl'}),
    _mk('MK-03', '跨领域能力', 'K', 'middle', {'K': 2, 'R': 1}, provide=[('K', 1)],
        sub_buy=[{'from': 'H', 'to': 'R', 'scope': None}, {'from': 'R', 'to': 'H', 'scope': None}]),
    _mk('MK-04', '多元技能组合', 'K', 'middle', {'K': 3}, flex=('K', 'R', 'M')),
    # ---------------- 中年 R ----------------
    _mk('MR-01', '长期伴侣', 'R', 'middle', {'R': 2, 'M': 1}, provide=[('R', 1), ('GL', 1)],
        upkeep={'M': 1}),
    _mk('MR-02', '照护家人', 'R', 'middle', {'R': 2, 'H': 1}, provide=[('R', 1), ('H', 1)]),
    _mk('MR-03', '多年好友', 'R', 'middle', {'R': 2}, provide=[('GL', 1)]),
    _mk('MR-04', '可靠人脉', 'R', 'middle', {'R': 2, 'K': 1},
        sub_buy=[{'from': 'R', 'to': 'M', 'scope': ('W', 'P')}]),
    # ---------------- 中年 W ----------------
    _mk('MW-01', '专业骨干', 'W', 'middle', {'K': 2, 'M': 1}, provide=[('M', 2), ('K', 1)]),
    _mk('MW-02', '项目负责人', 'W', 'middle', {'K': 1, 'R': 2}, provide=[('M', 2), ('R', 1)],
        upkeep={'H': 1}),
    _mk('MW-03', '独立接案', 'W', 'middle', {'K': 1, 'R': 2}, provide=[('M', 2)], flex=('K', 'R')),
    _mk('MW-04', '高压高薪岗位', 'W', 'middle', {'K': 2, 'H': 1}, provide=[('M', 3)],
        upkeep={'H': 1}),
    _mk('MW-05', '职业转型', 'W', 'middle', {'K': 2, 'R': 1}, provide=[('M', 1)],
        flex=('H', 'K', 'R')),
    # ---------------- 中年 P ----------------
    _mk('MP-01', '长期投资账户', 'P', 'middle', {'M': 3, 'K': 1}, vp=1, provide=[('M', 1)]),
    _mk('MP-02', '改善型住房', 'P', 'middle', {'M': 4}, vp=4),
    _mk('MP-03', '家庭汽车', 'P', 'middle', {'M': 3}, vp=3),
    _mk('MP-04', '独立工作室', 'P', 'middle', {'M': 3, 'K': 1}, vp=1, provide=[('K', 1)]),
    # ---------------- 老年 H ----------------
    _mk('OH-01', '长期健康管理', 'H', 'elder', {'H': 2, 'K': 2}, provide=[('H', 1)],
        shorten_debuff={'cost': {'H': 1}, 'reduce': 1}),
    _mk('OH-02', '精力分配', 'H', 'elder', {'H': 2, 'R': 1},
        sub_buy=[{'from': 'H', 'to': 'K', 'scope': None}, {'from': 'H', 'to': 'R', 'scope': None}]),
    _mk('OH-03', '低强度坚持', 'H', 'elder', {'H': 2}, provide=[('H', 1)]),
    _mk('OH-04', '风险预防', 'H', 'elder', {'H': 2, 'K': 1}, reroll={'filter': 'bl'}),
    _mk('OH-05', '留有余量', 'H', 'elder', {'H': 2, 'R': 1}, provide=[('H', 1)],
        upkeep_sub={'from': 'H', 'to': 'any'}),
    # ---------------- 老年 K ----------------
    _mk('OK-01', '经验迁移', 'K', 'elder', {'K': 3}, provide=[('K', 1)],
        sub_buy=[{'from': 'K', 'to': 'H', 'scope': None}, {'from': 'K', 'to': 'R', 'scope': None},
                 {'from': 'K', 'to': 'M', 'scope': None}]),
    _mk('OK-02', '融会贯通', 'K', 'elder', {'K': 2, 'R': 1, 'M': 1}, flex=('H', 'K', 'R', 'M')),
    _mk('OK-03', '经验判断', 'K', 'elder', {'K': 2, 'H': 1}, reroll={'filter': 'non_gl_bl'}),
    # ---------------- 老年 R ----------------
    _mk('OR-01', '彼此照应', 'R', 'elder', {'R': 2, 'H': 1}, provide=[('R', 1), ('GL', 1)]),
    _mk('OR-02', '家人常伴', 'R', 'elder', {'R': 2, 'M': 1}, provide=[('R', 1)],
        upkeep_sub={'from': 'R', 'to': ('H', 'M')}),
    _mk('OR-03', '三五知己', 'R', 'elder', {'R': 2}, provide=[('GL', 1)]),
    _mk('OR-04', '社区参与', 'R', 'elder', {'R': 2, 'K': 1}, provide=[('R', 1), ('K', 1)]),
    # ---------------- 老年 W ----------------
    _mk('OW-01', '资深顾问', 'W', 'elder', {'K': 2, 'R': 2}, provide=[('M', 3)], flex=('K', 'R')),
    _mk('OW-02', '资深从业者', 'W', 'elder', {'K': 2, 'H': 1, 'R': 1}, provide=[('M', 3), ('K', 1)]),
    _mk('OW-03', '个人品牌', 'W', 'elder', {'K': 2, 'R': 2}, provide=[('M', 2), ('R', 1)]),
    _mk('OW-04', '减少工时', 'W', 'elder', {'H': 2, 'K': 1}, provide=[('M', 1), ('H', 1)]),
    # ---------------- 老年 P ----------------
    _mk('OP-01', '安居居所', 'P', 'elder', {'M': 4, 'R': 1}, vp=5),
    _mk('OP-02', '收益型资产组合', 'P', 'elder', {'M': 4, 'K': 1}, vp=2, provide=[('M', 1)]),
    _mk('OP-03', '长期收藏', 'P', 'elder', {'M': 4}, vp=4),
    _mk('OP-04', '专业级兴趣设备', 'P', 'elder', {'M': 3, 'K': 1}, vp=4),
    _mk('OP-05', '无障碍居家改造', 'P', 'elder', {'M': 3, 'H': 1}, vp=2, provide=[('H', 1)]),
]

EVENTS = [
    _mk('YE-01', '短期项目', 'E', 'youth', {'M': 1}, temp_res=('M', 2)),
    _mk('YE-02', '临时搭把手', 'E', 'youth', {'R': 1}, temp_res=('R', 2)),
    _mk('YE-03', '再想一下', 'E', 'youth', {'K': 1},
        extra_reroll_rounds=1),
    _mk('YE-04', '保留机会', 'E', 'youth', {'GL': 1}, protect_market=True),
    _mk('YE-05', '调整安排', 'E', 'youth', {'H': 1}, temp_dice=1),
    _mk('ME-01', '应急周转', 'E', 'middle', {'K': 1}, upkeep_reduce=True),
    _mk('ME-02', '风险预案', 'E', 'middle', {'M': 1}, pre_cancel_debuff=True),
]

CHILDHOOD = [
    _mk('C01', '零钱罐', 'C', None, {}, temp_res=('M', 1)),
    _mk('C02', '幸运贴纸', 'C', None, {}, temp_gl=1),
    _mk('C03', '收藏册', 'C', None, {}, discount_type='P'),
    _mk('C04', '一起回家', 'C', None, {}, temp_res=('R', 1)),
    _mk('C05', '再玩五分钟', 'C', None, {}, temp_dice=1),
    _mk('C06', '再来一次', 'C', None, {}, extra_reroll_rounds=1),
    _mk('C07', '橡皮擦', 'C', None, {}, reroll={'filter': 'non_bl'}),
    _mk('C08', '紧急联系人', 'C', None, {}, cancel_debuff=True),
    _mk('C09', '今天请假', 'C', None, {}, pre_cancel_debuff=True),
    _mk('C10', '先别收走它', 'C', None, {}, protect_market=True),
    _mk('C11', '借来的笔记', 'C', None, {}, temp_res=('K', 1)),
    _mk('C12', '阿贝贝', 'C', None, {}, abebe=True),
]

DEBUFFS = [
    _mk('D01', '体检指标异常', 'D', extra_cost={'type': 'H', 'sym': 'H', 'n': 1}),
    _mk('D02', '信用受损', 'D', extra_cost={'type': 'P', 'sym': 'M', 'n': 1}),
    _mk('D03', '注意力涣散', 'D', extra_cost={'type': 'K', 'sym': 'K', 'n': 1}),
    _mk('D04', '社交倦怠', 'D', extra_cost={'type': 'R', 'sym': 'R', 'n': 1}),
    _mk('D05', '过劳状态', 'D', block_event=True),
    _mk('D06', '睡眠不足', 'D', reroll_delta=-1),
    _mk('D07', '悲观预期', 'D', gl_threshold=4),
    _mk('D08', '诸事不顺', 'D', virtual_bl=1),
    _mk('D09', '社交回避', 'D', block_type='R'),
    _mk('D10', '学习停滞', 'D', block_type='K'),
    _mk('D11', '预算冻结', 'D', block_type='P'),
    _mk('D12', '体脂率过高', 'D', block_type='H'),
]

FATES = [
    _mk('F01', '重新规划', 'F', cost={'GL': 1}, immediate='set_active'),
    _mk('F02', '夜间高效', 'F', cost={'GL': 1}, reroll_delta=1),
    _mk('F03', '目标重估', 'F', cost={'GL': 2}, immediate='replace_goal'),
    _mk('F04', '职业顺风期', 'F', cost={'GL': 2},
        first_discount={'type': 'W', 'sym': 'any', 'n': 1}),
    _mk('F05', '生活松绑', 'F', cost={'GL': 2}, upkeep_reduce=1),
    _mk('F06', '消费窗口期', 'F', cost={'GL': 1, 'BL': 1},
        first_discount={'type': 'P', 'sym': 'M', 'n': 1}),
    _mk('F07', '低能量期', 'F', cost={'BL': 1}, purchase_limit=1,
        first_discount={'type': None, 'sym': 'any', 'n': 2}),
    _mk('F08', '重心转移', 'F', cost={'BL': 1},
        extra_cost={'type': 'H', 'sym': 'H', 'n': 1},
        discount={'type': 'K', 'sym': 'K', 'n': 1}),
    _mk('F09', '专注模式', 'F', cost={'BL': 1}, block_event=True, dice_delta=1),
    _mk('F10', '不靠运气', 'F', cost={'BL': 2}, wildcard_normal=True),
    _mk('F11', '睡眠紊乱', 'F', cost={'BL': 2}, virtual_bl=1, reroll_delta=2),
    _mk('F12', '高压状态', 'F', cost={'BL': 2}, dice_delta=2, lock_reroll_on_bl=True),
]

DEBUFF_BY_ID = {c['id']: c for c in DEBUFFS}
FATE_BY_ID = {c['id']: c for c in FATES}

CARDS = {c['id']: c for c in CV + EVENTS + CHILDHOOD + DEBUFFS + FATES}

STAGE_ORDER = ('youth', 'middle', 'elder')
STAGE_CV = {s: [c['id'] for c in CV if c['stage'] == s] for s in STAGE_ORDER}
STAGE_EVENTS = {s: [c['id'] for c in EVENTS if c['stage'] == s] for s in STAGE_ORDER}
# 阶段牌库（CV + Event 共用市场）：youth 26 / middle 23 / elder 21
STAGE_DECKS = {s: STAGE_CV[s] + STAGE_EVENTS[s] for s in STAGE_ORDER}

LG_NAMES = {
    1: '身强体健', 2: '求知不止', 3: '情谊长存', 4: '事业有成', 5: '丰衣足食',
    6: '稳步积累', 7: '学以致用', 8: '安居乐业', 9: '张弛有度', 10: '人生广度',
    11: '一技之长', 12: '家庭美满', 13: '自由人生', 14: '安稳度日', 15: '协作成长',
    16: '财务自由', 17: '计划赶不上变化', 18: '来都来了',
}
