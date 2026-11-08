# models/user.py
from utils.database import db
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    # 邮箱改为可选：注册不再强制填写。
    # 为兼容老库（email 仍是 NOT NULL + UNIQUE），未填时由注册接口写入占位值
    # （与微信登录的 {openid}@wechat.local 同一做法）。
    email = db.Column(db.String(120), unique=True, nullable=True)
    password_hash = db.Column(db.String(128), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # # 定义关系并使用 backref 创建反向引用
    # health_reports = db.relationship('HealthReport', backref='user', lazy='dynamic', cascade="all, delete-orphan")
    # fridge_images = db.relationship('FridgeImage', backref='user', lazy='dynamic', cascade="all, delete-orphan")
    # recipes = db.relationship('Recipe', backref='user', lazy='dynamic', cascade="all, delete-orphan")
    # favorites = db.relationship('Favorite', backref='user', lazy='dynamic', cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'email': self.email,
            'created_at': self.created_at.isoformat()
        }