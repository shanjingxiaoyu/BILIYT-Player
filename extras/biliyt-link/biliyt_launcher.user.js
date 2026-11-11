// ==UserScript==
// @name         ⚡ BiliYTPlayer 一键唤起
// @name:zh-CN   ⚡ BiliYTPlayer 一键唤起
// @namespace    https://github.com/shanjingxiaoyu/BILIYT-Player
// @version      1.1.1
// @updateURL    https://cdn.jsdelivr.net/gh/shanjingxiaoyu/BILIYT-Player@main/extras/biliyt-link/biliyt_launcher.user.js
// @downloadURL  https://cdn.jsdelivr.net/gh/shanjingxiaoyu/BILIYT-Player@main/extras/biliyt-link/biliyt_launcher.user.js
// @description  在 B 站 / YouTube 视频页注入可拖动的悬浮按钮：一键复制当前链接并唤起本地 BiliYTPlayer 播放（biliyt:// 协议）。
// @author       shanjingxiaoyu
// @match        *://www.bilibili.com/*
// @match        *://*.bilibili.com/*
// @match        *://bilibili.com/*
// @match        *://*.youtube.com/*
// @match        *://youtube.com/*
// @include      /^https?:\/\/([a-z0-9-]+\.)*bilibili\.com\//
// @include      /^https?:\/\/([a-z0-9-]+\.)*youtube\.com\//
// @icon         https://www.bilibili.com/favicon.ico
// @grant        GM_setClipboard
// @grant        GM_registerMenuCommand
// @grant        GM_getValue
// @grant        GM_setValue
// @run-at       document-idle
// ==/UserScript==

(function () {
  'use strict';

  const BTN_ID = 'biliyt-launch-btn';
  const TOAST_ID = 'biliyt-launch-toast';
  const PROTOCOL = 'biliyt'; // 自定义协议名，需与 Windows 注册表 / 软件侧一致
  const BTN_SIZE = 46;
  const POS_KEY = 'biliyt-launch-pos';
  const DRAG_THRESHOLD = 5;  // 移动超过 5px 才算拖拽，避免手抖误判

  // ---------- 是否视频页（决定按钮显隐） ----------
  function isVideoPage() {
    const p = location.pathname;
    const h = location.hostname;
    if (h === 'bilibili.com' || h.endsWith('.bilibili.com')) {
      // 视频 BV 页 + 番剧/电影播放页
      return p.startsWith('/video/') || p.startsWith('/bangumi/play/');
    }
    if (h === 'youtube.com' || h.endsWith('.youtube.com')) {
      return p.startsWith('/watch');
    }
    return false;
  }

  // ---------- 注入样式（圆形按钮 + toast，深浅色均可见） ----------
  const style = document.createElement('style');
  style.textContent = [
    `#${BTN_ID}{`,
    '  position:fixed; right:24px; bottom:24px; z-index:2147483647;',
    '  width:46px; height:46px; border:none; border-radius:50%;',
    '  background:linear-gradient(135deg,#23ade5,#fb7299);',
    '  box-shadow:0 4px 14px rgba(251,114,153,.4);',
    '  cursor:pointer; user-select:none; touch-action:none;',
    '  display:flex; align-items:center; justify-content:center;',
    '  transition:transform .15s ease, box-shadow .15s ease, background .2s ease;',
    '  outline:none;',
    '}',
    `#${BTN_ID}:hover{transform:translateY(-2px) scale(1.06); box-shadow:0 6px 20px rgba(251,114,153,.55)}`,
    `#${BTN_ID}:active{transform:translateY(0) scale(.96)}`,
    `#${BTN_ID}.ok{background:linear-gradient(135deg,#18b566,#2fd17c); box-shadow:0 4px 14px rgba(24,181,102,.45)}`,
    `#${BTN_ID}.err{background:linear-gradient(135deg,#e05a5a,#f08080)}`,
    // 拖拽中：禁 hover 位移与过渡，避免拖动时视觉抖动
    `#${BTN_ID}.dragging{transform:none !important; transition:none; opacity:.88; cursor:grabbing}`,
    `#${BTN_ID} svg{pointer-events:none}`,
    `#${TOAST_ID}{`,
    '  position:fixed; right:24px; bottom:80px; z-index:2147483647;',
    '  padding:8px 14px; border-radius:8px;',
    '  font:600 12px/1.4 "Microsoft YaHei",sans-serif; color:#fff;',
    '  background:rgba(24,181,102,.95); box-shadow:0 4px 12px rgba(0,0,0,.18);',
    '  opacity:0; transform:translateY(8px); pointer-events:none;',
    '  transition:opacity .2s, transform .2s; white-space:nowrap;',
    '}',
    `#${TOAST_ID}.show{opacity:1; transform:translateY(0)}`,
    `#${TOAST_ID}.err{background:rgba(224,90,90,.95)}`,
  ].join('\n');
  document.head.appendChild(style);

  // ---------- 创建按钮（圆形 SVG 播放图标） ----------
  const btn = document.createElement('button');
  btn.id = BTN_ID;
  btn.type = 'button';
  btn.title = '点击唤起 BiliYTPlayer｜按住拖动可移动位置';
  btn.setAttribute('aria-label', '点击唤起 BiliYTPlayer，或拖动移动位置');
  btn.innerHTML =
    '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" aria-hidden="true">' +
    '<path d="M8 5.2v13.6L19 12 8 5.2z" fill="#fff"/></svg>';
  btn.style.display = 'none';
  document.body.appendChild(btn);

  // ---------- toast ----------
  const toast = document.createElement('div');
  toast.id = TOAST_ID;
  document.body.appendChild(toast);

  let toastTimer = null;
  function showToast(text, ok) {
    toast.textContent = text;
    toast.classList.toggle('err', ok === false);
    toast.classList.add('show');
    btn.classList.remove('ok', 'err');
    if (ok === false) btn.classList.add('err');
    else btn.classList.add('ok');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      toast.classList.remove('show');
      btn.classList.remove('ok', 'err');
    }, 1600);
  }

  // ---------- 位置持久化（油猴存储优先，localStorage 兜底） ----------
  function loadPos() {
    try {
      if (typeof GM_getValue === 'function') {
        const v = GM_getValue(POS_KEY, null);
        if (v && Number.isFinite(v.x) && Number.isFinite(v.y)) return { x: v.x, y: v.y };
      }
    } catch (e) { /* ignore */ }
    try {
      const raw = localStorage.getItem(POS_KEY);
      if (raw) {
        const v = JSON.parse(raw);
        if (Number.isFinite(v.x) && Number.isFinite(v.y)) return { x: v.x, y: v.y };
      }
    } catch (e) { /* ignore */ }
    return null;
  }

  function savePos(p) {
    try {
      if (typeof GM_setValue === 'function') { GM_setValue(POS_KEY, p); return; }
    } catch (e) { /* ignore */ }
    try { localStorage.setItem(POS_KEY, JSON.stringify(p)); } catch (e) { /* ignore */ }
  }

  function defaultPos() {
    return {
      x: Math.max(0, window.innerWidth - BTN_SIZE - 24),
      y: Math.max(0, window.innerHeight - BTN_SIZE - 24),
    };
  }

  function applyPos(p) {
    const maxX = Math.max(0, window.innerWidth - BTN_SIZE);
    const maxY = Math.max(0, window.innerHeight - BTN_SIZE);
    const c = {
      x: Math.min(Math.max(0, p.x), maxX),
      y: Math.min(Math.max(0, p.y), maxY),
    };
    btn.style.left = c.x + 'px';
    btn.style.top = c.y + 'px';
    btn.style.right = 'auto';
    btn.style.bottom = 'auto';
    return c;
  }

  let pos = applyPos(loadPos() || defaultPos());

  // ---------- 拖拽（点击与拖拽区分，拖完不误触发唤起） ----------
  let dragging = false, moved = false, justDragged = false;
  let startX = 0, startY = 0, grabX = 0, grabY = 0;

  btn.addEventListener('pointerdown', (e) => {
    if (e.pointerType === 'mouse' && e.button !== 0) return;  // 只响应左键
    dragging = true;
    moved = false;
    startX = e.clientX;
    startY = e.clientY;
    // 用我们设置的 left/top 计算抓取偏移，避开 hover transform 造成的 2px 偏差
    const baseX = parseFloat(btn.style.left) || 0;
    const baseY = parseFloat(btn.style.top) || 0;
    grabX = e.clientX - baseX;
    grabY = e.clientY - baseY;
    try { btn.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    btn.classList.add('dragging');
  });

  btn.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    const dx = e.clientX - startX, dy = e.clientY - startY;
    if (!moved && Math.sqrt(dx * dx + dy * dy) < DRAG_THRESHOLD) return;
    moved = true;
    pos = applyPos({ x: e.clientX - grabX, y: e.clientY - grabY });
  });

  function endDrag(e) {
    if (!dragging) return;
    dragging = false;
    btn.classList.remove('dragging');
    try { btn.releasePointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    if (moved) {
      savePos(pos);
      // pointerup 之后浏览器仍会派发 click，这里打个标记把它吞掉
      justDragged = true;
      setTimeout(() => { justDragged = false; }, 80);
    }
  }
  btn.addEventListener('pointerup', endDrag);
  btn.addEventListener('pointercancel', endDrag);

  // 窗口尺寸变化后把按钮拉回可视区
  window.addEventListener('resize', () => { pos = applyPos(pos); });

  function resetPos() {
    pos = applyPos(defaultPos());
    savePos(pos);
    showToast('按钮位置已重置');
  }

  // ---------- 复制（必须在用户点击回调中调用） ----------
  function copyText(text) {
    return new Promise((resolve) => {
      const fallback = () => {
        // 兜底 1：油猴 GM_setClipboard
        try {
          if (typeof GM_setClipboard === 'function') { GM_setClipboard(text); resolve(true); return; }
        } catch (e) { /* ignore */ }
        // 兜底 2：execCommand
        try {
          const ta = document.createElement('textarea');
          ta.value = text;
          ta.style.cssText = 'position:fixed;opacity:0;top:0;left:0;';
          document.body.appendChild(ta);
          ta.focus();
          ta.select();
          const ok = document.execCommand('copy');
          document.body.removeChild(ta);
          resolve(ok);
        } catch (e) { resolve(false); }
      };

      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(() => resolve(true)).catch(fallback);
      } else {
        fallback();
      }
    });
  }

  // ---------- 唤起本地软件（自定义协议） ----------
  function triggerProtocol(url) {
    const protoUrl = PROTOCOL + '://open?url=' + encodeURIComponent(url);
    // 用隐藏 iframe 触发，避免干扰 B 站 / YouTube 的 SPA 路由
    const iframe = document.createElement('iframe');
    iframe.style.display = 'none';
    iframe.src = protoUrl;
    document.body.appendChild(iframe);
    setTimeout(() => { iframe.remove(); }, 2000);
  }

  // ---------- 三步点击：复制 → 唤起 → 反馈 ----------
  function launch() {
    const url = location.href;
    copyText(url).then((ok) => {
      triggerProtocol(url);
      showToast(ok ? '已复制 ✓ 正在唤起播放器…' : '正在唤起播放器…（复制失败）', ok);
    });
  }

  btn.addEventListener('click', (e) => {
    if (justDragged) { e.preventDefault(); e.stopPropagation(); return; }
    launch();
  });

  // ---------- SPA 地址变化监听（YouTube 切视频不刷新页面） ----------
  let lastUrl = location.href;
  function check() {
    if (location.href !== lastUrl) {
      lastUrl = location.href;
      updateVisibility();
    }
  }
  function updateVisibility() {
    btn.style.display = isVideoPage() ? '' : 'none';
  }

  window.addEventListener('yt-navigate-finish', check); // YouTube 内部导航完成事件
  window.addEventListener('popstate', check);
  // patch history API（pushState / replaceState 不触发 popstate）
  const origPush = history.pushState, origReplace = history.replaceState;
  history.pushState = function () { origPush.apply(this, arguments); check(); };
  history.replaceState = function () { origReplace.apply(this, arguments); check(); };
  // 兜底轮询（1s 一次，覆盖所有漏网之鱼）
  setInterval(check, 1000);

  // ---------- 油猴菜单入口 ----------
  if (typeof GM_registerMenuCommand === 'function') {
    GM_registerMenuCommand('⚡ 唤起 BiliYTPlayer 播放当前页', launch);
    GM_registerMenuCommand('↺ 重置悬浮按钮位置', resetPos);
  }

  // 初始状态
  updateVisibility();
})();
