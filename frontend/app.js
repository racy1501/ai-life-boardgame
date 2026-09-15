// 无 session 时仅保留非卡牌的静态桌面状态；卡牌内容一律从正式 catalog hydrate。
const demoGame = {
  phase: '中年期', round: 10, totalRounds: 23, position: 9,
  playerIdentity: { name: 'AI玩家', emoji: '🦊' },
  goals: [['温暖的港湾', '关系履历每张 +2 分'], ['好奇的一生', '知识履历每张 +2 分']], logs: ['10:15　示例桌面已准备', '10:14　等待正式卡牌图鉴', '10:13　进入中年期'],
  dice: [{ value: 'H' }, { value: 'K' }, { value: 'R' }, { value: 'M' }, { value: 'GL' }, { value: 'BL', frozen: true }],
  maxDiceCount: 7,
};

const element = (selector) => document.querySelector(selector);
const list = (selector, values, render) => { element(selector).innerHTML = values.map(render).join(''); };
const escapeHtml = (value = '') => String(value).replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[char]);
const TYPE_META = {};
for (const [key, [label, zh, color]] of Object.entries({
  H: ['Health', '健康', 'health'], K: ['Knowledge', '知识', 'knowledge'], R: ['Relationship', '关系', 'relationship'], W: ['Work', '工作', 'work'], P: ['Possession', '财产', 'possession'], E: ['Event', '事件', 'event'], C: ['Childhood', '童年', 'childhood'], D: ['Debuff', '逆境', 'debuff'], F: ['Fate', '命运', 'fate'],
})) { TYPE_META[key] = { label, zh, color }; TYPE_META[label] = { label, zh, color }; }
const STAGE_LABELS = { youth: '青年期', middle: '中年期', elder: '老年期' };
const CV_TYPES = [['H', 'Health'], ['K', 'Knowledge'], ['R', 'Relationship'], ['W', 'Work'], ['P', 'Possession']];
const demoShowcase = {
  opportunity: ['YH-02', 'YK-03', 'YR-02', 'YW-01', 'YP-01'],
  fate: ['F01', 'F06'],
  resume: { H: ['MH-01'], K: ['MK-01'], R: ['MR-01'], W: ['MW-01'], P: ['MP-01'] },
  eventHand: ['YE-01', 'YE-03'],
  debuff: 'D06',
};
const demoShowcaseCardIds = [
  ...demoShowcase.opportunity, ...demoShowcase.fate,
  ...Object.values(demoShowcase.resume).flat(), ...demoShowcase.eventHand,
  demoShowcase.debuff,
];
if (new Set(demoShowcaseCardIds).size !== demoShowcaseCardIds.length) {
  throw new Error('demo showcase card_id must be unique');
}
const DICE_ICONS = {
  H: './assets/dice/dice-h.png', K: './assets/dice/dice-k.png', R: './assets/dice/dice-r.png',
  M: './assets/dice/dice-m.png', GL: './assets/dice/dice-gl.png', BL: './assets/dice/dice-bl.png',
};
const DEFAULT_PLAYER_IDENTITY = { name: 'AI玩家', emoji: '🤖' };

const formatCost = (cost) => typeof cost === 'string' ? cost : Object.entries(cost || {}).map(([symbol, count]) => `${symbol}×${count}`).join(' ') || '—';
const cardMeta = (type) => TYPE_META[type] || { label: type || 'Card', color: 'work' };
const renderCard = (card, { fate = false, resume = false, detail = false, showCost = !resume, typeLabel = null, index = 0 } = {}) => {
  const meta = cardMeta(card.type);
  const classes = fate ? 'game-card fate-card' : `game-card ${resume ? 'resume-card' : 'market-card'} ${detail ? 'detail-card ' : ''}${resume ? card.color : meta.color}`;
  const footer = fate ? '命运的赠礼' : detail
    ? `<span>成本</span><b>${formatCostChips(card.cost)}</b>${card.vp ? `<span>计分</span><b>+${escapeHtml(card.vp)}</b>` : ''}`
    : `${showCost ? `<span>成本</span><b>${escapeHtml(formatCost(card.cost))}</b>` : ''}${card.vp ? `<span>VP ${escapeHtml(card.vp)}</span>` : ''}`;
  const dataId = card.card_id ? ` data-card-id="${escapeHtml(card.card_id)}"` : '';
  const effectClass = detail ? 'card-effect card-effect-full' : 'card-effect';
  const effectHtml = detail ? formatEffectText(card.effect_summary || card.effect) : escapeHtml(card.effect_summary || card.effect || '暂无');
  return `<article class="${classes}"${dataId}><p class="card-type">${escapeHtml(fate ? 'FATE' : typeLabel || meta.label)}</p><h3>${escapeHtml(card.name || '暂无')}</h3><p class="${effectClass}">${effectHtml}</p>${footer ? `<footer>${footer}</footer>` : ''}</article>`;
};
const renderCardSlots = (selector, cards, slotCount, options = {}) => {
  const visibleCards = cards.slice(0, slotCount);
  const cardMarkup = visibleCards.map((card, index) => renderCard(card, { ...options, index }));
  const emptySlots = Array.from({ length: slotCount - visibleCards.length }, () => '<div class="card-slot" aria-hidden="true"></div>');
  element(selector).innerHTML = [...cardMarkup, ...emptySlots].join('');
};
const renderTextList = (selector, items, emptyText) => list(selector, items.length ? items : [[emptyText, '']], (item) => { const [name, detail] = Array.isArray(item) ? item : [item.name, item.effect_summary]; return `<li><b>${escapeHtml(name || emptyText)}</b>${detail ? `<span>${escapeHtml(detail)}</span>` : ''}</li>`; });
const renderDie = (die, rolling = false) => { const icon = DICE_ICONS[die.value]; const frozenLabel = die.frozen ? '，当前不可普通重掷' : ''; return `<div class="die ${die.frozen ? 'frozen' : ''} ${die.muted ? 'muted' : ''} ${rolling ? 'is-rolling' : ''}" title="${escapeHtml(die.value)}骰${frozenLabel}" aria-label="${escapeHtml(die.value)}骰${frozenLabel}">${icon ? `<img class="die-icon" src="${icon}" alt="${escapeHtml(die.value)}" />` : ''}</div>`; };
const DICE_SLOT_COUNT = 7;
const DICE_FACE_CYCLE = ['H', 'K', 'R', 'M', 'GL', 'BL'];
const renderDice = (dice, maxDiceCount = DICE_SLOT_COUNT, rollingFrame = null) => {
  element('#dice-count').textContent = `已启用 ${dice.length} / ${maxDiceCount}`;
  const slots = Array.from({ length: maxDiceCount }, (_, index) => {
    if (index >= dice.length) return '<div class="die die-empty" aria-label="未生效骰位"></div>';
    const die = dice[index];
    const rolling = rollingFrame !== null && !die.frozen;
    const value = rolling ? DICE_FACE_CYCLE[(rollingFrame + index) % DICE_FACE_CYCLE.length] : die.value;
    return renderDie({ ...die, value }, rolling);
  });
  const groups = [slots.slice(0, 3), slots.slice(3, 5), slots.slice(5, 7)];
  element('#dice-row').innerHTML = groups.map((group) => `<div class="dice-row-group">${group.join('')}</div>`).join('');
};
const ensureDiceRuleHint = () => {
  const dicePool = element('.dice-pool-card');
  if (dicePool && !dicePool.querySelector('.dice-rule-hint')) dicePool.insertAdjacentHTML('beforeend', '<p class="dice-rule-hint">厄运骰会被锁定，不能进行普通重掷。</p>');
};
const renderPlayerIdentity = (identity, round) => {
  const safeIdentity = { ...DEFAULT_PLAYER_IDENTITY, ...(identity || {}) };
  element('#player-name').textContent = safeIdentity.name || DEFAULT_PLAYER_IDENTITY.name;
  element('#player-emoji').textContent = safeIdentity.emoji || DEFAULT_PLAYER_IDENTITY.emoji;
  element('#player-turn').textContent = `${Number.isInteger(round) ? round : 0} / 23 回合`;
};

let displayedResume = [];
let displayedGoals = [];
let displayedEvents = [];
const renderGoalList = (goals) => { displayedGoals = goals; element('#goals-list').innerHTML = goals.map((goal, index) => `<li class="goal-item"><b>${escapeHtml(goal[0])}</b><button class="more-button goal-more" data-index="${index}" type="button">详情 <span>→</span></button></li>`).join('') || '<li class="goal-item"><b>暂无</b></li>'; };
const renderEventList = (events) => { displayedEvents = events; element('#events-list').innerHTML = events.length ? events.map((card, index) => `<li class="event-item"><b>${escapeHtml(card.name)}</b><button class="more-button event-more" data-index="${index}" type="button">详情 <span>→</span></button></li>`).join('') : '<li class="event-item"><b>暂无</b></li>'; };
const renderDesk = (game, { renderDicePool = true } = {}) => {
  element('#round-status').textContent = `${game.phase} · 第 ${game.round} / 23 回合 · ${game.connection}`;
  element('.opportunity-market .section-title span').textContent = `当前 ${game.opportunity.length} 张`;
  renderCardSlots('#opportunity-cards', game.opportunity, 5);
  renderCardSlots('#fate-cards', game.fate, 2, { fate: true });
  renderGoalList(game.goals); renderEventList(game.events);
  element('#debuff-detail').innerHTML = game.debuff ? `<b>${escapeHtml(game.debuff.name)}</b><span>${escapeHtml(game.debuff.turns)}</span><small>${escapeHtml(game.debuff.effect)}</small>` : '<span>暂无</span>';
  renderTextList('#log-list', game.logs.map((text) => [text, '']), '暂无'); if (renderDicePool) renderDice(game.dice, game.maxDiceCount); renderPlayerIdentity(game.playerIdentity, game.round);
  ensureDiceRuleHint();
  displayedResume = game.resume; list('#resume-cards', displayedResume, (card, index) => `<div class="resume-card-wrap">${renderCard(card, { resume: true, showCost: false, index })}<button class="more-button" data-index="${index}" type="button">more <span>→</span></button></div>`);
};
const renderDemo = () => renderDesk(buildDemoDesk());
const snapshotToDesk = (snapshot) => {
  const stage = STAGE_LABELS[snapshot.stage] || snapshot.stage || '未开始';
  const frozen = new Set(snapshot.dice?.frozen_indices || []);
  const resume = CV_TYPES.map(([type, label]) => { const slot = snapshot.cv?.[type] || { stack: [], top_card_id: null }; const top = slot.stack.find((card) => card.card_id === slot.top_card_id); return top ? { ...top, type: label, color: cardMeta(label).color, held: slot.stack.map((card) => ({ ...card, is_top: card.card_id === slot.top_card_id })) } : { type: label, color: cardMeta(label).color, name: '暂无', effect_summary: '当前没有持有履历', held: [] }; });
  return { phase: stage, round: snapshot.game_over ? snapshot.completed_turn : snapshot.current_turn, connection: snapshot.game_over || snapshot.status === 'game_over' ? '已结束' : '已连接', playerIdentity: snapshot.player_identity || DEFAULT_PLAYER_IDENTITY, opportunity: snapshot.opportunity_market || [], fate: snapshot.fate_market?.cards || [], goals: (snapshot.life_goals || []).map((goal) => [goal.name, goal.scoring_text]), events: snapshot.event_hand || [], debuff: snapshot.current_debuff ? { name: snapshot.current_debuff.name, turns: `剩余 ${snapshot.current_debuff.turns_remaining} 回合`, effect: snapshot.current_debuff.effect_summary } : null, logs: (snapshot.recent_events || []).slice(-6).map((event) => event.text), dice: (snapshot.dice?.values || []).map((value, index) => ({ value, frozen: frozen.has(index) })), maxDiceCount: snapshot.dice?.max_dice_count || DICE_SLOT_COUNT, rollRevision: snapshot.dice?.roll_revision, resume };
};

const modal = element('#resume-modal');
const openResume = (card) => { element('#modal-title').textContent = card.type; const held = card.held || []; element('#modal-note').textContent = held.length ? `本类仍持有的履历堆共 ${held.length} 张，按当前堆叠顺序展示，「当前」为顶牌。` : '本类当前没有持有牌。'; element('#modal-cards').innerHTML = held.length ? held.map((item) => `<li>${renderCard({ ...item, type: card.type, color: card.color }, { resume: true, showCost: false })}${item.is_top ? '<span class="current-badge">当前</span>' : ''}</li>`).join('') : '<li class="modal-empty">本类履历堆为空</li>'; modal.showModal(); };
element('#resume-cards').addEventListener('click', (event) => { const button = event.target.closest('.more-button'); if (button) openResume(displayedResume[button.dataset.index]); }); element('.modal-close').addEventListener('click', () => modal.close()); modal.addEventListener('click', (event) => { if (event.target === modal) modal.close(); });

// 图鉴与 Card Detail 共用唯一的正式 /cards/catalog 缓存，不复制、不改写卡牌规则。
let cardCatalogById = null;
let cardCatalogLoading = null;
const loadCardCatalog = async () => {
  if (cardCatalogById) return true;
  if (cardCatalogLoading) return cardCatalogLoading;
  cardCatalogLoading = (async () => {
    try {
    const response = await fetch(`${spectatorBase}/cards/catalog`);
    if (!response.ok) return false;
    const payload = await response.json();
    const map = {};
    for (const card of payload.cards || []) map[card.card_id] = card;
    cardCatalogById = map;
    return true;
    } catch (_) { return false; /* catalog 不可达：主桌面照常，详情入口轻量无反应 */
    } finally { cardCatalogLoading = null; }
  })();
  return cardCatalogLoading;
};
const demoCardsFromCatalog = (ids) => ids.map((id) => cardCatalogById?.[id]).filter(Boolean);
const buildDemoResume = () => CV_TYPES.map(([type, label]) => {
  const held = demoCardsFromCatalog(demoShowcase.resume[type]).map((card, index, cards) => ({ ...card, is_top: index === cards.length - 1 }));
  const top = held.find((card) => card.is_top);
  return top
    ? { ...top, type: label, color: cardMeta(label).color, held }
    : { type: label, color: cardMeta(label).color, name: '暂无', effect_summary: '当前没有持有履历', held: [] };
});
const buildDemoDesk = () => {
  const opportunity = demoCardsFromCatalog(demoShowcase.opportunity);
  const fate = demoCardsFromCatalog(demoShowcase.fate);
  const eventCards = demoCardsFromCatalog(demoShowcase.eventHand);
  const debuffCard = cardCatalogById?.[demoShowcase.debuff];
  return {
    phase: demoGame.phase, round: demoGame.round, connection: '静态预览', playerIdentity: demoGame.playerIdentity,
    opportunity, fate, goals: demoGame.goals,
    events: eventCards,
    debuff: debuffCard ? { name: debuffCard.name, turns: '示例状态', effect: debuffCard.effect_summary } : null,
    logs: demoGame.logs, dice: demoGame.dice, maxDiceCount: demoGame.maxDiceCount, resume: buildDemoResume(),
  };
};
const DETAIL_FIELD_LABELS = {
  provide: '提供资源', extra_die: '额外骰子', reroll: '额外重掷', upkeep: '维护',
  sub_buy: '支付替代', upkeep_discount: '维护减免', upkeep_sub: '维护替代',
  shorten_debuff: '缩短逆境', cancel_debuff_once: '取消逆境（每局一次）',
  bl_convert: '厄运转换', convert_turn: '资源转换', flex: '可变产出',
  temp_res: '临时资源', temp_gl: '临时好运', extra_reroll_rounds: '额外重掷轮数',
  protect_market: '市场保护', temp_dice: '临时骰子', upkeep_reduce: '维护费减免',
  pre_cancel_debuff: '预先取消逆境', cancel_debuff: '取消逆境',
  discount_type: '购买折扣类别', abebe: '阿贝贝', extra_cost: '额外成本',
  block_event: '事件限制', reroll_delta: '重掷轮数变化', gl_threshold: '好运门槛',
  virtual_bl: '额外厄运', block_type: '取得限制', immediate: '取得时立即',
  first_discount: '首购折扣', purchase_limit: '购买限制', discount: '购买折扣',
  dice_delta: '骰子变化', lock_reroll_on_bl: 'BL 锁重掷',
  wildcard_normal: '好运当普通资源',
};
const DETAIL_KEY_LABELS = {
  type: '类别', sym: '符号', n: '数量', from: '从', to: '到', reduce: '减少',
  cost: '成本', filter: '限定',
};
// 资源符号（H/K/R/M/GL/BL，M=金钱）与牌类型 code（H/K/R/W/P/E/C/D/F）是两套语义。
const RESOURCE_META = {
  H: { zh: '健康', icon: DICE_ICONS.H }, K: { zh: '知识', icon: DICE_ICONS.K },
  R: { zh: '关系', icon: DICE_ICONS.R }, M: { zh: '金钱', icon: DICE_ICONS.M },
  GL: { zh: '好运', icon: DICE_ICONS.GL }, BL: { zh: '厄运', icon: DICE_ICONS.BL },
};
const CARD_TYPE_ZH = {
  H: '健康牌', K: '知识牌', R: '关系牌', W: '工作牌', P: '财产牌',
  E: '事件牌', C: '童年牌', D: '逆境牌', F: '命运牌',
};
const zhCardType = (code) => CARD_TYPE_ZH[code] || code;
const resourceChip = (sym, n) => RESOURCE_META[sym] ? `<span class="res-chip"><img class="res-icon" src="${RESOURCE_META[sym].icon}" alt="${RESOURCE_META[sym].zh}" />${RESOURCE_META[sym].zh}${n ? `×${n}` : ''}</span>` : escapeHtml(String(sym));
const formatCostChips = (cost) => { const entries = Object.entries(cost || {}); return entries.length ? entries.map(([sym, n]) => resourceChip(sym, n)).join(' ') : '—'; };
const DETAIL_IMMEDIATE_ZH = { set_active: '立即置顶生效一张履历', replace_goal: '立即替换一张人生目标' };
const DETAIL_FILTER_ZH = { non_bl: '1 颗非厄运骰', bl: '1 颗厄运骰', non_gl_bl: '1 颗非好运/厄运骰' };
const isResourcePair = (item) => Array.isArray(item) && item.length === 2 && typeof item[0] === 'string' && typeof item[1] === 'number' && RESOURCE_META[item[0]];
const formatDetailValue = (key, value) => {
  if (value === null || value === undefined) return key === 'scope' ? '任意类' : '—';
  if (value === true) return '是';
  if (value === false) return '否';
  if (typeof value === 'number') return String(value);
  if (Array.isArray(value)) {
    if (isResourcePair(value)) return resourceChip(value[0], value[1]);
    if (value.every(isResourcePair)) return value.map(([sym, n]) => resourceChip(sym, n)).join('、');
    if (value.every((item) => typeof item === 'string' && RESOURCE_META[item])) return value.map((sym) => resourceChip(sym)).join('、');
    if (key === 'scope') return `适用于 ${value.map(zhCardType).join('/')}`;
    return value.map((item) => formatDetailValue(key, item)).join('；');
  }
  if (typeof value === 'object') {
    const parts = [];
    for (const [k, item] of Object.entries(value)) {
      if (k === 'scope') { parts.push(item === null ? '适用 任意类' : formatDetailValue('scope', item)); continue; }
      if (item === null) continue;
      if (RESOURCE_META[k] && typeof item === 'number') { parts.push(resourceChip(k, item)); continue; }
      parts.push(`${DETAIL_KEY_LABELS[k] || escapeHtml(k)} ${formatDetailValue(k, item)}`);
    }
    return parts.join('，');
  }
  if (key === 'immediate') return DETAIL_IMMEDIATE_ZH[value] || escapeHtml(value);
  if (key === 'filter') return DETAIL_FILTER_ZH[value] || escapeHtml(value);
  if (value === 'any') return key === 'sym' ? '任意符号' : '任意普通资源';
  if (RESOURCE_META[value] && ['sym', 'from', 'to'].includes(key)) return resourceChip(value);
  if (CARD_TYPE_ZH[value] && ['type', 'block_type', 'discount_type'].includes(key)) return zhCardType(value);
  return escapeHtml(value);
};
// 正式 effect_summary 文案中的资源/骰面符号做纯展示替换（icon 化），不改动正文语义。
const formatEffectText = (text) => {
  if (!text) return '暂无';
  return escapeHtml(text)
    .replace(/([HKRMGLBL])×(\d+)/g, (m, sym, n) => resourceChip(sym, Number(n)))
    .replace(/\b(GL|BL|[HKRM])\b/g, (m, sym) => resourceChip(sym));
};
const detailRow = (label, valueHtml, muted = false) => `<div class="fact-block"><p class="fact-label">${escapeHtml(label)}</p><p class="fact-value${muted ? ' detail-none' : ''}">${valueHtml}</p></div>`;
const cardDetailModal = element('#card-detail-modal');
const openCardDetail = (cardId) => {
  const detail = cardCatalogById && cardCatalogById[cardId];
  if (!detail) return;
  const meta = cardMeta(detail.type);
  element('#card-detail-kind').textContent = `${meta.zh} · ${meta.label}`;
  element('#card-detail-title').textContent = detail.name;
  element('#card-detail-card').innerHTML = renderCard(detail, { detail: true });
  const facts = [];
  if (detail.stage) facts.push(detailRow('阶段', escapeHtml(STAGE_LABELS[detail.stage] || detail.stage)));
  facts.push(detailRow('成本', formatCostChips(detail.cost)));
  if (detail.vp) facts.push(detailRow('计分', `+${detail.vp}`));
  facts.push(detail.effect_summary ? detailRow('效果', formatEffectText(detail.effect_summary)) : detailRow('效果', '暂无摘要', true));
  element('#card-detail-facts').innerHTML = facts.join('');
  const rules = Object.entries(detail.details || {}).map(([key, value]) => detailRow(DETAIL_FIELD_LABELS[key] || escapeHtml(key), formatDetailValue(key, value)));
  element('#card-detail-rules').innerHTML = rules.join('');
  element('#card-detail-rules').hidden = rules.length === 0;
  cardDetailModal.showModal();
};
element('.card-detail-modal .modal-close').addEventListener('click', () => cardDetailModal.close());
cardDetailModal.addEventListener('click', (event) => { if (event.target === cardDetailModal) cardDetailModal.close(); });
const openCardDetailWhenReady = async (cardId) => {
  if (!cardCatalogById?.[cardId] && !(await loadCardCatalog())) return;
  openCardDetail(cardId);
};
const bindCardDetail = (selector) => element(selector).addEventListener('click', (event) => { if (event.target.closest('.more-button')) return; const cardEl = event.target.closest('.game-card[data-card-id]'); if (cardEl) void openCardDetailWhenReady(cardEl.dataset.cardId); });
bindCardDetail('#opportunity-cards'); bindCardDetail('#fate-cards'); bindCardDetail('#resume-cards'); bindCardDetail('#modal-cards');
element('#events-list').addEventListener('click', (event) => { const button = event.target.closest('.event-more'); const card = button && displayedEvents[button.dataset.index]; if (card?.card_id) void openCardDetailWhenReady(card.card_id); });

const cardCatalogModal = element('#card-catalog-modal');
const CATALOG_TYPES = ['H', 'K', 'R', 'W', 'P', 'E', 'C', 'D', 'F'];
let catalogFilter = 'all';
const renderCardCatalog = () => {
  const cards = Object.values(cardCatalogById || {});
  const filtered = catalogFilter === 'all' ? cards : cards.filter((card) => card.type === catalogFilter);
  element('#card-catalog-count').textContent = `${filtered.length} 张`;
  element('#card-catalog-filters').innerHTML = [
    ['all', '全部'],
    ...CATALOG_TYPES.map((type) => [type, TYPE_META[type].zh]),
  ].map(([type, label]) => `<button class="catalog-filter${catalogFilter === type ? ' is-active' : ''}" type="button" data-type="${type}" aria-pressed="${catalogFilter === type}">${label}</button>`).join('');
  element('#card-catalog-cards').innerHTML = filtered.map((card) => renderCard(card, { typeLabel: cardMeta(card.type).zh })).join('');
};
const openCardCatalog = async () => {
  element('#card-catalog-status').textContent = cardCatalogById ? '' : '正在读取正式卡牌…';
  if (!cardCatalogModal.open) cardCatalogModal.showModal();
  const loaded = await loadCardCatalog();
  if (!loaded) { element('#card-catalog-status').textContent = '暂时无法读取卡牌图鉴，主桌面仍可正常使用。'; return; }
  element('#card-catalog-status').textContent = '';
  renderCardCatalog();
};
element('#open-card-catalog').addEventListener('click', openCardCatalog);
element('.card-catalog-modal .modal-close').addEventListener('click', () => cardCatalogModal.close());
cardCatalogModal.addEventListener('click', (event) => { if (event.target === cardCatalogModal) cardCatalogModal.close(); });
element('#card-catalog-filters').addEventListener('click', (event) => { const button = event.target.closest('.catalog-filter'); if (!button) return; catalogFilter = button.dataset.type; renderCardCatalog(); });
bindCardDetail('#card-catalog-cards');
const rulesModal = element('#rules-modal');
element('#open-rules').addEventListener('click', () => rulesModal.showModal());
element('.rules-modal .modal-close').addEventListener('click', () => rulesModal.close());
rulesModal.addEventListener('click', (event) => { if (event.target === rulesModal) rulesModal.close(); });
const goalModal = element('#goal-modal');
element('#goals-list').addEventListener('click', (event) => { const button = event.target.closest('.goal-more'); if (!button) return; const goal = displayedGoals[button.dataset.index]; if (!goal) return; element('#goal-title').textContent = goal[0]; element('#goal-scoring').textContent = goal[1] || '暂无计分说明'; goalModal.showModal(); });
element('.goal-modal .modal-close').addEventListener('click', () => goalModal.close());
goalModal.addEventListener('click', (event) => { if (event.target === goalModal) goalModal.close(); });

const sessionId = new URLSearchParams(window.location.search).get('session_id');
const spectatorBase = 'http://127.0.0.1:8765';
let pollTimer = null;
let pollInFlight = false;
let hasSnapshot = false;
let hasDiceBaseline = false;
let lastSeenRollRevision = null;
let rebaselineDiceAfterFailure = false;
let latestSpectatorDice = null;
let latestSpectatorMaxDiceCount = DICE_SLOT_COUNT;
let diceAnimationInterval = null;
let diceAnimationEndTimer = null;
let diceAnimationToken = 0;
const DICE_ROLL_DURATION_MS = 420;
const DICE_ROLL_FRAME_MS = 70;
const diceAnimationActive = () => diceAnimationEndTimer !== null;
const cancelDiceAnimation = () => {
  diceAnimationToken += 1;
  if (diceAnimationInterval !== null) window.clearInterval(diceAnimationInterval);
  if (diceAnimationEndTimer !== null) window.clearTimeout(diceAnimationEndTimer);
  diceAnimationInterval = null;
  diceAnimationEndTimer = null;
};
const startDiceRollAnimation = () => {
  cancelDiceAnimation();
  const token = diceAnimationToken;
  let frame = 0;
  const renderFrame = () => { if (token === diceAnimationToken && latestSpectatorDice) renderDice(latestSpectatorDice, latestSpectatorMaxDiceCount, frame); };
  renderFrame();
  diceAnimationInterval = window.setInterval(() => { frame += 1; renderFrame(); }, DICE_ROLL_FRAME_MS);
  diceAnimationEndTimer = window.setTimeout(() => {
    if (token !== diceAnimationToken) return;
    window.clearInterval(diceAnimationInterval);
    diceAnimationInterval = null;
    diceAnimationEndTimer = null;
    if (latestSpectatorDice) renderDice(latestSpectatorDice, latestSpectatorMaxDiceCount);
  }, DICE_ROLL_DURATION_MS);
};
const setConnectionLabel = (label) => { const status = element('#round-status'); status.textContent = status.textContent.replace(/· (静态预览|连接中|已连接|已结束|session 不存在|bridge 不可达)$/, `· ${label}`); };
const pollSnapshot = async () => {
  if (pollInFlight) return;
  pollInFlight = true;
  if (!hasSnapshot) setConnectionLabel('连接中');
  try {
    const response = await fetch(`${spectatorBase}/spectator/sessions/${encodeURIComponent(sessionId)}`);
    if (!response.ok) {
      rebaselineDiceAfterFailure = true;
      setConnectionLabel(response.status === 404 ? 'session 不存在' : 'bridge 不可达');
      return;
    }
    const desk = snapshotToDesk(await response.json());
    const revision = Number.isInteger(desk.rollRevision) ? desk.rollRevision : null;
    const establishBaseline = !hasDiceBaseline || rebaselineDiceAfterFailure;
    let shouldAnimate = false;
    latestSpectatorDice = desk.dice;
    latestSpectatorMaxDiceCount = desk.maxDiceCount;
    if (establishBaseline) {
      hasDiceBaseline = true;
      lastSeenRollRevision = revision;
      rebaselineDiceAfterFailure = false;
    } else if (revision !== null && lastSeenRollRevision !== null && revision > lastSeenRollRevision) {
      lastSeenRollRevision = revision;
      shouldAnimate = true;
    } else if (revision !== null && lastSeenRollRevision === null) {
      lastSeenRollRevision = revision;
    }
    renderDesk(desk, { renderDicePool: !diceAnimationActive() });
    if (shouldAnimate) startDiceRollAnimation();
    hasSnapshot = true;
  } catch (_) {
    rebaselineDiceAfterFailure = true;
    setConnectionLabel('bridge 不可达');
  } finally { pollInFlight = false; pollTimer = window.setTimeout(pollSnapshot, 1200); }
};
renderDemo();
if (sessionId) { pollSnapshot(); loadCardCatalog(); }
else { void loadCardCatalog().then((loaded) => { if (loaded) renderDemo(); }); }

// 保留 1080×1080 内部设计画布，只按浏览器可用高度整体缩放。
const DESIGN_BOARD_SIZE = 1080;
const boardSpace = document.querySelector('.board-space');
const boardCanvas = document.querySelector('.page-shell');
const syncBoardScale = () => { const top = boardSpace.getBoundingClientRect().top; const availableHeight = Math.max(0, window.innerHeight - top - 16); const scale = Math.min(1, availableHeight / DESIGN_BOARD_SIZE); boardSpace.style.width = `${DESIGN_BOARD_SIZE * scale}px`; boardSpace.style.height = `${DESIGN_BOARD_SIZE * scale}px`; boardCanvas.style.setProperty('--board-scale', scale); document.documentElement.style.setProperty('--board-scale', scale); };
window.addEventListener('resize', syncBoardScale);
syncBoardScale();
