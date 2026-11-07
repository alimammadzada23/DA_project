"""Large-table imports: row and disk limits, streaming Excel, folders, header-less CSV."""
from collections import namedtuple

import pytest

from insightpilot.universal import Workspace, store
from insightpilot.universal import disk_space

Usage = namedtuple('Usage', 'total used free')


@pytest.fixture
def workspace(tmp_path):
    return Workspace.create(tmp_path / 'workspace')


def write_csv(path, rows=3):
    path.write_text('x,y\n' + ''.join(f'{i},v{i}\n' for i in range(rows)))
    return path


def test_row_limit_is_100_million():
    assert store.MAX_ROWS == 100_000_000


def test_files_over_the_row_limit_are_rejected_not_sampled(workspace, tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'MAX_ROWS', 3)
    assert workspace.add_file(write_csv(tmp_path / 'fits.csv', 3), 'fits')['rows'] == 3
    with pytest.raises(ValueError, match='no silent sampling'):
        workspace.add_file(write_csv(tmp_path / 'over.csv', 4), 'over')
    assert set(workspace.tables()) == {'fits'}


def test_short_disk_is_refused_before_anything_is_copied(workspace, tmp_path, monkeypatch):
    source = write_csv(tmp_path / 'data.csv')
    monkeypatch.setattr(disk_space.shutil, 'disk_usage', lambda _: Usage(10 ** 12, 10 ** 12, 1024 ** 3))
    with pytest.raises(ValueError, match=r'Not enough free disk space.*1\.0 GB is free.*Nothing was imported'):
        workspace.add_file(source, 'data')
    assert workspace.tables() == {}
    assert not (workspace.directory / 'sources').exists()


def test_disk_estimate_counts_copy_table_and_margin(tmp_path):
    csv = tmp_path / 'a.csv'; csv.write_bytes(b'x' * 1000)
    parquet = tmp_path / 'a.parquet'; parquet.write_bytes(b'x' * 1000)
    assert disk_space.ensure_free_space(tmp_path, [csv])['required_bytes'] == 2000 + disk_space.SAFETY_MARGIN_BYTES
    assert disk_space.ensure_free_space(tmp_path, [parquet])['required_bytes'] == 4000 + disk_space.SAFETY_MARGIN_BYTES


def test_the_old_512_mib_file_cap_is_gone():
    assert not hasattr(store, 'MAX_BYTES')
