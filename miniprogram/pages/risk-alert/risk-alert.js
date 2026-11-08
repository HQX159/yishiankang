// pages/risk-alert/risk-alert.js
//
// 「建议尽快就医」提示页
//   由 generate / meal-plan 页面在后端返回 code 1001 时跳转过来，
//   数据经 utils/risk.js 暂存后在本页读出（读完即清）。
//
// 为什么单独做一个页面而不是弹窗
//   wx.showToast 最多两行，装不下「哪几项超标 + 下一步怎么办」；
//   医疗提示必须能完整、反复读到，所以给一个真正的页面。

const risk = require('../../utils/risk');

Page({
  data: {
    message: '',
    findings: [],
    hasFindings: false,
    disclaimer: ''
  },

  onLoad() {
    const payload = risk.readRisk();
    risk.clearRisk();   // 读完即清，避免下次误显示上一次的内容

    const findings = (payload.findings || []).map(function (f) {
      return {
        level: f.level || 'warn',
        tag: f.level === 'critical' ? '需就医' : '偏高',
        message: f.message || ''
      };
    });

    this.setData({
      message: payload.message ||
        '根据您上传的健康报告，部分指标已达到需要及时就医的水平，因此本次没有生成食谱。',
      findings: findings,
      hasFindings: findings.length > 0,
      disclaimer: payload.disclaimer || ''
    });
  },

  goUploadHealth() {
    wx.navigateTo({ url: '/pages/upload-health/upload-health' });
  },

  goBack() {
    const pages = getCurrentPages();
    if (pages.length > 1) {
      wx.navigateBack({ delta: 1 });
    } else {
      wx.switchTab({ url: '/pages/index/index' });
    }
  }
});
