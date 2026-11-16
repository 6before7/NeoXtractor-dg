"""ID5 script decompile dialog: the Python 2.7 scripts of a 2017-2018 Identity V ``script.npk``.

The opened archive's entries are already decrypted by NeoXtractor; ``id5mapping.scripts`` recovers the build's
opcode numbering, decompiles every module and, with a Python 2.7 interpreter, verifies each function by
recompiling it. Game code is never executed.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil

from PySide6 import QtCore, QtGui, QtWidgets

from core.logger import get_logger
from id5mapping.scripts import load_modules, recover_opcode_map
from id5mapping.scripts.decompile import decompile_scripts, read_archive

PYTHON2_KEY = "id5.scripts.python2"
UNCOMPYLE2_KEY = "id5.scripts.uncompyle2"


class _SignalHandler(logging.Handler):
    """Forwards log records from the worker thread to the dialog."""

    def __init__(self, signal: QtCore.SignalInstance):
        super().__init__(logging.INFO)
        self.signal = signal
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        self.signal.emit(self.format(record))


class Id5ScriptDecompileWindow(QtWidgets.QDialog):
    """Pick the output folder and optional helpers, then decompile the opened script.npk."""

    log_line = QtCore.Signal(str)
    finished = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None, archive_file, settings_manager):
        super().__init__(parent)
        self.setWindowTitle("ID5 Script Decompile")
        self.resize(760, 520)
        self.archive_file = archive_file
        self.settings_manager = settings_manager

        name = os.path.splitext(os.path.basename(archive_file.file_path))[0]
        self.output = QtWidgets.QLineEdit(
            os.path.join(os.path.dirname(archive_file.file_path), f"{name}-decompiled"))
        self.python2 = QtWidgets.QLineEdit(
            settings_manager.get(PYTHON2_KEY, None) or shutil.which("python2.7") or shutil.which("python2") or "")
        self.python2.setPlaceholderText("Optional: verifies every function by recompiling it")
        self.uncompyle2 = QtWidgets.QLineEdit(settings_manager.get(UNCOMPYLE2_KEY, None) or "")
        self.uncompyle2.setPlaceholderText("Optional: uncompyle2 1.1 source folder (second decompiler)")
        self.reference = QtWidgets.QLineEdit()
        self.reference.setPlaceholderText("Feb-May 2018 builds: a Dec 2017 script.npk to align with")
        self.include_stdlib = QtWidgets.QCheckBox("Also decompile the bundled standard library (lib/)")

        form = QtWidgets.QFormLayout()
        form.addRow("Output folder", self._with_browse(self.output, "Output Folder", folder=True))
        form.addRow("Python 2.7", self._with_browse(self.python2, "Python 2.7 Interpreter"))
        form.addRow("uncompyle2", self._with_browse(self.uncompyle2, "uncompyle2 Source Folder", folder=True))
        form.addRow("Align with", self._with_browse(self.reference, "Reference script.npk", "NPK (*.npk)"))
        form.addRow("", self.include_stdlib)

        self.log = QtWidgets.QPlainTextEdit(readOnly=True)
        self.log.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont))
        self.start_button = QtWidgets.QPushButton("Decompile")
        self.open_button = QtWidgets.QPushButton("Open Output Folder")
        self.open_button.setEnabled(False)
        self.start_button.clicked.connect(self.start)
        self.open_button.clicked.connect(
            lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(self.output.text())))
        self.log_line.connect(self.log.appendPlainText)
        self.finished.connect(self._done)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.start_button)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.log, 1)
        layout.addLayout(buttons)

    def _with_browse(self, line_edit: QtWidgets.QLineEdit, title: str, file_filter: str = "All Files (*)",
                     folder: bool = False) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(line_edit)
        button = QtWidgets.QPushButton("Browse...")

        def browse():
            if folder:
                path = QtWidgets.QFileDialog.getExistingDirectory(self, title, line_edit.text())
            else:
                path, _ = QtWidgets.QFileDialog.getOpenFileName(self, title, line_edit.text(), file_filter)
            if path:
                line_edit.setText(path)

        button.clicked.connect(browse)
        row.addWidget(button)
        return container

    def start(self) -> None:
        """Start the decompile on the thread pool; progress goes to the log pane."""
        out_dir = self.output.text().strip()
        if os.path.exists(out_dir):
            QtWidgets.QMessageBox.warning(
                self, "ID5 Script Decompile", "Choose an output folder that does not exist yet."
            )
            return
        python2 = self.python2.text().strip() or None
        uncompyle2 = self.uncompyle2.text().strip() or None
        reference = self.reference.text().strip() or None
        self.settings_manager.set(PYTHON2_KEY, python2, save=True)
        self.settings_manager.set(UNCOMPYLE2_KEY, uncompyle2, save=True)
        entries = [entry.data for entry in self.archive_file.entries.values() if entry.data]
        archive_path, include_stdlib = self.archive_file.file_path, self.include_stdlib.isChecked()

        def run():
            handler = _SignalHandler(self.log_line)
            scripts_logger = logging.getLogger("id5mapping.scripts")
            scripts_logger.addHandler(handler)
            try:
                references = []
                if reference:
                    reference_modules = read_archive(reference)
                    references.append((reference_modules, recover_opcode_map(reference_modules).mapping))
                with open(archive_path, "rb") as f:
                    sha256 = hashlib.file_digest(f, "sha256").hexdigest()
                summary = decompile_scripts(load_modules(entries), out_dir, label=os.path.basename(archive_path),
                                            archive_sha256=sha256, references=references, python2=python2,
                                            uncompyle2=uncompyle2, include_stdlib=include_stdlib)
                text = f"{summary['decompiled']} modules decompiled, {len(summary['refused'])} refused"
                if summary["verified_run"]:
                    text += f"; {summary['verified']} of {summary['functions']} functions verified"
                self.finished.emit(text)
            except Exception as e:  # pylint: disable=broad-exception-caught  # shown in the dialog
                get_logger().exception("Script decompile failed")
                self.finished.emit(f"Failed: {e}")
            finally:
                scripts_logger.removeHandler(handler)

        self.start_button.setEnabled(False)
        self.log.clear()
        QtCore.QThreadPool.globalInstance().start(run)

    def _done(self, text: str) -> None:
        self.log.appendPlainText(text)
        self.start_button.setEnabled(True)
        self.open_button.setEnabled(os.path.isdir(self.output.text()))
