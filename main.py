"""NeoXtractor entrypoint"""

import multiprocessing

from core.args import arguments, parse_args
from core.logger import setup_logger
from gui import run as run_gui

def run_cli():
    """Run NeoXtractor as a CLI application."""
    raise NotImplementedError("CLI mode is not implemented yet.")

if __name__ == "__main__":
    multiprocessing.freeze_support()  # worker processes (ID5 script decompile) in frozen builds
    parse_args()
    setup_logger()
    if arguments.subcommand == "gui" or arguments.subcommand is None:
        run_gui()
