import json
from pathlib import Path


def test_sviridov_catalog_covers_only_reference_solution_numbers_once():
    catalog = json.loads((Path(__file__).parents[1] / 'content/general_inorganic_lab/sviridov-blueprints.json').read_text())
    numbers = [n for bp in catalog['blueprints'] for n in bp['source_numbers']]
    expected = {f'7.{n}' for n in range(1, 138) if n not in (67, 136)}
    expected |= {f'8.{n}' for n in range(1, 243)} | {f'9.{n}' for n in range(1, 85)}
    assert len(numbers) == len(set(numbers)) == catalog['eligible_count'] == 461
    assert set(numbers) == expected
    assert all(bp['instructions'] and bp['source_numbers'] for bp in catalog['blueprints'])
