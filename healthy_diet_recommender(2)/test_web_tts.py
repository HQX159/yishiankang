import sys
import tempfile
import types
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from flask import Flask, render_template_string
from utils.database import db
from models.user import User
from models.recipe import Recipe
from routes.tts_routes import tts_bp
from utils.web_tts import audio_for_recipe, recipe_text


class WebTTSTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Flask(__name__, instance_path=self.temp.name)
        self.app.config.update(SECRET_KEY='test', SQLALCHEMY_DATABASE_URI='sqlite://', TESTING=True)
        db.init_app(self.app)
        self.app.register_blueprint(tts_bp, url_prefix='/api/tts')
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        db.session.add(User(id=1, username='tts-test', password_hash='unused'))
        db.session.add(Recipe(id=1, user_id=1, title='清蒸鱼', ingredients='鱼一条\n姜少许',
                              instructions='1. 洗净。\n2. 蒸熟。'))
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()
        self.temp.cleanup()

    def login(self, uid=1):
        with self.client.session_transaction() as sess:
            sess['user_id'] = uid

    def test_login_and_ownership_checked_before_synthesis(self):
        with patch('utils.web_tts.audio_for_recipe') as generate:
            self.assertEqual(self.client.get('/api/tts/recipes/1/audio').status_code, 401)
            self.login(2)
            self.assertEqual(self.client.get('/api/tts/recipes/1/audio').status_code, 404)
            self.login()
            self.assertEqual(self.client.get('/api/tts/recipes/999/audio').status_code, 404)
            generate.assert_not_called()

    def test_mp3_and_range_response(self):
        target = Path(self.temp.name) / 'sample.mp3'
        target.write_bytes(b'ID3' + b'\0' * 2000)
        self.login()
        with patch('utils.web_tts.audio_for_recipe', return_value=target):
            r = self.client.get('/api/tts/recipes/1/audio')
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.mimetype, 'audio/mpeg')
            self.assertIn('private', r.headers['Cache-Control'])
            self.assertEqual(len(r.data), 2003)
            r.close()
            r = self.client.get('/api/tts/recipes/1/audio', headers={'Range': 'bytes=0-99'})
            self.assertEqual(r.status_code, 206)
            self.assertEqual(len(r.data), 100)
            r.close()

    def test_native_controls_and_links_have_audio_without_javascript(self):
        template = (Path(__file__).parent / 'templates/recipe/detail.html').read_text(encoding='utf-8')
        fragment = template.split('<div class="tts-player"', 1)[1].split('<!-- 换一道', 1)[0]
        class MediaMarkup(HTMLParser):
            def __init__(self):
                super().__init__()
                self.tags = []
            def handle_starttag(self, tag, attrs):
                self.tags.append((tag, dict(attrs)))
        parser = MediaMarkup()
        with self.app.test_request_context():
            parser.feed(render_template_string('<div class="tts-player"' + fragment,
                                               recipe=db.session.get(Recipe, 1)))
        url = '/api/tts/recipes/1/audio'
        player = next(attrs for tag, attrs in parser.tags if attrs.get('id') == 'ttsAudio')
        self.assertEqual(player['src'], url)
        self.assertIn('controls', player)
        links = [attrs for tag, attrs in parser.tags if tag == 'a' and attrs.get('href') == url]
        self.assertEqual(len(links), 2)
        self.assertTrue(any(attrs.get('id') == 'ttsBtn' for attrs in links))
        self.assertTrue(all(attrs.get('target') == '_blank' for attrs in links))
        self.login()
        target = Path(self.temp.name) / 'native.mp3'
        target.write_bytes(b'ID3' + b'\0' * 2000)
        with patch('utils.web_tts.audio_for_recipe', return_value=target):
            response = self.client.get(player['src'], headers={'Range': 'bytes=0-1'})
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.headers['Content-Range'], 'bytes 0-1/2003')
            self.assertEqual(response.mimetype, 'audio/mpeg')
            response.close()

    def test_service_failure_returns_503(self):
        self.login()
        with patch('utils.web_tts.audio_for_recipe', side_effect=RuntimeError('offline')):
            self.assertEqual(self.client.get('/api/tts/recipes/1/audio').status_code, 503)

    def test_text_contains_food_steps_and_glucose_advice(self):
        text = recipe_text(db.session.get(Recipe, 1))
        for expected in ['清蒸鱼', '控糖提示', '所需食材', '第1步', '第2步']:
            self.assertIn(expected, text)

    def test_generation_is_atomic_cached_and_content_sensitive(self):
        calls = []
        class Communicate:
            def __init__(self, text, voice, **kwargs):
                calls.append((text, voice))
            async def save(self, filename):
                Path(filename).write_bytes(b'ID3' + b'\0' * 2000)
        recipe = db.session.get(Recipe, 1)
        with patch.dict(sys.modules, edge_tts=types.SimpleNamespace(Communicate=Communicate)):
            first = audio_for_recipe(recipe, self.temp.name)
            self.assertEqual(audio_for_recipe(recipe, self.temp.name), first)
            self.assertEqual(len(calls), 1)
            recipe.instructions += '\n3. 装盘。'
            self.assertNotEqual(audio_for_recipe(recipe, self.temp.name), first)
        self.assertEqual(list((Path(self.temp.name) / 'tts').glob('*.tmp')), [])

    def test_empty_or_invalid_audio_is_not_cached(self):
        class Communicate:
            def __init__(self, *args, **kwargs): pass
            async def save(self, filename):
                Path(filename).write_bytes(b'not an mp3' * 200)
        with patch.dict(sys.modules, edge_tts=types.SimpleNamespace(Communicate=Communicate)):
            with self.assertRaises(RuntimeError):
                audio_for_recipe(db.session.get(Recipe, 1), self.temp.name)
        self.assertEqual(list((Path(self.temp.name) / 'tts').iterdir()), [])


if __name__ == '__main__':
    unittest.main()
