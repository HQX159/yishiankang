// app.js — 全局入口
App({
  globalData: {
    userInfo: null,
    token: null,
    baseUrl: 'http://127.0.0.1:5000'  // 修改为实际后端地址
  },

  onLaunch() {
    // 读取本地 token
    const token = wx.getStorageSync('token');
    const userInfo = wx.getStorageSync('userInfo');
    if (token) {
      this.globalData.token = token;
      this.globalData.userInfo = userInfo;
    }
    // 自动登录
    this.doLogin();
  },

  /**
   * 微信登录 → 获取 JWT token
   */
  doLogin() {
    return new Promise((resolve, reject) => {
      // 如果已有有效 token，直接返回
      if (this.globalData.token) {
        resolve(this.globalData.token);
        return;
      }

      wx.login({
        success: (loginRes) => {
          if (!loginRes.code) {
            console.error('wx.login 失败');
            this._handleLoginFail('获取微信登录凭证失败');
            reject(new Error('wx.login failed'));
            return;
          }
          // 调后端换取 JWT
          wx.request({
            url: `${this.globalData.baseUrl}/api/wechat/login`,
            method: 'POST',
            data: { code: loginRes.code },
            success: (res) => {
              if (res.statusCode === 200) {
                const token = res.data.token;
                const user = res.data.user;
                this.globalData.token = token;
                this.globalData.userInfo = user;
                wx.setStorageSync('token', token);
                wx.setStorageSync('userInfo', user);
                resolve(token);
              } else {
                console.error('登录失败:', res.data);
                this._handleLoginFail(res.data.message || '登录失败');
                reject(new Error(res.data.message || '登录失败'));
              }
            },
            fail: (err) => {
              console.error('登录请求失败:', err);
              this._handleLoginFail('无法连接服务器，请检查网络');
              reject(err);
            }
          });
        },
        fail: (err) => {
          console.error('wx.login 失败:', err);
          this._handleLoginFail('获取微信登录凭证失败');
          reject(err);
        }
      });
    });
  },

  /**
   * 登录失败提示 + 重试入口
   */
  _handleLoginFail(msg) {
    if (this._loginRetryShowing) return;
    this._loginRetryShowing = true;
    wx.showModal({
      title: '登录失败',
      content: msg || '请检查网络后重试',
      confirmText: '重试',
      cancelText: '取消',
      success: (r) => {
        this._loginRetryShowing = false;
        if (r.confirm) {
          this.doLogin();
        }
      }
    });
  },

  /**
   * 清除登录态
   */
  logout() {
    this.globalData.token = null;
    this.globalData.userInfo = null;
    wx.removeStorageSync('token');
    wx.removeStorageSync('userInfo');
  }
});
