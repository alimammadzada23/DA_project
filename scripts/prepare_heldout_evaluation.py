"""Reproduce the already-selected held-out evaluation input; no UI presets.

The original held-out selection time and preselection engine fingerprint are in
tracked receipts. Repeating this source now is a regression check, not a new blind
holdout. Column references below belong solely to the independent test oracle.
"""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import uuid

import pandas as pd
from insightpilot.universal import Workspace

ROOT=Path(__file__).resolve().parents[1]


def main():
    receipt=json.loads((ROOT/'docs/unified-heldout-source.json').read_text())
    source=ROOT/'data/unified-evaluation/sources'/receipt['filename']
    if hashlib.sha256(source.read_bytes()).hexdigest()!=receipt['sha256']:raise ValueError('Held-out source checksum mismatch; existing input preserved.')
    root=ROOT/'outputs/unified-heldout'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:5]);root.mkdir(parents=True)
    raw=pd.read_csv(source);target=root/'unseen-rows.jsonl';raw.to_json(target,orient='records',lines=True)
    assert len(pd.read_json(target,lines=True))==receipt['rows']
    w=Workspace.create(root/'workspace')
    counts={'cases':int(raw.ncases.sum()),'controls':int(raw.ncontrols.sum()),'denominator':int(raw.ncases.sum()+raw.ncontrols.sum())}
    plan={'workspace':str(w.directory),'upload':str(target),'table':'unseen_rows','source_receipt':receipt,'original_csv':str(source.relative_to(ROOT)),
          'derived_format':'JSONL, all original rows and fields retained; no semantic or chart presets.',
          'independent_counts':counts,'expected':counts['cases']/counts['denominator']}
    (ROOT/'outputs/heldout-evaluation-plan.json').write_text(json.dumps(plan,indent=2)+'\n')
    print('Fresh empty held-out test workspace:',w.directory)

if __name__=='__main__':main()
