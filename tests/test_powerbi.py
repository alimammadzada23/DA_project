import importlib.util
from pathlib import Path


def test_official_schemas():
    path=Path(__file__).parents[1]/'powerbi/validate.py'
    spec=importlib.util.spec_from_file_location('validate_powerbi',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    files=module.validate()
    assert len([f for f in files if f.endswith('/page.json')])==4
    assert len([f for f in files if f.endswith('/visual.json')])==7
