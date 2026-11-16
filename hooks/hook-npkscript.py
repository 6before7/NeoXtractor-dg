from PyInstaller.utils.hooks import collect_data_files

# The stdlib reference and known maps, and the Python 2.7 helpers, which an external Python 2.7 runs as files.
datas = collect_data_files("npkscript", include_py_files=True, includes=["data/*", "helpers27/*.py"])
hiddenimports = ["uncompyle6.main"]
