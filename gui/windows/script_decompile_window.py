"""Decompile the Python scripts of a loaded script.npk with npkscript (2017-2018 Identity V, NeoX Python 2.7).

The archive's entries are already decrypted by NeoXtractor; this dialog hands them to
`npkscript.pipeline.decompile_entries`, which recovers the opcode map, decompiles every module and, when a
Python 2.7 interpreter is available, verifies each function by recompilation. Game code is never executed.
"""

from __future__ import annotations

import hashlib
import os
import shutil

from PySide6 import QtCore, QtGui, QtWidgets

from npkscript import pipeline

PYTHON2_KEY = "scripts.python2"
UNCOMPYLE2_KEY = "scripts.uncompyle2"


class _Signals(QtCore.QObject):
    line = QtCore.Signal(str)
    finished = QtCore.Signal(str)


class _Job(QtCore.QRunnable):
    def __init__(self, entries, out, opts, path):
        super().__init__()
        self.entries, self.out, self.opts, self.path = entries, out, opts, path
        self.label = os.path.basename(path)
        self.signals = _Signals()

    def run(self):
        self.opts.log = self.signals.line.emit
        try:
            with open(self.path, "rb") as f:
                sha = hashlib.file_digest(f, "sha256").hexdigest()
            r = pipeline.decompile_entries(self.entries, self.out, self.opts, label=self.label, archive_sha256=sha)
            summary = f"{r['decompiled']} modules decompiled, {len(r['refused'])} refused"
            if r["functions"]:
                summary += f"; {r['verified']} of {r['functions']} functions verified"
            self.signals.finished.emit(summary)
        except Exception as e:  # pylint: disable=broad-exception-caught  # reported in the dialog
            self.signals.finished.emit(f"Failed: {e}")


class ScriptDecompileWindow(QtWidgets.QDialog):
    """Options, progress log and result of one decompile run."""

    def __init__(self, archive_file, settings_manager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Decompile Python Scripts")
        self.resize(720, 520)
        self.archive_file = archive_file
        self.settings = settings_manager

        name = os.path.splitext(os.path.basename(archive_file.file_path))[0]
        default_out = os.path.join(os.path.dirname(archive_file.file_path), name + "-decompiled")
        self.out = QtWidgets.QLineEdit(default_out)
        self.python2 = QtWidgets.QLineEdit(
            settings_manager.get(PYTHON2_KEY, None) or shutil.which("python2.7") or shutil.which("python2") or "")
        self.uncompyle2 = QtWidgets.QLineEdit(settings_manager.get(UNCOMPYLE2_KEY, None) or "")
        self.align = QtWidgets.QLineEdit()
        self.stdlib = QtWidgets.QCheckBox("Also decompile the bundled standard library (lib/)")
        self.python2.setPlaceholderText("optional: enables verification of every function")
        self.uncompyle2.setPlaceholderText("optional: folder of the uncompyle2 1.1 source (second decompiler)")
        self.align.setPlaceholderText("optional: a Dec 2017 script.npk, needed for Feb-May 2018 builds")

        form = QtWidgets.QFormLayout()
        form.addRow("Output folder", self._browse(self.out, folder=True))
        form.addRow("Python 2.7", self._browse(self.python2, folder=False))
        form.addRow("uncompyle2", self._browse(self.uncompyle2, folder=True))
        form.addRow("Align with", self._browse(self.align, folder=False))
        form.addRow("", self.stdlib)

        self.log = QtWidgets.QPlainTextEdit(readOnly=True)
        self.log.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont))
        self.start = QtWidgets.QPushButton("Decompile")
        self.open_out = QtWidgets.QPushButton("Open Output Folder")
        self.open_out.setEnabled(False)
        self.start.clicked.connect(self._start)
        self.open_out.clicked.connect(
            lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(self.out.text())))
        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.open_out)
        buttons.addWidget(self.start)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.log, 1)
        layout.addLayout(buttons)

    def _browse(self, edit: QtWidgets.QLineEdit, folder: bool) -> QtWidgets.QWidget:
        def pick():
            if folder:
                path = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose Folder", edit.text())
            else:
                path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Choose File", edit.text())
            if path:
                edit.setText(path)
        row = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(edit, 1)
        button = QtWidgets.QPushButton("Browse...")
        button.clicked.connect(pick)
        h.addWidget(button)
        return row

    def _start(self):
        out = self.out.text().strip()
        if os.path.exists(out):
            QtWidgets.QMessageBox.warning(self, "Output folder exists", "Choose a folder that does not exist yet.")
            return
        python2 = self.python2.text().strip() or None
        uncompyle2 = self.uncompyle2.text().strip() or None
        self.settings.set(PYTHON2_KEY, python2, save=True)
        self.settings.set(UNCOMPYLE2_KEY, uncompyle2, save=True)
        entries = [(e.file_signature, e.data) for e in self.archive_file.entries.values() if e.data]
        opts = pipeline.Options(python2=python2, uncompyle2=uncompyle2, include_stdlib=self.stdlib.isChecked(),
                                align_with=[self.align.text().strip()] if self.align.text().strip() else [])
        job = _Job(entries, out, opts, self.archive_file.file_path)
        job.signals.line.connect(self.log.appendPlainText)
        job.signals.finished.connect(self._finished)
        self.start.setEnabled(False)
        self.log.clear()
        QtCore.QThreadPool.globalInstance().start(job)

    def _finished(self, summary: str):
        self.log.appendPlainText(summary)
        self.start.setEnabled(True)
        self.open_out.setEnabled(os.path.isdir(self.out.text()))
