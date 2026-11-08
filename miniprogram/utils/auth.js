// utils/auth.js — 登录态管理
const app = getApp();

/**
 * 确保已登录，返回 token
 */
function ensureLogin() {
  const token = wx.getStorageSync('token');
  if (token) {
    return Promise.resolve(token);
  }
  return app.doLogin();
}

/**
 * 检查是否已登录
 */
function isLoggedIn() {
  return !!wx.getStorageSync('token');
}

/**
 * 获取当前用户信息
 */
function getUserInfo() {
  return wx.getStorageSync('userInfo') || null;
}

/**
 * 退出登录
 */
function logout() {
  app.logout();
}

module.exports = { ensureLogin, isLoggedIn, getUserInfo, logout };
