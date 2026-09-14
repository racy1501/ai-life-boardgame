# -*- coding: utf-8 -*-
"""AI人生桌游 模拟器批量运行入口。

用法：
  python run_simulation.py --config V06 --games 1000 --seed 1
  python run_simulation.py --config V06 --games 20 --seed 1 --output-name V06-smoke
输出：
  results/<config>/rows_<strategy>.jsonl   单局原始记录
  results/<config>/summary.json            聚合（含单卡表）
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ailife.engine import Game, CONFIGS
from ailife.strategies import STRATEGIES
from ailife.stats import Stats

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, 'results')

import random


def run_config(config_name, games, base_seed, strategies=None, quiet=False,
               output_name=None):
    cfg = CONFIGS[config_name]
    strat_names = strategies or list(STRATEGIES)
    out_dir = os.path.join(RESULTS, output_name or config_name)
    os.makedirs(out_dir, exist_ok=True)
    total_stats = {}
    t0 = time.time()
    for sname in strat_names:
        strat_cls = STRATEGIES[sname]
        stats = Stats()
        for i in range(games):
            seed = base_seed * 100003 + i  # 同 index 跨配置/策略共用种子（配对对比）
            rng = random.Random(seed)
            game = Game(cfg, strat_cls, rng, stats=stats)
            game.run()
        rows_path = os.path.join(out_dir, 'rows_%s.jsonl' % sname)
        with open(rows_path, 'w', encoding='utf-8') as f:
            for row in stats.rows:
                f.write(json.dumps(row, ensure_ascii=False) + '\n')
        total_stats[sname] = stats.run_summary()
        if not quiet:
            tot = [r['score']['total'] for r in stats.rows]
            print('  %-14s games=%d mean_total=%.2f turns=%.1f' % (
                sname, len(stats.rows), sum(tot) / len(tot),
                sum(r['turns'] for r in stats.rows) / len(stats.rows)))
    summary = {
        'config': config_name,
        'upkeep_immediate': cfg.upkeep_immediate,
        'fallback': cfg.fallback,
        'debuff': cfg.debuff,
        'fate': cfg.fate,
        'life_goals': cfg.life_goals,
        'games_per_strategy': games,
        'base_seed': base_seed,
        'strategies': total_stats,
        'elapsed_sec': round(time.time() - t0, 2),
    }
    with open(os.path.join(out_dir, 'summary.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default='BASE-A',
                    help='BASE-A / BASE-B / TEST-A / TEST-B / V06 / all')
    ap.add_argument('--games', type=int, default=1000)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--strategies', default=None,
                    help='逗号分隔，默认全部 5 个')
    ap.add_argument('--output-name', default=None,
                    help='结果子目录名；smoke 可用 V06-smoke，默认等于 config')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args()

    strats = args.strategies.split(',') if args.strategies else None
    names = list(CONFIGS) if args.config == 'all' else [args.config]
    t0 = time.time()
    for name in names:
        print('[%s] games=%d/strategy seed=%d' % (name, args.games, args.seed))
        output_name = args.output_name if len(names) == 1 else None
        run_config(name, args.games, args.seed, strats, args.quiet, output_name)
    print('total elapsed: %.1fs' % (time.time() - t0))


if __name__ == '__main__':
    main()
