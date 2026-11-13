"""Optional local-AI summary of the findings digest (off by default).

The model receives only the findings JSON (id, type, severity, statement). Every sentence it
returns must cite at least one finding ID in brackets, every number in the sentence must appear
in a cited finding's statement (at the precision written), and causal wording is rejected. Any
sentence failing a check is dropped and counted; the deterministic report never depends on it.
"""
import json
import re

from ..findings import numbers_in

MAX_BYTES = 16_000
CITATION = re.compile(r'\[(F\d{2,3})\]')
SENTENCE = re.compile(r'(?<=[.!?])\s+(?=[A-Z\[])')
SYSTEM = ('You summarise a findings digest that a statistics engine has already computed. Write JSON matching the schema.\n'
          'Rules:\n'
          '- Every sentence must end with the IDs of the findings it uses, in square brackets, for example [F01] or [F02][F05].\n'
          '- Use only numbers that appear in the cited findings, written exactly as there. Do not compute new numbers.\n'
          '- Describe associations only. Never claim causes or reasons (no because, due to, caused, led to, drove, driven by, resulted in).\n'
          '- headline: one sentence. findings: up to 4 sentences, each about different findings. caveats: up to 3 sentences that '
          'restate limitations from the digest. follow_ups: leave empty.\n'
          'Return JSON only.')


def payload(output):
    rows = [{'id': f['id'], 'type': f['type'], 'severity': f['severity'], 'statement': f['statement']} for f in output.get('findings', [])]
    while rows and len(json.dumps(rows, ensure_ascii=False).encode()) > MAX_BYTES: rows.pop()
    return rows


def messages(output):
    return [{'role': 'system', 'content': SYSTEM},
            {'role': 'user', 'content': json.dumps({'target': output.get('target'), 'findings': payload(output)}, ensure_ascii=False)}]


def _decimals(token):
    return len(token.split('.')[1]) if '.' in token else 0


def number_ok(token, allowed):
    value = float(token.replace(',', ''))
    places = _decimals(token)
    return any(round(abs(a), places) == abs(value) for a in allowed)


def check(sentence, findings):
    """(accepted, reason) for one model sentence."""
    from ...analyst_interpret import CAUSAL
    ids = CITATION.findall(sentence)
    if not ids: return False, 'no finding ID cited'
    unknown = [i for i in ids if i not in findings]
    if unknown: return False, 'cites unknown finding ' + ', '.join(unknown)
    if CAUSAL.search(sentence): return False, 'causal wording'
    allowed = [n for i in ids for n in numbers_in(findings[i]['statement'])]
    body = CITATION.sub('', sentence)
    for token in re.findall(r'(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?', body):
        if not number_ok(token.lstrip('+-'), allowed): return False, f'number {token} is not in the cited findings'
    return True, ''


def validate(value, output):
    findings = {f['id']: f for f in output.get('findings', [])}
    sentences = []
    for part in ['headline', 'findings', 'caveats']:
        texts = value.get(part) or []
        for text in ([texts] if isinstance(texts, str) else texts):
            sentences += [(part, s.strip()) for s in SENTENCE.split(str(text)) if s.strip()]
    accepted, rejected = [], []
    for part, sentence in sentences:
        ok, reason = check(sentence, findings)
        (accepted if ok else rejected).append({'part': part, 'text': sentence, **({} if ok else {'reason': reason})})
    return {'sentences': accepted, 'rejected': rejected}


def summarise(output, client):
    """Ask the local model (an OllamaClient) for a summary; returns the validated section, or raises LocalAIError."""
    value, meta = client.interpret(messages(output))
    section = validate(value if isinstance(value, dict) else {}, output)
    section['model'] = meta.get('model')
    return section
