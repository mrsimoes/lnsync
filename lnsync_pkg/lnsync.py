#!/usr/bin/env python

"""Sync target file tree with source tree using hardlinks.
Copyright (C) 2018 Miguel Simoes, miguelrsimoes[a]yahoo[.]com

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

from __future__ import print_function

import os
import fnmatch
import argparse
import random
import sys
from collections import defaultdict
from sqlite3 import Error as SQLError

import lnsync_pkg.printutils as pr
from lnsync_pkg import metadata
from lnsync_pkg.human2bytes import human2bytes
from lnsync_pkg.hashdb import FileHashDB, FileHashDBs, copy_hashdb
from lnsync_pkg.matcher import TreePairMatcher

DEFAULT_DBPREFIX = "lnsync-"

DESCRIPTION = (
    "lnsync version "+metadata.version+" Copyright (C) 2018 Miguel Simoes.\n\n"
    "This program comes with ABSOLUTELY NO WARRANTY. This is free software, and you\n"
    "are welcome to redistribute it under certain conditions. See the GNU General\n"
    "Public Licence for details.\n\n"
    "Sync by content, with hardlink support, using mv, ln, unlink."
    )

def pick_db_basename(dir_path, dbprefix):
    """Find or create a unique basename matching <dbprefix>[0-9]*.db in the directory.

    Raise RuntimeError if there are too many files matching the database basename pattern
    or if there are none and the given dir is not writable.
    """
    assert os.path.isdir(dir_path), "pick_db_basename: not a directory: %s ." % dir_path
    if dbprefix.endswith(".db"):
        dbprefix = dbprefix[:-3]
    pattern = "%s[0-9]*.db" % dbprefix
    candidates_base = fnmatch.filter(os.listdir(dir_path), pattern)
    if len(candidates_base) == 1:
        db_basename = candidates_base[0]
    elif candidates_base == []:
        if not os.access(dir_path, os.W_OK):
            raise RuntimeError("no write access to %s" % str(dir_path))
        def random_digit_str():
            ndigit = 3
            return ("%%0%dd" % ndigit) % random.randint(0, 10*ndigit-1)
        db_basename = "%s%s.db" % (dbprefix, random_digit_str())
    else:
        raise RuntimeError("too many db files in %s" % str(dir_path))
    return db_basename

class CreateDB(argparse.Action):
    """Create FileHashDB object given directory/filename.
    """
    def __call__(self, parser, namespace, values, option_string=None):
        def make_online_or_offline_db(dir_or_db):
            """
            Create and return a FileHashDB object.
            Given a dir: create/open a unique db file in that dir, in online mode.
            Given a file: open that db in offline mode.
            """
            if os.path.isdir(dir_or_db):
                dbprefix = namespace.dbprefix
                db_basename = pick_db_basename(dir_or_db, dbprefix)
                dbpath = os.path.join(dir_or_db, db_basename)
                return FileHashDB(dbpath, mode="online",
                                  size_as_hash=size_as_hash, maxsize=maxsize)
            elif os.path.isfile(dir_or_db):
                dbpath = dir_or_db
                return FileHashDB(dbpath, mode="offline",
                                  size_as_hash=size_as_hash, maxsize=maxsize)
            else:
                raise RuntimeError(self, "not a dir or file: %s" % dir_or_db)
        # Not all commands have these options, so default values are set here.
        d = vars(namespace)
        size_as_hash = d.get("bysize", False)
        maxsize = d.get("maxsize", 0)
        if maxsize == 0:
            maxsize = None
        if type(values) != list:
            values = make_online_or_offline_db(values)
        else:
            values = map(make_online_or_offline_db, values)
        setattr(namespace, self.dest, values) # Store this parameter in the Namespace.

class CreateOnlineDB(CreateDB):
    """Create FileHashDB object given directory.
    """
    def __call__(self, parser, namespace, values, option_string=None):
        def test_path_is_readable_dir(path):
            if not os.path.isdir(path):
                msg = "not a dir: %s" % path
                parser.error(msg)
            elif not os.access(path, os.R_OK):
                msg = "cannot read dir: %s" % path
                parser.error(msg)
        if not isinstance(values, list):
            test_path_is_readable_dir(values)
        else:
            for p in values:
                test_path_is_readable_dir(p)
        super(CreateOnlineDB, self).__call__(parser, namespace, values)

class CreateOfflineDB(CreateDB):
    """Create FileHashDB objects given directory.
    """
    def __call__(self, parser, namespace, values, option_string=None):
        def test_path_is_readable_file_or_none(path):
            if os.path.exists(path):
                if not os.path.isfile(path) or not os.access(path, os.R_OK):
                    msg = "%s is not a readable file" % path
                    parser.error(msg)
        if not isinstance(values, list):
            test_path_is_readable_file_or_none(values)
        else:
            for p in values:
                test_path_is_readable_file_or_none(p)
        super(CreateOfflineDB, self).__call__(parser, namespace, values)

class SetPrintutilsParam(argparse.Action):
    """Set a parameter in the printutils module to True.
    """
    def __init__(self, nargs=0, **kw): # Override default switch consuming argument following it.
        super(SetPrintutilsParam, self).__init__(nargs=0, **kw)
#    def __call__(self, parser, namespace, values, option_string=None):
#        attr_name = "option_%s" % self.dest
#        prev_value = getattr(pr, attr_name)
#        if isinstance(prev_value, bool):
#            new_value = True
#        elif isinstance(prev_value, int):
#            new_value = prev_value + 1
#        else:
#            raise RuntimeError("unrecognized internal pr attribute: %s" % attr_name)
#        setattr(namespace, self.dest, new_value) # self.dest is scroll, debug, etc. Disregard values==[].
#        setattr(pr, attr_name, new_value)

class IncreaseVerbosity(SetPrintutilsParam):
    def __call__(self, parser, namespace, values, option_string=None):
        pr.option_verbosity += 1
#        setattr(namespace, self.dest, new_value) # self.dest is scroll, debug, etc. Disregard values==[].

class DecreaseVerbosity(SetPrintutilsParam):
    def __call__(self, parser, namespace, values, option_string=None):
        pr.option_verbosity -= 1

def relative_path(value):
    """Argument type to exclude absolute paths.
    """
    if os.path.isabs(value):
        raise ValueError("not a relative path: %s." % value)
    return value

#
# Create main parser and subcommand parsers.
#

cmd_handlers = {}  # Register here the handler function for each command.

dbprefix_option_parser = argparse.ArgumentParser(add_help=False)
dbprefix_option_parser.add_argument("-p", "--dbprefix", type=str, default=DEFAULT_DBPREFIX, \
           help="sqlite database name prefix")

bysize_option_parser = argparse.ArgumentParser(add_help=False)
bysize_option_parser.add_argument("-z", "--bysize", action="store_true", \
           help="compare files by size only")

maxsize_option_parser = argparse.ArgumentParser(add_help=False)
maxsize_option_parser.add_argument("-M", "--maxsize", type=human2bytes, default=0, \
           help="ignore files larger than MAXSIZE (0 for no limit; suffixes allowed: K, M, G, etc)")

find_options_parser = argparse.ArgumentParser(add_help=False)
find_options_parser.add_argument("-H", "--hardlinks", action="store_true", \
                           help="hardlinks are duplicates")

top_parser = argparse.ArgumentParser(description=DESCRIPTION,
                                     formatter_class=argparse.RawTextHelpFormatter)
top_parser.add_argument("-q", "--quiet", action=DecreaseVerbosity,
                        help="decrease verbosity")
top_parser.add_argument("-v", "--verbose", action=IncreaseVerbosity,
                        help="increase verbosity")
#top_parser.add_argument("-s", "--scrollprogress", action=SetPrintutilsParam,
#                        help="scroll progress info")
cmd_parsers = top_parser.add_subparsers(dest="cmdname", help="sub-command help")

## sync
parser_sync = \
    cmd_parsers.add_parser(
        'sync',
        parents=[dbprefix_option_parser,
                 bysize_option_parser,
                 maxsize_option_parser],
        help="sync target (mv and ln on target only, no data copied or deleted)")
parser_sync.add_argument("source", action=CreateDB)
parser_sync.add_argument("targetdir", action=CreateOnlineDB)
parser_sync.add_argument("-n", "--dry-run", help="dry run", action="store_true")
def do_sync(args):
    with args.source as src_db:
        with args.targetdir as tgt_db:
            try:
                matcher = TreePairMatcher(src_db, tgt_db)
            except ValueError as e:
                raise RuntimeError, str(e), sys.exc_info()[2]
            if not matcher.do_match():
                msg = "match failed"
                raise RuntimeError(msg)
            for cmd in matcher.generate_sync_cmds():
                if not args.dry_run:
                    try:
                        tgt_db.exec_cmd(cmd)
                    except OSError as e: # Catches e.g. linking not supported on target.
                        msg = "could not execute: " + " ".join(cmd)
                        raise RuntimeError(msg)
                pr.print(" ".join(cmd))
            pr.info("lnsync: sync done")
cmd_handlers["sync"] = do_sync

## update
parser_update = cmd_parsers.add_parser('update', \
        parents=[dbprefix_option_parser, maxsize_option_parser], \
        help='update hash values for all new and modified files')
parser_update.add_argument("locations", action=CreateOnlineDB, nargs="*")
def do_update(args):
    with FileHashDBs(args.locations) as locations:
        for db in locations:
            db.db_update_all()
cmd_handlers["update"] = do_update

## rehash
parser_rehash = cmd_parsers.add_parser('rehash', parents=[dbprefix_option_parser], \
                    help='force hash update for given files')
parser_rehash.add_argument("topdir", action=CreateOnlineDB)
parser_rehash.add_argument("relfilepaths", type=relative_path, nargs='+')
def do_rehash(args):
    with args.topdir as db:
        for relpath in args.relfilepaths:
            file_obj = db.follow_path(relpath)
            if file_obj is None or not file_obj.is_file():
                pr.error("lnsync: not a relative path to a file: %s" % str(relpath))
                continue
            try:
                db.do_recompute_file(file_obj)
            except Exception as e:
                pr.debug(e)
                pr.error("lnsync: cannot rehash %s" % db.printable_path(relpath))
                continue
cmd_handlers["rehash"] = do_rehash


## subdir
parser_subdir = \
    cmd_parsers.add_parser(
        'subdir',
        parents=[dbprefix_option_parser],
        help='copy hash database to a relative subdir')
parser_subdir.add_argument("topdir", type=str)
parser_subdir.add_argument("relativesubdir", type=relative_path)
def do_subdir(args):
    src_dir = args.topdir
    src_db_basename = pick_db_basename(src_dir, args.dbprefix)
    src_db_path = os.path.join(src_dir, src_db_basename)
    if not os.path.isfile(src_db_path):
        msg = "no database at: %s." % (src_dir,)
        raise ValueError(msg)
    tgt_dir = os.path.join(src_dir, args.relativesubdir)
    if not os.path.isdir(tgt_dir):
        msg = "not a subdir: %s.", (tgt_dir,)
        raise ValueError(msg)
    tgt_db_basename = pick_db_basename(tgt_dir, args.dbprefix)
    tgt_db_path = os.path.join(tgt_dir, tgt_db_basename)
    copy_hashdb(src_db_path, tgt_db_path)
    with FileHashDB(tgt_db_path, mode="online") as tgt_db:
        tgt_db.db_purge()
cmd_handlers["subdir"] = do_subdir

## fdupes
parser_fdupes = \
    cmd_parsers.add_parser(
        'fdupes',
        parents=[find_options_parser,
                 dbprefix_option_parser,
                 bysize_option_parser,
                 maxsize_option_parser],
        help='find duplicate files')
parser_fdupes.add_argument("locations", action=CreateDB, nargs="*")
def do_fdupes(args):
    """
    Find duplicates, using file size as well as file hash.
    """
    sizes_seen_once, sizes_seen_twice = set(), set()
    with FileHashDBs(args.locations) as all_dbs:
        for db in all_dbs:
            pr.progress("assembling sizes for %s ." % db.printable_path(""))
            for sz in db.size_to_files():
                if (sz in sizes_seen_once) or (len(db.size_to_files(sz)) > 1):
                    sizes_seen_twice.add(sz)
                if args.hardlinks:
                    # In this case, a size value seen once for an id
                    # with multiple paths is recorded as a dupe.
                    sz_files = db.size_to_files(sz)
                    if any(len(sz_file.relpaths) > 1 for sz_file in sz_files):
                        sizes_seen_twice.add(sz)
                sizes_seen_once.add(sz)
        del sizes_seen_once
        grouped_repeats = []            # Dupe paths, grouped by common contents.
        for sz in sizes_seen_twice:
            hashes_seen_once = set()    # For size sz and all databases.
            hashes_seen_twice = set()
            hash_to_fpaths = {}
            for db in all_dbs:
                if sz in db.size_to_files():
                    for fobj in db.size_to_files(sz):
                        try:
                            hval = db.db_get_prop(fobj) # Raises RuntimeError on failure.
                        except Exception as e:
                            msg = "could not hash file id '%d'." % fobj.file_id
                            pr.warning(msg)
                            continue
                        if hval in hashes_seen_once or \
                            (args.hardlinks and len(fobj.relpaths) > 1):
                            hashes_seen_twice.add(hval)
                        hashes_seen_once.add(hval)
                        if not hval in hash_to_fpaths:
                            hash_to_fpaths[hval] = []
                        this_file_paths = [db.printable_path(p) for p in fobj.relpaths]
                        if args.hardlinks:
                            this_file_paths[1:] = ["= " + p for p in this_file_paths[1:]]
                        hash_to_fpaths[hval] += this_file_paths
            for rep_hash in hashes_seen_twice: # Unequal sizes correspond to unequal hash values.
                grouped_repeats.append(hash_to_fpaths[rep_hash])
    output_leading_linebreak = False
    for gr in grouped_repeats:
        if output_leading_linebreak:
            pr.print(" ")
        else: output_leading_linebreak = True
        for fpath in gr:
            pr.print(fpath)
cmd_handlers["fdupes"] = do_fdupes

## onall
parser_onall = \
    cmd_parsers.add_parser(
        'onall',
        parents=[find_options_parser,
                 dbprefix_option_parser, \
                 bysize_option_parser,
                 maxsize_option_parser], \
        help='find files common to all locations')
parser_onall.add_argument("locations", action=CreateDB, nargs="+")
def do_onall(args):
    with FileHashDBs(args.locations) as all_dbs:
        first_db = all_dbs[0]
        other_dbs = all_dbs[1:]
        common_sizes = set(first_db.get_all_sizes())
        for db in other_dbs:
            pr.progress("assembling sizes for %s ." % db.printable_path(""))
            common_sizes.intersection_update(db.get_all_sizes())
        def size_to_hashes(adb, sz):
            "Generate all prop values for files of size sz on database adb."
            for f in adb.size_to_files(sz):
                yield adb.db_get_prop(f) # Raises RuntimeError on failure.
        for sz in common_sizes:
            common_hashes_this_sz = set(size_to_hashes(first_db, sz))
            for db in other_dbs:
                hashes_this_db = set(size_to_hashes(db, sz))
                common_hashes_this_sz.intersection_update(hashes_this_db)
            common_hash_paths = defaultdict(lambda: [])
            for db in all_dbs:
                for f in db.size_to_files(sz):
                    h = db.db_get_prop(f) # Raises RuntimeError on failure.
                    if h in common_hashes_this_sz:
                        paths = [db.printable_path(rp) for rp in f.relpaths]
                        common_hash_paths[h] += paths
            for h, paths in common_hash_paths.iteritems():
                for p in paths:
                    pr.print(p)
                pr.print("\n")
cmd_handlers['onall'] = do_onall


## onfirstonly
parser_onfirstonly = \
    cmd_parsers.add_parser(
        'onfirstonly',
        parents=[find_options_parser,
                 dbprefix_option_parser,
                 bysize_option_parser,
                 maxsize_option_parser], \
        help='find files present on first location, but not any other')
parser_onfirstonly.add_argument("locations", action=CreateDB, nargs="+")
def do_onfirstonly(args):
    with FileHashDBs(args.locations) as all_dbs:
        first_db = all_dbs[0]
        other_dbs = all_dbs[1:]
        for sz in first_db.get_all_sizes():
            sz_other_db_hashes = set()
            for db in other_dbs:
                if sz in db.size_to_files():
                    for f in db.size_to_files(sz): # db_get_prop raises RuntimeError.
                        sz_other_db_hashes.add(db.db_get_prop(f))
            for f in first_db.size_to_files(sz):
                if not first_db.db_get_prop(f) in sz_other_db_hashes:
                            # db_get_prop raises RuntimeError on failure.
                    paths = f.relpaths
                    pr.print(paths[0])
                    if not args.hardlinks:
                        fs = "= %s"
                    else:
                        fs = "%s"
                    for p in paths[1:]:
                        pr.print(fs, p)
cmd_handlers["onfirstonly"] = do_onfirstonly

## cmp
parser_cmp = \
    cmd_parsers.add_parser(
        'cmp',
        parents=[dbprefix_option_parser,
                 bysize_option_parser,
                 maxsize_option_parser],
        help='compare two dirs by name (recursive)')
parser_cmp.add_argument("leftlocation", action=CreateDB)
parser_cmp.add_argument("rightlocation", action=CreateDB)
def do_cmp(args):
    """
    Compare two directories. Always recursive, by name. Ignore links.
    """
    with args.leftlocation as left_db:
        with args.rightlocation as right_db:
            dirpaths_to_visit = [""]
            while dirpaths_to_visit:
                cur_dirpath = dirpaths_to_visit.pop()
                for left_obj, basename in left_db.walk_dir_contents(cur_dirpath, dirs=True):
                    left_path = os.path.join(cur_dirpath, basename)
                    right_obj = right_db.follow_path(left_path)
                    if left_obj.is_file():
                        if right_obj is not None and right_obj.is_file():
                                    # NB: db_get_prop raises RuntimeError on failure.
                            if left_db.db_get_prop(left_obj) != right_db.db_get_prop(right_obj):
                                pr.print("differ: %s" % left_path)
                        else:
                            pr.print("left only: %s" % left_path)
                    else: # left_obj is dir
                        if right_obj is not None and right_obj.is_dir():
                            dirpaths_to_visit.append(left_path)
                        else:
                            pr.print("left only: %s%s" % (left_path, os.path.sep))
                for right_obj, basename in right_db.walk_dir_contents(cur_dirpath, dirs=True):
                    right_path = os.path.join(cur_dirpath, basename)
                    left_obj = left_db.follow_path(right_path)
                    if right_obj.is_file():
                        if left_obj is None or left_obj.is_dir():
                            pr.print("right only: %s" % right_path)
                    else:
                        if left_obj is None or left_obj.is_file():
                            pr.print("right only: %s%s" % (right_path, os.path.sep))
cmd_handlers["cmp"] = do_cmp

## lookup
parser_lookup = \
    cmd_parsers.add_parser(
        'lookup', \
        parents=[dbprefix_option_parser],
        help='get a file hash')
parser_lookup.add_argument("location", action=CreateDB)
parser_lookup.add_argument("relpath", type=relative_path)
def do_lookup(args):
    "Handler for looking up a fpath hash in the DB."
    with args.location as db:
        fpath = args.relpath
        fobj = db.follow_path(fpath)
        if fobj is None or not fobj.is_file():
            pr.error("lnsync: not a file: %s" % str(fpath))
        else:
            h = db.db_get_prop(fobj)
            pr.print(h)
cmd_handlers["lookup"] = do_lookup

## check
parser_check_files = \
    cmd_parsers.add_parser(
        'check', \
        parents=[dbprefix_option_parser,
                 maxsize_option_parser], \
        help='recompute file hash and check against db (all files, if none given)')
parser_check_files.add_argument("location", action=CreateOnlineDB)
parser_check_files.add_argument("relpaths", type=relative_path, nargs="*")

def do_check(args):
    with args.location as db:
        if db.mode == "offline":
            raise ValueError("cannot check files in offline mode")
        which_files_gen = args.relpaths
        if len(which_files_gen) == 0:
            def gen_all_paths():
                for _obj, _parent, path in db.walk_paths():
                    yield path
            which_files_gen = gen_all_paths()
        num_changed = 0
        for path in which_files_gen:
            pr.progress("checking: %s" % path)
            try:
                fobj = db.follow_path(path)
                res = db.db_check_prop(fobj)
            except Exception as e:
                pr.warning("lnsync: error checking %s" % path)
                pr.warning(e)
            else:
                if res is False:
                    pr.print(path)
                    num_changed += 1
        if num_changed > 0:
            pr.print("%d file(s) failed check" % num_changed)
        else:
            pr.info("no files failed check")

cmd_handlers["check"] = do_check

## rsync
parser_rsync = \
    cmd_parsers.add_parser(
        'rsync',
        parents=[dbprefix_option_parser,
                 maxsize_option_parser],
        help="print rsync command to sync skipping db files")
parser_rsync.add_argument("-x", "--execute", action="store_true", help="also execute rsync command")
parser_rsync.add_argument("sourcedir", type=str)
parser_rsync.add_argument("targetdir", type=str)
parser_rsync.add_argument("rsyncargs", type=str, nargs="*")
def do_rsync(args):
    """Print suitable rsync command.
    """
    import pipes
    src_dir, tgt_dir = args.sourcedir, args.targetdir
    if src_dir[-1] != os.sep: src_dir += os.sep # rsync needs trailing / on sourcedir.
    while tgt_dir[-1] == os.sep: tgt_dir = tgt_dir[:-1]
    src_dir = pipes.quote(src_dir)
    tgt_dir = pipes.quote(tgt_dir)
    rsync_opts = "-r -t -v -H --progress --delete-before"
    if args.maxsize > 0:
        rsync_opts += " --max-size %d" % args.maxsize
    rsync_opts += " ".join(args.rsyncargs)
    rsync_opts += r"  --exclude %s\*.db" % args.dbprefix
    rsync_cmd = "rsync %s %s %s" % (rsync_opts, src_dir, tgt_dir)
    pr.print(rsync_cmd)
    if args.execute:
        try:
            os.system(rsync_cmd)
        except Exception as e:
            pr.debug(e)
            msg = "error executing '%s'." % rsync_cmd
            raise RuntimeError, msg, sys.exc_info()[2] # Chain exception.
cmd_handlers["rsync"] = do_rsync

## mkoffline
parser_mkoffline = \
    cmd_parsers.add_parser(
        'mkoffline',
        parents=[dbprefix_option_parser,
                 maxsize_option_parser],
        help="incorporate offline tree structure into db")
parser_mkoffline.add_argument("sourcedir", action=CreateOnlineDB)
def do_mkoffline(args):
    """Prepare an existing db for offline use, by inserting file tree directory
    structure and file metadata.
    """
    with args.sourcedir as src_db:
        pr.info("updating all hashes...")
        src_db.db_update_all()
        pr.info("saving directory info...")
        src_db.db_store_tree()
cmd_handlers["mkoffline"] = do_mkoffline

## rmoffline
parser_rmoffline = \
    cmd_parsers.add_parser(
        'rmoffline',
        help="remove offline tree structure from db")
parser_rmoffline.add_argument("database", action=CreateOfflineDB)
def do_rmoffline(args):
    """Clear offline tree info from a database.
    """
    with args.database as db:
        pr.info("clearing directory info...")
        db.db_clear_tree()
cmd_handlers["rmoffline"] = do_rmoffline


## cleandb
parser_cleandb = \
    cmd_parsers.add_parser(
        'cleandb',
        parents=[dbprefix_option_parser],
        help="clean and defragment the database")
parser_cleandb.add_argument("location", action=CreateOnlineDB)
def do_cleandb(args):
    """Purge old entries from db.
    """
    with args.location as db:
        db.db_purge()
cmd_handlers["cleandb"] = do_cleandb

def main():
    if len(sys.argv) == 1:
        top_parser.print_help(sys.stderr)
        sys.exit(1)
    pr.APP_PREFIX = "lnsync: "
    try:
        args = top_parser.parse_args()
        handler_fn = cmd_handlers[args.cmdname]
        try:
            handler_fn(args)
        except Exception as e:
            if __debug__:
                print(type(e), e)
                import pdb; pdb.set_trace()
            raise type(e), e, sys.exc_info()[2]
        sys.exit(0)
    except KeyboardInterrupt:
        raise SystemExit("lnsync: interrupted")
    except NotImplementedError as e:
        pr.error("lnsync: not implemented for your system: %s", str(e))
    except RuntimeError as e: # Includes NotImplementedError
        pr.error("lnsync: runtime error: %s" % str(e))
    except SQLError as e:
        pr.error("lnsync: database error: %s" % str(e))
    except AssertionError as e:
        pr.error("lnsync: internal check failed: %s" % str(e))
    except Exception as e:
        pr.error("lnsync: general exception: %s" % str(e))
    finally:
        sys.exit(1)

if __name__ == "__main__":
    main()