"""Run the project launcher when invoked from the frontend directory."""

import runpy
from pathlib import Path


runpy.run_path(str(Path(__file__).resolve().parent.parent / "dev.py"), run_name="__main__")
