"""未收录食品队列及短期外部查询缓存；不保存用户健康提问。"""
from datetime import datetime, timezone
from utils.database import db


def utc_now():
    # 与项目 SQLite DateTime 的无时区 UTC 存储约定一致。
    return datetime.now(timezone.utc).replace(tzinfo=None)


class FoodLookupRequest(db.Model):
    __tablename__ = 'food_lookup_request'
    id = db.Column(db.Integer, primary_key=True)
    food_name = db.Column('query', db.String(60), unique=True, nullable=False)
    query_count = db.Column(db.Integer, nullable=False, default=0)
    review_status = db.Column(db.String(24), nullable=False, default='pending')
    result_json = db.Column(db.Text)
    draft_json = db.Column(db.Text)
    expires_at = db.Column(db.DateTime)
    lease_until = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now)


class FoodLookupUsage(db.Model):
    __tablename__ = 'food_lookup_usage'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    bucket = db.Column(db.Integer, nullable=False)
    count = db.Column(db.Integer, nullable=False, default=0)
    __table_args__ = (db.UniqueConstraint('user_id', 'bucket', name='uq_food_lookup_usage_window'),)
