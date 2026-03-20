// Stock Alert - クライアントロジック

const API_KEYWORDS = '/api/keywords';
const API_ALERTS = '/api/alerts';

const keywordInput = document.getElementById('keyword-input');
const categoryInput = document.getElementById('category-input');
const addBtn = document.getElementById('add-keyword-btn');
const keywordList = document.getElementById('keyword-list');
const categoryFilters = document.getElementById('category-filters');
const alertFeed = document.getElementById('alert-feed');
const connectionStatus = document.getElementById('connection-status');
const soundToggle = document.getElementById('sound-toggle');
const alertKeywordFilter = document.getElementById('alert-keyword-filter');
const markAllReadBtn = document.getElementById('mark-all-read-btn');
const statKeywords = document.getElementById('stat-keywords');
const statUnread = document.getElementById('stat-unread');
const statTotal = document.getElementById('stat-total');

// ── 状態 ────────────────────────────────────────────────

let soundEnabled = true;
let ws = null;
let allKeywords = [];
let allAlerts = [];
let selectedCategory = '';  // 空文字 = 全表示

// ── ユーティリティ ──────────────────────────────────────

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function isPositiveAlert(category) {
    return category && category !== '注意';
}

function highlightKeyword(text, keyword, positive) {
    if (!keyword) return escapeHtml(text);
    const escaped = escapeHtml(text);
    const re = new RegExp(`(${keyword.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
    const cls = positive
        ? 'bg-red-500/30 text-red-300 rounded px-0.5'
        : 'bg-emerald-500/30 text-emerald-300 rounded px-0.5';
    return escaped.replace(re, `<mark class="${cls}">$1</mark>`);
}

function timeAgo(dateStr) {
    const d = new Date(dateStr.replace(' ', 'T'));
    const now = new Date();
    const sec = Math.floor((now - d) / 1000);
    if (sec < 0) return '今';
    if (sec < 60) return `${sec}秒前`;
    if (sec < 3600) return `${Math.floor(sec / 60)}分前`;
    if (sec < 86400) return `${Math.floor(sec / 3600)}時間前`;
    return dateStr.split(' ')[1] || dateStr;
}

// ── 統計更新 ────────────────────────────────────────────

function updateStats() {
    const activeCount = allKeywords.filter(k => k.is_active).length;
    const unreadCount = allAlerts.filter(a => !a.is_read).length;
    statKeywords.textContent = activeCount;
    statUnread.textContent = unreadCount;
    statTotal.textContent = allAlerts.length;
}

// ── アラート音（Web Audio API） ─────────────────────────

function playAlertSound() {
    if (!soundEnabled) return;
    try {
        const ctx = new AudioContext();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.frequency.value = 880;
        osc.type = 'sine';
        gain.gain.setValueAtTime(0.3, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.5);
        osc.start();
        osc.stop(ctx.currentTime + 0.5);
    } catch (e) {
        // AudioContext未対応環境では無視
    }
}

// ── ブラウザ通知 ────────────────────────────────────────

function requestNotificationPermission() {
    if ('Notification' in window && Notification.permission === 'default') {
        Notification.requestPermission();
    }
}

function showBrowserNotification(a) {
    if ('Notification' in window && Notification.permission === 'granted') {
        const n = new Notification(`[${a.keyword}] キーワードヒット`, {
            body: a.title,
            tag: `alert-${a.id}`,
            requireInteraction: false,
        });
        n.onclick = () => {
            window.open(a.url, '_blank');
            n.close();
        };
    }
}

// ── キーワード管理 ──────────────────────────────────────

function getCategories() {
    const cats = new Set();
    for (const kw of allKeywords) {
        if (kw.category) cats.add(kw.category);
    }
    return [...cats].sort();
}

function renderCategoryFilters() {
    const cats = getCategories();
    if (cats.length === 0) {
        categoryFilters.innerHTML = '';
        return;
    }
    categoryFilters.innerHTML = `
        <button class="cat-filter text-xs rounded px-2 py-0.5 transition-colors ${
            selectedCategory === '' ? 'bg-emerald-600 text-white' : 'bg-gray-700 text-gray-400 hover:text-gray-200'
        }" data-cat="">すべて</button>
        ${cats.map(c => `
            <button class="cat-filter text-xs rounded px-2 py-0.5 transition-colors ${
                selectedCategory === c ? 'bg-emerald-600 text-white' : 'bg-gray-700 text-gray-400 hover:text-gray-200'
            }" data-cat="${escapeHtml(c)}">${escapeHtml(c)}</button>
        `).join('')}`;
}

function renderKeywords() {
    const filtered = selectedCategory
        ? allKeywords.filter(kw => kw.category === selectedCategory)
        : allKeywords;

    if (filtered.length === 0) {
        keywordList.innerHTML = '<li class="text-gray-500 text-sm text-center py-4">キーワードが未登録です</li>';
        return;
    }

    keywordList.innerHTML = filtered.map(kw => `
        <li class="flex items-center justify-between bg-gray-700/50 rounded px-3 py-2 group" data-id="${kw.id}">
            <div class="flex items-center gap-2 min-w-0">
                <button class="toggle-btn flex-shrink-0 w-3.5 h-3.5 rounded-full border-2 ${
                    kw.is_active
                        ? 'bg-emerald-500 border-emerald-500'
                        : 'bg-transparent border-gray-500'
                }" title="${kw.is_active ? '無効にする' : '有効にする'}"></button>
                <span class="text-sm truncate ${kw.is_active ? '' : 'line-through text-gray-500'}">${escapeHtml(kw.keyword)}</span>
                ${kw.category ? `<span class="text-[10px] bg-gray-600 rounded px-1 py-0.5 text-gray-400 flex-shrink-0">${escapeHtml(kw.category)}</span>` : ''}
            </div>
            <button class="delete-btn text-gray-500 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity text-xs ml-2 flex-shrink-0"
                title="削除">✕</button>
        </li>
    `).join('');
}

async function fetchKeywords() {
    const res = await fetch(API_KEYWORDS);
    allKeywords = await res.json();
    renderCategoryFilters();
    renderKeywords();
    updateAlertKeywordFilter();
    updateStats();
}

async function addKeyword() {
    const keyword = keywordInput.value.trim();
    if (!keyword) return;

    const res = await fetch(API_KEYWORDS, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            keyword,
            category: categoryInput.value.trim(),
        }),
    });

    if (res.status === 409) {
        showToast('このキーワードは既に登録されています', 'warn');
        return;
    }
    if (!res.ok) {
        showToast('登録に失敗しました', 'error');
        return;
    }

    keywordInput.value = '';
    categoryInput.value = '';
    keywordInput.focus();
    showToast(`「${keyword}」を追加しました`);
    await fetchKeywords();
}

async function toggleKeyword(id, currentActive) {
    await fetch(`${API_KEYWORDS}/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_active: !currentActive }),
    });
    await fetchKeywords();
}

async function deleteKeyword(id) {
    if (!confirm('このキーワードを削除しますか？')) return;
    await fetch(`${API_KEYWORDS}/${id}`, { method: 'DELETE' });
    await fetchKeywords();
}

// ── アラート表示 ────────────────────────────────────────

function updateAlertKeywordFilter() {
    const current = alertKeywordFilter.value;
    const keywords = [...new Set(allAlerts.map(a => a.keyword))].sort();
    alertKeywordFilter.innerHTML = '<option value="">すべて</option>' +
        keywords.map(k => `<option value="${escapeHtml(k)}" ${k === current ? 'selected' : ''}>${escapeHtml(k)}</option>`).join('');
}

function renderAlertItem(a) {
    const positive = isPositiveAlert(a.category);
    const title = highlightKeyword(a.title, a.keyword, positive);

    // ポジティブ: 赤系、注意系: 緑系
    const borderColor = a.is_read ? 'border-gray-600' : (positive ? 'border-red-500' : 'border-emerald-500');
    const bgColor = a.is_read ? 'bg-gray-700/30' : 'bg-gray-700/70';
    const badgeBg = positive ? 'bg-red-600/80' : 'bg-emerald-600/80';
    const hoverColor = positive ? 'hover:text-red-400' : 'hover:text-emerald-400';
    const checkColor = positive ? 'hover:text-red-400' : 'hover:text-emerald-400';

    return `
        <div class="alert-item rounded px-4 py-3 border-l-4 transition-opacity ${bgColor} ${borderColor} ${a.is_read ? 'opacity-50' : ''}" data-alert-id="${a.id}">
            <div class="flex items-start justify-between gap-2">
                <div class="min-w-0">
                    <div class="flex items-center gap-2 mb-1 flex-wrap">
                        <span class="text-[10px] font-semibold ${badgeBg} text-white rounded px-1.5 py-0.5">${escapeHtml(a.keyword)}</span>
                        <span class="text-[10px] text-gray-500">${escapeHtml(a.source)}</span>
                        <span class="text-[10px] text-gray-600">${timeAgo(a.matched_at)}</span>
                    </div>
                    <a href="${escapeHtml(a.url)}" target="_blank" rel="noopener"
                       class="text-sm text-gray-200 ${hoverColor} transition-colors leading-snug block">
                        ${title}
                    </a>
                </div>
                ${!a.is_read ? `<button class="mark-read-btn text-gray-600 ${checkColor} flex-shrink-0 mt-1 text-sm" title="既読にする">✓</button>` : ''}
            </div>
        </div>`;
}

function renderAlerts() {
    const filterKeyword = alertKeywordFilter.value;
    const filtered = filterKeyword
        ? allAlerts.filter(a => a.keyword === filterKeyword)
        : allAlerts;

    if (filtered.length === 0) {
        alertFeed.innerHTML = '<p class="text-gray-500 text-sm text-center py-8">アラートはまだありません</p>';
        return;
    }
    alertFeed.innerHTML = filtered.map(renderAlertItem).join('');
}

function prependAlerts(newAlerts) {
    // allAlertsの先頭に追加
    allAlerts = [...newAlerts, ...allAlerts];
    updateStats();
    updateAlertKeywordFilter();

    // DOM更新
    const placeholder = alertFeed.querySelector('p.text-gray-500');
    if (placeholder) placeholder.remove();

    const filterKeyword = alertKeywordFilter.value;
    for (const a of newAlerts) {
        if (filterKeyword && a.keyword !== filterKeyword) continue;
        const div = document.createElement('div');
        div.innerHTML = renderAlertItem(a);
        const el = div.firstElementChild;
        el.classList.add(isPositiveAlert(a.category) ? 'alert-new-positive' : 'alert-new');
        alertFeed.prepend(el);
    }
}

async function fetchAlerts() {
    const res = await fetch(API_ALERTS);
    allAlerts = await res.json();
    updateAlertKeywordFilter();
    renderAlerts();
    updateStats();
}

async function markRead(alertId) {
    await fetch(`${API_ALERTS}/${alertId}/read`, { method: 'PUT' });
    const a = allAlerts.find(x => x.id == alertId);
    if (a) a.is_read = true;
    renderAlerts();
    updateStats();
}

async function markAllRead() {
    const unread = allAlerts.filter(a => !a.is_read);
    if (unread.length === 0) return;
    await fetch(`${API_ALERTS}/read-all`, { method: 'PUT' });
    allAlerts.forEach(a => a.is_read = true);
    renderAlerts();
    updateStats();
    showToast(`${unread.length}件を既読にしました`);
}

// ── トースト通知 ────────────────────────────────────────

function showToast(message, type = 'info') {
    const colors = { info: 'bg-emerald-600', warn: 'bg-yellow-600', error: 'bg-red-600' };
    const toast = document.createElement('div');
    toast.className = `fixed bottom-4 right-4 ${colors[type]} text-white text-sm px-4 py-2 rounded-lg shadow-lg z-50 toast-enter`;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => {
        toast.classList.add('toast-exit');
        setTimeout(() => toast.remove(), 300);
    }, 2500);
}

// ── WebSocket接続 ───────────────────────────────────────

function setConnectionStatus(status) {
    const dot = connectionStatus.querySelector('.status-dot');
    const text = connectionStatus.querySelector('.status-text');
    const dotColors = { connected: 'bg-emerald-400', connecting: 'bg-yellow-400', disconnected: 'bg-red-400' };
    const labels = { connected: '接続中', connecting: '再接続中...', disconnected: '未接続' };
    dot.className = `status-dot w-2 h-2 rounded-full inline-block ${dotColors[status]}`;
    text.textContent = labels[status];
}

function connectWebSocket() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${protocol}//${location.host}/ws`;

    setConnectionStatus('connecting');
    ws = new WebSocket(url);

    let pingInterval = null;

    ws.onopen = () => {
        setConnectionStatus('connected');
        // 30秒ごとにpingを送信して接続を維持
        pingInterval = setInterval(() => {
            if (ws.readyState === WebSocket.OPEN) {
                ws.send('ping');
            }
        }, 30000);
    };

    ws.onmessage = (event) => {
        if (event.data === 'pong') return;
        const msg = JSON.parse(event.data);
        if (msg.type === 'new_alerts' && msg.alerts.length > 0) {
            prependAlerts(msg.alerts);
            playAlertSound();
            showBrowserNotification(msg.alerts[0]);
            showToast(`${msg.alerts.length}件の新着アラート`);
        }
    };

    ws.onclose = () => {
        setConnectionStatus('disconnected');
        if (pingInterval) clearInterval(pingInterval);
        setTimeout(connectWebSocket, 5000);
    };

    ws.onerror = () => ws.close();
}

// ── イベントハンドラ ────────────────────────────────────

addBtn.addEventListener('click', addKeyword);

keywordInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') addKeyword();
});

keywordList.addEventListener('click', (e) => {
    const li = e.target.closest('li[data-id]');
    if (!li) return;
    const id = li.dataset.id;

    if (e.target.closest('.delete-btn')) {
        deleteKeyword(id);
    } else if (e.target.closest('.toggle-btn')) {
        const isActive = e.target.closest('.toggle-btn').classList.contains('bg-emerald-500');
        toggleKeyword(id, isActive);
    }
});

categoryFilters.addEventListener('click', (e) => {
    const btn = e.target.closest('.cat-filter');
    if (!btn) return;
    selectedCategory = btn.dataset.cat;
    renderCategoryFilters();
    renderKeywords();
});

alertFeed.addEventListener('click', (e) => {
    const btn = e.target.closest('.mark-read-btn');
    if (!btn) return;
    const item = btn.closest('[data-alert-id]');
    if (item) markRead(item.dataset.alertId);
});

alertKeywordFilter.addEventListener('change', renderAlerts);

markAllReadBtn.addEventListener('click', markAllRead);

soundToggle.addEventListener('click', () => {
    soundEnabled = !soundEnabled;
    soundToggle.textContent = soundEnabled ? '🔔' : '🔕';
    soundToggle.title = soundEnabled ? 'アラート音ON' : 'アラート音OFF';
});

// ── 初期化 ──────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    fetchKeywords();
    fetchAlerts();
    connectWebSocket();
    requestNotificationPermission();
});
