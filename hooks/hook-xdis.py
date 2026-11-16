from PyInstaller.utils.hooks import collect_submodules

# xdis loads its per-version opcode tables dynamically.
hiddenimports = collect_submodules("xdis", on_error="warn once")
