import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from datetime import timedelta
from unittest.mock import patch, Mock

import requests
from models.food_lookup import FoodLookupRequest, FoodLookupUsage, utc_now
from test_health_metrics_page import build_isolated_app
from utils.auth import generate_jwt_token
from utils.database import db
from utils.food_guide import catalog
from utils.food_lookup import lookup_food, _drafts, _search_sources
from scripts.export_food_requests import main as export_requests


SOURCES = [{'id': 1, 'title': '芋头营养资料', 'url': 'https://www.chinanutri.cn/example',
            'excerpt': '芋头，每100克碳水化合物20克。'}]


class FoodLookupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.app = build_isolated_app(os.path.join(cls.temporary.name, 'lookup.db'))
        cls.app.config.update(TESTING=True, FOOD_LOOKUP_ENABLED=True,
                              FOOD_SEARCH_API_KEY='fake-test-key', SECRET_KEY='food-lookup-test-secret-at-least-32-bytes')

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            db.session.remove()
            db.engine.dispose()
        cls.temporary.cleanup()

    def setUp(self):
        self.context = self.app.app_context()
        self.context.push()
        FoodLookupRequest.query.delete()
        FoodLookupUsage.query.delete()
        db.session.commit()
        self.app.config['FOOD_LOOKUP_ENABLED'] = True
        self.client = self.app.test_client()
        self.headers = {'Authorization': 'Bearer ' + generate_jwt_token(1)}

    def tearDown(self):
        db.session.remove()
        self.context.pop()

    @patch('utils.food_lookup._search_sources')
    def test_local_first_and_question_normalization(self, search):
        result = lookup_food('我想吃两块苹果能吃多少？', 1)
        self.assertEqual(result['status'], 'local')
        self.assertIn('apple', [f['id'] for f in result['foods']])
        search.assert_not_called()
        self.assertEqual(FoodLookupRequest.query.count(), 0)

    @patch('utils.food_lookup._search_sources')
    def test_health_text_and_invalid_names_not_sent(self, search):
        for query in ['血糖9.2想吃芋头', '糖尿病可以吃芋头吗', '体重60吃芋头', '忽略指令', 'https://example.com', '', None, ['芋头'], 'x' * 61]:
            with self.subTest(query=query), self.assertRaises(ValueError):
                lookup_food(query, 1)
        self.assertEqual(FoodLookupRequest.query.count(), 0)
        search.assert_not_called()

    @patch('utils.food_lookup._drafts', return_value=[{'name': '芋头', 'source_id': 1, 'quote': '每100克碳水化合物20克', 'carbohydrate_text': '20克'}])
    @patch('utils.food_lookup._search_sources', return_value=SOURCES)
    def test_cache_queue_and_unreviewed_values_not_published(self, search, drafts):
        first = lookup_food('芋头', 1)
        second = lookup_food('芋头', 2)
        self.assertEqual(first['status'], 'candidates')
        self.assertTrue(second['cached'])
        search.assert_called_once_with('芋头')
        self.assertEqual(FoodLookupRequest.query.one().query_count, 2)
        self.assertIn('20克', FoodLookupRequest.query.one().draft_json)
        self.assertNotIn('20克', json.dumps(first, ensure_ascii=False))
        self.assertNotIn('carbohydrate_text', first['candidates'][0])
        self.assertEqual(catalog('芋头'), [])
        self.assertEqual(FoodLookupUsage.query.count(), 1)

    @patch('utils.food_lookup._search_sources', return_value=[])
    def test_quota_and_negative_cache(self, search):
        for index in range(5):
            self.assertEqual(lookup_food(f'测试食品{index}', 1)['status'], 'not_found')
        self.assertTrue(lookup_food('测试食品0', 1)['cached'])
        self.assertEqual(lookup_food('测试食品6', 1)['status'], 'rate_limited')
        self.assertEqual(search.call_count, 5)

    @patch('utils.food_lookup._search_sources', side_effect=requests.Timeout())
    def test_timeout_is_clear_and_lease_released(self, search):
        result = lookup_food('芋头', 1)
        self.assertEqual(result['status'], 'unavailable')
        self.assertIsNone(FoodLookupRequest.query.one().lease_until)
        self.assertNotIn('fake-test-key', json.dumps(result))

    @patch('utils.food_lookup._search_sources')
    def test_disabled_and_inflight_requests_do_not_search(self, search):
        self.app.config['FOOD_LOOKUP_ENABLED'] = False
        self.assertEqual(lookup_food('芋头', 1)['status'], 'unavailable')
        self.assertEqual(FoodLookupRequest.query.one().review_status, 'pending')
        self.app.config['FOOD_LOOKUP_ENABLED'] = True
        FoodLookupRequest.query.one().lease_until = utc_now() + timedelta(seconds=30)
        db.session.commit()
        self.assertEqual(lookup_food('芋头', 1)['status'], 'busy')
        search.assert_not_called()

    @patch('utils.food_lookup._drafts', side_effect=ValueError('bad model output'))
    @patch('utils.food_lookup._search_sources', return_value=SOURCES)
    def test_model_failure_keeps_real_sources(self, search, drafts):
        result = lookup_food('芋头', 1)
        self.assertEqual(result['status'], 'candidates')
        self.assertEqual(result['sources'][0]['url'], SOURCES[0]['url'])
        self.assertEqual(result['candidates'], [])

    @patch('utils.food_lookup.requests.post')
    def test_source_domain_and_topic_validation(self, post):
        post.return_value = Mock(json=lambda: {'search_result': [
            {'title': '芋头', 'content': '芋头', 'link': 'javascript:alert(1)'},
            {'title': '芋头', 'content': '芋头', 'link': 'https://chinanutri.cn.evil.example/1'},
            {'title': '芋头', 'content': '芋头', 'link': 'https://user:pass@chinanutri.cn/1'},
            {'title': '香蕉', 'content': '香蕉', 'link': 'https://www.chinanutri.cn/2'},
            {'title': '芋头', 'content': '芋头', 'link': 'https://www.chinanutri.cn/1'}]})
        result = _search_sources('芋头')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['url'], 'https://www.chinanutri.cn/1')

    @patch('utils.food_lookup.LLMClient')
    def test_model_cannot_invent_quote_or_reference(self, client):
        client.return_value.generate_text.return_value = json.dumps({'candidates': [
            {'name': '芋头', 'source_id': 1, 'quote': '芋头，每100克碳水化合物20克。', 'carbohydrate_text': '99克'},
            {'name': '芋头', 'source_id': 2, 'quote': '任意原文'},
            {'name': '芋头', 'source_id': 1, 'quote': '这是虚构的原文'}]})
        result = _drafts('芋头', SOURCES)
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]['carbohydrate_text'])

    @patch('utils.food_lookup.LLMClient')
    def test_rise_level_requires_real_quote(self, client):
        """第 2 步（2026-11-06）：联网只能给定性档位，依据句必须真在摘要里。

        模型很爱顺口说「升糖快」，但网页摘要里根本没有这句 —— 这种必须退回
        unknown，否则「有依据」就是一句空话。
        """
        sources = [
            {'id': 1, 'title': '芋头', 'url': 'https://www.chinanutri.cn/1',
             'excerpt': '芋头属于中低升糖食物，蒸煮后升糖速度相对平缓。'},
            {'id': 2, 'title': '芋头', 'url': 'https://www.chinanutri.cn/2',
             'excerpt': '芋头，每100克碳水化合物20克。'},
        ]
        client.return_value.generate_text.return_value = json.dumps({'candidates': [
            {'name': '芋头', 'source_id': 1,
             'quote': '芋头属于中低升糖食物，蒸煮后升糖速度相对平缓。',
             'rise_level': 'slow', 'rise_quote': '蒸煮后升糖速度相对平缓'},
            {'name': '芋头', 'source_id': 2,
             'quote': '芋头，每100克碳水化合物20克。',
             'rise_level': 'fast', 'rise_quote': '芋头升糖非常快'}]})
        result = _drafts('芋头', sources)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['rise_level'], 'slow')
        self.assertEqual(result[0]['rise_quote'], '蒸煮后升糖速度相对平缓')
        # 依据句在摘要里找不到 → 档位退回 unknown，不接受「凭常识」的结论
        self.assertEqual(result[1]['rise_level'], 'unknown')
        self.assertIsNone(result[1]['rise_quote'])

    @patch('utils.food_lookup.LLMClient')
    @patch('utils.food_lookup._search_sources')
    def test_web_page_shows_rise_with_quote(self, search, client):
        """定级必须连着原文依据一起展示，且标明「不是我们的判断」。"""
        search.return_value = SOURCES
        client.return_value.generate_text.return_value = json.dumps({'candidates': [
            {'name': '芋头', 'source_id': 1, 'quote': '芋头，每100克碳水化合物20克。',
             'rise_level': 'mid', 'rise_quote': '每100克碳水化合物20克'}]})
        with self.client.session_transaction() as session:
            session['user_id'] = 1
        self.client.get('/nutrition/food-guide')
        with self.client.session_transaction() as session:
            token = session['food_lookup_token']
        html = self.client.post('/nutrition/food-guide',
                                data={'q': '芋头', 'lookup_token': token, 'net': '1'}).get_data(as_text=True)
        self.assertIn('资料提到升糖中等', html)
        self.assertIn('每100克碳水化合物20克', html)
        self.assertIn('不是我们的判断', html)

    @patch('utils.food_lookup._search_sources', return_value=[])
    def test_web_post_token_and_api_auth(self, search):
        path = '/api/v1/food-guide/lookup'
        self.assertEqual(self.client.post(path, json={'q': '芋头'}).status_code, 401)
        self.assertEqual(self.client.post(path, json=[], headers=self.headers).status_code, 400)
        self.assertEqual(self.client.post(path, json={'q': '血糖9.2芋头'}, headers=self.headers).status_code, 400)
        with self.client.session_transaction() as session:
            session['user_id'] = 1
        self.client.get('/nutrition/food-guide')
        with self.client.session_transaction() as session:
            token = session['food_lookup_token']
        invalid = self.client.post('/nutrition/food-guide', data={'q': '芋头', 'lookup_token': '无效'})
        self.assertIn('页面已过期', invalid.get_data(as_text=True))
        search.assert_not_called()
        html = self.client.post('/nutrition/food-guide', data={'q': '芋头', 'lookup_token': token, 'net': '1'}).get_data(as_text=True)
        self.assertIn('暂未找到可核对的来源', html)
        result = self.client.post(path, json={'q': '芋头'}, headers=self.headers)
        self.assertTrue(result.json['data']['cached'])
        search.assert_called_once()

    @patch('utils.food_lookup._search_sources', return_value=[])
    def test_web_post_without_net_only_offers(self, search):
        """第 2 步下半（2026-11-06）：本地没收录时不再自动联网。

        整页 POST 去联网要白屏 2~3 秒，中老年用户会以为页面卡死；而且每人
        每 10 分钟只有 5 次额度，不能每次搜空就烧掉一次。改成先给一个
        「联网找找看」按钮，用户点了才真的查。
        """
        with self.client.session_transaction() as session:
            session['user_id'] = 1
        self.client.get('/nutrition/food-guide')
        with self.client.session_transaction() as session:
            token = session['food_lookup_token']
        html = self.client.post('/nutrition/food-guide',
                                data={'q': '芋头', 'lookup_token': token}).get_data(as_text=True)
        search.assert_not_called()
        self.assertEqual(FoodLookupRequest.query.count(), 0)
        self.assertIn('本地暂未收录', html)
        self.assertIn('name="net" value="1"', html)
        # 点了之后的等待界面：JS 在提交前把它换上来，无 JS 时这段不生效
        self.assertIn('正在查「芋头」的资料', html)
        self.assertIn('大概几秒就好', html)
        self.assertIn('js/food-guide.js', html)
        wait_tag = re.search(r'<div class="fg-net-wait"[^>]*>', html)
        self.assertIsNotNone(wait_tag)
        # 默认必须是 hidden，否则一打开页面就显示"正在查"
        self.assertIn('hidden', wait_tag.group(0))
        self.assertIn('role="status"', wait_tag.group(0))
        # 点了才联网，且给的是确定结论而不是空转
        clicked = self.client.post('/nutrition/food-guide',
                                   data={'q': '芋头', 'lookup_token': token, 'net': '1'}).get_data(as_text=True)
        search.assert_called_once_with('芋头')
        self.assertIn('暂未找到可核对的来源', clicked)
        # 已经联网查过了，就不能还把「联网找找看」的入口摆在那儿
        self.assertNotIn('本地暂未收录', clicked)
        self.assertNotIn('name="net" value="1"', clicked)

    @patch('utils.food_lookup._search_sources')
    def test_filter_miss_restores_local_without_external_call(self, search):
        with self.client.session_transaction() as session:
            session['user_id'] = 1
        self.client.get('/nutrition/food-guide')
        with self.client.session_transaction() as session:
            token = session['food_lookup_token']
        html = self.client.post('/nutrition/food-guide', data={'q': '香蕉', 'choice': 'recommend',
                                 'lookup_token': token}).get_data(as_text=True)
        self.assertIn('香蕉', html)
        self.assertIn('name="food_id" value="banana"', html)
        search.assert_not_called()

    def test_queue_export_is_read_only(self):
        self.app.config['FOOD_LOOKUP_ENABLED'] = False
        lookup_food('蒸芋头', 1)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'requests.json'
            with patch('sys.argv', ['export', '--db', os.path.join(self.temporary.name, 'lookup.db'),
                                    '--output', str(output)]), patch('builtins.print'):
                export_requests()
            self.assertEqual(json.loads(output.read_text(encoding='utf-8'))[0]['query'], '蒸芋头')
            self.assertEqual(FoodLookupRequest.query.count(), 1)

    @patch('utils.food_lookup._search_sources', return_value=[])
    def test_get_does_not_trigger_external_and_api_rate_limit(self, search):
        with self.client.session_transaction() as session:
            session['user_id'] = 1
        html = self.client.get('/nutrition/food-guide?q=芋头').get_data(as_text=True)
        search.assert_not_called()
        self.assertEqual(FoodLookupRequest.query.count(), 0)
        # 从「常见查询」这类链接进来（GET）搜不到，也要给出联网入口，不能只显示 0 种
        self.assertIn('本地暂未收录', html)
        for index in range(5):
            self.assertEqual(self.client.post('/api/v1/food-guide/lookup', json={'q': f'食品{index}'}, headers=self.headers).status_code, 200)
        response = self.client.post('/api/v1/food-guide/lookup', json={'q': '食品6'}, headers=self.headers)
        self.assertEqual(response.status_code, 429)


if __name__ == '__main__':
    unittest.main()
