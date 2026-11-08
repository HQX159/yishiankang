# models/health_report.py
from utils.database import db
from datetime import datetime


class HealthReport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    filepath = db.Column(db.String(500), nullable=False)
    content_text = db.Column(db.Text)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    # user 关系由 User 模型中的 backref 自动创建，无需在此处重复定义
    # user = db.relationship('User', backref=db.backref('health_reports', lazy=True))

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'filename': self.filename,
            'filepath': self.filepath,
            'content_text': self.content_text,
            'uploaded_at': self.uploaded_at.isoformat() if self.uploaded_at else None
        }