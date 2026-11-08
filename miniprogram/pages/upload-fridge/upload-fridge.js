// pages/upload-fridge/upload-fridge.js
const api = require('../../utils/api');

Page({
  data: {
    imagePath: '',
    uploading: false,
    uploaded: false,
    btnText: '上传识别',
    showPicker: true,
    showActions: false,
    showResult: false,
    showTips: true,
    ingredientCount: 0,
    ingredients: []
  },

  chooseImage() {
    wx.chooseImage({
      count: 1,
      sizeType: ['compressed'],
      sourceType: ['camera', 'album'],
      success: (res) => {
        this.setData({
          imagePath: res.tempFilePaths[0],
          uploaded: false,
          showPicker: false,
          showActions: true,
          showResult: false,
          showTips: true,
          ingredients: [],
          ingredientCount: 0
        });
      }
    });
  },

  async upload() {
    if (!this.data.imagePath || this.data.uploading) return;
    this.setData({ uploading: true, btnText: '识别中...' });

    try {
      const data = await api.uploadFile(this.data.imagePath, '/api/v1/upload/fridge_image');
      const ings = (data.ingredients || []).map(ing => ({
        id: ing.id,
        name: ing.name,
        displayName: ing.name + ' (' + Math.round(ing.confidence * 100) + '%)'
      }));
      this.setData({
        uploading: false,
        btnText: '上传识别',
        uploaded: true,
        showActions: false,
        showResult: true,
        showTips: false,
        ingredients: ings,
        ingredientCount: ings.length
      });
      wx.showToast({ title: '识别到 ' + ings.length + ' 种食材', icon: 'success' });
    } catch (e) {
      this.setData({ uploading: false, btnText: '上传识别' });
      wx.showToast({ title: '识别失败，请重试', icon: 'none' });
    }
  },

  goGenerate() { wx.switchTab({ url: '/pages/generate/generate' }); }
});
