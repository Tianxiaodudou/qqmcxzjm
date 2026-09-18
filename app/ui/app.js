/* ==========================================================
   QQ音乐下载器 · 前端主逻辑
   凭证只存在于后端；前端只调用本项目 /api/*
   ========================================================== */

const BASE = (() => {
  const m = location.pathname.match(/^(.*?\/app\/[^/]+)(?:\/|$)/);
  return m ? m[1] : '';
})();
const API = `${BASE}/api`;
const STATIC = `${BASE}/static`;

/* 目录授权流程（宿主内直接选择 / 独立浏览器走 openAppAuth 回调） */
import { requestUserDirectory, listenForAuthResult } from './js/auth-callback.js';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
/* 安全绑定：元素缺失只告警不抛错，避免一处失误中断后续全部绑定 */
const on = (sel, ev, fn) => {
  const node = $(sel);
  if (node) node.addEventListener(ev, fn);
  else console.warn('[bind] 缺少元素', sel);
  return node;
};

const state = {
  view: 'home',
  loggedIn: false,
  home: { songlists: [], newsongs: [], loading: false, sel: new Set(), loadedAt: 0,
          guess: [], guessSel: new Set(), guessError: '',
          radar: [], radarSel: new Set(), radarError: '',
          panel: '' },
  search: { keyword: '', type: 'song', page: 1, items: [], songlists: [], sel: new Set(), loading: false, hasMore: false },
  fav: { songlists: [], songs: [], page: 1, sel: new Set(), loading: false, hasMore: false, loadedAt: 0 },
  songlist: { id: 0, info: {}, songs: [], page: 1, sel: new Set(), hasMore: false, loading: false },
  tasks: { items: [], counts: {}, timer: null, loadedAt: 0 },
  history: { items: [], record: new Map(), cache: new Map(), observer: null, timer: null, total: 0, loadedAt: 0 },
  settings: {},
  settingsLoadedAt: 0,
  logs: { items: [], counts: {}, records: [], loadedAt: 0, tab: 'logs', level: '' },
  accounts: { items: [], current: '' },
  qualities: [],
  login: { mode: '', sessionId: '', busy: false, timer: null },
  player: { songmid: '', lyrics: [], index: -1, timer: null, ready: false, song: null },
};

/* ---------------- 基础工具 ---------------- */
function esc(text) {
  return String(text == null ? '' : text)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function toast(message, type = 'info', duration = 3200) {
  const wrap = $('#toast-wrap');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = message;
  wrap.appendChild(el);
  setTimeout(() => el.remove(), duration);
}

let loadingCount = 0;

/* ---------------- 关于：开发者 / 发布者 ---------------- */
const ABOUT = {
  developer: { name: 'Tianxiaodudou', url: 'https://github.com/Tianxiaodudou?tab=repositories' },
  publisher: { name: 'A鱼儿', wechat: 'telegram96' },
};

/** 复制到剪贴板：飞牛窗口多是 http 环境（无 navigator.clipboard），带 execCommand 兜底 */
async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (err) { /* 继续走兜底 */ }
  try {
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand('copy');
    area.remove();
    return ok;
  } catch (err) {
    return false;
  }
}

/**
 * 发布者：弹窗展示微信号，并提供一键复制（不做微信客户端唤起）。
 */
function openPublisher() {
  const modal = $('#modal-wechat');
  const idEl = $('#wechat-id');
  if (idEl) idEl.textContent = ABOUT.publisher.wechat;
  if (modal) modal.classList.remove('hidden');
  else toast(`发布者微信号：${ABOUT.publisher.wechat}`, 'success', 5000);
}

async function copyPublisherWechat() {
  const id = ABOUT.publisher.wechat;
  const ok = await copyText(id);
  toast(ok ? `微信号 ${id} 已复制，打开微信搜索添加` : `微信号：${id}`, 'success', 4000);
}

/** 品牌区与设置页的开发者 / 发布者入口统一挂载 */
function bindAbout() {
  $$('[data-about="developer"]').forEach((el) => {
    el.setAttribute('href', ABOUT.developer.url);
    el.setAttribute('target', '_blank');
    el.setAttribute('rel', 'noopener noreferrer');
    el.textContent = ABOUT.developer.name;
    el.title = `打开 GitHub 主页：${ABOUT.developer.url}`;
  });
  $$('[data-about="publisher"]').forEach((el) => {
    el.textContent = ABOUT.publisher.name;
    el.title = `微信号：${ABOUT.publisher.wechat}（点击查看并复制）`;
    el.addEventListener('click', (ev) => {
      ev.preventDefault();
      openPublisher();
    });
  });
  $$('[data-about="publisher-wechat"]').forEach((el) => {
    el.textContent = ABOUT.publisher.wechat;
  });
  on('#btn-copy-wechat', 'click', copyPublisherWechat);
  bindAboutLinks();
}

function setLoading(on) {
  loadingCount = Math.max(0, loadingCount + (on ? 1 : -1));
  $('#global-loading').classList.toggle('hidden', loadingCount === 0);
}

function fmtDuration(seconds) {
  const s = Number(seconds) || 0;
  return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
}

function fmtSize(bytes) {
  const b = Number(bytes) || 0;
  if (!b) return '—';
  const units = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  let v = b;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function fmtTime(ts) {
  const d = new Date((Number(ts) || 0) * 1000);
  if (!d.getTime()) return '—';
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function fmtClock(seconds) {
  const s = Math.max(0, Number(seconds) || 0);
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
}

function coverUrl(pmid, size = 500) {
  return pmid ? `https://y.gtimg.cn/music/photo_new/T002R${size}x${size}M000${pmid}.jpg` : '';
}

function songlistCover(url) {
  if (!url) return '';
  return url.replace(/^http:/, 'https:');
}

/* ---------------- 网络层 ---------------- */
function normalizeError(res, data) {
  const payload = data || {};
  const err = payload.error || payload;
  const message = err.message || err.detail || (typeof err === 'string' ? err : '') || `请求失败（${res ? res.status : '网络异常'}）`;
  return { code: err.code || (res ? `http_${res.status}` : 'network'), message };
}

async function api(path, { method = 'GET', body, query } = {}) {
  let url = API + path;
  if (query) {
    const entries = Object.entries(query).filter(([, v]) => v !== undefined && v !== null && v !== '');
    if (entries.length) url += (url.includes('?') ? '&' : '?') + new URLSearchParams(entries).toString();
  }
  const init = { method, headers: {} };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(url, init);
  } catch (e) {
    throw { code: 'network', message: '无法连接到应用后端，请稍后重试' };
  }
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  if (!res.ok || (data && data.ok === false)) throw normalizeError(res, data);
  return data || { ok: true };
}

/** 统一处理需要登录的接口报错：凭证过期时提示重新登录 */
function handleError(err, { silent = false } = {}) {
  const code = err && err.code;
  const message = (err && err.message) || '操作失败';
  if (code === 'credential_expired') {
    setLoggedIn(false);
    toast('登录已过期，请重新登录', 'error', 4200);
    return;
  }
  if (!silent) toast(message, 'error', 4200);
}

async function withLoading(fn) {
  setLoading(true);
  try {
    return await fn();
  } finally {
    setLoading(false);
  }
}

/* ---------------- 登录态 ---------------- */
function setLoggedIn(loggedIn) {
  const was = state.loggedIn;
  state.loggedIn = !!loggedIn;
  const dot = $('#account-status .dot');
  dot.classList.toggle('online', state.loggedIn);
  dot.classList.toggle('offline', !state.loggedIn);
  $('#account-text').textContent = state.loggedIn ? '已登录' : '未登录';
  if (was !== state.loggedIn || !state.accounts.loadedAt) loadAccounts();
  $('#btn-login').classList.toggle('hidden', state.loggedIn);
  $('#btn-logout').classList.toggle('hidden', !state.loggedIn);
  if (!was && state.loggedIn) schedulePrefetch();   // 刚登录：空闲时预取各页数据
}

/** 闲时预加载：把「切页才开始请求」变成「切页直接显示」。
 *  串行 + 间隔执行，避免并发打满；每个页面只取首屏（第一页）数据，不预取后续分页。 */
async function prefetchAllViews() {
  const FRESH_MS = 60000;
  // 首屏初始加载可能还在路上：先等它落地再判断「新鲜度」，否则同一份数据会被请求两遍
  const fresh = (loading, loadedAt) => !loading && !!loadedAt && Date.now() - loadedAt < FRESH_MS;
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline && (state.home.loading || state.fav.loading)) {
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  // 已经渲染过且数据新鲜（60 秒内）的页面不再重复请求，避免刚进首页又立刻打一遍接口
  const steps = [
    () => (fresh(state.home.loading, state.home.loadedAt) ? null : loadHome({ background: true })),        // 首页推荐（歌单第 1 页 + 新歌）
    () => (fresh(state.fav.loading, state.fav.loadedAt) ? null : loadFav({ background: true })),           // 我的歌单（歌单列表 + 收藏第 1 页）
    () => refreshTasks(),                                                              // 下载任务（本地队列状态，最轻）
    () => (fresh(false, state.history.loadedAt) ? null : loadHistory({ background: true })),   // 下载历史
    () => (fresh(false, state.settings.loadedAt) ? null : loadSettings({ background: true })), // 设置（含已授权目录）
  ];
  for (const step of steps) {
    if (!state.loggedIn) return;
    const task = step();
    if (task) {
      try {
        await task;
      } catch (err) {
        /* 预取失败不影响使用：真正切到该页时会重新请求 */
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
}

function schedulePrefetch() {
  const run = () => { prefetchAllViews(); };
  if (window.requestIdleCallback) window.requestIdleCallback(run, { timeout: 3000 });
  else setTimeout(run, 1200);
}

async function refreshLoginStatus() {
  try {
    const data = await api('/status');
    setLoggedIn(data.logged_in);
  } catch (e) {
    setLoggedIn(false);
  }
}

/* ---------------- 视图切换 ---------------- */
const VIEW_TITLES = {
  home: '首页推荐',
  search: '搜索',
  fav: '我的歌单',
  tasks: '下载任务',
  history: '下载历史',
  logs: '消息日志',
  settings: '设置',
};

/* ---------------- 首页入口（四宫格） ----------------
 * 首页默认只显示四个入口卡片，点进去才渲染对应列表，避免一屏堆四张长列表。 */
const HOME_PANELS = {
  songlists: '推荐歌单',
  newsongs: '新歌推荐',
  guess: '猜你喜欢',
  radar: '每日推荐 · 私人雷达',
};

/** 切换首页面板：'' 表示入口宫格，其它值为对应列表 */
function openHomePanel(name) {
  state.home.panel = HOME_PANELS[name] ? name : '';
  applyHomePanel();
}

/** 按 state.home.panel 显示/隐藏宫格与列表，并同步标题栏文字 */
function applyHomePanel() {
  const active = state.home.panel || '';
  const hub = $('#home-hub');
  if (hub) hub.classList.toggle('hidden', !!active);
  $$('#view-home .home-panel').forEach((el) => {
    el.classList.toggle('hidden', el.dataset.panel !== active);
  });
  if (state.view === 'home') {
    $('#view-title').textContent = active ? `首页推荐 · ${HOME_PANELS[active]}` : VIEW_TITLES.home;
  }
  if (active) hydrateCovers($('#view-home'));   // 面板刚从隐藏变可见：按需补封面
}

function switchView(view) {
  state.view = view;
  $$('.nav-item').forEach((btn) => btn.classList.toggle('active', btn.dataset.view === view));
  $$('.view').forEach((el) => el.classList.toggle('active', el.id === `view-${view}`));
  $('#view-title').textContent = VIEW_TITLES[view] || '';
  // 每个页面都走「有缓存先渲染、过期再后台刷新」，切页不再白屏等待
  if (view === 'home') { applyHomePanel(); loadHome(); }
  if (view === 'tasks') { renderTasks(); refreshTasks(); }
  if (view === 'history') loadHistory();
  if (view === 'logs') loadLogs();
  if (view === 'settings') loadSettings();
  if (view === 'fav') loadFav();
}

/* ---------------- 通用渲染：歌曲列表 ---------------- */
function songRowHtml(song, index, checked) {
  const tag = song.subtitle ? `<span class="tag">${esc(song.subtitle).slice(0, 8)}</span>` : '';
  const album = song.album ? ` · ${esc(song.album)}` : '';
  return `
    <label class="checkbox"><input type="checkbox" data-role="pick" data-songmid="${esc(song.songmid)}"${checked ? ' checked' : ''} /></label>
    <span class="idx">${index + 1}</span>
    <div class="info">
      <div class="name" title="${esc(song.name)}">${esc(song.name)}${tag}</div>
      <div class="sub" title="${esc(song.singer)}${esc(album)}">${esc(song.singer || '未知歌手')}${album}</div>
    </div>
    <span class="duration">${fmtDuration(song.interval)}</span>
    <div class="ops">
      <button class="btn btn-sm btn-ghost" data-role="preview" data-songmid="${esc(song.songmid)}">试听</button>
      <button class="btn btn-sm btn-primary" data-role="download" data-songmid="${esc(song.songmid)}">下载</button>
    </div>`;
}

function renderSongList(container, songs, sel, emptyText = '暂无歌曲') {
  if (!songs.length) {
    container.innerHTML = `<li class="empty">${esc(emptyText)}</li>`;
    return;
  }
  container.innerHTML = songs
    .map((song, i) => `<li class="song-item${sel.has(song.songmid) ? ' selected' : ''}" data-songmid="${esc(song.songmid)}">${songRowHtml(song, i, sel.has(song.songmid))}</li>`)
    .join('');
}

function renderSongSkeleton(container, rows = 6) {
  container.innerHTML = Array.from({ length: rows })
    .map(() => `<li class="song-skeleton"><div class="skeleton" style="width:18px;height:18px"></div><div class="skeleton bar" style="height:16px"></div><div class="skeleton" style="width:60px"></div></li>`)
    .join('');
}

/* ---------------- 通用渲染：歌单卡片 ---------------- */
function renderSonglistCards(container, songlists, emptyText = '暂无歌单') {
  if (!songlists.length) {
    container.innerHTML = `<div class="empty">${esc(emptyText)}</div>`;
    return;
  }
  container.innerHTML = songlists
    .map((item) => `
      <div class="songlist-card" data-songlist="${item.id}">
        <div class="cover" data-cover="${esc(songlistCover(item.picurl))}">
          ${item.listennum ? `<span class="play-count">▶ ${fmtListen(item.listennum)}</span>` : ''}
        </div>
        <div class="meta">
          <div class="title">${esc(item.title || '未命名歌单')}</div>
          <div class="sub">${esc(item.creator || '')}${item.songnum ? ` · ${item.songnum} 首` : ''}</div>
        </div>
      </div>`)
    .join('');
  hydrateCovers(container);
}

/* 可见区域预加载：视口附近（±300px）的封面立即加载，离得远的等滚动到再请求，
   这样首屏只拉当前可见的图，滚动时又不会看到空白 */
let coverObserver = null;
function hydrateCovers(root) {
  const nodes = $$('[data-cover]', root || document);
  if (!nodes.length) return;
  if (typeof IntersectionObserver === 'undefined') {
    nodes.forEach((el) => applyCover(el));
    return;
  }
  if (!coverObserver) {
    coverObserver = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        applyCover(entry.target);
        coverObserver.unobserve(entry.target);
      });
    }, { rootMargin: '300px 0px' });
  }
  nodes.forEach((el) => coverObserver.observe(el));
}

function applyCover(el) {
  const url = el.getAttribute('data-cover') || '';
  if (url) el.style.backgroundImage = `url('${url}')`;
  el.removeAttribute('data-cover');
}

function fmtListen(num) {
  const n = Number(num) || 0;
  if (n >= 100000000) return `${(n / 100000000).toFixed(1)}亿`;
  if (n >= 10000) return `${(n / 10000).toFixed(1)}万`;
  return String(n);
}

/* ---------------- 选择与批量下载 ---------------- */
function bindSongListEvents(container, sel, listRef, onChange) {
  container.addEventListener('click', (ev) => {
    const target = ev.target;
    const role = target.dataset ? target.dataset.role : '';
    if (role === 'preview') {
      openPlayer(target.dataset.songmid, listRef());
      return;
    }
    if (role === 'download') {
      const song = listRef().find((s) => s.songmid === target.dataset.songmid);
      if (song) createTasks([song]);
      return;
    }
    if (role === 'pick') {
      const mid = target.dataset.songmid;
      if (target.checked) sel.add(mid); else sel.delete(mid);
      const li = target.closest('.song-item');
      if (li) li.classList.toggle('selected', target.checked);
      if (onChange) onChange();
    }
  });
}

function updateBulkBar(allBox, invertBtn, countEl, sel, songs) {
  if (allBox) allBox.checked = songs.length > 0 && sel.size === songs.length;
  if (invertBtn) invertBtn.disabled = !songs.length;
  // busy 时显示的是「正在载入完整列表 x/y」，不要覆盖成计数
  if (countEl && !countEl.dataset.busy) {
    countEl.textContent = songs.length
      ? (sel.size ? `已选 ${sel.size} / 共 ${songs.length} 首` : `共 ${songs.length} 首`)
      : '';
  }
}

/* ---------------- 全量选择：全选/反选按「整张列表」生效 ---------------- */
const ALL_PAGE_NUM = 50;       // 后端单页上限：搜索 / 收藏 = 50
const ALL_PAGE_NUM_LIST = 100;  // 歌单单页上限 = 100
const PAGE_SAFETY_MAX = 400;   // 翻页硬上限（400×50 = 2 万首），避免接口异常时无限翻页
const SELECT_MAX_FALLBACK = 500;  // 「全选上限」兜底值：设置未加载时使用

/** 当前「全选上限」（设置页可调，默认 500 首）。 */
function selectMax() {
  const value = Number((state.settings || {}).select_max);
  return Number.isFinite(value) && value > 0 ? value : SELECT_MAX_FALLBACK;
}

/* ---------------- 首页推荐数量上限（设置页可调） ---------------- */
const HOME_LIMIT_FALLBACK = { songlists: 20, newsongs: 30, guess: 30, radar: 50 };

/** 某一类首页推荐最多显示多少（设置值非法时退回默认）。 */
function homeLimit(key) {
  const value = Number((state.settings || {})[`home_${key}_max`]);
  return Number.isFinite(value) && value >= 1 ? Math.round(value) : HOME_LIMIT_FALLBACK[key];
}

/* 上一次 fetchAllPages 是否因为触到「全选上限」而提前结束（供提示用） */
let allPagesTruncated = false;

/** 连续翻页把整张列表取回（最多取「全选上限」首）；已知总数 target（歌单 songnum）时可提前结束。 */
async function fetchAllPages({ fetchPage, num = ALL_PAGE_NUM, target = 0, onProgress }) {
  const out = [];
  const seen = new Set();
  const limit = Math.max(1, target > 0 ? Math.min(selectMax(), target) : selectMax());
  const maxPages = Math.max(1, Math.min(PAGE_SAFETY_MAX, Math.ceil(limit / Math.max(1, num))));
  allPagesTruncated = false;
  for (let page = 1; page <= maxPages; page += 1) {
    const batch = (await fetchPage(page, num)) || [];
    batch.forEach((song) => {
      const mid = song && song.songmid;
      if (mid && !seen.has(mid)) {
        seen.add(mid);
        out.push(song);
      }
    });
    if (onProgress) onProgress(out.length, target);
    const moreOnServer = batch.length >= num;      // 这一页是满的 → 上游还有下一页
    if (out.length >= limit) {
      allPagesTruncated = moreOnServer;
      break;
    }
    if (!moreOnServer) break;      // 不满一页 → 已经到底
    if (target && out.length >= target) break;
  }
  return out;
}

function invertSelection(sel, songs) {
  songs.forEach((song) => {
    if (sel.has(song.songmid)) sel.delete(song.songmid); else sel.add(song.songmid);
  });
}

function selectAll(sel, songs, checked) {
  songs.forEach((song) => { if (checked) sel.add(song.songmid); else sel.delete(song.songmid); });
}

const TASK_BATCH_MAX = 100;   // 与后端 MAX_BATCH 对齐：单次最多创建 100 个任务

async function createTasks(songs, { silent = false } = {}) {
  const payload = songs
    .filter((s) => s && s.songmid)
    .map((s) => ({
      songmid: s.songmid,
      songid: Number(s.songid) || 0,
      name: s.name || '',
      singer: s.singer || '',
      album_pmid: s.album_pmid || '',
    }));
  if (!payload.length) {
    toast('请先选择歌曲', 'warn');
    return [];
  }
  const created = [];
  const skipped = [];
  try {
    // 选中数量可能超过后端单次上限（整张列表全选）：分批提交（全程只亮一次 loading）
    await withLoading(async () => {
      for (let i = 0; i < payload.length; i += TASK_BATCH_MAX) {
        const chunk = payload.slice(i, i + TASK_BATCH_MAX);
        if (payload.length > TASK_BATCH_MAX) {
          toast(`正在创建下载任务 ${Math.min(i + chunk.length, payload.length)} / ${payload.length}…`, 'info', 1600);
        }
        const data = await api('/tasks', { method: 'POST', body: { songs: chunk } });
        created.push(...(data.created || []));
        skipped.push(...(data.skipped || []));
      }
    });
    if (skipped.length) {
      // 下载目录里已有同名成品：不重复下载，直接告知
      const names = skipped.slice(0, 5).map((s) => s.name || s.songmid).join('、');
      const more = skipped.length > 5 ? ` 等 ${skipped.length} 首` : '';
      toast(`已有该音乐文件：${names}${more}`, 'warn', Math.min(9000, 3000 + skipped.length * 300));
    }
    if (created.length) {
      if (!silent) toast(`已创建 ${created.length} 个下载任务`, 'success');
    } else if (!skipped.length && !silent) {
      toast('未创建任何下载任务', 'warn');
    }
    await refreshTasks();
    updateTaskBadge();
    return created;
  } catch (err) {
    handleError(err);
    return created;
  }
}

/* ---------------- 首页推荐 ---------------- */
/** 同一页面的在途请求合并：init 切页与闲时预取几乎同时触发时，只发一次请求。 */
const inflightLoads = new Map();
function onceLoad(key, fn) {
  const running = inflightLoads.get(key);
  if (running) return running;
  const task = Promise.resolve()
    .then(fn)
    .finally(() => inflightLoads.delete(key));
  inflightLoads.set(key, task);
  return task;
}

function loadHome(opts) { return onceLoad('home', () => loadHomeInner(opts)); }
function loadFav(opts) { return onceLoad('fav', () => loadFavInner(opts)); }
/* 事件回调常被直接当作 opts 传入（DOM Event 也带 target），这里统一归一化：
   参数不是「配置对象」时，一律视为「用户手动刷新」→ force。 */
function asOpts(opts) {
  if (opts && typeof opts === 'object' && !opts.target && !(opts instanceof Event)) return opts;
  return opts ? { force: true } : undefined;
}
function loadHistory(opts) { return onceLoad('history', () => loadHistoryInner(asOpts(opts))); }
function loadSettings(opts) { return onceLoad('settings', () => loadSettingsInner(asOpts(opts))); }
function refreshTasks() { return onceLoad('tasks', () => refreshTasksInner()); }

async function loadHomeInner({ force = false, background = false } = {}) {
  // 缓存优先：已有数据立即渲染（切页秒开），仅在后台静默刷新过期数据
  if (!force && !background && state.home.loadedAt) {
    renderHome();
    if (Date.now() - state.home.loadedAt > 60000) loadHome({ force: true, background: true });
    return;
  }
  state.home.loading = true;
  // 推荐数量上限来自设置：冷启动时设置还没拉到就先取一次，避免首页按兜底上限展示
  if (!state.settingsLoadedAt) {
    try { await loadSettings(); } catch (err) { /* 取不到就按兜底上限 */ }
  }
  if (!background) {
    renderSonglistCards($('#recommend-songlists'), [], '');
    $('#recommend-songlists').innerHTML = '<div class="empty">加载中…</div>';
    renderSongSkeleton($('#recommend-newsongs'));
    renderSongSkeleton($('#recommend-guess'));
    renderSongSkeleton($('#recommend-radar'));
  }
  try {
    const [lists, songs, guess, radar] = await Promise.all([
      api('/recommend/songlists', { query: { page: 1, limit: homeLimit('songlists') } }),
      api('/recommend/newsongs', { query: { limit: homeLimit('newsongs') } }),
      fetchRecommendList('/recommend/guess', homeLimit('guess')),
      fetchRecommendList('/recommend/radar', homeLimit('radar')),
    ]);
    state.home.songlists = lists.items || [];
    state.home.newsongs = songs.items || [];
    state.home.guess = guess.items;
    state.home.guessError = guess.error;
    state.home.radar = radar.items;
    state.home.radarError = radar.error;
    state.home.loadedAt = Date.now();
    if (!state.home.songlists.length && !state.home.newsongs.length && !background) {
      await refreshLoginStatus();
    }
    renderHome();
  } catch (err) {
    if (background) return;   // 静默预取失败：保留现有界面，等真正切页时再取
    handleError(err, { silent: err.code === 'not_logged_in' });
    if (err.code === 'not_logged_in') {
      $('#recommend-songlists').innerHTML = '<div class="empty">登录后可查看推荐歌单</div>';
      $('#recommend-newsongs').innerHTML = '<li class="empty">登录后可查看推荐新歌</li>';
    } else {
      $('#recommend-songlists').innerHTML = '<div class="empty">加载失败</div>';
      renderSongList($('#recommend-newsongs'), [], state.home.sel, '加载失败');
    }
  } finally {
    state.home.loading = false;
  }
}

/**
 * 首页个性化推荐（猜你喜欢 / 每日推荐）：由 QQ音乐服务器按当前账号口味推送。
 * 失败不影响首页其它区块，只在该区块内提示。
 */
async function fetchRecommendList(path, limit) {
  try {
    const data = await api(path, limit ? { query: { limit } } : undefined);
    return { items: data.items || [], error: '' };
  } catch (err) {
    if (err && err.code === 'not_logged_in') {
      return { items: [], error: '登录后可按你的口味推送' };
    }
    return { items: [], error: '加载失败，稍后可重试' };
  }
}

/** 渲染「猜你喜欢 / 每日推荐」区块：列表 + 批量栏（无数据时隐藏批量栏） */
function renderRecommendBlock(key, items, sel, errorText) {
  const list = $(`#recommend-${key}`);
  if (!list) return;
  // 清掉不在本列表里的历史选中项，避免「已选 N」虚高
  const valid = new Set(items.map((s) => s.songmid));
  Array.from(sel).forEach((mid) => { if (!valid.has(mid)) sel.delete(mid); });
  renderSongList(list, items, sel, errorText || '暂无推荐');
  const bulk = $(`#${key}-bulk`);
  if (bulk) bulk.classList.toggle('hidden', !items.length);
  updateBulkBar($(`#${key}-all`), $(`#${key}-invert`), $(`#${key}-count`), sel, items);
}

/** 用 state.home 渲染首页（缓存命中时也走这里） */
function renderHome() {
  renderSonglistCards($('#recommend-songlists'), state.home.songlists, '暂无推荐歌单');
  renderSongList($('#recommend-newsongs'), state.home.newsongs, state.home.sel, '暂无推荐新歌');
  updateBulkBar($('#newsongs-all'), $('#newsongs-invert'), null, state.home.sel, state.home.newsongs);
  renderRecommendBlock('guess', state.home.guess, state.home.guessSel, state.home.guessError);
  renderRecommendBlock('radar', state.home.radar, state.home.radarSel, state.home.radarError);
  renderHomeHub();
}

/** 入口卡片上的计数与面板显隐（数据变了就刷新一次） */
function renderHomeHub() {
  const setCount = (key, text) => {
    const el = $(`#hub-count-${key}`);
    if (el) el.textContent = text;
  };
  const n = (arr) => (arr || []).length;
  setCount('songlists', n(state.home.songlists) ? `${n(state.home.songlists)} 个歌单` : '暂无');
  setCount('newsongs', n(state.home.newsongs) ? `${n(state.home.newsongs)} 首` : '暂无');
  setCount('guess', n(state.home.guess) ? `${n(state.home.guess)} 首` : '暂无');
  setCount('radar', n(state.home.radar) ? `${n(state.home.radar)} 首` : '暂无');
  const sub = $('#home-songlists-count');
  if (sub) sub.textContent = n(state.home.songlists) ? `共 ${n(state.home.songlists)} 个歌单（点歌单卡片查看其中的歌曲）` : '';
  applyHomePanel();
}

/* ---------------- 搜索 ---------------- */
/* 从输入里识别歌单 ID：支持 y.qq.com 歌单链接、id/disstid 参数、纯数字 ID */
function extractSonglistId(text) {
  const raw = String(text || '').trim();
  if (!raw) return '';
  const hit =
    raw.match(/playlist\/(\d{5,})/) ||
    raw.match(/[?&](?:id|disstid|dissid)=(\d{5,})/) ||
    raw.match(/^(\d{5,})$/);
  return hit ? hit[1] : '';
}

function looksLikePlaylistLink(text) {
  return /y\.qq\.com|playlist|disstid|[?&]id=/i.test(String(text || ''));
}

async function loadSearch({ reset = false } = {}) {
  const keyword = $('#search-input').value.trim();
  const type = $('#search-type').value || 'song';
  if (!keyword) {
    toast('请输入搜索关键词', 'warn');
    return;
  }
  // 粘贴了歌单链接/ID（歌单类型下）→ 直接打开歌单，比名称搜索更精准
  const listId = extractSonglistId(keyword);
  if (listId && (looksLikePlaylistLink(keyword) || type === 'songlist')) {
    toast(`正在打开歌单 ${listId}`, 'success');
    openSonglistDetail(listId, '');
    return;
  }
  if (reset) {
    state.search.page = 1;
    state.search.items = [];
    state.search.songlists = [];
    state.search.sel.clear();
  }
  state.search.keyword = keyword;
  state.search.type = type;
  state.search.loading = true;
  if (reset) {
    renderSongSkeleton($('#search-results'));
    $('#search-songlists').innerHTML = '<div class="empty">加载中…</div>';
    $('#search-more').classList.add('hidden');
  }
  try {
    const data = await api('/search', { query: { keyword, type: type === 'songlist' ? 'songlist' : (type || 'song'), page: state.search.page } });
    const items = data.items || [];
    if (type === 'songlist') {
      state.search.songlists = reset ? items : state.search.songlists.concat(items);
      renderSonglistCards($('#search-songlists'), state.search.songlists, '没有找到相关歌单');
      $('#search-results').innerHTML = '<li class="empty">当前为歌单搜索</li>';
    } else {
      state.search.items = reset ? items : state.search.items.concat(items);
      renderSongList($('#search-results'), state.search.items, state.search.sel, '没有找到相关歌曲');
      $('#search-songlists').innerHTML = '';
    }
    state.search.hasMore = items.length >= 20;
    $('#search-more').classList.toggle('hidden', !state.search.hasMore);
    // 有结果才显示批量操作栏（此前它一直带着 hidden，导致搜索结果里点不到「全选」）
    $('#search-bulk').classList.toggle('hidden', !state.search.items.length);
    updateBulkBar($('#search-all'), $('#search-invert'), $('#search-count'), state.search.sel, state.search.items);
  } catch (err) {
    handleError(err, { silent: err.code === 'not_logged_in' });
    renderSongList($('#search-results'), [], state.search.sel, '搜索失败');
    $('#search-bulk').classList.add('hidden');
  } finally {
    state.search.loading = false;
  }
}

/** 把搜索结果整张列表取回（含尚未翻到的分页），供全选/反选使用。 */
async function loadAllSearch(onProgress) {
  const keyword = state.search.keyword;
  const kind = state.search.type === 'songlist' ? 'songlist' : (state.search.type || 'song');
  return fetchAllPages({
    num: ALL_PAGE_NUM,
    fetchPage: async (page, num) => {
      const data = await api('/search', { query: { keyword, type: kind, page, num } });
      return data.items || [];
    },
    onProgress,
  });
}

/** 整张搜索结果到手后替换列表并重渲染（分页按钮随之隐藏）。 */
function applyAllSearch(items) {
  if (!items.length) return;
  state.search.items = items;
  state.search.page = 1;
  state.search.hasMore = false;
  renderSongList($('#search-results'), state.search.items, state.search.sel, '没有找到相关歌曲');
  $('#search-more').classList.add('hidden');
  updateBulkBar($('#search-all'), $('#search-invert'), $('#search-count'), state.search.sel, state.search.items);
}

/* ---------------- 我的歌单（收藏） ---------------- */
async function loadFavInner({ force = false, background = false } = {}) {
  if (!state.loggedIn) {
    $('#fav-songlists').innerHTML = '<div class="empty">请先登录</div>';
    renderSongList($('#fav-songs'), [], state.fav.sel, '请先登录');
    $('#fav-more').classList.add('hidden');
    return;
  }
  // 缓存优先：已有数据立即渲染（切页秒开），过期数据后台静默刷新
  if (!force && !background && state.fav.loadedAt) {
    renderFav();
    if (Date.now() - state.fav.loadedAt > 60000) loadFav({ force: true, background: true });
    return;
  }
  state.fav.loading = true;
  if (!background) {
    $('#fav-songlists').innerHTML = '<div class="empty">加载中…</div>';
    renderSongSkeleton($('#fav-songs'));
  }
  try {
    const [lists, songs] = await Promise.all([
      api('/user/songlists'),
      api('/user/fav', { query: { page: 1 } }),
    ]);
    state.fav.songlists = lists.items || [];
    state.fav.songs = songs.items || [];
    state.fav.page = 1;
    state.fav.hasMore = state.fav.songs.length >= 30;
    state.fav.loadedAt = Date.now();
    renderFav();
  } catch (err) {
    if (background) return;
    handleError(err, { silent: err.code === 'not_logged_in' });
    $('#fav-songlists').innerHTML = '<div class="empty">加载失败</div>';
    renderSongList($('#fav-songs'), [], state.fav.sel, '加载失败');
  } finally {
    state.fav.loading = false;
  }
}

/** 用 state.fav 渲染收藏页 */
function renderFav() {
  renderSonglistCards($('#fav-songlists'), state.fav.songlists, '暂无收藏歌单');
  renderSongList($('#fav-songs'), state.fav.songs, state.fav.sel, '暂无收藏歌曲');
  $('#fav-more').classList.toggle('hidden', !state.fav.hasMore);
  updateBulkBar($('#fav-all'), $('#fav-invert'), $('#fav-count'), state.fav.sel, state.fav.songs);
  hydrateCovers($('#fav-songlists'));
}

async function loadMoreFav() {
  state.fav.page += 1;
  try {
    const data = await withLoading(() => api('/user/fav', { query: { page: state.fav.page } }));
    const items = data.items || [];
    state.fav.songs = state.fav.songs.concat(items);
    renderSongList($('#fav-songs'), state.fav.songs, state.fav.sel, '暂无收藏歌曲');
    state.fav.hasMore = items.length >= 30;
    $('#fav-more').classList.toggle('hidden', !state.fav.hasMore);
    updateBulkBar($('#fav-all'), $('#fav-invert'), $('#fav-count'), state.fav.sel, state.fav.songs);
  } catch (err) {
    handleError(err);
  }
}

/** 把收藏歌曲整张列表取回（收藏接口不返回总数，翻到不满一页为止）。 */
async function loadAllFav(onProgress) {
  return fetchAllPages({
    num: ALL_PAGE_NUM,
    fetchPage: async (page, num) => {
      const data = await api('/user/fav', { query: { page, num } });
      return data.items || [];
    },
    onProgress,
  });
}

/** 整张收藏列表到手后替换列表并重渲染。 */
function applyAllFav(items) {
  if (!items.length) return;
  state.fav.songs = items;
  state.fav.page = 1;
  state.fav.hasMore = false;
  renderFav();
}

/* ---------------- 歌单详情 ---------------- */
async function openSonglistDetail(songlistId, title = '') {
  if (!songlistId) return;
  state.songlist = { id: songlistId, info: { id: songlistId, title }, songs: [], page: 1, sel: new Set(), hasMore: false, loading: true };
  $('#songlist-title').textContent = title || '歌单详情';
  $('#songlist-count').textContent = '';
  renderSongSkeleton($('#songlist-songs'), 8);
  $('#modal-songlist').classList.remove('hidden');
  await loadSonglistPage(true);
}

async function loadSonglistPage(reset = false) {
  const id = state.songlist.id;
  if (!id) return;
  if (reset) {
    state.songlist.page = 1;
    state.songlist.songs = [];
    state.songlist.sel.clear();
  }
  state.songlist.loading = true;
  try {
    const data = await api(`/songlist/${id}`, { query: { page: state.songlist.page } });
    const info = data.info || {};
    const items = data.songs || [];
    state.songlist.info = info;
    state.songlist.songs = reset ? items : state.songlist.songs.concat(items);
    $('#songlist-title').textContent = info.title || $('#songlist-title').textContent;
    $('#songlist-count').textContent = info.songnum ? `${info.songnum} 首` : '';
    renderSongList($('#songlist-songs'), state.songlist.songs, state.songlist.sel, '该歌单暂无歌曲');
    state.songlist.hasMore = items.length >= 30;
    $('#songlist-more').classList.toggle('hidden', !state.songlist.hasMore);
    updateBulkBar($('#songlist-all'), $('#songlist-invert'), $('#songlist-sel-count'), state.songlist.sel, state.songlist.songs);
  } catch (err) {
    handleError(err);
    renderSongList($('#songlist-songs'), [], state.songlist.sel, '加载失败');
  } finally {
    state.songlist.loading = false;
  }
}

/** 把歌单整张列表取回：已知 songnum 时到数即停，否则翻到不满一页。 */
async function loadAllSonglist(onProgress) {
  const id = state.songlist.id;
  const target = Number((state.songlist.info || {}).songnum) || 0;
  return fetchAllPages({
    num: ALL_PAGE_NUM_LIST,
    target,
    fetchPage: async (page, num) => {
      const data = await api(`/songlist/${id}`, { query: { page, num } });
      return data.songs || [];
    },
    onProgress,
  });
}

/** 整张歌单到手后替换列表并重渲染。 */
function applyAllSonglist(items) {
  if (!items.length) return;
  state.songlist.songs = items;
  state.songlist.page = 1;
  state.songlist.hasMore = false;
  renderSongList($('#songlist-songs'), state.songlist.songs, state.songlist.sel, '该歌单暂无歌曲');
  $('#songlist-more').classList.add('hidden');
  updateBulkBar($('#songlist-all'), $('#songlist-invert'), $('#songlist-sel-count'), state.songlist.sel, state.songlist.songs);
}

/* ---------------- 登录：二维码 / 手机号 ---------------- */
function openLogin() {
  resetLoginPane();
  $('#modal-login').classList.remove('hidden');
}

/** 打开弹窗时的初始状态：三种登录方式都不预选，等用户自己点。 */
function resetLoginPane() {
  stopQrPolling();
  state.login.mode = '';
  $$('#login-tabs .tab').forEach((tab) => tab.classList.remove('active'));
  const choose = $('#login-choose');
  if (choose) choose.classList.remove('hidden');
  $('#qr-pane').classList.add('hidden');
  $('#phone-pane').classList.add('hidden');
}

function closeLogin() {
  stopQrPolling();
  $('#modal-login').classList.add('hidden');
}

function switchLoginTab(mode) {
  if (!mode) {
    resetLoginPane();
    return;
  }
  state.login.mode = mode;
  $$('#login-tabs .tab').forEach((tab) => tab.classList.toggle('active', (tab.dataset.login || tab.dataset.tab) === mode));
  const choose = $('#login-choose');
  if (choose) choose.classList.add('hidden');
  const isPhone = mode === 'phone';
  $('#qr-pane').classList.toggle('hidden', isPhone);
  $('#phone-pane').classList.toggle('hidden', !isPhone);
  if (isPhone) {
    stopQrPolling();
  } else {
    startQrLogin(mode);
  }
}

function stopQrPolling() {
  if (state.login.timer) {
    clearTimeout(state.login.timer);
    state.login.timer = null;
  }
  state.login.sessionId = '';
}

async function startQrLogin(mode) {
  stopQrPolling();
  const img = $('#qr-image');
  img.removeAttribute('src');
  const mask = $('#qr-mask');
  mask.textContent = '二维码生成中…';
  mask.classList.remove('hidden');
  $('#qr-hint').textContent = '正在获取二维码…';
  try {
    const data = await api('/login/qrcode', { method: 'POST', body: { type: mode } });
    state.login.sessionId = data.session_id || '';
    if (!data.image) throw new Error('未获取到二维码数据');
    img.src = data.image;
    mask.classList.add('hidden');
    $('#qr-hint').textContent = mode === 'wx' ? '请使用微信扫码登录' : '请使用手机 QQ 扫码登录';
    pollQrLogin();
  } catch (err) {
    handleError(err);
    $('#qr-hint').textContent = '二维码获取失败，点击重试';
    mask.textContent = '获取失败，点击重试';
    mask.classList.remove('hidden');
  }
}

async function pollQrLogin() {
  if (!state.login.sessionId) return;
  try {
    const data = await api('/login/qrcode/check', { method: 'POST', body: { session_id: state.login.sessionId } });
    if (data.logged_in) {
      stopQrPolling();
      setLoggedIn(true);
      closeLogin();
      toast('登录成功', 'success');
      await loadHome();
      return;
    }
    const st = String(data.state || '').toLowerCase();
    if (st === 'timeout' || st === 'refuse') {
      stopQrPolling();
      $('#qr-mask').classList.remove('hidden');
      $('#qr-hint').textContent = st === 'timeout' ? '二维码已过期，点击刷新' : '已取消授权，点击刷新';
      return;
    }
    if (st === 'conf' || st === 'confirm' || st === 'scanned') {
      $('#qr-hint').textContent = '已扫码，请在手机上确认';
    } else if (st === 'scan' || st === 'waiting' || st === '') {
      $('#qr-hint').textContent = state.login.mode === 'wx' ? '请使用微信扫码登录' : '请使用手机 QQ 扫码登录';
    }
    state.login.timer = setTimeout(pollQrLogin, 2000);
  } catch (err) {
    stopQrPolling();
    handleError(err);
    $('#qr-hint').textContent = '二维码已失效，点击刷新';
  }
}

async function sendSmsCode() {
  const phone = $('#login-phone').value.trim();
  if (!/^\d{6,15}$/.test(phone)) {
    toast('请输入正确的手机号', 'warn');
    return;
  }
  const btn = $('#btn-send-code');
  btn.disabled = true;
  try {
    const data = await withLoading(() => api('/login/sms', { method: 'POST', body: { phone, country_code: 86 } }));
    toast(data.info || '验证码已发送', data.sent ? 'success' : 'error');
    let left = 60;
    btn.textContent = `重新发送(${left})`;
    const timer = setInterval(() => {
      left -= 1;
      btn.textContent = `重新发送(${left})`;
      if (left <= 0) {
        clearInterval(timer);
        btn.disabled = false;
        btn.textContent = '获取验证码';
      }
    }, 1000);
  } catch (err) {
    btn.disabled = false;
    handleError(err);
  }
}

async function submitPhoneLogin() {
  const phone = $('#login-phone').value.trim();
  const code = $('#login-code').value.trim();
  if (!phone || !code) {
    toast('请输入手机号与验证码', 'warn');
    return;
  }
  try {
    await withLoading(() => api('/login/phone', { method: 'POST', body: { phone, code } }));
    setLoggedIn(true);
    closeLogin();
    toast('登录成功', 'success');
    await loadHome();
  } catch (err) {
    handleError(err);
  }
}

async function logout() {
  try {
    await withLoading(() => api('/login/logout', { method: 'POST' }));
    setLoggedIn(false);
    state.home.songlists = [];
    state.home.newsongs = [];
    state.home.loadedAt = 0;
    state.tasks.loadedAt = 0;
    toast('已退出登录', 'success');
    switchView('home');
    loadHome();
  } catch (err) {
    handleError(err);
  }
}

/* ---------------- 预览播放器 ---------------- */
function parseLrc(text) {
  const lines = [];
  String(text || '').split(/\r?\n/).forEach((raw) => {
    const matches = raw.match(/\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]/g);
    if (!matches) return;
    const content = raw.replace(/\[[^\]]*\]/g, '').trim();
    if (!content) return;
    matches.forEach((tag) => {
      const m = tag.match(/\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]/);
      const ms = (Number(m[3] || 0) * 10);
      lines.push({ time: Number(m[1]) * 60 + Number(m[2]) + ms / 1000, text: content });
    });
  });
  return lines.sort((a, b) => a.time - b.time);
}

function mergeTranslation(main, trans) {
  if (!trans.length) return main;
  const map = new Map();
  trans.forEach((line) => map.set(Math.round(line.time * 10), line.text));
  return main.map((line) => ({ ...line, trans: map.get(Math.round(line.time * 10)) || '' }));
}

/* 播放器弹窗：播放器 + 同步歌词 + 元数据字段（试听 / 播放已下载文件共用） */
const Player = (() => {
  let lines = [];
  let els = [];
  let follow = true;       // 是否自动居中
  let progScroll = false;  // 忽略自己造成的滚动
  let lastIndex = -1;

  const el = (id) => document.getElementById(id);
  const audio = () => el('pw-audio');
  const box = () => el('pw-lrc');
  const hint = (text) => { const h = el('pw-hint'); if (h) h.textContent = text || ''; };

  /* ---- 自绘控制条 ---- */
  const SPEEDS = [1, 1.25, 1.5, 2, 0.75];
  let speedIndex = 0;
  let dragging = false;

  function syncPlayButton() {
    const a = audio();
    const btn = el('pw-toggle');
    if (!a || !btn) return;
    const playing = !a.paused && !a.ended && !!a.currentSrc;
    btn.textContent = playing ? '❚❚' : '▶';
    btn.title = playing ? '暂停' : '播放';
  }

  function syncTime() {
    const a = audio();
    if (!a) return;
    const dur = Number.isFinite(a.duration) ? a.duration : 0;
    const cur = Number(a.currentTime) || 0;
    const seek = el('pw-seek');
    if (seek && !dragging) seek.value = dur ? String(Math.round((cur / dur) * 1000)) : '0';
    const label = el('pw-time');
    if (label) label.textContent = `${fmtClock(cur)} / ${dur ? fmtClock(dur) : '--:--'}`;
  }

  function syncMute() {
    const a = audio();
    if (!a) return;
    const vol = el('pw-vol');
    if (vol && Number(vol.value) !== a.volume) vol.value = String(a.volume);
    const btn = el('pw-mute');
    if (btn) {
      const silent = a.muted || a.volume === 0;
      btn.textContent = silent ? '🔇' : '🔊';
      btn.title = silent ? '取消静音' : '静音';
    }
  }

  function toggleMenu(force) {
    const menu = el('pw-more-menu');
    if (!menu) return;
    const show = force === undefined ? menu.classList.contains('hidden') : !!force;
    menu.classList.toggle('hidden', !show);
  }

  function cycleSpeed() {
    speedIndex = (speedIndex + 1) % SPEEDS.length;
    const rate = SPEEDS[speedIndex];
    const a = audio();
    if (a) a.playbackRate = rate;
    const label = el('pw-speed');
    if (label) label.textContent = `${rate}×`;
  }

  function resetControls() {
    const a = audio();
    speedIndex = 0;
    dragging = false;
    if (a) { a.playbackRate = 1; a.volume = 1; a.muted = false; }
    const speed = el('pw-speed');
    if (speed) speed.textContent = '1×';
    const vol = el('pw-vol');
    if (vol) vol.value = '1';
    toggleMenu(false);
    syncTime();
    syncPlayButton();
    syncMute();
  }

  /* 弹窗里的「下载」：走应用内正式下载（账号最高音质 + 同名文件去重 + 任务进度） */
  async function downloadCurrent() {
    toggleMenu(false);
    const song = state.player.song;
    if (!song || !song.songmid) {
      toast('暂无可下载的歌曲信息，请稍候重试', 'warn');
      return;
    }
    await createTasks([song]);
  }

  function syncHint() {
    if (!lines.length) { hint('同步歌词：该音频未内嵌时间轴歌词（不可同步高亮）'); return; }
    hint(follow
      ? `同步歌词：共 ${lines.length} 行 · 正在跟随播放（点击歌词可跳转；滚动歌词即取消自动居中）`
      : `同步歌词：共 ${lines.length} 行 · 已按你的滚动位置显示（点击任意歌词行恢复跟随）`);
  }

  function renderLyric() {
    const b = box();
    lastIndex = -1;
    if (!lines.length) {
      b.innerHTML = '<div class="lyric-empty">暂无歌词</div>';
      els = [];
      syncHint();
      return;
    }
    b.innerHTML = lines.map((line, i) => {
      const trans = line.trans ? `<span class="lyric-trans">${esc(line.trans)}</span>` : '';
      return `<div class="lyric-line" data-index="${i}" data-time="${line.time}">${esc(line.text)}${trans}</div>`;
    }).join('');
    els = Array.from(b.querySelectorAll('.lyric-line'));
    /* 重置滚动位置会被当成“用户手动滚动”而关掉跟随，这里先上屏蔽标记 */
    if (b.scrollTop !== 0) {
      progScroll = true;
      b.scrollTop = 0;
      setTimeout(() => { progScroll = false; }, 120);
    }
    syncHint();
  }

  function highlight() {
    if (!lines.length) return;
    const a = audio();
    const now = (a && a.currentTime) || 0;
    let idx = -1;
    for (let i = 0; i < lines.length; i += 1) {
      if (now + 0.15 >= lines[i].time) idx = i; else break;
    }
    if (idx === lastIndex) return;
    lastIndex = idx;
    els.forEach((node, i) => node.classList.toggle('active', i === idx));
    const active = els[idx];
    if (!active || !follow) return;
    const b = box();
    const delta = active.getBoundingClientRect().top - b.getBoundingClientRect().top;
    const want = b.scrollTop + delta - (b.clientHeight - active.offsetHeight) / 2;
    progScroll = true;
    b.scrollTop = Math.max(0, want);
    setTimeout(() => { progScroll = false; }, 120);
  }

  function renderFields(fields) {
    const b = el('pw-fields');
    if (!fields || !fields.length) { b.innerHTML = ''; return; }
    b.innerHTML = fields.map((f) => `
      <div class="pw-field">
        <div class="pw-field-label">${esc(f.label)}</div>
        <div class="pw-field-value">${f.value ? esc(f.value) : '<span class="lyric-empty-inline">（空）</span>'}</div>
      </div>`).join('');
  }

  function setCover(url) {
    const img = el('pw-cover');
    if (url) {
      img.onerror = () => img.classList.add('hidden');
      img.classList.remove('hidden');
      img.src = url;
    } else {
      img.classList.add('hidden');
      img.removeAttribute('src');
    }
  }

  function reset() {
    const a = audio();
    a.pause();
    a.removeAttribute('src');
    try { a.load(); } catch (e) {}
    lines = [];
    follow = true;
    progScroll = false;
    lastIndex = -1;
    resetControls();
  }

  function open(opts = {}) {
    reset();
    el('pw-title').textContent = opts.title || '播放器';
    el('pw-meta').textContent = opts.meta || '';
    setCover(opts.cover || '');
    renderFields(opts.fields || []);
    lines = opts.lines || [];
    renderLyric();
    hint(opts.hint || '正在准备音频…');
    el('modal-player').classList.remove('hidden');
    document.body.classList.add('has-player');
    resetControls();
    if (opts.src) play(opts.src);
  }

  function play(src) {
    const a = audio();
    a.src = src;
    const p = a.play();
    if (p && p.catch) p.catch(() => hint('浏览器阻止了自动播放：请点一下播放按钮（歌词会自动跟随）'));
  }

  /* 增量更新（不重置音频，用于异步拿到直链/歌词后回填） */
  function update(opts = {}) {
    if (opts.title !== undefined) el('pw-title').textContent = opts.title || '播放器';
    if (opts.meta !== undefined) el('pw-meta').textContent = opts.meta || '';
    if (opts.cover !== undefined) setCover(opts.cover || '');
    if (opts.fields) renderFields(opts.fields);
    if (opts.lines) { lines = opts.lines; renderLyric(); }
    if (opts.hint) hint(opts.hint);
    if (opts.src) play(opts.src);
    syncTime();
    syncPlayButton();
  }

  function bind() {
    const a = audio();
    a.addEventListener('timeupdate', highlight);
    a.addEventListener('seeked', highlight);
    a.addEventListener('loadedmetadata', () => {
      highlight();
      const d = Number(a.duration);
      const dur = Number.isFinite(d) ? ` · 时长 ${Math.floor(d / 60)}:${String(Math.floor(d % 60)).padStart(2, '0')}` : '';
      if (lines.length) syncHint();
      else hint(`该音频未内嵌时间轴歌词，无法同步高亮${dur}`);
    });
    a.addEventListener('error', () => {
      const code = a.error ? a.error.code : '?';
      const map = { 1: '加载被中止', 2: '网络错误（文件读取失败）', 3: '解码错误（浏览器不支持该编码）', 4: '音频源不可用（文件缺失/格式不受支持）' };
      hint(`音频加载失败（错误码 ${code}：${map[code] || '未知'}）。若为本地文件，请确认文件仍在下载目录内且未被移动。`);
    });
    const b = box();
    b.addEventListener('click', (ev) => {
      const line = ev.target.closest('.lyric-line');
      if (!line) return;
      follow = true;
      syncHint();
      try { a.currentTime = Number(line.dataset.time) || 0; } catch (e) {}
      const p = a.play();
      if (p && p.catch) p.catch(() => {});
      highlight();
    });
    b.addEventListener('scroll', () => {
      if (progScroll) return;
      if (follow) { follow = false; syncHint(); }
    });

    /* ---- 自绘控制条 ---- */
    a.addEventListener('play', syncPlayButton);
    a.addEventListener('pause', syncPlayButton);
    a.addEventListener('ended', syncPlayButton);
    a.addEventListener('timeupdate', syncTime);
    a.addEventListener('durationchange', syncTime);
    a.addEventListener('loadedmetadata', () => { syncTime(); syncMute(); });
    a.addEventListener('emptied', () => { syncTime(); syncPlayButton(); });

    const toggle = el('pw-toggle');
    if (toggle) toggle.addEventListener('click', () => {
      if (a.paused) {
        const p = a.play();
        if (p && p.catch) p.catch(() => hint('浏览器阻止了自动播放：请再点一次播放按钮'));
      } else {
        a.pause();
      }
    });

    const seek = el('pw-seek');
    if (seek) {
      seek.addEventListener('input', () => {
        dragging = true;
        const dur = Number.isFinite(a.duration) ? a.duration : 0;
        const label = el('pw-time');
        if (dur && label) label.textContent = `${fmtClock((Number(seek.value) / 1000) * dur)} / ${fmtClock(dur)}`;
      });
      const commit = () => {
        const dur = Number.isFinite(a.duration) ? a.duration : 0;
        if (dur) {
          try { a.currentTime = (Number(seek.value) / 1000) * dur; } catch (e) {}
        }
        dragging = false;
        syncTime();
      };
      seek.addEventListener('change', commit);
      seek.addEventListener('pointerup', commit);
    }

    const vol = el('pw-vol');
    if (vol) vol.addEventListener('input', () => {
      a.volume = Math.max(0, Math.min(1, Number(vol.value)));
      a.muted = false;
      syncMute();
    });

    const mute = el('pw-mute');
    if (mute) mute.addEventListener('click', () => {
      a.muted = !a.muted;
      syncMute();
    });

    const more = el('pw-more');
    if (more) more.addEventListener('click', (ev) => {
      ev.stopPropagation();
      toggleMenu();
    });

    const menu = el('pw-more-menu');
    if (menu) menu.addEventListener('click', (ev) => {
      const item = ev.target.closest('[data-pw-action]');
      if (!item) return;
      const action = item.dataset.pwAction;
      if (action === 'download') downloadCurrent();
      else if (action === 'speed') cycleSpeed();
    });

    /* 点空白处收起「⋮」菜单 */
    document.addEventListener('click', (ev) => {
      if (!ev.target.closest('.pw-more-wrap')) toggleMenu(false);
    });
  }

  function close() {
    const a = audio();
    a.pause();
    a.removeAttribute('src');
    el('modal-player').classList.add('hidden');
    document.body.classList.remove('has-player');
    state.player.songmid = '';
    state.player.lyrics = [];
    state.player.song = null;
    lines = [];
    els = [];
    resetControls();
  }

  return { open, update, close, bind, hint, setCover };
})();

/* 在线试听：QQ 音乐直链 */
async function openPlayer(songmid, songList = []) {
  if (!songmid) return;
  const cached = (songList || []).find((s) => s.songmid === songmid) || {};
  state.player.songmid = songmid;
  state.player.list = songList || [];
  /* 弹窗「⋮ → 下载」用：先放列表里的信息，拿到详情后再补全 */
  state.player.song = {
    songmid,
    songid: cached.songid || 0,
    name: cached.name || '',
    singer: cached.singer || '',
    album_pmid: cached.album_pmid || '',
  };
  Player.open({
    title: cached.name || '加载中…',
    meta: cached.singer || '',
    cover: cached.album_pmid ? coverUrl(cached.album_pmid) : '',
    fields: [
      { label: '歌名', value: cached.name || songmid },
      { label: '歌手', value: cached.singer || '' },
      { label: '专辑', value: cached.album || '' },
      { label: '来源', value: 'QQ 音乐在线试听' },
    ],
    hint: '正在获取试听直链与歌词…',
  });
  try {
    const [detail, urlData] = await Promise.all([
      api(`/song/${encodeURIComponent(songmid)}`),
      api('/song/url', { method: 'POST', body: { songmid } }),
    ]);
    const song = detail.song || cached || {};
    /* 供弹窗「⋮ → 下载」使用：正式下载（最高音质）需要歌名/歌手/ID */
    state.player.song = {
      songmid,
      songid: song.songid || cached.songid || 0,
      name: song.name || cached.name || '',
      singer: song.singer || cached.singer || '',
      album_pmid: song.album_pmid || cached.album_pmid || '',
    };
    const q = urlData.quality_label || urlData.quality || '默认';
    const cover = song.album_pmid ? coverUrl(song.album_pmid) : '';
    Player.update({
      title: song.name || songmid,
      meta: [song.singer, song.album, `试听音质 ${q}`].filter(Boolean).join(' · '),
      cover,
      fields: [
        { label: '歌名', value: song.name || songmid },
        { label: '歌手', value: song.singer || '' },
        { label: '专辑', value: song.album || '' },
        { label: '试听音质', value: q },
        { label: '来源', value: 'QQ 音乐在线试听（下载时自动取账号最高音质）' },
      ],
      hint: '正在加载音频…',
      /* 走同源代理：QQ 直链是 http://IP，https 页面会被混合内容拦截（无声/进度不动） */
      src: `${API}/preview/stream?songmid=${encodeURIComponent(songmid)}`,
    });
  } catch (err) {
    handleError(err);
    Player.hint('获取试听直链失败，请查看提示信息。');
  }
  try {
    const lyricData = await api(`/song/${encodeURIComponent(songmid)}/lyric`, { query: { trans: 1 } });
    const merged = mergeTranslation(parseLrc(lyricData.lyric), parseLrc(lyricData.translation));
    state.player.lyrics = merged;
    Player.update({ lines: merged });
  } catch (err) {
    /* 无歌词不阻塞播放 */
  }
}

/* 播放本地已下载文件（读内嵌元数据/歌词/封面） */
async function playLocal(songmid) {
  if (!songmid) return;
  try {
    const data = await withLoading(() => api('/local/meta', { query: { songmid } }));
    const merged = mergeTranslation(parseLrc(data.lyric), parseLrc(data.translation));
    state.player.songmid = songmid;
    state.player.lyrics = merged;
    state.player.song = {
      songmid,
      name: data.name || data.title || '',
      singer: data.singer || '',
      album_pmid: '',
    };
    Player.open({
      title: data.name || data.title || songmid,
      meta: data.meta || '',
      cover: `${API}/local/cover?songmid=${encodeURIComponent(songmid)}`,
      lines: merged,
      fields: data.fields || [],
      hint: `正在播放本地文件：${data.filename || ''}`,
      src: `${API}/local/stream?songmid=${encodeURIComponent(songmid)}`,
    });
  } catch (err) {
    handleError(err);
  }
}

function closePlayer() {
  Player.close();
}

/* ---------------- 下载任务 ---------------- */
const STATE_LABEL = { pending: '等待中', downloading: '下载中', done: '已完成', failed: '失败' };
const QUALITY_TEXT = { flac: 'FLAC', master: '臻品母带', ogg_320: 'OGG 320', mp3_320: 'MP3 320', mp3_128: 'MP3 128', acc_192: 'AAC 192' };

function stateBadge(state) {
  const key = String(state || '').toLowerCase();
  const cls = key === 'done' ? 'ok' : key === 'failed' ? 'bad' : key === 'pending' ? 'idle' : 'run';
  return `<span class="badge ${cls}">${esc(STATE_LABEL[key] || key || '未知')}</span>`;
}

function progressRow(label, state, progress, extra = '') {
  const key = String(state || '').toLowerCase();
  const done = key === 'done';
  const failed = key === 'failed';
  const skipped = key === 'skipped';
  const pending = !done && !failed && !skipped && ['pending', 'queued', 'waiting', ''].includes(key);
  const pct = done ? 100 : Math.max(0, Math.min(100, Math.round((Number(progress) || 0) * 100)));
  const cls = ['progress', done ? 'done' : '', failed ? 'failed' : '', skipped ? 'skipped' : '', pending ? 'pending' : (!done && !failed ? 'running' : '')]
    .filter(Boolean).join(' ');
  const width = done ? 100 : pct > 0 ? pct : (pending || skipped ? 0 : 6);
  const text = done ? '100%' : failed ? (pct > 0 ? `${pct}%` : '失败') : skipped ? '跳过' : pending ? '等待' : `${pct}%`;
  return `
    <div class="progress-row">
      <span class="label">${esc(label)}</span>
      <div class="${cls}"><i class="bar-fill" style="width:${width}%"></i></div>
      <span class="pct">${text}</span>
      <span class="value">${esc(extra)}</span>
    </div>`;
}

function renderTasks() {
  const items = state.tasks.items || [];
  const counts = state.tasks.counts || {};
  const success = counts.success || 0;
  $('#tasks-summary').textContent = `进行中 ${counts.running || 0} · 失败 ${counts.failed || 0}${success ? ` · 成功 ${success}` : ''}`;
  const box = $('#task-list');
  if (!items.length) {
    box.innerHTML = '<div class="empty">暂无进行中的任务；下载完成的会自动进入「下载历史」</div>';
    return;
  }
  box.innerHTML = items
    .map((task) => {
      const size = task.total ? `${fmtSize(task.received)} / ${fmtSize(task.total)}` : '';
      const steps = task.stages || [];
      const rows = steps
        .map((st, idx) => progressRow(st.label || st.key, st.state, st.progress, idx === 0 ? size : ''))
        .join('');
      const badge = task.encrypted ? '加密源·已解密' : '音质自动';
      const detail = task.step ? `<div class="task-foot"><span class="extra">当前：${esc(task.step)}</span></div>` : '';
      const outName = task.output_name ? `<div class="task-foot"><span class="extra">成品：${esc(task.output_name)}</span></div>` : '';
      return `
        <div class="task-card ${esc(task.status || '')}" data-task="${esc(task.id)}">
          <div class="task-head">
            <div class="task-title" title="${esc(task.name)}">
              <span class="name">${esc(task.name || task.songmid)}</span>
              <span class="sub">${esc(task.singer || '')}</span>
            </div>
            <span class="badge idle">${esc(task.quality_label || QUALITY_TEXT[task.quality] || badge)}</span>
            ${stateBadge(task.status)}
            <button class="btn btn-sm btn-primary" data-role="retry" data-task="${esc(task.id)}"${task.status === 'failed' ? '' : ' disabled'}>重试</button>
          </div>
          ${rows}
          ${detail}
          ${outName}
          ${task.fail_reason ? `<div class="task-foot"><span class="fail-reason">失败原因：${esc(task.fail_reason)}</span></div>` : ''}
        </div>`;
    })
    .join('');
}

/** 任务成功后后端会把它从「下载任务」移到「下载历史」，这里给一次提示并同步历史页 */
function notifyRetired(before, next) {
  const alive = new Set((next || []).map((t) => t.id));
  const retired = (before || []).filter((t) => t.status === 'success' && !alive.has(t.id));
  if (!retired.length) return;
  const names = retired.map((t) => t.name || t.songmid).filter(Boolean);
  toast(
    names.length === 1
      ? `「${names[0]}」下载完成，已移入下载历史`
      : `${names.length} 首下载完成，已移入下载历史`,
    'success'
  );
  if (state.view === 'history') loadHistory({ force: true, background: true });
}

async function refreshTasksInner() {
  const before = state.tasks.items || [];
  try {
    const data = await api('/tasks');
    const next = data.tasks || [];
    state.tasks.items = next;
    state.tasks.counts = data.counts || {};
    state.tasks.loadedAt = Date.now();
    notifyRetired(before, next);
    if (state.view === 'tasks') renderTasks();
  } catch (err) {
    if (state.view === 'tasks') handleError(err, { silent: true });
  }
}

function updateTaskBadge() {
  const badge = $('#task-badge');
  const running = (state.tasks.counts && state.tasks.counts.running) || 0;
  badge.textContent = running ? String(running) : '';
  badge.classList.toggle('hidden', !running);
}

function startTaskPolling() {
  if (state.tasks.timer) return;
  state.tasks.timer = setInterval(async () => {
    await refreshTasks();
    updateTaskBadge();
    if (!state.tasks.counts || !state.tasks.counts.running) {
      // 队列空闲时降低轮询频率
      clearInterval(state.tasks.timer);
      state.tasks.timer = null;
      setTimeout(startTaskPolling, 5000);
    }
  }, 700);
}

async function retryTask(taskId) {
  try {
    await withLoading(() => api(`/tasks/${encodeURIComponent(taskId)}/retry`, { method: 'POST' }));
    toast('已重新排队', 'success');
    await refreshTasks();
    updateTaskBadge();
    startTaskPolling();
  } catch (err) {
    handleError(err);
  }
}

/** 按钮瞬时反馈：禁用 + 文案替换（替代全屏 loading，点下去立刻有反应） */
function setBtnBusy(btn, busy, busyText = '处理中…') {
  if (!btn) return;
  if (busy) {
    if (!btn.dataset.originText) btn.dataset.originText = btn.textContent;
    btn.disabled = true;
    btn.textContent = busyText;
  } else {
    btn.disabled = false;
    if (btn.dataset.originText) btn.textContent = btn.dataset.originText;
  }
}

async function clearTasks(scope = 'finished') {
  const all = scope === 'all';
  const btn = $(all ? '#btn-tasks-clear-all' : '#btn-tasks-clear-finished');
  const running = (state.tasks.items || []).filter((t) => t.status === 'downloading').length;
  const tip = all
    ? (running ? `确定清空全部任务记录吗？\n（${running} 个正在进行的任务会保留）` : '确定清空全部任务记录吗？')
    : '确定清除已完成的任务记录吗？\n（失败/中断的任务会保留，方便你重试）';
  if (!window.confirm(tip)) return;
  setBtnBusy(btn, true, '清理中…');
  const backup = state.tasks.items || [];
  // 乐观更新：本地先移除，界面立刻干净，不再等接口回来
  state.tasks.items = backup.filter((t) => (all ? t.status === 'downloading' : t.status !== 'success'));
  renderTasks();
  try {
    const data = await api('/tasks/clear', { method: 'POST', query: { scope } });
    const extra = data.kept ? `（${data.kept} 个进行中的任务已保留）` : '';
    toast(`已清理 ${data.removed || 0} 条记录${extra}`, 'success');
    await refreshTasks();
    updateTaskBadge();
  } catch (err) {
    state.tasks.items = backup;
    renderTasks();
    handleError(err);
  } finally {
    setBtnBusy(btn, false);
  }
}

/* ---------------- 下载历史 ---------------- */
function historyRowHtml(item) {
  const mid = esc(item.songmid);
  return `
    <tr data-songmid="${mid}">
      <td class="col-name">
        <div class="name">${esc(item.name || item.songmid)}</div>
        <div class="sub">${esc(item.singer || '')}</div>
      </td>
      <td>${esc(item.quality_label || QUALITY_TEXT[item.quality] || item.quality || '最高')}</td>
      <td class="col-state" data-role="file-state"><span class="badge idle">待检查</span></td>
      <td>${fmtTime(item.time)}</td>
      <td class="col-action"><button class="btn btn-sm" data-role="play" data-songmid="${mid}">播放</button></td>
    </tr>`;
}

async function loadHistoryInner({ force = false, background = false } = {}) {
  const box = $('#history-body');
  // 缓存优先：已有数据立即渲染（切页秒开），过期数据后台静默刷新
  if (!force && !background && state.history.loadedAt) {
    renderHistory();
    if (Date.now() - state.history.loadedAt > 60000) loadHistory({ force: true, background: true });
    return;
  }
  if (!background) box.innerHTML = '<tr><td colspan="5" class="empty">加载中…</td></tr>';
  try {
    const data = await api('/history');
    state.history.items = data.items || [];
    state.history.total = data.total || state.history.items.length;
    state.history.loadedAt = Date.now();
    renderHistory();
  } catch (err) {
    if (background) return;
    handleError(err);
    box.innerHTML = '<tr><td colspan="5" class="empty">加载失败</td></tr>';
  }
}

/** 用 state.history.items 渲染历史表格 */
function renderHistory() {
  const box = $('#history-body');
  state.history.record.clear();
  const total = state.history.total || state.history.items.length;
  $('#history-summary').textContent = state.history.items.length ? `共 ${total} 条记录` : '';
  if (!state.history.items.length) {
    box.innerHTML = '<tr><td colspan="5" class="empty">暂无下载历史</td></tr>';
    return;
  }
  box.innerHTML = state.history.items.map(historyRowHtml).join('');
  observeHistoryRows();
}

/** 只检查可见行的文件状态，避免全量扫描磁盘 */
function observeHistoryRows() {
  if (state.history.observer) state.history.observer.disconnect();
  const scroll = $('#history-scroll');
  const rows = $$('#history-body tr[data-songmid]');
  const pending = new Map();
  let timer = null;

  const flush = async () => {
    const songmids = Array.from(pending.keys());
    pending.clear();
    if (!songmids.length) return;
    try {
      const data = await api('/history/check', { method: 'POST', body: { songmids } });
      const result = data.items || {};
      songmids.forEach((mid) => {
        const row = $(`#history-body tr[data-songmid="${mid}"]`);
        if (!row) return;
        const state2 = result[mid] || { output: 'unknown' };
        renderFileState(row.querySelector('[data-role="file-state"]'), state2.output ?? state2.audio);
      });
    } catch (err) {
      // 检查失败不打扰用户，保持“待检查”
    }
  };

  state.history.observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      const mid = entry.target.dataset.songmid;
      pending.set(mid, true);
      state.history.observer.unobserve(entry.target);
    });
    if (timer) clearTimeout(timer);
    timer = setTimeout(flush, 350);
  }, { root: scroll, rootMargin: '120px 0px', threshold: 0.01 });

  rows.forEach((row) => state.history.observer.observe(row));
}

function renderFileState(cell, value) {
  if (!cell) return;
  const key = String(value || 'unknown').toLowerCase();
  const map = { ok: ['ok', '存在'], missing: ['bad', '缺失'], unknown: ['idle', '未知'] };
  const [cls, text] = map[key] || map.unknown;
  cell.innerHTML = `<span class="badge ${cls}">${text}</span>`;
  const row = cell.closest('tr');
  const btn = row && row.querySelector('button[data-role="play"]');
  if (btn) {
    const missing = key === 'missing';
    btn.disabled = missing;
    btn.title = missing ? '文件已不存在，无法播放' : '播放本地文件';
  }
}

async function clearHistory() {
  if (!window.confirm('确定清空下载历史吗？\n（不影响已下载文件，清空前会自动备份到 history.json.bak）')) return;
  const btn = $('#btn-history-clear');
  setBtnBusy(btn, true, '清理中…');
  const backup = state.history.items || [];
  // 乐观更新：表格立刻清空，配合后端 12ms 的响应基本无感
  state.history.items = [];
  state.history.total = 0;
  renderHistory();
  try {
    await api('/history/clear', { method: 'POST' });
    state.history.loadedAt = Date.now();
    toast('历史已清空', 'success');
  } catch (err) {
    state.history.items = backup;
    state.history.total = backup.length;
    renderHistory();
    handleError(err);
  } finally {
    setBtnBusy(btn, false);
  }
}

/* ---------------- 设置 ---------------- */
async function loadSettingsInner({ force = false, background = false } = {}) {
  // 缓存优先：已有数据就立即渲染（切页秒开），仅在后台静默刷新过期数据
  if (!force && !background && state.settingsLoadedAt) {
    applySettings();
    if (Date.now() - state.settingsLoadedAt > 120000) loadSettings({ force: true, background: true });
    return;
  }
  try {
    const data = await api('/settings');
    state.settings = data.settings || {};
    state.authorizedDirs = data.authorized_dirs || [];
    state.authorizedHint = data.authorized_hint || '';
    state.settingsLoadedAt = Date.now();
    applySettings();
  } catch (err) {
    if (!background) handleError(err);
  }
}

/** 用已有的 state.settings 渲染设置页（缓存命中时直接调用，不再发请求） */
function applySettings() {
  const s = state.settings || {};
  $('#setting-lyric-trans').checked = !!s.lyric_trans;
  $('#setting-meta-full').checked = s.meta_full !== false;
  $('#setting-meta-json').checked = !!s.meta_json;
  renderDirOptions();
  $('#setting-interval-min').value = s.interval_min_ms || 300;
  $('#setting-interval-max').value = s.interval_max_ms || 800;
  const selectMaxInput = $('#setting-select-max');
  if (selectMaxInput) selectMaxInput.value = s.select_max || SELECT_MAX_FALLBACK;
  const setHomeInput = (sel, key) => { const el = $(sel); if (el) el.value = homeLimit(key); };
  setHomeInput('#setting-home-songlists', 'songlists');
  setHomeInput('#setting-home-newsongs', 'newsongs');
  setHomeInput('#setting-home-guess', 'guess');
  setHomeInput('#setting-home-radar', 'radar');
  applyPushSettings();
}

/* 下载目录 = 已授权目录列表（选择与授权合并为一处） */
function renderDirOptions() {
  const select = $('#setting-download-dir');
  if (!select) return;
  const authorized = state.authorizedDirs || [];
  const current = (state.settings && state.settings.download_dir) || '';
  const dirs = authorized.slice();
  if (current && dirs.indexOf(current) === -1) dirs.unshift(current);
  select.innerHTML = dirs
    .map((dir) => {
      const label = authorized.indexOf(dir) === -1 ? `${dir}（默认目录·未授权）` : dir;
      return `<option value="${esc(dir)}">${esc(label)}</option>`;
    })
    .join('');
  select.value = current || (dirs[0] || '');
  const hint = $('#dir-hint');
  if (hint) {
    const parts = ['点「选择文件夹…」在飞牛内嵌文件夹选择器中点选文件夹：选择即完成授权（trim.file.userAccess），下载目录与访问权限一步到位。'];
    if (!authorized.length) parts.push('当前还没有已授权的文件夹。');
    if (state.authorizedHint) parts.push(state.authorizedHint);
    if (!window.trimApp) parts.push('（独立浏览器中需在飞牛应用内打开本页面才能调用选择器）');
    hint.textContent = parts.join(' ');
  }
}

/* 下拉点选即保存（与授权结果一致，无需再点保存设置） */
async function saveDownloadDirFromSelect() {
  const value = $('#setting-download-dir').value;
  if (!value || value === ((state.settings && state.settings.download_dir) || '')) return;
  try {
    const data = await withLoading(() => api('/settings', { method: 'POST', body: { download_dir: value } }));
    state.settings = data.settings || state.settings;
    toast('下载目录已更新', 'success');
    await loadSettings({ force: true });
  } catch (err) {
    handleError(err);
    await loadSettings({ force: true });
  }
}

async function saveSettings() {
  const payload = {
    lyric_trans: $('#setting-lyric-trans').checked,
    meta_full: $('#setting-meta-full').checked,
    meta_json: $('#setting-meta-json').checked,
    download_dir: $('#setting-download-dir').value.trim(),
    interval_min_ms: Number($('#setting-interval-min').value) || 300,
    interval_max_ms: Number($('#setting-interval-max').value) || 800,
  };
  if (payload.interval_max_ms < payload.interval_min_ms) {
    toast('最大间隔不能小于最小间隔', 'warn');
    return;
  }
  const selectMaxInput = Number($('#setting-select-max').value);
  if (!Number.isFinite(selectMaxInput) || selectMaxInput < 10 || selectMaxInput > 20000) {
    toast('全选上限需在 10 ~ 20000 首之间', 'warn');
    return;
  }
  payload.select_max = Math.round(selectMaxInput);
  // 消息推送（飞牛统一推送服务）
  payload.push_base = ($('#setting-push-base') || {}).value || '';
  payload.push_token = ($('#setting-push-token') || {}).value || '';
  payload.push_on_success = !!($('#setting-push-success') || {}).checked;
  payload.push_on_dup = !!($('#setting-push-dup') || {}).checked;
  payload.push_on_fail = !!($('#setting-push-fail') || {}).checked;
  payload.push_on_expire = !!($('#setting-push-expire') || {}).checked;
  // 首页四块推荐的数量上限：1 ~ 上限（下限交给 QQ 服务器：拿不到就少显示）
  const homeFields = [
    ['#setting-home-songlists', 'home_songlists_max', 1, 60, '推荐歌单'],
    ['#setting-home-newsongs', 'home_newsongs_max', 1, 100, '新歌推荐'],
    ['#setting-home-guess', 'home_guess_max', 1, 60, '猜你喜欢'],
    ['#setting-home-radar', 'home_radar_max', 1, 100, '每日推荐'],
  ];
  for (const [selector, key, low, high, label] of homeFields) {
    const value = Number($(selector).value);
    if (!Number.isFinite(value) || value < low || value > high) {
      toast(`${label}数量需在 ${low} ~ ${high} 之间`, 'warn');
      return;
    }
    payload[key] = Math.round(value);
  }
  try {
    const data = await withLoading(() => api('/settings', { method: 'POST', body: payload }));
    state.settings = data.settings || state.settings;
    state.home.loadedAt = 0;   // 数量变了 → 下次进首页按新上限重新取
    toast('设置已保存', 'success');
  } catch (err) {
    handleError(err);
  }
}

/* 宿主调用超时保护：未挂载飞牛 SDK 桥接通道时 pickUserFile 永不返回，避免「一直转圈」 */
function withTimeout(promise, ms, message) {
  let timer = 0;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => {
      const err = new Error(message);
      err.code = 'PICKER_TIMEOUT';
      reject(err);
    }, ms);
  });
  return Promise.race([Promise.resolve(promise), timeout]).finally(() => clearTimeout(timer));
}

/* 通过飞牛文件授权选择下载目录 */
async function chooseDownloadDir() {
  if (!window.trimApp) {
    toast('请在飞牛 fnOS 应用中打开此页面以选择目录', 'warn');
    return;
  }
  try {
    const paths = await withLoading(() => withTimeout(
      requestUserDirectory(window.trimApp),
      20000,
      '目录选择器没有响应：请从飞牛桌面/应用中心打开本应用后再试（用浏览器直连地址打开不支持飞牛的文件夹选择器）。',
    ));
    if (!paths || !paths.length) {
      /* 独立浏览器：等待授权回调页回传结果 */
      toast('已打开授权窗口，完成后将自动更新', 'info');
      return;
    }
    const data = await withLoading(() => api('/settings', { method: 'POST', body: { download_dir: paths[0], dir_from_picker: true } }));
    state.settings = data.settings || state.settings;
    toast('已选择并授权该文件夹，下载目录已更新', 'success');
    await loadSettings();
  } catch (err) {
    if (err && err.code === 'PICKER_TIMEOUT') {
      toast(err.message, 'warn');
      return;
    }
    handleError(err);
  }
}

/* ---------------- 宿主 SDK ---------------- */
function initSdk() {
  import('./js/trim-web-app.js')
    .then((mod) => {
      const App = mod.TrimApp || mod.default;
      if (typeof App !== 'function') return;
      try {
        window.trimApp = new App();
        const app = window.trimApp;
        if (typeof app.setTitle === 'function') app.setTitle('QQ音乐下载器');
        if (typeof app.setExitPageTips === 'function') app.setExitPageTips();
        if (typeof app.getPlatformConfig === 'function') {
          app.getPlatformConfig().then((config) => {
            const theme = config && (config.theme || config.colorScheme);
            if (theme) document.documentElement.dataset.theme = String(theme);
          }).catch(() => {});
        }
        /* 独立浏览器授权完成后（回调页 postMessage 同源回传）刷新目录设置 */
        listenForAuthResult(async (result) => {
          try {
            const paths = result && Array.isArray(result.data) ? result.data : [];
            if (paths.length) {
              await withLoading(() => api('/settings', {
                method: 'POST',
                body: { download_dir: paths[0], dir_from_picker: true },
              }));
            }
            await loadSettings();
            toast(paths.length ? '已选择并授权该文件夹，下载目录已更新' : '目录授权已更新', 'success');
          } catch (err) {
            handleError(err);
          }
        });
      } catch (e) {
        /* 非飞牛宿主环境（本地浏览器调试）下静默降级 */
      }
    })
    .catch(() => {});
}

/* ---------------- 事件绑定 ---------------- */
function bindSonglistCards(container) {
  container.addEventListener('click', (ev) => {
    const card = ev.target.closest('.songlist-card');
    if (!card) return;
    const id = Number(card.dataset.songlist);
    const title = card.querySelector('.title') ? card.querySelector('.title').textContent : '';
    openSonglistDetail(id, title);
  });
}

function rerenderWithSelection(container, songs, sel) {
  renderSongList(container, songs, sel);
}

/**
 * 批量栏绑定：全选 / 反选作用于「整张列表」——点下去先把尚未加载完的分页全部取回
 * （loadAll / applyAll 由各列表提供；不提供说明该列表本身已是完整的一屏列表）。
 */
function bindBulkControls({ container, songs, sel, allSel, invertBtn, downloadBtn, bulkBar, countEl, loadAll, applyAll, hasMore, label = '列表' }) {
  let busy = false;

  const sync = () => updateBulkBar(allSel, invertBtn, countEl, sel, songs());

  const setBusy = (on) => {
    busy = on;
    if (bulkBar) bulkBar.classList.toggle('busy', on);
    [allSel, invertBtn, downloadBtn].forEach((el) => {
      if (el) el.disabled = on;
    });
    if (countEl) {
      if (on) {
        countEl.dataset.busy = '1';
        countEl.textContent = `正在载入完整${label}…`;   // 首个分页返回前也要有反馈
      } else {
        delete countEl.dataset.busy;
      }
    }
  };

  /** 需要时先把整张列表取回来；返回 false 表示取回失败（此时不要做全选/反选）。 */
  const ensureWholeList = async () => {
    if (busy) return false;
    if (!loadAll || !applyAll) return true;
    if (hasMore && !hasMore()) return true;   // 列表已经完整，不必再拉一次
    if (!state.settingsLoadedAt) {            // 「全选上限」来自设置，还没加载就先取一次
      try { await loadSettings(); } catch (err) { /* 取不到就用兜底上限 */ }
    }
    setBusy(true);
    try {
      const items = await loadAll((got, total) => {
        if (!countEl) return;
        countEl.textContent = total
          ? `正在载入完整${label} ${got} / ${total} 首…`
          : `正在载入完整${label} ${got} 首…`;
      });
      applyAll(items);
      if (allPagesTruncated) {
        toast(`已按「全选上限」${selectMax()} 首截断，本次只处理前 ${items.length} 首（可在设置页调大上限）`, 'warn', 6000);
      }
      return true;
    } catch (err) {
      handleError(err);
      return false;
    } finally {
      setBusy(false);
      sync();
    }
  };

  if (allSel) {
    allSel.addEventListener('change', async () => {
      // 先记下用户意图：拉整表过程中会重渲染列表并同步勾选框，不能等到 await 之后再读
      const want = allSel.checked;
      if (want && !(await ensureWholeList())) {
        allSel.checked = false;   // 整张列表没取回来 → 不假装已全选
        return;
      }
      selectAll(sel, songs(), want);
      rerenderWithSelection(container, songs(), sel);
      sync();
    });
  }
  if (invertBtn) {
    invertBtn.addEventListener('click', async () => {
      if (!(await ensureWholeList())) return;
      invertSelection(sel, songs());
      rerenderWithSelection(container, songs(), sel);
      sync();
    });
  }
  if (downloadBtn) {
    downloadBtn.addEventListener('click', async () => {
      const picked = songs().filter((s) => sel.has(s.songmid));
      if (!picked.length) {
        toast('请先勾选要下载的歌曲', 'warn');
        return;
      }
      const created = await createTasks(picked);
      if (created && created.length) {
        sel.clear();
        rerenderWithSelection(container, songs(), sel);
        sync();
        startTaskPolling();
        switchView('tasks');
      }
    });
  }
}

function bindModals() {
  $$('[data-close], .modal-close').forEach((btn) => {
    btn.addEventListener('click', () => {
      const target = btn.dataset.close ? document.getElementById(btn.dataset.close) : null;
      const modal = target || btn.closest('.modal');
      if (modal) modal.classList.add('hidden');
    });
  });
  $$('.modal').forEach((modal) => {
    modal.addEventListener('click', (ev) => {
      if (ev.target === modal) modal.classList.add('hidden');
    });
  });
  // 播放器弹窗：关闭时停止音频（✕ / 点遮罩 / Esc）
  const playerModal = $('#modal-player');
  const playerClose = $('#pw-close');
  if (playerModal) playerModal.addEventListener('click', (ev) => { if (ev.target === playerModal) closePlayer(); });
  if (playerClose) playerClose.addEventListener('click', () => closePlayer());
  document.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Escape') return;
    const modal = $('#modal-player');
    if (modal && !modal.classList.contains('hidden')) closePlayer();
  });
  on('#qr-mask', 'click', () => startQrLogin(state.login.mode));
}

function bindEvents() {
  on('#nav', 'click', (ev) => {
    const item = ev.target.closest('.nav-item');
    if (item) switchView(item.dataset.view);
  });
  on('#btn-login', 'click', openLogin);
  on('#btn-logout', 'click', logout);

  $$('#login-tabs .tab').forEach((tab) => {
    tab.addEventListener('click', () => switchLoginTab(tab.dataset.login || tab.dataset.tab));
  });
  // 二维码重新获取：刷新按钮 + 点击遮罩（生成中/失败/过期时显示）
  const qrRefresh = () => {
    if (state.login.mode === 'qq' || state.login.mode === 'wx') startQrLogin(state.login.mode);
  };
  const qrRefreshBtn = $('#btn-qr-refresh');
  if (qrRefreshBtn) qrRefreshBtn.addEventListener('click', qrRefresh);
  on('#qr-mask', 'click', qrRefresh);
  on('#btn-send-code', 'click', sendSmsCode);
  on('#btn-phone-login', 'click', submitPhoneLogin);
  on('#login-code', 'keydown', (ev) => { if (ev.key === 'Enter') submitPhoneLogin(); });

  on('#search-form', 'submit', (ev) => {
    ev.preventDefault();
    loadSearch({ reset: true });
  });
  on('#search-type', 'change', () => {
    if ($('#search-input').value.trim()) loadSearch({ reset: true });
  });
  on('#btn-search-more', 'click', () => {
    state.search.page += 1;
    loadSearch({});
  });

  on('#btn-reload-recommend', 'click', loadHome);
  bindSonglistCards($('#recommend-songlists'));
  // 首页四宫格：点入口进对应列表，点「← 返回」回到入口
  on('#home-hub', 'click', (ev) => {
    const entry = ev.target.closest('.home-entry');
    if (entry) openHomePanel(entry.dataset.panel);
  });
  on('#view-home', 'click', (ev) => {
    if (ev.target.closest('[data-home-back]')) openHomePanel('');
  });

  on('#btn-reload-fav', 'click', loadFav);
  on('#btn-fav-more', 'click', loadMoreFav);
  bindSonglistCards($('#fav-songlists'));

  bindSonglistCards($('#search-songlists'));

  on('#btn-tasks-clear-all', 'click', () => clearTasks('all'));

  on('#btn-history-reload', 'click', () => loadHistory({ force: true }));
  on('#btn-history-clear', 'click', clearHistory);

  on('#btn-save-settings', 'click', saveSettings);
  on('#btn-push-test', 'click', sendPushTest);
  bindLogsPanel();
  bindAccountPanel();
  on('#btn-choose-dir', 'click', chooseDownloadDir);
  on('#setting-download-dir', 'change', saveDownloadDirFromSelect);

  on('#task-list', 'click', (ev) => {
    const btn = ev.target.closest('button[data-role="retry"]');
    if (btn && !btn.disabled) retryTask(btn.dataset.task);
  });

  bindSongListEvents($('#recommend-newsongs'), state.home.sel, () => state.home.newsongs);
  bindSongListEvents($('#search-results'), state.search.sel, () => state.search.items);
  bindSongListEvents($('#fav-songs'), state.fav.sel, () => state.fav.songs);
  bindSongListEvents($('#songlist-songs'), state.songlist.sel, () => state.songlist.songs);

  bindBulkControls({
    container: $('#recommend-newsongs'),
    songs: () => state.home.newsongs,
    sel: state.home.sel,
    allSel: $('#newsongs-all'),
    invertBtn: $('#newsongs-invert'),
    downloadBtn: $('#newsongs-download'),
    countEl: $('#newsongs-count'),
    label: '推荐列表',
    bulkBar: $('#newsongs-bulk'),
  });
  // 猜你喜欢 / 每日推荐：整表一次取回，无需翻页补全
  bindSongListEvents($('#recommend-guess'), state.home.guessSel, () => state.home.guess);
  bindSongListEvents($('#recommend-radar'), state.home.radarSel, () => state.home.radar);
  bindBulkControls({
    container: $('#recommend-guess'),
    songs: () => state.home.guess,
    sel: state.home.guessSel,
    allSel: $('#guess-all'),
    invertBtn: $('#guess-invert'),
    downloadBtn: $('#guess-download'),
    countEl: $('#guess-count'),
    label: '猜你喜欢',
    bulkBar: $('#guess-bulk'),
  });
  bindBulkControls({
    container: $('#recommend-radar'),
    songs: () => state.home.radar,
    sel: state.home.radarSel,
    allSel: $('#radar-all'),
    invertBtn: $('#radar-invert'),
    downloadBtn: $('#radar-download'),
    countEl: $('#radar-count'),
    label: '每日推荐',
    bulkBar: $('#radar-bulk'),
  });
  bindBulkControls({
    container: $('#search-results'),
    songs: () => state.search.items,
    sel: state.search.sel,
    allSel: $('#search-all'),
    invertBtn: $('#search-invert'),
    downloadBtn: $('#search-download'),
    countEl: $('#search-count'),
    hasMore: () => state.search.hasMore,
    loadAll: loadAllSearch,
    applyAll: applyAllSearch,
    label: '搜索结果',
    bulkBar: $('#search-bulk'),
  });
  bindBulkControls({
    container: $('#fav-songs'),
    songs: () => state.fav.songs,
    sel: state.fav.sel,
    allSel: $('#fav-all'),
    invertBtn: $('#fav-invert'),
    downloadBtn: $('#fav-download'),
    countEl: $('#fav-count'),
    hasMore: () => state.fav.hasMore,
    loadAll: loadAllFav,
    applyAll: applyAllFav,
    label: '收藏列表',
    bulkBar: $('#fav-bulk'),
  });
  bindBulkControls({
    container: $('#songlist-songs'),
    songs: () => state.songlist.songs,
    sel: state.songlist.sel,
    allSel: $('#songlist-all'),
    invertBtn: $('#songlist-invert'),
    downloadBtn: $('#songlist-download'),
    countEl: $('#songlist-sel-count'),
    hasMore: () => state.songlist.hasMore,
    loadAll: loadAllSonglist,
    applyAll: applyAllSonglist,
    label: '歌单',
    bulkBar: $('#songlist-bulk'),
  });

  on('#btn-songlist-more', 'click', () => {
    state.songlist.page += 1;
    loadSonglistPage(false);
  });

  // 历史记录：播放本地已下载文件
  const histBody = $('#history-body');
  if (histBody) {
    histBody.addEventListener('click', (ev) => {
      const btn = ev.target.closest('button[data-role="play"]');
      if (!btn || btn.disabled) return;
      playLocal(btn.dataset.songmid);
    });
  }

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      refreshTasks().then(updateTaskBadge);
      refreshLoginStatus();
    }
  });
}

/* ---------------- 启动 ---------------- */
async function init() {
  document.body.classList.add('ready');
  bindModals();
  bindEvents();
  bindAbout();
  Player.bind();
  initSdk();
  await refreshLoginStatus();
  applySettings();          // 设置（含推送项）先落一遍，避免推送卡片空白
  loadSettings().catch(() => {});
  loadLogs({ background: true }).catch(() => {});   // 只为拿到消息日志未读徽标
  await refreshTasks();
  updateTaskBadge();
  startTaskPolling();
  switchView('home');
  try {
    const health = await api('/health');
    const badge = document.getElementById('app-version');
    if (badge && health && health.version) badge.textContent = health.version;
  } catch (err) { /* 忽略 */ }
}

init();


/* ==========================================================
   消息日志 / 推送设置 / 多账号（v1.2.0 新增）
   ========================================================== */

/* ---------------- 消息日志 ---------------- */
const LOG_LEVEL_LABEL = { success: '成功', error: '失败', warn: '提醒', info: '信息' };

function loadLogs(opts) { return onceLoad('logs', () => loadLogsInner(asOpts(opts))); }

/* 未读日志徽标：记录上次查看消息日志的时间点（本地存储），晚于该时间的条目算未读 */
const LOGS_SEEN_KEY = 'qqmd.logs.seenAt';

function logsSeenAt() {
  const v = Number(window.localStorage.getItem(LOGS_SEEN_KEY) || 0);
  return Number.isFinite(v) && v > 0 ? v : 0;
}

function updateLogsBadge() {
  const badge = $('#logs-badge');
  if (!badge) return;
  const seen = logsSeenAt();
  const n = (state.logs.items || []).filter((x) => (Number(x.time) || 0) > seen).length;
  badge.textContent = n ? String(n) : '';
  badge.classList.toggle('hidden', !n);
}

function markLogsSeen() {
  const newest = (state.logs.items || []).reduce((m, x) => Math.max(m, Number(x.time) || 0), 0);
  if (newest > logsSeenAt()) window.localStorage.setItem(LOGS_SEEN_KEY, String(newest));
  updateLogsBadge();
}

async function loadLogsInner({ force = false, background = false } = {}) {
  if (!force && !background && state.logs.loadedAt) {
    renderLogs(); renderPushRecords();
    if (state.view === 'logs') markLogsSeen(); else updateLogsBadge();
    if (Date.now() - state.logs.loadedAt > 60000) loadLogs({ force: true, background: true });
    return;
  }
  try {
    const [logs, records] = await Promise.all([
      api('/logs', { query: { limit: 200 } }),
      api('/push/records', { query: { limit: 200 } }),
    ]);
    state.logs.items = logs.items || [];
    state.logs.counts = logs.counts || {};
    state.logs.records = records.items || [];
    state.logs.loadedAt = Date.now();
    renderLogs(); renderPushRecords();
    if (state.view === 'logs') markLogsSeen(); else updateLogsBadge();
  } catch (err) {
    if (!background) handleError(err);
  }
}

function renderLogs() {
  const box = $('#logs-scroll'); if (!box) return;
  const level = state.logs.level;
  const items = state.logs.items.filter((x) => !level || x.level === level);
  const c = state.logs.counts || {};
  const summary = $('#logs-summary');
  if (summary) summary.textContent = `共 ${state.logs.items.length} 条 · 成功 ${c.success || 0} · 失败 ${c.error || 0} · 提醒 ${c.warn || 0}`;
  if (!items.length) {
    box.innerHTML = `<div class="empty">${state.logs.items.length ? '该筛选下没有记录' : '暂无消息记录（下载成功 / 失败 / 已有同名文件 / 登录过期都会记在这里）'}</div>`;
    return;
  }
  box.innerHTML = items.map((x) => `
    <div class="log-row level-${esc(x.level)}">
      <span class="log-time">${esc(fmtTime(x.time))}</span>
      <span class="log-tag">${esc(LOG_LEVEL_LABEL[x.level] || x.level)}</span>
      <div class="log-main">
        <div class="log-title">${esc(x.title || x.event)}</div>
        ${x.detail ? `<div class="log-detail">${esc(x.detail)}</div>` : ''}
      </div>
    </div>`).join('');
}

function renderPushRecords() {
  const box = $('#push-scroll'); if (!box) return;
  const items = state.logs.records || [];
  if (!items.length) { box.innerHTML = '<div class="empty">尚无推送记录（可到设置页配置推送服务并发送测试通知）</div>'; return; }
  box.innerHTML = items.map((x) => `
    <div class="log-row ${x.ok ? 'level-success' : 'level-error'}">
      <span class="log-time">${esc(fmtTime(x.time))}</span>
      <span class="log-tag">${x.ok ? '已推送' : '失败'}</span>
      <div class="log-main">
        <div class="log-title">${esc(x.title || x.event)}</div>
        <div class="log-detail">${esc(x.detail || x.error || '')}${x.status && !x.ok ? ` · HTTP ${esc(x.status)}` : ''}</div>
      </div>
    </div>`).join('');
}

function bindLogsPanel() {
  const tabs = $$('[data-logs-tab]');
  tabs.forEach((btn) => btn.addEventListener('click', () => {
    state.logs.tab = btn.dataset.logsTab;
    tabs.forEach((b) => b.classList.toggle('active', b === btn));
    $('#logs-panel').classList.toggle('hidden', state.logs.tab !== 'logs');
    $('#push-panel').classList.toggle('hidden', state.logs.tab !== 'push');
  }));
  $$('#logs-filter .chip').forEach((chip) => chip.addEventListener('click', () => {
    state.logs.level = chip.dataset.level || '';
    $$('#logs-filter .chip').forEach((c) => c.classList.toggle('active', c === chip));
    renderLogs();
  }));
  on('#btn-logs-reload', 'click', () => loadLogs({ force: true }));
  on('#btn-logs-clear', 'click', async () => {
    if (!window.confirm('确定清空全部消息日志？')) return;
    try {
      await api('/logs/clear', { method: 'POST' });
      await loadLogs({ force: true });
      toast('消息日志已清空', 'success');
    } catch (err) { handleError(err); }
  });
}


/* ---------------- 账号卡片 / 多账号 ---------------- */
function loadAccounts() { return onceLoad('accounts', () => loadAccountsInner()); }

async function loadAccountsInner() {
  try {
    const data = await api('/account');
    state.accounts = { items: data.accounts || [], current: data.current_key || '' };
    state.accounts.loadedAt = Date.now();
    renderAccounts(data);
  } catch (err) { /* 未登录或网络异常：保持原样 */ }
}

function renderAccounts(data) {
  const info = data || {};
  const name = info.nickname || info.musicid || '';
  const nameEl = $('#acc-name');
  const subEl = $('#acc-sub');
  const avatar = $('#acc-avatar');
  if (avatar) {
    if (info.avatar) { avatar.src = info.avatar; avatar.classList.remove('hidden'); }
    else avatar.classList.add('hidden');
  }
  if (nameEl) nameEl.textContent = info.logged_in ? (name || '已登录') : '未登录';
  if (subEl) {
    if (!info.logged_in) subEl.textContent = '登录后可下载无损 / 母带';
    else {
      const parts = [];
      if (info.vip_level || info.vip_desc) parts.push(`会员：${info.vip_level || info.vip_desc}`);
      if (Number(info.vip_days_left) > 0) parts.push(`剩余 ${info.vip_days_left} 天`);
      if (!parts.length) parts.push(info.musicid ? `账号 ${info.musicid}` : '已登录');
      subEl.textContent = parts.join(' · ');
    }
  }
  const list = $('#acc-list');
  if (!list) return;
  const items = (state.accounts && state.accounts.items) || [];
  if (!info.logged_in || items.length <= 1) { list.innerHTML = ''; list.classList.add('hidden'); return; }
  list.classList.remove('hidden');
  list.innerHTML = items.map((a) => `
    <div class="acc-item${a.current ? ' current' : ''}">
      <button class="acc-pick" data-role="acc-switch" data-key="${esc(a.key)}"${a.current ? ' disabled' : ''}>
        ${esc(a.nickname || a.musicid || a.key)}${a.current ? ' ·当前' : ''}
      </button>
      <button class="acc-del" data-role="acc-remove" data-key="${esc(a.key)}" title="移除该账号">✕</button>
    </div>`).join('');
}

function bindAccountPanel() {
  const sidebar = $('.sidebar');
  if (!sidebar) return;
  sidebar.addEventListener('click', async (ev) => {
    const pick = ev.target.closest('button[data-role="acc-switch"]');
    const del = ev.target.closest('button[data-role="acc-remove"]');
    if (pick && !pick.disabled) {
      try {
        await withLoading(() => api('/account/switch', { method: 'POST', body: { key: pick.dataset.key } }));
        toast('已切换账号', 'success');
        state.settingsLoadedAt = 0; state.logs.loadedAt = 0;
        await loadAccounts();
        await refreshLoginStatus();
      } catch (err) { handleError(err); }
      return;
    }
    if (del) {
      if (!window.confirm('移除该账号的本地登录态？（可重新扫码登录）')) return;
      try {
        await withLoading(() => api('/account/remove', { method: 'POST', body: { key: del.dataset.key } }));
        toast('账号已移除', 'success');
        await loadAccounts();
        await refreshLoginStatus();
      } catch (err) { handleError(err); }
    }
  });
}


/* ---------------- 消息推送设置 ---------------- */
function applyPushSettings() {
  const s = state.settings || {};
  const base = $('#setting-push-base'); if (base) base.value = s.push_base || '';
  const token = $('#setting-push-token'); if (token) token.value = s.push_token || '';
  const set = (sel, val) => { const el = $(sel); if (el) el.checked = val !== false; };
  set('#setting-push-success', s.push_on_success);
  set('#setting-push-dup', s.push_on_dup);
  set('#setting-push-fail', s.push_on_fail);
  set('#setting-push-expire', s.push_on_expire);
  const hint = $('#push-test-hint');
  if (hint) hint.textContent = s.push_base ? '' : '未配置推送服务地址';
}

/** 发送测试通知：用「当前输入框」里的地址/Token 先保存，再触发一次测试推送。 */
async function sendPushTest() {
  const baseEl = $('#setting-push-base');
  const tokenEl = $('#setting-push-token');
  const base = (baseEl && baseEl.value.trim()) || '';
  if (!base) { toast('请先填写推送服务地址', 'warn'); return; }
  const hint = $('#push-test-hint');
  try {
    await withLoading(() => api('/settings', { method: 'POST', body: {
      push_base: base,
      push_token: (tokenEl && tokenEl.value.trim()) || '',
    } }));
    await loadSettings({ force: true });
    const data = await withLoading(() => api('/push/test', { method: 'POST' }));
    const result = data.result || {};
    if (result.ok) {
      toast('测试通知已发送，请查看飞牛推送服务', 'success', 4200);
      if (hint) hint.textContent = '';
    } else {
      toast(`测试通知发送失败：${result.error || `HTTP ${result.status || '?'}`}`, 'error', 5200);
      if (hint) hint.textContent = result.error || `HTTP ${result.status || '?'}`;
    }
    state.logs.loadedAt = 0;
  } catch (err) { handleError(err); }
}

/* ---------------- 开发者 / 发布者链接 ---------------- */
/** 点开发者名字 → 新标签打开 GitHub 的「公开仓库列表」页。 */
function bindAboutLinks() {
  $$('[data-about="developer"]').forEach((el) => {
    el.addEventListener('click', (ev) => {
      if (el.tagName === 'A' && el.getAttribute('href') && el.getAttribute('href') !== '#') return;
      ev.preventDefault();
      window.open(ABOUT.developer.url, '_blank', 'noopener,noreferrer');
    });
  });
}
