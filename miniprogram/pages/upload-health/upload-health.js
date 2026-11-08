// pages/upload-health/upload-health.js
const api = require('../../utils/api');
const risk = require('../../utils/risk');

Page({
  data: {
    filePath: '',
    fileName: '',
    uploading: false,
    btnText: '上传并解析',
    btnDisabled: true,
    showResult: false,
    resultText: '',
    riskWarn: ''          // 指标偏高（未到危险）时的一行提醒
  },

  // 拍照 / 从相册选图片
  chooseFromAlbum() {
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sourceType: ['album', 'camera'],
      success: (res) => {
        const file = res.tempFiles[0];
        this.setData({
          filePath: file.tempFilePath,
          fileName: '体检报告图片',
          btnDisabled: false
        });
      }
    });
  },

  // 从微信聊天选择文件（支持 PDF）
  chooseFromChat() {
    wx.chooseMessageFile({
      count: 1,
      type: 'file',
      extension: ['pdf', 'png', 'jpg', 'jpeg'],
      success: (res) => {
        const file = res.tempFiles[0];
        this.setData({
          filePath: file.path,
          fileName: file.name,
          btnDisabled: false
        });
      }
    });
  },

  async upload() {
    if (!this.data.filePath || this.data.uploading) return;
    this.setData({ uploading: true, btnText: '上传解析中...', btnDisabled: true });

    try {
      const data = await api.uploadFile(this.data.filePath, '/api/v1/upload/health_report');
      const report = data.report || {};
      const riskInfo = data.risk || {};

      this.setData({
        uploading: false,
        btnText: '上传并解析',
        btnDisabled: false,
        showResult: true,
        resultText: report.content_text || '解析完成',
        riskWarn: riskInfo.level === 'warn' ? (riskInfo.message || '') : ''
      });

      wx.showToast({ title: '上传成功', icon: 'success' });

      if (riskInfo.blocked) {
        // 上传本身是成功的（报告已存、指标已落库），只是顺带发现危值。
        // 这里不是错误、不弹 toast，稍等一下让"上传成功"的提示被看到，再跳就医页。
        setTimeout(function () { risk.openRiskPage(riskInfo); }, 400);
      }
    } catch (e) {
      this.setData({
        uploading: false,
        btnText: '上传并解析',
        btnDisabled: false
      });
      // 拦截类错误（code 1001）统一跳就医页；上传接口目前不返回它，
      // 但保留这条分支，免得以后后端改成拦截式时这里悄悄退化成一句 toast。
      if (risk.handleIfBlocked(e)) return;
      wx.showToast({ title: '上传失败，请重试', icon: 'none' });
    }
  }
});
