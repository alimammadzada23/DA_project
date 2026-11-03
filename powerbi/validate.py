"""Validate generated JSON against retained official schemas, without network access."""
import json
import re
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
    for required in ['InsightPilot.pbip','InsightPilot.Report/definition.pbir',
                     'InsightPilot.Report/definition/version.json','InsightPilot.SemanticModel/definition/model.tmdl']:
        if not (root/required).is_file():raise ValueError(f'Missing required project component: {required}')
    bindings={}
    for path in (root/'InsightPilot.SemanticModel/definition/tables').glob('*.tmdl'):
        text=path.read_text()
        table=re.search(r'^table (.+)$',text,re.MULTILINE).group(1).strip("'")
        bindings[table]={'Column':set(),'Measure':set()}
        for kind,quoted,unquoted in re.findall(r"^\t(column|measure) (?:'([^']+)'|([^\s=]+))",text,re.MULTILINE):
            bindings[table]['Column' if kind=='column' else 'Measure'].add(quoted or unquoted)
    checked=[]
    for path in root.rglob('*'):
        if path.suffix not in ('.json','.pbip','.pbir','.pbism') or 'schemas' in path.parts: continue
        obj=json.loads(path.read_text())
        if '$schema' not in obj: continue
        uri=obj['$schema']; schema=registry.contents(uri)
        # Establish base URI for relative references even when official schema omits $id.
        validator=validators.validator_for(schema)({'$ref':uri},registry=registry)
        validator.validate(obj)
        if path.name=='visual.json':
            for role in obj.get('visual',{}).get('query',{}).get('queryState',{}).values():
                for projection in role.get('projections',[]):
                    for kind,field in projection['field'].items():
                        if kind not in ('Column','Measure'):continue
                        table=field['Expression']['SourceRef']['Entity']
                        if field['Property'] not in bindings.get(table,{}).get(kind,set()):
                            raise ValueError(f'Unresolved semantic field in {path}: {table}.{field["Property"]}')
        checked.append(str(path.relative_to(root)))
    return checked


if __name__=='__main__':
    result=validate()
    print(json.dumps({'json_files_validated':len(result),'files':result,
                      'not_validated':['TMDL parser','DAX execution','Desktop rendering','data refresh']},indent=2))
