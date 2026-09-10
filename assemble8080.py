#!/usr/bin/env python3
"""Compatibility entry point for running directly from a source checkout."""
import sys
from pathlib import Path

# Permit legacy imports as well as direct execution without installation.
__path__ = [str(Path(__file__).resolve().parent / 'src' / 'assemble8080')]
if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))

from assemble8080.assembler import Assembler, AssemblyError, assemble, intel_hex
from assemble8080.cli import main

__all__ = ['Assembler', 'AssemblyError', 'assemble', 'intel_hex', 'main']

if __name__ == '__main__':
    sys.exit(main())
