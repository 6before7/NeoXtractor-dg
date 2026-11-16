from PyInstaller.utils.hooks import collect_submodules

# xdis loads per-version opcode tables dynamically.
hiddenimports = collect_submodules("xdis", on_error="warn once")
