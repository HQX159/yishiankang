"""只读导出食品待补充队列，供维护人员核对；不发布草稿。"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=root / 'instance/app.db')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.db.is_file():
        parser.error('数据库不存在')
    with closing(sqlite3.connect('file:' + args.db.resolve().as_posix() + '?mode=ro', uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute('SELECT query, query_count, review_status, updated_at, result_json, draft_json '
                                  'FROM food_lookup_request ORDER BY query_count DESC, updated_at DESC').fetchall()
    exported = []
    for row in rows:
        item = dict(row)
        item['result'] = json.loads(item.pop('result_json') or '{}')
        item['drafts'] = json.loads(item.pop('draft_json') or '[]')
        exported.append(item)
    args.output.write_text(json.dumps(exported, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'已导出 {len(exported)} 项待核对资料，未修改食品库。')


if __name__ == '__main__':
    main()
