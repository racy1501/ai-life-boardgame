# -*- coding: utf-8 -*-
"""最薄本地 Production Runtime 入口。

输出一行当前 decision JSON；读取一行 action JSON；输出 submit_action 结果。
本入口不解释规则、不选择策略，也不生成任何购买方案。
"""
import argparse
import json
import sys

from ailife.runtime import GameSession


def _json_line(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def build_parser():
    parser = argparse.ArgumentParser(description='AI人生桌游本地 Runtime 入口')
    parser.add_argument('--seed', type=int, default=None,
                        help='Runtime 随机种子')
    parser.add_argument('--goals', type=int, nargs=2, metavar=('GOAL_A', 'GOAL_B'),
                        help='可选的两个初始 Life Goal 编号')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    session = GameSession(seed=args.seed, forced_goals=args.goals)

    while True:
        decision = session.current_decision()
        print(_json_line(decision), flush=True)
        if decision.get('kind') == 'game_over':
            return 0

        # 某些阶段只会自动推进；入口不要求人工点击“继续”。
        # purchase_ready L1 的提交字段在 purchase_targets 条目上，
        # 无 legal_actions 也必须等待人工输入，不得视为可自动推进。
        if not decision.get('legal_actions') \
                and 'purchase_targets' not in decision:
            continue

        line = sys.stdin.readline()
        if line == '':
            return 0
        try:
            action = json.loads(line)
        except json.JSONDecodeError as exc:
            print(_json_line({
                'ok': False,
                'error': 'invalid_json',
                'message': str(exc),
            }), flush=True)
            continue

        result = session.submit_action(decision['decision_id'], action)
        print(_json_line(result), flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
