# -*- coding: utf-8 -*-
import json
import os
import subprocess
import sys
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestRuntimeCli(unittest.TestCase):
    def test_starts_and_accepts_one_legal_action(self):
        process = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, 'run_runtime.py'),
             '--seed', '1', '--goals', '1', '2'],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            first = json.loads(process.stdout.readline())
            self.assertEqual(first['kind'], 'childhood_pick_1')
            action = {'card_id': first['legal_actions'][0]['card_id']}
            process.stdin.write(json.dumps(action) + '\n')
            process.stdin.flush()

            result = json.loads(process.stdout.readline())
            self.assertTrue(result['ok'])
            self.assertEqual(result['accepted_action'], action)

            next_decision = json.loads(process.stdout.readline())
            self.assertEqual(next_decision['kind'], 'childhood_pick_2')
            self.assertEqual(next_decision['decision_id'],
                             result['decision']['decision_id'])
        finally:
            process.stdin.close()
            process.terminate()
            process.wait(timeout=5)

    def test_purchase_ready_l1_waits_for_input_without_legal_actions(self):
        """Payload Slim v1：L1 无 legal_actions，CLI 仍等待输入而非自动推进。"""
        process = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, 'run_runtime.py'),
             '--seed', '1', '--goals', '1', '2'],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            lines = []
            decision = None
            # Draft 两手 + 可能的 pre_roll_c11/首骰自动推进，走查到 L1 为止
            for _ in range(20):
                raw = process.stdout.readline()
                if raw == '':
                    break
                decision = json.loads(raw)
                lines.append(decision)
                if decision.get('kind') == 'game_over':
                    break
                if 'purchase_targets' in decision:
                    break
                legal = decision.get('legal_actions')
                if not legal:
                    continue
                action = legal[0]
                if action.get('choice') == 'normal_reroll':
                    action = {'choice': 'proceed_to_purchase'}
                process.stdin.write(json.dumps(action) + '\n')
                process.stdin.flush()
                ack = json.loads(process.stdout.readline())
                self.assertTrue(ack['ok'])
            self.assertIsNotNone(decision)
            self.assertIn('purchase_targets', decision)
            self.assertNotIn('legal_actions', decision)
            # 关键回归：L1 无 legal_actions 也必须停在等待输入，
            # 不打印第二行（死循环自动推进会立刻刷出下一决策）。
            self.assertEqual([d for d in lines if 'purchase_targets' in d],
                             [decision])
        finally:
            process.stdin.close()
            process.terminate()
            process.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
