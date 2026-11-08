// pages/profile/profile.js
const auth = require('../../utils/auth');
const api = require('../../utils/api');

Page({
  data: {
    displayName: '微信用户',
    recipeCount: 0,
    favoriteCount: 0,
    reportCount: 0
  },

  onShow() {
    const ui = auth.getUserInfo();
    this.setData({ displayName: (ui && ui.username) ? ui.username : '微信用户' });
    this.loadStats();
  },

  async loadStats() {
    try {
      const data = await api.get('/api/v1/dashboard');
      const stats = data.stats || {};
      this.setData({
        recipeCount: stats.recipe_count || 0,
        favoriteCount: stats.favorite_count || 0,
        reportCount: stats.report_count || 0
      });
    } catch (e) {}
  },

  goUploadHealth() { wx.navigateTo({ url: '/pages/upload-health/upload-health' }); },
  goUploadFridge() { wx.navigateTo({ url: '/pages/upload-fridge/upload-fridge' }); },
  goMealPlan() { wx.navigateTo({ url: '/pages/meal-plan/meal-plan' }); },
  goRecipes() { wx.switchTab({ url: '/pages/recipes/recipes' }); },
  goFavorites() { wx.switchTab({ url: '/pages/favorites/favorites' }); },

  logout() {
    wx.showModal({
      title: '退出登录',
      content: '确定要退出登录吗？',
      success: (res) => {
        if (res.confirm) {
          auth.logout();
          this.setData({ displayName: '微信用户', recipeCount: 0, favoriteCount: 0, reportCount: 0 });
          wx.showToast({ title: '已退出', icon: 'success' });
        }
      }
    });
  }
});
