// pages/index/index.js
const api = require('../../utils/api');

Page({
  data: {
    recipeCount: 0,
    favoriteCount: 0,
    reportCount: 0,
    ingredientCount: 0,
    hasHealth: false,
    healthContent: '',
    healthDate: '',
    hasIngredients: false,
    latestIngredients: [],
    hasRecipes: false,
    recentRecipes: []
  },

  onShow() {
    this.loadDashboard();
  },

  onPullDownRefresh() {
    this.loadDashboard().then(() => wx.stopPullDownRefresh());
  },

  async loadDashboard() {
    try {
      const data = await api.get('/api/v1/dashboard');
      const stats = data.stats || {};
      const health = data.latest_health;
      const ings = data.latest_ingredients || [];
      const recipes = data.recent_recipes || [];

      // 预计算食材显示名（置信度 + 名称）
      const displayIngs = ings.map(ing => ({
        name: ing.name,
        displayName: ing.name + ' (' + Math.round(ing.confidence * 100) + '%)'
      }));

      this.setData({
        recipeCount: stats.recipe_count || 0,
        favoriteCount: stats.favorite_count || 0,
        reportCount: stats.report_count || 0,
        ingredientCount: stats.ingredient_count || 0,
        hasHealth: !!health,
        healthContent: health ? (health.content_text || '').substring(0, 300) : '',
        healthDate: health ? (health.uploaded_at || '') : '',
        hasIngredients: displayIngs.length > 0,
        latestIngredients: displayIngs,
        hasRecipes: recipes.length > 0,
        recentRecipes: recipes
      });
    } catch (e) {
      console.error('加载仪表盘失败:', e);
    }
  },

  goRecipes() { wx.switchTab({ url: '/pages/recipes/recipes' }); },
  goFavorites() { wx.switchTab({ url: '/pages/favorites/favorites' }); },
  goGenerate() { wx.switchTab({ url: '/pages/generate/generate' }); },
  goFoodGuide() { wx.navigateTo({ url: '/pages/food-guide/food-guide' }); },
  goUploadHealth() { wx.navigateTo({ url: '/pages/upload-health/upload-health' }); },
  goUploadFridge() { wx.navigateTo({ url: '/pages/upload-fridge/upload-fridge' }); },
  goRecipeDetail(e) {
    const id = e.currentTarget.dataset.id;
    wx.navigateTo({ url: '/pages/recipe-detail/recipe-detail?id=' + id });
  }
});
