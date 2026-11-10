"""Optional: automatic analysis on public datasets, verified against pandas/SciPy on the raw files.

The data files are not committed. Download them as described in docs/AUTOMATIC_ANALYSIS.md
into outputs/public-datasets (or set INSIGHTPILOT_PUBLIC_DATA). Missing files skip with a
message. The two large datasets run only with INSIGHTPILOT_LARGE_TESTS=1.
"""
import importlib.util
import os
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / 'scripts/auto_analysis_datasets.py'
spec = importlib.util.spec_from_file_location('auto_analysis_datasets', SCRIPT)
datasets = importlib.util.module_from_spec(spec); spec.loader.exec_module(datasets)
LARGE = {'covertype', 'retail'}
FILES = {'telco': None, 'adult': 'adult/adult.data', 'penguins': 'penguins/penguins.csv', 'bike': 'bike/hour.csv',
         'covertype': 'covertype/covtype.data', 'retail': 'retail/online_retail_II.xlsx'}


@pytest.mark.parametrize('key,loader,verify', datasets.DATASETS, ids=[d[0] for d in datasets.DATASETS])
def test_public_dataset_numbers_match_independent_calculations(key, loader, verify, tmp_path):
    if key in LARGE and os.environ.get('INSIGHTPILOT_LARGE_TESTS') != '1':
        pytest.skip(f'{key} is large; set INSIGHTPILOT_LARGE_TESTS=1 to run it.')
    if FILES[key] and not (datasets.DATA / FILES[key]).is_file():
        pytest.skip(f'{datasets.DATA / FILES[key]} not found; see docs/AUTOMATIC_ANALYSIS.md for the download commands.')
    result = datasets.run_dataset(key, loader, verify, tmp_path)
    if result is None:
        pytest.skip('Telco file not found; set INSIGHTPILOT_TELCO_CSV to its path.')
    assert not [s for s, status in result['stages'].items() if status == 'failed'], result['stages']
    assert not result['failures'], result['failures']
    assert [c for c in result['checks'] if not c['match']] == []
