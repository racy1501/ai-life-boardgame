# -*- coding: utf-8 -*-
"""统计收集器：游戏内事件 -> 单局 row + 运行级聚合 + 单卡聚合。"""
from collections import Counter, defaultdict
from statistics import mean, median

from .cards import CARDS, DEBUFF_BY_ID, FATE_BY_ID
from . import scoring


def _new_card_stats():
    return {
        'exposure': 0, 'bought': 0, 'eliminated': 0,
        'dwell_sum': 0, 'dwell_n': 0, 'ability': Counter(),
        'flex_turns': 0,
        'upkeep_active': 0, 'upkeep_paid': 0, 'upkeep_failed': 0,
        'same_turn_death': 0, 'placed_top': 0, 'placed_bury': 0,
        'lost_misfortune': 0, 'lost_upkeep': 0,
    }


def _new_fate_stats():
    return {
        'exposure': 0, 'acquired': 0, 'active_turns': 0,
        'times_replaced': 0, 'final_active_count': 0, 'ability': Counter(),
    }


def _new_debuff_stats():
    return {
        'drawn': 0, 'active_turns': 0, 'duration_sum': 0,
        'duration_n': 0, 'ability': Counter(),
    }


def _percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    return values[round((len(values) - 1) * fraction)]


class Stats:
    def __init__(self):
        self.rows = []
        self.card_stats = defaultdict(_new_card_stats)
        self.fate_stats = defaultdict(_new_fate_stats)
        self.debuff_stats = defaultdict(_new_debuff_stats)
        self.goal_values = defaultdict(list)
        self.run = defaultdict(float)
        self.run.update({
            'dice_faces': Counter(), 'stable_by': Counter(),
            'elim_fixed': 0, 'elim_random': 0, 'shortfalls': 0,
            'ye04_used': 0, 'ye04_mattered': 0, 'quick_random_elim': 0,
            'reroll_rounds_used': 0, 'dice_rerolled': 0,
            'single_rerolls': 0, 'oh04_bl_rerolls': 0, 'abebe_used': 0,
            'mh04_converted': 0, 'yk05_used': 0, 'fallback_used': 0,
            'waste_normal': 0, 'waste_gl': 0,
            'dice_turns': 0, 'dice_n_sum': 0, 'stable_total': 0,
            'debuff_triggers': 0, 'debuff_cancelled': 0,
            'debuff_empty_triggers': 0, 'natural_expiry': 0,
            'replaced_early': 0, 'active_at_game_end': 0,
            'd08_trigger_mattered': 0,
            'fate_acquired_total': 0, 'fate_declined_turns': 0,
            'fate_market_discards': 0, 'fate_deck_reshuffles': 0,
            'real_BL_spent_on_fate': 0, 'GL_spent_on_fate': 0,
            'fate_payment_prevented_debuff': 0,
            'debuff_per_game': Counter(), 'fate_acquired_per_game': Counter(),
            'goal_drawn': Counter(), 'goal_final': Counter(),
            'goal_swapped_in': Counter(), 'goal_swapped_out': Counter(),
            'lg18_bottleneck': Counter(), 'final_active_combinations': Counter(),
            'cv_cooccurrence': Counter(),
        })
        self._reset_game()

    def _reset_game(self):
        self.game = {
            'turns': 0, 'stage_turns': Counter(), 'purchases': 0,
            'hist': Counter(), 'zero_no_legal': 0, 'zero_declined': 0,
            'buys_by_class': Counter(), 'gl_takes': 0, 'gl_take_value': 0.0,
            'losses_by_class': Counter(),
            'event_buys': Counter(), 'event_uses': Counter(),
            'childhood_uses': Counter(), 'turn_departures': [],
            'debuff_triggers': 0, 'debuff_cancelled': 0,
            'debuff_drawn': [], 'fate_acquired': [],
            'fate_windows': 0,
            'pre_debuff_cancel_declared': 0,
            'goal_swapped_in': Counter(), 'goal_swapped_out': Counter(),
        }

    def card(self, cid):
        return self.card_stats[cid]

    def fate(self, cid):
        return self.fate_stats[cid]

    def debuff(self, cid):
        return self.debuff_stats[cid]

    def start_game(self, game):
        self._reset_game()

    def finish_game(self, game):
        meta = game.scoring_counts()
        score = scoring.full_score(game.cv, game.goals, **meta)
        debuffs = list(game.debuff_history)
        if game.current_debuff:
            debuffs.append(game.current_debuff)
            self.run['active_at_game_end'] += 1
            ds = self.debuff(game.current_debuff)
            ds['duration_sum'] += game.debuff_active_turns
            ds['duration_n'] += 1
        if game.active_fate:
            self.fate(game.active_fate)['final_active_count'] += 1

        active_five = tuple(game.active(c) or '-' for c in 'HKRWP')
        event_count = game.event_acquired_count()
        row = {
            'turns': self.game['turns'],
            'stage_turns': dict(self.game['stage_turns']),
            'purchases': self.game['purchases'],
            'hist': {str(k): v for k, v in self.game['hist'].items()},
            'zero_no_legal': self.game['zero_no_legal'],
            'zero_declined': self.game['zero_declined'],
            'buys_by_class': dict(self.game['buys_by_class']),
            'gl_takes': self.game['gl_takes'],
            # 兼容旧字段名：v0.6 中 misfortunes 指达到 Debuff 阈值，不再删除 active。
            'misfortunes': self.game['debuff_triggers'],
            'losses_by_class': dict(self.game['losses_by_class']),
            'event_buys': dict(self.game['event_buys']),
            'event_uses': dict(self.game['event_uses']),
            'childhood_uses': dict(self.game['childhood_uses']),
            'hand_left': dict(Counter(game.hand)),
            'final_market': len(game.market),
            'debuff_triggers': self.game['debuff_triggers'],
            'debuff_cancelled': self.game['debuff_cancelled'],
            'debuff_history': debuffs,
            'debuff_count': len(debuffs),
            'current_debuff': game.current_debuff,
            'fate_acquired': list(self.game['fate_acquired']),
            'fate_count': len(game.fate_stack),
            'fate_windows': self.game['fate_windows'],
            'active_fate': game.active_fate,
            'event_acquired_count': event_count,
            'active_five': active_five,
            'score': {
                'total': score['total'], 'base_sum': score['base_sum'],
                'pvp': score['pvp'], 'debuff_vp': score['debuff_vp'],
                'lg_sum': score['lg_sum'], 'lg_ids': score['lg_ids'],
                'lg_scores': score['lg_scores'], 'counts': score['counts'],
            },
        }
        self.rows.append(row)

        self.run['games'] += 1
        self.run['turns_total'] += row['turns']
        for stage, n in row['stage_turns'].items():
            self.run['stage_turns_' + stage] += n
        for k, n in row['hist'].items():
            self.run['hist_' + k] += n
        self.run['zero_no_legal'] += row['zero_no_legal']
        self.run['zero_declined'] += row['zero_declined']
        self.run['purchases_total'] += row['purchases']
        self.run['gl_takes'] += row['gl_takes']
        self.run['gl_take_value'] += self.game['gl_take_value']
        self.run['misfortunes'] += row['misfortunes']
        self.run['score_total_sum'] += row['score']['total']
        self.run['base_sum_sum'] += row['score']['base_sum']
        self.run['pvp_sum'] += row['score']['pvp']
        self.run['debuff_vp_sum'] += row['score']['debuff_vp']
        self.run['lg_sum_sum'] += row['score']['lg_sum']
        self.run['debuff_experienced_total'] += row['debuff_count']
        self.run['fate_acquired_total'] += row['fate_count']
        self.run['fate_windows_total'] += row['fate_windows']
        self.run['debuff_per_game'][row['debuff_count']] += 1
        self.run['fate_acquired_per_game'][row['fate_count']] += 1
        for dep in self.game['turn_departures']:
            self.run['departures_' + str(dep)] += 1

        for goal in set(game.initial_goals):
            self.run['goal_drawn'][goal] += 1
        for goal, value in zip(game.goals, score['lg_scores']):
            self.run['goal_final'][goal] += 1
            self.goal_values[goal].append(value)
        self.run['goal_swapped_in'].update(self.game['goal_swapped_in'])
        self.run['goal_swapped_out'].update(self.game['goal_swapped_out'])
        if 18 in game.goals:
            values = {'fate': len(game.fate_stack), 'debuff': len(debuffs),
                      'event': event_count}
            bottleneck = min(values.values())
            for key, value in values.items():
                if value == bottleneck:
                    self.run['lg18_bottleneck'][key] += 1

        combo_key = '|'.join(active_five)
        self.run['final_active_combinations'][combo_key] += 1
        owned = sorted(cid for cls in 'HKRWP' for cid in game.cv[cls])
        for i in range(len(owned)):
            for j in range(i + 1, len(owned)):
                self.run['cv_cooccurrence'][owned[i] + '|' + owned[j]] += 1

    def card_table(self):
        out = {}
        for cid, cs in self.card_stats.items():
            d = {k: (dict(v) if isinstance(v, Counter) else v)
                 for k, v in cs.items()}
            d['name'] = CARDS[cid]['name']
            d['type'] = CARDS[cid]['type']
            d['stage'] = CARDS[cid]['stage']
            d['purchase_rate'] = round(cs['bought'] / cs['exposure'], 4) if cs['exposure'] else 0.0
            d['avg_dwell'] = round(cs['dwell_sum'] / cs['dwell_n'], 3) if cs['dwell_n'] else None
            out[cid] = d
        return out

    def fate_table(self):
        out = {}
        for cid in FATE_BY_ID:
            fs = self.fate_stats[cid]
            d = {k: (dict(v) if isinstance(v, Counter) else v)
                 for k, v in fs.items()}
            d['name'] = FATE_BY_ID[cid]['name']
            d['acquire_rate'] = round(fs['acquired'] / fs['exposure'], 4) if fs['exposure'] else 0.0
            out[cid] = d
        return out

    def debuff_table(self):
        out = {}
        for cid in DEBUFF_BY_ID:
            ds = self.debuff_stats[cid]
            d = {k: (dict(v) if isinstance(v, Counter) else v)
                 for k, v in ds.items()}
            d['name'] = DEBUFF_BY_ID[cid]['name']
            d['avg_actual_duration'] = round(ds['duration_sum'] / ds['duration_n'], 3) if ds['duration_n'] else None
            out[cid] = d
        return out

    def goal_table(self):
        out = {}
        for goal in range(1, 19):
            values = self.goal_values[goal]
            out[str(goal)] = {
                'drawn_games': self.run['goal_drawn'][goal],
                'final_games': self.run['goal_final'][goal],
                'swapped_in': self.run['goal_swapped_in'][goal],
                'swapped_out': self.run['goal_swapped_out'][goal],
                'score_mean': round(mean(values), 3) if values else None,
                'score_median': median(values) if values else None,
                'score_p10': _percentile(values, 0.10),
                'score_p90': _percentile(values, 0.90),
            }
        return out

    def run_summary(self):
        r = dict(self.run)
        for key in list(r):
            if isinstance(r[key], Counter):
                r[key] = dict(r[key])
        games = int(self.run.get('games', 0))
        combos = self.run['final_active_combinations']
        r['unique_final_active_combinations'] = len(combos)
        r['top_10_final_active_combinations'] = [
            {'active': key.split('|'), 'count': n,
             'share': round(n / games, 4) if games else 0.0}
            for key, n in combos.most_common(10)
        ]
        r['final_active_combination_concentration'] = (
            round(max(combos.values()) / games, 4) if games and combos else 0.0)
        r['top_cv_cooccurrence'] = [
            {'cards': key.split('|'), 'count': n}
            for key, n in self.run['cv_cooccurrence'].most_common(20)
        ]
        r['card_table'] = self.card_table()
        r['fate_table'] = self.fate_table()
        r['debuff_table'] = self.debuff_table()
        r['goal_table'] = self.goal_table()
        r['rows_digest'] = len(self.rows)
        return r
