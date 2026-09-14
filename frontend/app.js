// 静态视觉原型 mock 数据：仅供展示，未连接 Runtime、MCP 或 API。
const demoGame = {
  phase: '中年期', round: 10, totalRounds: 23, position: 9,
  opportunity: [
    { type: 'Knowledge', name: '夜校课程', cost: '◆ ◆', effect: '知识 +2', color: 'knowledge' }, { type: 'Relationship', name: '社区花园', cost: '● ◆', effect: '关系 +1 · 健康 +1', color: 'relationship' }, { type: 'Work', name: '策展机会', cost: '■ ■', effect: '工作 +2', color: 'work' }, { type: 'Health', name: '山间徒步', cost: '● ●', effect: '健康 +2', color: 'health' }, { type: 'Possession', name: '二手唱机', cost: '◆ ●', effect: '金钱 +1 · 心情 +1', color: 'possession' },
  ],
  fate: [{ name: '恰好的时机', effect: '下一次机会，悄然来到。' }, { name: '远方来信', effect: '获得一次新的选择。' }],
  goals: [['温暖的港湾', '关系履历每张 +2 分'], ['好奇的一生', '知识履历每张 +2 分']], events: [['短期项目', '本次购买临时提供 M×2'], ['再想一下', '本回合额外获得 1 轮重掷']], debuff: { name: '睡眠不足', turns: '剩余 2 回合', effect: '每回合少 1 轮正常重掷' }, logs: ['10:15　购买「夜校课程」', '10:14　重掷 2 颗骰子', '10:13　进入中年期', '10:12　获得 Fate「恰好的时机」'],
  dice: [{ value: 1 }, { value: 2 }, { value: 3 }, { value: 4 }, { value: 5 }, { value: 6, frozen: true }, { value: 2, muted: true }],
  resume: [
    { type: 'Health', name: '规律作息', effect: '精力 +1', color: 'health', held: ['晨间散步', '均衡饮食'] }, { type: 'Knowledge', name: '写作训练', effect: '知识 +2', color: 'knowledge', held: ['摄影入门'] }, { type: 'Relationship', name: '老朋友', effect: '关系 +2', color: 'relationship', held: ['大学同学', '邻居', '桌游俱乐部'] }, { type: 'Work', name: '独立设计师', effect: '工作 +2', color: 'work', held: ['实习经历'] }, { type: 'Possession', name: '安静的书桌', effect: '金钱 +1', color: 'possession', held: ['旧相机', '收藏唱片'] },
  ],
};
const element = (selector) => document.querySelector(selector);
const list = (selector, values, render) => { element(selector).innerHTML = values.map(render).join(''); };
element('#round-status').textContent = `${demoGame.phase} · 第 ${demoGame.round} / ${demoGame.totalRounds} 回合 · demo`; element('#phase-label').textContent = demoGame.phase;
list('#opportunity-cards', demoGame.opportunity, (card) => `<article class="game-card market-card ${card.color}"><p class="card-type">${card.type}</p><h3>${card.name}</h3><p class="card-effect">${card.effect}</p><footer><span>成本</span><b>${card.cost}</b></footer></article>`);
list('#fate-cards', demoGame.fate, (card) => `<article class="game-card fate-card"><p class="card-type">FATE</p><h3>${card.name}</h3><p class="card-effect">${card.effect}</p><footer>命运的赠礼</footer></article>`);
list('#goals-list', demoGame.goals, ([name, detail]) => `<li><b>${name}</b><span>${detail}</span></li>`); list('#events-list', demoGame.events, ([name, detail]) => `<li><b>${name}</b><span>${detail}</span></li>`); element('#debuff-detail').innerHTML = `<b>${demoGame.debuff.name}</b><span>${demoGame.debuff.turns}</span><small>${demoGame.debuff.effect}</small>`; list('#log-list', demoGame.logs, (item) => `<li>${item}</li>`);
const dots = { 1:['center'], 2:['top-left','bottom-right'], 3:['top-left','center','bottom-right'], 4:['top-left','top-right','bottom-left','bottom-right'], 5:['top-left','top-right','center','bottom-left','bottom-right'], 6:['top-left','top-right','middle-left','middle-right','bottom-left','bottom-right'] };
list('#dice-row', demoGame.dice, (die) => `<div class="die ${die.frozen ? 'frozen' : ''} ${die.muted ? 'muted' : ''}" aria-label="${die.value} 点">${dots[die.value].map((position) => `<i class="pip ${position}"></i>`).join('')}</div>`);
const track = element('#life-track');
// 23 个成年回合格沿固定方形回环排列：青年 8 / 中年 7 / 老年 8。
const orbit = [
  ['-42%','-40%'],['-28%','-40%'],['-14%','-40%'],['0%','-40%'],['14%','-40%'],['28%','-40%'],['42%','-40%'],
  ['44%','-24%'],['44%','-8%'],['44%','8%'],['44%','24%'],['44%','40%'],
  ['30%','40%'],['18%','40%'],['6%','40%'],['-6%','40%'],['-18%','40%'],['-30%','40%'],['-42%','40%'],
  ['-44%','24%'],['-44%','8%'],['-44%','-8%'],['-44%','-24%'],
].map(([x, y]) => ({ x, y }));
orbit.forEach((point, index) => { const cell = document.createElement('span'); const phase = index < 8 ? 'youth' : index < 15 ? 'midlife' : 'elder'; const current = index === demoGame.position - 1; cell.className = `track-cell ${phase} ${current ? 'current' : ''}`; cell.style.setProperty('--x', point.x); cell.style.setProperty('--y', point.y); cell.innerHTML = `<b>${index + 1}</b>${current ? '<em>AI</em>' : ''}`; track.append(cell); });
list('#resume-cards', demoGame.resume, (card, index) => `<article class="game-card resume-card ${card.color}"><p class="card-type">${card.type}</p><h3>${card.name}</h3><p class="card-effect">${card.effect}</p><button class="more-button" data-index="${index}" type="button">more <span>→</span></button></article>`);
const modal = element('#resume-modal'); const openResume = (card) => { element('#modal-title').textContent = card.type; element('#modal-note').textContent = `顶部牌为「${card.name}」。以下是本类其他仍持有履历，按当前履历堆顺序展示。`; element('#modal-cards').innerHTML = card.held.length ? card.held.map((name) => `<li>${name}</li>`).join('') : '<li>暂无其他持有牌</li>'; modal.showModal(); };
element('#resume-cards').addEventListener('click', (event) => { const button = event.target.closest('.more-button'); if (button) openResume(demoGame.resume[button.dataset.index]); }); element('.modal-close').addEventListener('click', () => modal.close()); modal.addEventListener('click', (event) => { if (event.target === modal) modal.close(); });
