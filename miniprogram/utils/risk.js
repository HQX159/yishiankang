// utils/risk.js — 「建议尽快就医」提示的统一入口
//
// 什么时候用
//   后端生成食谱 / 膳食计划前会做健康风险扫描。报告指标达危险水平时，
//   接口返回 HTTP 409 + {"code": 1001, "message": "...", "data": {"risk": {...}}}，
//   且**不会生成食谱**。这时不该弹 toast（文案长、会被截断），而应跳到专门页面讲清楚。
//
// 为什么要单独一个模块
//   「生成食谱」和「膳食计划」两个页面都要处理这个情况，
//   错误体形状（data.risk / 顶层 risk）也两处都要兼容 —— 放一处避免抄两遍抄歪。

const RISK_KEY = 'lastRiskAlert';

/** 是否是「健康风险拦截」错误（code 1001） */
function isBlockedError(err) {
  return !!(err && err.code === 1001);
}

/** 从错误体里取出 risk 详情，兼容 data.risk 与顶层 risk 两种形状 */
function pickRisk(err) {
  if (!err) return {};
  if (err.data && err.data.risk) return err.data.risk;
  if (err.risk) return err.risk;
  return {};
}

/** 把 risk 详情暂存起来（页面间数据量较大，用 storage 传比拼 URL 稳） */
function saveRisk(risk) {
  try {
    wx.setStorageSync(RISK_KEY, risk || {});
  } catch (e) {}
}

/** 读取暂存的 risk 详情 */
function readRisk() {
  try {
    return wx.getStorageSync(RISK_KEY) || {};
  } catch (e) {
    return {};
  }
}

/** 用完即清，避免下次误显示上一次的内容 */
function clearRisk() {
  try {
    wx.removeStorageSync(RISK_KEY);
  } catch (e) {}
}

/**
 * 跳到「建议就医」页（已经拿到 risk 对象时用这个）
 *
 * 与 handleIfBlocked 的区别
 *   上传报告：报告存好了、上传本身是成功的（code 0），只是顺带发现危值 ——
 *   这不是错误，走不到 catch 分支，所以要把 data.risk 直接交给本函数。
 */
function openRiskPage(risk) {
  const payload = risk || {};
  saveRisk(payload);
  wx.navigateTo({
    url: '/pages/risk-alert/risk-alert',
    fail() {
      // 跳转失败时兜底：至少把后端给的原话显示出来
      wx.showModal({
        title: '建议尽快就医',
        content: payload.message || '部分指标已达需要就医的水平，请尽快就医。',
        showCancel: false
      });
    }
  });
}

/**
 * 统一处理：拦截错误 → 跳「建议就医」页
 * @returns {boolean} true 表示已按拦截处理（调用方不要再弹 toast）
 */
function handleIfBlocked(err) {
  if (!isBlockedError(err)) return false;
  openRiskPage(pickRisk(err));
  return true;
}

module.exports = {
  RISK_KEY,
  isBlockedError,
  pickRisk,
  saveRisk,
  readRisk,
  clearRisk,
  openRiskPage,
  handleIfBlocked
};
