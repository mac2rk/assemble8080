# Standalone 8080 assembler

`assemble8080.py` is a Python 3.9+ assembler with no third-party dependencies.
It extends the original two-pass assembler using the source-language facilities
described in the supplied **Microsoft MACRO-80 Assembler, CP/M Version,
Software Reference Manual** (Heath/Zenith, 1981), particularly chapters 2–4.
It is an absolute-image assembler, not a complete M80 executable replacement.

```powershell
python assemble8080.py examples/macros.asm output/macros
python assemble8080.py program.asm output/program -I includes
python -m unittest -v
```

The original two positional arguments and output formats are preserved:

| File | Contents |
| --- | --- |
| `.bin` | Bytes from the lowest emitted address through the highest, with zero-filled gaps. No address header. |
| `.hex` | Intel HEX type-00 data records, up to 16 bytes each, followed by the type-01 EOF record. |
| `.sym` | Alphabetically sorted `NAME=HHHH` lines. Includes generated macro local symbols and final SET values. |

`END address` validates the address but adds no entry-point record or metadata.
`DS` remains zero-filled. An empty program produces an empty binary/symbol file
and an EOF-only HEX file. Source files use UTF-8 (an optional BOM is accepted);
assembly string data must be ASCII. Python 3.9 or later is required.

## Source features

| Facility | Implementation |
| --- | --- |
| Instructions | All documented 8080 instructions, including `RST 0` through `RST 7`. Intel register-pair spelling: `B`, `D`, `H`, `SP`; `PSW` for PUSH/POP. |
| Macros | `name MACRO parameters` / `ENDM`, nested expansion, missing/null arguments, ignored extra arguments, and macro calls overriding instruction mnemonics. |
| Macro locals | `LOCAL` declarations at the beginning of a macro body; unique `..0001`–`..FFFF` symbols for each expansion. |
| Repeat blocks | `REPT`, `IRP`, `IRPC`; nested blocks; `EXITM` exits the innermost macro/repeat invocation, including remaining repeat iterations. |
| Macro arguments | Nested angle brackets, `&` concatenation and quoted substitution, `!` character escaping, `%` evaluation in the current radix, and `NUL`. One bracket level is removed per argument use. |
| Conditionals | `IF`/`IFT`, `IFE`/`IFF`, `IF1`, `IF2`, `IFDEF`, `IFNDEF`, `IFB`, `IFNB`, `IFIDN`, `IFDIF`, `ELSE`, `ENDIF`. String comparisons preserve case. |
| Expressions | Parentheses; unary `+`, `-`, `NOT`, `HIGH`, `LOW`, `TYPE`; `*`, `/`, `MOD`, `SHL`, `SHR`, `+`, `-`, `EQ`, `NE`, `LT`, `LE`, `GT`, `GE`, `AND`, `OR`, `XOR`. M80 precedence and unsigned 16-bit arithmetic; true is `FFFFH`. |
| Constants | Decimal default; `.RADIX 2`–`.RADIX 16`; `B`, `D`, `O`, `Q`, `H` suffixes; `X'ABCD'`; current address `$`; one-/two-character expression strings. `.RADIX` operands are evaluated in decimal. |
| Opcode operands | First opcode byte as an expression, for example `MVI A,(JMP)`, `MVI C,MOV A,B`, `DB (LXI H)`. Immediate/address operands are not part of these expressions. |
| Symbols | Case-insensitive labels, `label::`, `EQU` including forward references, mutable `SET`, and the M80 `$`, `?`, `@`, `_` symbol characters. |
| Data | `DB`, `DW`, `DS`, `DC`; single/double-quoted strings with doubled delimiter escapes; expression/string statements as implicit `DB`. `DC` sets the last character's high bit. |
| Includes | `INCLUDE`, `$INCLUDE`, `MACLIB`; relative to the including file, then `-I` directories; `.mac` fallback extension. Quoted or angle-bracketed filenames accepted. |
| Addresses | `ORG`, `.PHASE`, `.DEPHASE`, `ASEG`, `CSEG`, `DSEG`; see absolute segment rules below. |
| Other source directives | `.8080`, `.COMMENT`, `.PRINTX`, `PUBLIC`/`ENTRY`, `END`. `.PRINTX` prints on each active pass; use `IF1`/`IF2` to select one. |
| Listing controls | `.LIST`, `.XLIST`, `.SFCOND`, `.LFCOND`, `.TFCOND`, `.LALL`, `.SALL`, `.XALL`, `PAGE`/`.PAGE`, `TITLE`, `SUBTTL`, `NAME` accepted; no listing/module metadata is emitted. |
| Directive aliases | `COND`, `ENDC`, `EJECT`, `DEFB`, `DEFM`, `DEFW`, `DEFS`, `DEFL`, `GLOBAL`. |

`PUBLIC` declarations must resolve in the current assembly. Double-colon labels
are ordinary defined labels in the flat output. Listing-only text is discarded.

## Absolute segment rules and compatibility limits

There is no linker, library manager, relocation stream, Z80 instruction encoder,
or 8085-specific instruction mode. `EXTRN`/`EXT`/`EXTERNAL`, external `##`
references, `COMMON`, `.REQUEST`/`REQUEST`, and `.Z80` are rejected. Combine
source modules with `INCLUDE` and define all referenced symbols locally.

`ASEG`, `CSEG` (initial selection), and `DSEG` each have a saved location counter,
initially zero. Their addresses are **absolute** in the same 64 KiB output image.
Use explicit `ORG` values to place different segments; overlapping emitted
addresses are errors. No automatic placement or relocation is performed.
Consequently `TYPE` returns `20H` for a valid defined expression and zero for an
undefined/invalid operand; it does not report code/data relocation modes.

`.PHASE address` changes the execution address used by labels and `$` while bytes
continue at the physical output address. `.DEPHASE` restores normal addressing.
Nested phases, segment switches, or `ORG` within a phase are rejected.

Compatibility choices relative to historical M80:

- Full symbol names remain significant by default to preserve the original
  assembler's behavior. Use `--m80-symbols` for M80's six-character significance
  for symbols, macro names, and parameters.
- Source lines have no historical 132-character limit. Nested includes are
  supported with cycle/depth checks, although this manual disallows nesting.
- `END` is optional, preserving the original script's behavior. CP/M Ctrl-Z
  terminates source input. Extended `.COMMENT` delimiters are handled before
  conditional/block parsing.
- `ORG`, `DS`, `SET`, radix changes, repeat counts, and numeric conditionals must
  be evaluable when encountered on pass 1. Forward instruction/data references
  and forward `EQU` definitions are supported. Cyclic/unresolved equates fail.
- `IFDEF`/`IFNDEF` can see pass-1 symbols during pass 2, as M80 does. If a branch
  changes emitted addresses or label values, assembly fails with a phase error.
  Prefer defining conditional switches before testing them.
- Arithmetic wraps to 16 bits as in M80. Byte operands require a high byte of
  `00H` or `FFH` (M80's rule), rather than silently truncating arbitrary values.
- `MOV M,M` is rejected because its encoding is `HLT`; write `HLT` explicitly.
- A `LOCAL` cannot duplicate a macro parameter. `SET` may change an existing
  symbol; conflicting `EQU` values and duplicate labels are errors.
- This tool does not implement M80 command strings, `.REL`, listings,
  cross-reference files, CP/M device names, or object-library searches.

## Diagnostics and Python API

Errors report the source path/line and macro expansion call chain. Validation
covers operand counts and registers, undefined/duplicate symbols, malformed
expressions and strings, division by zero, byte bounds, 64 KiB overflow,
overlapping output, conditional/block nesting, include cycles, and pass changes.
Default limits are 64 macro/include levels and 100,000 processed source lines
per pass. The CLI returns status 1 without a traceback on source or file errors.
Assembly errors leave existing output files untouched. Output cannot overwrite
the source or a loaded include. Output writes are not a transactional group;
an I/O failure during writing can leave a partially updated output set.

```python
from assemble8080 import assemble, intel_hex

lo, hi, image, symbols = assemble(
    ["ORG 100H", "MVI A,42H", "HLT", "END"],
    source_path="program.asm",  # base directory for relative includes
)
hex_text = intel_hex(lo, image)
```

The return tuple remains `(lowest_address, highest_address, bytes, symbols)`.
For empty output it is `(0, -1, b'', symbols)`. Optional keyword arguments are
`include_dirs`, `m80_symbols`, `max_depth`, `max_expansion`, and `printer`.
The `Assembler` class provides the same configurable interface. `.PRINTX`
uses the supplied `printer` callback (default: `print`).

Tests include the manual's macro/repeat examples, instruction and expression
encodings, local labels, nested arguments, conditionals, phased code, includes,
CLI formats, and error handling. The original 3,072-byte `radio86_basic.asm`
image was also reassembled and compared byte-for-byte with its existing binary.
