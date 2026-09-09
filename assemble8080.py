#!/usr/bin/env python3
"""Standalone Intel 8080 assembler with M80-style source facilities."""

import re
import sys
from pathlib import Path
import argparse
from dataclasses import dataclass

REG = {"B":0,"C":1,"D":2,"E":3,"H":4,"L":5,"M":6,"A":7}
RP = {"B":0,"D":1,"H":2,"SP":3}
SINGLE = {
    "NOP":0x00,"RLC":0x07,"RRC":0x0F,"RAL":0x17,"RAR":0x1F,
    "DAA":0x27,"CMA":0x2F,"STC":0x37,"CMC":0x3F,"HLT":0x76,
    "RNZ":0xC0,"RZ":0xC8,"RET":0xC9,"RNC":0xD0,"RC":0xD8,
    "RPO":0xE0,"XTHL":0xE3,"RPE":0xE8,"PCHL":0xE9,"XCHG":0xEB,
    "RP":0xF0,"DI":0xF3,"RM":0xF8,"SPHL":0xF9,"EI":0xFB,
}
IMM8 = {"ADI":0xC6,"ACI":0xCE,"SUI":0xD6,"SBI":0xDE,
        "ANI":0xE6,"XRI":0xEE,"ORI":0xF6,"CPI":0xFE,
        "IN":0xDB,"OUT":0xD3}
ADDR = {"JNZ":0xC2,"JMP":0xC3,"CNZ":0xC4,"JZ":0xCA,"CZ":0xCC,
        "JNC":0xD2,"CNC":0xD4,"JC":0xDA,"CC":0xDC,"CALL":0xCD,
        "JPO":0xE2,"CPO":0xE4,"JPE":0xEA,"CPE":0xEC,
        "JP":0xF2,"CP":0xF4,"JM":0xFA,"CM":0xFC,
        "SHLD":0x22,"LHLD":0x2A,"STA":0x32,"LDA":0x3A}

SYMBOL = r'(?:[A-Za-z_$?@][A-Za-z0-9_$?@]*|\.\.[0-9A-Fa-f]{4})'
ALIASES = {'DEFB':'DB', 'DEFM':'DB', 'DEFW':'DW', 'DEFS':'DS',
           'DEFL':'SET', 'COND':'IFT', 'ENDC':'ENDIF', 'GLOBAL':'PUBLIC',
           'ENTRY':'PUBLIC', 'EXTERNAL':'EXTRN', 'EXT':'EXTRN',
           '$INCLUDE':'INCLUDE', 'MACLIB':'INCLUDE', 'EJECT':'PAGE'}
BLOCKS = {'MACRO', 'REPT', 'IRP', 'IRPC'}
IFS = {'IF','IFT','IFE','IFF','IF1','IF2','IFDEF','IFNDEF','IFB','IFNB','IFIDN','IFDIF'}
LISTING = {'.LIST','.XLIST','.SFCOND','.LFCOND','.TFCOND','.LALL','.SALL','.XALL'}


class AssemblyError(ValueError):
    """A source error, including its source/expansion location when available."""


class UnknownSymbol(AssemblyError):
    pass


def symbol(s, short=False):
    if not re.fullmatch(SYMBOL, s):
        raise AssemblyError(f'invalid symbol {s!r}')
    s = s.upper()
    return s[:6] if short else s


def strip_comment(s):
    """Keep semicolons inside strings, brackets, or escaped macro arguments."""
    quote = None
    level = 0
    i = 0
    while i < len(s):
        c = s[i]
        if c == '!' and quote is None:
            i += 2
            continue
        if quote:
            if c == quote:
                if i + 1 < len(s) and s[i+1] == quote:
                    i += 2
                    continue
                quote = None
        elif c in "\"'":
            quote = c
        elif c == '<':
            level += 1
        elif c == '>':
            level -= 1
        elif c == ';' and level <= 0:
            return s[:i].strip()
        i += 1
    return s.strip()


def split_args(s, brackets=False):
    if not s.strip():
        return []
    out, start, depth, quote, i = [], 0, 0, None, 0
    while i < len(s):
        c = s[i]
        if brackets and c == '!':
            i += 2
            continue
        if quote:
            if c == quote:
                if i+1 < len(s) and s[i+1] == quote:
                    i += 2
                    continue
                quote = None
        elif c in "\"'":
            quote = c
        elif c == '(' or (brackets and c == '<'):
            depth += 1
        elif c == ')' or (brackets and c == '>'):
            depth -= 1
            if depth < 0:
                raise AssemblyError('unmatched closing delimiter')
        elif c == ',' and depth == 0:
            out.append(s[start:i].strip())
            start = i+1
        i += 1
    if quote or depth:
        raise AssemblyError('unterminated string or argument delimiter')
    return out + [s[start:].strip()]


def string_value(s):
    if not re.fullmatch(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"", s):
        return None
    value = s[1:-1].replace(s[0]*2, s[0])
    if any(ord(c) > 127 for c in value):
        raise AssemblyError('strings must contain ASCII characters')
    return value


def parse_line(raw):
    # PRINTX owns its delimiter, so a semicolon in its text is not a comment.
    printx = re.match(r'^\s*\.PRINTX\s+(.*)$', raw, re.I)
    if printx:
        return None, '.PRINTX', printx[1]
    s = strip_comment(raw)
    label = None
    m = re.match(r'^(' + SYMBOL + r')::?\s*(.*)$', s)
    if m:
        label, s = m.groups()
    m = re.match(r'^(' + SYMBOL + r')\s+(EQU|SET|DEFL|MACRO)\b\s*(.*)$', s, re.I)
    if m:
        if label:
            raise AssemblyError('two names on one definition')
        label, op, arg = m.groups()
    elif s:
        parts = s.split(None, 1)
        op, arg = parts[0], parts[1] if len(parts) > 1 else ''
    else:
        return label, None, ''
    op = op.upper()
    return label, ALIASES.get(op, op), arg


class Expression:
    # M80 precedence, including unary minus below multiplication and NOT
    # below comparisons. All intermediate arithmetic is unsigned 16-bit.
    precedence = {'OR':10,'XOR':10,'AND':20,'EQ':40,'NE':40,'LT':40,
                  'LE':40,'GT':40,'GE':40,'+':50,'-':50,'*':70,'/':70,
                  'MOD':70,'SHL':70,'SHR':70}
    token = re.compile(r"\s*(X'(?:[0-9A-Fa-f]*)'|'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|[0-9][A-Za-z0-9]*|" + SYMBOL + r"|[()+*/,-])", re.I)

    def __init__(self, text, lookup, pc=0, radix=10):
        self.lookup, self.pc, self.radix = lookup, pc, radix
        self.tokens = []
        pos = 0
        while pos < len(text.rstrip()):
            m = self.token.match(text, pos)
            if not m:
                raise AssemblyError(f'invalid expression near {text[pos:]!r}')
            self.tokens.append(m.group(1))
            pos = m.end()
        self.i = 0

    def peek(self):
        return self.tokens[self.i].upper() if self.i < len(self.tokens) else ''

    def take(self):
        if self.i == len(self.tokens):
            raise AssemblyError('missing expression operand')
        t = self.tokens[self.i]
        self.i += 1
        return t

    def parse(self, minimum=0):
        t = self.take()
        u = t.upper()
        if u in ('+','-','NOT','HIGH','LOW','TYPE'):
            power = {'+':60,'-':60,'NOT':30,'HIGH':80,'LOW':80,'TYPE':80}[u]
            old_lookup = self.lookup
            undefined = []
            if u == 'TYPE':
                def type_lookup(name):
                    try: return old_lookup(name)
                    except UnknownSymbol:
                        undefined.append(name)
                        return 0
                self.lookup = type_lookup
            operand_start = self.i
            try:
                v = self.parse(power)
            except AssemblyError:
                if u != 'TYPE':
                    raise
                v = None
                if operand_start < len(self.tokens) and self.tokens[operand_start] == '(':
                    nesting = 0
                    for end in range(operand_start, len(self.tokens)):
                        if self.tokens[end] == '(': nesting += 1
                        elif self.tokens[end] == ')': nesting -= 1
                        if not nesting:
                            self.i = end+1
                            break
            finally:
                self.lookup = old_lookup
            if u == 'TYPE':
                v = 0x20 if v is not None and not undefined else 0
            elif u == '-': v = -v
            elif u == 'NOT': v = ~v
            elif u == 'HIGH': v >>= 8
            elif u == 'LOW': v &= 255
        elif u == '(':
            v = self.parse()
            if self.take() != ')':
                raise AssemblyError('expected closing parenthesis')
        elif u == '$':
            v = self.pc
        elif re.fullmatch(r"X'[0-9A-F]*'", u):
            if len(u) == 3:
                raise AssemblyError('empty hexadecimal constant')
            v = int(u[2:-1], 16)
        elif t[0] in "'\"":
            chars = string_value(t)
            if len(chars) > 2:
                raise AssemblyError('expression strings may contain at most two characters')
            v = int.from_bytes(chars.encode('ascii'), 'big')
        elif t[0].isdigit():
            base = {'B':2,'D':10,'O':8,'Q':8,'H':16}.get(u[-1])
            digits = u[:-1] if base else u
            try:
                v = int(digits, base or self.radix)
            except ValueError:
                raise AssemblyError(f'invalid number {t!r} for radix {base or self.radix}') from None
        elif u in SINGLE or u in IMM8 or u in ADDR:
            v = {**SINGLE, **IMM8, **ADDR}[u]
        elif u in ('MOV','MVI','LXI','INX','DCX','DAD','INR','DCR','ADD','ADC','SUB','SBB','ANA','XRA','ORA','CMP','PUSH','POP','LDAX','STAX','RST'):
            # Only opcode/register fields are allowed, never immediate data.
            first = self.take().upper()
            if u == 'MOV':
                if self.take() != ',': raise AssemblyError('MOV operand needs a comma')
                second = self.take().upper()
                v = encode(u, first+','+second, lambda _: 0)[0]
            elif u in ('MVI','LXI'):
                regs = REG if u == 'MVI' else RP
                if first not in regs: raise AssemblyError(f'invalid register {first}')
                v = (6 + regs[first]*8) if u == 'MVI' else (1+regs[first]*16)
            else:
                v = encode(u, first, lambda x: int(x))[0]
        else:
            v = self.lookup(u)
        v &= 0xffff
        while self.peek() in self.precedence and self.precedence[self.peek()] >= minimum:
            op = self.take().upper()
            rhs = self.parse(self.precedence[op]+1)
            if op == '+': v += rhs
            elif op == '-': v -= rhs
            elif op == '*': v *= rhs
            elif op in ('/','MOD'):
                if rhs == 0: raise AssemblyError('division by zero')
                v = v // rhs if op == '/' else v % rhs
            elif op == 'SHL': v = (v << rhs) if rhs < 16 else 0
            elif op == 'SHR': v = (v >> rhs) if rhs < 16 else 0
            elif op == 'AND': v &= rhs
            elif op == 'OR': v |= rhs
            elif op == 'XOR': v ^= rhs
            else:
                result = {'EQ':v==rhs,'NE':v!=rhs,'LT':v<rhs,'LE':v<=rhs,'GT':v>rhs,'GE':v>=rhs}[op]
                v = 0xffff if result else 0
            v &= 0xffff
        return v

    def evaluate(self):
        if self.peek() == 'NUL':
            return 0xffff if len(self.tokens) == 1 else 0
        v = self.parse()
        if self.i != len(self.tokens):
            raise AssemblyError(f'unexpected expression token {self.tokens[self.i]!r}')
        return v


def byte(v):
    if v >> 8 not in (0, 255):
        raise AssemblyError(f'byte value out of range: {v:04X}H')
    return v & 255


def encode(op, arg, evaluate):
    # Limit splits for MVI so an opcode-as-operand can itself contain a comma.
    a = split_args(arg)
    if op == 'MVI' and len(a) > 2:
        a = [a[0], ','.join(a[1:])]
    def count(n):
        if len(a) != n or any(not x for x in a):
            raise AssemblyError(f'{op} expects {n} operand(s)')
    def reg(value, choices):
        if value.upper() not in choices:
            raise AssemblyError(f'invalid {op} register {value!r}')
        return choices[value.upper()]
    if op in SINGLE:
        count(0)
        return [SINGLE[op]]
    if op == 'MOV':
        count(2)
        d, s = reg(a[0], REG), reg(a[1], REG)
        if d == s == 6: raise AssemblyError('MOV M,M encodes HLT; use HLT explicitly')
        return [0x40+d*8+s]
    if op in ('MVI','LXI'):
        count(2)
        r = reg(a[0], REG if op == 'MVI' else RP)
        v = evaluate(a[1])
        return [6+r*8,byte(v)] if op == 'MVI' else [1+r*16,v&255,v>>8]
    if op in ('INX','DCX','DAD','INR','DCR','ADD','ADC','SUB','SBB','ANA','XRA','ORA','CMP','PUSH','POP','LDAX','STAX'):
        count(1)
        if op in ('INX','DCX','DAD'):
            return [{'INX':3,'DCX':11,'DAD':9}[op]+16*reg(a[0], RP)]
        if op in ('INR','DCR'):
            return [(4 if op == 'INR' else 5)+8*reg(a[0],REG)]
        if op in ('PUSH','POP'):
            return [(0xc5 if op == 'PUSH' else 0xc1)+16*reg(a[0],{'B':0,'D':1,'H':2,'PSW':3})]
        if op in ('LDAX','STAX'):
            return [(10 if op == 'LDAX' else 2)+16*reg(a[0],{'B':0,'D':1})]
        return [{'ADD':0x80,'ADC':0x88,'SUB':0x90,'SBB':0x98,'ANA':0xa0,'XRA':0xa8,'ORA':0xb0,'CMP':0xb8}[op]+reg(a[0],REG)]
    if op == 'RST':
        count(1)
        v = evaluate(a[0])
        if v > 7: raise AssemblyError('RST vector must be 0 through 7')
        return [0xc7+8*v]
    if op in IMM8 or op in ADDR:
        # Opcodes used as immediate operands may contain commas.
        if op in IMM8 and len(a) > 1: a = [arg]
        count(1)
        v = evaluate(a[0])
        return [IMM8[op],byte(v)] if op in IMM8 else [ADDR[op],v&255,v>>8]
    raise AssemblyError(f'unsupported instruction {op}')


@dataclass(frozen=True)
class Line:
    text: str
    path: Path
    number: int
    trace: tuple = ()

    def at(self):
        origin = f'{self.path}:{self.number}'
        return origin + ((' (expanded from '+ ' -> '.join(self.trace)+')') if self.trace else '')


def source_lines(lines, path):
    """Remove extended comments before block matching; preserve line numbers."""
    result, delimiter = [], None
    for n, text in enumerate(lines, 1):
        eof = '\x1a' in text
        text = text.split('\x1a', 1)[0]  # CP/M text end marker
        if delimiter:
            if delimiter in text:
                delimiter = None
            continue
        m = re.match(r'\s*\.COMMENT\s+(\S)(.*)', text, re.I)
        if m:
            if m[1] not in m[2]: delimiter = m[1]
            continue
        result.append(Line(text, path, n))
        if eof: break
    if delimiter:
        raise AssemblyError(f'{path}: unterminated .COMMENT')
    return result


def unbracket(s, required=False):
    if s.startswith('<') and s.endswith('>'):
        return s[1:-1]
    if required:
        raise AssemblyError('argument must be enclosed in angle brackets')
    return s


def substitute(text, mapping, short=False):
    """Token substitution, with & required for substitution inside quotes."""
    text = strip_comment(text)
    out, i, quote = [], 0, None
    while i < len(text):
        c = text[i]
        if c in "'\"":
            if quote == c and i+1 < len(text) and text[i+1] == c:
                out.append(c*2); i += 2; continue
            quote = None if quote == c else (c if quote is None else quote)
            out.append(c); i += 1; continue
        amp = c == '&'
        start = i+1 if amp else i
        m = re.match(SYMBOL, text[start:])
        if m:
            token = m[0]
            replacement = mapping.get(token.upper()[:6] if short else token.upper())
            if replacement is not None and (not quote or amp):
                out.append(replacement)
            else:
                out.append(('&' if amp and quote else '')+token)
            i = start+len(token)
        elif amp and not quote:
            i += 1
        else:
            out.append(c); i += 1
    return ''.join(out)


class ExitBlock(Exception):
    pass


class EndSource(Exception):
    pass


class Assembler:
    def __init__(self, *, include_dirs=(), m80_symbols=False, max_expansion=100000,
                 max_depth=64, printer=print):
        self.include_dirs = [Path(p) for p in include_dirs]
        self.short = m80_symbols
        self.max_expansion, self.max_depth = max_expansion, max_depth
        self.printer = printer
        self.files = {}

    def key(self, name):
        return symbol(name, self.short)

    def lookup(self, name):
        key = self.key(name)
        if key in self.syms: return self.syms[key]
        if key in self.previous: return self.previous[key]
        raise UnknownSymbol(f'undefined symbol {name}')

    @property
    def pc(self):
        return self.location + self.phase

    def value(self, text, unknown=False, radix=None):
        if re.match(r'^NUL(?:\s|$)', text, re.I):
            return 0xffff if not text[3:].strip() else 0
        try:
            return Expression(text, self.lookup, self.pc, radix or self.radix).evaluate()
        except UnknownSymbol:
            if unknown and self.passno == 1: return 0
            raise

    def define(self, name, value, kind):
        if not 0 <= value <= 65535:
            raise AssemblyError(f'symbol {name} is outside the 16-bit address space')
        key = self.key(name)
        if key in self.pending:
            raise AssemblyError(f'duplicate or incompatible definition of {name}')
        if key in self.syms:
            if kind == 'SET': pass
            elif kind == 'EQU' and self.syms[key] == value: return
            else: raise AssemblyError(f'duplicate or incompatible definition of {name}')
        if self.passno == 2 and kind != 'SET' and key in self.phase_definitions and self.phase_definitions[key] != value:
            raise AssemblyError(f'phase error: {name} changed between passes')
        if self.passno == 1 and kind != 'SET': self.phase_definitions.setdefault(key,value)
        self.syms[key], self.kinds[key] = value, kind

    def emit(self, data):
        if self.location < 0 or self.location+len(data) > 0x10000 or self.pc+len(data) > 0x10000:
            raise AssemblyError('code exceeds the 16-bit address space')
        for b in data:
            if self.location in self.mem:
                raise AssemblyError(f'overlapping output at {self.location:04X}H')
            self.mem[self.location] = b
            self.location += 1

    def condition(self, op, arg):
        if op in ('IF1','IF2'):
            if arg: raise AssemblyError(f'{op} takes no arguments')
            return self.passno == int(op[-1])
        if op in ('IFDEF','IFNDEF'):
            key = self.key(arg.strip())
            found = key in self.syms or key in self.previous
            return found if op == 'IFDEF' else not found
        if op in ('IFB','IFNB'):
            blank = not unbracket(arg.strip(), True).strip()
            return blank if op == 'IFB' else not blank
        if op in ('IFIDN','IFDIF'):
            args = split_args(arg, True)
            if len(args) != 2: raise AssemblyError(f'{op} expects two arguments')
            same = unbracket(args[0], True) == unbracket(args[1], True)
            return same if op == 'IFIDN' else not same
        truth = self.value(arg) != 0
        return not truth if op in ('IFE','IFF') else truth

    def macro_argument(self, arg):
        if arg.startswith('%'):
            value = self.value(arg[1:])
            digits = '0123456789ABCDEF'
            out = ''
            while value:
                out = digits[value % self.radix]+out
                value //= self.radix
            return out or '0'
        return re.sub(r'!(.)', r'\1', unbracket(arg))

    def expand(self, body, mapping, call):
        return [Line(substitute(line.text, mapping, self.short), line.path, line.number,
                     line.trace+(call.at(),)) for line in body]

    def invoke(self, body, mapping, call, depth, macro=False):
        if macro:
            body = list(body)
            while body:
                label, op, arg = parse_line(body[0].text)
                if not op and not label:
                    body.pop(0); continue
                if op != 'LOCAL': break
                if label: raise AssemblyError('LOCAL cannot have a label')
                names = split_args(arg)
                if not names: raise AssemblyError('LOCAL needs a symbol list')
                for name in names:
                    key = self.key(name)
                    if key in mapping: raise AssemblyError(f'duplicate LOCAL/parameter {name}')
                    self.local_id += 1
                    if self.local_id > 65535: raise AssemblyError('too many LOCAL symbols')
                    mapping[key] = f'..{self.local_id:04X}'
                body.pop(0)
        self.process(self.expand(body, mapping, call), depth+1, True)

    def process(self, lines, depth=0, in_block=False):
        if depth > self.max_depth: raise AssemblyError('macro/include nesting limit exceeded')
        stack, i = [], 0
        while i < len(lines):
            line = lines[i]
            i += 1
            self.steps += 1
            if self.steps > self.max_expansion: raise AssemblyError('expanded source limit exceeded')
            try:
                label, op, arg = parse_line(line.text)
                active = all(entry[0] for entry in stack)
                if op in IFS:
                    stack.append([self.condition(op, arg) if active else False, active, False])
                    continue
                if op in ('ELSE','ENDIF'):
                    if label or arg: raise AssemblyError(f'{op} takes no label or arguments')
                    if not stack: raise AssemblyError(f'{op} without IF')
                    if op == 'ENDIF': stack.pop()
                    else:
                        if stack[-1][2]: raise AssemblyError('duplicate ELSE')
                        stack[-1] = [stack[-1][1] and not stack[-1][0], stack[-1][1], True]
                    continue
                if op in BLOCKS:
                    start, nesting = i, 1
                    while i < len(lines):
                        _, nested_op, _ = parse_line(lines[i].text)
                        i += 1
                        if nested_op in BLOCKS: nesting += 1
                        if nested_op == 'ENDM': nesting -= 1
                        if not nesting: break
                    if nesting: raise AssemblyError(f'unterminated {op} block')
                    body = lines[start:i-1]
                    if not active: continue
                    if op == 'MACRO':
                        if not label: raise AssemblyError('MACRO needs a name')
                        params = [self.key(p) for p in split_args(arg)]
                        if len(set(params)) != len(params): raise AssemblyError('duplicate macro parameter')
                        self.macros[self.key(label)] = (params, body)
                    else:
                        if label: self.define(label, self.pc, 'LABEL')
                        if op == 'REPT':
                            mappings = ({} for _ in range(self.value(arg)))
                        else:
                            args = split_args(arg, True)
                            if len(args) != 2: raise AssemblyError(f'{op} expects dummy and argument list')
                            dummy = self.key(args[0])
                            text = unbracket(args[1], op == 'IRP')
                            values = (split_args(text, True) or ['']) if op == 'IRP' else list(text)
                            mappings = ({dummy:self.macro_argument(v)} for v in values)
                        try:
                            for mapping in mappings: self.invoke(body, mapping, line, depth)
                        except ExitBlock: pass
                    continue
                if not active: continue
                if op in ('EQU','SET'):
                    if not label: raise AssemblyError(f'{op} needs a name')
                    try:
                        self.define(label, self.value(arg), op)
                    except UnknownSymbol:
                        if op != 'EQU' or self.passno != 1: raise
                        key = self.key(label)
                        if key in self.syms or key in self.pending:
                            raise AssemblyError(f'duplicate definition of {label}')
                        self.pending[key] = (arg,self.pc,self.radix,dict(self.syms),line)
                    continue
                if label: self.define(label, self.pc, 'LABEL')
                if not op: continue
                macro_key = op[:6] if self.short else op
                if macro_key in self.macros:
                    params, body = self.macros[macro_key]
                    actual = [self.macro_argument(a) for a in split_args(arg, True)]
                    mapping = dict(zip(params, actual + ['']*len(params)))
                    try: self.invoke(body, mapping, line, depth, True)
                    except ExitBlock: pass
                elif op == 'EXITM':
                    if arg or not in_block: raise AssemblyError('EXITM requires a block and no operands')
                    raise ExitBlock()
                elif op in ('ENDM','LOCAL'):
                    raise AssemblyError(f'misplaced {op}')
                else:
                    self.statement(op, arg, line, depth)
            except EndSource:
                if stack: raise AssemblyError(f'{line.at()}: unterminated conditional before END') from None
                raise
            except AssemblyError as e:
                raise AssemblyError(f'{line.at()}: {e}') from None
        if stack: raise AssemblyError(f'{lines[-1].at()}: unterminated conditional')

    def statement(self, op, arg, line, depth):
        if op == 'END':
            if arg: self.value(arg, unknown=True)
            self.ended = True
            raise EndSource()
        if op == 'ORG':
            if self.phased: raise AssemblyError('ORG inside .PHASE is not supported')
            self.location = self.value(arg)
        elif op == '.PHASE':
            if self.phased: raise AssemblyError('nested .PHASE is not allowed')
            self.phase = self.value(arg)-self.location
            self.phased = True
        elif op == '.DEPHASE':
            if arg or not self.phased: raise AssemblyError('unmatched .DEPHASE or unexpected operand')
            self.phase, self.phased = 0, False
        elif op in ('ASEG','CSEG','DSEG'):
            if arg or self.phased: raise AssemblyError('invalid segment switch')
            self.counters[self.segment] = self.location
            self.segment = op
            self.location = self.counters.get(op, 0)
        elif op in ('EXTRN','COMMON','.REQUEST','REQUEST') or '##' in arg:
            raise AssemblyError(f'{op}: external linkage/common libraries require a linker and are unsupported')
        elif op == 'PUBLIC':
            names = split_args(arg)
            if not names: raise AssemblyError('PUBLIC requires symbols')
            self.public.update(self.key(n) for n in names)
        elif op == '.RADIX':
            radix = self.value(arg, radix=10)
            if not 2 <= radix <= 16: raise AssemblyError('.RADIX must be 2 through 16')
            self.radix = radix
        elif op == 'INCLUDE':
            name = unbracket(arg.strip())
            if string_value(name) is not None: name = string_value(name)
            if not name: raise AssemblyError('INCLUDE requires a filename')
            path = Path(name)
            candidates = [line.path.parent / path] + [d/path for d in self.include_dirs]
            candidates += [p.with_suffix('.mac') for p in candidates if not p.suffix]
            found = next((p.resolve() for p in candidates if p.is_file()), None)
            if found is None: raise AssemblyError(f'include file not found: {name}')
            if found in self.include_stack: raise AssemblyError(f'include cycle: {found}')
            if found not in self.files:
                self.files[found] = source_lines(found.read_text(encoding='utf-8-sig').splitlines(), found)
            self.include_stack.append(found)
            try: self.process(self.files[found], depth+1)
            finally: self.include_stack.pop()
        elif op in LISTING or op == '.8080':
            if arg: raise AssemblyError(f'{op} takes no operands')
        elif op == '.Z80':
            raise AssemblyError('Z80 instruction mode is not supported; use .8080')
        elif op in ('TITLE','SUBTTL','NAME'):
            pass  # No listing or object-module metadata in the existing formats.
        elif op in ('PAGE','.PAGE'):
            if arg and not 10 <= self.value(arg) <= 255: raise AssemblyError('PAGE size must be 10 through 255')
        elif op == '.PRINTX':
            if not arg or arg[0] not in arg[1:]: raise AssemblyError('unterminated .PRINTX delimiter')
            self.printer(arg[1:arg.index(arg[0],1)])
        elif op == 'DS':
            self.emit([0]*self.value(arg))
        elif op in ('DB','DW','DC'):
            args = split_args(arg)
            # A bare MOV opcode operand consumes its register-separating comma.
            merged = []
            i = 0
            while i < len(args):
                a = args[i]
                if re.search(r'\bMOV\s+[A-Za-z]+\s*$', a, re.I) and i+1 < len(args):
                    i += 1
                    a += ','+args[i]
                merged.append(a)
                i += 1
            args = merged
            if not args or any(not a for a in args): raise AssemblyError(f'{op} requires data')
            if op == 'DC':
                if len(args) != 1: raise AssemblyError('DC requires one string')
                s = string_value(args[0])
                if not s: raise AssemblyError('DC requires a nonempty string')
                data = list(s.encode('ascii')); data[-1] |= 128
                self.emit(data)
            else:
                for a in args:
                    s = string_value(a)
                    if op == 'DB' and s is not None: self.emit(s.encode('ascii'))
                    else:
                        v = self.value(a, unknown=True)
                        self.emit([byte(v)] if op == 'DB' else [v&255,v>>8])
        else:
            instructions = set(SINGLE) | set(IMM8) | set(ADDR) | {'MOV','MVI','LXI','INX','DCX','DAD','INR','DCR','ADD','ADC','SUB','SBB','ANA','XRA','ORA','CMP','PUSH','POP','LDAX','STAX','RST'}
            if op in instructions:
                self.emit(encode(op,arg,lambda s:self.value(s,unknown=True)))
            else:
                # M80 permits an expression in the operation field as implicit DB.
                raw = strip_comment(line.text)
                raw = re.sub(r'^'+SYMBOL+r'::?\s*', '', raw)
                self.statement('DB', raw, line, depth)

    def assemble(self, lines, source_path='<string>'):
        path = Path(source_path).resolve()
        source = source_lines(list(lines), path)
        self.previous = {}
        self.phase_definitions = {}
        first_addresses = None
        for self.passno in (1, 2):
            self.syms, self.kinds, self.macros, self.mem = {}, {}, {}, {}
            self.pending = {}
            self.public, self.counters = set(), {}
            self.segment, self.location, self.phase = 'CSEG', 0, 0
            self.phased, self.ended = False, False
            self.radix, self.local_id, self.steps = 10, 0, 0
            self.include_stack = [path]
            try: self.process(source)
            except EndSource: pass
            if self.phased: raise AssemblyError('unterminated .PHASE')
            if self.passno == 1:
                while self.pending:
                    progress = False
                    for key, (arg,pc,radix,snapshot,line) in list(self.pending.items()):
                        def lookup(name):
                            k = self.key(name)
                            if k in snapshot: return snapshot[k]
                            return self.lookup(name)
                        try: value = Expression(arg,lookup,pc,radix).evaluate()
                        except UnknownSymbol: continue
                        except AssemblyError as e:
                            raise AssemblyError(f'{line.at()}: {e}') from None
                        del self.pending[key]
                        self.define(key,value,'EQU')
                        progress = True
                    if not progress:
                        key = next(iter(self.pending))
                        raise AssemblyError(f'{self.pending[key][-1].at()}: unresolved or cyclic EQU {key}')
                self.previous = dict(self.syms)
                first_addresses = set(self.mem)
            elif set(self.mem) != first_addresses:
                raise AssemblyError('phase error: emitted addresses differ between passes')
            elif set(self.syms) != set(self.previous):
                raise AssemblyError('phase error: defined symbols differ between passes')
        for key in self.public:
            if key not in self.syms: raise AssemblyError(f'undefined PUBLIC symbol {key}')
        if not self.mem: return 0, -1, b'', self.syms
        lo, hi = min(self.mem), max(self.mem)
        return lo, hi, bytes(self.mem.get(x,0) for x in range(lo,hi+1)), self.syms


def assemble(lines, **options):
    """Return (lowest address, highest address, flat bytes, symbol dict)."""
    source_path = options.pop('source_path', '<string>')
    return Assembler(**options).assemble(lines, source_path)


def intel_hex(base, data):
    if base < 0 or base+len(data) > 65536:
        raise AssemblyError('Intel HEX output exceeds the 16-bit address space')
    rows = []
    for off in range(0,len(data),16):
        chunk, addr = data[off:off+16], base+off
        rec = [len(chunk),addr>>8,addr&255,0,*chunk]
        rows.append(':'+''.join(f'{x:02X}' for x in rec+[(-sum(rec))&255]))
    return '\n'.join(rows+[':00000001FF'])+'\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output_stem', type=Path)
    parser.add_argument('-I','--include-dir', action='append', default=[])
    parser.add_argument('--m80-symbols', action='store_true', help='use only the first six characters of symbols')
    args = parser.parse_args(argv)
    try:
        assembler = Assembler(include_dirs=args.include_dir, m80_symbols=args.m80_symbols)
        lo, hi, data, syms = assembler.assemble(args.source.read_text(encoding='utf-8-sig').splitlines(),
                                              source_path=args.source)
        outputs = {args.output_stem.with_suffix('.bin'):data,
                   args.output_stem.with_suffix('.hex'):intel_hex(lo,data).encode('ascii'),
                   args.output_stem.with_suffix('.sym'):'\n'.join(f'{k}={v:04X}' for k,v in sorted(syms.items())).encode('ascii')}
        inputs = {args.source.resolve(), *assembler.files}
        if inputs.intersection(p.resolve() for p in outputs):
            raise AssemblyError('output path would overwrite the input source')
        args.output_stem.parent.mkdir(parents=True,exist_ok=True)
        for path, payload in outputs.items(): path.write_bytes(payload)
        print(f'assembled {len(data)} bytes: {lo:04X}-{hi:04X}' if data else 'assembled 0 bytes (empty image)')
        return 0
    except (AssemblyError,OSError,UnicodeError) as e:
        print(f'error: {e}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

