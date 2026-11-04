"""Small aggregate expression language. Column literals are data, never SQL."""
import ast
from dataclasses import asdict, dataclass
import json
import math

from ..datasets import quote
from .profile import identifier_name, numeric_expression

NUMERIC_ROLES={'measure','quantity','duration','price','revenue','expense'}

@dataclass(frozen=True)
class Metric:
    name: str
    formula: str = 'count()'
    description: str = 'Number of source rows; row grain must be reviewed.'
    units: str = 'records'
    limitations: str = 'Missing inputs are excluded; observed rows do not establish population incidence.'
    denominator: str = ''
    currency: str = ''

    def compile(self, columns, roles=None, *, allow_scalar=False):
        roles=roles or {}; required=set(); aggregates=[]
        if len(self.formula)>2000: raise ValueError('Metric expression is too complex.')
        try: tree=ast.parse(self.formula,mode='eval')
        except (SyntaxError,ValueError) as exc: raise ValueError('Invalid metric expression.') from exc
        if sum(1 for _ in ast.walk(tree))>100: raise ValueError('Metric expression is too complex.')

        def literal(node):
            if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.USub,ast.UAdd)) and isinstance(node.operand,ast.Constant) and type(node.operand.value) in (int,float):
                return (-1 if isinstance(node.op,ast.USub) else 1)*node.operand.value
            if not isinstance(node,ast.Constant): raise ValueError('Use literal column names and condition values.')
            return node.value

        def column(node, numeric=False):
            name=literal(node)
            if not isinstance(name,str) or name not in columns: raise ValueError(f'Metric requires unavailable column: {name}')
            required.add(name)
            role=roles.get(name)
            if numeric and (role in {'identifier','customer','product','transaction','date','text','geography','company','status','category'} or
                            identifier_name(name) and role not in NUMERIC_ROLES):
                raise ValueError('Identifiers and dimensions cannot be summed or averaged; approve an appropriate numeric role first.')
            return numeric_expression(name) if numeric else quote(name)

        def number(value):
            if type(value) not in (int,float) or not math.isfinite(value): raise ValueError('Metric constants must be finite.')
            return str(value)

        def arithmetic(node, visitor):
            left,right=visitor(node.left),visitor(node.right)
            if isinstance(node.op,ast.Div): return f'({left} / NULLIF({right},0))'
            if type(node.op) not in (ast.Add,ast.Sub,ast.Mult): raise ValueError('Only + - * / arithmetic is supported.')
            return f'({left} '+{ast.Add:'+',ast.Sub:'-',ast.Mult:'*'}[type(node.op)]+f' {right})'

        def row(node):
            if isinstance(node,ast.Constant):
                return column(node,True) if isinstance(node.value,str) else number(node.value)
            if isinstance(node,ast.BinOp): return arithmetic(node,row)
            if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.USub,ast.UAdd)):
                return ('-' if isinstance(node.op,ast.USub) else '+')+'('+row(node.operand)+')'
            raise ValueError('Derived row inputs accept column literals, constants and arithmetic only.')

        def condition(args):
            if len(args)!=3: raise ValueError('Use condition column, comparison and literal value.')
            name,op,value=map(literal,args)
            if not isinstance(name,str) or name not in columns or op not in ('==','!=','>','>=','<','<='):
                raise ValueError('Invalid conditional-count column or comparison.')
            required.add(name)
            if isinstance(value,str) and op in ('==','!='):
                left=f'CAST({quote(name)} AS VARCHAR)'; right="'"+value.replace("'","''")+"'"
            elif type(value) in (int,float): left=numeric_expression(name); right=number(value)
            else: raise ValueError('Conditional counts require finite numbers or exact string equality.')
            return f'{left} '+({'==':'=','!=':'<>'}.get(op,op))+f' {right}'

        def visit(node):
            if isinstance(node,ast.Expression): return visit(node.body)
            if isinstance(node,ast.Constant): return number(node.value)
            if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.USub,ast.UAdd)):
                return ('-' if isinstance(node.op,ast.USub) else '+')+'('+visit(node.operand)+')'
            if isinstance(node,ast.BinOp): return arithmetic(node,visit)
            if not isinstance(node,ast.Call) or not isinstance(node.func,ast.Name) or node.keywords:
                raise ValueError('Use aggregates and arithmetic only. SQL and Python execution are not supported.')
            func=node.func.id; args=node.args; aggregates.append(func)
            if func=='count_if': return f'count(*) FILTER (WHERE {condition(args)})'
            if func=='sum_if':
                if len(args)!=4: raise ValueError('Use sum_if("value", "condition", "==", "literal").')
                return f'sum({row(args[0])}) FILTER (WHERE {condition(args[1:])})'
            if func=='weighted_avg':
                if len(args)!=2: raise ValueError('Use weighted_avg("value", "weight").')
                x,w=map(row,args)
                return f'CASE WHEN min({w})<0 THEN NULL ELSE sum(({x})*({w})) / NULLIF(sum(CASE WHEN ({x}) IS NOT NULL AND ({w}) IS NOT NULL THEN ({w}) END),0) END'
            if func=='quantile':
                if len(args)!=2: raise ValueError('Use quantile("value", 0.9).')
                p=literal(args[1]); number(p)
                if not 0<=p<=1: raise ValueError('Quantile probability must be between 0 and 1.')
                return f'quantile_cont({row(args[0])},{p})'
            if func not in ('sum','avg','median','min','max','count','distinct','valid'):
                raise ValueError('Allowed aggregates: sum, avg, median, quantile, min, max, count, distinct, valid, count_if, sum_if, weighted_avg.')
            if func=='count' and not args: return 'count(*)'
            if len(args)!=1: raise ValueError('Aggregate needs one column or derived row expression.')
            if func in ('count','distinct'):
                expr=f"NULLIF(trim(CAST({column(args[0])} AS VARCHAR)),'')"
                return f'count(DISTINCT {expr})' if func=='distinct' else f'count({expr})'
            if func=='valid': return f'count({row(args[0])})'
            return f'{func}({row(args[0])})'

        expression=visit(tree)
        if not aggregates and not allow_scalar: raise ValueError('A metric needs at least one aggregate.')
        return expression,sorted(required)

    @property
    def additive(self):
        def const(n):
            if isinstance(n,ast.Constant) and type(n.value) in (int,float): return n.value
            if isinstance(n,ast.UnaryOp) and isinstance(n.op,(ast.USub,ast.UAdd)):
                v=const(n.operand); return None if v is None else -v if isinstance(n.op,ast.USub) else v
            return None
        def linear(n):
            if isinstance(n,ast.Call) and isinstance(n.func,ast.Name): return n.func.id in ('sum','sum_if','count','valid','count_if')
            if isinstance(n,ast.UnaryOp): return linear(n.operand)
            if isinstance(n,ast.BinOp):
                if isinstance(n.op,(ast.Add,ast.Sub)): return linear(n.left) and linear(n.right)
                if isinstance(n.op,ast.Mult): return linear(n.left) and const(n.right) is not None or const(n.left) is not None and linear(n.right)
                if isinstance(n.op,ast.Div): return linear(n.left) and const(n.right) not in (None,0)
            return False
        try: return bool(linear(ast.parse(self.formula,mode='eval').body))
        except SyntaxError: return False

    @property
    def components(self):
        """Expose exact ratio components rather than averaging precomputed rates."""
        try: node=ast.parse(self.formula,mode='eval').body
        except SyntaxError: return {}
        if isinstance(node,ast.BinOp) and isinstance(node.op,ast.Div):
            return {'numerator':ast.unparse(node.left),'denominator':ast.unparse(node.right)}
        return {}

    def dictionary(self, info):
        sql,required=self.compile(info['columns'],info['roles'])
        return asdict(self)|{'dependencies':required,'compiled_sql':sql,'components':self.components,
            'aggregation':'additive' if self.additive else 'non-additive; recompute from eligible rows',
            'validation':'reviewed definition' if info.get('semantics',{}).get('reviewed') else 'formula validated; business definitions need review',
            'version':info['current']}


def save_metric(workspace,table,metric):
    info=workspace.info(table); metric.compile(info['columns'],info['roles'])
    if not metric.name.strip() or not metric.units.strip() or not metric.description.strip():
        raise ValueError('Metric name, units and description are required.')
    if metric.currency and (len(metric.currency)!=3 or not metric.currency.isalpha()):
        raise ValueError('Currency needs a three-letter code, or leave it empty.')
    # Reject obvious incompatible addition/subtraction when reviewed input units exist.
    units=info.get('semantics',{}).get('units',{})
    def dimension(n):
        if isinstance(n,ast.Constant) and isinstance(n.value,str): return units.get(n.value)
        if isinstance(n,ast.Call) and n.args and getattr(n.func,'id','') in ('sum','avg','median','quantile','min','max','sum_if'):
            return dimension(n.args[0])
        if isinstance(n,ast.BinOp):
            l,r=dimension(n.left),dimension(n.right)
            if isinstance(n.op,(ast.Add,ast.Sub)) and l and r and l!=r:
                raise ValueError(f'Incompatible units in addition/subtraction: {l} and {r}. Define a conversion explicitly.')
            return l if isinstance(n.op,(ast.Add,ast.Sub)) and l==r else None
    dimension(ast.parse(metric.formula,mode='eval').body)
    definition=asdict(metric)|{'table':table}
    with workspace.connect(write=True) as con:
        con.execute('INSERT OR REPLACE INTO _metrics VALUES (?,?)',[table+':'+metric.name,json.dumps(definition)])


def load_metrics(workspace,table):
    with workspace.connect() as con:
        definitions=[json.loads(r[0]) for r in con.execute('SELECT definition FROM _metrics ORDER BY name').fetchall()]
    return [Metric(**{k:v for k,v in d.items() if k!='table'}) for d in definitions if d['table']==table]
