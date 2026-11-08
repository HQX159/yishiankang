"""身高/BMI 的落库、复用、隔离及趋势定位回归测试；仅使用临时库。"""
import os
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta

from test_health_metrics_page import build_isolated_app
from utils.database import db
from utils.checkin import parse_checkin, save_checkin, build_api_context
from utils.auth import generate_jwt_token
from utils.metric_store import metric_trend
from models.health_metric import HealthMetric
from models.user import User
from models.health_report import HealthReport
from utils.biz_time import biz_date_str


class CheckinBmiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='checkin-bmi-')
        self.app = build_isolated_app(os.path.join(self.temp.name, 'test.db'))
        self.app.config['TESTING'] = True
        self.app.secret_key = 'checkin-bmi-test-secret-at-least-32-bytes'
        self.ctx = self.app.app_context()
        self.ctx.push()
        for name in ('alice', 'bob'):
            user = User(username=name)
            user.set_password('test1234')
            db.session.add(user)
        db.session.commit()
        self.client = self.app.test_client()
        self.day = datetime(2026, 10, 1, 1)

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.ctx.pop()
        self.temp.cleanup()

    def save(self, values, user=1, when=None):
        items, errors = parse_checkin(values)
        self.assertFalse(errors)
        result = save_checkin(user, items, when=when or self.day)
        self.assertIsNone(result['error'])
        return result

    def test_height_validation(self):
        for value in ('170', '170cm', '170厘米'):
            items, errors = parse_checkin({'height': value})
            self.assertFalse(errors)
            self.assertEqual(items[0]['value'], 170)
        for value in ('NaN', 'inf', 0, 251, 'bad'):
            self.assertTrue(parse_checkin({'height': value})[1])

    def test_today_date_does_not_add_timezone_twice(self):
        with patch('utils.biz_time.utc_now', return_value=datetime(2026, 11, 3, 9)):
            self.assertEqual(biz_date_str(), '2026-11-03')
        with patch('utils.biz_time.utc_now', return_value=datetime(2026, 11, 3, 17)):
            self.assertEqual(biz_date_str(), '2026-11-04')

    def test_bmi_and_same_day_edit(self):
        result = self.save({'height': 170, 'weight': 70})
        self.assertEqual(result['saved'], 2)  # 保持 API 原有计数：用户填写项。
        row = HealthMetric.query.filter_by(metric_key='bmi', user_id=1).one()
        self.assertEqual(row.value, 24.2)
        self.save({'weight': 65}, when=self.day + timedelta(hours=1))
        rows = HealthMetric.query.filter_by(metric_key='bmi', user_id=1).all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].value, 22.5)

    def test_reuse_height_and_bmi_trend(self):
        self.save({'height': 170, 'weight': 70})
        next_day = self.day + timedelta(days=1)
        self.save({'weight': 68}, when=next_day)
        self.assertEqual([p['value'] for p in metric_trend(1, 'bmi')['points']], [24.2, 23.5])
        data = build_api_context(1, when=next_day)
        self.assertEqual(data['bmi']['value'], 23.5)
        self.assertFalse(data['done'])  # 只记身高体重不能算血糖已记录。
        self.assertFalse(data['height_filled'])
        self.assertEqual(data['height']['last_value'], 170)

    def test_missing_height_and_user_isolation(self):
        self.save({'height': 170})
        self.assertEqual(HealthMetric.query.filter_by(metric_key='bmi').count(), 0)
        self.save({'weight': 70}, user=2)
        self.assertEqual(HealthMetric.query.filter_by(metric_key='bmi', user_id=2).count(), 0)
        self.save({'weight': 70}, when=self.day + timedelta(hours=1))
        self.assertEqual(HealthMetric.query.filter_by(metric_key='bmi', user_id=1).count(), 1)

    def test_no_future_height_for_historical_weight(self):
        self.save({'height': 180}, when=self.day + timedelta(days=2))
        self.save({'weight': 70})
        self.assertEqual(HealthMetric.query.filter_by(metric_key='bmi').count(), 0)

    def test_height_edit_recalculates_bmi(self):
        self.save({'height': 170, 'weight': 70})
        self.save({'height': 175}, when=self.day + timedelta(hours=1))
        row = HealthMetric.query.filter_by(metric_key='bmi', user_id=1).one()
        self.assertEqual(row.value, 22.9)

    def test_reuse_report_height_without_changing_report_bmi(self):
        report = HealthReport(user_id=1, filename='test.pdf', filepath='test.pdf')
        db.session.add(report)
        db.session.flush()
        for key, value, label, unit in [('height', 170, '身高', 'cm'), ('bmi', 26, 'BMI', '')]:
            db.session.add(HealthMetric(user_id=1, report_id=report.id, metric_key=key,
                                        value=value, label=label, unit=unit,
                                        measured_at=self.day - timedelta(days=1)))
        db.session.commit()
        self.save({'weight': 70})
        self.assertEqual(HealthMetric.query.filter_by(metric_key='bmi', report_id=report.id).one().value, 26)
        self.assertEqual(HealthMetric.query.filter_by(metric_key='bmi', report_id=None).one().value, 24.2)

    def test_api_and_page(self):
        headers = {'Authorization': 'Bearer ' + generate_jwt_token(1)}
        response = self.client.post('/api/v1/health/checkin', json={'height': 170, 'weight': 70}, headers=headers)
        self.assertEqual(response.status_code, 200)
        data = response.json['data']
        self.assertEqual(data['height']['value'], 170)
        self.assertEqual(data['bmi']['value'], 24.2)
        with self.client.session_transaction() as sess:
            sess['user_id'] = 1
        html = self.client.get('/api/upload/upload_health_report?key=bmi').get_data(as_text=True)
        self.assertIn('name="height"', html)
        self.assertIn('BMI 24.2', html)
        self.assertIn('id="metric-trend"', html)
        self.assertIn('?key=bmi#metric-trend', html)
        self.assertIn('metric-card-link', html)
        redirect = self.client.get('/health/metrics?key=bmi')
        self.assertTrue(redirect.location.endswith('?key=bmi#metric-trend'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
