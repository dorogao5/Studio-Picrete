"""Deterministic discovery in the mounted reference table; no model-generated IDs or values."""
import json
import re
import unicodedata
from collections import Counter
from decimal import Decimal
from pathlib import Path

PROPERTY_ALIASES = {
 'solubility_product': ['ksp','произведение растворимости','произведения растворимости','пр','solubility product'],
 'standard_molar_formation_enthalpy': ['энтальпия образования','энтальпию образования','теплота образования','formation enthalpy','enthalpy of formation','deltah_f'],
 'standard_molar_entropy': ['энтропия','энтропию','entropy'],
 'standard_molar_heat_capacity_pressure': ['теплоемкость','теплоёмкость','heat capacity','cp'],
 'acid_dissociation_constant': ['кислотная диссоциация','константа диссоциации кислоты','константу диссоциации кислоты','acid dissociation','ka'],
 'base_dissociation_constant': ['константа диссоциации основания','base dissociation','kb'],
 'complex_instability_constant': ['константа нестойкости','константу нестойкости','нестoйкость','instability constant','complex dissociation'],
 'standard_electrode_potential': ['электродный потенциал','электродного потенциала','электродные потенциалы','стандартный потенциал','потенциал','electrode potential','reduction potential','e0'],
 'molar_gas_constant': ['газовая постоянная','газовую постоянную','gas constant'],
 'avogadro_number': ['число авогадро','постоянная авогадро','avogadro'],
 'relative_atomic_mass': ['атомная масса','атомную массу','atomic weight','atomic mass'],
 'molar_mass': ['молярная масса','молярную массу','molar mass'],
 'atomic_number': ['атомный номер','atomic number'],
 'electron_configuration': ['электронная конфигурация','электронную конфигурацию','electron configuration'],
 'mean_bond_energy': ['энергия связи','энергию связи','bond energy'],
 'diatomic_dissociation_energy': ['энергия диссоциации','энергию диссоциации','dissociation energy'],
 'cryoscopic_constant': ['криоскопическая постоянная','cryoscopic'],
 'ebullioscopic_constant': ['эбуллиоскопическая постоянная','ebullioscopic'],
 'limiting_ionic_mobility': ['ионная подвижность','подвижность иона','ionic mobility'],
 'van_der_waals_a': ['van der waals a','ван дер ваальса a'],
 'van_der_waals_b': ['van der waals b','ван дер ваальса b'],
 'calorie_to_joule': ['calorie to joule','калория джоуль'],
 'atmosphere_to_pascal': ['atmosphere to pascal','атмосфера паскаль'],
 'torr_to_pascal': ['torr to pascal','торр паскаль'],
 'molar_energy_per_wavenumber': ['wavenumber','волновое число'],
 'electrode_potential_at_specified_concentration': ['electrode potential at specified concentration'],
}
NAMES = json.loads(Path(__file__).with_name('chemical_names.json').read_text())
PHASES = {'solid':'s','liquid':'l','gas':'g','aqueous':'aq','s':'s','l':'l','g':'g','aq':'aq'}
PHASE_WORDS = {'g':['газообразной','газообразный','газообразная','газообразного','gas','gaseous'],
 'l':['жидкой','жидкая','жидкий','жидкого','liquid'], 's':['твердой','твёрдой','твердый','твёрдый','solid'], 'aq':['водный раствор','водном растворе','aqueous']}
SUBS = str.maketrans('₀₁₂₃₄₅₆₇₈₉','0123456789')
SUPERS = str.maketrans('⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻','0123456789+-')

def normalize(text):
    text = re.sub(r'[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻]+', lambda m: '^'+m[0].translate(SUPERS), text)
    text = unicodedata.normalize('NFKC',text.translate(SUBS)).replace('−','-').replace('→',' -> ')
    text = re.sub(r'\^([+-])',r'\1',text)
    # Unambiguous monatomic cation notation; never turn molecular N3- into N^3-.
    text = re.sub(r'(^|\s)(\d*)([A-Z][a-z]?)([2-9])\+(?=\s|$)',r'\1\2\3^\4+',text)
    return re.sub(r'\s+',' ',text).strip()

def phase(record):
    c=record['conditions']
    p=c.get('phase')
    if p in ('s','l','g','aq'): return p
    if p in ('graphite','diamond','quartz','amorphous','red','white','rhombic','monoclinic'): return 's'
    m=re.search(r'\((s|l|g|aq)\)$',record['entity'])
    if m:return m[1]
    if c.get('solvent')=='water':return 'aq'
    return None

def strip_phase(entity):
    return re.sub(r'\((?:s|l|g|aq|graphite|diamond|quartz|amorphous|red|white|rhombic|monoclinic)\)$','',entity).strip()

def phrase_replace(text,phrase,replacement=''):
    return re.sub(r'(?<!\w)'+re.escape(phrase)+r'(?!\w)',replacement,text,flags=re.I)

def parse_query(query, prop, requested_phase):
    query=normalize(query)
    inferred=[]
    for key,aliases in PROPERTY_ALIASES.items():
        for alias in sorted([key]+aliases,key=len,reverse=True):
            updated=phrase_replace(query,alias)
            if updated!=query:
                inferred.append(key);query=updated
    if prop:
        prop=normalize(prop).lower()
        known={k for k in PROPERTY_ALIASES}
        if prop not in known:
            prop=next((k for k,v in PROPERTY_ALIASES.items() if prop in v),prop)
        if prop not in known:raise ValueError('unknown property; use a property name from the tool catalog')
    elif inferred:
        if len(set(inferred))>1:raise ValueError('search one property at a time')
        prop=inferred[0]
    if requested_phase:
        if requested_phase not in PHASES:raise ValueError('phase must be solid, liquid, gas or aqueous')
        requested_phase=PHASES[requested_phase]
    for p,words in PHASE_WORDS.items():
        for word in words:
            updated=phrase_replace(query,word)
            if updated!=query:
                if requested_phase and requested_phase!=p:raise ValueError('conflicting phases in query and filter')
                requested_phase=p;query=updated
    query=re.sub(r'^\s*(?:найди|найти|найдите|получи|получить|значение|find|get)\s+','',query,flags=re.I).strip(' ,;:')
    query=re.sub(r'^\s*(?:для|of|for)\s+','',query,flags=re.I).strip()
    mapped=NAMES.get(query.lower())
    if mapped:query=mapped
    # Query can include a phase as part of a formula.
    m=re.search(r'\((s|l|g|aq)\)$',query)
    if m:
        if requested_phase and requested_phase!=m[1]:raise ValueError('conflicting phases')
        requested_phase=m[1];query=strip_phase(query)
    return query,prop,requested_phase

def search(records, args):
    allowed={'query','property','phase','temperature_k','limit','offset'}
    if set(args)-allowed:raise ValueError('search accepts query/property/phase/temperature_k/limit/offset, not reference_id')
    query=args.get('query')
    if not isinstance(query,str) or not query.strip() or len(query)>512:raise ValueError('query must be a nonempty string up to 512 characters')
    for key in ('property','phase'):
        if key in args and (not isinstance(args[key],str) or not args[key].strip() or len(args[key])>128):raise ValueError('invalid '+key)
    limit=args.get('limit',8);offset=args.get('offset',0)
    if type(limit)!=int or not 1<=limit<=8:raise ValueError('limit must be 1..8')
    if type(offset)!=int or not 0<=offset<=10000:raise ValueError('offset must be 0..10000')
    requested_temperature=args.get('temperature_k')
    if requested_temperature is not None and (type(requested_temperature) not in (int,float) or not 0<requested_temperature<=100000 or not Decimal(str(requested_temperature)).is_finite()):raise ValueError('invalid temperature_k')
    query,prop,p=parse_query(query,args.get('property'),args.get('phase'))
    pair=[normalize(v) for v in query.split('/')] if '/' in query else []
    if pair and not prop:prop='standard_electrode_potential'
    if not query and not prop:raise ValueError('specify a substance or property')
    matches=[]
    for record in records:
        if prop and record['property']!=prop:continue
        entity=normalize(record['entity']);base=strip_phase(entity)
        # Formula case matters: CO is carbon monoxide, Co is cobalt.
        normalized_name=NAMES.get(base.lower(),base) if not re.fullmatch(r'[A-Z][A-Za-z0-9()\[\]^+\-]*',base) else base
        if not query:score=1
        elif query==base or query==entity or query==normalized_name:score=100
        elif query.lower()==base.lower() and not re.fullmatch(r'[A-Z][A-Za-z0-9()\[\]^+\-]*',query):score=90
        elif pair and len(pair)==2 and ' -> ' in entity and all(v in [normalize(re.sub(r'^\d+(?=[A-Z\[])','',t)) for t in re.split(r' \+ | -> ',entity)] for v in pair):score=95
        elif ' -> ' in entity and query in entity.split(' -> '):score=70
        elif ' -> ' in entity and query in [re.sub(r'^\d+(?=[A-Z\[])','',v) for v in re.split(r' \+ | -> ',entity)]:score=60
        elif query.lower()==record['property'].lower():score=80
        else:continue
        checks=[];rp=phase(record)
        if p:
            if rp and rp!=p:continue
            checks.append('matched' if rp else 'unknown_phase')
        if requested_temperature is not None:
            c=record['conditions'];t=c.get('temperature');unit=c.get('temperature_unit')
            if t is None or unit not in ('K','C','°C'):checks.append('unknown_temperature')
            else:
                tk=Decimal(str(t))+(Decimal('273.15') if unit in ('C','°C') else 0)
                if abs(tk-Decimal(str(requested_temperature)))>Decimal('0.000001'):continue
                checks.append('matched')
        matches.append((score,record,[v for v in checks if v!='matched']))
    matches.sort(key=lambda v:(-v[0],v[1]['entity'],v[1]['property'],v[1]['reference_id']))
    exact=[v for v in matches if v[0]>=90]
    if exact:matches=exact
    page=matches[offset:offset+limit]
    status='not_found' if not matches else ('unique' if len(matches)==1 and not matches[0][2] else ('conditions_unverified' if len(matches)==1 else 'ambiguous'))
    out=dict(match_status=status,total_matches=len(matches),offset=offset,next_offset=offset+len(page) if offset+len(page)<len(matches) else None,
             candidates=[dict(record=r,conditions_warnings=w) for _,r,w in page],
             available_properties=dict(Counter(r['property'] for _,r,_ in matches)),
             guidance='Use only returned records and their actual units/conditions. If ambiguous, refine property/phase/temperature or choose the explicitly matching candidate. Unknown conditions are not a match to requested conditions. If not_found, no data is available for this query; do not invent IDs or values.')
    if status=='unique':out['record']=matches[0][1]
    return out
