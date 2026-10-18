// ==UserScript==
// @name         ⚡ BiliYTPlayer 一键 MPV 播放
// @name:zh-CN   ⚡ BiliYTPlayer 一键 MPV 播放
// @namespace    https://github.com/shanjingxiaoyu/BILIYT-Player
// @version      1.0.0
// @description  复制当前视频完整链接（含分P/剧集参数）到剪贴板，唤起本地 BiliYTPlayer 播放。快捷键 Alt+P。
// @author       shanjingxiaoyu
// @match        https://www.bilibili.com/video/*
// @match        https://www.bilibili.com/bangumi/play/*
// @icon         https://www.bilibili.com/favicon.ico
// @grant        GM_setClipboard
// @grant        GM_registerMenuCommand
// @run-at       document-idle
// ==/UserScript==

(function () {
  'use strict';

  const BTN_ID = 'bili-mpv-play-btn';
  const TOAST_ID = 'bili-mpv-play-toast';

  // ---- 悬浮按钮样式（右下角，现代轻量） ----
  const style = document.createElement('style');
  style.textContent = `
    #${BTN_ID}{
      position:fixed; right:24px; bottom:88px; z-index:2147483647;
      display:flex; align-items:center; gap:6px;
      padding:9px 16px; border:none; border-radius:999px;
      background:linear-gradient(135deg,#23ade5,#fb7299);
      color:#fff; font:600 13px/1 "Microsoft YaHei",sans-serif;
      box-shadow:0 4px 14px rgba(251,114,153,.35);
      cursor:pointer; user-select:none;
      transition:transform .15s ease, box-shadow .15s ease, background .2s ease;
    }
    #${BTN_ID}:hover{transform:translateY(-2px); box-shadow:0 6px 20px rgba(251,114,153,.5)}
    #${BTN_ID}:active{transform:translateY(0) scale(.97)}
    #${BTN_ID}.ok{background:linear-gradient(135deg,#18b566,#2fd17c); box-shadow:0 4px 14px rgba(24,181,102,.4)}
    #${BTN_ID}.err{background:linear-gradient(135deg,#e05a5a,#f08080)}
    #${TOAST_ID}{
      position:fixed; right:24px; bottom:142px; z-index:2147483647;
      padding:8px 14px; border-radius:8px;
      font:600 12px/1.4 "Microsoft YaHei",sans-serif; color:#fff;
      background:rgba(24,181,102,.95); box-shadow:0 4px 12px rgba(0,0,0,.15);
      opacity:0; transform:translateY(8px);
      transition:opacity .2s, transform .2s; pointer-events:none;
    }
    #${TOAST_ID}.show{opacity:1; transform:translateY(0)}
  `;
  document.head.appendChild(style);

  // ---- 创建按钮与 toast ----
  const btn = document.createElement('button');
  btn.id = BTN_ID;
  btn.type = 'button';
  btn.innerHTML = '⚡ MPV 播放';
  document.body.appendChild(btn);

  const toast = document.createElement('div');
  toast.id = TOAST_ID;
  document.body.appendChild(toast);

  let toastTimer = null;

  function showToast(text, ok) {
    toast.textContent = text;
    toast.classList.add('show');
    btn.classList.add(ok === false ? 'err' : 'ok');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      toast.classList.remove('show');
      btn.classList.remove('ok', 'err');
    }, 1400);
  }

  function copyAndLaunch() {
    // window.location.href 天然包含 ?p=X（视频分P）或 /play/epXXXX（番剧剧集）
    const url = window.location.href;
    try {
      GM_setClipboard(url);
      showToast('已复制链接，正在唤起本地播放器…');
    } catch (e) {
      // GM_setClipboard 不可用时回退到 execCommand
      try {
        const ta = document.createElement('textarea');
        ta.value = url;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        showToast('已复制链接（回退模式）…');
      } catch (e2) {
        showToast('复制失败：' + (e2 && e2.message ? e2.message : '未知错误'), false);
      }
    }
  }

  btn.addEventListener('click', copyAndLaunch);

  // ---- Alt + P 快捷键 ----
  document.addEventListener('keydown', (e) => {
    if (
      e.altKey && !e.ctrlKey && !e.shiftKey && !e.metaKey &&
      (e.code === 'KeyP' || e.key === 'p' || e.key === 'P')
    ) {
      e.preventDefault();
      e.stopPropagation();
      copyAndLaunch();
    }
  }, true);

  // ---- 油猴菜单入口（可选） ----
  if (typeof GM_registerMenuCommand === 'function') {
    GM_registerMenuCommand('⚡ MPV 播放（复制链接）', copyAndLaunch);
  }
})();
