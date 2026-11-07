"""Shared DuckDB resource policy: sizing, overrides, spilling and retained restrictions."""
from pathlib import Path
import re

import duckdb
import pytest

from insightpilot import engine_settings as settings
from insightpilot.universal import Workspace
from insightpilot.universal.engine import Engine
from insightpilot.universal.projects import copy_workspace


@pytest.fixture(autouse=True)
def no_overrides(monkeypatch):
    for name in (settings.MEMORY_ENV, settings.THREADS_ENV, settings.TEMP_ENV):
        monkeypatch.delenv(name, raising=False)


def test_defaults_scale_with_this_machine(monkeypatch):
    monkeypatch.setattr(settings, 'physical_memory', lambda: 16 * 1024 ** 3)
    monkeypatch.setattr(settings.os, 'cpu_count', lambda: 10)
    assert settings.memory_limit() == '9011MB'
    assert settings.thread_count() == 10


def test_small_machines_keep_a_usable_minimum(monkeypatch):
    monkeypatch.setattr(settings, 'physical_memory', lambda: 1024 ** 3)
    assert settings.memory_limit() == '1024MB'


def test_environment_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv(settings.MEMORY_ENV, '2GB')
    monkeypatch.setenv(settings.THREADS_ENV, '3')
    monkeypatch.setenv(settings.TEMP_ENV, str(tmp_path / 'spill'))
    config = settings.connection_config(tmp_path / 'a.duckdb')
    assert config['memory_limit'] == '2GB' and config['threads'] == '3'
    assert Path(config['temp_directory']).parent == tmp_path / 'spill'


@pytest.mark.parametrize('name,value', [(settings.MEMORY_ENV, 'lots'), (settings.MEMORY_ENV, '5'),
                                        (settings.THREADS_ENV, '0'), (settings.THREADS_ENV, 'many')])
def test_invalid_overrides_are_explained(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        settings.resources()


def test_each_database_has_its_own_spill_directory(tmp_path):
    assert settings.spill_directory(tmp_path / 'a.duckdb') != settings.spill_directory(tmp_path / 'b.duckdb')


def test_workspace_connections_use_the_shared_settings(monkeypatch, tmp_path):
    monkeypatch.setenv(settings.MEMORY_ENV, '512MB')
    monkeypatch.setenv(settings.THREADS_ENV, '3')
    workspace = Workspace.create(tmp_path / 'w')
    with workspace.connect() as con:
        memory, threads, temp = con.execute("""SELECT current_setting('memory_limit'),
            current_setting('threads'),current_setting('temp_directory')""").fetchone()
    assert re.match(r'4\d\d(\.\d+)? MiB', memory) and threads == 3
    assert temp == str(settings.spill_directory(workspace.database))


def test_read_connections_stay_restricted_and_writers_still_import(tmp_path):
    workspace = Workspace.create(tmp_path / 'w')
    secret = tmp_path / 'secret.csv'; secret.write_text('x\n1\n')
    with workspace.connect() as con:
        for statement in (f"SELECT * FROM read_csv('{secret}')", 'SET enable_external_access=true',
                          f"SET temp_directory='{tmp_path}'", f"COPY (SELECT 1) TO '{tmp_path}/out.csv'",
                          f"ATTACH '{tmp_path}/other.duckdb'"):
            with pytest.raises(duckdb.Error):
                con.execute(statement)
    # The restriction ends with its database instance; imports open a new one.
    assert workspace.add_file(secret, 'facts')['rows'] == 1
    with workspace.connect() as con:
        with pytest.raises(duckdb.Error):
            con.execute(f"SELECT * FROM read_csv('{secret}')")


def test_large_sort_spills_instead_of_failing(monkeypatch, tmp_path):
    monkeypatch.setenv(settings.MEMORY_ENV, '100MB')
    monkeypatch.setenv(settings.THREADS_ENV, '2')
    monkeypatch.setenv(settings.TEMP_ENV, str(tmp_path / 'spill'))
    workspace = Workspace.create(tmp_path / 'w')
    with workspace.connect(write=True) as con:
        con.execute("CREATE TABLE big AS SELECT i, md5(i::VARCHAR) AS value FROM range(2000000) t(i)")
    with workspace.connect() as con:
        assert con.execute('SELECT count(*) FROM (SELECT DISTINCT value FROM big)').fetchone()[0] == 2_000_000
        assert con.execute('SELECT value FROM big ORDER BY value LIMIT 1 OFFSET 1000000').fetchone()


def test_spill_directory_is_outside_project_snapshots(tmp_path):
    source = Workspace.create(tmp_path / 'w')
    assert source.directory not in settings.spill_directory(source.database).parents
    copy = copy_workspace(source, tmp_path / 'copy')
    assert not list(copy.directory.rglob('*.tmp'))


@pytest.mark.parametrize('seconds,rows,expected', [(20, 1_000, 20), (20, 5_000_000, 20), (20, 50_000_000, 200),
                                                   (10, 50_000_000, 100), (60, 1_000, 20), (.01, 100, .01),
                                                   (20, 1_000_000_000, 900)])
def test_query_deadline_scales_only_above_the_tuned_size(seconds, rows, expected):
    assert settings.query_deadline(seconds, rows) == pytest.approx(expected)


def test_engine_uses_the_scaled_deadline(monkeypatch, tmp_path):
    source = tmp_path / 'facts.csv'; source.write_text('x\n1\n')
    workspace = Workspace.create(tmp_path / 'w'); workspace.add_file(source, 'facts')
    engine = Engine(workspace, 'facts'); engine.info['rows'] = 50_000_000
    seen = []
    real = __import__('threading').Timer
    monkeypatch.setattr('insightpilot.universal.engine.threading.Timer',
                        lambda delay, action: seen.append(delay) or real(delay, action))
    engine.count()
    assert seen == [200]
