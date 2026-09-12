"""Deterministic primitives. Untrusted callers MUST use gateway.invoke isolation."""
from __future__ import annotations
import ast
import csv
import hashlib
import io
import json
import os
import re
from decimal import Decimal, Underflow, localcontext
from pathlib import Path
import sympy as sp

ROOT = Path(__file__).resolve().parent
MAX_EXPRESSION_LENGTH = 512
CALCULATOR_VERSION = 'scientific-calculator-v2'
SYMPY_VERSION = 'ast-sympy-v2'
REFERENCE_VERSION = 'reference-db-private-v1'
FUNCTIONS = {'ln': sp.log, 'log': sp.log, 'exp': sp.exp, 'sqrt': sp.sqrt,
             'sin': sp.sin, 'cos': sp.cos, 'tan': sp.tan, 'asin': sp.asin,
             'acos': sp.acos, 'atan': sp.atan, 'sinh': sp.sinh, 'cosh': sp.cosh,
             'tanh': sp.tanh, 'abs': sp.Abs}
CONSTANTS = {'pi': sp.pi, 'E': sp.E}

def trace(tool, args, result, version):
    payload = json.dumps({'tool': tool, 'version': version, 'arguments': args}, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return dict(trace_id=tool+':'+hashlib.sha256(payload.encode()).hexdigest()[:16],
                tool_name=tool, tool_version=version, arguments=args,
                normalized_result=result, status='success')

def tree(source):
    if not isinstance(source, str) or not source.strip() or len(source) > MAX_EXPRESSION_LENGTH:
        raise ValueError('expression must contain 1..512 characters')
    source = source.replace('^', '**').strip()
    root = ast.parse(source, mode='eval').body
    def depth(n, level=0):
        if level > 64:
            raise ValueError('expression nesting limit')
        return 1 + sum(depth(c, level+1) for c in ast.iter_child_nodes(n))
    if depth(root) > 1024:
        raise ValueError('expression node limit')
    return source, root

def literal(node, source):
    if not isinstance(node, ast.Constant) or type(node.value) not in (int, float):
        raise ValueError('only decimal numeric literals allowed')
    token = ast.get_source_segment(source, node)
    if not re.fullmatch(r'(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?', token):
        raise ValueError('invalid decimal literal')
    value = Decimal(token)
    if not value.is_finite() or (value and abs(value.adjusted()) > 10000):
        raise ValueError('numeric magnitude limit')
    return value

def decimal_node(node, source):
    if isinstance(node, ast.Constant):
        value = literal(node, source)
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = decimal_node(node.operand, source)
        if isinstance(node.op, ast.USub): value = -value
    elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
        a, b = decimal_node(node.left, source), decimal_node(node.right, source)
        if isinstance(node.op, ast.Add): value = a+b
        elif isinstance(node.op, ast.Sub): value = a-b
        elif isinstance(node.op, ast.Mult): value = a*b
        elif isinstance(node.op, ast.Div): value = a/b
        else: value = a**b
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ('ln','log','exp','sqrt') and len(node.args)==1 and not node.keywords:
        a = decimal_node(node.args[0], source)
        value = {'ln': a.ln, 'log': a.ln, 'exp': a.exp, 'sqrt': a.sqrt}[node.func.id]()
    else:
        raise ValueError('unsupported calculator syntax')
    if not value.is_finite(): raise ValueError('nonfinite result')
    return value

def calculator(arguments):
    source, root = tree(arguments.get('expression'))
    with localcontext() as ctx:
        ctx.prec, ctx.Emax, ctx.Emin = 34, 10000, -10000
        ctx.traps[Underflow] = True
        value = +decimal_node(root, source)
        if not value.is_finite(): raise ValueError('nonfinite result')
        result = str(value)
    return trace('calculator', arguments, {'value': result, 'unit': 'dimensionless',
                 'unit_note': 'Scalar arithmetic only; caller must explicitly convert and verify units.',
                 'precision': 34}, CALCULATOR_VERSION)

def symbolic_node(node, source, names):
    if isinstance(node, ast.Constant):
        # Construct exact rational from Decimal integer ratio, never parse strings in SymPy.
        n, d = literal(node, source).as_integer_ratio()
        return sp.Rational(n, d)
    if isinstance(node, ast.Name) and node.id in names: return names[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        a = symbolic_node(node.operand, source, names)
        return a if isinstance(node.op, ast.UAdd) else sp.Mul(-1, a, evaluate=False)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
        a, b = symbolic_node(node.left, source, names), symbolic_node(node.right, source, names)
        if isinstance(node.op, ast.Add): return sp.Add(a,b,evaluate=False)
        if isinstance(node.op, ast.Sub): return sp.Add(a,sp.Mul(-1,b,evaluate=False),evaluate=False)
        if isinstance(node.op, ast.Mult): return sp.Mul(a,b,evaluate=False)
        if isinstance(node.op, ast.Div): return sp.Mul(a,sp.Pow(b,-1,evaluate=False),evaluate=False)
        return sp.Pow(a,b,evaluate=False)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS and len(node.args)==1 and not node.keywords:
        return FUNCTIONS[node.func.id](symbolic_node(node.args[0], source, names), evaluate=False)
    raise ValueError('unsupported symbolic syntax or undeclared symbol')

def sympy_tool(arguments):
    symbols = arguments.get('symbols', [])
    if not isinstance(symbols, list) or len(symbols)>64 or any(not isinstance(s,str) or not re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}',s) or s in FUNCTIONS or s in CONSTANTS for s in symbols) or len(set(symbols))!=len(symbols):
        raise ValueError('invalid, duplicate or reserved declared symbols')
    names = {s: sp.Symbol(s, real=True) for s in symbols}
    names.update(CONSTANTS)
    def parse(key):
        source, root = tree(arguments.get(key))
        return symbolic_node(root, source, names)
    op = arguments.get('operation')
    if op in ('derivative','integral','solve'):
        variable = arguments.get('variable')
        if variable not in symbols: raise ValueError('variable must be declared')
        v = names[variable]
    if op=='simplify': result = sp.simplify(parse('expression'))
    elif op=='derivative': result = sp.diff(parse('expression'),v)
    elif op=='integral': result = sp.integrate(parse('expression'),v)
    elif op=='solve': result = sp.solve(parse('equation'),v)
    elif op=='identity': result = sp.simplify(parse('left')-parse('right')) == 0
    else: raise ValueError('unsupported operation')
    values = result if isinstance(result,list) else [result]
    if any(isinstance(x,sp.Basic) and x.has(sp.nan,sp.zoo,sp.oo,-sp.oo) for x in values):
        raise ValueError('nonfinite symbolic result')
    normalized = result if isinstance(result,bool) else str(result)
    data={'result':normalized,'is_zero': normalized=='0' if isinstance(normalized,str) else None,
          'assumptions': {s:'real' for s in symbols}}
    if op=='identity':
        data['interpretation']='proven_equal' if result else 'not_proven'
        data['domain_note']='Equality on the common domain; singularities and applicability require separate review. False is inconclusive, not proof of inequivalence.'
    return trace('sympy',arguments,data,SYMPY_VERSION)

def reference_db(arguments):
    configured = os.environ.get('REFERENCE_DB_PATH')
    if not configured: raise ValueError('REFERENCE_DB_PATH is required for private reference lookup')
    path = Path(configured)
    data = path.read_bytes()
    records = list(csv.DictReader(io.StringIO(data.decode('utf-8'))))
    record = next((r for r in records if r['reference_id']==arguments.get('reference_id')),None)
    if record is None: raise ValueError('unknown reference id')
    record['conditions'] = json.loads(record['conditions'])
    warnings = json.loads((ROOT/'reference-warnings.json').read_text()).get(record['reference_id'],[])
    return trace('reference_db',arguments,dict(database_id=REFERENCE_VERSION,database_version='1.0.0',database_sha256=hashlib.sha256(data).hexdigest(),record=record,review_warnings=warnings),REFERENCE_VERSION)

def dispatch(tool, arguments):
    try:
        if not isinstance(arguments,dict): raise ValueError('arguments must be an object')
        return {'calculator':calculator,'sympy':sympy_tool,'reference_db':reference_db}[tool](arguments)
    except Exception as exc:
        result = trace(tool, arguments, None, {'calculator':CALCULATOR_VERSION,'sympy':SYMPY_VERSION,'reference_db':REFERENCE_VERSION}.get(tool,'unknown'))
        result.update(status='error',error_type=type(exc).__name__,error='reference data unavailable' if isinstance(exc,OSError) else str(exc)[:512])
        return result
