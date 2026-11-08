const api = require('../../utils/api');

Page({
  data: {
    query: '', category: 'all', choice: 'all', foods: [], tips: [], sources: [],
    categories: [], filters: [], categoryIndex: 0, choiceIndex: 0,
    loading: true, error: '', selected: null, amount: '', result: null,
    calculating: false, portionError: '', notice: '', labelCarb: '', showDetails: false, showReference: false,
    customAmount: '', customCarb: '', customUnitIndex: 0, customUnits: ['克', '毫升'],
    customResult: null, customError: '', customLoading: false, lookupResult: null, lookupLoading: false
  },
  onLoad() { this.search(); },
  onUnload() { this._requestId = (this._requestId || 0) + 1; this._portionId = (this._portionId || 0) + 1; this._customId = (this._customId || 0) + 1; },
  onQuery(e) {
    this._requestId = (this._requestId || 0) + 1;
    this._portionId = (this._portionId || 0) + 1;
    this.resetCustom();
    this.setData({ query: e.detail.value, lookupResult: null, lookupLoading: false,
      foods: [], selected: null, result: null, loading: false, calculating: false });
  },
  resetCustom() {
    this._customId = (this._customId || 0) + 1;
    this.setData({ customResult: null, customError: '', customLoading: false });
  },
  onCustomAmount(e) { this.resetCustom(); this.setData({ customAmount: e.detail.value }); },
  onCustomCarb(e) { this.resetCustom(); this.setData({ customCarb: e.detail.value }); },
  onCustomUnit(e) { this.resetCustom(); this.setData({ customUnitIndex: Number(e.detail.value) }); },
  toggleDetails() { this.setData({ showDetails: !this.data.showDetails }); },
  toggleReference() { this.setData({ showReference: !this.data.showReference }); },
  onAmount(e) {
    this._portionId = (this._portionId || 0) + 1;
    this.setData({ amount: e.detail.value, result: null, portionError: '', calculating: false });
  },
  onLabelCarb(e) {
    this._portionId = (this._portionId || 0) + 1;
    this.setData({ labelCarb: e.detail.value, result: null, portionError: '', calculating: false });
  },
  onCategory(e) {
    const index = Number(e.detail.value);
    this.setData({ categoryIndex: index, category: this.data.categories[index].key });
    this.search();
  },
  onChoice(e) {
    const index = Number(e.detail.value);
    this.setData({ choiceIndex: index, choice: this.data.filters[index].key });
    this.search();
  },
  clear() {
    this.setData({ query: '', category: 'all', choice: 'all', categoryIndex: 0, choiceIndex: 0 });
    this.search();
  },
  async search() {
    const requestId = this._requestId = (this._requestId || 0) + 1;
    this._portionId = (this._portionId || 0) + 1;
    this.resetCustom();
    this.setData({ loading: true, error: '', selected: null, result: null, foods: [], calculating: false, portionError: '', lookupResult: null, lookupLoading: false });
    try {
      const data = await api.get('/api/v1/food-guide', {
        q: this.data.query.trim(), category: this.data.category, choice: this.data.choice
      });
      if (requestId !== this._requestId) return;
      this.setData({ foods: data.foods, tips: data.tips, sources: data.sources, notice: data.notice,
        categories: Object.keys(data.categories).map(key => ({ key, label: data.categories[key] })),
        filters: Object.keys(data.filters).map(key => ({ key, label: data.filters[key] })) });
      if (!data.foods.length && this.data.query.trim()) {
        this.setData({ lookupLoading: true });
        try {
          const lookup = await api.post('/api/v1/food-guide/lookup', { q: this.data.query.trim() }, { timeout: 60000 });
          if (requestId !== this._requestId) return;
          this.setData({ lookupResult: lookup });
          if (lookup.status === 'local') {
            this.setData({ foods: lookup.foods, query: lookup.query, category: 'all', choice: 'all', categoryIndex: 0, choiceIndex: 0 });
          }
        } catch (e) {
          if (requestId === this._requestId) this.setData({ lookupResult: { status: 'unavailable', message: e.message || '联网查找暂不可用，请稍后再试。', sources: [] } });
        }
      }
    } catch (e) {
      if (requestId === this._requestId) this.setData({ error: '食品目录加载失败，请检查网络后重试。' });
    } finally {
      if (requestId === this._requestId) this.setData({ loading: false, lookupLoading: false });
    }
  },
  selectFood(e) {
    const food = this.data.foods.find(item => item.id === e.currentTarget.dataset.id);
    if (!food) return;
    this._portionId = (this._portionId || 0) + 1;
    this.setData({ selected: food, amount: String(food.portion), labelCarb: '', result: null, portionError: '', calculating: false, showDetails: false });
    wx.pageScrollTo({ selector: '#portion-panel', duration: 0 });
  },
  async calculate() {
    const { selected, amount } = this.data;
    if (!selected || this.data.calculating) return;
    const value = Number(amount);
    if (!Number.isFinite(value) || value <= 0 || value > 5000) {
      this.setData({ result: null, portionError: '请输入大于 0 且不超过 5000 的食用量' });
      return;
    }
    const label = this.data.labelCarb.trim();
    if (label && (!Number.isFinite(Number(label)) || Number(label) < 0 || Number(label) > 100)) {
      this.setData({ result: null, portionError: '标签碳水请输入 0 到 100 之间的数字' });
      return;
    }
    const portionId = this._portionId = (this._portionId || 0) + 1;
    this.setData({ calculating: true, result: null, portionError: '' });
    try {
      const result = await api.get('/api/v1/food-guide/portion', { food_id: selected.id, amount: value, label_carb: label || undefined });
      if (portionId === this._portionId) this.setData({ result });
    } catch (e) {
      if (portionId === this._portionId) this.setData({ portionError: '份量查询失败，请重试' });
    } finally {
      if (portionId === this._portionId) this.setData({ calculating: false });
    }
  },
  async calculateCustom() {
    if (this.data.customLoading) return;
    const name = this.data.query.trim();
    const amount = Number(this.data.customAmount);
    const rate = Number(this.data.customCarb);
    if (!name || name.length > 60 || !this.data.customCarb.trim() ||
        !Number.isFinite(amount) || amount <= 0 || amount > 5000 ||
        !Number.isFinite(rate) || rate < 0 || rate > 100) {
      this.setData({ customError: '请填写食品名称、食用量和标签碳水', customResult: null });
      return;
    }
    const id = this._customId = (this._customId || 0) + 1;
    this.setData({ customLoading: true, customError: '', customResult: null });
    try {
      const result = await api.get('/api/v1/food-guide/label-portion', {
        name, amount, label_carb: rate, unit: this.data.customUnitIndex ? 'ml' : 'g'
      });
      if (id === this._customId) this.setData({ customResult: result });
    } catch (e) {
      if (id === this._customId) this.setData({ customError: '换算失败，请重试' });
    } finally {
      if (id === this._customId) this.setData({ customLoading: false });
    }
  },
  copySource(e) { wx.setClipboardData({ data: e.currentTarget.dataset.url }); }
});
