// 静态视觉原型 mock 数据：没有 session_id 时仅供展示，未连接 Runtime、MCP 或 API。
const demoGame = {
  phase: '中年期', round: 10, totalRounds: 23, position: 9,
  playerIdentity: { name: 'AI玩家', emoji: '🦊' },
  opportunity: [
    { type: 'Knowledge', name: '夜校课程', cost: '◆ ◆', effect: '知识 +2', color: 'knowledge' }, { type: 'Relationship', name: '社区花园', cost: '● ◆', effect: '关系 +1 · 健康 +1', color: 'relationship' }, { type: 'Work', name: '策展机会', cost: '■ ■', effect: '工作 +2', color: 'work' }, { type: 'Health', name: '山间徒步', cost: '● ●', effect: '健康 +2', color: 'health' }, { type: 'Possession', name: '二手唱机', cost: '◆ ●', effect: '金钱 +1 · 心情 +1', color: 'possession' },
  ],
  fate: [{ name: '恰好的时机', effect: '下一次机会，悄然来到。' }, { name: '远方来信', effect: '获得一次新的选择。' }],
  goals: [['温暖的港湾', '关系履历每张 +2 分'], ['好奇的一生', '知识履历每张 +2 分']], events: [['短期项目', '本次购买临时提供 M×2'], ['再想一下', '本回合额外获得 1 轮重掷']], debuff: { name: '睡眠不足', turns: '剩余 2 回合', effect: '每回合少 1 轮正常重掷' }, logs: ['10:15　购买「夜校课程」', '10:14　重掷 2 颗骰子', '10:13　进入中年期', '10:12　获得 Fate「恰好的时机」'],
  dice: [{ value: 'H' }, { value: 'K' }, { value: 'R' }, { value: 'M' }, { value: 'GL' }, { value: 'BL', frozen: true }, { value: 'H', muted: true }],
  resume: [
    { type: 'Health', name: '规律作息', effect: '精力 +1', color: 'health', held: ['晨间散步', '均衡饮食'] }, { type: 'Knowledge', name: '写作训练', effect: '知识 +2', color: 'knowledge', held: ['摄影入门'] }, { type: 'Relationship', name: '老朋友', effect: '关系 +2', color: 'relationship', held: ['大学同学', '邻居', '桌游俱乐部'] }, { type: 'Work', name: '独立设计师', effect: '工作 +2', color: 'work', held: ['实习经历'] }, { type: 'Possession', name: '安静的书桌', effect: '金钱 +1', color: 'possession', held: ['旧相机', '收藏唱片'] },
  ],
};

const element = (selector) => document.querySelector(selector);
const list = (selector, values, render) => { element(selector).innerHTML = values.map(render).join(''); };
const escapeHtml = (value = '') => String(value).replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[char]);
const TYPE_META = {
  H: { label: 'Health', color: 'health' }, Health: { label: 'Health', color: 'health' }, K: { label: 'Knowledge', color: 'knowledge' }, Knowledge: { label: 'Knowledge', color: 'knowledge' }, R: { label: 'Relationship', color: 'relationship' }, Relationship: { label: 'Relationship', color: 'relationship' }, W: { label: 'Work', color: 'work' }, Work: { label: 'Work', color: 'work' }, P: { label: 'Possession', color: 'possession' }, Possession: { label: 'Possession', color: 'possession' }, E: { label: 'Event', color: 'work' }, Event: { label: 'Event', color: 'work' },
};
const STAGE_LABELS = { youth: '青年期', middle: '中年期', old: '老年期' };
const CV_TYPES = [['H', 'Health'], ['K', 'Knowledge'], ['R', 'Relationship'], ['W', 'Work'], ['P', 'Possession']];
const DICE_ICONS = {
  H: './assets/dice/dice-h.png', K: './assets/dice/dice-k.png', R: './assets/dice/dice-r.png',
  M: './assets/dice/dice-m.png', GL: './assets/dice/dice-gl.png', BL: './assets/dice/dice-bl.png',
};
const DEFAULT_PLAYER_IDENTITY = { name: 'AI玩家', emoji: '🤖' };

const formatCost = (cost) => typeof cost === 'string' ? cost : Object.entries(cost || {}).map(([symbol, count]) => `${symbol}×${count}`).join(' ') || '—';
const cardMeta = (type) => TYPE_META[type] || { label: type || 'Card', color: 'work' };
const renderCard = (card, { fate = false, resume = false, index = 0 } = {}) => {
  const meta = cardMeta(card.type);
  const classes = fate ? 'game-card fate-card' : `game-card ${resume ? 'resume-card' : 'market-card'} ${resume ? card.color : meta.color}`;
  const footer = fate ? '命运的赠礼' : `<span>成本</span><b>${escapeHtml(formatCost(card.cost))}</b>${card.vp ? `<span>VP ${escapeHtml(card.vp)}</span>` : ''}`;
  const more = resume ? `<button class="more-button" data-index="${index}" type="button">more <span>→</span></button>` : '';
  return `<article class="${classes}"><p class="card-type">${escapeHtml(fate ? 'FATE' : meta.label)}</p><h3>${escapeHtml(card.name || '暂无')}</h3><p class="card-effect">${escapeHtml(card.effect_summary || card.effect || '暂无')}</p><footer>${footer}</footer>${more}</article>`;
};
const renderCardSlots = (selector, cards, slotCount, options = {}) => {
  const visibleCards = cards.slice(0, slotCount);
  const cardMarkup = visibleCards.map((card, index) => renderCard(card, { ...options, index }));
  const emptySlots = Array.from({ length: slotCount - visibleCards.length }, () => '<div class="card-slot" aria-hidden="true"></div>');
  element(selector).innerHTML = [...cardMarkup, ...emptySlots].join('');
};
const renderTextList = (selector, items, emptyText) => list(selector, items.length ? items : [[emptyText, '']], (item) => { const [name, detail] = Array.isArray(item) ? item : [item.name, item.effect_summary]; return `<li><b>${escapeHtml(name || emptyText)}</b>${detail ? `<span>${escapeHtml(detail)}</span>` : ''}</li>`; });
const renderDie = (die) => { const icon = DICE_ICONS[die.value]; return `<div class="die ${die.frozen ? 'frozen' : ''} ${die.muted ? 'muted' : ''}" aria-label="${escapeHtml(die.value)}">${icon ? `<img class="die-icon" src="${icon}" alt="${escapeHtml(die.value)}" />` : ''}</div>`; };
const renderDice = (dice) => {
  const groups = [dice.slice(0, 3), dice.slice(3, 5), dice.slice(5, 7)].filter((group) => group.length);
  element('#dice-row').innerHTML = groups.map((group) => `<div class="dice-row-group">${group.map(renderDie).join('')}</div>`).join('');
};
const renderPlayerIdentity = (identity, round) => {
  const safeIdentity = { ...DEFAULT_PLAYER_IDENTITY, ...(identity || {}) };
  element('#player-name').textContent = safeIdentity.name || DEFAULT_PLAYER_IDENTITY.name;
  element('#player-emoji').textContent = safeIdentity.emoji || DEFAULT_PLAYER_IDENTITY.emoji;
  element('#player-turn').textContent = `${Number.isInteger(round) ? round : 0} / 23 回合`;
};

let displayedResume = demoGame.resume;
const renderDesk = (game) => {
  element('#round-status').textContent = `${game.phase} · 第 ${game.round} / 23 回合 · ${game.connection}`;
  element('.opportunity-market .section-title span').textContent = `当前 ${game.opportunity.length} 张`;
  renderCardSlots('#opportunity-cards', game.opportunity, 5);
  renderCardSlots('#fate-cards', game.fate, 2, { fate: true });
  renderTextList('#goals-list', game.goals, '暂无'); renderTextList('#events-list', game.events, '暂无');
  element('#debuff-detail').innerHTML = game.debuff ? `<b>${escapeHtml(game.debuff.name)}</b><span>${escapeHtml(game.debuff.turns)}</span><small>${escapeHtml(game.debuff.effect)}</small>` : '<span>暂无</span>';
  renderTextList('#log-list', game.logs.map((text) => [text, '']), '暂无'); renderDice(game.dice); renderPlayerIdentity(game.playerIdentity, game.round);
  displayedResume = game.resume; list('#resume-cards', displayedResume, (card, index) => renderCard(card, { resume: true, index }));
};
const renderDemo = () => renderDesk({ phase: demoGame.phase, round: demoGame.round, connection: '静态预览', playerIdentity: demoGame.playerIdentity, opportunity: demoGame.opportunity, fate: demoGame.fate, goals: demoGame.goals, events: demoGame.events, debuff: demoGame.debuff, logs: demoGame.logs, dice: demoGame.dice, resume: demoGame.resume });
const snapshotToDesk = (snapshot) => {
  const stage = STAGE_LABELS[snapshot.stage] || snapshot.stage || '未开始';
  const frozen = new Set(snapshot.dice?.frozen_indices || []);
  const resume = CV_TYPES.map(([type, label]) => { const slot = snapshot.cv?.[type] || { stack: [], top_card_id: null }; const top = slot.stack.find((card) => card.card_id === slot.top_card_id); return top ? { ...top, type: label, color: cardMeta(label).color, held: slot.stack.filter((card) => card.card_id !== slot.top_card_id).map((card) => card.name) } : { type: label, color: cardMeta(label).color, name: '暂无', effect_summary: '当前没有持有履历', held: [] }; });
  return { phase: stage, round: snapshot.game_over ? snapshot.completed_turn : snapshot.current_turn, connection: snapshot.game_over || snapshot.status === 'game_over' ? '已结束' : '已连接', playerIdentity: snapshot.player_identity || DEFAULT_PLAYER_IDENTITY, opportunity: snapshot.opportunity_market || [], fate: snapshot.fate_market?.cards || [], goals: (snapshot.life_goals || []).map((goal) => [goal.name, goal.scoring_text]), events: (snapshot.event_hand || []).map((card) => [card.name, card.effect_summary]), debuff: snapshot.current_debuff ? { name: snapshot.current_debuff.name, turns: `剩余 ${snapshot.current_debuff.turns_remaining} 回合`, effect: snapshot.current_debuff.effect_summary } : null, logs: (snapshot.recent_events || []).slice(-6).map((event) => event.text), dice: (snapshot.dice?.values || []).map((value, index) => ({ value, frozen: frozen.has(index) })), resume };
};

const modal = element('#resume-modal');
const openResume = (card) => { element('#modal-title').textContent = card.type; element('#modal-note').textContent = `顶部牌为「${card.name}」。以下是本类其他仍持有履历，按当前履历堆顺序展示。`; element('#modal-cards').innerHTML = card.held.length ? card.held.map((name) => `<li>${escapeHtml(name)}</li>`).join('') : '<li>暂无其他持有牌</li>'; modal.showModal(); };
element('#resume-cards').addEventListener('click', (event) => { const button = event.target.closest('.more-button'); if (button) openResume(displayedResume[button.dataset.index]); }); element('.modal-close').addEventListener('click', () => modal.close()); modal.addEventListener('click', (event) => { if (event.target === modal) modal.close(); });

const sessionId = new URLSearchParams(window.location.search).get('session_id');
const spectatorBase = 'http://127.0.0.1:8765';
let pollTimer = null;
let pollInFlight = false;
let hasSnapshot = false;
const setConnectionLabel = (label) => { const status = element('#round-status'); status.textContent = status.textContent.replace(/· (静态预览|连接中|已连接|已结束|session 不存在|bridge 不可达)$/, `· ${label}`); };
const pollSnapshot = async () => {
  if (pollInFlight) return;
  pollInFlight = true;
  if (!hasSnapshot) setConnectionLabel('连接中');
  try { const response = await fetch(`${spectatorBase}/spectator/sessions/${encodeURIComponent(sessionId)}`); if (!response.ok) { setConnectionLabel(response.status === 404 ? 'session 不存在' : 'bridge 不可达'); return; } renderDesk(snapshotToDesk(await response.json())); hasSnapshot = true; } catch (_) { setConnectionLabel('bridge 不可达'); } finally { pollInFlight = false; pollTimer = window.setTimeout(pollSnapshot, 1200); }
};
renderDemo();
if (sessionId) pollSnapshot();

// 保留 1080×1080 内部设计画布，只按浏览器可用高度整体缩放。
const DESIGN_BOARD_SIZE = 1080;
const boardSpace = document.querySelector('.board-space');
const boardCanvas = document.querySelector('.page-shell');
const syncBoardScale = () => { const top = boardSpace.getBoundingClientRect().top; const availableHeight = Math.max(0, window.innerHeight - top - 16); const scale = Math.min(1, availableHeight / DESIGN_BOARD_SIZE); boardSpace.style.width = `${DESIGN_BOARD_SIZE * scale}px`; boardSpace.style.height = `${DESIGN_BOARD_SIZE * scale}px`; boardCanvas.style.setProperty('--board-scale', scale); };
window.addEventListener('resize', syncBoardScale);
syncBoardScale();
