"""Command-line interface for the 8080 assembler."""

import argparse
import sys
from pathlib import Path
from .assembler import Assembler, AssemblyError, intel_hex

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
