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

const state = {
  view: 'home',
  loggedIn: false,
  home: { songlists: [], newsongs: [], loading: false, sel: new Set() },
  search: { keyword: '', type: 'song', page: 1, items: [], songlists: [], sel: new Set(), loading: false, hasMore: false },
  fav: { songlists: [], songs: [], page: 1, sel: new Set(), loading: false, hasMore: false },
  songlist: { id: 0, info: {}, songs: [], page: 1, sel: new Set(), hasMore: false, loading: false },
  tasks: { items: [], counts: {}, timer: null },
  history: { items: [], record: new Map(), cache: new Map(), observer: null, timer: null },
  settings: {},
  qualities: [],
  login: { mode: 'qq', sessionId: '', busy: false, timer: null },
  player: { songmid: '', lyrics: [], index: -1, timer: null, ready: false },
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
  state.loggedIn = !!loggedIn;
  const dot = $('#account-status .dot');
  dot.classList.toggle('online', state.loggedIn);
  dot.classList.toggle('offline', !state.loggedIn);
  $('#account-text').textContent = state.loggedIn ? '已登录' : '未登录';
  $('#btn-login').classList.toggle('hidden', state.loggedIn);
  $('#btn-logout').classList.toggle('hidden', !state.loggedIn);
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
  settings: '设置',
};

function switchView(view) {
  state.view = view;
  $$('.nav-item').forEach((btn) => btn.classList.toggle('active', btn.dataset.view === view));
  $$('.view').forEach((el) => el.classList.toggle('active', el.id === `view-${view}`));
  $('#view-title').textContent = VIEW_TITLES[view] || '';
  if (view === 'tasks') renderTasks();
  if (view === 'history') loadHistory();
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
        <div class="cover" style="background-image:url('${esc(songlistCover(item.picurl))}')">
          ${item.listennum ? `<span class="play-count">▶ ${fmtListen(item.listennum)}</span>` : ''}
        </div>
        <div class="meta">
          <div class="title">${esc(item.title || '未命名歌单')}</div>
          <div class="sub">${esc(item.creator || '')}${item.songnum ? ` · ${item.songnum} 首` : ''}</div>
        </div>
      </div>`)
    .join('');
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
  if (countEl) countEl.textContent = sel.size ? `已选 ${sel.size} 首` : '';
}

function invertSelection(sel, songs) {
  songs.forEach((song) => {
    if (sel.has(song.songmid)) sel.delete(song.songmid); else sel.add(song.songmid);
  });
}

function selectAll(sel, songs, checked) {
  songs.forEach((song) => { if (checked) sel.add(song.songmid); else sel.delete(song.songmid); });
}

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
  try {
    const data = await withLoading(() => api('/tasks', { method: 'POST', body: { songs: payload } }));
    if (!silent) toast(`已创建 ${data.created.length} 个下载任务`, 'success');
    await refreshTasks();
    updateTaskBadge();
    return data.created;
  } catch (err) {
    handleError(err);
    return [];
  }
}

/* ---------------- 首页推荐 ---------------- */
async function loadHome() {
  state.home.loading = true;
  renderSonglistCards($('#recommend-songlists'), [], '');
  $('#recommend-songlists').innerHTML = '<div class="empty">加载中…</div>';
  renderSongSkeleton($('#recommend-newsongs'));
  try {
    const [lists, songs] = await Promise.all([
      api('/recommend/songlists', { query: { page: 1 } }),
      api('/recommend/newsongs'),
    ]);
    state.home.songlists = lists.items || [];
    state.home.newsongs = songs.items || [];
    if (!state.home.songlists.length && !state.home.newsongs.length) {
      await refreshLoginStatus();
    }
    renderSonglistCards($('#recommend-songlists'), state.home.songlists, '暂无推荐歌单');
    renderSongList($('#recommend-newsongs'), state.home.newsongs, state.home.sel, '暂无推荐新歌');
    updateBulkBar($('#newsongs-all'), $('#newsongs-invert'), null, state.home.sel, state.home.newsongs);
  } catch (err) {
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

/* ---------------- 搜索 ---------------- */
async function loadSearch({ reset = false } = {}) {
  const keyword = $('#search-input').value.trim();
  const type = $('#search-type').value || 'song';
  if (!keyword) {
    toast('请输入搜索关键词', 'warn');
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
    updateBulkBar($('#search-all'), $('#search-invert'), null, state.search.sel, state.search.items);
  } catch (err) {
    handleError(err, { silent: err.code === 'not_logged_in' });
    renderSongList($('#search-results'), [], state.search.sel, '搜索失败');
  } finally {
    state.search.loading = false;
  }
}

/* ---------------- 我的歌单（收藏） ---------------- */
async function loadFav() {
  if (!state.loggedIn) {
    $('#fav-songlists').innerHTML = '<div class="empty">请先登录</div>';
    renderSongList($('#fav-songs'), [], state.fav.sel, '请先登录');
    $('#fav-more').classList.add('hidden');
    return;
  }
  state.fav.loading = true;
  $('#fav-songlists').innerHTML = '<div class="empty">加载中…</div>';
  renderSongSkeleton($('#fav-songs'));
  try {
    const [lists, songs] = await Promise.all([
      api('/user/songlists'),
      api('/user/fav', { query: { page: 1 } }),
    ]);
    state.fav.songlists = lists.items || [];
    state.fav.songs = songs.items || [];
    state.fav.page = 1;
    renderSonglistCards($('#fav-songlists'), state.fav.songlists, '暂无收藏歌单');
    renderSongList($('#fav-songs'), state.fav.songs, state.fav.sel, '暂无收藏歌曲');
    state.fav.hasMore = state.fav.songs.length >= 30;
    $('#fav-more').classList.toggle('hidden', !state.fav.hasMore);
    updateBulkBar($('#fav-all'), $('#fav-invert'), null, state.fav.sel, state.fav.songs);
  } catch (err) {
    handleError(err, { silent: err.code === 'not_logged_in' });
    $('#fav-songlists').innerHTML = '<div class="empty">加载失败</div>';
    renderSongList($('#fav-songs'), [], state.fav.sel, '加载失败');
  } finally {
    state.fav.loading = false;
  }
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
    updateBulkBar($('#fav-all'), $('#fav-invert'), null, state.fav.sel, state.fav.songs);
  } catch (err) {
    handleError(err);
  }
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
    updateBulkBar($('#songlist-all'), $('#songlist-invert'), null, state.songlist.sel, state.songlist.songs);
  } catch (err) {
    handleError(err);
    renderSongList($('#songlist-songs'), [], state.songlist.sel, '加载失败');
  } finally {
    state.songlist.loading = false;
  }
}

/* ---------------- 登录：二维码 / 手机号 ---------------- */
function openLogin() {
  $('#modal-login').classList.remove('hidden');
  switchLoginTab(state.login.mode || 'qq');
}

function closeLogin() {
  stopQrPolling();
  $('#modal-login').classList.add('hidden');
}

function switchLoginTab(mode) {
  state.login.mode = mode;
  $$('#login-tabs .tab').forEach((tab) => tab.classList.toggle('active', tab.dataset.tab === mode));
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
  $('#qr-image').innerHTML = '<div class="spinner"></div>';
  $('#qr-mask').classList.add('hidden');
  $('#qr-hint').textContent = '正在获取二维码…';
  try {
    const data = await api('/login/qrcode', { method: 'POST', body: { type: mode } });
    state.login.sessionId = data.session_id || '';
    $('#qr-image').innerHTML = `<img src="${esc(data.image || '')}" alt="登录二维码" />`;
    $('#qr-hint').textContent = mode === 'wx' ? '请使用微信扫码登录' : '请使用手机 QQ 扫码登录';
    pollQrLogin();
  } catch (err) {
    handleError(err);
    $('#qr-hint').textContent = '二维码获取失败，点击重试';
    $('#qr-image').innerHTML = '<div class="empty">获取失败</div>';
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
    if (st === 'scanned' || st === 'confirm' || st === 'scan') {
      $('#qr-hint').textContent = '已扫码，请在手机上确认';
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

function renderLyrics(lines) {
  state.player.lyrics = lines;
  state.player.index = -1;
  const box = $('#player-lyric');
  if (!lines.length) {
    box.innerHTML = '<div class="lyric-empty">暂无歌词</div>';
    return;
  }
  box.innerHTML = lines
    .map((line, i) => `<div class="lyric-line" data-index="${i}" data-time="${line.time}">${esc(line.text)}${line.trans ? `<span class="lyric-trans">${esc(line.trans)}</span>` : ''}</div>`)
    .join('');
}

function syncLyric(currentTime) {
  const lines = state.player.lyrics;
  if (!lines.length) return;
  let idx = -1;
  for (let i = 0; i < lines.length; i += 1) {
    if (currentTime + 0.15 >= lines[i].time) idx = i; else break;
  }
  if (idx === state.player.index) return;
  state.player.index = idx;
  const box = $('#player-lyric');
  $$('.lyric-line', box).forEach((el, i) => el.classList.toggle('active', i === idx));
  const active = box.querySelector('.lyric-line.active');
  if (active) {
    const target = active.offsetTop - box.clientHeight / 2 + active.clientHeight / 2;
    box.scrollTo({ top: Math.max(0, target), behavior: 'smooth' });
  }
}

async function openPlayer(songmid, songList = []) {
  if (!songmid) return;
  const player = $('#player');
  player.classList.remove('hidden');
  document.body.classList.add('has-player');
  const cached = (songList || []).find((s) => s.songmid === songmid);
  state.player.songmid = songmid;
  state.player.list = songList || [];
  $('#player-title').textContent = cached ? cached.name : '加载中…';
  $('#player-artist').textContent = cached ? cached.singer : '';
  $('#player-cover').style.backgroundImage = cached && cached.album_pmid ? `url('${coverUrl(cached.album_pmid)}')` : '';
  $('#player-lyric').innerHTML = '<div class="lyric-empty">歌词加载中…</div>';
  const audio = $('#player-audio');
  audio.pause();
  audio.removeAttribute('src');

  try {
    const [detail, urlData] = await Promise.all([
      api(`/song/${encodeURIComponent(songmid)}`),
      api('/song/url', { method: 'POST', body: { songmid } }),
    ]);
    const song = detail.song || cached || {};
    $('#player-title').textContent = song.name || songmid;
    $('#player-artist').textContent = song.singer || '';
    if (song.album_pmid) $('#player-cover').style.backgroundImage = `url('${coverUrl(song.album_pmid)}')`;
    audio.src = urlData.url;
    audio.play().catch(() => {});
    toast(`试听音质：${urlData.quality_label || urlData.quality || '默认'}（下载时自动取账号最高音质）`, 'info', 2600);
  } catch (err) {
    handleError(err);
  }

  try {
    const lyricData = await api(`/song/${encodeURIComponent(songmid)}/lyric`, { query: { trans: 1 } });
    const main = parseLrc(lyricData.lyric);
    const trans = parseLrc(lyricData.translation);
    renderLyrics(mergeTranslation(main, trans));
  } catch (err) {
    $('#player-lyric').innerHTML = '<div class="lyric-empty">暂无歌词</div>';
  }
}

function closePlayer() {
  const audio = $('#player-audio');
  audio.pause();
  audio.removeAttribute('src');
  $('#player').classList.add('hidden');
  document.body.classList.remove('has-player');
  state.player.songmid = '';
  state.player.lyrics = [];
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
  const pct = done ? 100 : Math.max(0, Math.min(100, Math.round((Number(progress) || 0) * 100)));
  const cls = ['bar', failed ? 'failed' : '', (!done && !failed && pct <= 0) ? 'indeterminate' : ''].filter(Boolean).join(' ');
  return `
    <div class="progress-row">
      <span class="label">${label}</span>
      <div class="${cls}"><i class="bar-fill" style="width:${pct}%"></i></div>
      <span class="pct">${done ? '完成' : failed ? '失败' : (pct ? `${pct}%` : '进行中')}</span>
      <span class="extra">${esc(extra)}</span>
    </div>`;
}

function renderTasks() {
  const items = state.tasks.items || [];
  const counts = state.tasks.counts || {};
  $('#tasks-summary').textContent = `进行中 ${counts.running || 0} · 成功 ${counts.success || 0} · 失败 ${counts.failed || 0}`;
  const box = $('#task-list');
  if (!items.length) {
    box.innerHTML = '<div class="empty">暂无下载任务，去首页挑选歌曲吧</div>';
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

async function refreshTasks() {
  try {
    const data = await api('/tasks');
    state.tasks.items = data.tasks || [];
    state.tasks.counts = data.counts || {};
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
      setTimeout(startTaskPolling, 8000);
    }
  }, 1500);
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

async function clearTasks() {
  if (!window.confirm('确定清空已完成的任务记录吗？')) return;
  try {
    const data = await withLoading(() => api('/tasks/clear', { method: 'POST', query: { scope: 'finished' } }));
    toast(`已清理 ${data.removed || 0} 条完成记录`, 'success');
    await refreshTasks();
    updateTaskBadge();
  } catch (err) {
    handleError(err);
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
    </tr>`;
}

async function loadHistory() {
  const box = $('#history-body');
  box.innerHTML = '<tr><td colspan="4" class="empty">加载中…</td></tr>';
  try {
    const data = await api('/history');
    state.history.items = data.items || [];
    state.history.record.clear();
    $('#history-summary').textContent = state.history.items.length ? `共 ${data.total || state.history.items.length} 条记录` : '';
    if (!state.history.items.length) {
      box.innerHTML = '<tr><td colspan="4" class="empty">暂无下载历史</td></tr>';
      return;
    }
    box.innerHTML = state.history.items.map(historyRowHtml).join('');
    observeHistoryRows();
  } catch (err) {
    handleError(err);
    box.innerHTML = '<tr><td colspan="4" class="empty">加载失败</td></tr>';
  }
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
}

async function clearHistory() {
  if (!window.confirm('确定清空下载历史吗？（不影响已下载文件）')) return;
  try {
    await withLoading(() => api('/history/clear', { method: 'POST' }));
    toast('历史已清空', 'success');
    await loadHistory();
  } catch (err) {
    handleError(err);
  }
}

/* ---------------- 设置 ---------------- */
async function loadSettings() {
  try {
    const data = await api('/settings');
    state.settings = data.settings || {};
    state.authorizedDirs = data.authorized_dirs || [];
    state.authorizedHint = data.authorized_hint || '';
    const s = state.settings;
    $('#setting-lyric-trans').checked = !!s.lyric_trans;
    renderDirOptions();
    $('#setting-interval-min').value = s.interval_min_ms || 300;
    $('#setting-interval-max').value = s.interval_max_ms || 800;
  } catch (err) {
    handleError(err);
  }
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
    await loadSettings();
  } catch (err) {
    handleError(err);
    await loadSettings();
  }
}

async function saveSettings() {
  const payload = {
    lyric_trans: $('#setting-lyric-trans').checked,
    download_dir: $('#setting-download-dir').value.trim(),
    interval_min_ms: Number($('#setting-interval-min').value) || 300,
    interval_max_ms: Number($('#setting-interval-max').value) || 800,
  };
  if (payload.interval_max_ms < payload.interval_min_ms) {
    toast('最大间隔不能小于最小间隔', 'warn');
    return;
  }
  try {
    const data = await withLoading(() => api('/settings', { method: 'POST', body: payload }));
    state.settings = data.settings || state.settings;
    toast('设置已保存', 'success');
  } catch (err) {
    handleError(err);
  }
}

/* 通过飞牛文件授权选择下载目录 */
async function chooseDownloadDir() {
  if (!window.trimApp) {
    toast('请在飞牛 fnOS 应用中打开此页面以选择目录', 'warn');
    return;
  }
  try {
    const paths = await withLoading(() => requestUserDirectory(window.trimApp));
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

function bindBulkControls({ container, songs, sel, allSel, invertBtn, downloadBtn, bulkBar }) {
  if (allSel) {
    allSel.addEventListener('change', () => {
      selectAll(sel, songs(), allSel.checked);
      rerenderWithSelection(container, songs(), sel);
      updateBulkBar(allSel, invertBtn, bulkBar, sel, songs());
    });
  }
  if (invertBtn) {
    invertBtn.addEventListener('click', () => {
      invertSelection(sel, songs());
      rerenderWithSelection(container, songs(), sel);
      updateBulkBar(allSel, invertBtn, bulkBar, sel, songs());
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
        updateBulkBar(allSel, invertBtn, bulkBar, sel, songs());
        startTaskPolling();
        switchView('tasks');
      }
    });
  }
}

function bindModals() {
  $$('.modal-close').forEach((btn) => {
    btn.addEventListener('click', () => {
      const modal = btn.closest('.modal');
      if (modal) modal.classList.add('hidden');
    });
  });
  $$('.modal').forEach((modal) => {
    modal.addEventListener('click', (ev) => {
      if (ev.target === modal) modal.classList.add('hidden');
    });
  });
  $('#qr-mask').addEventListener('click', () => startQrLogin(state.login.mode));
}

function bindEvents() {
  $('#nav').addEventListener('click', (ev) => {
    const item = ev.target.closest('.nav-item');
    if (item) switchView(item.dataset.view);
  });
  $('#btn-login').addEventListener('click', openLogin);
  $('#btn-logout').addEventListener('click', logout);

  $$('#login-tabs .tab').forEach((tab) => {
    tab.addEventListener('click', () => switchLoginTab(tab.dataset.tab));
  });
  $('#btn-send-code').addEventListener('click', sendSmsCode);
  $('#btn-phone-login').addEventListener('click', submitPhoneLogin);
  $('#login-code').addEventListener('keydown', (ev) => { if (ev.key === 'Enter') submitPhoneLogin(); });

  $('#search-form').addEventListener('submit', (ev) => {
    ev.preventDefault();
    loadSearch({ reset: true });
  });
  $('#search-type').addEventListener('change', () => {
    if ($('#search-input').value.trim()) loadSearch({ reset: true });
  });
  $('#btn-search-more').addEventListener('click', () => {
    state.search.page += 1;
    loadSearch({});
  });

  $('#btn-reload-recommend').addEventListener('click', loadHome);
  bindSonglistCards($('#recommend-songlists'));

  $('#btn-reload-fav').addEventListener('click', loadFav);
  $('#btn-fav-more').addEventListener('click', loadMoreFav);
  bindSonglistCards($('#fav-songlists'));

  bindSonglistCards($('#search-songlists'));

  $('#btn-tasks-clear-finished').addEventListener('click', clearTasks);
  $('#btn-tasks-clear-all').addEventListener('click', clearTasks);

  $('#btn-history-reload').addEventListener('click', loadHistory);
  $('#btn-history-clear').addEventListener('click', clearHistory);

  $('#btn-save-settings').addEventListener('click', saveSettings);
  $('#btn-choose-dir').addEventListener('click', chooseDownloadDir);
  $('#setting-download-dir').addEventListener('change', saveDownloadDirFromSelect);

  $('#btn-player-close').addEventListener('click', closePlayer);
  $('#player-audio').addEventListener('timeupdate', (ev) => syncLyric(ev.target.currentTime));
  $('#player-audio').addEventListener('ended', () => syncLyric(0));
  $('#player-lyric').addEventListener('click', (ev) => {
    const line = ev.target.closest('.lyric-line');
    if (!line) return;
    const audio = $('#player-audio');
    audio.currentTime = Number(line.dataset.time) || 0;
    audio.play().catch(() => {});
  });

  $('#task-list').addEventListener('click', (ev) => {
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
    bulkBar: null,
  });
  bindBulkControls({
    container: $('#search-results'),
    songs: () => state.search.items,
    sel: state.search.sel,
    allSel: $('#search-all'),
    invertBtn: $('#search-invert'),
    downloadBtn: $('#search-download'),
    bulkBar: null,
  });
  bindBulkControls({
    container: $('#fav-songs'),
    songs: () => state.fav.songs,
    sel: state.fav.sel,
    allSel: $('#fav-all'),
    invertBtn: $('#fav-invert'),
    downloadBtn: $('#fav-download'),
    bulkBar: null,
  });
  bindBulkControls({
    container: $('#songlist-songs'),
    songs: () => state.songlist.songs,
    sel: state.songlist.sel,
    allSel: $('#songlist-all'),
    invertBtn: $('#songlist-invert'),
    downloadBtn: $('#songlist-download'),
    bulkBar: null,
  });

  $('#btn-songlist-more').addEventListener('click', () => {
    state.songlist.page += 1;
    loadSonglistPage(false);
  });

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
  initSdk();
  await refreshLoginStatus();
  await refreshTasks();
  updateTaskBadge();
  startTaskPolling();
  switchView('home');
  try {
    const data = await api('/status');
  } catch (err) { /* 忽略 */ }
}

init();
