// utils/util.js — 通用工具函数

/**
 * 格式化时间
 */
function formatTime(date) {
  if (!date) return '';
  const d = new Date(date);
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function formatDateTime(date) {
  if (!date) return '';
  const d = new Date(date);
  const hour = String(d.getHours()).padStart(2, '0');
  const minute = String(d.getMinutes()).padStart(2, '0');
  return `${formatTime(date)} ${hour}:${minute}`;
}

/**
 * 格式化音频时长
 */
function formatDuration(seconds) {
  if (!seconds || isNaN(seconds)) return '0:00';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

/**
 * 文本截断
 */
function truncate(text, maxLen = 100) {
  if (!text) return '';
  if (text.length <= maxLen) return text;
  return text.slice(0, maxLen) + '...';
}

/**
 * 防抖
 */
function debounce(fn, delay = 300) {
  let timer = null;
  return function (...args) {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), delay);
  };
}

/**
 * 显示 Toast
 */
function showToast(title, icon = 'none') {
  wx.showToast({ title, icon, duration: 2000 });
}

/**
 * 确认对话框
 */
function showConfirm(title, content) {
  return new Promise((resolve) => {
    wx.showModal({
      title,
      content,
      success(res) { resolve(res.confirm); }
    });
  });
}

module.exports = {
  formatTime,
  formatDateTime,
  formatDuration,
  truncate,
  debounce,
  showToast,
  showConfirm
};
