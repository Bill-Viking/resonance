#!/usr/bin/env python3
"""Isolated native-Mac source build of the CPython 3.14.10 runtime stack (diagnostic only).

Usage (read the plan first; nothing is created without --run):

    python3 -I scripts/build-local.py --out <new-child-folder-of-this-experiment-root>
    python3 -I scripts/build-local.py --out <new-child-folder> --run

Without --run it prints the plan and creates nothing.  With --run it creates a new 0700
output folder (logs, tmp, sources, build, prefix), verifies the 9 archives in downloads/
against the fixed digests, extracts each into its own new folder, checks every configure
flag against the extracted text, then builds in the fixed order into <out>/prefix.

The result is a diagnostic build only: not RES accepted, not signed, no product tests.
No network access.  Never removes anything.  Never touches /Library, /Applications,
/usr/local, a Homebrew prefix or user config.
"""

import argparse
import datetime
import hashlib
import json
import os
import platform
import re
import shlex
import signal
import stat
import subprocess
import sys
import tarfile
import time

if sys.version_info < (3, 12):
    sys.exit("FAIL — Python 3.12+ is required (tarfile filter='data' and friends)")

# ---------------------------------------------------------------------------
# Fixed inputs.  Digests are copied from SOURCE-INPUTS.json and cross-checked at run time.
# Order is the build order.
# ---------------------------------------------------------------------------
COMPONENTS = [
    dict(name="openssl", version="3.5.9", file="openssl-3.5.9.tar.gz", root="openssl-3.5.9",
         sha256="603f5602e2eef00d77fbd429d34dcd5822bb301757a1bc9cdb24c670f1eb859a"),
    dict(name="libffi", version="3.5.2", file="libffi-3.5.2.tar.gz", root="libffi-3.5.2",
         sha256="f3a3082a23b37c293a4fcd1053147b371f2ff91fa7ea1b2a52e335676bac82dc"),
    dict(name="xz", version="5.8.4", file="xz-5.8.4.tar.gz", root="xz-5.8.4",
         sha256="0014c7886930454fe8bd4228665b51af55eeae560ea135c9c4cd33f55b2591d9"),
    dict(name="sqlite", version="3.50.4", file="sqlite-autoconf-3500400.tar.gz",
         root="sqlite-autoconf-3500400",
         sha256="a3db587a1b92ee5ddac2f66b3edb41b26f9c867275782d46c3a088977d6a5b18"),
    dict(name="mpdecimal", version="4.0.1", file="mpdecimal-4.0.1.tar.gz", root="mpdecimal-4.0.1",
         sha256="96d33abb4bb0070c7be0fed4246cd38416188325f820468214471938545b1ac8"),
    dict(name="zstd", version="1.5.7", file="zstd-1.5.7.tar.gz", root="zstd-1.5.7",
         sha256="eb33e51f49a15e023950cd7825ca74a4a2b43db8354825ac24fc1b7ee09e6fa3"),
    dict(name="tcl", version="9.0.4", file="tcl9.0.4-src.tar.gz", root="tcl9.0.4",
         sha256="d0aed49230bc02a65c1e0229e65f34590a4b037ec40d546f32573b467f7551ea"),
    dict(name="tk", version="9.0.5-pre-33904f039f48ed64", file="tk9.0.5.tar.gz", root="tk9.0.5",
         sha256="51ea4221578b994ec7768df9165c6682eef487a148a15003a74135c33cab35b5"),
    dict(name="cpython", version="3.14.10", file="Python-3.14.10.tgz", root="Python-3.14.10",
         sha256="9e4996c19ae5a3df31f10c37723661f71d3455d73bd005f79df7098e5cc13192"),
]
BY_NAME = {c["name"]: c for c in COMPONENTS}

DEPLOYMENT_TARGET = "13.0"
JOBS = 4
STEP_TIMEOUT = 900          # seconds, per command, no retries
TOTAL_BUDGET = 2400         # seconds, whole --run
UNPACK_CAP = 600 * 1024 * 1024
ARCHIVE_CAP = 100 * 1024 * 1024
MEMBER_CAP = 200000
LOG_CAP = 200 * 1024 * 1024
SYS_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
FORBIDDEN_ARG_ROOTS = ("/usr/local", "/opt/homebrew", "/Library", "/Applications", "/System")
FORBIDDEN_ENV_SUBSTR = ("/opt/homebrew", "/usr/local", "homebrew", "Homebrew")
OUT_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
SAFE_PATH_RE = re.compile(r"[A-Za-z0-9._/+-]+")

# Child-process isolation constants.  PYTHONNOUSERSITE is the only PYTHON* variable ever allowed
# in a child environment, and only with exactly this value (see check_env_safe).
NO_USER_SITE_ENV = ("PYTHONNOUSERSITE", "1")
# Literal Make command-line override (argument array, never parent-shell interpolation).  Make
# expands $(BUILDPYTHON) itself; -s keeps the user site out even though -E ignores environment.
PYTHON_FOR_BUILD_ARG = "PYTHON_FOR_BUILD=./$(BUILDPYTHON) -E -s"

SQLITE_EXTRA_CFLAGS = "-DSQLITE_ENABLE_FTS5 -DSQLITE_ENABLE_RTREE -DSQLITE_THREADSAFE=1"

# /bin/sh sources the generated Tcl/Tk config files; paths arrive as positional arguments.
SH_TCL = (r'. "$1" || exit 1; printf "%s\0" "$TCL_VERSION" "$TCL_PATCH_LEVEL" "$TCL_PREFIX" '
          r'"$TCL_EXEC_PREFIX" "$TCL_INCLUDE_SPEC" "$TCL_LIB_SPEC" "$TCL_STUB_LIB_SPEC" "$TCL_SRC_DIR"')
TCL_FIELDS = ["TCL_VERSION", "TCL_PATCH_LEVEL", "TCL_PREFIX", "TCL_EXEC_PREFIX",
              "TCL_INCLUDE_SPEC", "TCL_LIB_SPEC", "TCL_STUB_LIB_SPEC", "TCL_SRC_DIR"]
SH_TK = (r'. "$1" || exit 1; printf "%s\0" "$TK_VERSION" "$TK_PATCH_LEVEL" "$TK_PREFIX" '
         r'"$TK_EXEC_PREFIX" "$TK_INCLUDE_SPEC" "$TK_LIB_SPEC" "$TK_STUB_LIB_SPEC" "$TK_XINCLUDES" '
         r'"$TK_SRC_DIR"')
TK_FIELDS = ["TK_VERSION", "TK_PATCH_LEVEL", "TK_PREFIX", "TK_EXEC_PREFIX", "TK_INCLUDE_SPEC",
             "TK_LIB_SPEC", "TK_STUB_LIB_SPEC", "TK_XINCLUDES", "TK_SRC_DIR"]

# Headless probe run as `python -I -B -c PROBE`.  Tcl interpreter only, never a Tk GUI.
PROBE = r'''
import sys, json, platform, sysconfig, importlib, importlib.util
r = {}
def guard(key, fn):
    try:
        r[key] = fn()
    except Exception as e:
        r[key] = "error: " + repr(e)
r["version"] = "%d.%d.%d" % sys.version_info[:3]
r["implementation"] = sys.implementation.name
guard("gil_enabled", lambda: sys._is_gil_enabled())
r["py_gil_disabled_config"] = sysconfig.get_config_var("Py_GIL_DISABLED")
r["machine"] = platform.machine()
r["deployment_target"] = sysconfig.get_config_var("MACOSX_DEPLOYMENT_TARGET")
r["framework_build"] = sysconfig.get_config_var("PYTHONFRAMEWORK")
r["prefix"] = sys.prefix
r["executable"] = sys.executable
r["no_user_site"] = sys.flags.no_user_site
r["isolated"] = sys.flags.isolated
mods = {}
for n in ("ssl", "sqlite3", "ctypes", "lzma", "bz2", "zlib", "decimal", "compression.zstd",
          "tkinter", "venv", "ensurepip"):
    try:
        importlib.import_module(n)
        mods[n] = "ok"
    except Exception as e:
        mods[n] = "error: " + repr(e)
r["imports"] = mods
guard("pip_file", lambda: importlib.import_module("pip").__file__)
guard("openssl_version", lambda: importlib.import_module("ssl").OPENSSL_VERSION)
guard("sqlite_version", lambda: importlib.import_module("sqlite3").sqlite_version)
guard("decimal_libmpdec_version", lambda: importlib.import_module("decimal").__libmpdec_version__)
guard("decimal_version", lambda: importlib.import_module("decimal").__version__)
def tcl_info():
    tk = importlib.import_module("tkinter")
    interp = tk.Tcl()
    return {"patchlevel": interp.eval("info patchlevel"), "library": interp.eval("info library"),
            "tcl_version": tk.TclVersion, "tk_version": tk.TkVersion}
guard("tcl_tk", tcl_info)
locs = {}
for n in ("_tkinter", "_ssl", "_hashlib", "_ctypes", "_sqlite3", "_decimal", "_lzma", "_zstd", "_bz2"):
    try:
        spec = importlib.util.find_spec(n)
        locs[n] = spec.origin if spec else None
    except Exception as e:
        locs[n] = "error: " + repr(e)
r["locations"] = locs
print(json.dumps(r, sort_keys=True))
'''


class BuildFail(Exception):
    pass


class Blocked(Exception):
    pass


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def sha256_path(path):
    h = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def inside(path, base):
    path = os.path.normpath(path)
    base = os.path.normpath(base)
    return path == base or path.startswith(base + os.sep)


def minimal_env():
    return {"PATH": SYS_PATH, "LC_ALL": "C"}


def sysrun(argv, timeout=60, env=None):
    """Short read-only system helper (xcrun, xattr, sysctl).  Argument array, no shell."""
    r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                       env=env or minimal_env(), stdin=subprocess.DEVNULL)
    return r.returncode, r.stdout, r.stderr


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------
class Ctx:
    def __init__(self, root, out, run):
        self.root = root
        self.out = out
        self.run = run
        self.downloads = os.path.join(root, "downloads")
        self.prefix = os.path.join(out, "prefix")
        self.tmp = os.path.join(out, "tmp")
        self.logs = os.path.join(out, "logs")
        self.arch = platform.machine()
        self.sdk = "<SDK from /usr/bin/xcrun --sdk macosx --show-sdk-path>"
        self.cc = "<clang from xcrun --find clang>"
        self.cxx = "<clang++ from xcrun --find clang++>"
        self.tcltk = None
        self.t0 = time.monotonic()
        self.rec = {}

    def src(self, name):
        return os.path.join(self.out, "sources", name, BY_NAME[name]["root"])

    def build(self, name):
        return os.path.join(self.out, "build", name)

    def remaining(self):
        return TOTAL_BUDGET - (time.monotonic() - self.t0)

    def base_flags(self):
        return (f"-O2 -fPIC -arch {self.arch} -mmacosx-version-min={DEPLOYMENT_TARGET} "
                f"-isysroot {self.sdk}")

    def env(self, kind="base"):
        cflags = self.base_flags()
        if kind == "sqlite":
            cflags += " " + SQLITE_EXTRA_CFLAGS
        path = SYS_PATH + (":" + os.path.join(self.prefix, "bin") if kind == "cpython" else "")
        env = {
            "PATH": path,
            "LC_ALL": "C",
            "TMPDIR": self.tmp + "/",
            "SDKROOT": self.sdk,
            "MACOSX_DEPLOYMENT_TARGET": DEPLOYMENT_TARGET,
            "CC": self.cc,
            "CXX": self.cxx,
            "CFLAGS": cflags,
            "CXXFLAGS": self.base_flags(),
            "CPPFLAGS": f"-I{self.prefix}/include",
            # No -rpath: Tcl/Tk install names are absolute and everything else is static.
            "LDFLAGS": (f"-L{self.prefix}/lib -arch {self.arch} "
                        f"-mmacosx-version-min={DEPLOYMENT_TARGET} -isysroot {self.sdk}"),
            # Isolation additions (not inherited, not user config): pip must not read any config
            # or index while ensurepip runs from bundled wheels.
            "PIP_CONFIG_FILE": os.devnull,
            "PIP_NO_INDEX": "1",
        }
        env[NO_USER_SITE_ENV[0]] = NO_USER_SITE_ENV[1]
        return env

    def env_for(self, kind):
        return self.ensurepip_env() if kind == "ensurepip" else self.env(kind)

    def ensurepip_env(self):
        """Clean offline environment for `python -I -m ensurepip` (no compiler or SDK variables)."""
        return {
            "PATH": SYS_PATH,
            "LC_ALL": "C",
            "TMPDIR": self.tmp + "/",
            NO_USER_SITE_ENV[0]: NO_USER_SITE_ENV[1],
            "PIP_CONFIG_FILE": os.devnull,
            "PIP_NO_INDEX": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        }

    def save(self):
        if not self.run:
            return
        tmp = os.path.join(self.tmp, "receipt.json.new")
        with open(tmp, "w") as f:
            json.dump(self.rec, f, indent=1, sort_keys=True)
            f.write("\n")
        os.replace(tmp, os.path.join(self.out, "BUILD-RECEIPT.json"))


# ---------------------------------------------------------------------------
# Output-path validation
# ---------------------------------------------------------------------------
def validate_out(root, raw):
    p = os.path.abspath(raw)
    parent, name = os.path.split(p)
    if os.path.realpath(parent) != root or os.path.abspath(parent) != os.path.realpath(parent):
        raise BuildFail(f"--out must be a direct child of {root} with no symlink in its path")
    if not OUT_NAME_RE.fullmatch(name):
        raise BuildFail("--out folder name must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}")
    if os.path.lexists(p):
        raise BuildFail(f"--out already exists (existing folder, file or symlink refused): {p}")
    if not SAFE_PATH_RE.fullmatch(p):
        raise BuildFail("--out path contains characters unsafe for configure scripts")
    return p


# ---------------------------------------------------------------------------
# Plan (shared by plan mode and --run)
# ---------------------------------------------------------------------------
def build_plan(c):
    P = c.prefix
    MAKE = "/usr/bin/make"
    SH = "/bin/sh"
    plan = []

    def add(comp, label, argv, cwd, env="base", hook=None, mkbuild=False):
        plan.append(dict(id=f"{len(plan) + 1:02d}-{comp}-{label}", comp=comp, label=label,
                         argv=argv, cwd=cwd, env=env, hook=hook, mkbuild=mkbuild))

    def autoconf(comp, flags, env="base", out_of_tree=True, extra=None, configure="configure",
                 make_args=()):
        src = c.src(comp)
        cwd = c.build(comp) if out_of_tree else src
        add(comp, "configure", lambda cx=c: [SH, os.path.join(src, configure), f"--prefix={P}"]
            + flags + (extra(cx) if extra else []), cwd, env, hook="inspect_makefile",
            mkbuild=out_of_tree)
        add(comp, "build", [MAKE, f"-j{JOBS}", *make_args], cwd, env)
        add(comp, "install", [MAKE, "install", *make_args], cwd, env)

    target = {"arm64": "darwin64-arm64-cc", "x86_64": "darwin64-x86_64-cc"}.get(c.arch, "UNSUPPORTED-ARCH")

    # 1 OpenSSL
    cwd = c.build("openssl")
    add("openssl", "configure",
        ["/usr/bin/perl", os.path.join(c.src("openssl"), "Configure"), target, "no-shared", "no-tests",
         f"--prefix={P}", f"--openssldir={P}/etc/ssl"], cwd, hook="inspect_makefile", mkbuild=True)
    add("openssl", "build", [MAKE, f"-j{JOBS}"], cwd)
    add("openssl", "install_sw", [MAKE, "install_sw"], cwd)

    # 2 libffi, 3 XZ, 4 SQLite, 5 mpdecimal
    autoconf("libffi", ["--disable-shared", "--enable-static"])
    autoconf("xz", ["--disable-shared", "--enable-static", "--disable-xz", "--disable-xzdec",
                    "--disable-lzmadec", "--disable-lzmainfo", "--disable-scripts", "--disable-doc"])
    autoconf("sqlite", ["--disable-shared", "--enable-static", "--disable-readline"],
             env="sqlite")
    # mpdecimal: top-level Makefile.in recurses with `cd libmpdec && $(MAKE)`; in-tree in its fresh copy.
    autoconf("mpdecimal", ["--disable-shared", "--enable-static", "--disable-cxx"], out_of_tree=False)

    # 6 zstd: no configure; compile in its fresh extracted copy under out/sources.
    zs = c.src("zstd")
    add("zstd", "build", [MAKE, "-C", "lib", f"-j{JOBS}", "libzstd.a"], zs)
    # MAKE=/usr/bin/make pins the recursive make token printed by lib/Makefile (B1).
    inst = ["install-static", "install-includes", "install-pc", f"PREFIX={P}", "MAKE=/usr/bin/make"]
    add("zstd", "install-dryrun", [MAKE, "-n", "-C", "lib"] + inst, zs, hook="zstd_dryrun")
    add("zstd", "install", [MAKE, "-C", "lib"] + inst, zs)

    # 7 Tcl, 8 Tk
    autoconf("tcl", ["--enable-shared", "--disable-framework", "--disable-zipfs"], configure="unix/configure")
    plan[-2]["argv"] = [MAKE, f"-j{JOBS}", "binaries", "libraries", "doc"]   # core only: no optional pkgs/ extensions
    plan[-1]["argv"] = [MAKE, "install", "INSTALL_PACKAGE_TARGETS="]        # upstream install minus bundled pkgs
    plan[-1]["hook"] = "tcl_meta"          # install step: installed tclConfig.sh must resolve inside prefix
    autoconf("tk", ["--enable-aqua", "--enable-shared", "--disable-framework", "--disable-zipfs",
                f"--with-tcl={P}/lib"], configure="unix/configure")
    plan[-1]["hook"] = "tk_meta"           # install step: installed tkConfig.sh must resolve inside prefix

    # 9 CPython
    def py_extra(cx):
        i = f"-I{P}/include"
        L = f"-L{P}/lib"
        tcltk_c = cx.tcltk["cflags"] if cx.tcltk else "<TCL_INCLUDE_SPEC + TK_INCLUDE_SPEC from generated config>"
        tcltk_l = cx.tcltk["libs"] if cx.tcltk else "<TCL_LIB_SPEC + TK_LIB_SPEC from generated config>"
        return [f"LIBFFI_CFLAGS={i}", f"LIBFFI_LIBS={L} -lffi",
                f"LIBLZMA_CFLAGS={i}", f"LIBLZMA_LIBS={L} -llzma",
                f"LIBZSTD_CFLAGS={i}", f"LIBZSTD_LIBS={L} -lzstd",
                f"LIBMPDEC_CFLAGS={i}", f"LIBMPDEC_LIBS={L} -lmpdec",
                f"LIBSQLITE3_CFLAGS={i}", f"LIBSQLITE3_LIBS={L} -lsqlite3",
                f"TCLTK_CFLAGS={tcltk_c}", f"TCLTK_LIBS={tcltk_l}"]
    autoconf("cpython", ["--enable-shared", "--without-readline", "--disable-test-modules",
                         "--with-system-libmpdec", f"--with-openssl={P}", "--with-ensurepip=no"],
             env="cpython", extra=py_extra, make_args=(PYTHON_FOR_BUILD_ARG,))

    # 10 pip: ensurepip from the bundled wheels, isolated (-I propagates to its pip child), offline.
    add("cpython", "ensurepip", [os.path.join(P, "bin", "python3.14"), "-I", "-m", "ensurepip", "--upgrade"],
        c.build("cpython"), env="ensurepip")
    return plan


def argv_of(step):
    return step["argv"]() if callable(step["argv"]) else step["argv"]


def check_argv_safe(argv):
    if not argv or not os.path.isabs(argv[0]):
        raise BuildFail(f"argv[0] must be an absolute path: {argv[:1]}")
    for a in argv:
        value = a.split("=", 1)[1] if a.startswith("--") and "=" in a else a
        for bad in FORBIDDEN_ARG_ROOTS:
            if value == bad or value.startswith(bad + "/"):
                raise BuildFail(f"argument points at forbidden location {bad}: {a}")
        if value.startswith(("-I", "-L")) and value[2:].startswith(FORBIDDEN_ARG_ROOTS):
            raise BuildFail(f"argument points at forbidden location: {a}")


def check_env_safe(env):
    for k, v in env.items():
        if (k, v) == NO_USER_SITE_ENV:
            continue                                 # the one explicit, fixed PYTHON* setting
        if k.startswith(("DYLD", "HOMEBREW", "GIT_", "PYTHON")):
            raise BuildFail(f"forbidden inherited variable {k}")
        for bad in FORBIDDEN_ENV_SUBSTR:
            if bad in v:
                raise BuildFail(f"environment {k} mentions {bad}")


# ---------------------------------------------------------------------------
# Flag / target preflight against extracted text (blocking, never auto-changed)
# ---------------------------------------------------------------------------
def read_text(path):
    with open(path, "r", errors="replace") as f:
        return f.read()


def autoconf_flag_present(text, flag):
    m = re.fullmatch(r"--(enable|disable|with|without)-([A-Za-z0-9_+-]+)(=.*)?", flag)
    if not m:
        return None
    fam = ("enable", "disable") if m.group(1) in ("enable", "disable") else ("with", "without")
    name = m.group(2)
    for variant in {name, name.replace("-", "_")}:
        if re.search(r"--(?:%s|%s)-%s(?![A-Za-z0-9_-])" % (fam[0], fam[1], re.escape(variant)), text):
            return True
    return False


def sqlite_option_modes(cfg_text):
    """Option name -> build modes it is declared for, from autosetup/sqlite-config.tcl."""
    modes, found = None, {}
    for line in cfg_text.splitlines():
        h = re.match(r"^ {6}\{([^}]*)\} \{$", line)
        if h:
            modes = h.group(1).split()
            continue
        o = re.match(r"^ {8}([a-z][a-z0-9-]*)(?:[=:]|\s*=>|\s*$)", line)
        if o and modes is not None:
            found.setdefault(o.group(1), set()).update(modes)
    return found


def preflight(ctx, srcdir_of):
    """Return a list of check results; any ok=False is blocking."""
    results = []

    def res(comp, check, ok, detail):
        results.append(dict(component=comp, check=check, ok=bool(ok), detail=detail))

    def need_file(comp, rel):
        p = os.path.join(srcdir_of(comp), rel)
        if not os.path.isfile(p):
            res(comp, f"file {rel}", False, "missing")
            return None
        return read_text(p)

    # OpenSSL
    t = need_file("openssl", "Configurations/10-main.conf")
    if t is not None:
        for tgt in ("darwin64-arm64-cc", "darwin64-x86_64-cc"):
            res("openssl", f"Configure target {tgt}", f'"{tgt}"' in t, "10-main.conf")
    t = need_file("openssl", "Configure")
    if t is not None:
        for opt in ("shared", "tests"):
            res("openssl", f"Configure option no-{opt}", f'"{opt}"' in t, "disablables list in Configure")
    t = need_file("openssl", "Configurations/unix-Makefile.tmpl")
    if t is not None:
        res("openssl", "make target install_sw", re.search(r"^install_sw:", t, re.M), "unix-Makefile.tmpl")

    # Plain autoconf components: every --enable/--disable/--with/--without flag in the plan
    flag_sets = {
        "libffi": ("configure", ["--disable-shared", "--enable-static"]),
        "xz": ("configure", ["--disable-shared", "--enable-static", "--disable-xz", "--disable-xzdec",
                             "--disable-lzmadec", "--disable-lzmainfo", "--disable-scripts", "--disable-doc"]),
        "mpdecimal": ("configure", ["--disable-shared", "--enable-static", "--disable-cxx"]),
        "tcl": ("unix/configure", ["--enable-shared", "--disable-framework", "--disable-zipfs"]),
        "tk": ("unix/configure", ["--enable-aqua", "--enable-shared", "--disable-framework", "--disable-zipfs",
                             "--with-tcl=DIR"]),
        "cpython": ("configure", ["--enable-shared", "--without-readline", "--disable-test-modules",
                                  "--with-system-libmpdec", "--with-openssl=DIR",
                                  "--with-ensurepip=no"]),
    }
    for comp, (rel, flags) in flag_sets.items():
        t = need_file(comp, rel)
        if t is None:
            continue
        for fl in flags:
            res(comp, f"configure flag {fl}", autoconf_flag_present(t, fl), rel)
    t = need_file("cpython", "configure")
    if t is not None:
        for var in ("LIBFFI_CFLAGS", "LIBFFI_LIBS", "LIBLZMA_CFLAGS", "LIBLZMA_LIBS", "LIBZSTD_CFLAGS",
                    "LIBZSTD_LIBS", "LIBMPDEC_CFLAGS", "LIBMPDEC_LIBS", "LIBSQLITE3_CFLAGS",
                    "LIBSQLITE3_LIBS", "TCLTK_CFLAGS", "TCLTK_LIBS"):
            res("cpython", f"precious variable {var}", re.search(r"^  %s(?:\s|$)" % var, t, re.M), "configure help")

    # SQLite: autosetup, not autoconf.  Option availability depends on the build mode.
    auto = need_file("sqlite", "auto.def")
    cfg = need_file("sqlite", "autosetup/sqlite-config.tcl")
    if auto is not None and cfg is not None:
        mode_m = re.search(r"^sqlite-configure\s+(\w+)\s*\{", auto, re.M)
        mode = mode_m.group(1) if mode_m else None
        res("sqlite", "build mode of this bundle", mode is not None, f"auto.def calls sqlite-configure {mode}")
        opts = sqlite_option_modes(cfg)
        for name, flag in (("shared", "--disable-shared"), ("static", "--enable-static"),
                           ("readline", "--disable-readline")):
            modes = opts.get(name)
            ok = bool(modes) and (mode in modes or "*" in modes)
            res("sqlite", f"configure flag {flag}", ok,
                f"option '{name}' declared for build mode(s) {sorted(modes) if modes else 'none'} in "
                f"autosetup/sqlite-config.tcl; this bundle is '{mode}'"
                + ("" if ok else " -> autosetup rejects unknown options; BLOCKING, recipe unchanged"))

    # zstd
    t = need_file("zstd", "lib/Makefile")
    if t is not None:
        for tg in (r"libzstd\.a", "install-static", "install-includes", "install-pc"):
            res("zstd", f"lib/Makefile target {tg}", re.search(r"^%s:" % tg, t, re.M), "lib/Makefile")
        res("zstd", "lib/Makefile honours PREFIX", re.search(r"^PREFIX\s*\?=", t, re.M), "lib/Makefile")
    return results


# ---------------------------------------------------------------------------
# Archive verification and extraction
# ---------------------------------------------------------------------------
def xattr_names(path):
    rc, out, err = sysrun(["/usr/bin/xattr", path])
    if rc != 0:
        raise BuildFail(f"xattr read failed for {path}: {err.strip()[:200]}")
    return sorted(l.strip() for l in out.splitlines() if l.strip())


def verify_catalog_and_archives(ctx):
    cat_path = os.path.join(ctx.root, "SOURCE-INPUTS.json")
    with open(cat_path) as f:
        cat = json.load(f)
    listed = {s["name"]: s for s in cat["sources"]}
    if set(listed) != set(BY_NAME) or len(cat["sources"]) != 9:
        raise BuildFail("SOURCE-INPUTS.json component set differs from the script's fixed set")
    if cat.get("deployment_target") != DEPLOYMENT_TARGET:
        raise BuildFail("SOURCE-INPUTS.json deployment_target differs from the script")
    out = []
    for c in COMPONENTS:
        s = listed[c["name"]]
        if s["sha256"] != c["sha256"] or s["version"] != c["version"] \
                or os.path.basename(s["url"]) != c["file"]:
            raise BuildFail(f"{c['name']}: SOURCE-INPUTS.json disagrees with the script's fixed digest/version")
        path = os.path.join(ctx.downloads, c["file"])
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_size > ARCHIVE_CAP:
            raise BuildFail(f"{c['file']}: not a regular file or larger than {ARCHIVE_CAP} bytes")
        digest = sha256_path(path)
        if digest != c["sha256"]:
            raise BuildFail(f"{c['file']}: sha256 mismatch ({digest})")
        names = xattr_names(path)
        if any(n.startswith("com.apple.quarantine") for n in names):
            raise BuildFail(f"{c['file']}: quarantine xattr present; refusing (not removing)")
        out.append(dict(name=c["name"], file=c["file"], bytes=st.st_size, sha256=digest, xattr_names=names))
    return hashlib.sha256(open(cat_path, "rb").read()).hexdigest(), out


def inspect_tar(path, root, ctx):
    stats = dict(members=0, files=0, dirs=0, symlinks=0, hardlinks=0, unpacked_bytes=0)
    total = 0

    def link_ok(base, link):
        if os.path.isabs(link):
            return False
        tgt = os.path.normpath(os.path.join(base, link))
        return inside(tgt, root)

    with tarfile.open(path, "r:gz") as tf:
        for m in tf:
            if ctx.remaining() <= 0:
                raise BuildFail("total time budget exhausted during archive inspection")
            stats["members"] += 1
            if stats["members"] > MEMBER_CAP:
                raise BuildFail(f"{path}: more than {MEMBER_CAP} members")
            name = m.name
            if name.startswith("./"):
                name = name[2:]
            parts = [p for p in name.split("/") if p not in ("",)]
            if name.startswith("/") or "\x00" in name or ".." in parts or not parts or parts[0] != root:
                raise BuildFail(f"{path}: absolute, escaping or unexpected-root member {m.name!r}")
            if "__MACOSX" in parts or any(p.startswith("._") for p in parts):
                raise BuildFail(f"{path}: AppleDouble member {m.name!r}")
            for k in m.pax_headers:
                if "xattr" in k.lower() or k.upper().startswith("APPLE."):
                    raise BuildFail(f"{path}: PAX extended attribute header {k!r} on {m.name!r}")
            if m.isreg():
                stats["files"] += 1
                total += m.size
            elif m.isdir():
                stats["dirs"] += 1
            elif m.issym():
                stats["symlinks"] += 1
                if not link_ok(os.path.dirname("/".join(parts)), m.linkname):
                    raise BuildFail(f"{path}: symlink escapes the source root: {m.name!r} -> {m.linkname!r}")
            elif m.islnk():
                stats["hardlinks"] += 1
                if not link_ok("", m.linkname):
                    raise BuildFail(f"{path}: hardlink escapes the source root: {m.name!r} -> {m.linkname!r}")
            else:
                raise BuildFail(f"{path}: device/FIFO/other member type refused: {m.name!r}")
            if total > UNPACK_CAP:
                raise BuildFail(f"{path}: unpacked size exceeds {UNPACK_CAP} bytes")
        for k in tf.pax_headers:
            if "xattr" in k.lower() or k.upper().startswith("APPLE."):
                raise BuildFail(f"{path}: global PAX extended attribute header {k!r}")
    stats["unpacked_bytes"] = total
    return stats


def verify_extracted(dest, root, ctx):
    if sorted(os.listdir(dest)) != [root]:
        raise BuildFail(f"{dest}: expected exactly one top-level folder {root}")
    n, size = 0, 0
    for d, dirs, files in os.walk(dest, followlinks=False):
        for name in dirs + files:
            p = os.path.join(d, name)
            st = os.lstat(p)
            n += 1
            if name.startswith("._"):
                raise BuildFail(f"AppleDouble file after extraction: {p}")
            if stat.S_ISLNK(st.st_mode):
                if not inside(os.path.realpath(p), os.path.realpath(dest)):
                    raise BuildFail(f"symlink escapes extraction folder: {p}")
            elif stat.S_ISREG(st.st_mode):
                size += st.st_size
            elif not stat.S_ISDIR(st.st_mode):
                raise BuildFail(f"special file after extraction: {p}")
    if size > UNPACK_CAP:
        raise BuildFail(f"{dest}: extracted size exceeds cap")
    reject_quarantine(dest, ctx)
    return dict(entries=n, bytes=size)


def reject_quarantine(path, ctx):
    rc, out, err = sysrun(["/usr/bin/xattr", "-r", "-s", path], timeout=300)
    if rc != 0:
        raise BuildFail(f"xattr scan failed for {path}: {err.strip()[:200]}")
    if "com.apple.quarantine" in out:
        raise BuildFail(f"quarantine attribute found under {path}; refusing (not removing)")


def extract_all(ctx):
    rec = []
    for c in COMPONENTS:
        archive = os.path.join(ctx.downloads, c["file"])
        dest = os.path.join(ctx.out, "sources", c["name"])
        os.mkdir(dest, 0o700)                      # new, empty, own folder
        stats = inspect_tar(archive, c["root"], ctx)
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(dest, filter="data")
        post = verify_extracted(dest, c["root"], ctx)
        after = sha256_path(archive)
        if after != c["sha256"]:
            raise BuildFail(f"{c['file']} changed during extraction")
        rec.append(dict(name=c["name"], dest=dest, archive_sha256_after=after, tar=stats, extracted=post))
        print(f"[extract] {c['name']}: {stats['members']} members, {post['bytes']} bytes", flush=True)
    return rec


# ---------------------------------------------------------------------------
# Process runner
# ---------------------------------------------------------------------------
def kill_group(pgid):
    for sig, wait in ((signal.SIGTERM, 5), (signal.SIGKILL, 0)):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            return
        if wait:
            time.sleep(wait)


def run_step(ctx, step_id, argv, cwd, env):
    check_argv_safe(argv)
    check_env_safe(env)
    limit = min(STEP_TIMEOUT, ctx.remaining())
    if limit <= 0:
        raise BuildFail("total time budget exhausted before this step")
    log = os.path.join(ctx.logs, step_id + ".log")
    rec = dict(id=step_id, argv=argv, cwd=cwd, started=utc_now(), log=log,
               env_keys=sorted(env), limit_seconds=round(limit, 1))
    t0 = time.monotonic()
    status = "ok"
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as lf:
            p = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=lf,
                                 stderr=subprocess.STDOUT, start_new_session=True)
            while True:
                try:
                    p.wait(timeout=1.0)
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() - t0 > limit:
                        status = "timeout"
                    elif os.fstat(lf.fileno()).st_size > LOG_CAP:
                        status = "log-cap"
                    if status != "ok":
                        kill_group(p.pid)
                        p.wait()
                        break
            try:
                os.killpg(p.pid, signal.SIGKILL)   # stragglers of the process group
            except (ProcessLookupError, PermissionError):
                pass
    except OSError as e:
        rec.update(status="spawn-error", error=repr(e), seconds=round(time.monotonic() - t0, 1))
        return rec
    rec["seconds"] = round(time.monotonic() - t0, 1)
    rec["returncode"] = p.returncode
    rec["log_bytes"] = os.path.getsize(log)
    rec["ended"] = utc_now()
    if status == "ok" and p.returncode != 0:
        status = "failed"
    rec["status"] = status
    if status != "ok":
        with open(log, "rb") as f:
            f.seek(max(0, rec["log_bytes"] - 3000))
            rec["log_tail"] = f.read().decode("utf-8", "replace")
    return rec


# ---------------------------------------------------------------------------
# Install-target inspection
# ---------------------------------------------------------------------------
# `oldincludedir` is deliberately absent: it is an unused Automake compatibility variable that
# configure defaults to /usr/include whatever --prefix says; nothing installs into it.  The real
# includedir/bindir/libdir/mandir checks below stay blocking.
INSTALL_VARS = {
    "prefix", "exec_prefix", "bindir", "sbindir", "libexecdir", "libdir", "includedir",
    "datadir", "datarootdir", "mandir", "infodir", "docdir", "htmldir", "dvidir", "pdfdir", "psdir",
    "localedir", "sysconfdir", "sharedstatedir", "localstatedir", "runstatedir", "pkgconfigdir",
    "pkgdatadir", "pkgincludedir", "pkglibdir", "installtop", "openssldir", "enginesdir", "modulesdir",
    "libpl", "libdest", "scriptdir", "confincludedir", "confincludepy", "includepy", "binlibdest",
}


# Tcl/Tk Makefile variables whose names end in _install_dir but are not install destinations:
# INSTALL_DATA_DIR is the `install -d -m 755` command; HTML_INSTALL_DIR is used only by the explicit
# html-tcl/html-tk generation targets (normal install-doc uses the MAN*_INSTALL_DIR paths).
NON_DESTINATION_INSTALL_DIRS = {"install_data_dir", "html_install_dir"}


def parse_makefile_vars(text):
    found = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)[ \t]*(?::=|\?=|=)[ \t]*(.*?)[ \t]*$", line)
        if m and m.group(1) not in found:
            found[m.group(1)] = m.group(2)
    return found


def expand_make(val, mv):
    for _ in range(12):
        new = re.sub(r"\$[({]([A-Za-z_][A-Za-z0-9_]*)[)}]", lambda m: mv.get(m.group(1), m.group(0)), val)
        if new == val:
            break
        val = new
    return val


def inspect_makefile(ctx, makefile):
    if not os.path.isfile(makefile):
        raise BuildFail(f"install-target metadata missing: {makefile}")
    mv = parse_makefile_vars(read_text(makefile))
    mv.setdefault("DESTDIR", "")
    mv.setdefault("INSTALL_ROOT", "")
    for k in ("DESTDIR", "INSTALL_ROOT"):
        if expand_make(mv[k], mv).strip():            # unresolved or non-empty after expansion
            raise BuildFail(f"{makefile}: {k} is set; install would leave the prefix")
    checked, unresolved, relative, bad = {}, [], [], []
    for name, raw in sorted(mv.items()):
        ln = name.lower()
        if ln in NON_DESTINATION_INSTALL_DIRS or not (ln in INSTALL_VARS or ln.endswith("_install_dir")):
            continue
        val = expand_make(raw, mv)
        if "$" in val or "@" in val:
            unresolved.append(name)
        elif not val.startswith("/"):
            relative.append(name)
        else:
            checked[name] = val
            if not inside(val, ctx.prefix):
                bad.append(f"{name}={val}")
    if "prefix" not in checked and "INSTALLTOP" not in checked:
        bad.append("no resolved prefix/INSTALLTOP found")
    for n in ("prefix", "exec_prefix", "INSTALLTOP"):
        if n in checked and os.path.normpath(checked[n]) != os.path.normpath(ctx.prefix):
            bad.append(f"{n}={checked[n]} is not the private prefix")
    res = dict(makefile=makefile, checked=checked, unresolved=unresolved, relative=relative, violations=bad)
    ctx.rec.setdefault("install_inspections", []).append(res)
    if bad:
        raise BuildFail(f"{makefile}: install settings outside the private prefix: {bad}")


# The pinned zstd 1.5.7 lib/Makefile line 354 (`[ -e libzstd.a ] || $(MAKE) libzstd.a-release`) as
# `make -n` prints it.  Known recipe text, not a general shell parser: only the make token is exempt.
ZSTD_KNOWN_MAKE_LINE = ["[", "-e", "libzstd.a", "]", "||", "/usr/bin/make", "libzstd.a-release"]


def zstd_dryrun(ctx, step_log):
    text = read_text(step_log)
    seen, bad = 0, []
    for line in text.splitlines():
        try:
            toks = shlex.split(line)
        except ValueError:
            toks = line.split()
        if not toks:
            continue
        first = os.path.basename(toks[0])
        if not (toks[0] == "[" or first in ("install", "ln", "cp", "mkdir", "chmod", "mv", "rm")):
            continue
        seen += 1
        for i, t in enumerate(toks):
            if toks == ZSTD_KNOWN_MAKE_LINE and i == 5:
                continue
            if t.startswith("/") and not (inside(t, ctx.prefix) or inside(t, ctx.out)):
                bad.append(t)
    ctx.rec.setdefault("install_inspections", []).append(dict(zstd_dryrun_lines=seen, violations=bad))
    if seen == 0:
        raise BuildFail("zstd dry-run produced no install commands to inspect")
    if bad:
        raise BuildFail(f"zstd install would write outside the private prefix: {bad}")


def read_sh_config(ctx, path, script, fields):
    r = subprocess.run(["/bin/sh", "-c", script, "sh", path], capture_output=True,
                       env=minimal_env(), stdin=subprocess.DEVNULL, timeout=30)
    if r.returncode != 0:
        raise BuildFail(f"could not read {path}: {r.stderr.decode('utf-8', 'replace')[:200]}")
    vals = r.stdout.decode("utf-8", "replace").split("\0")[:-1]
    if len(vals) != len(fields):
        raise BuildFail(f"{path}: unexpected field count")
    return dict(zip(fields, vals))


def spec_check(spec, prefix, label):
    problems = []
    for tok in shlex.split(spec):
        path = tok[2:] if tok.startswith(("-I", "-L", "-F")) else (tok if tok.startswith("/") else None)
        if path and not inside(path, prefix):
            problems.append(f"{label}: {tok}")
        if "X11" in tok:
            problems.append(f"{label}: X11 reference {tok}")
    return problems


def tcl_meta(ctx):
    cfg = os.path.join(ctx.prefix, "lib", "tclConfig.sh")
    v = read_sh_config(ctx, cfg, SH_TCL, TCL_FIELDS)
    bad = []
    for k in ("TCL_PREFIX", "TCL_EXEC_PREFIX"):
        if not inside(v[k], ctx.prefix):
            bad.append(f"{k}={v[k]}")
    for k in ("TCL_INCLUDE_SPEC", "TCL_LIB_SPEC", "TCL_STUB_LIB_SPEC"):
        bad += spec_check(v[k], ctx.prefix, k)
    patch = v["TCL_VERSION"] + v["TCL_PATCH_LEVEL"]
    if patch != "9.0.4":
        bad.append(f"Tcl patch level {patch!r} is not 9.0.4")
    if not any(f.startswith("libtcl9") and f.endswith(".dylib") for f in os.listdir(os.path.join(ctx.prefix, "lib"))):
        bad.append("libtcl9*.dylib not found in prefix/lib")
    ctx.rec["tcl_config"] = dict(values=v, violations=bad, patch_level=patch)
    ctx.tcltk = dict(tcl=v)
    if bad:
        raise BuildFail(f"Tcl installed metadata does not resolve inside the prefix: {bad}")


def tk_meta(ctx):
    cfg = os.path.join(ctx.prefix, "lib", "tkConfig.sh")
    v = read_sh_config(ctx, cfg, SH_TK, TK_FIELDS)
    bad = []
    for k in ("TK_PREFIX", "TK_EXEC_PREFIX"):
        if not inside(v[k], ctx.prefix):
            bad.append(f"{k}={v[k]}")
    for k in ("TK_INCLUDE_SPEC", "TK_LIB_SPEC", "TK_STUB_LIB_SPEC"):
        bad += spec_check(v[k], ctx.prefix, k)
    if "X11" in v["TK_XINCLUDES"]:
        bad.append(f"TK_XINCLUDES mentions X11: {v['TK_XINCLUDES']}")
    # Tk built against Tcl 9 names its library libtcl9tk9.0.dylib (TK_LIB_FILE_TCL9), not libtk9*.
    if not any(f.startswith("libtcl9tk9") and f.endswith(".dylib")
               for f in os.listdir(os.path.join(ctx.prefix, "lib"))):
        bad.append("libtcl9tk9*.dylib not found in prefix/lib")
    t = ctx.tcltk["tcl"]
    ctx.rec["tk_config"] = dict(values=v, violations=bad)
    if bad:
        raise BuildFail(f"Tk installed metadata does not resolve inside the prefix / is not Aqua: {bad}")
    ctx.tcltk = dict(
        cflags=" ".join([t["TCL_INCLUDE_SPEC"], v["TK_INCLUDE_SPEC"]]).strip(),
        libs=" ".join([t["TCL_LIB_SPEC"], v["TK_LIB_SPEC"]]).strip(), tcl=t, tk=v)
    ctx.rec["tcltk_for_cpython"] = dict(cflags=ctx.tcltk["cflags"], libs=ctx.tcltk["libs"])


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------
def final_probe(ctx):
    py = os.path.join(ctx.prefix, "bin", "python3.14")
    if not os.path.isfile(py):
        raise BuildFail(f"{py} not found after install")
    reject_quarantine(ctx.prefix, ctx)
    env = dict(minimal_env(), TMPDIR=ctx.tmp + "/", **{NO_USER_SITE_ENV[0]: NO_USER_SITE_ENV[1]})
    r = subprocess.run([py, "-I", "-B", "-c", PROBE], capture_output=True, env=env, timeout=120,
                       stdin=subprocess.DEVNULL, cwd=ctx.tmp)
    ctx.rec["probe_returncode"] = r.returncode
    if r.returncode != 0:
        raise BuildFail("probe failed: " + r.stderr.decode("utf-8", "replace")[-500:])
    p = json.loads(r.stdout.decode())
    ctx.rec["probe"] = p
    tcltk = p.get("tcl_tk") if isinstance(p.get("tcl_tk"), dict) else {}
    loc = p.get("locations", {})
    checks = {
        "python_version_3.14.10": p.get("version") == "3.14.10",
        "implementation_cpython": p.get("implementation") == "cpython",
        "gil_not_disabled": p.get("gil_enabled") is True and not p.get("py_gil_disabled_config"),
        "native_arch": p.get("machine") == ctx.arch,
        "sysconfig_target_13.0": p.get("deployment_target") == DEPLOYMENT_TARGET,
        "nonframework": not p.get("framework_build"),
        "all_imports_ok": all(v == "ok" for v in p.get("imports", {}).values()) and len(p.get("imports", {})) == 11,
        "tcl_patch_9.0.4": tcltk.get("patchlevel") == "9.0.4",
        "tk_major_9": isinstance(tcltk.get("tk_version"), (int, float)) and int(tcltk["tk_version"]) == 9,
        "openssl_3.5.9": str(p.get("openssl_version", "")).startswith("OpenSSL 3.5.9"),
        "sqlite_3.50.4": p.get("sqlite_version") == "3.50.4",
        "libmpdec_4.0.1": p.get("decimal_libmpdec_version") == "4.0.1",
        "tkinter_ssl_ctypes_inside_prefix": all(
            isinstance(loc.get(m), str) and inside(loc[m], ctx.prefix) for m in ("_tkinter", "_ssl", "_ctypes")),
        "sys_prefix_is_private_prefix": inside(p.get("prefix", ""), ctx.prefix),
        "pip_inside_prefix": isinstance(p.get("pip_file"), str) and inside(p["pip_file"], ctx.prefix),
        "no_user_site_flag": p.get("no_user_site") == 1,
    }
    ctx.rec["probe_checks"] = checks
    failed = [k for k, v in checks.items() if not v]
    if failed:
        raise BuildFail("probe checks failed: " + ", ".join(failed))


# ---------------------------------------------------------------------------
# Toolchain / platform
# ---------------------------------------------------------------------------
def discover_toolchain(ctx):
    if sys.platform != "darwin":
        raise BuildFail("native macOS only")
    if ctx.arch not in ("arm64", "x86_64"):
        raise BuildFail(f"unsupported machine {ctx.arch!r}")
    rc, translated, _ = sysrun(["/usr/sbin/sysctl", "-n", "sysctl.proc_translated"])
    rc2, arm_hw, _ = sysrun(["/usr/sbin/sysctl", "-n", "hw.optional.arm64"])
    if (rc == 0 and translated.strip() == "1") or (ctx.arch == "x86_64" and rc2 == 0 and arm_hw.strip() == "1"):
        raise BuildFail("running under Rosetta / non-native architecture; refused")

    def xc(*args):
        rc, out, err = sysrun(["/usr/bin/xcrun", "--sdk", "macosx", *args])
        if rc != 0 or not out.strip():
            raise BuildFail(f"xcrun {' '.join(args)} failed: {err.strip()[:200]}")
        return out.strip()
    ctx.sdk = xc("--show-sdk-path")
    sdk_version = xc("--show-sdk-version")
    ctx.cc = xc("--find", "clang")
    ctx.cxx = xc("--find", "clang++")
    for label, v in (("sdk", ctx.sdk), ("clang", ctx.cc), ("clang++", ctx.cxx)):
        if not os.path.isabs(v) or not SAFE_PATH_RE.fullmatch(v):
            raise BuildFail(f"{label} path is not a plain absolute path: {v!r}")
    if not os.path.isdir(ctx.sdk):
        raise BuildFail(f"SDK directory missing: {ctx.sdk}")
    _, clang_ver, _ = sysrun([ctx.cc, "--version"])
    _, dev_dir, _ = sysrun(["/usr/bin/xcode-select", "-p"])
    ctx.rec["toolchain"] = dict(arch=ctx.arch, sdk_path=ctx.sdk, sdk_version=sdk_version, clang=ctx.cc,
                                clangxx=ctx.cxx, clang_version=clang_ver.strip(),
                                developer_dir=dev_dir.strip(), macos=platform.mac_ver()[0],
                                uname=list(os.uname()))


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------
def print_plan(ctx, advisory):
    print("RES isolated Mac source-build — PLAN ONLY (nothing is created or run)")
    print(f"experiment root : {ctx.root}")
    print(f"output folder   : {ctx.out}  (will be created 0700 with --run)")
    print(f"prefix          : {ctx.prefix}")
    print(f"native arch     : {ctx.arch} (Rosetta check, SDK and clang are resolved at --run)")
    print(f"budget          : {STEP_TIMEOUT}s per command, {TOTAL_BUDGET}s total, no retries, make -j{JOBS}")
    print("child env       : " + json.dumps(ctx.env("base"), indent=2))
    print("  (sqlite adds to CFLAGS: " + SQLITE_EXTRA_CFLAGS + "; cpython step appends <prefix>/bin to PATH)")
    print("ensurepip env   : " + json.dumps(ctx.ensurepip_env(), indent=2))
    print("archives        : verified against fixed SHA-256 before any extraction")
    for c in COMPONENTS:
        print(f"  {c['name']:10s} {c['version']:30s} {c['file']}  {c['sha256']}")
    print("steps:")
    for s in build_plan(ctx):
        print(f"  [{s['id']}] cwd={s['cwd']}\n      {shlex.join(argv_of(s))}")
    print("final probe     : <prefix>/bin/python3.14 -I -B -c <headless JSON probe>; Tcl interpreter only, no Tk GUI;")
    print("                  also records pip.__file__ (must be inside the prefix) and the no_user_site flag")
    if advisory is None:
        print("advisory flag check: parent-extracted sources/ not present; run --run to check")
    else:
        bad = [r for r in advisory if not r["ok"]]
        print(f"advisory flag check against parent-extracted sources/ (hashes not re-verified here): "
              f"{len(advisory)} checks, {len(bad)} blocking")
        for r in bad:
            print(f"  BLOCKING {r['component']}: {r['check']} — {r['detail']}")
        if bad:
            print("  --run will stop before building anything while any check is blocking.")
    print("Plan only. Re-run with --run to execute.")


def do_run(ctx):
    ctx.rec.update(schema=1, purpose="diagnostic build only; not RES accepted, not signed, no product tests",
                   started=utc_now(), status="running", accepted=False, signed=False)
    try:
        discover_toolchain(ctx)
        os.mkdir(ctx.out, 0o700)
        for d in ("logs", "tmp", "sources", "build", "prefix"):
            os.mkdir(os.path.join(ctx.out, d), 0o700)
        for d in ("include", "lib"):
            os.mkdir(os.path.join(ctx.prefix, d), 0o700)
        ctx.rec["out"] = ctx.out
        ctx.rec["script_sha256"] = sha256_path(os.path.realpath(__file__))
        ctx.rec["python_running_script"] = sys.version
        ctx.rec["budget"] = dict(step=STEP_TIMEOUT, total=TOTAL_BUDGET, jobs=JOBS)
        ctx.rec["base_env"] = ctx.env("base")
        ctx.save()

        ctx.rec["catalog_sha256"], ctx.rec["archives"] = verify_catalog_and_archives(ctx)
        ctx.save()
        ctx.rec["extraction"] = extract_all(ctx)
        ctx.save()

        results = preflight(ctx, lambda comp: ctx.src(comp))
        ctx.rec["flag_preflight"] = results
        ctx.save()
        blocking = [r for r in results if not r["ok"]]
        if blocking:
            ctx.rec["blocking"] = blocking
            raise Blocked("; ".join(f"{r['component']} {r['check']}" for r in blocking))

        ctx.rec["steps"] = []
        for step in build_plan(ctx):
            if step["mkbuild"]:
                os.mkdir(step["cwd"], 0o700)
            argv = argv_of(step)
            print(f"[{step['id']}] {shlex.join(argv)}", flush=True)
            res = run_step(ctx, step["id"], argv, step["cwd"], ctx.env_for(step["env"]))
            ctx.rec["steps"].append(res)
            ctx.save()
            if res["status"] != "ok":
                raise BuildFail(f"step {step['id']} {res['status']} (see {res['log']})")
            hook = step["hook"]
            if hook == "inspect_makefile":
                inspect_makefile(ctx, os.path.join(step["cwd"], "Makefile"))
            elif hook == "zstd_dryrun":
                zstd_dryrun(ctx, res["log"])
            elif hook == "tcl_meta":
                tcl_meta(ctx)
            elif hook == "tk_meta":
                tk_meta(ctx)
            ctx.save()

        final_probe(ctx)
        ctx.rec["status"] = "diagnostic-build-complete"
        ctx.rec["total_seconds"] = round(time.monotonic() - ctx.t0, 1)
        ctx.rec["ended"] = utc_now()
        ctx.save()
        print(f"RESULT: diagnostic build complete in {ctx.rec['total_seconds']}s — not RES accepted, "
              f"not signed; receipt {ctx.out}/BUILD-RECEIPT.json")
        return 0
    except Blocked as e:
        ctx.rec.update(status="blocked", reason=str(e), ended=utc_now())
        ctx.save()
        print(f"BLOCKED — {e}; nothing was built. Receipt {ctx.out}/BUILD-RECEIPT.json")
        return 3
    except (BuildFail, OSError, subprocess.SubprocessError, tarfile.TarError, ValueError, KeyError) as e:
        ctx.rec.update(status="failed", reason=repr(e), ended=utc_now())
        if os.path.isdir(ctx.tmp):
            ctx.save()
        print(f"FAIL — {e}")
        return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="new child folder of the experiment root")
    ap.add_argument("--run", action="store_true", help="actually build (default: print plan only)")
    a = ap.parse_args()

    root = os.path.realpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))
    if not SAFE_PATH_RE.fullmatch(root) or not os.path.isfile(os.path.join(root, "SOURCE-INPUTS.json")) \
            or not os.path.isdir(os.path.join(root, "downloads")):
        print("FAIL — experiment root is not the expected folder (needs SOURCE-INPUTS.json and downloads/)")
        return 1
    try:
        out = validate_out(root, a.out)
    except BuildFail as e:
        print(f"FAIL — {e}")
        return 1
    ctx = Ctx(root, out, a.run)
    if not a.run:
        advisory = None
        if all(os.path.isdir(os.path.join(root, "sources", c["name"], c["root"])) for c in COMPONENTS):
            advisory = preflight(ctx, lambda comp: os.path.join(root, "sources", comp, BY_NAME[comp]["root"]))
        print_plan(ctx, advisory)
        return 0
    os.umask(0o022)
    return do_run(ctx)


if __name__ == "__main__":
    sys.exit(main())
