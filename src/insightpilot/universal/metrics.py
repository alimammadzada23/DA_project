"""Explicit aggregate metrics using a small safe expression language, never eval."""
import ast
from dataclasses import asdict, dataclass
import json
import math

from ..datasets import quote
from .profile import identifier_name, numeric_expression


@dataclass(frozen=True)
class Metric:
    name: str
    formula: str = 'count()'
    description: str = 'Number of source rows; row grain must be reviewed.'
    units: str = 'records'
    limitations: str = 'Missing inputs are excluded; observed rows do not establish population incidence.'

    def compile(self, columns, roles=None):
        roles = roles or {}
        required = set()
        aggregates = []
        if len(self.formula)>2000: raise ValueError('Metric expression is too complex.')
        try: tree = ast.parse(self.formula, mode='eval')
        except (SyntaxError, ValueError) as exc: raise ValueError('Invalid metric expression.') from exc
        if len(self.formula) > 2000 or sum(1 for _ in ast.walk(tree)) > 100:
            raise ValueError('Metric expression is too complex.')

        def visit(node):
            if isinstance(node, ast.Expression): return visit(node.body)
            if isinstance(node, ast.Constant) and isinstance(node.value, (int,float)) and not isinstance(node.value,bool):
                if not math.isfinite(node.value): raise ValueError('Metric constants must be finite.')
                return str(node.value)
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub,ast.UAdd)):
                return ('-' if isinstance(node.op,ast.USub) else '+') + '(' + visit(node.operand) + ')'
            if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add,ast.Sub,ast.Mult,ast.Div)):
                left, right = visit(node.left), visit(node.right)
                if isinstance(node.op,ast.Div): return f'({left} / NULLIF({right},0))'
                op = {ast.Add:'+',ast.Sub:'-',ast.Mult:'*'}[type(node.op)]
                return f'({left} {op} {right})'
            if isinstance(node, ast.Call) and isinstance(node.func,ast.Name) and not node.keywords:
                func = node.func.id
                if func=='count_if':
                    if len(node.args)!=3 or not all(isinstance(a,ast.Constant) for a in node.args):
                        raise ValueError('Use count_if("column", ">", 15) with literal arguments.')
                    name,op,value=[a.value for a in node.args]
                    if not isinstance(name,str) or name not in columns or op not in ('==','!=','>','>=','<','<='):
                        raise ValueError('Invalid conditional-count column or comparison.')
                    required.add(name); aggregates.append(func)
                    if isinstance(value,str) and op in ('==','!='):
                        right="'"+value.replace("'","''")+"'"; left=f'CAST({quote(name)} AS VARCHAR)'
                    elif type(value) in (int,float) and math.isfinite(value):
                        right=str(value); left=numeric_expression(name)
                    else: raise ValueError('Conditional counts require finite numbers or exact string equality.')
                    return f'count(*) FILTER (WHERE {left} {"=" if op=="==" else "<>" if op=="!=" else op} {right})'
                if func not in ('sum','avg','median','min','max','count','distinct','valid'):
                    raise ValueError('Allowed aggregates: sum, avg, median, min, max, count, distinct, valid, count_if.')
                aggregates.append(func)
                if func == 'count' and not node.args: return 'count(*)'
                if len(node.args) != 1 or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value,str):
                    raise ValueError('Aggregate arguments must be literal column names, e.g. sum("amount").')
                name = node.args[0].value
                if name not in columns: raise ValueError(f'Metric requires unavailable column: {name}')
                required.add(name)
                if func=='valid': return f'count({numeric_expression(name)})'
                if func in ('count','distinct'):
                    expr = f"NULLIF(trim(CAST({quote(name)} AS VARCHAR)),'')"
                    return f'count(DISTINCT {expr})' if func == 'distinct' else f'count({expr})'
                role = roles.get(name)
                if role in ('identifier','customer','product','transaction','date','text','geography','company','status','category') or (
                        identifier_name(name) and role not in ('measure','quantity','duration','price','revenue','expense')):
                    raise ValueError('Identifiers and dimensions cannot be summed or averaged; approve an appropriate numeric role first.')
                return f'{func}({numeric_expression(name)})'
            raise ValueError('Use aggregates, numeric constants and + - * / only. SQL and Python execution are not supported.')

        expression = visit(tree)
        if not aggregates: raise ValueError('A metric needs at least one aggregate.')
        return expression, sorted(required)

    @property
    def additive(self):
        def constant(node):
            if isinstance(node,ast.Constant) and type(node.value) in (int,float): return node.value
            if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.USub,ast.UAdd)):
                value=constant(node.operand)
                return None if value is None else -value if isinstance(node.op,ast.USub) else value
            return None
        def linear(node):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name): return node.func.id in ('sum','count','valid','count_if')
            if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.USub,ast.UAdd)): return linear(node.operand)
            if isinstance(node,ast.BinOp):
                if isinstance(node.op,(ast.Add,ast.Sub)): return linear(node.left) and linear(node.right)
                if isinstance(node.op,ast.Mult): return (linear(node.left) and constant(node.right) is not None) or (constant(node.left) is not None and linear(node.right))
                if isinstance(node.op,ast.Div): return linear(node.left) and constant(node.right) not in (None,0)
            return False
        try:
            node = ast.parse(self.formula,mode='eval').body
            return linear(node)
        except SyntaxError: return False


def save_metric(workspace, table, metric):
    info = workspace.info(table)
    metric.compile(info['columns'],info['roles'])
    if not metric.name.strip() or not metric.units.strip() or not metric.description.strip():
        raise ValueError('Metric name, units and description are required.')
    definition = asdict(metric) | {'table':table}
    with workspace.connect(write=True) as con:
        con.execute('INSERT OR REPLACE INTO _metrics VALUES (?,?)', [table+':'+metric.name,json.dumps(definition)])


def load_metrics(workspace, table):
    with workspace.connect() as con:
        definitions = [json.loads(r[0]) for r in con.execute('SELECT definition FROM _metrics ORDER BY name').fetchall()]
    return [Metric(**{k:v for k,v in d.items() if k!='table'}) for d in definitions if d['table']==table]
