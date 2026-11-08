// pages/generate/generate.js
const api = require('../../utils/api');
const risk = require('../../utils/risk');

Page({
  data: {
    query: '',
    generating: false,
    btnText: '开始生成个性化食谱'
  },

  onQueryInput(e) {
    this.setData({ query: e.detail.value });
  },

  async generate() {
    if (this.data.generating) return;
    this.setData({ generating: true, btnText: 'AI 生成中...' });

    try {
      const data = await api.post('/api/v1/recipes/generate', {
        query: this.data.query || undefined
      });
      this.setData({ generating: false, btnText: '开始生成个性化食谱' });
      if (data && data.recipe) {
        wx.navigateTo({
          url: '/pages/recipe-detail/recipe-detail?id=' + data.recipe.id
        });
      }
    } catch (e) {
      this.setData({ generating: false, btnText: '开始生成个性化食谱' });
      // 报告指标达危险水平：后端未生成食谱，跳「建议就医」页（不再弹无意义的 toast）
      if (risk.handleIfBlocked(e)) return;
      wx.showToast({ title: '生成失败，请稍后重试', icon: 'none' });
    }
  }
});
