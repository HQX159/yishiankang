// pages/recipe-detail/recipe-detail.js
const api = require('../../utils/api');
const util = require('../../utils/util');

const innerAudioContext = wx.createInnerAudioContext();

Page({
  data: {
    recipeId: null,
    recipe: {},
    ingredientLines: [],
    stepLines: [],
    cookingTime: '',
    servings: '',
    createdAt: '',
    hasNutrition: false,
    isFavorited: false,
    favBtnText: '收藏食谱',
    favBtnClass: 'btn-outline',
    favBtnStyle: '',
    ttsLoading: false,
    ttsBtnText: '朗读食谱',
    ttsBtnClass: 'btn-primary',
    audioUrl: '',
    isPlaying: false,
    audioProgress: 0,
    audioCurrent: 0,
    audioDuration: 0,
    audioCurrentStr: '0:00',
    audioDurationStr: '0:00',
    ttsStatus: '',
    ttsStatusBg: '',
    ttsStatusColor: ''
  },

  onLoad(options) {
    this.setData({ recipeId: options.id });
    this.loadRecipe();

    innerAudioContext.onTimeUpdate(() => {
      const cur = innerAudioContext.currentTime;
      const dur = innerAudioContext.duration;
      this.setData({
        audioCurrent: cur,
        audioDuration: dur,
        audioProgress: dur > 0 ? (cur / dur) * 100 : 0,
        audioCurrentStr: util.formatDuration(cur),
        audioDurationStr: util.formatDuration(dur)
      });
    });
    innerAudioContext.onEnded(() => this._resetAudio());
    innerAudioContext.onError(() => {
      this.setData({
        isPlaying: false, ttsLoading: false, ttsBtnText: '朗读食谱', ttsBtnClass: 'btn-primary',
        ttsStatus: '播放失败', ttsStatusBg: '#fee2e2', ttsStatusColor: '#ef4444'
      });
    });
  },

  onUnload() {
    innerAudioContext.stop();
  },

  _resetAudio() {
    this.setData({
      isPlaying: false, audioProgress: 0, audioCurrent: 0,
      ttsBtnText: '朗读食谱', ttsBtnClass: 'btn-primary',
      ttsStatus: '', ttsStatusBg: '', ttsStatusColor: ''
    });
  },

  async loadRecipe() {
    try {
      const data = await api.get('/api/v1/recipes/' + this.data.recipeId);
      const recipe = data.recipe || {};
      const ings = (recipe.ingredients || '').split('\n')
        .map(s => s.replace(/^[-\s•·*]+/, '').trim()).filter(Boolean);
      const steps = (recipe.instructions || '').split('\n')
        .map(s => s.replace(/^[\d.\s、\)\-\s•·*]+/, '').trim()).filter(Boolean)
        .map((text, i) => ({ num: i + 1, text: text }));

      this.setData({
        recipe: recipe,
        cookingTime: (recipe.cooking_time || '未知') + ' 分钟',
        servings: (recipe.servings || '-') + ' 人份',
        createdAt: recipe.created_at || '',
        ingredientLines: ings,
        stepLines: steps,
        hasNutrition: !!recipe.nutrition_info,
        isFavorited: data.is_favorited,
        favBtnText: data.is_favorited ? '已收藏' : '收藏食谱',
        favBtnClass: data.is_favorited ? '' : 'btn-outline',
        favBtnStyle: data.is_favorited ? 'background:#fef9c3;color:#92400e;' : ''
      });
      wx.setNavigationBarTitle({ title: recipe.title || '食谱详情' });
    } catch (e) {
      console.error('加载食谱失败:', e);
    }
  },

  async toggleTTS() {
    if (this.data.isPlaying) {
      innerAudioContext.pause();
      this.setData({
        isPlaying: false, ttsBtnText: '继续播放', ttsBtnClass: 'btn-primary',
        ttsStatus: '已暂停', ttsStatusBg: '#fef9c3', ttsStatusColor: '#92400e'
      });
      return;
    }
    if (this.data.audioUrl && !this.data.isPlaying) {
      innerAudioContext.play();
      this.setData({
        isPlaying: true, ttsBtnText: '暂停', ttsBtnClass: 'btn-outline',
        ttsStatus: '正在播放', ttsStatusBg: '#dcfce7', ttsStatusColor: '#15803d'
      });
      return;
    }

    this.setData({
      ttsLoading: true, ttsBtnText: '生成语音中...', ttsBtnClass: 'btn-outline',
      ttsStatus: '正在合成语音...', ttsStatusBg: '#dbeafe', ttsStatusColor: '#1e40af'
    });

    const text = [this.data.recipe.title, this.data.recipe.ingredients, this.data.recipe.instructions].join('\n\n');
    try {
      const data = await api.post('/api/v1/tts/text_to_speech', { text: text });
      const audioUrl = api.BASE_URL + data.audio_url;
      this.setData({ ttsLoading: false, audioUrl: audioUrl });
      innerAudioContext.src = audioUrl;
      innerAudioContext.play();
      this.setData({
        isPlaying: true, ttsBtnText: '暂停', ttsBtnClass: 'btn-outline',
        ttsStatus: '正在播放', ttsStatusBg: '#dcfce7', ttsStatusColor: '#15803d'
      });
    } catch (e) {
      this.setData({
        ttsLoading: false, ttsBtnText: '朗读食谱', ttsBtnClass: 'btn-primary',
        ttsStatus: '语音合成失败', ttsStatusBg: '#fee2e2', ttsStatusColor: '#ef4444'
      });
    }
  },

  onSliderChange(e) {
    const dur = innerAudioContext.duration;
    if (dur > 0) innerAudioContext.seek((e.detail.value / 100) * dur);
  },

  async toggleFavorite() {
    const rid = this.data.recipeId;
    try {
      if (this.data.isFavorited) {
        await api.del('/api/v1/recipes/' + rid + '/favorite');
        this.setData({ isFavorited: false, favBtnText: '收藏食谱', favBtnClass: 'btn-outline', favBtnStyle: '' });
        util.showToast('已取消收藏');
      } else {
        await api.post('/api/v1/recipes/' + rid + '/favorite');
        this.setData({ isFavorited: true, favBtnText: '已收藏', favBtnClass: '', favBtnStyle: 'background:#fef9c3;color:#92400e;' });
        util.showToast('已收藏');
      }
    } catch (e) {}
  }
});
