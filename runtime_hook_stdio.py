"""
PyInstaller runtime hook: guarantee ``sys.stdout`` / ``sys.stderr`` exist.

The apps are built with ``console=False`` (no console window beside the GUI —
the log lives in the window's own console drop-down). In a windowed build there
is no attached console, so PyInstaller leaves ``sys.stdout`` and ``sys.stderr``
set to ``None``.

``print()`` copes with that (CPython makes it a no-op), but plenty of code does
not:

* ``Table_image_extractor`` calls ``sys.stdout.isatty()`` at **module level** —
  an ``AttributeError`` there kills the app during import, before any window is
  shown and with nowhere to report it.
* ``logging.StreamHandler(sys.stdout)`` and the progress bars inside docling /
  transformers / huggingface_hub write to the stream directly.

This hook runs before any application code is imported and replaces a missing
stream with a sink that swallows writes. The main window still redirects
``sys.stdout``/``sys.stderr`` into its in-app console once it starts, so real
output is not lost — this only covers the gap before that, and the libraries
that keep a reference to the original stream.

``sys.__stdout__`` / ``sys.__stderr__`` are filled in too: ``crash_logger``
falls back to ``sys.__stderr__`` when reporting a crash, precisely because the
window has taken over ``sys.stderr``.
"""

import sys


class _NullStream:
    """A writable stream that discards everything, quietly."""

    encoding = "utf-8"
    errors = "replace"

    def write(self, text):          # noqa: D102 - stream protocol
        return len(text) if text else 0

    def writelines(self, lines):    # noqa: D102
        for line in lines:
            self.write(line)

    def flush(self):                # noqa: D102
        pass

    def close(self):                # noqa: D102
        pass

    def isatty(self):               # noqa: D102 - the call that used to crash
        return False

    def fileno(self):               # noqa: D102
        # Some libraries probe fileno() and expect failure on a non-file
        # stream; OSError is what they are written to handle.
        raise OSError("no file descriptor in a windowed build")

    def readable(self):             # noqa: D102
        return False

    def writable(self):             # noqa: D102
        return True

    def seekable(self):             # noqa: D102
        return False


for _name in ("stdout", "stderr", "__stdout__", "__stderr__"):
    if getattr(sys, _name, None) is None:
        setattr(sys, _name, _NullStream())
