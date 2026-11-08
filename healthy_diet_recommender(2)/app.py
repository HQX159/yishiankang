from dotenv import load_dotenv
load_dotenv()

from flask import Flask, render_template, redirect, url_for, session, request, jsonify
from config import config
from utils.database import db
from routes.user_routes import user_bp
from routes.upload_routes import upload_bp
from routes.recipe_routes import recipe_bp
from routes.tts_routes import tts_bp
from routes.voice_routes import voice_bp
from routes.wechat_routes import wechat_bp
from routes.api_v1 import api_v1_bp
from routes.health_routes import health_bp
from routes.nutrition_routes import nutrition_bp
from routes.community_routes import community_bp
from routes.checkin_routes import checkin_bp
from models.recipe import Recipe, Favorite
from models.community import CommunityPost, PostInteraction
from models.health_report import HealthReport
from models.health_metric import HealthMetric
from models.fridge_image import FridgeImage
from models.ingredient import Ingredient
from models.food_lookup import FoodLookupRequest, FoodLookupUsage
from utils.checkin import build_card_context
from utils.visit_advice import build_visit_advice
import os
from flask_cors import CORS

def create_app():
    app = Flask(__name__)
    os.makedirs(app.instance_path, exist_ok=True)
    app.config.from_object(config['default'])
    # app.secret_key = config.SECRET_KEY
    app.secret_key = app.config.get('SECRET_KEY') or os.environ.get('FLASK_SECRET_KEY') or 'dev-secret-key-change-in-production'

    # 初始化扩展
    db.init_app(app)
    CORS(app)

    # 确保上传目录存在
    os.makedirs(app.config['UPLOAD_FOLDER_HEALTH_REPORTS'], exist_ok=True)
    os.makedirs(app.config['UPLOAD_FOLDER_FRIDGE_IMAGES'], exist_ok=True)
    os.makedirs('static/audio', exist_ok=True)

    # 注册蓝图 - 使用前缀以匹配前端API调用
    app.register_blueprint(user_bp, url_prefix='/api/user')
    app.register_blueprint(upload_bp, url_prefix='/api/upload')
    app.register_blueprint(recipe_bp, url_prefix='/api/recipes')
    app.register_blueprint(tts_bp, url_prefix='/api/tts')
    app.register_blueprint(voice_bp, url_prefix='/api/voice')
    app.register_blueprint(wechat_bp, url_prefix='/api/wechat')
    app.register_blueprint(api_v1_bp, url_prefix='/api/v1')
    app.register_blueprint(health_bp, url_prefix='/health')
    app.register_blueprint(nutrition_bp, url_prefix='/nutrition')
    app.register_blueprint(community_bp, url_prefix='/api/community')
    app.register_blueprint(checkin_bp, url_prefix='/api/checkin')

    # 创建数据库表
    with app.app_context():
        db.create_all()

    # 主页路由
    @app.route('/')
    def index():
        if 'user_id' in session:
            return redirect(url_for('dashboard'))
        return redirect(url_for('user.login'))

    # 仪表盘主页
    @app.route('/dashboard')
    def dashboard():
        if 'user_id' not in session:
            return redirect(url_for('user.login'))

        user_id = session['user_id']
        stats = {}

        # 食谱数量
        stats['recipe_count'] = Recipe.query.filter_by(user_id=user_id).count()

        # 收藏数量
        stats['favorite_count'] = Favorite.query.filter_by(user_id=user_id).count()

        # 健康报告数量
        stats['report_count'] = HealthReport.query.filter_by(user_id=user_id).count()

        # 最新健康报告
        stats['latest_health'] = HealthReport.query.filter_by(user_id=user_id) \
            .order_by(HealthReport.uploaded_at.desc()).first()

        # 最新冰箱照片和食材
        stats['latest_fridge'] = FridgeImage.query.filter_by(user_id=user_id) \
            .order_by(FridgeImage.uploaded_at.desc()).first()

        if stats['latest_fridge']:
            ingredients = Ingredient.query.filter_by(
                fridge_image_id=stats['latest_fridge'].id
            ).all()
            stats['latest_ingredients'] = ingredients
            stats['ingredient_count'] = len(ingredients)
        else:
            stats['latest_ingredients'] = []
            stats['ingredient_count'] = 0

        # 最近食谱
        stats['recent_recipes'] = Recipe.query.filter_by(user_id=user_id) \
            .order_by(Recipe.created_at.desc()).limit(3).all()

        # 最近食谱的控糖档位（R1 糖尿病专项：卡片上的「低/中/高升糖」小徽标）
        from routes.recipe_routes import advice_map
        stats['recent_advices'] = advice_map(stats['recent_recipes'])

        # R3 社区：我分享过几条 + 广场最新 3 条（仪表盘上给个入口，不用先去导航翻）
        from models.community import list_posts
        stats['my_share_count'] = CommunityPost.query.filter_by(user_id=user_id).count()
        stats['community_latest'] = list_posts(per_page=3)[0]
        stats['liked_post_ids'] = {
            r[0] for r in db.session.query(PostInteraction.post_id)
            .filter(PostInteraction.user_id == user_id,
                    PostInteraction.type == 'like').all()
        }

        # R5 每日血糖记录：卡片数据（今天已填的回填、上次的值做提示）
        # 写成 /dashboard 而不是 url_for('dashboard')，是因为表单里的 next 要当
        # 相对路径用（保存后跳回来），url_for 给的是绝对路径也对，但保持相对更干净
        stats['checkin'] = build_card_context(
            user_id,
            action_url=url_for('checkin.save'),
            next_url='/dashboard',
        )

        # R5 第 3 项 就诊沟通清单：指标偏高 / 连续多天偏高时出现。
        # 放在仪表盘是因为"今天要不要去医院"这个念头不会等到用户点进报告页才出现，
        # 而且它只在下述情况渲染：判档 warn/critical，或近 3 天连续偏高（show=False 时模板整块不出）。
        stats['visit'] = build_visit_advice(user_id)

        return render_template('dashboard/index.html', stats=stats)

    return app

if __name__ == '__main__':
    app = create_app()
    app.run(debug=True, host='0.0.0.0', port=5000)
else:
    # gunicorn 启动时使用：gunicorn app:app
    app = create_app()