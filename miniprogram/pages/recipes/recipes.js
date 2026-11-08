// pages/recipes/recipes.js
const api = require('../../utils/api');

function fmtRecipe(r) {
  return {
    id: r.id,
    title: r.title,
    meta: (r.cooking_time || '未知') + ' 分钟 · ' + (r.servings || '-') + ' 人份 · ' + (r.created_at || ''),
    ingPreview: (r.ingredients || '').substring(0, 100)
  };
}

Page({
  data: {
    displayRecipes: [],
    showList: false,
    showLoadMore: false,
    loading: false,
    loadMoreText: '加载更多',
    page: 1,
    perPage: 20,
    hasMore: false,
    allRecipes: []
  },

  onShow() {
    this.setData({ page: 1, allRecipes: [], displayRecipes: [], showList: false });
    this.loadRecipes();
  },

  onPullDownRefresh() {
    this.setData({ page: 1, allRecipes: [], displayRecipes: [], showList: false });
    this.loadRecipes().then(() => wx.stopPullDownRefresh());
  },

  onReachBottom() { this.loadMore(); },

  async loadRecipes() {
    this.setData({ loading: true, loadMoreText: '加载中...' });
    try {
      const data = await api.get('/api/v1/recipes', {
        page: this.data.page, per_page: this.data.perPage
      });
      const formatted = (data.recipes || []).map(fmtRecipe);
      const all = this.data.page === 1 ? formatted : [...this.data.allRecipes, ...formatted];
      const hasMore = data.page < data.pages;
      this.setData({
        allRecipes: all,
        displayRecipes: all,
        showList: all.length > 0,
        hasMore: hasMore,
        showLoadMore: hasMore,
        loading: false,
        loadMoreText: '加载更多'
      });
    } catch (e) {
      this.setData({ loading: false, loadMoreText: '加载更多' });
    }
  },

  async loadMore() {
    if (this.data.loading || !this.data.hasMore) return;
    this.setData({ page: this.data.page + 1, loading: true, loadMoreText: '加载中...' });
    await this.loadRecipes();
  },

  goDetail(e) {
    const id = e.currentTarget.dataset.id;
    wx.navigateTo({ url: '/pages/recipe-detail/recipe-detail?id=' + id });
  },
  goGenerate() { wx.switchTab({ url: '/pages/generate/generate' }); }
});
