// utils/api.js — API 请求封装
const app = getApp();

const BASE_URL = app.globalData.baseUrl;

/**
 * 统一请求封装
 */
function request(method, url, data = {}, options = {}) {
  const token = wx.getStorageSync('token');
  const headers = {
    'Content-Type': 'application/json',
    ...options.header
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  return new Promise((resolve, reject) => {
    wx.request({
      url: `${BASE_URL}${url}`,
      method,
      data,
      header: headers,
      timeout: options.timeout || 30000,
      success(res) {
        if (res.statusCode === 401) {
          // Token 过期，清除并重新登录
          wx.removeStorageSync('token');
          app.globalData.token = null;
          app.doLogin().then(() => {
            // 重试原请求
            request(method, url, data, options).then(resolve).catch(reject);
          }).catch(() => {
            wx.showToast({ title: '登录失败，请重启小程序', icon: 'none' });
            reject(res);
          });
        } else if (res.data && res.data.code === 0) {
          resolve(res.data.data);
        } else if (res.data && res.data.code === 1001) {
          // 健康风险拦截（HTTP 409）：文案较长、需完整展示，因此**不弹 toast**，
          // 由调用方通过 utils/risk.js 跳到「建议就医」页。
          reject(res.data);
        } else {
          const msg = (res.data && res.data.message) || '请求失败';
          wx.showToast({ title: msg, icon: 'none' });
          reject(res.data || res);
        }
      },
      fail(err) {
        wx.showToast({ title: '网络请求失败', icon: 'none' });
        reject(err);
      }
    });
  });
}

function get(url, params = {}, options = {}) {
  const query = Object.keys(params)
    .filter(k => params[k] !== undefined && params[k] !== null)
    .map(k => `${k}=${encodeURIComponent(params[k])}`)
    .join('&');
  const fullUrl = query ? `${url}?${query}` : url;
  return request('GET', fullUrl, {}, options);
}

function post(url, data = {}, options = {}) {
  return request('POST', url, data, options);
}

function put(url, data = {}, options = {}) {
  return request('PUT', url, data, options);
}

function del(url, options = {}) {
  return request('DELETE', url, {}, options);
}

/**
 * 文件上传
 */
function uploadFile(filePath, url, formData = {}) {
  const token = wx.getStorageSync('token');

  return new Promise((resolve, reject) => {
    wx.uploadFile({
      url: `${BASE_URL}${url}`,
      filePath,
      name: 'file',
      formData,
      header: {
        'Authorization': token ? `Bearer ${token}` : ''
      },
      timeout: 60000,
      success(res) {
        try {
          const data = JSON.parse(res.data);
          if (data.code === 0) {
            resolve(data.data);
          } else {
            wx.showToast({ title: data.message || '上传失败', icon: 'none' });
            reject(data);
          }
        } catch (e) {
          reject(res);
        }
      },
      fail(err) {
        wx.showToast({ title: '上传失败', icon: 'none' });
        reject(err);
      }
    });
  });
}

module.exports = { get, post, put, del, uploadFile, BASE_URL };
