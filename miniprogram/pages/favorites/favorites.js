// pages/favorites/favorites.js
const api = require('../../utils/api');
const util = require('../../utils/util');

function fmtRecipe(r) {
  return {
    id: r.id,
    title: r.title,
    meta: (r.cooking_time || '未知') + ' 分钟 · ' + (r.servings || '-') + ' 人份'
  };
}

Page({
  data: {
    displayRecipes: [],
    showList: false
  },

  onShow() { this.loadFavorites(); },
  onPullDownRefresh() {
    this.loadFavorites().then(() => wx.stopPullDownRefresh());
  },

  async loadFavorites() {
    try {
      const data = await api.get('/api/v1/recipes/favorites');
      const formatted = (data.recipes || []).map(fmtRecipe);
      this.setData({ displayRecipes: formatted, showList: formatted.length > 0 });
    } catch (e) {
      console.error('加载收藏失败:', e);
    }
  },

  async removeFav(e) {
    const id = e.currentTarget.dataset.id;
    const confirmed = await util.showConfirm('取消收藏', '确定要取消收藏这个食谱吗？');
    if (!confirmed) return;
    try {
      await api.del('/api/v1/recipes/' + id + '/favorite');
      const recipes = this.data.displayRecipes.filter(r => r.id !== id);
      this.setData({ displayRecipes: recipes, showList: recipes.length > 0 });
      util.showToast('已取消收藏');
    } catch (e) {}
  },

  goDetail(e) {
    const id = e.currentTarget.dataset.id;
    wx.navigateTo({ url: '/pages/recipe-detail/recipe-detail?id=' + id });
  },
  goRecipes() { wx.switchTab({ url: '/pages/recipes/recipes' }); }
});
