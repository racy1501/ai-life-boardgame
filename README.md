# 🎲 AI人生桌游

这是一个主要给 AI 自己玩的单人策略人生桌游。AI 通过 MCP 做人生选择，系统负责规则、随机、合法性、结算与计分；人类主要通过网页围观 AI 的人生进程。

> **AI负责选择，系统负责裁决。**
> **AI负责策略，后台负责算账。**

## 项目简介

本项目灵感参考桌游《人生履历 / CV》的“人生阶段 + 履历构筑 + 随机事件 + 人生目标”机制语言，但不直接复刻原版卡牌、美术、具体数值与完整规则：当前已重新设计为一款适合 AI 通过 MCP 游玩的单人策略人生桌游。

游戏保留明确的桌游感和策略性，不做成纯剧情人生模拟器。AI 应明确感受到“自己正在玩一局桌游”，而不是只在调用数据库接口。

规则边界是固定的：

- AI 不自行计算规则，正式判定全部由后台完成。
- 人类不是与 AI 轮流操作的第二名玩家，人类前端主要用于围观。
- 随机数、合法性、结算与计分永远以后台代码为准，AI 的自然语言推理不能覆盖后台结果。

当前名称仍为暂定名，正式游戏名待后续确定。

## 核心玩法

- **Childhood Draft**：开局 3 选 1 → 重新抽 2 选 1 → 随机补第 3 张，最终 3 张与两张 Life Goal 一同进入正式状态；童年只负责开局 Draft，不进入成年机会市场回合。
- **三个成年阶段**：青年 → 中年 → 老年，采用牌库驱动推进，不预先写死每个阶段的回合数。
- **CV 五类履历**：Health / Knowledge / Relationship / Work / Possession，构成本局的资源与得分结构。
- **Event 事件牌**：在特定窗口中可用的正式能力。
- **Fate 命运**：独立命运市场，每 3 个成年回合开放一次命运窗口。
- **Debuff 逆境**：唯一不放回的逆境牌，同一时间最多 1 张生效，风险可被策略管理但不会完全消失。
- **Life Goal**：每局两张私人人生目标，计分公式公开，AI 需要在自己的人生目标与当前市场机会之间取舍。
- **成年回合主链**：回合前声明 → 首次掷骰 → 重掷 / 特殊骰操作 → 普通与 Fate 购买 → Debuff 结算 → 履历放置 → 持续成本维护 → 市场清理 → 回合收尾。
- **市场**：普通市场开局 5 张，每个完整回合固定总离场 3 张。
- **回合规模**：成年阶段约 23 个完整回合（青年约 8 / 中年约 7 / 老年约 8）。这不是写死的回合表，而是当前牌量、固定市场流速与最终回合规则自然产生的结果。
- **终局**：统一由代码计算正式分数，AI 的口头计算不覆盖后台结算。

完整规则不放在这里，以根目录基础策划文档为准。

## AI / MCP 如何游玩

AI 通过 MCP 工具游玩，基本循环是：

```text
start_game
→ current_decision
→ AI 做策略选择
→ submit_action
→ 返回下一 decision
→ …… 直到 game_over
```

当前本地测试 MCP 只提供三个正式工具：

| 工具 | 作用 |
| --- | --- |
| `start_game` | 开一局新游戏，支持 seed 与两张初始 Life Goal，返回 `session_id` 与首个 decision |
| `current_decision` | 读取指定 session 的当前 decision；重复读取不改变任何状态 |
| `submit_action` | 提交当前 decision 的 action，返回提交结果与下一 decision |

运行时分层原则：

- Runtime 每次只给 AI 当前决策所需的最小充分信息：当前阶段、骰面与剩余重掷、稳定资源、当前机会牌、相关 active 能力、当前窗口可用的 Event、两张 Life Goal、当前合法行动或合法购买方案，以及本轮必须做出的决策。
- 合法购买、支付来源、市场流动、Fate、Debuff、维护成本与终局计分都由后台计算并校验；AI 不需要读取完整牌库，也不负责自己验证支付合法性。
- 规则信息按需出现：牌走到哪里，解释走到哪里；本局没有抽到、没有遇到的牌与机制，不提前占用上下文。
- MCP 层保持薄：不复制规则、不自行计算合法动作与支付方案，只做事状态的透传与进程内 session 管理。
- 本仓库包含 Production Runtime 与本地测试 MCP。最终游戏站的 MCP 封装与 VPS 部署不在本仓库当前开发阶段内，由后续接入方完成。

## 人类围观前端

当前状态：**人类围观网页前端尚未开发。**

前端定位：

- 前端只负责展示游戏世界、卡牌、骰子、人生阶段、履历与结算过程。
- 前端不成为第二套规则事实源：正式游戏状态始终由 Runtime / Engine 决定，前端不做规则判定。
- 人类前端主要用于围观与查看，不与 AI 轮流操作同一局游戏。

后续计划加入轻量桌游视觉演出，例如：

- 掷骰
- 棋子移动
- 卡牌翻开 / 获得
- 人生阶段切换
- 游戏进程与终局履历展示

本仓库当前不包含任何前端文件，也不存在可访问的前端网页地址。视觉方向见基础策划文档第 18 节。

## 当前状态

- ✅ Simulation / 数值基线完成
- ✅ Production Runtime 主链完成
- ✅ 本地测试 MCP 完成
- ✅ 特殊能力、JIT 信息层、终局计分完成
- ✅ 多轮真实模型完整黑盒可自然运行到 `game_over`
- ✅ Runtime Payload Slim v1 已完成
- 🔄 当前阶段：建立公开 Git 基线后进入人类围观前端开发
- ⏳ 前端完成后进行整体验收
- ⏳ 最后交由外部接入方进行正式 MCP 封装、VPS 部署与游戏站接入

数值结构与大规模平衡调整当前处于冻结状态。已有的单局试玩分数与路线只用于 Runtime 信息、接口与可理解性验收，不作为平衡结论。

## 项目结构

```text
AI人生桌游/
├── README.md
├── LICENSE
├── AI人生桌游-基础策划文档-v0.6-运行时同步版-2026-09-14.md
├── AI人生桌游-完整卡牌表-v0.6.md
└── simulation/
    ├── ailife/            # 规则与运行时核心：cards / engine / runtime / scoring / stats / strategies
    ├── runtime_mcp.py     # 本地测试 MCP，只提供三个正式工具
    ├── run_runtime.py     # 极薄本地 CLI
    ├── run_simulation.py  # 数值模拟批量入口
    └── tests/             # Engine / Runtime / CLI / MCP 测试
```

## 本地测试 MCP

```bash
cd simulation
uv run --no-project --with mcp python runtime_mcp.py
```

- 本地测试 MCP 使用官方 Python MCP SDK，通过 stdio 提供 `start_game` / `current_decision` / `submit_action` 三个工具。
- 上一条命令在临时环境里获取 SDK，不需要改动项目依赖。当前环境已安装官方 mcp SDK 时，也可以直接运行 `python runtime_mcp.py`。
- session 只存在于进程内，使用不可预测 ID 生成：不落库、不存档、不做序列化。server 重启后旧 `session_id` 失效，需要重新 `start_game`。
- 传入不存在的 `session_id` 会返回 `unknown_session_id`；提交过期或非法的 action 由 Runtime 判定并返回当前正式 decision。

## 本地运行

极薄本地 CLI：

```bash
cd simulation
python run_runtime.py --seed 1 --goals 1 2
```

CLI 输出一行当前 decision JSON，读取一行 action JSON 后输出提交结果，直到 `game_over`。`--seed` 用于复现同一局随机序列，`--goals` 可指定两张初始 Life Goal。

数值模拟：

```bash
cd simulation
python run_simulation.py --config V06 --games 1000 --seed 1
```

模拟结果输出到 `results/<config>/`，属于本地运行产物，不进入版本库。

Runtime、CLI 与模拟入口只依赖 Python 标准库；测试使用 pytest；本地测试 MCP 需要官方 mcp SDK。当前开发与验证环境为 Python 3.13，本地测试 MCP 亦在 Python 3.14 下运行通过。

## 测试

从 `simulation` 目录执行当前的全量测试：

```bash
cd simulation
python -m pytest tests -q
```

覆盖 Engine、Runtime、CLI 与 MCP。其中与 MCP server 注册相关的用例需要官方 mcp SDK，未安装时会自动跳过；需要完整运行时改用：

```bash
cd simulation
uv run --no-project --with mcp --with pytest python -m pytest tests -q
```

## 文档

- [基础策划文档](AI人生桌游-基础策划文档-v0.6-运行时同步版-2026-09-14.md)：规则、Runtime 设计、系统架构与当前开发基线。
- [完整卡牌表](AI人生桌游-完整卡牌表-v0.6.md)：当前正式牌池、能力与 Life Goal 计分公式。

基础策划文档是当前规则与开发基线的正式来源；卡牌数值与能力以完整卡牌表为准。

## 使用许可

本项目公开源码，但不是允许商业使用的传统宽松开源项目。

在遵守 [PolyForm Noncommercial License 1.0.0](LICENSE) 的前提下，允许个人非商业：

- 游玩
- 部署
- 修改
- 学习
- 研究
- 分享非商业修改版本

明确禁止：

- 收费部署
- 打包售卖
- 广告 / 流量变现
- 商业软件集成
- 付费服务集成
- 其他商业使用

二次公开发布时，需要保留原作者信息、原仓库链接与 `LICENSE` 文件，并且不得暗示修改版本是原作者的官方版本。当前不提供商业使用授权；完整许可条件以根目录 [`LICENSE`](LICENSE) 为准。
