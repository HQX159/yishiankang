"""将官方 SR Legacy ZIP 中人工确认的食品转换为可追溯营养快照。

只读 ZIP，不解压执行；不自动匹配名称、不生成 GI 和个人份量。
python scripts/import_usda_foods.py --archive PATH [--output PATH]
"""
import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DOWNLOAD_URL = 'https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_sr_legacy_food_csv_2018-04.zip'
NUTRIENTS = {'1005': ('carbohydrate_g', 'G'), '1079': ('fiber_g', 'G'),
             '2000': ('sugars_g', 'G'), '1003': ('protein_g', 'G'),
             '1004': ('fat_g', 'G'), '1008': ('energy_kcal', 'KCAL'),
             '1093': ('sodium_mg', 'MG')}


def table(archive, filename):
    matches = [n for n in archive.namelist() if n.endswith('/' + filename)]
    if len(matches) != 1:
        raise ValueError('官方归档缺少或重复数据表：' + filename)
    with archive.open(matches[0]) as raw:
        yield from csv.DictReader(io.TextIOWrapper(raw, encoding='utf-8-sig'))


def build_snapshot(archive_path, selection_path):
    selection = json.loads(Path(selection_path).read_text(encoding='utf-8'))
    selected = selection['foods']
    if len({f['id'] for f in selected}) != len(selected):
        raise ValueError('食品 ID 重复')
    ids = {str(f['fdc_id']) for f in selected}
    with zipfile.ZipFile(archive_path) as archive:
        foods = {r['fdc_id']: r for r in table(archive, 'food.csv') if r['fdc_id'] in ids}
        units = {r['id']: r['unit_name'].upper() for r in table(archive, 'nutrient.csv')}
        values = {key: {} for key in ids}
        for row in table(archive, 'food_nutrient.csv'):
            if row['fdc_id'] not in ids or row['nutrient_id'] not in NUTRIENTS:
                continue
            field, unit = NUTRIENTS[row['nutrient_id']]
            if units.get(row['nutrient_id']) != unit:
                raise ValueError('营养成分单位不符：' + row['nutrient_id'])
            if not row['amount'].strip():
                continue
            value = float(row['amount'])
            if not math.isfinite(value) or value < 0 or (unit == 'G' and value > 100):
                raise ValueError('营养数值超出范围')
            target = values[row['fdc_id']]
            if field in target:
                raise ValueError('同一食品营养成分重复，须人工处理')
            target[field] = value
        records = []
        for item in selected:
            key = str(item['fdc_id'])
            food = foods.get(key)
            if not food or food['data_type'] != 'sr_legacy_food' or food['description'] != item['expected_description']:
                raise ValueError('食品缺失、类型或描述发生变化：' + key)
            nutrition = {field: values[key].get(field) for field, _ in NUTRIENTS.values()}
            if nutrition['carbohydrate_g'] is None:
                raise ValueError('选中食品缺少碳水：' + key)
            records.append({**item, 'nutrition': nutrition, 'basis_amount': 100, 'basis_unit': 'g',
                            'source': {'provider': 'USDA FoodData Central', 'data_type': 'SR Legacy',
                                       'release': '2018-04', 'publication_date': food['publication_date'],
                                       'fdc_id': int(key), 'license': 'CC0-1.0',
                                       'url': f'https://fdc.nal.usda.gov/food-details/{key}/nutrients'}})
    return {'schema_version': 1, 'verified_on': selection['verified_on'],
            'verification_scope': '食品形态与官方数据字段核对；不是临床份量审核',
            'download_url': DOWNLOAD_URL,
            'archive_sha256': hashlib.sha256(Path(archive_path).read_bytes()).hexdigest(),
            'carbohydrate_definition': 'USDA carbohydrate by difference，包含膳食纤维；不等同于所有中文标签口径',
            'foods': records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True)
    parser.add_argument('--selection', default=ROOT / 'data/food_usda_selection.json')
    parser.add_argument('--output', default=ROOT / 'data/food_usda_snapshot.json')
    args = parser.parse_args()
    snapshot = build_snapshot(args.archive, args.selection)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + '.tmp')
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(output)
    print(f'已生成 {len(snapshot["foods"])} 条官方食品营养记录；未生成 GI 或个人份量。')


if __name__ == '__main__':
    main()
