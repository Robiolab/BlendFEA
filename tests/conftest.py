"""Make the repo importable for the tests: `blendfea` (bpy-free parts) from the root,
and the mesher packages (matching_library, step_converter) from blendfea/lib."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(ROOT, "blendfea", "lib")
FEA_DIR = os.path.join(ROOT, "blendfea", "fea")   # core.py imports fxclient as a plain module
for p in (ROOT, LIB_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)
