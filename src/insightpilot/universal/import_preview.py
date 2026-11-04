"""Bounded delimiter feedback; never silently reinterpret or rewrite source data."""
import codecs
import csv
import io
from itertools import islice

SEPARATORS=(',', ';', '\t', '|')


def parsing_preview(data,selected=','):
    if selected not in SEPARATORS:raise ValueError('Choose a supported delimiter.')
    sample=codecs.getincrementaldecoder('utf-8-sig')().decode(bytes(data[:65536]),final=False)
    def rows_for(separator):
        return list(islice(csv.reader(io.StringIO(sample),delimiter=separator),51))
    rows=rows_for(selected)
    if not rows:return {'delimiter':selected,'encoding':'UTF-8','columns':[],'rows':[],'suggestions':[],'warning':None,'field_count':0}
    width=len(rows[0]);suggestions=[]
    if width==1:
        for sep in SEPARATORS:
            if sep==selected:continue
            candidate=rows_for(sep)
            if len(candidate)<3 or len(candidate[0])<2:continue
            lengths=[len(r) for r in candidate[1:] if r]
            if lengths and sum(n==len(candidate[0]) for n in lengths)/len(lengths)>=.95:
                suggestions.append({'delimiter':sep,'columns':len(candidate[0]),'sample_rows':len(lengths)})
    warning=None
    if suggestions:
        alternatives=', '.join(('tab' if s['delimiter']=='\t' else repr(s['delimiter']))+f' ({s["columns"]} fields)' for s in suggestions)
        warning='The selected separator produces one field. Another separator looks more plausible: '+alternatives+'. Choose a separator and review the preview before importing. A legitimate one-column file can still be imported as selected.'
    return {'delimiter':selected,'encoding':'UTF-8','columns':rows[0],'rows':[r for r in rows[1:6] if len(r)==width],
            'suggestions':suggestions,'warning':warning,'field_count':width}
