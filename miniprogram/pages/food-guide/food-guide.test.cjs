// 模拟网络乱序：旧搜索和旧份量结果不得覆盖用户的新选择。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const pending = [];
let definition;
vm.runInNewContext(fs.readFileSync(__dirname + '/food-guide.js', 'utf8'), {
  require: () => ({
    get: () => new Promise((resolve, reject) => pending.push({ resolve, reject })),
    post: () => new Promise((resolve, reject) => pending.push({ resolve, reject }))
  }),
  Page: value => { definition = value; },
  wx: { pageScrollTo() {}, setClipboardData() {} }
});
const page = { ...definition, data: { ...definition.data }, setData(value) { Object.assign(this.data, value); } };
const response = name => ({ foods: [{ id: name, portion: 100 }], tips: [], sources: [], notice: '', categories: { all: '全部' }, filters: { all: '全部' } });
(async () => {
  const first = page.search();
  page.data.query = '香蕉';
  const second = page.search();
  pending[1].resolve(response('banana'));
  await second;
  pending[0].resolve(response('apple'));
  await first;
  assert.equal(page.data.foods[0].id, 'banana');
  page.selectFood({ currentTarget: { dataset: { id: 'banana' } } });
  const calculating = page.calculate();
  page.onAmount({ detail: { value: '30' } });
  pending[2].resolve({ carb: 20 });
  await calculating;
  assert.equal(page.data.result, null);
  assert.equal(page.data.calculating, false);
  page.onAmount({ detail: { value: 'NaN' } });
  await page.calculate();
  assert.match(page.data.portionError, /请输入/);
  assert.equal(pending.length, 3);
  const failing = page.search();
  pending[3].reject(new Error('offline'));
  await failing;
  assert.match(page.data.error, /加载失败/);
  assert.equal(page.data.loading, false);
  page.setData({ query: '未收录饼干', customAmount: '25', customCarb: '60' });
  const custom = page.calculateCustom();
  page.onCustomAmount({ detail: { value: '30' } });
  pending[4].resolve({ carb: 15 });
  await custom;
  assert.equal(page.data.customResult, null);
  assert.equal(page.data.customLoading, false);
  page.onCustomCarb({ detail: { value: '' } });
  await page.calculateCustom();
  assert.match(page.data.customError, /请填写/);
  assert.equal(pending.length, 5);
  page.onCustomCarb({ detail: { value: '0' } });
  const zeroCarb = page.calculateCustom();
  pending[5].resolve({ carb: 0 });
  await zeroCarb;
  assert.equal(page.data.customResult.carb, 0);
  page.onQuery({ detail: { value: '芋头' } });
  const oldLookup = page.search();
  pending[6].resolve({ ...response('unused'), foods: [] });
  await new Promise(setImmediate);
  assert.equal(page.data.lookupLoading, true);
  assert.equal(pending.length, 8);
  page.onQuery({ detail: { value: '玉米' } });
  pending[7].resolve({ status: 'candidates', message: '旧的芋头结果', sources: [] });
  await oldLookup;
  assert.equal(page.data.lookupResult, null);
  assert.equal(page.data.loading, false);
  const currentLookup = page.search();
  pending[8].resolve({ ...response('unused'), foods: [] });
  await new Promise(setImmediate);
  pending[9].resolve({ status: 'candidates', message: '玉米资料待核对', sources: [] });
  await currentLookup;
  assert.equal(page.data.lookupResult.message, '玉米资料待核对');
  assert.equal(page.data.lookupLoading, false);
  assert.equal(page.data.loading, false);
  console.log('PASS: search/portion races, validation, failures, label zero, automatic external lookup and stale lookup rejection');
})().catch(error => { console.error(error); process.exitCode = 1; });
