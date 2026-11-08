// pages/meal-plan/meal-plan.js
const api = require('../../utils/api');
const risk = require('../../utils/risk');

Page({
  data: {
    dayOptions: ['3 天', '7 天', '14 天', '30 天'],
    daysValues: [3, 7, 14, 30],
    dayIndex: 1,
    preference: '',
    generating: false,
    btnText: '生成膳食计划',
    showResult: false,
    showError: false,
    planDays: [],
    hasAdvice: false,
    advice: ''
  },

  onDayChange(e) { this.setData({ dayIndex: Number(e.detail.value) }); },
  onPrefInput(e) { this.setData({ preference: e.detail.value }); },

  async generate() {
    if (this.data.generating) return;
    this.setData({
      generating: true, btnText: '生成中...',
      showResult: false, showError: false
    });

    try {
      const days = this.data.daysValues[this.data.dayIndex];
      const data = await api.post('/api/v1/recipes/meal_plan', {
        days: days,
        preference: this.data.preference
      });
      const plan = data.meal_plan || {};
      const daysArr = plan.days || [];
      const hasAdvice = !!(plan.advice);
      this.setData({
        generating: false, btnText: '生成膳食计划',
        showResult: daysArr.length > 0,
        showError: daysArr.length === 0,
        planDays: daysArr,
        hasAdvice: hasAdvice,
        advice: plan.advice || ''
      });
    } catch (e) {
      this.setData({
        generating: false, btnText: '生成膳食计划',
        showError: false, showResult: false
      });
      // 报告指标达危险水平：后端未生成膳食计划，跳「建议就医」页
      if (risk.handleIfBlocked(e)) return;
      this.setData({ showError: true });
      wx.showToast({ title: '生成失败，请重试', icon: 'none' });
    }
  }
});
