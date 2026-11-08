# init_health_metrics.py
"""
建表 + 回填历史报告指标（一次性维护脚本，幂等，可重复运行）

做什么
    1. 补出 `health_metric` 表（只 CREATE 缺的那张，**不碰任何已有表**）
    2. 把库里已有的健康报告逐份解析，指标写入新表

为什么单独一个脚本
    主 app 启动时会 `db.create_all()` 自动补表，但本机 Flask 服务正在跑、
    且重启会和看门狗抢端口，所以这里用「最小应用 + 同一个 app.db」离线补表 + 回填，
    不必重启服务。脚本**不 import app.py**，因此不会加载 ultralytics / 模型，
    不会影响正在进行的 YOLO 训练。

用法
    python init_health_metrics.py --dry-run   # 只看会写什么，不动数据库
    python init_health_metrics.py             # 真正建表 + 回填

    （Windows 上用项目自己的解释器：
     "C:/Users/123/AppData/Local/Programs/Python/Python314/python.exe" init_health_metrics.py）
"""
import os
import sys

from flask import Flask

from config import config as app_config
from utils.database import db
from models.user import User                      # noqa: F401  建表需要（外键指向 user）
from models.health_report import HealthReport
from models.health_metric import HealthMetric
from utils.metric_store import save_metrics_for_report, latest_metrics

DRY_RUN = '--dry-run' in sys.argv


def build_app():
    """最小应用：只挂配置和数据库，不注册蓝图、不加载模型。"""
    app = Flask(__name__)
    app.config.from_object(app_config['default'])
    db.init_app(app)
    return app


def main():
    uri = app_config['default'].SQLALCHEMY_DATABASE_URI
    print('=' * 78)
    print('健康指标建表 + 回填')
    print('=' * 78)
    print(f'数据库      : {uri}')
    print(f'模式        : {"DRY-RUN（只读，不写库）" if DRY_RUN else "正式执行（会写入 health_metric）"}')
    print('-' * 78)

    app = build_app()

    with app.app_context():
        reports = HealthReport.query.order_by(HealthReport.id.asc()).all()
        print(f'待处理报告  : {len(reports)} 份')

        if not DRY_RUN:
            db.create_all()      # 只补缺失的表
            print('建表完成    : health_metric（已存在则跳过）')
        print('-' * 78)

        total_saved = 0
        for report in reports:
            parsed_metrics = None
            result = None
            if DRY_RUN:
                # 只解析不写库
                from utils.report_parser import parse_health_report
                parsed_metrics = parse_health_report(report.content_text)
                saved = parsed_metrics['found']
            else:
                result = save_metrics_for_report(report)
                saved = result['saved']

            total_saved += saved
            status = f'{saved} 项'
            if result and result.get('error'):
                status += f"  出错: {result['error']}"
            print(f'  报告 #{report.id:<4} 用户 {report.user_id:<4} 正文 {len(report.content_text or ""):<5} 字 → {status}')

        print('-' * 78)
        print(f'合计落库    : {total_saved} 行')

        if not DRY_RUN:
            user_ids = sorted({r.user_id for r in reports})
            for uid in user_ids:
                metrics = latest_metrics(uid)
                keys = [m['key'] for m in metrics]
                print(f'  用户 {uid}: 最新指标 {len(metrics)} 项 {keys}')
            print('-' * 78)
            print(f'health_metric 表当前行数: {HealthMetric.query.count()}')
        else:
            print('DRY-RUN 结束，数据库未做任何修改。')

    print('=' * 78)
    return 0


if __name__ == '__main__':
    sys.exit(main())
