# -*- coding: utf-8 -*-
"""益食安康 · 幂等表结构迁移（部署前必跑）

为什么需要它
------------
`app.py` 里已经有 `db.create_all()`，它能补**缺失的表**，但**不会给已存在的表加列**。
2026-10-19 线上就栽在这：代码带上了 R4 三列（purpose / flavor / meal_time），
线上 `recipe` 表还是老的 10 列 → 所有查 recipe 的接口 500
（`g80-error.log` 里 `no such column: recipe.purpose`）。

它做什么
--------
以 `models/*.py` 为**唯一真相源**，跟真实库比对后只做三种增量动作：

  1. 建缺失的表            （交给 create_all，checkfirst）
  2. 给已存在的表补缺失的列（ALTER TABLE ... ADD COLUMN）
  3. 补缺失的索引 / 唯一约束（CREATE [UNIQUE] INDEX IF NOT EXISTS）

**绝不** DROP TABLE、DROP COLUMN、改类型、改约束、删数据。
重复跑结果一样（幂等），不需要记"迁到第几版了"。

用法
----
    python migrate.py                       # 用 config.py 算出的库（本地开发）
    python migrate.py --db /path/to/app.db  # 指定库（服务器部署用这个）
    python migrate.py --dry-run             # 只报告要改什么，一个字都不写
    python migrate.py --quiet               # 只在有改动或出错时输出（给 auto_sync 用）

⚠️ 本文件**故意不写 `#!/usr/bin/env python3`**：Windows 上的 `py` 启动器看到这行会
   去找 `python3`，而本机 `python3` 指向宿主的托管 3.13（没装 flask）→ 直接
   ModuleNotFoundError。去掉后 `py migrate.py` 才会用系统 3.14。Linux 上照旧用
   `venv/bin/python3 migrate.py` 显式指定解释器，不依赖 shebang。

退出码
------
    0  已是最新，或迁移成功
    1  有无法安全自动完成的改动（如给老表加 NOT NULL 且无默认值的列）→ 需人工
    2  运行出错（库被锁、路径不存在、导入失败……）
"""
import argparse
import glob
import os
import sqlite3
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# 备份保留个数：每次真正改表前备份一次，只留最近的，避免堆成垃圾场
KEEP_BACKUPS = 5


# ── 输出 ─────────────────────────────────────────────────────
class Out:
    def __init__(self, quiet=False):
        self.quiet = quiet
        self.changed = 0
        self.lines = []

    def info(self, msg):
        self.lines.append(msg)
        if not self.quiet:
            print(msg)

    def always(self, msg):
        self.lines.append(msg)
        print(msg)


# ── 加载模型元数据 ───────────────────────────────────────────
def load_db(db_path):
    """建一个最小 Flask app 挂上 db，只注册模型，不碰路由/蓝图。

    不走 app.create_app()：那会注册一堆蓝图、建上传目录、还会读 .env，
    迁移只需要 metadata，越干净越好。
    """
    from flask import Flask
    from utils.database import db

    app = Flask('migrate')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + db_path.replace('\\', '/')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db.init_app(app)

    # 逐个导入，把模型注册进 db.metadata（models/__init__.py 是空的，不会自动注册）
    import models.user            # noqa: F401
    import models.recipe          # noqa: F401
    import models.community       # noqa: F401
    import models.health_report   # noqa: F401
    import models.health_metric   # noqa: F401
    import models.fridge_image    # noqa: F401
    import models.ingredient      # noqa: F401
    import models.food_lookup     # noqa: F401

    ctx = app.app_context()
    ctx.push()
    return db, ctx


# ── 规划：比对模型与真实库 ───────────────────────────────────
def q(name):
    return '"%s"' % name.replace('"', '""')


def index_signatures(conn, table):
    """库里所有**唯一**索引的列组合，用来判断某个唯一约束是否已生效。"""
    sigs = set()
    for row in conn.execute('PRAGMA index_list(%s)' % q(table)):
        # row = (seq, name, unique, origin, partial)
        if row[2]:
            cols = tuple(c[2] for c in conn.execute('PRAGMA index_info(%s)' % q(row[1])))
            sigs.add(cols)
    return sigs


def add_column_sql(table, col):
    """生成 ALTER TABLE ADD COLUMN。返回 (sql, 问题说明)，二者必有一为 None。"""
    from sqlalchemy.dialects import sqlite as sqlite_dialect
    dialect = sqlite_dialect.dialect()

    try:
        coltype = col.type.compile(dialect=dialect)
    except Exception as e:
        return None, '类型无法编译（%s）' % e

    sql = 'ALTER TABLE %s ADD COLUMN %s %s' % (q(table.name), q(col.name), coltype)

    if not col.nullable:
        # SQLite 的 ADD COLUMN 不允许「NOT NULL 且无默认值」
        if col.server_default is not None:
            arg = col.server_default.arg
            default = arg.text if hasattr(arg, 'text') else str(arg)
            sql += ' NOT NULL DEFAULT %s' % default
        else:
            return None, ('模型里是 NOT NULL 但没给 server_default。SQLite 无法给已有表加这种列，'
                          '请给该列加 server_default，或先手工回填再上线')
    return sql, None


def plan(db, conn):
    """返回 (actions, problems)。actions 元素 = (类别, 描述, SQL 或 None)。"""
    actions, problems = [], []

    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}

    for tname, table in sorted(db.metadata.tables.items()):
        if tname not in tables:
            actions.append(('table', tname, None))       # create_all 负责
            continue

        have_cols = {r[1] for r in conn.execute('PRAGMA table_info(%s)' % q(tname))}
        for col in table.columns:
            if col.name in have_cols:
                continue
            sql, err = add_column_sql(table, col)
            if err:
                problems.append('%s.%s：%s' % (tname, col.name, err))
            else:
                actions.append(('column', '%s.%s' % (tname, col.name), sql))

        # 索引：create_all 对已存在的表整个跳过，所以新索引得自己补
        # ⚠️ 必须先按**名字**排除已存在的索引：否则每次都会把老索引当成"待补"，
        #    IF NOT EXISTS 虽然不会报错，但迁移后自检永远不通过（会判成失败）。
        have_idx = {r[1] for r in conn.execute('PRAGMA index_list(%s)' % q(tname))}
        uniq_sigs = index_signatures(conn, tname)
        for idx in table.indexes:
            cols = tuple(c.name for c in idx.columns)
            if idx.name in have_idx:
                continue                                  # 索引已存在
            if idx.unique and cols in uniq_sigs:
                continue                                  # 已有等价唯一索引
            body = ', '.join(q(c) for c in cols)
            sql = 'CREATE %sINDEX IF NOT EXISTS %s ON %s (%s)' % (
                'UNIQUE ' if idx.unique else '', q(idx.name), q(tname), body)
            actions.append(('index', idx.name, sql))

        # 唯一约束（UniqueConstraint 在 SQLite 里是建表时写进 CREATE TABLE 的，
        # 老表补不了；用等价唯一索引代替，语义一致）
        for cons in table.constraints:
            from sqlalchemy import UniqueConstraint
            if not isinstance(cons, UniqueConstraint) or not cons.name:
                continue
            cols = tuple(c.name for c in cons.columns)
            if cols in uniq_sigs:
                continue
            body = ', '.join(q(c) for c in cols)
            sql = 'CREATE UNIQUE INDEX IF NOT EXISTS %s ON %s (%s)' % (
                q(cons.name), q(tname), body)
            actions.append(('constraint', cons.name, sql))

    return actions, problems


# ── 备份 ─────────────────────────────────────────────────────
def backup_db(db_path):
    """用 sqlite3 的在线备份 API 拷贝 —— 比 shutil.copy2 安全（不会抄到写了一半的页）。"""
    d = os.path.join(os.path.dirname(db_path), 'backups')
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, 'app.db.%s.bak' % time.strftime('%Y%m%d-%H%M%S'))
    src = sqlite3.connect(db_path, timeout=30)
    try:
        out = sqlite3.connect(dst)
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()

    olds = sorted(glob.glob(os.path.join(d, 'app.db.*.bak')))
    for old in olds[:-KEEP_BACKUPS]:
        try:
            os.remove(old)
        except OSError:
            pass
    return dst


# ── 主流程 ───────────────────────────────────────────────────
def resolve_db(arg):
    """--db 优先；否则用 config.py 算出来的路径（跟线上服务一致）。"""
    if arg:
        return os.path.abspath(arg)
    import config
    uri = config.Config.SQLALCHEMY_DATABASE_URI
    if not uri.startswith('sqlite:///'):
        raise SystemExit('只支持本地 sqlite 库，当前是 %r' % uri)
    return os.path.abspath(uri[len('sqlite:///'):])


def main():
    ap = argparse.ArgumentParser(description='益食安康 · 幂等表结构迁移')
    ap.add_argument('--db', help='sqlite 库文件路径')
    ap.add_argument('--dry-run', action='store_true', help='只报告，不写库')
    ap.add_argument('--quiet', action='store_true', help='只在有改动/出错时输出')
    args = ap.parse_args()

    out = Out(quiet=args.quiet)

    try:
        db_path = resolve_db(args.db)
    except SystemExit as e:
        out.always('[migrate] FATAL %s' % e)
        return 2

    if not os.path.exists(db_path):
        out.always('[migrate] FATAL 库文件不存在：%s' % db_path)
        return 2

    out.info('[migrate] 目标库 %s' % db_path)

    try:
        db, ctx = load_db(db_path)
    except Exception as e:
        out.always('[migrate] FATAL 加载模型失败：%s: %s' % (type(e).__name__, e))
        traceback.print_exc()
        return 2

    try:
        conn = sqlite3.connect(db_path, timeout=30)
        try:
            actions, problems = plan(db, conn)
        finally:
            conn.close()

        if problems:
            for p in problems:
                out.always('[migrate] 需人工 %s' % p)

        if not actions:
            if problems:
                out.always('[migrate] 有 %d 项无法自动补，需手工处理' % len(problems))
                return 1
            out.info('[migrate] 表结构已是最新，无需改动')
            return 0

        for kind, name, _sql in actions:
            out.info('[migrate] 待补 %-10s %s' % (kind, name))

        if args.dry_run:
            out.always('[migrate] DRY-RUN：共 %d 项待补，未写库' % len(actions))
            return 1 if problems else 0

        bak = backup_db(db_path)
        out.info('[migrate] 已备份 -> %s' % bak)

        conn = sqlite3.connect(db_path, timeout=30)
        done = 0
        try:
            for kind, name, sql in actions:
                if kind == 'table':
                    continue                      # 交给下面的 create_all
                try:
                    conn.execute(sql)
                    conn.commit()
                    done += 1
                    out.info('[migrate] OK   %s %s' % (kind, name))
                except Exception as e:
                    out.always('[migrate] FAIL %s %s -> %s' % (kind, name, e))
                    problems.append('%s %s：%s' % (kind, name, e))
        finally:
            conn.close()

        # 缺失的表交给 create_all（它带 checkfirst，已存在的表不动）
        new_tables = [n for k, n, _ in actions if k == 'table']
        if new_tables:
            db.create_all()
            done += len(new_tables)
            for n in new_tables:
                out.info('[migrate] OK   table      %s' % n)

        # 回读校验：把刚才说要补的再算一遍，应该为 0
        conn = sqlite3.connect(db_path, timeout=30)
        try:
            left, left_problems = plan(db, conn)
        finally:
            conn.close()
        if left:
            out.always('[migrate] 警告：迁移后仍有 %d 项未达成（%s）'
                       % (len(left), ', '.join(n for _, n, _ in left[:5])))
            return 1

        out.always('[migrate] 迁移完成：改动 %d 项，表结构已与代码一致' % done)
        return 1 if problems else 0

    except Exception as e:
        out.always('[migrate] FATAL %s: %s' % (type(e).__name__, e))
        return 2
    finally:
        try:
            ctx.pop()
        except Exception:
            pass


if __name__ == '__main__':
    sys.exit(main())
