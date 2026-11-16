from PyInstaller.utils.hooks import collect_submodules

# uncompyle6 picks its scanner and parser modules by bytecode version at run time.
hiddenimports = collect_submodules("uncompyle6", on_error="warn once")
