"""Validate generated JSON against retained official schemas, without network access."""
import json
from pathlib import Path
from jsonschema import validators
from referencing import Registry,Resource
from referencing.jsonschema import DRAFT7


def validate():
    root=Path(__file__).parent
    base='https://developer.microsoft.com/json-schemas/'
    resources=[]
    for path in (root/'schemas').rglob('*.json'):
        uri=base+path.relative_to(root/'schemas').as_posix()
        resources.append((uri,Resource.from_contents(json.loads(path.read_text()),default_specification=DRAFT7)))
    registry=Registry().with_resources(resources)
    checked=[]
    for path in root.rglob('*'):
        if path.suffix not in ('.json','.pbip','.pbir','.pbism') or 'schemas' in path.parts: continue
        obj=json.loads(path.read_text())
        if '$schema' not in obj: continue
        uri=obj['$schema']; schema=registry.contents(uri)
        # Establish base URI for relative references even when official schema omits $id.
        validator=validators.validator_for(schema)({'$ref':uri},registry=registry)
        validator.validate(obj)
        checked.append(str(path.relative_to(root)))
    return checked


if __name__=='__main__':
    result=validate()
    print(json.dumps({'json_files_validated':len(result),'files':result,
                      'not_validated':['TMDL parser','DAX execution','Desktop rendering','data refresh']},indent=2))
