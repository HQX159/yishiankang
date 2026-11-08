# models/health_metric.py
"""
健康指标表 —— 把报告里抽出来的数字存下来

为什么需要这张表
    原先 `HealthReport.content_text` 只是一坨 OCR 原文，指标"用完即弃"：
    生成食谱时抽一次就丢了，下次再想画血糖趋势、做评分，无从下手。
    本表按 **一行一个指标** 的纵向结构存，方便按时间取值、画趋势。

设计要点
    - **新增表，不改任何已有表**：不动 `health_report` 的列，历史数据零影响。
    - 同一份报告 (report_id, metric_key) 唯一，重复解析时按报告覆盖，天然幂等。
    - 抽不到的指标**不落行**，不做"补 0 / 填默认值"。
"""
from utils.database import db
from datetime import datetime


class HealthMetric(db.Model):
    __tablename__ = 'health_metric'

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer, db.ForeignKey('user.id'), nullable=False, index=True
    )
    # 来源报告；手工录入的指标允许为空
    report_id = db.Column(
        db.Integer, db.ForeignKey('health_report.id'), nullable=True, index=True
    )

    metric_key = db.Column(db.String(40), nullable=False, index=True)  # 如 fasting_glucose
    label = db.Column(db.String(40), nullable=False)                   # 如 空腹血糖
    value = db.Column(db.Float, nullable=False)
    unit = db.Column(db.String(20), default='')

    # 指标的"测量时间"，取报告上传时间；趋势图按它排序
    measured_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('report_id', 'metric_key', name='uq_health_metric_report_key'),
        db.Index('ix_health_metric_user_key_time', 'user_id', 'metric_key', 'measured_at'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'key': self.metric_key,
            'label': self.label,
            'value': self.value,
            'unit': self.unit,
            'report_id': self.report_id,
            'measured_at': self.measured_at.isoformat() if self.measured_at else None,
        }

    def __repr__(self):
        return f'<HealthMetric {self.metric_key}={self.value}{self.unit}>'
