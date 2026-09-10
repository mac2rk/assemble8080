import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from assemble8080 import AssemblyError, assemble, intel_hex, main


def build(source, **kwargs):
    return assemble(source.splitlines(), printer=lambda _: None, **kwargs)


class AssemblerTests(unittest.TestCase):
    def test_printx_semicolons_in_macro_and_repeat_expansion(self):
        source = '''INNER MACRO X
IF1
.PRINTX /X; middle;; end/ ; trailing comment
ENDIF
ENDM
OUTER MACRO Y
REPT 2
INNER Y
ENDM
ENDM
OUTER hello
DB 42 ; regular comments still work
'''
        messages = []
        result = assemble(source.splitlines(), printer=messages.append)
        self.assertEqual(messages, ['hello; middle;; end'] * 2)
        self.assertEqual(result[2], b'*')

    def test_printx_semicolon_delimiter_and_irp(self):
        messages = []
        source = '''IRP X,<one,two>
.PRINTX ;X;
ENDM
'''
        assemble(source.splitlines(), printer=messages.append)
        self.assertEqual(messages, ['one', 'two'] * 2)

    def test_original_api_and_formats(self):
        lo, hi, data, syms = build('ORG 100H\nSTART: MVI A,42H\nSTA 200H\nJMP START\nEND')
        self.assertEqual((lo, hi, data, syms), (256,263,bytes.fromhex('3E42320002C30001'),{'START':256}))
        self.assertEqual(intel_hex(lo,data), ':080100003E42320002C300017F\n:00000001FF\n')

    def test_manual_repeat_examples(self):
        sources = [
            'X SET 0\nREPT 10\nX SET X+1\nDB X\nENDM',
            'IRP X,<1,2,3,4,5,6,7,8,9,10>\nDB X\nENDM',
            'IRPC X,0123456789\nDB X+1\nENDM',
            'FOO MACRO X\nY SET 0\nREPT X\nY SET Y+1\nDB Y\nENDM\nENDM\nFOO 10',
            'FOO MACRO X\nIRP Y,<X>\nDB Y\nENDM\nENDM\nFOO <1,2,3,4,5,6,7,8,9,10>',
        ]
        for source in sources:
            with self.subTest(source=source): self.assertEqual(build(source)[2],bytes(range(1,11)))

    def test_locals_and_quote_substitution(self):
        source = '''ORG 100H
M MACRO X
LOCAL AGAIN
AGAIN: MVI A,'&X'
JMP AGAIN
DB 'X'
ENDM
M a
M b
'''
        self.assertEqual(build(source)[2], bytes.fromhex('3E61C30001583E62C3060158'))

    def test_call_by_value_and_concatenation(self):
        source = '''MAKLAB MACRO Y
ERR&Y: DB 'Error &Y',0
ENDM
LB SET 0
REPT 3
LB SET LB+1
MAKLAB %LB
ENDM'''
        result = build(source)
        self.assertEqual(result[2], b'Error 1\0Error 2\0Error 3\0')
        self.assertEqual([result[3][f'ERR{i}'] for i in range(1,4)],[0,8,16])

    def test_exitm_in_nested_repeat(self):
        source = '''REPT 2
REPT 5
DB 1
EXITM
DB 99
ENDM
DB 2
ENDM'''
        self.assertEqual(build(source)[2], b'\1\2\1\2')

    def test_null_parameters_and_extra_parameters(self):
        source = '''M MACRO X,Y
IFB <X>
DB 1
ELSE
DB X
ENDIF
IFNB <Y>
DB Y
ENDIF
ENDM
M
M ,2,99
M 3'''
        self.assertEqual(build(source)[2],bytes([1,1,2,3]))

    def test_conditional_variants(self):
        source = '''IF 0
IF UNDEFINED
BAD
ENDIF
REPT UNKNOWN
BAD
ENDM
ELSE
DB 1
ENDIF
IFIDN <abc>,<abc>
DB 2
ENDIF
IFDIF <abc>,<ABC>
DB 3
ENDIF
IFE 0
DB 4
ENDIF
IFF 0
DB 5
ENDIF
IF NUL
DB 6
ENDIF'''
        self.assertEqual(build(source)[2], bytes(range(1,7)))

    def test_pass_conditionals(self):
        source = 'IF1\n.PRINTX /one/\nENDIF\nIF2\n.PRINTX /two/\nENDIF\nDB 1'
        output = []
        assemble(source.splitlines(), printer=output.append)
        self.assertEqual(output, ['one','two'])

    def test_expressions(self):
        source = '''DW 2+3*4,(2+3)*4,NOT 1 EQ 1,-2*3,1 SHL 8
DW 9 MOD 4,7/2,LOW 1234H,HIGH 1234H,1 LT 2,1 OR 2 XOR 3
DW 'AB',X'ABCD',101B,12Q,12O,12D
.RADIX 16
DW 10,0FF
.RADIX 10
DW 10
'''
        expected = [14,20,0,65530,256,1,3,52,18,65535,0,0x4142,0xabcd,5,10,10,12,16,255,10]
        self.assertEqual(build(source)[2], b''.join(v.to_bytes(2,'little') for v in expected))

    def test_strings_and_dc(self):
        self.assertEqual(build('DB \'A;B\',"x""y",\'\',\'AB\' AND 0FFH\nDC \'hi\'')[2],b'A;Bx"yBh\xe9')

    def test_opcode_operands(self):
        self.assertEqual(build('MVI A,(JMP)\nMVI B,RNZ\nMVI C,MOV A,B\nDB (INR C)\nDB (LXI H)')[2],bytes.fromhex('3EC306C00E780C21'))
        self.assertEqual(build('DB MOV A,B,1,MOV C,D')[2],bytes.fromhex('78014A'))

    def test_macro_escaping_nested_arguments_and_shadowing(self):
        source = '''INNER MACRO X
DB '&X'
ENDM
OUTER MACRO X
INNER <X>
ENDM
OUTER <!;>
IRP X,<<a,b>,<c,d>>
INNER <X>
ENDM
NOP MACRO
DB 42
ENDM
NOP'''
        self.assertEqual(build(source)[2],b';a,bc,d*')

    def test_type_and_printx(self):
        messages = []
        result = assemble(['.PRINTX /hello; world/', 'DB TYPE MISSING,TYPE (1/0),TYPE 1'],printer=messages.append)
        self.assertEqual(result[2],b'\0\0\x20')
        self.assertEqual(messages,['hello; world']*2)

    def test_rst_vectors(self):
        self.assertEqual(build('\n'.join(f'RST {n}' for n in range(8)))[2],bytes(range(0xc7,0x100,8)))

    def test_instruction_families(self):
        source = '''NOP
MOV A,M
MVI M,-1
LXI SP,1234H
INX H
DCX D
DAD B
INR C
DCR M
ADD B
ADC C
SUB D
SBB E
ANA H
XRA L
ORA M
CMP A
PUSH PSW
POP H
LDAX D
STAX B
IN 1
OUT 2
CALL 4567H
RET'''
        self.assertEqual(build(source)[2],bytes.fromhex('007E36FF313412231B090C358089929BA4ADB6BFF5E11A02DB01D302CD6745C9'))

    def test_forward_equ_and_type(self):
        result = build('VALUE EQU THERE+1\nDW VALUE\nTHERE: DB 1\nDB TYPE (THERE+1),TYPE (MISSING+1)')
        self.assertEqual(result[2],bytes.fromhex('0300012000'))
        with self.assertRaisesRegex(AssemblyError,'cyclic EQU'):
            build('FIRST EQU SECOND\nSECOND EQU FIRST')
        self.assertEqual(build('X EQU 1\nDB X\nX SET 2\nDB X')[2],b'\1\2')

    def test_implicit_db_case_and_bracketed_semicolon(self):
        self.assertEqual(build("'abc'")[2],b'abc')
        source = "M MACRO X\nDB '&X'\nENDM\nN MACRO\nM <a;b>\nENDM\nN"
        self.assertEqual(build(source)[2],b'a;b')

    def test_end_does_not_hide_unterminated_if(self):
        with self.assertRaisesRegex(AssemblyError,'unterminated conditional'):
            build('IF 1\nDB 1\nEND')

    def test_phase_and_segment_counters(self):
        source = '''ASEG
ORG 100H
.PHASE 8000H
HERE: JMP HERE
.DEPHASE
DB 1
DSEG
ORG 110H
DATA: DW HERE
ASEG
AFTER: DB 2'''
        lo,hi,data,syms = build(source)
        self.assertEqual((lo,hi),(0x100,0x111))
        self.assertEqual(data[:5],bytes.fromhex('C300800102'))
        self.assertEqual(data[-2:],bytes.fromhex('0080'))
        self.assertEqual(syms,{'HERE':0x8000,'DATA':0x110,'AFTER':0x104})

    def test_include_and_diagnostics(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            (path/'defs.mac').write_text('VALUE EQU 42\n')
            self.assertEqual(build('INCLUDE defs\nDB VALUE', source_path=path/'main.asm')[2],b'*')
            (path/'defs.mac').write_text('INCLUDE defs\n')
            with self.assertRaisesRegex(AssemblyError,'include cycle'):
                build('INCLUDE defs',source_path=path/'main.asm')
            (path/'defs.mac').write_text('MVI Q,1\n')
            with self.assertRaisesRegex(AssemblyError,r'defs.mac:1.*invalid MVI register'):
                build('INCLUDE defs',source_path=path/'main.asm')

    def test_hardening_errors(self):
        cases = {
            'NOP 1':'expects 0', 'MVI A':'expects 2', 'MOV M,M':'encodes HLT',
            'STAX H':'invalid STAX', 'RST 8':'RST vector', 'DB 256':'out of range',
            'DB 8Q':'invalid number', 'DB 1/0':'division by zero', 'DB missing':'undefined symbol',
            'X: NOP\nX: RET':'duplicate', 'DS FUTURE\nFUTURE EQU 2':'undefined symbol',
            'ORG FUTURE\nFUTURE: NOP':'undefined symbol', 'ORG 0FFFFH\nDW 1':'address space',
            'DB 1\nORG 0\nDB 1':'overlapping', 'IF 1\nDB 1':'unterminated conditional',
            'ELSE':'without IF', 'IF 1\nELSE\nELSE\nENDIF':'duplicate ELSE',
            'REPT 2\nDB 1':'unterminated REPT', 'ENDM':'misplaced', 'EXITM':'requires a block',
            'LOCAL X':'misplaced', 'DC \'\'':'nonempty', '.RADIX 1':'2 through 16',
            '.PHASE 100H\nDB 1':'unterminated .PHASE', '.DEPHASE':'unmatched',
            'EXTRN X':'require a linker', '.Z80':'not supported', 'DB 1,':'requires data',
        }
        for source, message in cases.items():
            with self.subTest(source=source), self.assertRaisesRegex(AssemblyError,message): build(source)

    def test_expansion_limit(self):
        with self.assertRaisesRegex(AssemblyError,'nesting limit'):
            build('M MACRO\nM\nENDM\nM',max_depth=5)
        with self.assertRaisesRegex(AssemblyError,'source limit'):
            build('REPT 100\nDB 1\nENDM',max_expansion=10)

    def test_symbol_policy(self):
        self.assertEqual(build('LongNameA EQU 1\nLongNameB EQU 2\nDB LongNameA,LongNameB')[2],b'\1\2')
        with self.assertRaisesRegex(AssemblyError,'duplicate'):
            build('LongNameA EQU 1\nLongNameB EQU 2',m80_symbols=True)
        self.assertEqual(build('@FOO EQU 1\n?BAR EQU 2\n_X EQU 3\nDB @FOO,?BAR,_X')[2],b'\1\2\3')

    def test_empty_and_public(self):
        self.assertEqual(build('END'),(0,-1,b'',{}))
        self.assertEqual(intel_hex(0,b''),':00000001FF\n')
        self.assertEqual(build('PUBLIC FOO\nFOO:: RET')[2],b'\xc9')
        with self.assertRaisesRegex(AssemblyError,'undefined PUBLIC'): build('PUBLIC X')

    def test_comments_and_aliases(self):
        self.assertEqual(build('.COMMENT *\nENDM nonsense\n*\nDEFB 1\nDEFW 2\nDEFS 1\n.LIST')[2],b'\1\2\0\0')

    def test_cli_preserves_files_on_assembly_error(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            source = path/'input.asm'
            source.write_text('DB 1\n')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main([str(source),str(path/'out')]),0)
            saved = [(path/f'out.{ext}').read_bytes() for ext in ('bin','hex','sym')]
            source.write_text('MVI Q,5\n')
            with contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(main([str(source),str(path/'out')]),1)
            self.assertNotIn('Traceback',errors.getvalue())
            self.assertEqual(saved,[(path/f'out.{ext}').read_bytes() for ext in ('bin','hex','sym')])


if __name__ == '__main__':
    unittest.main()
