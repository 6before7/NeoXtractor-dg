"""`python -m npkscript script.npk OUT_DIR [options]`"""
import multiprocessing

from .cli import main

if __name__ == '__main__':
    multiprocessing.freeze_support()
    main()
