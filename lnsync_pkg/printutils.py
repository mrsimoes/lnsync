#!/usr/bin/env python

# Copyright (C) 2018 Miguel Simoes, miguelrsimoes[a]yahoo[.]com
# For conditions of distribution and use, see copyright notice in lnsync.py

"""Provide printing services with multiple output levels
    (progress, print, info, warning, debug, error)
    with these rules:
    - progress is disabled if sys.stdout is not a tty.
#    - progress prints normally if option_scrollprogress is True.
    - progress, print, info each outputs to stdout.
    - warning, debug, error each outputs to stderr.
    - default verbosity level is 0.
    - error outputs when verbosity >= -1.
    - print, info, warning, debug output when verbosity >= resp 0, 1, 2, 3.
#    - option_quiet is True, all output is suppressed except for print.
    - each line output from info, warning, debug, error is preceded by APP_PREFIX.
"""

from __future__ import print_function

import sys
import os
import time
import atexit
import threading

# Set by the module user.
option_verbosity = 0

APP_PREFIX = ""

# How many characters were printed to stdout in the last progress line,
# or None if last line printed to stdout was not a progress line
_last_print_len = None

_builtin_print = print

_stdout_is_tty = sys.stdout.isatty()
_stderr_is_tty = sys.stderr.isatty()
if _stdout_is_tty or _stderr_is_tty:
    _tty_size = os.popen('stty size -F /dev/tty', 'r').read().split()
    if len(_tty_size) == 1:
        _term_cols = int(_tty_size[0])
    elif len(_tty_size) == 2:
        _term_cols = int(_tty_size[1])
    else:
        _term_cols = 24

def _print_main(*args, **kwargs):
    global _last_print_len
    global _flushing_needed
    file = kwargs.pop("file", sys.stdout)
    prefix = kwargs.pop("prefix", "")
    assert file in (sys.stdout, sys.stderr)
    if file == sys.stdout:
        is_tty = _stdout_is_tty
    else:
        is_tty = _stderr_is_tty
    if is_tty:
        progress("", flush=True) # Clear any progress info on screen.
    first_line = True
    for ln in " ".join(map(str, args)).splitlines():
        _builtin_print(prefix+ln, file=file, **kwargs)
    if is_tty:
        _last_print_len = None
    _flushing_needed = True
    
def print(*args, **kwargs):
    if option_verbosity >= 0:
        _print_main(*args, file=sys.stdout, prefix="", **kwargs)

def info(*args, **kwargs):
    if option_verbosity >= 1:
        _print_main(*args, file=sys.stdout, prefix=APP_PREFIX, **kwargs)

def warning(*args, **kwargs):
    if option_verbosity >= 2:
        _print_main(*args, file=sys.stderr, prefix=APP_PREFIX+"warning: ", **kwargs)

def debug(template_str, *str_args, **kwargs):
    if option_verbosity >= 3:
        _print_main(template_str % str_args, file=sys.stderr, prefix="", **kwargs)

def error(*args, **kwargs):
    if option_verbosity >= -1:
        _print_main(*args, file=sys.stderr, prefix=APP_PREFIX+"error: ", **kwargs)

def progress(*args, **kwargs):
    """Print each arg in the same line, erase remainder of last the return carriage.
    """
    global _last_print_len # Update these globals.
    global _flushing_needed
    flush = kwargs.pop("flush", False)
    if option_verbosity < 0 or not _stdout_is_tty:
        return
#    _builtin_print("\r", end="")
    tot = 0
    for a in args:
        s = str(a).replace("\n", "\\n")
        try:
            l = len(s.decode('utf-8'))
        except:
            l = len(s) # Not really UTF8.
        if tot + l > _term_cols:
            s = s[:_term_cols - tot]
            l = _term_cols - tot
        _builtin_print(s, end="")
        tot += l
    if _last_print_len is None:
        _last_print_len = _term_cols
    _builtin_print(" " * (_last_print_len - tot), end="")
    _builtin_print("\r", end="") # Carriage return to beg of line.
    _last_print_len = tot
    if flush:
        sys.stdout.flush()
        _flushing_needed = False
    else:
        _flushing_needed = True

_last_progress_percent = None
def progress_percentage(pos, tot, prefix=""):
    global _last_progress_percent
    new_progress_percent = 100*pos//tot
    if _last_progress_percent != new_progress_percent:
        _last_progress_percent = new_progress_percent
        progress("%s%02d%%" % (prefix, new_progress_percent))

FLUSH_INTERVAL_SECS = 1.1
_flushing_needed = False
_flusher_exit_flag = threading.Event()
def _flusher():
    global _flushing_needed
    while not _flusher_exit_flag.is_set() or _flushing_needed:
        if _flushing_needed:
            sys.stdout.flush()
            _flushing_needed = False
        _flusher_exit_flag.wait(timeout=FLUSH_INTERVAL_SECS)
_flusher = threading.Thread(target=_flusher, name="flusher-thread")
_flusher.daemon = True
_flusher.start()

def _exit_func():
    progress("")
    global _flushing_needed
    _flushing_needed = True
    _flusher_exit_flag.set()
    _flusher.join()
atexit.register(_exit_func)

def _final_progress_linebreak():
    """Clear last progress line at exit time."""
    progress("")

if __name__ == "__main__":
    for it in sys.argv[1:]:
        progress(it)
        time.sleep(1)