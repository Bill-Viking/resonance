#!/usr/bin/env python3
"""Fixture tests for the install-target guards in build-local.py (standard library only).

Run with Python 3.12+:  python3 -I scripts/test_build_local.py

Nothing is compiled or downloaded and no make, compiler, GUI or network is used.  Every input is a
synthetic temporary fixture: Makefile text taken from the exact upstream templates with synthetic
prefix paths filled in, a pasted `make -n` log, mocked generated Tk config values and an empty fake
dylib.  Only the script's own text/path guards run; no real user data is read.
"""

import importlib.util
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = os.path.join(os.path.dirname(os.path.realpath(__file__)), "build-local.py")
_spec = importlib.util.spec_from_file_location("build_local_under_test", SCRIPT)
bl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bl)


def fill(template, values):
    """Replace @var@ placeholders the way configure would, with synthetic values."""
    text = re.sub(r"@([A-Za-z_]+)@", lambda m: values[m.group(1)], template)
    assert "@" not in text, text
    return text


# --- upstream templates (exact lines from the pinned sources, placeholders kept) ------------------
AUTOMAKE_DIRS = """\
bindir = @bindir@
datarootdir = @datarootdir@
docdir = @docdir@
exec_prefix = @exec_prefix@
htmldir = @htmldir@
includedir = @includedir@
libdir = @libdir@
localedir = @localedir@
mandir = @mandir@
oldincludedir = @oldincludedir@
prefix = @prefix@
pkgconfigdir = $(libdir)/pkgconfig
"""

TCL_TEMPLATE = (
    "VERSION\\t\\t\\t= @TCL_VERSION@\n"
    "prefix\\t\\t\\t= @prefix@\n"
    "exec_prefix\\t\\t= @exec_prefix@\n"
    "bindir\\t\\t\\t= @bindir@\n"
    "libdir\\t\\t\\t= @libdir@\n"
    "includedir\\t\\t= @includedir@\n"
    "datarootdir\\t\\t= @datarootdir@\n"
    "mandir\\t\\t\\t= @mandir@\n"
    "INSTALL_ROOT\\t\\t= $(DESTDIR)\n"
    "TCL_LIBRARY\\t\\t= @TCL_LIBRARY@\n"
    "BIN_INSTALL_DIR\\t\\t= $(INSTALL_ROOT)$(bindir)\n"
    "LIB_INSTALL_DIR\\t\\t= $(INSTALL_ROOT)$(libdir)\n"
    "DLL_INSTALL_DIR\\t\\t= @DLL_INSTALL_DIR@\n"
    "SCRIPT_INSTALL_DIR\\t= $(INSTALL_ROOT)$(TCL_LIBRARY)\n"
    "MODULE_INSTALL_DIR\\t= $(SCRIPT_INSTALL_DIR)/../tcl9\n"
    "INCLUDE_INSTALL_DIR\\t= $(INSTALL_ROOT)$(includedir)\n"
    "PRIVATE_INCLUDE_DIR\\t= @PRIVATE_INCLUDE_DIR@\n"
    "PRIVATE_INCLUDE_INSTALL_DIR = $(INSTALL_ROOT)$(PRIVATE_INCLUDE_DIR)\n"
    "MAN_INSTALL_DIR\\t\\t= $(INSTALL_ROOT)$(mandir)\n"
    "MANN_INSTALL_DIR\\t= $(MAN_INSTALL_DIR)/mann\n"
    "HTML_DIR\\t\\t= @HTML_DIR@\n"
    "HTML_INSTALL_DIR\\t= $(INSTALL_ROOT)$(HTML_DIR)\n"
    "CONFIG_INSTALL_DIR\\t= $(INSTALL_ROOT)$(libdir)\n"
    "SHELL\\t\\t\\t= @MAKEFILE_SHELL@\n"
    "INSTALL\\t\\t\\t= $(SHELL) $(UNIX_DIR)/install-sh -c\n"
    "INSTALL_PROGRAM\\t\\t= ${INSTALL} -m 755\n"
    "INSTALL_DATA\\t\\t= ${INSTALL} -m 644\n"
    "INSTALL_DATA_DIR\\t= ${INSTALL} -d -m 755\n"
    "DYLIB_INSTALL_DIR\\t= $(libdir)\n"
    "TOP_DIR\\t\\t\\t= @TCL_SRC_DIR@\n"
    "UNIX_DIR\\t\\t= $(TOP_DIR)/unix\n"
    "DISTROOT = /tmp/dist\n"
    "DISTNAME = tcl${VERSION}${PATCH_LEVEL}\n"
    "DISTDIR\\t = $(DISTROOT)/$(DISTNAME)\n"
).replace("\\t", "\t")

TK_TEMPLATE = (
    "VERSION\\t\\t= @TK_VERSION@\n"
    "prefix\\t\\t\\t= @prefix@\n"
    "exec_prefix\\t\\t= @exec_prefix@\n"
    "bindir\\t\\t\\t= @bindir@\n"
    "libdir\\t\\t\\t= @libdir@\n"
    "includedir\\t\\t= @includedir@\n"
    "datarootdir\\t\\t= @datarootdir@\n"
    "mandir\\t\\t\\t= @mandir@\n"
    "INSTALL_ROOT\\t\\t= $(DESTDIR)\n"
    "TK_LIBRARY\\t\\t= @TK_LIBRARY@\n"
    "BIN_INSTALL_DIR\\t\\t= $(INSTALL_ROOT)$(bindir)\n"
    "LIB_INSTALL_DIR\\t\\t= $(INSTALL_ROOT)$(libdir)\n"
    "DLL_INSTALL_DIR\\t\\t= @DLL_INSTALL_DIR@\n"
    "SCRIPT_INSTALL_DIR\\t= $(INSTALL_ROOT)$(TK_LIBRARY)\n"
    "INCLUDE_INSTALL_DIR\\t= $(INSTALL_ROOT)$(includedir)\n"
    "PRIVATE_INCLUDE_DIR\\t= @PRIVATE_INCLUDE_DIR@\n"
    "PRIVATE_INCLUDE_INSTALL_DIR = $(INSTALL_ROOT)$(PRIVATE_INCLUDE_DIR)\n"
    "MAN_INSTALL_DIR\\t\\t= $(INSTALL_ROOT)$(mandir)\n"
    "MANN_INSTALL_DIR\\t= $(MAN_INSTALL_DIR)/mann\n"
    "HTML_DIR\\t\\t= @HTML_DIR@\n"
    "HTML_INSTALL_DIR\\t= $(INSTALL_ROOT)$(HTML_DIR)\n"
    "CONFIG_INSTALL_DIR\\t= $(INSTALL_ROOT)$(libdir)\n"
    "DEMO_INSTALL_DIR\\t= $(INSTALL_ROOT)@DEMO_DIR@\n"
    "TK_PKG_DIR\\t\\t= @TK_PKG_DIR@\n"
    "PKG_INSTALL_DIR\\t\\t= $(LIB_INSTALL_DIR)/$(TK_PKG_DIR)\n"
    "SHELL\\t\\t\\t= @SHELL@\n"
    "INSTALL\\t\\t\\t= $(SHELL) $(UNIX_DIR)/install-sh -c\n"
    "INSTALL_DATA\\t\\t= ${INSTALL} -m 644\n"
    "INSTALL_DATA_DIR\\t= ${INSTALL} -d -m 755\n"
    "DYLIB_INSTALL_DIR\\t= $(libdir)\n"
    "SRC_DIR\\t\\t\\t= @srcdir@\n"
    "TOP_DIR\\t\\t\\t= $(SRC_DIR)/..\n"
    "UNIX_DIR\\t\\t= $(TOP_DIR)/unix\n"
    "DISTROOT = /tmp/dist\n"
    "DISTNAME = tk${VERSION}${PATCH_LEVEL}\n"
    "DISTDIR\\t = $(DISTROOT)/$(DISTNAME)\n"
).replace("\\t", "\t")

# The generated OpenSSL Makefile has the perl snippets of unix-Makefile.tmpl already evaluated.
OPENSSL_GENERATED = """\
DESTDIR=
INSTALLTOP=@P@
OPENSSLDIR=@P@/etc/ssl
LIBDIR=lib
libdir=@P@/lib
ENGINESDIR=$(libdir)/engines-3
MODULESDIR=$(libdir)/ossl-modules
BINDIR=bin
bindir=@P@/bin
PKGCONFIGDIR=$(libdir)/pkgconfig
MANDIR=$(INSTALLTOP)/share/man
DOCDIR=$(INSTALLTOP)/share/doc/$(BASENAME)
HTMLDIR=$(DOCDIR)/html
"""

SQLITE_TEMPLATE = """\
prefix      = @prefix@
datadir     = @datadir@
mandir      = @mandir@
includedir  = @includedir@
exec_prefix = @exec_prefix@
bindir      = @bindir@
libdir      = @libdir@
"""

MPDECIMAL_TEMPLATE = """\
srcdir = @srcdir@
prefix = @prefix@
exec_prefix = @exec_prefix@
bindir = @bindir@
includedir = @includedir@
libdir = @libdir@
datarootdir = @datarootdir@
docdir = @docdir@
mandir = @mandir@
"""

CPYTHON_TEMPLATE = """\
prefix=\t\t@prefix@
exec_prefix=\t@exec_prefix@
datarootdir=    @datarootdir@
BINDIR=\t\t@bindir@
LIBDIR=\t\t@libdir@
MANDIR=\t\t@mandir@
INCLUDEDIR=\t@includedir@
CONFINCLUDEDIR=\t$(exec_prefix)/include
PLATLIBDIR=\tlib
SCRIPTDIR=\t$(prefix)/$(PLATLIBDIR)
VERSION=\t3.14
ABI_THREAD=
LDVERSION=\t$(VERSION)$(ABI_THREAD)
BINLIBDEST=\t@BINLIBDEST@
LIBDEST=\t$(SCRIPTDIR)/python$(VERSION)$(ABI_THREAD)
INCLUDEPY=\t$(INCLUDEDIR)/python$(LDVERSION)
CONFINCLUDEPY=\t$(CONFINCLUDEDIR)/python$(LDVERSION)
LIBPL=\t\t@LIBPL@
LIBPC=\t\t$(LIBDIR)/pkgconfig
"""


class Fixture(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = os.path.realpath(self._td.name)
        self.out = os.path.join(self.root, "out")
        self.ctx = bl.Ctx(self.root, self.out, False)
        self.P = self.ctx.prefix
        os.makedirs(os.path.join(self.P, "lib"))

    def auto_values(self, **over):
        v = dict(prefix=self.P, exec_prefix="${prefix}", bindir="${exec_prefix}/bin",
                 libdir="${exec_prefix}/lib", includedir="${prefix}/include",
                 datarootdir="${prefix}/share", datadir="${datarootdir}", docdir="${datarootdir}/doc/pkg",
                 htmldir="${docdir}", localedir="${datarootdir}/locale", mandir="${datarootdir}/man",
                 oldincludedir="/usr/include", srcdir="/synthetic/src",
                 # Tcl/Tk configure substitutions
                 TCL_VERSION="9.0", TK_VERSION="9.0", TCL_LIBRARY="$(libdir)/tcl$(VERSION)",
                 TK_LIBRARY="$(libdir)/tk$(VERSION)", DLL_INSTALL_DIR="$(LIB_INSTALL_DIR)",
                 PRIVATE_INCLUDE_DIR="$(includedir)", HTML_DIR="$(DISTDIR)/html",
                 MAKEFILE_SHELL="/bin/sh", SHELL="/bin/sh", TCL_SRC_DIR="/synthetic/src/tcl9.0.4",
                 TK_PKG_DIR="tk$(VERSION)", DEMO_DIR="$(TK_LIBRARY)/demos",
                 BINLIBDEST="${prefix}/lib/python3.14", LIBPL="${prefix}/lib/python3.14/config-3.14-darwin")
        v.update(over)
        return v

    def inspect(self, text):
        path = os.path.join(self.root, "Makefile")
        with open(path, "w") as f:
            f.write(text)
        bl.inspect_makefile(self.ctx, path)

    def assertAccepted(self, text):
        self.inspect(text)

    def assertRefused(self, text, mention=None):
        with self.assertRaises(bl.BuildFail) as cm:
            self.inspect(text)
        if mention:
            self.assertIn(mention, str(cm.exception))

    def replace_line(self, text, name, new_line):
        out, hit = [], 0
        for line in text.splitlines():
            if re.match(r"^%s\s*=" % re.escape(name), line):
                out.append(new_line)
                hit += 1
            else:
                out.append(line)
        self.assertEqual(hit, 1, name)
        return "\n".join(out) + "\n"


class InspectAutomake(Fixture):
    """H1: libffi / XZ (Automake) Makefiles."""

    def test_default_oldincludedir_accepted(self):
        self.assertAccepted(fill(AUTOMAKE_DIRS, self.auto_values()))
        checked = self.ctx.rec["install_inspections"][-1]["checked"]
        self.assertNotIn("oldincludedir", checked)
        self.assertEqual(checked["includedir"], os.path.join(self.P, "include"))

    def test_real_destinations_outside_prefix_still_refused(self):
        for name, value in (("includedir", "/usr/include"), ("bindir", "/usr/local/bin"),
                            ("libdir", "/usr/lib"), ("mandir", "/usr/share/man")):
            with self.subTest(name=name):
                self.assertRefused(fill(AUTOMAKE_DIRS, self.auto_values(**{name: value})), name)

    def test_prefix_not_the_private_prefix_refused(self):
        self.assertRefused(fill(AUTOMAKE_DIRS, self.auto_values(prefix="/usr/local")))


class InspectTclTk(Fixture):
    """H2: Tcl / Tk Makefiles."""

    templates = (("tcl", TCL_TEMPLATE), ("tk", TK_TEMPLATE))

    def test_normal_makefile_accepted(self):
        for name, tpl in self.templates:
            with self.subTest(component=name):
                self.assertAccepted(fill(tpl, self.auto_values()))
                checked = self.ctx.rec["install_inspections"][-1]["checked"]
                self.assertNotIn("INSTALL_DATA_DIR", checked)     # command, not a destination
                self.assertNotIn("HTML_INSTALL_DIR", checked)     # html generation target only
                self.assertIn("LIB_INSTALL_DIR", checked)

    def test_install_root_alias_of_empty_destdir_accepted(self):
        text = fill(TCL_TEMPLATE, self.auto_values())
        self.assertIn("INSTALL_ROOT\t\t= $(DESTDIR)", text)
        self.assertAccepted(text)

    def test_explicit_empty_destdir_accepted(self):
        self.assertAccepted("DESTDIR =\n" + fill(TK_TEMPLATE, self.auto_values()))

    def test_command_install_data_dir_not_a_destination(self):
        text = fill(TCL_TEMPLATE, self.auto_values())
        self.assertIn("INSTALL_DATA_DIR\t= ${INSTALL} -d -m 755", text)
        self.assertAccepted(text)

    def test_html_install_dir_outside_prefix_accepted(self):
        for name, tpl in self.templates:
            with self.subTest(component=name):
                text = fill(tpl, self.auto_values())
                # DISTROOT = /tmp/dist makes HTML_INSTALL_DIR /tmp/dist/<name>/html: not an install target.
                self.assertIn("DISTROOT = /tmp/dist", text)
                self.assertAccepted(text)

    def test_real_install_dir_outside_prefix_refused(self):
        for name, tpl in self.templates:
            for var in ("LIB_INSTALL_DIR", "BIN_INSTALL_DIR", "MAN_INSTALL_DIR", "INCLUDE_INSTALL_DIR"):
                with self.subTest(component=name, var=var):
                    text = self.replace_line(fill(tpl, self.auto_values()), var,
                                             f"{var} = /synthetic/outside/dest")
                    self.assertRefused(text, var)

    def test_real_libdir_outside_refused(self):
        self.assertRefused(fill(TCL_TEMPLATE, self.auto_values(libdir="/usr/lib")), "LIB_INSTALL_DIR")

    def test_nonempty_destdir_refused(self):
        for name, tpl in self.templates:
            with self.subTest(component=name):
                self.assertRefused("DESTDIR = /synthetic/stage\n" + fill(tpl, self.auto_values()), "DESTDIR")

    def test_nonempty_install_root_refused(self):
        text = self.replace_line(fill(TCL_TEMPLATE, self.auto_values()), "INSTALL_ROOT",
                                 "INSTALL_ROOT = /synthetic/stage")
        self.assertRefused(text, "INSTALL_ROOT")

    def test_unresolved_destdir_refused(self):
        text = self.replace_line(fill(TK_TEMPLATE, self.auto_values()), "INSTALL_ROOT",
                                 "INSTALL_ROOT = $(NOT_DEFINED_ANYWHERE)")
        self.assertRefused(text, "INSTALL_ROOT")
        self.assertRefused("DESTDIR = @DESTDIR@\n" + fill(TK_TEMPLATE, self.auto_values()), "DESTDIR")


class InspectOtherComponents(Fixture):
    """The other inspect_makefile hooks, positive and negative, without compiling."""

    def openssl(self, prefix=None):
        return OPENSSL_GENERATED.replace("@P@", prefix or self.P)

    def test_openssl(self):
        self.assertAccepted(self.openssl())
        self.assertRefused(self.openssl("/usr/local"))
        self.assertRefused(self.openssl().replace("OPENSSLDIR=" + self.P + "/etc/ssl", "OPENSSLDIR=/etc/ssl"),
                           "OPENSSLDIR")
        self.assertRefused(self.openssl().replace("DESTDIR=\n", "DESTDIR=/synthetic/stage\n"), "DESTDIR")

    def test_sqlite(self):
        ok = self.auto_values(datadir="${datarootdir}", datarootdir="${prefix}/share")
        self.assertAccepted(fill(SQLITE_TEMPLATE, ok))
        self.assertRefused(fill(SQLITE_TEMPLATE, dict(ok, includedir="/usr/include")), "includedir")
        self.assertRefused(fill(SQLITE_TEMPLATE, dict(ok, libdir="/usr/lib")), "libdir")

    def test_mpdecimal(self):
        ok = self.auto_values()
        self.assertAccepted(fill(MPDECIMAL_TEMPLATE, ok))
        self.assertRefused(fill(MPDECIMAL_TEMPLATE, dict(ok, libdir="/usr/lib")), "libdir")
        self.assertRefused(fill(MPDECIMAL_TEMPLATE, dict(ok, docdir="/usr/share/doc/mpdecimal")), "docdir")

    def test_cpython_actual_install_vars(self):
        ok = self.auto_values()
        self.assertAccepted(fill(CPYTHON_TEMPLATE, ok))
        checked = self.ctx.rec["install_inspections"][-1]["checked"]
        self.assertEqual(checked["LIBDEST"], os.path.join(self.P, "lib", "python3.14"))
        for name, value in (("bindir", "/usr/local/bin"), ("libdir", "/usr/lib"),
                            ("includedir", "/usr/include"),
                            ("BINLIBDEST", "/Library/Frameworks/Python/lib/python3.14")):
            with self.subTest(name=name):
                self.assertRefused(fill(CPYTHON_TEMPLATE, dict(ok, **{name: value})))

    def test_missing_prefix_refused(self):
        self.assertRefused("libdir = %s/lib\n" % self.P, "no resolved prefix")


class TkMeta(Fixture):
    """H3: libtcl9tk9*.dylib for Tcl-9-built Tk, with every other check kept."""

    def values(self, **over):
        P = self.P
        v = dict(TK_VERSION="9.0", TK_PATCH_LEVEL=".5", TK_PREFIX=P, TK_EXEC_PREFIX=P,
                 TK_INCLUDE_SPEC=f"-I{P}/include", TK_LIB_SPEC=f"-L{P}/lib -ltcl9tk9.0",
                 TK_STUB_LIB_SPEC=f"-L{P}/lib -ltkstub9.0", TK_XINCLUDES="", TK_SRC_DIR=f"{P}/src")
        v.update(over)
        return v

    def run_tk_meta(self, lib_files, **over):
        for name in os.listdir(os.path.join(self.P, "lib")):
            os.remove(os.path.join(self.P, "lib", name))
        for name in lib_files:
            open(os.path.join(self.P, "lib", name), "w").close()
        self.ctx.tcltk = dict(tcl=dict(TCL_INCLUDE_SPEC=f"-I{self.P}/include",
                                       TCL_LIB_SPEC=f"-L{self.P}/lib -ltcl9.0"))
        with mock.patch.object(bl, "read_sh_config", return_value=self.values(**over)):
            bl.tk_meta(self.ctx)

    def test_tcl9_tk_library_name_accepted(self):
        self.run_tk_meta(["libtcl9tk9.0.dylib"])
        self.assertIn("-ltcl9tk9.0", self.ctx.tcltk["libs"])
        self.assertEqual(self.ctx.rec["tk_config"]["violations"], [])

    def test_missing_library_refused(self):
        with self.assertRaises(bl.BuildFail) as cm:
            self.run_tk_meta([])
        self.assertIn("libtcl9tk9", str(cm.exception))

    def test_wrong_library_names_refused(self):
        for names in (["libtk9.0.dylib"], ["libtcl9.0.dylib"], ["libtcl9tk9.0.a"], ["libtcl9tk8.6.dylib"]):
            with self.subTest(names=names):
                with self.assertRaises(bl.BuildFail):
                    self.run_tk_meta(names)

    def test_lib_spec_outside_prefix_refused(self):
        with self.assertRaises(bl.BuildFail) as cm:
            self.run_tk_meta(["libtcl9tk9.0.dylib"], TK_LIB_SPEC="-L/synthetic/outside/lib -ltcl9tk9.0")
        self.assertIn("TK_LIB_SPEC", str(cm.exception))

    def test_other_checks_kept(self):
        for over in (dict(TK_PREFIX="/synthetic/outside"), dict(TK_INCLUDE_SPEC="-I/synthetic/outside/include"),
                     dict(TK_XINCLUDES="-I/opt/X11/include")):
            with self.subTest(over=over):
                with self.assertRaises(bl.BuildFail):
                    self.run_tk_meta(["libtcl9tk9.0.dylib"], **over)


class ZstdDryrun(Fixture):
    """H4: known conditional make line only."""

    KNOWN = "[ -e libzstd.a ] || /usr/bin/make libzstd.a-release"

    def run_log(self, *lines):
        path = os.path.join(self.root, "dryrun.log")
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        bl.zstd_dryrun(self.ctx, path)

    def install_lines(self):
        P = self.P
        return [f"install -d -m 755 {P}/lib/", f"install -m 644 libzstd.a {P}/lib",
                f"install -m 644 zstd.h {P}/include"]

    def test_known_conditional_make_accepted(self):
        self.run_log("make: Entering directory 'synthetic'", self.KNOWN, *self.install_lines())
        self.assertEqual(self.ctx.rec["install_inspections"][-1]["violations"], [])

    def test_outside_install_target_refused(self):
        with self.assertRaises(bl.BuildFail):
            self.run_log(self.KNOWN, "install -m 644 libzstd.a /usr/lib/libzstd.a")
        with self.assertRaises(bl.BuildFail):
            self.run_log(self.KNOWN, f"install -m 644 libzstd.a {self.P}/lib", "cp libzstd.a /usr/local/lib/")

    def test_make_exemption_is_exact(self):
        for line in ("[ -e libzstd.a ] || /usr/bin/make install DESTDIR=/synthetic/stage",
                     "[ -e libzstd.a ] || /opt/synthetic/bin/make libzstd.a-release",
                     "[ -e libzstd.a ] || /usr/bin/make libzstd.a-release; install x /usr/lib/x",
                     "[ -e libzstd.a ] && /usr/bin/make libzstd.a-release",
                     "[ -e libzstd.a ] || /usr/bin/make libzstd.a-release /usr/lib/extra",
                     "install -m 644 /usr/bin/make " + self.P + "/lib"):
            with self.subTest(line=line):
                with self.assertRaises(bl.BuildFail):
                    self.run_log(line, *self.install_lines())

    def test_no_install_commands_refused(self):
        with self.assertRaises(bl.BuildFail):
            self.run_log("make: nothing to do")


class PlanAndEnvironment(Fixture):
    """M1 and L2: no rpath, isolated CPython and ensurepip, 28 steps, PYTHON* variables blocked."""

    def test_plan_has_28_steps(self):
        plan = bl.build_plan(self.ctx)
        self.assertEqual(len(plan), 28)
        self.assertTrue(plan[-1]["id"].startswith("28-cpython-ensurepip"))

    def test_cpython_make_steps_use_literal_python_for_build(self):
        literal = "PYTHON_FOR_BUILD=./$(BUILDPYTHON) -E -s"
        self.assertEqual(bl.PYTHON_FOR_BUILD_ARG, literal)
        for s in bl.build_plan(self.ctx):
            argv = bl.argv_of(s)
            if s["comp"] == "cpython" and s["label"] in ("build", "install"):
                self.assertIn(literal, argv, s["id"])
                self.assertEqual(argv[0], "/usr/bin/make")
            else:
                self.assertFalse(any(a.startswith("PYTHON_FOR_BUILD") for a in argv), s["id"])

    def test_zstd_install_steps_pin_make_token(self):
        pin = "MAKE=/usr/bin/make"
        for s in bl.build_plan(self.ctx):
            argv = bl.argv_of(s)
            if s["comp"] == "zstd" and s["label"] in ("install-dryrun", "install"):
                self.assertEqual(argv.count(pin), 1, s["id"])
            else:
                self.assertNotIn(pin, argv, s["id"])

    def test_tcl_core_targets_only_on_tcl_steps(self):
        plan = {s["id"]: s for s in bl.build_plan(self.ctx)}
        tcl = [s for s in plan.values() if s["comp"] == "tcl"]
        self.assertEqual([s["label"] for s in tcl], ["configure", "build", "install"])
        self.assertEqual(bl.argv_of(tcl[1]), ["/usr/bin/make", "-j4", "binaries", "libraries", "doc"])
        self.assertEqual(bl.argv_of(tcl[2]), ["/usr/bin/make", "install", "INSTALL_PACKAGE_TARGETS="])
        self.assertEqual((tcl[0]["hook"], tcl[1]["hook"], tcl[2]["hook"]),
                         ("inspect_makefile", None, "tcl_meta"))
        for s in plan.values():
            argv = bl.argv_of(s)
            if s["comp"] != "tcl":
                self.assertFalse(set(argv) & {"binaries", "libraries", "doc", "INSTALL_PACKAGE_TARGETS="}, s["id"])

    def test_tcl_tk_configure_disable_zipfs_and_preflight(self):
        flag = "--disable-zipfs"
        for s in bl.build_plan(self.ctx):
            argv = bl.argv_of(s)
            if s["comp"] in ("tcl", "tk") and s["label"] == "configure":
                self.assertEqual(argv.count(flag), 1, s["id"])
            else:
                self.assertNotIn(flag, argv, s["id"])
        for comp in ("tcl", "tk"):
            d = os.path.join(self.root, "srcs", comp, bl.BY_NAME[comp]["root"], "unix")
            os.makedirs(d)
            with open(os.path.join(d, "configure"), "w") as f:
                f.write("  --enable-zipfs\n")
        res = bl.preflight(self.ctx, lambda comp: os.path.join(self.root, "srcs", comp,
                                                               bl.BY_NAME[comp]["root"]))
        for comp in ("tcl", "tk"):
            checks = {r["check"]: r["ok"] for r in res if r["component"] == comp}
            self.assertTrue(checks[f"configure flag {flag}"], comp)

    def test_no_rpath_anywhere(self):
        for kind in ("base", "sqlite", "cpython", "ensurepip"):
            env = self.ctx.env_for(kind)
            self.assertFalse(any("-rpath" in v for v in env.values()), kind)
        for s in bl.build_plan(self.ctx):
            self.assertFalse(any("-rpath" in a for a in bl.argv_of(s)), s["id"])

    def test_cpython_configure_and_flag_preflight(self):
        conf = [s for s in bl.build_plan(self.ctx) if s["id"].endswith("cpython-configure")][0]
        argv = bl.argv_of(conf)
        self.assertIn("--with-ensurepip=no", argv)
        self.assertNotIn("--with-ensurepip=install", argv)
        self.assertFalse(any(a.startswith("--with-openssl-rpath") for a in argv))
        # Flag preflight against a synthetic configure text.
        d = os.path.join(self.root, "srcs", "cpython", "Python-3.14.10")
        os.makedirs(d)
        with open(os.path.join(d, "configure"), "w") as f:
            f.write("  --with-ensurepip[=install|upgrade|no]\n  --with-openssl-rpath=[auto|no|DIR]\n"
                    "  --enable-shared\n  --without-readline\n  --disable-test-modules\n"
                    "  --with-system-libmpdec\n  --with-openssl=DIR\n")
        res = bl.preflight(self.ctx, lambda comp: os.path.join(self.root, "srcs", comp,
                                                               bl.BY_NAME[comp]["root"]))
        py = {r["check"]: r["ok"] for r in res if r["component"] == "cpython"}
        self.assertTrue(py["configure flag --with-ensurepip=no"])
        self.assertFalse(any("openssl-rpath" in k for k in py))
        self.assertNotIn("configure flag --with-ensurepip=install", py)

    def test_ensurepip_step_is_isolated_and_offline(self):
        step = bl.build_plan(self.ctx)[-1]
        self.assertEqual(bl.argv_of(step), [os.path.join(self.P, "bin", "python3.14"), "-I", "-m",
                                            "ensurepip", "--upgrade"])
        self.assertEqual(step["env"], "ensurepip")
        env = self.ctx.env_for("ensurepip")
        self.assertEqual(env["PYTHONNOUSERSITE"], "1")
        self.assertEqual(env["PIP_NO_INDEX"], "1")
        self.assertEqual(env["PIP_CONFIG_FILE"], os.devnull)
        self.assertNotIn("HOME", env)
        self.assertEqual(sorted(k for k in env if k.startswith("PYTHON")), ["PYTHONNOUSERSITE"])
        bl.check_argv_safe(bl.argv_of(step))
        bl.check_env_safe(env)

    def test_every_planned_step_passes_the_guards(self):
        for s in bl.build_plan(self.ctx):
            bl.check_argv_safe(bl.argv_of(s))
            bl.check_env_safe(self.ctx.env_for(s["env"]))
            self.assertEqual(self.ctx.env_for(s["env"])["PYTHONNOUSERSITE"], "1", s["id"])

    def test_only_exact_nousersite_python_variable_allowed(self):
        base = self.ctx.env("base")
        bl.check_env_safe(base)
        for k, v in (("PYTHONPATH", "/synthetic"), ("PYTHONHOME", "/synthetic"), ("PYTHONUSERBASE", "/synthetic"),
                     ("PYTHONSTARTUP", "/synthetic/x.py"), ("PYTHONNOUSERSITE", "0"),
                     ("PYTHONNOUSERSITE", ""), ("PYTHONNOUSERSITE", "11"), ("PYTHONDONTWRITEBYTECODE", "1"),
                     ("DYLD_LIBRARY_PATH", "/synthetic"), ("GIT_DIR", "/synthetic")):
            with self.subTest(k=k, v=v):
                with self.assertRaises(bl.BuildFail):
                    bl.check_env_safe(dict(base, **{k: v}))

    def test_probe_checks_pip_and_user_site(self):
        self.assertIn('"pip"', bl.PROBE)
        self.assertIn("sys.flags.no_user_site", bl.PROBE)
        # The eleven original import checks are unchanged.
        m = re.search(r"for n in \(([^)]*)\):", bl.PROBE)
        names = re.findall(r'"([^"]+)"', m.group(1))
        self.assertEqual(names, ["ssl", "sqlite3", "ctypes", "lzma", "bz2", "zlib", "decimal",
                                 "compression.zstd", "tkinter", "venv", "ensurepip"])


if __name__ == "__main__":
    unittest.main()
