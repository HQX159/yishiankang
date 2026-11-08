"""验证来源匹配、营养单位与缺失值，避免导入错误成分。"""
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from scripts.import_usda_foods import build_snapshot


class USDAImportTest(unittest.TestCase):
    def build(self, description='Test food, raw', unit='G', nutrients=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        folder = Path(temporary.name)
        selection = folder / 'selection.json'
        selection.write_text(json.dumps({'verified_on': '2026-11-03', 'foods': [
            {'id': 'test', 'fdc_id': 1, 'expected_description': 'Test food, raw'}]}), encoding='utf-8')
        archive = folder / 'test.zip'
        with zipfile.ZipFile(archive, 'w') as output:
            output.writestr('data/food.csv', f'fdc_id,data_type,description,publication_date\n1,sr_legacy_food,"{description}",2019-04-01\n')
            output.writestr('data/nutrient.csv', f'id,unit_name\n1005,{unit}\n2000,G\n')
            output.writestr('data/food_nutrient.csv', 'fdc_id,nutrient_id,amount\n' +
                            (nutrients if nutrients is not None else '1,1005,0\n'))
        return archive, selection

    def test_zero_is_not_missing_and_deterministic(self):
        archive, selection = self.build()
        snapshot = build_snapshot(archive, selection)
        self.assertEqual(snapshot, build_snapshot(archive, selection))
        self.assertEqual(snapshot['foods'][0]['nutrition']['carbohydrate_g'], 0)
        self.assertIsNone(snapshot['foods'][0]['nutrition']['sugars_g'])
        self.assertEqual(len(snapshot['archive_sha256']), 64)

    def test_wrong_form_or_unit_rejected(self):
        for args in [{'description': 'Test food, cooked'}, {'unit': 'MG'}]:
            with self.subTest(args=args):
                archive, selection = self.build(**args)
                with self.assertRaises(ValueError):
                    build_snapshot(archive, selection)

    def test_missing_duplicate_or_invalid_nutrients_rejected(self):
        for nutrients in ['', '1,1005,\n', '1,1005,NaN\n', '1,1005,-1\n',
                          '1,1005,101\n', '1,1005,10\n1,1005,20\n']:
            with self.subTest(nutrients=nutrients):
                archive, selection = self.build(nutrients=nutrients)
                with self.assertRaises(ValueError):
                    build_snapshot(archive, selection)


if __name__ == '__main__':
    unittest.main()
