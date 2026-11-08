"""临时数据库验证食品搜索、份量换算、Web 表单与 JWT 接口。"""
import os
import tempfile
import unittest
from utils.food_guide import FOOD_GUIDE, catalog, portion_result, label_portion, glycemic_load
from utils.auth import generate_jwt_token
from test_health_metrics_page import build_isolated_app
from utils.database import db


class FoodGuideTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='food-guide-')
        cls.app = build_isolated_app(os.path.join(cls.temp.name, 'test.db'))
        cls.app.config['TESTING'] = True
        cls.app.secret_key = 'food-guide-test-secret-key-at-least-32-bytes'
        with cls.app.app_context():
            cls.token = generate_jwt_token(1)

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            db.session.remove()
            db.engine.dispose()
        cls.temp.cleanup()

    def setUp(self):
        self.client = self.app.test_client()
        self.headers = {'Authorization': 'Bearer ' + self.token}

    def test_catalog_integrity(self):
        self.assertEqual(len({f['id'] for f in FOOD_GUIDE}), len(FOOD_GUIDE))
        self.assertGreaterEqual(len(FOOD_GUIDE), 30)
        for food in FOOD_GUIDE:
            self.assertTrue(food['source']['url'].startswith('https://'))
            self.assertGreater(food['portion'], 0)
        self.assertIsNone(portion_result('milktea')['carb'])
        self.assertTrue(all(f['unit'] == 'ml' for f in catalog(category='drink')))

    def test_search_and_alias(self):
        self.assertEqual([f['id'] for f in catalog('  红富士 ')], ['apple'])
        self.assertEqual([f['id'] for f in catalog('无糖饼干')], ['sugarfree'])
        self.assertEqual(catalog('苹果汁'), [])
        self.assertEqual(catalog('火星牌零食'), [])

    def test_official_data_and_food_forms(self):
        watermelon = portion_result('watermelon', 100)
        self.assertEqual(watermelon['carb'], 7.5)
        self.assertEqual(watermelon['food']['nutrition_provenance']['fdc_id'], 167765)
        self.assertEqual(portion_result('lychee', 100)['carb'], 16.5)
        self.assertIn('longan_dried', [f['id'] for f in catalog('桂圆干')])
        self.assertNotIn('longan', [f['id'] for f in catalog('桂圆干')])
        # 「核桃」现在会同时命中 原味核桃仁 / 核桃乳 / 桃酥（别名核桃酥）——
        # 这正是要的：三种形态是三条独立数据，不能互相顶替。
        walnut_hits = [f['id'] for f in catalog('核桃')]
        self.assertIn('walnut_raw', walnut_hits)
        self.assertEqual(len(walnut_hits), len(set(walnut_hits)))
        for fid in walnut_hits:
            if fid != 'walnut_raw':
                self.assertNotEqual(portion_result(fid)['food']['carb_per_100'],
                                    portion_result('walnut_raw')['food']['carb_per_100'])
        self.assertEqual([f['id'] for f in catalog('花生')], ['peanut_raw'])
        self.assertEqual(catalog('原味酸奶'), [])
        self.assertTrue(all(f['unit'] == 'g' for f in catalog(choice='reference')))

    def test_chinese_snacks_are_covered(self):
        """补库护栏（2026-11-06）：中式零食与饮品不能再是空白。

        上一轮 64 条是照 USDA 补的，补进来的是库斯库斯、布格麦、碧根果，
        而 14 个常见搜索词里 13 个搜不到，连「馒头」都没有 —— 补库方向错了。
        这组断言就是防止再按「哪个数据库有」去补，而不是按「糖友会问什么」去补。
        """
        must_have = ('辣条', '方便面', '火腿肠', '馒头', '包子', '油条', '沙琪玛',
                     '锅巴', '山楂', '月饼', '汤圆', '豆浆', '可乐', '啤酒', '豆腐干')
        missing = [q for q in must_have if not catalog(q)]
        self.assertEqual(missing, [], '这些词搜不到：%s' % missing)

        # 老人口中的别称也要能搜到，不能只认包装上的正式名
        for alias, expect in (('辣片', 'latiao'), ('泡面', 'instantnoodle'),
                              ('豆干', 'driedtofu'), ('果丹皮', 'hawthornroll'),
                              ('话梅', 'preservedplum'), ('糯米条', 'jiangmitiao'),
                              ('萨其马', 'shaqima'), ('元宵', 'tangyuan')):
            self.assertIn(expect, [f['id'] for f in catalog(alias)], alias)

        # 有条目还不够，必须有真的碳水值（不能只挂个名字、数值留空）
        for key, carb in (('latiao', 45.5), ('instantnoodle', 61.6), ('ham', 15.6),
                          ('beefjerky', 1.9), ('soymilk', 1.1), ('soda', 10.7),
                          ('steamedbun', 47.0), ('baozi', 28.6)):
            self.assertEqual(portion_result(key)['food']['carb_per_100'], carb, key)

        # 每个新增条目都要能指到 https 来源，不允许「来源不明」
        for key in ('latiao', 'instantnoodle', 'ham', 'soymilk', 'beer', 'steamedbun'):
            self.assertTrue(portion_result(key)['food']['source']['url'].startswith('https://'), key)

        # 中式加工零食一律是「少选替换/注意份量」，绝不能出现「优先选择」
        for key in ('latiao', 'instantnoodle', 'shaqima', 'mahua', 'guoba', 'mooncake'):
            self.assertIn(portion_result(key)['food']['choice'], ('occasional', 'limit'), key)

    def test_nutrition_reference_does_not_invent_portion_or_gi(self):
        result = portion_result('kiwi_green', 200)
        self.assertEqual(result['carb'], 29.3)
        # 2026-11-06 起判据变了：「绿心猕猴桃」的别名就是「猕猴桃」，权威表里
        # 猕猴桃 GI 52 是 exact 原值，属于可追溯的引用而不是编造。所以不再断言
        # 「必须为 None」，改成「必须指得出 FOODS 里的哪一个键」——这比原来更难作弊。
        self.assertEqual(result['food']['gi'], 52.0)
        self.assertEqual(result['food']['gi_key'], '猕猴桃')
        self.assertIsNone(result['craving']['amount'])
        self.assertFalse(result['above_reference'])
        self.assertIn('包含膳食纤维', result['food']['carbohydrate_definition'])
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
        html = self.client.get('/nutrition/food-guide?q=绿心猕猴桃&food_id=kiwi_green&amount=200').get_data(as_text=True)
        self.assertIn('29.3 克', html)
        self.assertIn('来源编号：168153', html)
        self.assertIn('个人参考份量尚未整理', html)
        self.assertNotIn('偶尔解馋小份示例：100', html)
        response = self.client.get('/api/v1/food-guide?q=猕猴桃', headers=self.headers)
        self.assertEqual(response.json['data']['foods'][0]['id'], 'kiwi_green')
        self.assertEqual(response.json['data']['foods'][0]['nutrition_provenance']['license'], 'CC0-1.0')

    def test_glycemic_load_and_strict_matching(self):
        """升糖改造（2026-11-06）：GI 只认整名/别名完全相等，GL 按实际份量算。

        起因：用户问「我想吃这个，升糖怎么样」，页面只答「含多少碳水」，
        而 GI 又藏在折叠层第 4 行、144 条里 132 条干脆没数据。
        """
        # 1) GL = GI × 这份碳水 ÷ 100，拿真实值对一遍（数字都能手算复核）
        self.assertEqual(glycemic_load(72, 7.55, 150), (8.2, '低'))     # 西瓜：GI 高但一份量小
        self.assertEqual(glycemic_load(88.1, 47.0, 50), (20.7, '高'))   # 馒头：GI 高且量也大
        self.assertEqual(glycemic_load(36, 10, 100), (3.6, '低'))       # 苹果
        self.assertEqual(glycemic_load(83.2, 33.3, 50), (13.9, '中'))   # 白米饭
        # 缺任何一项就不算，绝不拿别的值凑
        for args in ((None, 10, 100), (50, None, 100), (50, 10, None)):
            self.assertEqual(glycemic_load(*args), (None, ''))

        # 2) 两条分档界线都要测边界值（最容易写错成 </<=）
        #    GI：＜55 慢 / 55～70 中 / ＞70 快 —— 用真实条目侧证
        self.assertEqual(portion_result('mango')['rise_key'], 'mid')        # GI 55 属中
        self.assertEqual(portion_result('pear')['rise_key'], 'slow')       # GI 36 属慢
        self.assertEqual(portion_result('watermelon')['rise_key'], 'fast')  # GI 72 属快
        #    GL：≤10 低 / 11～19 中 / ≥20 高
        self.assertEqual(glycemic_load(100, 10, 100), (10.0, '低'))   # 10.0 仍在低
        self.assertEqual(glycemic_load(100, 10.1, 100)[1], '中')      # 10.1 进中
        self.assertEqual(glycemic_load(100, 19, 100)[1], '中')        # 19.0 仍是中
        self.assertEqual(glycemic_load(100, 19.1, 100)[1], '高')      # 19.1 进高

        # 3) 🔴 误配护栏：这些名字里「包含」权威表的键，但根本不是同一个东西。
        #    按包含匹配会全部撞上，界面就成了「黑芝麻汤圆按芝麻算」。
        for name, wrong in (('黑芝麻汤圆', '芝麻'), ('牛奶巧克力', '牛奶'),
                            ('菠萝蜜', '菠萝'), ('虾片', '虾'), ('绿豆糕', '绿豆')):
            food = next(f for f in FOOD_GUIDE if f['name'] == name)
            self.assertNotEqual(food.get('gi_key'), wrong, name)

        # 4) 正面必须看得到档位，不能只藏在 <details> 里
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
        html = self.client.get('/nutrition/food-guide?q=西瓜').get_data(as_text=True)
        self.assertIn('fg-rise-fast', html)
        self.assertIn('升糖快', html)
        self.assertIn('这份升糖负荷 GL', html)
        # 🔴 用户 2026-11-06 当场指出两件事，各留一条护栏，别再退回去：
        #   ① 卡片正面把「升糖快」和「负荷 8.2 · 低」挤在一行，看着像自相矛盾，
        #      读不出哪个是 GI、哪个是 GL —— 所以 GL 必须带自己的名字和缩写。
        self.assertNotIn('这一份负荷', html)
        #   ② GL 是 8.2 却没有任何说明，用户问「这怎么没有单位」。
        #      GL 本身无量纲，所以正面必须紧跟一句葡萄糖当量，光甩数字等于没解释。
        #      写「相当于吃」而不是「约 X 克」——后者会被读成「这份含 8.2 克葡萄糖」，
        #      跟同一张卡片的「这份碳水 22.6 克」对不上。
        self.assertIn('这份升糖负荷 GL 8.2 · 低', html)
        self.assertIn('相当于吃 8.2 克葡萄糖', html)
        self.assertIn('1 个 GL 约等于 1 克葡萄糖', html)
        # 档位和负荷打架时要给一句人话，否则老人会被「升糖快」吓跑
        self.assertIn('这一份量小，实际负担不高', html)

        # 5) 没数据的要老实说「暂无」，不许编一个数出来
        html = self.client.get('/nutrition/food-guide?q=辣条').get_data(as_text=True)
        self.assertIn('升糖快慢暂无数据', html)
        self.assertNotIn('GI 暂无可靠数值 · ', html)

        # 6) 实际份量变了，GL 要跟着变（不是拿示例份量糊弄）
        self.assertEqual(portion_result('apple', 300)['gl'], 10.8)
        self.assertEqual(portion_result('apple', 100)['gl'], 3.6)

    def test_combined_filters(self):
        foods = catalog(category='fruit', choice='recommend')
        self.assertGreater(len(foods), 3)
        self.assertTrue(all(f['category'] == 'fruit' and f['choice'] == 'recommend' for f in foods))
        self.assertIn('watermelon', [f['id'] for f in catalog(choice='high')])
        self.assertTrue(all(f['fast'] for f in catalog(choice='high')))
        for args in [('x' * 61, 'all', 'all'), ('', 'bad', 'all'), ('', 'all', 'bad')]:
            with self.assertRaises(ValueError):
                catalog(*args)

    def test_portion_math_and_unknown(self):
        self.assertEqual(portion_result('apple', 150)['carb'], 15)
        self.assertEqual(portion_result('milk', 100)['carb'], 5)
        self.assertEqual(portion_result('apple')['amount'], 100)
        self.assertTrue(portion_result('apple', 200)['above_reference'])
        self.assertFalse(portion_result('apple', 50)['above_reference'])
        self.assertIsNone(portion_result('milktea', 300)['carb'])

    def test_invalid_portions(self):
        for value in ['', 0, -1, 5001, 'NaN', 'inf', '-inf', 'no', True]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                portion_result('apple', value)
        with self.assertRaises(LookupError):
            portion_result('missing', 20)

    def test_unknown_food_label_conversion(self):
        data = label_portion('某品牌饼干', 25, 60)
        self.assertEqual(data['carb'], 15)
        self.assertEqual(data['suitability'], '暂无法判断')
        self.assertNotIn('recommended_amount', data)
        self.assertEqual(label_portion('某饮品', 200, 0, 'ml')['carb'], 0)
        for args in [('食品', 0, 10), ('食品', 'NaN', 10), ('食品', 20, 'inf'),
                     ('食品', 20, ''), ('食品', 20, 101), ('食品', 20, -1),
                     ('', 20, 10), ('食品', 20, 10, 'cup')]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                label_portion(*args)

    def test_unknown_food_web_and_api(self):
        path = '/api/v1/food-guide/label-portion'
        self.assertEqual(self.client.get(path).status_code, 401)
        response = self.client.get(path, query_string={'name': '某饼干', 'amount': 25, 'label_carb': 60}, headers=self.headers)
        self.assertEqual(response.json['data']['carb'], 15)
        self.assertEqual(self.client.get(path, query_string={'name': '某饼干', 'amount': 25}, headers=self.headers).status_code, 400)
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
        html = self.client.get('/nutrition/food-guide', query_string={'q': '某饼干', 'custom_food': '某饼干', 'custom_amount': 25, 'custom_carb': 60}).get_data(as_text=True)
        self.assertIn('本地暂未收录', html)
        self.assertIn('不能判断适不适合您吃', html)
        self.assertIn('15.0 克', html)
        self.assertIn('不提供个人安全食用量', html)

    def test_craving_examples_and_label(self):
        result = portion_result('crisps', 100)
        self.assertEqual(result['craving']['amount'], 20)
        self.assertEqual(result['craving']['carb'], 8.6)
        self.assertIn('不是', result['craving']['boundary'])
        self.assertIsNone(portion_result('milktea')['craving']['amount'])
        result = portion_result('milktea', 200, '12')
        self.assertEqual(result['carb'], 24)
        self.assertEqual(result['craving']['carb'], 12)
        self.assertTrue(result['from_label'])
        self.assertEqual(portion_result('milktea', 200, '0')['carb'], 0)
        self.assertEqual(portion_result('apple', 100)['carb'], 10)
        self.assertEqual(portion_result('apple', 100, '15')['carb'], 15)
        for value in ['NaN', 'inf', '-1', '101', 'bad', True]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                portion_result('milktea', 100, value)

    def test_craving_web_and_api(self):
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
        html = self.client.get('/nutrition/food-guide?food_id=crisps&amount=100').get_data(as_text=True)
        self.assertIn('实在想吃，吃多少合适？', html)
        self.assertIn('偶尔解馋小份示例：20 克', html)
        response = self.client.get('/api/v1/food-guide/portion?food_id=milktea&amount=200&label_carb=12', headers=self.headers)
        self.assertEqual(response.json['data']['carb'], 24)
        self.assertTrue(response.json['data']['from_label'])
        self.assertEqual(self.client.get('/api/v1/food-guide/portion?food_id=milktea&label_carb=NaN', headers=self.headers).status_code, 400)

    def test_auth(self):
        self.assertEqual(self.client.get('/nutrition/food-guide').status_code, 302)
        for path in ['/api/v1/food-guide', '/api/v1/food-guide/portion?food_id=apple']:
            self.assertEqual(self.client.get(path).status_code, 401)

    def test_api_search_and_portion(self):
        response = self.client.get('/api/v1/food-guide?q=红富士', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['data']['foods'][0]['id'], 'apple')
        response = self.client.get('/api/v1/food-guide/portion?food_id=apple&amount=150', headers=self.headers)
        self.assertEqual(response.json['data']['carb'], 15)
        self.assertIn('不是个人安全上限', response.json['data']['notice'])

    def test_api_errors(self):
        for suffix, status in [('/portion?food_id=missing', 404), ('/portion?food_id=apple&amount=NaN', 400), ('?category=bad', 400), ('/portion?food_id=apple&amount=', 400)]:
            self.assertEqual(self.client.get('/api/v1/food-guide' + suffix, headers=self.headers).status_code, status)

    def test_page_search_and_calculate(self):
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
        response = self.client.get('/nutrition/food-guide?q=苹果&food_id=apple&amount=150')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('约 15.0 克', html)
        self.assertIn('超过页面参考示例', html)
        self.assertIn('食品科普 · 加餐查询', html)
        self.assertIn('name="food_id" value="apple"', html)

    def test_empty_and_escaped_html(self):
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
        html = self.client.get('/nutrition/food-guide', query_string={'q': '<script>alert(1)</script>'}).get_data(as_text=True)
        self.assertIn('本地暂未收录', html)
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('&lt;script&gt;', html)
        html = self.client.get('/nutrition/food-guide?food_id=apple&amount=inf').get_data(as_text=True)
        self.assertIn('食用量应大于 0', html)


if __name__ == '__main__':
    unittest.main(verbosity=2)
