"""A safe, practice-grade DAX engine over the existing DuckDB workspace.

Users write real DAX measures and calculated columns; InsightPilot parses them
into a small syntax tree, accepts only whitelisted functions and existing
columns, and compiles each measure into DuckDB SQL that reproduces DAX filter
context. Filter context is a set of column conditions; every aggregation is a
correlated subquery over the selected rows that satisfy the current context,
so CALCULATE, ALL, REMOVEFILTERS, KEEPFILTERS, FILTER, RANKX and time
intelligence behave like their Power BI counterparts on a single fact table
plus an automatic Date table built from the reviewed date basis.

No Python eval, no user SQL: identifiers are quoted, literals are escaped and
every generated statement runs through Engine.run with its timeout, shared
filters and immutable source version.
"""
from dataclasses import dataclass, field, replace
import math
import re

from ..datasets import quote
from .profile import date_expression, numeric_expression

D='\x00D\x00'           # placeholder for the (possibly shifted) date-basis expression
DF='\x00F\x00'          # frozen date-basis expression: visible-range bounds, never shifted again
MAX_LENGTH=4000
MAX_NODES=400
MAX_DEPTH=14
MAX_GROUPS=500
DATE_TABLE='Date'
DATE_COLUMNS={
    'Date':f'{D}',
    'Year':f'CAST(year({D}) AS INTEGER)',
    'Quarter':f"CAST(year({D}) AS VARCHAR)||'-Q'||CAST(quarter({D}) AS VARCHAR)",
    'Month':f"strftime({D},'%Y-%m')",
    'Month Number':f'CAST(month({D}) AS INTEGER)',
    'Weekday':f"CAST(isodow({D}) AS VARCHAR)||' '||strftime({D},'%a')",
    'Day':f'CAST(day({D}) AS INTEGER)',
}
UNITS={'DAY':'DAY','MONTH':'MONTH','QUARTER':'QUARTER','YEAR':'YEAR'}
AGGREGATES={'SUM':'sum','AVERAGE':'avg','MIN':'min','MAX':'max','MEDIAN':'median'}
ITERATORS={'SUMX':'sum','AVERAGEX':'avg','MINX':'min','MAXX':'max'}
FUNCTIONS=sorted(set(AGGREGATES)|set(ITERATORS)|{
    'COUNT','COUNTA','COUNTBLANK','COUNTROWS','DISTINCTCOUNT','DISTINCTCOUNTNOBLANK','CALCULATE','FILTER','ALL',
    'REMOVEFILTERS','ALLEXCEPT','KEEPFILTERS','DIVIDE','IF','SWITCH','COALESCE','ABS','ROUND','BLANK','TRUE','FALSE','NOT',
    'AND','OR','ISBLANK','HASONEVALUE','SELECTEDVALUE','RANKX','TOTALYTD','TOTALQTD','TOTALMTD','DATESYTD','DATESQTD',
    'DATESMTD','SAMEPERIODLASTYEAR','DATEADD','PREVIOUSMONTH','PREVIOUSQUARTER','PREVIOUSYEAR','PREVIOUSDAY',
    'YEAR','MONTH','DAY','QUARTER','UPPER','LOWER','LEN','CONCATENATE','VALUE','INT'})


class DAXError(ValueError):
    """Invalid or unsupported DAX; nothing was executed."""


# ---------------------------------------------------------------- tokenizer
TOKEN=re.compile(r'''
   (?P<ws>\s+|//[^\n]*|--[^\n]*|/\*.*?\*/)
  |(?P<number>\d+(?:\.\d+)?(?:[eE][-+]?\d+)?|\.\d+)
  |(?P<string>"(?:[^"]|"")*")
  |(?P<quoted>'(?:[^']|'')+')
  |(?P<bracket>\[[^\[\]]+\])
  |(?P<ident>[A-Za-z_][A-Za-z0-9_.]*)
  |(?P<op>&&|\|\||<>|<=|>=|==|[-+*/&=<>(),{}])
''',re.X|re.S)


def tokenize(text):
    if not isinstance(text,str) or not text.strip():raise DAXError('Write a DAX expression.')
    if len(text)>MAX_LENGTH:raise DAXError(f'DAX expressions are limited to {MAX_LENGTH:,} characters.')
    if '\x00' in text:raise DAXError('The expression contains a hidden control character.')
    tokens=[];position=0
    while position<len(text):
        match=TOKEN.match(text,position)
        if not match:raise DAXError(f'Unexpected character {text[position]!r} at position {position+1}.')
        position=match.end();kind=match.lastgroup;value=match.group()
        if kind=='ws':continue
        if kind=='string':value=value[1:-1].replace('""','"')
        if kind=='quoted':value=value[1:-1].replace("''","'")
        if kind=='bracket':value=value[1:-1].strip()
        tokens.append((kind,value))
    tokens.append(('end',''))
    return tokens


# ------------------------------------------------------------------- parser
class Parser:
    """Recursive descent over DAX expression syntax (no VAR/RETURN, no table constructors beyond {..})."""
    def __init__(self,text):
        self.tokens=tokenize(text);self.i=0;self.nodes=0

    def peek(self,offset=0):return self.tokens[min(self.i+offset,len(self.tokens)-1)]
    def take(self):token=self.tokens[self.i];self.i+=1;return token
    def accept(self,value):
        if self.peek()[0] in ('op','ident') and self.peek()[1].upper()==value.upper():self.i+=1;return True
        return False
    def expect(self,value):
        if not self.accept(value):raise DAXError(f'Expected “{value}” but found “{self.peek()[1] or "end of expression"}”.')

    def node(self,*value):
        self.nodes+=1
        if self.nodes>MAX_NODES:raise DAXError('The expression is too complex for this workbench.')
        return value

    def parse(self):
        tree=self.logical()
        if self.peek()[0]!='end':raise DAXError(f'Unexpected “{self.peek()[1]}”. Check commas and parentheses.')
        return tree

    def logical(self):
        left=self.comparison()
        while self.peek()[1] in ('&&','||'):
            op=self.take()[1];left=self.node('bin',op,left,self.comparison())
        return left

    def comparison(self):
        left=self.concat()
        if self.peek()[0]=='op' and self.peek()[1] in ('=','==','<>','<','<=','>','>='):
            op=self.take()[1];op='=' if op=='==' else op
            return self.node('bin',op,left,self.concat())
        if self.peek()[0]=='ident' and self.peek()[1].upper()=='IN':
            self.take();self.expect('{');values=[]
            while True:
                values.append(self.additive())
                if self.accept('}'):break
                self.expect(',')
            return self.node('in',left,values)
        return left

    def concat(self):
        left=self.additive()
        while self.peek()==('op','&'):self.take();left=self.node('bin','&',left,self.additive())
        return left

    def additive(self):
        left=self.multiplicative()
        while self.peek()[0]=='op' and self.peek()[1] in ('+','-'):
            op=self.take()[1];left=self.node('bin',op,left,self.multiplicative())
        return left

    def multiplicative(self):
        left=self.unary()
        while self.peek()[0]=='op' and self.peek()[1] in ('*','/'):
            op=self.take()[1];left=self.node('bin',op,left,self.unary())
        return left

    def unary(self):
        if self.accept('-'):return self.node('neg',self.unary())
        if self.accept('+'):return self.unary()
        if self.peek()[0]=='ident' and self.peek()[1].upper()=='NOT' and self.peek(1)!=('op','('):
            self.take();return self.node('not',self.unary())
        return self.primary()

    def primary(self):
        kind,value=self.take()
        if kind=='number':
            number=float(value)
            if not math.isfinite(number):raise DAXError('Numbers must be finite.')
            return self.node('num',number)
        if kind=='string':return self.node('str',value)
        if kind=='op' and value=='(':
            inner=self.logical();self.expect(')');return inner
        if kind=='bracket':return self.node('measure',value)
        if kind=='quoted':
            if self.peek()[0]=='bracket':return self.node('col',value,self.take()[1])
            return self.node('table',value)
        if kind=='ident':
            if self.peek()==('op','('):
                name=value.upper();self.take();args=[]
                if name not in FUNCTIONS:raise DAXError(f'{value} is not supported here. Supported functions: '+', '.join(FUNCTIONS)+'.')
                if not self.accept(')'):
                    while True:
                        args.append(self.logical())
                        if self.accept(')'):break
                        self.expect(',')
                return self.node('call',name,args)
            if self.peek()[0]=='bracket':return self.node('col',value,self.take()[1])
            if value.upper() in UNITS:return self.node('unit',value.upper())
            if value.upper() in ('TRUE','FALSE'):return self.node('bool',value.upper()=='TRUE')
            return self.node('table',value)
        raise DAXError(f'Unexpected “{value or "end of expression"}”.')


def parse(text):return Parser(text).parse()


# ------------------------------------------------------------------- model
def _key(name):return name.casefold()


@dataclass
class Model:
    """The semantic model the expressions resolve against: one fact table, its calculated
    columns, saved measures and (when a date basis is reviewed) an automatic Date table."""
    table: str
    columns: list
    names: tuple
    date_basis: str=None
    measures: dict=field(default_factory=dict)       # name -> DAX text
    calculated: dict=field(default_factory=dict)     # name -> DAX row expression

    @classmethod
    def from_engine(cls,engine,measures=(),calculated=()):
        names={engine.table}
        info=engine.info;display=info.get('display_name') or info.get('source',{}).get('filename','')
        if display:names.add(display);names.add(display.rsplit('.',1)[0])
        model=cls(engine.table,list(engine.info['columns']),tuple(sorted(names)),engine.context.date_basis)
        for item in calculated:model.add_column(item['name'],item['expression'])
        for item in measures:model.add_measure(item['name'],item['expression'])
        return model

    def is_fact(self,table):return _key(table) in {_key(n) for n in self.names}

    def _check_name(self,name,kind):
        if not isinstance(name,str) or not re.fullmatch(r'[^\[\]\x00-\x1f]{1,80}',name.strip()):
            raise DAXError(f'Name the {kind} with 1–80 characters and no square brackets.')
        return name.strip()

    def add_measure(self,name,expression):
        name=self._check_name(name,'measure')
        if _key(name) in {_key(c) for c in [*self.columns,*self.calculated]}:raise DAXError(f'“{name}” is already a column name.')
        parse(expression);self.measures[name]=expression;return name

    def add_column(self,name,expression):
        name=self._check_name(name,'calculated column')
        if _key(name) in {_key(c) for c in self.columns}:raise DAXError(f'“{name}” already exists in the source table.')
        tree=parse(expression)
        self.calculated[name]=expression
        try:Compiler(self).row(tree,set([name]))
        except DAXError:del self.calculated[name];raise
        return name

    def column_sql(self,table,column,*,stack=frozenset()):
        """SQL for a column reference plus its filter-context key."""
        if _key(table)==_key(DATE_TABLE) and not self.is_fact(table):
            if not self.date_basis:raise DAXError('The Date table needs a reviewed date basis. Choose one in the sidebar.')
            match=next((c for c in DATE_COLUMNS if _key(c)==_key(column)),None)
            if match is None:raise DAXError(f"'Date'[{column}] does not exist. Date columns: "+', '.join(DATE_COLUMNS)+'.')
            return DATE_COLUMNS[match],'date['+_key(match)+']',True
        if not self.is_fact(table):raise DAXError(f"Unknown table “{table}”. Use '{self.table}'[column] or 'Date'[column].")
        match=next((c for c in self.columns if _key(c)==_key(column)),None)
        if match is not None:return quote(match),'fact['+_key(match)+']',False
        calc=next((c for c in self.calculated if _key(c)==_key(column)),None)
        if calc is None:raise DAXError(f'Column [{column}] does not exist in {self.table}.')
        if calc in stack:raise DAXError(f'Calculated column [{calc}] refers to itself.')
        sql=Compiler(self).row(parse(self.calculated[calc]),set(stack)|{calc})
        return '('+sql+')','fact['+_key(calc)+']',D in sql

    def date_sql(self):
        if not self.date_basis:raise DAXError('Time intelligence needs a reviewed date basis. Choose one in the sidebar.')
        return date_expression(self.date_basis)


# --------------------------------------------------------- filter context
@dataclass(frozen=True)
class Condition:
    key: str          # column identity used by ALL/REMOVEFILTERS/overrides
    sql: str          # may contain the D placeholder
    dated: bool       # depends on the date basis (shifted by time intelligence)


@dataclass(frozen=True)
class Context:
    conditions: tuple=()

    def without(self,predicate):return Context(tuple(c for c in self.conditions if not predicate(c)))
    def plus(self,*conditions):return Context(self.conditions+tuple(conditions))
    def where(self):return ' AND '.join('('+c.sql+')' for c in self.conditions) or 'TRUE'


def literal(node):
    kind=node[0]
    if kind=='num':return repr(float(node[1])) if not float(node[1]).is_integer() else str(int(node[1]))
    if kind=='str':return "'"+node[1].replace("'","''")+"'"
    if kind=='bool':return 'TRUE' if node[1] else 'FALSE'
    if kind=='neg' and node[1][0]=='num':return '-'+literal(node[1])
    raise DAXError('Filter values must be literal numbers, text or TRUE/FALSE.')


class Compiler:
    def __init__(self,model):self.model=model;self.depth=0

    # ---- row-level (calculated columns, iterator bodies, FILTER conditions)
    def row(self,node,stack=frozenset()):
        kind=node[0]
        if kind in ('num','str','bool'):return literal(node)
        if kind=='col':
            sql,_,_=self.model.column_sql(node[1],node[2],stack=frozenset(stack));return self.row_value(sql,node)
        if kind=='measure':raise DAXError(f'[{node[1]}] is a measure; row expressions use columns such as \'{self.model.table}\'[column].')
        if kind=='neg':return '(-('+self.row(node[1],stack)+'))'
        if kind=='not':return '(NOT ('+self.row(node[1],stack)+'))'
        if kind=='bin':
            op=node[1];left=self.row(node[2],stack);right=self.row(node[3],stack)
            if op=='&':return f"(COALESCE(CAST({left} AS VARCHAR),'')||COALESCE(CAST({right} AS VARCHAR),''))"
            if op in ('&&','||'):return f'({left} {"AND" if op=="&&" else "OR"} {right})'
            if op in ('+','-','*'):return f'({self.number(left)} {op} {self.number(right)})'
            if op=='/':return f'(CASE WHEN {self.number(right)}=0 THEN NULL ELSE {self.number(left)} / {self.number(right)} END)'
            # Comparisons: text against a text literal, numbers otherwise when a side is numeric.
            if 'str' in (node[2][0],node[3][0]):return f'({self.text(left)} {op} {self.text(right)})'
            if 'num' in (node[2][0],node[3][0]) or node[2][0]=='neg' or node[3][0]=='neg':return f'({self.number(left)} {op} {self.number(right)})'
            return f'({left} {op} {right})'
        if kind=='in':
            values=node[2]
            if values and all(v[0]=='str' for v in values):return '('+self.text(self.row(node[1],stack))+' IN ('+','.join(literal(v) for v in values)+'))'
            return '('+self.number(self.row(node[1],stack))+' IN ('+','.join(literal(v) for v in values)+'))'
        if kind=='call':
            name,args=node[1],node[2]
            def a(i):return self.row(args[i],stack)
            if name=='IF':
                self.arity(name,args,2,3);return f'(CASE WHEN {a(0)} THEN {a(1)} ELSE {a(2) if len(args)>2 else "NULL"} END)'
            if name=='DIVIDE':
                self.arity(name,args,2,3);return f'(CASE WHEN ({a(1)}) IS NULL OR ({a(1)})=0 THEN {a(2) if len(args)>2 else "NULL"} ELSE ({a(0)})/({a(1)}) END)'
            if name in ('ABS','INT','UPPER','LOWER','LEN','VALUE','ISBLANK','YEAR','MONTH','DAY','QUARTER'):
                self.arity(name,args,1,1)
                return {'ABS':f'abs({a(0)})','INT':f'floor({a(0)})','UPPER':f'upper(CAST({a(0)} AS VARCHAR))','LOWER':f'lower(CAST({a(0)} AS VARCHAR))',
                        'LEN':f'length(CAST({a(0)} AS VARCHAR))','VALUE':f'TRY_CAST({a(0)} AS DOUBLE)','ISBLANK':f'(({a(0)}) IS NULL)',
                        'YEAR':f'year(TRY_CAST({a(0)} AS DATE))','MONTH':f'month(TRY_CAST({a(0)} AS DATE))','DAY':f'day(TRY_CAST({a(0)} AS DATE))',
                        'QUARTER':f'quarter(TRY_CAST({a(0)} AS DATE))'}[name]
            if name=='ROUND':self.arity(name,args,2,2);return f'round({a(0)},CAST({a(1)} AS INTEGER))'
            if name=='COALESCE':self.arity(name,args,2,8);return 'COALESCE('+','.join(a(i) for i in range(len(args)))+')'
            if name=='CONCATENATE':self.arity(name,args,2,2);return f"(COALESCE(CAST({a(0)} AS VARCHAR),'')||COALESCE(CAST({a(1)} AS VARCHAR),''))"
            if name in ('AND','OR'):self.arity(name,args,2,2);return f'({a(0)} {name} {a(1)})'
            if name=='NOT':self.arity(name,args,1,1);return f'(NOT {a(0)})'
            if name in ('TRUE','FALSE'):return name
            if name=='BLANK':return 'NULL'
            raise DAXError(f'{name} is not available in row expressions (calculated columns, iterators and FILTER conditions).')
        raise DAXError('Unsupported row expression.')

    def row_value(self,sql,node):
        """Columns are compared as text but used numerically in arithmetic; keep raw value, cast at use."""
        return sql

    def arity(self,name,args,low,high):
        if not low<=len(args)<=high:
            raise DAXError(f'{name} takes {low} argument{"s" if low!=1 else ""}'+(f' to {high}' if high!=low else '')+f'; {len(args)} given.')

    def text(self,sql):
        return f"NULLIF(trim(CAST({sql} AS VARCHAR)),'')"

    def number(self,sql):
        """Blank-safe numeric view of a raw column/row value."""
        return f"TRY_CAST(NULLIF(trim(CAST({sql} AS VARCHAR)),'') AS DOUBLE)"

    def column_number(self,node):
        if node[0]!='col':raise DAXError('Aggregations take a column such as \''+self.model.table+'\'[amount].')
        sql,_,_=self.model.column_sql(node[1],node[2])
        if sql.startswith('"'):return numeric_expression(sql[1:-1].replace('""','"'))
        return self.number(sql)

    def column_text(self,node):
        if node[0]!='col':raise DAXError('Use a column reference here.')
        sql,_,_=self.model.column_sql(node[1],node[2])
        return f"NULLIF(trim(CAST({sql} AS VARCHAR)),'')"

    # ---- measure-level (scalar in filter context)
    def subquery(self,select,context,extra=None):
        where=context.where()+(' AND ('+extra+')' if extra else '')
        return f'(SELECT {select} FROM selected WHERE {where})'

    def measure(self,node,context,stack=()):
        self.depth+=1
        if self.depth>200 or len(stack)>MAX_DEPTH:raise DAXError('Measures are nested too deeply.')
        try:return self._measure(node,context,stack)
        finally:self.depth-=1

    def _measure(self,node,context,stack):
        kind=node[0]
        if kind in ('num','str','bool'):return literal(node)
        if kind=='measure':
            name=next((m for m in self.model.measures if _key(m)==_key(node[1])),None)
            if name is None:
                if any(_key(c)==_key(node[1]) for c in [*self.model.columns,*self.model.calculated]):
                    raise DAXError(f"[{node[1]}] is a column. In a measure, aggregate it, e.g. SUM('{self.model.table}'[{node[1]}]).")
                raise DAXError(f'Measure [{node[1]}] does not exist yet. Create it first.')
            if name in stack:raise DAXError(f'Measure [{name}] refers to itself through '+' → '.join([*stack,name])+'.')
            return '('+self.measure(parse(self.model.measures[name]),context,(*stack,name))+')'
        if kind=='col':raise DAXError(f"A measure cannot return the bare column [{node[2]}]. Aggregate it, e.g. SUM('{node[1]}'[{node[2]}]), or use SELECTEDVALUE.")
        if kind=='neg':return '(-('+self.measure(node[1],context,stack)+'))'
        if kind=='not':return '(NOT ('+self.measure(node[1],context,stack)+'))'
        if kind=='bin':
            op=node[1];left=self.measure(node[2],context,stack);right=self.measure(node[3],context,stack)
            if op in ('+','-'):return f'(CASE WHEN ({left}) IS NULL AND ({right}) IS NULL THEN NULL ELSE COALESCE({left},0){op}COALESCE({right},0) END)'
            if op=='/':return f'(CASE WHEN ({right})=0 THEN NULL ELSE ({left})/({right}) END)'
            if op=='&':return f"(COALESCE(CAST({left} AS VARCHAR),'')||COALESCE(CAST({right} AS VARCHAR),''))"
            return f'({left} {dict({"&&":"AND","||":"OR"}).get(op,op)} {right})'
        if kind=='in':return '('+self.measure(node[1],context,stack)+' IN ('+','.join(literal(v) for v in node[2])+'))'
        if kind!='call':raise DAXError('A table or unit cannot be used as a value here.')
        name,args=node[1],node[2]
        def m(i,ctx=context):return self.measure(args[i],ctx,stack)
        if name in AGGREGATES:
            self.arity(name,args,1,2)
            if len(args)==2:  # scalar MIN(a,b)/MAX(a,b)
                if name not in ('MIN','MAX'):raise DAXError(f'{name} takes one column.')
                return f'{"least" if name=="MIN" else "greatest"}({m(0)},{m(1)})'
            return self.subquery(f'{AGGREGATES[name]}({self.column_number(args[0])})',context)
        if name in ('COUNT','COUNTA','COUNTBLANK','DISTINCTCOUNT','DISTINCTCOUNTNOBLANK'):
            self.arity(name,args,1,1)
            if name=='COUNT':return self.subquery(f'NULLIF(count({self.column_number(args[0])}),0)',context)
            value=self.column_text(args[0])
            if name=='COUNTA':return self.subquery(f'NULLIF(count({value}),0)',context)
            if name=='COUNTBLANK':return self.subquery(f'NULLIF(count(*) FILTER (WHERE {value} IS NULL),0)',context)
            if name=='DISTINCTCOUNTNOBLANK':return self.subquery(f'NULLIF(count(DISTINCT {value}),0)',context)
            # DAX counts BLANK as one distinct value.
            return self.subquery(f'NULLIF(count(DISTINCT {value})+CASE WHEN count(*) FILTER (WHERE {value} IS NULL)>0 THEN 1 ELSE 0 END,0)',context)
        if name=='COUNTROWS':
            self.arity(name,args,1,1);ctx,extra=self.table_argument(args[0],context,stack)
            return self.subquery('NULLIF(count(*),0)',ctx,extra)
        if name in ITERATORS:
            self.arity(name,args,2,2);ctx,extra=self.table_argument(args[0],context,stack)
            return self.subquery(f'{ITERATORS[name]}({self.number(self.row(args[1]))})',ctx,extra)
        if name=='DIVIDE':
            self.arity(name,args,2,3);a,b=m(0),m(1);alt=m(2) if len(args)>2 else 'NULL'
            return f'(CASE WHEN ({b}) IS NULL OR ({b})=0 THEN {alt} ELSE ({a})/({b}) END)'
        if name=='IF':
            self.arity(name,args,2,3);return f'(CASE WHEN {m(0)} THEN {m(1)} ELSE {m(2) if len(args)>2 else "NULL"} END)'
        if name=='SWITCH':
            if len(args)<3:raise DAXError('SWITCH needs an expression and at least one value/result pair.')
            subject=m(0);pairs=args[1:];default='NULL'
            if len(pairs)%2:default=self.measure(pairs[-1],context,stack);pairs=pairs[:-1]
            if args[0]==('bool',True):
                cases=' '.join(f'WHEN {self.measure(pairs[i],context,stack)} THEN {self.measure(pairs[i+1],context,stack)}' for i in range(0,len(pairs),2))
            else:
                cases=' '.join(f'WHEN ({subject})=({self.measure(pairs[i],context,stack)}) THEN {self.measure(pairs[i+1],context,stack)}' for i in range(0,len(pairs),2))
            return f'(CASE {cases} ELSE {default} END)'
        if name=='COALESCE':self.arity(name,args,2,8);return 'COALESCE('+','.join(m(i) for i in range(len(args)))+')'
        if name in ('ABS','INT'):self.arity(name,args,1,1);return f'{"abs" if name=="ABS" else "floor"}({m(0)})'
        if name=='ROUND':self.arity(name,args,2,2);return f'round({m(0)},CAST({m(1)} AS INTEGER))'
        if name=='BLANK':return 'NULL'
        if name in ('TRUE','FALSE'):return name
        if name=='ISBLANK':self.arity(name,args,1,1);return f'(({m(0)}) IS NULL)'
        if name in ('AND','OR'):self.arity(name,args,2,2);return f'({m(0)} {name} {m(1)})'
        if name=='NOT':self.arity(name,args,1,1);return f'(NOT {m(0)})'
        if name in ('HASONEVALUE','SELECTEDVALUE'):
            self.arity(name,args,1 if name=='HASONEVALUE' else 1,1 if name=='HASONEVALUE' else 2)
            value=self.column_text(args[0])
            if name=='HASONEVALUE':return self.subquery(f'count(DISTINCT {value})=1',context)
            alt=m(1) if len(args)>1 else 'NULL'
            return f'COALESCE({self.subquery(f"CASE WHEN count(DISTINCT {value})=1 THEN min({value}) END",context)},{alt})'
        if name=='CALCULATE':
            if not args:raise DAXError('CALCULATE needs an expression.')
            ctx=context
            for argument in args[1:]:ctx=self.modifier(argument,ctx,stack)
            return m(0,ctx)
        if name in ('TOTALYTD','TOTALQTD','TOTALMTD'):
            self.arity(name,args,2,2);self.date_reference(args[1])
            return m(0,self.period_to_date({'TOTALYTD':'year','TOTALQTD':'quarter','TOTALMTD':'month'}[name],context))
        if name=='RANKX':return self.rankx(args,context,stack)
        if name in ('FILTER','ALL','REMOVEFILTERS','ALLEXCEPT','KEEPFILTERS','DATESYTD','DATESQTD','DATESMTD','SAMEPERIODLASTYEAR','DATEADD',
                    'PREVIOUSMONTH','PREVIOUSQUARTER','PREVIOUSYEAR','PREVIOUSDAY'):
            raise DAXError(f'{name} returns a table or filter. Use it inside CALCULATE, e.g. CALCULATE([Measure], {name}(...)).')
        raise DAXError(f'{name} cannot be used as a measure value.')

    # ---- tables, filters and context modifiers
    def table_argument(self,node,context,stack):
        """Table expression for COUNTROWS/iterators: the fact table, FILTER(...), or ALL(...)."""
        if node[0]=='table':
            if not self.model.is_fact(node[1]):raise DAXError(f'Iterate over the fact table \'{self.model.table}\'.')
            return context,None
        if node[0]=='call' and node[1]=='FILTER':
            self.arity('FILTER',node[2],2,2)
            ctx,extra=self.table_argument(node[2][0],context,stack)
            condition='('+self.row(node[2][1])+')'
            return ctx,(extra+' AND '+condition) if extra else condition
        if node[0]=='call' and node[1] in ('ALL','REMOVEFILTERS'):
            return self.modifier(node,context,stack),None
        raise DAXError('Use the fact table, FILTER(table, condition) or ALL(...) as the table argument.')

    def removal(self,node):
        """Keys removed by ALL/REMOVEFILTERS arguments."""
        if node[0]=='table':
            if self.model.is_fact(node[1]):return lambda c:c.key.startswith('fact[')
            if _key(node[1])==_key(DATE_TABLE):return lambda c:c.key.startswith('date[')
            raise DAXError(f'Unknown table “{node[1]}”.')
        if node[0]=='col':
            _,key,_=self.model.column_sql(node[1],node[2]);return lambda c,key=key:c.key==key
        raise DAXError('ALL and REMOVEFILTERS take a table or columns.')

    def modifier(self,node,context,stack):
        kind=node[0]
        if kind=='call':
            name,args=node[1],node[2]
            if name in ('ALL','REMOVEFILTERS'):
                if not args:return Context()
                removals=[self.removal(a) for a in args]
                return context.without(lambda c:any(r(c) for r in removals))
            if name=='ALLEXCEPT':
                if len(args)<2:raise DAXError('ALLEXCEPT needs a table and at least one column to keep.')
                keep=set()
                for a in args[1:]:
                    if a[0]!='col':raise DAXError('ALLEXCEPT keeps columns, e.g. ALLEXCEPT(Facts, Facts[region]).')
                    keep.add(self.model.column_sql(a[1],a[2])[1])
                prefix='date[' if args[0][0]=='table' and _key(args[0][1])==_key(DATE_TABLE) else 'fact['
                return context.without(lambda c:c.key.startswith(prefix) and c.key not in keep)
            if name=='KEEPFILTERS':
                self.arity(name,args,1,1);return context.plus(*self.boolean_filters(args[0]))
            if name=='FILTER':
                self.arity(name,args,2,2);ctx,extra=self.table_argument(args[0],context,stack)
                if any(n[0]=='measure' for n in walk(args[1])):
                    raise DAXError('Measures inside FILTER conditions are not supported here; filter on columns, e.g. FILTER(Facts, Facts[amount] > 100).')
                condition='('+self.row(args[1])+')'+(' AND '+extra if extra else '')
                return ctx.plus(Condition('filter:'+condition,condition,D in condition))
            if name in ('DATESYTD','DATESQTD','DATESMTD'):
                self.arity(name,args,1,1);self.date_reference(args[0])
                return self.period_to_date({'DATESYTD':'year','DATESQTD':'quarter','DATESMTD':'month'}[name],context)
            if name=='SAMEPERIODLASTYEAR':
                self.arity(name,args,1,1);self.date_reference(args[0]);return self.shift(context,1,'YEAR')
            if name=='DATEADD':
                self.arity(name,args,3,3);self.date_reference(args[0])
                count=args[1]
                if count[0]=='neg' and count[1][0]=='num':n=-count[1][1]
                elif count[0]=='num':n=count[1]
                else:raise DAXError('DATEADD needs a whole number of intervals, e.g. DATEADD(\'Date\'[Date], -1, YEAR).')
                if args[2][0]!='unit' or not float(n).is_integer():raise DAXError('DATEADD intervals are DAY, MONTH, QUARTER or YEAR with a whole-number count.')
                return self.shift(context,-int(n),args[2][1])
            if name in ('PREVIOUSMONTH','PREVIOUSQUARTER','PREVIOUSYEAR','PREVIOUSDAY'):
                self.arity(name,args,1,1);self.date_reference(args[0])
                return self.previous({'PREVIOUSMONTH':'month','PREVIOUSQUARTER':'quarter','PREVIOUSYEAR':'year','PREVIOUSDAY':'day'}[name],context)
        return context.without(lambda c:c.key in {f.key for f in self.boolean_filters(node)}).plus(*self.boolean_filters(node))

    def boolean_filters(self,node,keep=False):
        """CALCULATE boolean filters: each references one column and replaces that column's filter."""
        if node[0]=='bin' and node[1]=='&&':
            return [*self.boolean_filters(node[2],keep),*self.boolean_filters(node[3],keep)]
        columns={(n[1],n[2]) for n in walk(node) if n[0]=='col'}
        if any(n[0]=='measure' for n in walk(node)):raise DAXError('CALCULATE filters compare a column with values, e.g. Facts[region] = "North". Use FILTER for other conditions.')
        keys={self.model.column_sql(t,c)[1] for t,c in columns}
        if len(keys)!=1:raise DAXError('Each CALCULATE filter must reference exactly one column; use FILTER(table, condition) to combine columns.')
        condition=self.row(node);key=keys.pop()
        return [Condition(key,condition,D in condition)]

    def date_reference(self,node):
        if not (node[0]=='col' and _key(node[1])==_key(DATE_TABLE) and _key(node[2])=='date'):
            raise DAXError("Time intelligence takes the Date table's date column: 'Date'[Date].")
        self.model.date_sql()

    def shift(self,context,amount,unit):
        """Rows whose date moved by `amount` units lands in the current context (DATEADD semantics).

        The visible dates are pinned first: with no date filter in context (a card, a region row) the
        visible period is the Date table's span in that context, so last year means that span shifted
        back - not the same rows again."""
        pinned=context.plus(Condition('date[visible]',f'{D} BETWEEN {self.visible("min",context)} AND {self.visible("max",context)}',True))
        shifted=f'CAST(({D}) + INTERVAL ({amount}) {UNITS[unit]} AS DATE)'
        return Context(tuple(replace(c,sql=c.sql.replace(D,shifted)) if c.dated else c for c in pinned.conditions))

    def visible(self,aggregate,context):
        # The visible dates of *this* context are fixed here; later shifts move rows, not these bounds.
        return f'(SELECT {aggregate}({D}) FROM selected WHERE {context.where()})'.replace(D,DF)

    def period_to_date(self,grain,context):
        last=self.visible('max',context)
        undated=context.without(lambda c:c.dated)
        return undated.plus(Condition('date[date]',f"{D} BETWEEN CAST(date_trunc('{grain}',{last}) AS DATE) AND {last}",True))

    def previous(self,grain,context):
        first=self.visible('min',context);start=f"CAST(date_trunc('{grain}',{first}) AS DATE)"
        undated=context.without(lambda c:c.dated)
        return undated.plus(Condition('date[date]',f'{D} >= CAST({start} - INTERVAL 1 {grain.upper()} AS DATE) AND {D} < {start}',True))

    def rankx(self,args,context,stack):
        self.arity('RANKX',args,2,3)
        table=args[0]
        if not (table[0]=='call' and table[1] in ('ALL','REMOVEFILTERS') and len(table[2])==1 and table[2][0][0]=='col'):
            raise DAXError("RANKX here ranks over one column: RANKX(ALL('Table'[column]), [Measure]).")
        column=table[2][0];sql,key,_=self.model.column_sql(column[1],column[2])
        base=context.without(lambda c:c.key==key)
        candidate=base.plus(Condition(key,f'({sql}) IS NOT DISTINCT FROM __rank.v',D in sql))
        current=self.measure(args[1],context,stack);other=self.measure(args[1],candidate,stack)
        order=args[2] if len(args)>2 else ('table','DESC')
        if order[0]!='table' or order[1].upper() not in ('DESC','ASC'):raise DAXError('RANKX order is DESC or ASC.')
        better='>' if order[1].upper()=='DESC' else '<'
        values=f'(SELECT DISTINCT {sql} AS v FROM selected WHERE {base.where()}) __rank'
        return f'(CASE WHEN ({current}) IS NULL THEN NULL ELSE 1+(SELECT count(*) FROM {values} WHERE ({other}) {better} ({current})) END)'


def walk(node):
    yield node
    for child in node[1:]:
        if isinstance(child,tuple) and child and isinstance(child[0],str):yield from walk(child)
        elif isinstance(child,list):
            for item in child:
                if isinstance(item,tuple):yield from walk(item)


# ------------------------------------------------------------- evaluation
def finalize(sql,model):
    if D not in sql and DF not in sql:return sql
    date='('+model.date_sql()+')'
    return sql.replace(D,date).replace(DF,date)


@dataclass
class Field:
    """A visual axis/legend field: a fact column, calculated column or Date column."""
    table: str
    column: str

    @property
    def label(self):return f"{self.table}[{self.column}]"


def evaluate(engine,model,measures,rows=(),slicers=(),*,sort=None,descending=True,limit=MAX_GROUPS):
    """Evaluate measures by row fields under slicer filters. Returns an Engine result with a Total row separately."""
    if not measures:raise DAXError('Choose at least one measure.')
    if len(rows)>2:raise DAXError('Use at most two row fields.')
    compiler=Compiler(model);groups=[]
    for index,item in enumerate(rows):
        sql,key,_=model.column_sql(item.table,item.column);groups.append((f'__g{index}',sql,key))
    base=Context(tuple(slicers))
    context=base.plus(*(Condition(key,f'({sql}) IS NOT DISTINCT FROM ctx.{alias}',D in sql) for alias,sql,key in groups))
    expressions=[]
    for name in measures:
        canonical=next((m for m in model.measures if _key(m)==_key(name)),None)
        if canonical is None:raise DAXError(f'Measure [{name}] does not exist.')
        expressions.append((canonical,compiler.measure(parse(model.measures[canonical]),context,(canonical,))))
    totals=[(name,compiler.measure(parse(model.measures[name]),base,(name,))) for name,_ in expressions]
    columns=[quote(item.label) for item in rows]+[quote(name) for name,_ in expressions]
    if groups:
        select=', '.join([f'ctx.{alias} AS {quote(rows[i].label)}' for i,(alias,_,_) in enumerate(groups)]+[f'{sql} AS {quote(name)}' for name,sql in expressions])
        distinct=', '.join(f'{sql} AS {alias}' for alias,sql,_ in groups)
        order=quote(sort) if sort else ', '.join(f'ctx.{alias}' for alias,_,_ in groups)
        direction='DESC' if (sort and descending) else 'ASC'
        query=f'SELECT {select} FROM (SELECT DISTINCT {distinct} FROM selected WHERE {base.where()}) ctx ORDER BY {order} {direction} NULLS LAST'
    else:
        query='SELECT '+', '.join(f'{sql} AS {quote(name)}' for name,sql in expressions)
    query=finalize(query,model);total_query=finalize('SELECT '+', '.join(f'{sql} AS {quote(name)}' for name,sql in totals),model)
    method={'dax':{name:model.measures[name] for name,_ in expressions},'rows':[r.label for r in rows],
            'slicers':[c.key for c in slicers],'semantics':'DAX filter context compiled to correlated DuckDB subqueries over the selected rows'}
    result=engine.run(query,limit=limit,method=method,timeout=20)
    if groups:
        # Power BI hides rows where every measure is blank.
        keep=[r for r in result['rows'] if any(v is not None for v in r[len(groups):])]
        result['rows']=keep
    total=engine.run(total_query,limit=1,method=method|{'total':True},timeout=20)
    result['total']=list(total['rows'][0]) if total['rows'] else None
    result['dax_sql']=query
    return result


def slicer(model,table,column,values=None,*,start=None,end=None):
    """A page-level slicer condition (removable by ALL on that column)."""
    sql,key,dated=model.column_sql(table,column)
    if values is not None:
        if not values:raise DAXError('Choose at least one slicer value.')
        parts=[f'({sql}) IS NULL' if v is None else f"CAST({sql} AS VARCHAR) = {literal(('str',str(v)))}" for v in values]
        return Condition(key,' OR '.join(parts),dated)
    bounds=[]
    if start is not None:bounds.append(f"({sql}) >= DATE {literal(('str',str(start)))}")
    if end is not None:bounds.append(f"({sql}) <= DATE {literal(('str',str(end)))}")
    if not bounds:raise DAXError('Choose a date range.')
    return Condition(key,' AND '.join(bounds),dated)


def check(engine,model,expression,name='Check'):
    """Validate and evaluate one expression as a card without saving it."""
    trial=Model(model.table,model.columns,model.names,model.date_basis,dict(model.measures),dict(model.calculated))
    trial.measures.pop(next((m for m in trial.measures if _key(m)==_key(name)),''),None)
    trial.add_measure(name,expression)
    return evaluate(engine,trial,[name])


def distinct_values(engine,model,table,column,limit=200):
    sql,_,_=model.column_sql(table,column)
    result=engine.run(finalize(f'SELECT DISTINCT {sql} AS v FROM selected ORDER BY 1 NULLS LAST',model),limit=limit)
    return [r[0] for r in result['rows']],result['truncated']
