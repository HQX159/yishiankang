import os

basedir = os.path.abspath(os.path.dirname(__file__))


class Config:
    # SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    SECRET_KEY = os.environ.get('SECRET_KEY') or os.environ.get('FLASK_SECRET_KEY') or 'dev-secret-key-change-in-production'
    # SQLALCHEMY_DATABASE_URI
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + os.path.join(basedir, 'instance', 'app.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': 10,
        'pool_recycle': 3600,
        'pool_pre_ping': True,
        'max_overflow': 20,
        'pool_timeout': 30
    }
    
    UPLOAD_FOLDER_HEALTH_REPORTS = os.path.join(basedir, 'static', 'uploads', 'health_reports')
    UPLOAD_FOLDER_FRIDGE_IMAGES = os.path.join(basedir, 'static', 'uploads', 'fridge_images')
    
    SILICONFLOW_API_KEY = os.environ.get('SILICONFLOW_API_KEY') or 'your-api-key-here'
    SILICONFLOW_BASE_URL = os.environ.get('SILICONFLOW_BASE_URL') or 'https://api.siliconflow.cn/v1'
    
    # 视觉大模型。两个用途：
    #   ① 冰箱照片食材识别（detect_ingredients）
    #   ② 健康报告图片 / 扫描版 PDF 的文字转录（utils/report_ocr，2026-10-14 新增）
    # 未单独设置时沿用 SILICONFLOW_*** 一对，可用 VISION_* 覆盖
    VISION_API_KEY = os.environ.get('VISION_API_KEY') or os.environ.get('SILICONFLOW_API_KEY')
    VISION_BASE_URL = os.environ.get('VISION_BASE_URL') or os.environ.get('SILICONFLOW_BASE_URL')
    VISION_MODEL_NAME = os.environ.get('VISION_MODEL_NAME') or 'Qwen/Qwen2.5-VL-7B-Instruct'
    # 备用视觉模型（限流/故障时自动切换）。留空时若端点是智谱，自动配对另一个免费档
    VISION_MODEL_ALT = os.environ.get('VISION_MODEL_ALT') or ''

    TEXT_MODEL_NAME = os.environ.get('TEXT_MODEL_NAME') or 'Qwen/Qwen2.5-7B-Instruct'
    # 食品未命中后查找资料；复用智谱视觉 Key 时只允许官方智谱域名。
    FOOD_LOOKUP_ENABLED = os.environ.get('FOOD_LOOKUP_ENABLED', 'true').lower() in ('1', 'true', 'yes')
    FOOD_SEARCH_API_KEY = os.environ.get('FOOD_SEARCH_API_KEY')
    FOOD_SEARCH_ENGINE = os.environ.get('FOOD_SEARCH_ENGINE', 'search_pro')
    FOOD_SEARCH_DOMAIN = os.environ.get('FOOD_SEARCH_DOMAIN', 'gov.cn')
    ASR_MODEL_NAME = os.environ.get('ASR_MODEL_NAME') or 'whisper-1'
    EMBEDDING_MODEL_NAME = os.environ.get('EMBEDDING_MODEL_NAME') or 'BAAI/bge-small-zh-v1.5'

    # 食材检测权重：默认用自训的 8 类食材模型（r2_eight，mAP50=0.5538，标签为中文）。
    # 可用环境变量 YOLO_MODEL_PATH 覆盖（例如换成 16 类的 r2_core）。
    # 注意必须是项目内绝对路径 —— 相对路径会取决于进程工作目录。
    YOLO_MODEL_PATH = os.environ.get('YOLO_MODEL_PATH') or os.path.join(basedir, 'models', 'yolo', 'best.pt')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'


config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}
