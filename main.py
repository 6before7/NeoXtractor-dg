"""NeoXtractor entrypoint"""

import multiprocessing

from core.args import arguments, parse_args
from core.logger import setup_logger
from gui import run as run_gui

def run_cli():
    """Run NeoXtractor as a CLI application."""
    raise NotImplementedError("CLI mode is not implemented yet.")

if __name__ == "__main__":
    multiprocessing.freeze_support()  # worker processes of the script decompiler in frozen builds
    parse_args()
    setup_logger()
    if arguments.subcommand == "gui" or arguments.subcommand is None:
        run_gui()
    elif arguments.subcommand == "decompile":
        from npkscript.cli import main as decompile_main
        decompile_main(arguments.decompile_args)
