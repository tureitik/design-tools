#!/usr/bin/env python3
"""Regression tests for mermaid2cpp.py (run: python3 test_mermaid2cpp.py -v).

Every test generates C++ from a small diagram. Where it matters the output is also compiled
with `g++ -std=c++17 -Wall -Wextra -Werror -fsyntax-only` (skipped if g++ is not installed).
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mermaid2cpp as m  # noqa: E402

HAVE_GXX = shutil.which("g++") is not None

# --------------------------------------------------------------------------- fixtures

SPEC = """
classDiagram
  class Leash {
    +isTight() bool
    +pull(force: int)
  }
  class Owner {
    +wag(n: int)
  }
  class Dog {
    -leash: Leash
    -owner: Owner
    +walk(times: int) bool
  }

sequenceDiagram
  participant w as Walker
  participant d as Dog
  participant leash as Leash
  participant owner as Owner
  w->>d: walk(times: int) bool
  d->>leash: isTight() bool
  leash-->>d: tight
  alt tight
    d->>owner: wag(times)
  else
    d->>leash: pull(times)
  end
  opt times > 3
    d->>owner: wag(1)
  end
  alt is angry
    d->>owner: wag(0)
  end
"""

# Dog has NO leash / cursor member: the lifelines must become injected members
NO_MEMBER = """
classDiagram
  class Leash {
    +pull(force: int)
  }
  class Cursor {
    +hasNext() bool
    +next() int
  }
  class Dog {
    +walk(times: int)
  }

sequenceDiagram
  participant w as Walker
  participant d as Dog
  participant leash as Leash
  participant cursor as Cursor
  w->>d: walk(times: int)
  d->>leash: pull(times)
  loop more
    d->>cursor: hasNext() bool
    cursor-->>d: more
    d->>cursor: next()
  end
"""

PTR_PARAM = """
classDiagram
  class Leash {
    +pull(force: int)
  }
  class Dog {
    +walk(l: Leash*, times: int)
  }

sequenceDiagram
  participant w as Walker
  participant d as Dog
  participant l as Leash
  w->>d: walk(l: Leash*, times: int)
  d->>l: pull(times)
"""


def reply_diagram(callee_decl: str, owner_extra: str = "", reply: str = "ok",
                  call: str = "pull(times)", reply_to: str = "d") -> str:
    return f"""
classDiagram
  class Leash {{
{callee_decl}
  }}
  class Dog {{
    -leash: Leash
    +walk(times: int)
{owner_extra}
  }}

sequenceDiagram
  participant w as Walker
  participant d as Dog
  participant leash as Leash
  w->>d: walk(times: int)
  d->>leash: {call}
  leash-->>{reply_to}: {reply}
"""


# --------------------------------------------------------------------------- helpers

def build(text, testable=False, external=(), dashed_calls=False, **kw):
    warns: list[str] = []
    classes = m.build_model(text, list(external), warns.append, dashed_calls)
    files = m.generate_files(classes, {}, external=list(external), dashed_calls=dashed_calls,
                             warn=warns.append, testable=testable, **kw)
    return classes, files, warns


def compile_files(files, extra_flags=()):
    """Return (ok, compiler output). Every .cpp is syntax-checked with warnings as errors."""
    if not HAVE_GXX:
        return True, "g++ missing"
    with tempfile.TemporaryDirectory() as d:
        for fn, txt in files.items():
            with open(os.path.join(d, fn), "w", encoding="utf-8") as f:
                f.write(txt)
        out = []
        ok = True
        for fn in files:
            if fn.endswith(".cpp"):
                r = subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                    "-fsyntax-only", f"-I{d}", *extra_flags, os.path.join(d, fn)],
                                   capture_output=True, text=True)
                if r.returncode:
                    ok = False
                    out.append(r.stderr)
        return ok, "\n".join(out)


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def code(text: str) -> str:
    """Generated text without user-code slot markers (easier to assert on)."""
    return "\n".join(ln for ln in text.splitlines() if "USER CODE" not in ln)


def method(classes, cls, name):
    return next(x for x in classes[cls].methods if x.name == name)


class Base(unittest.TestCase):
    def assertCompiles(self, files, msg=""):
        ok, out = compile_files(files)
        self.assertTrue(ok, f"{msg}\n{out}")


# --------------------------------------------------------------------------- main example

class SpecExample(Base):
    def test_testable_output(self):
        _, f, w = build(SPEC, testable=True)
        hpp, cpp = f["Dog.hpp"], code(f["Dog.cpp"])
        self.assertIn("friend struct Dog_TestAccess;", hpp)
        self.assertIn("Dog(std::shared_ptr<Leash> leash, std::shared_ptr<Owner> owner);", hpp)
        self.assertIn("std::shared_ptr<Leash> leash;", hpp)
        self.assertIn("std::shared_ptr<Owner> owner;", hpp)
        self.assertIn("bool tight{};", cpp)
        self.assertIn("tight = leash->isTight();", cpp)
        for frag in ("if (tight) {", "} else {", "if (times > 3) {", "bool alt2_guard0 = false;"):
            self.assertIn(frag, cpp)
        self.assertTrue(any("is angry" in x and "cannot be selected" in x for x in w))
        for cls in ("Leash", "Owner"):
            h = f[f"{cls}.hpp"]
            self.assertIn(f"virtual ~{cls}() = default;", h)
        self.assertIn("virtual bool isTight();", f["Leash.hpp"])
        self.assertIn("virtual void wag(int n);", f["Owner.hpp"])

    def test_testable_has_no_default_ctor_and_no_spurious_warning(self):
        _, f, w = build(SPEC, testable=True)
        self.assertNotRegex(f["Dog.hpp"], r"\bDog\(\);")
        self.assertNotIn("Dog::Dog()", f["Dog.cpp"])
        self.assertFalse([x for x in w if "already declared" in x or "is declared in the class" in x], w)

    def test_testable_compiles(self):
        self.assertCompiles(build(SPEC, testable=True)[1])

    def test_plain_output(self):
        _, f, _ = build(SPEC)
        cpp = code(f["Dog.cpp"])
        self.assertIn("tight = leash.isTight();", cpp)
        self.assertIn("bool tight{};", cpp)
        self.assertIn("if (times > 3) {", cpp)
        hpp = f["Dog.hpp"]
        self.assertNotIn("friend", hpp)
        self.assertNotIn("shared_ptr", hpp)
        self.assertIn("Dog() = default;", hpp)
        self.assertNotIn("virtual", f["Leash.hpp"])

    def test_plain_compiles(self):
        self.assertCompiles(build(SPEC)[1])

    def test_single_file_compiles(self):
        _, f, _ = build(SPEC, testable=True, single="All")
        self.assertEqual(sorted(f), ["All.cpp", "All.hpp"])
        self.assertCompiles(f)

    def test_generation_is_idempotent_on_same_model(self):
        classes = m.build_model(SPEC, [], lambda s: None)
        a = m.generate_files(classes, {}, testable=True, warn=lambda s: None)
        b = m.generate_files(classes, {}, testable=True, warn=lambda s: None)
        self.assertEqual(a, b)
        self.assertEqual([x.params for x in classes["Dog"].methods if x.name == "Dog"], [])
        self.assertFalse(any(x.synth for x in classes["Dog"].methods))


# --------------------------------------------------------------------------- control points

class ControlPoints(Base):
    def setUp(self):
        self.classes, _, _ = build(SPEC, testable=True)
        self.meth = method(self.classes, "Dog", "walk")

    def test_controls(self):
        got = [(c.block_id, c.branch, c.kind, c.drivable) for c in self.meth.controls]
        self.assertEqual(got, [("alt1", 0, "binding", True), ("alt1", 1, "else", True),
                               ("opt1", 0, "expr", True), ("alt2", 0, "free", False)])

    def test_binding_control_details(self):
        c = self.meth.controls[0]
        self.assertEqual((c.var, c.type, c.source), ("tight", "bool", ("leash", "isTight", [])))

    def test_expr_control(self):
        c = self.meth.controls[2]
        self.assertEqual((c.expr, c.vars), ("times > 3", []))

    def test_method_bindings(self):
        self.assertEqual(self.meth.bindings, {"tight": ("bool", ("leash", "isTight", []))})

    def test_branch_calls(self):
        blk = next(i for i in self.meth.seq.root.items if isinstance(i, m.Block))
        got = [[(r.instance, r.method, r.args, r.binding) for r in m.branch_calls(self.meth.seq, br)]
               for br in blk.branches]
        self.assertEqual(got, [[("owner", "wag", ["times"], None)],
                               [("leash", "pull", ["times"], None)]])

    def test_expr_guard_records_bound_variables(self):
        txt = SPEC.replace("alt tight", "alt !tight")
        classes, f, _ = build(txt, testable=True)
        c = method(classes, "Dog", "walk").controls[0]
        self.assertEqual((c.kind, c.expr, c.vars), ("expr", "!tight", ["tight"]))
        self.assertIn("if (!tight) {", f["Dog.cpp"])

    def test_else_with_guard_is_expression(self):
        txt = SPEC.replace("  else\n    d->>leash: pull(times)", "  else times > 9\n    d->>leash: pull(times)")
        classes, f, _ = build(txt, testable=True)
        cps = method(classes, "Dog", "walk").controls
        self.assertEqual((cps[1].kind, cps[1].expr), ("expr", "times > 9"))
        self.assertIn("} else if (times > 9) {", f["Dog.cpp"])


# --------------------------------------------------------------------------- bug 1: lifeline members

class LifelineMembers(Base):
    def test_missing_member_is_declared_injected_and_compiles(self):
        _, f, w = build(NO_MEMBER, testable=True)
        hpp = f["Dog.hpp"]
        self.assertIn("std::shared_ptr<Leash> leash;", hpp)
        self.assertIn("std::shared_ptr<Cursor> cursor;", hpp)
        self.assertIn("Dog(std::shared_ptr<Leash> leash, std::shared_ptr<Cursor> cursor);", hpp)
        self.assertIn("class Leash;", hpp)          # forward declaration only
        self.assertIn('#include "Leash.hpp"', f["Dog.cpp"])
        self.assertIn(": leash(leash), cursor(cursor)", f["Dog.cpp"])
        self.assertCompiles(f)

    def test_added_member_warning_printed_once_per_lifeline(self):
        _, _, w = build(NO_MEMBER, testable=True)
        added = [x for x in w if "added a new member" in x]
        self.assertEqual(len(added), 2, w)

    def test_plain_mode_still_uses_default_constructed_locals(self):
        _, f, w = build(NO_MEMBER)
        self.assertIn("Leash leash{};", f["Dog.cpp"])
        self.assertTrue([x for x in w if "default-constructed local" in x])

    def test_lifeline_matched_by_type_to_differently_named_field(self):
        txt = SPEC.replace("-leash: Leash", "-theLeash: Leash")
        _, f, w = build(txt, testable=True)
        cpp = code(f["Dog.cpp"])
        self.assertIn("theLeash->isTight()", cpp)
        self.assertNotIn("std::shared_ptr<Leash> leash;", f["Dog.hpp"])
        self.assertFalse([x for x in w if "added a new member" in x], w)
        self.assertCompiles(f)

    def test_static_method_keeps_local_fallback(self):
        txt = """
classDiagram
  class Leash {
    +pull(force: int)
  }
  class Dog {
    +walk(times: int)$
  }

sequenceDiagram
  participant w as Walker
  participant d as Dog
  participant leash as Leash
  w->>d: walk(times: int)
  d->>leash: pull(times)
"""
        _, f, _ = build(txt, testable=True)
        self.assertIn("Leash leash{};", f["Dog.cpp"])
        self.assertNotIn("shared_ptr<Leash>", f["Dog.hpp"])
        self.assertCompiles(f)

    def test_same_instance_with_two_types_is_an_error(self):
        txt = NO_MEMBER.replace("class Dog {", "class Dog {\n    +other(x: int)\n  }\n  class Zed {\n    +n() bool") + """
sequenceDiagram
  participant w as Walker
  participant d as Dog
  participant leash as Zed
  w->>d: other(x: int)
  d->>leash: n()
"""
        with self.assertRaises(m.DiagramError):
            build(txt, testable=True)


# --------------------------------------------------------------------------- bug 2: while(true)

class WhileLoops(Base):
    def test_break_directly_after_binding(self):
        _, f, _ = build(NO_MEMBER, testable=True)
        lines = [ln.strip() for ln in code(f["Dog.cpp"]).splitlines()]
        i = lines.index("while (true) {  // more")
        body = lines[i + 1:]
        a = body.index("more = cursor->hasNext();")
        self.assertEqual(body[a + 1], "if (!more) break;")
        self.assertLess(body.index("if (!more) break;"), body.index("cursor->next();"))
        self.assertEqual(sum(1 for ln in lines if ln == "if (!more) break;"), 1)

    def test_while_loop_plain_mode_compiles_and_has_args_in_control(self):
        classes, f, _ = build(NO_MEMBER)
        self.assertIn("more = cursor.hasNext();", f["Dog.cpp"])
        self.assertCompiles(f)
        cp = [c for c in method(classes, "Dog", "walk").controls if c.block_id == "loop1"][0]
        self.assertEqual(cp.source, ("cursor", "hasNext", []))

    def test_bound_variable_not_bound_first_uses_plain_while(self):
        txt = NO_MEMBER.replace("    d->>cursor: hasNext() bool\n    cursor-->>d: more\n    d->>cursor: next()",
                                "    d->>cursor: next()\n    d->>cursor: hasNext() bool\n    cursor-->>d: more")
        _, f, _ = build(txt, testable=True)
        cpp = code(f["Dog.cpp"])
        self.assertIn("while (more) {", cpp)
        self.assertNotIn("break;", cpp)

    def test_nested_for_each_loops_each_get_an_advance_slot(self):
        txt = """
classDiagram
  class Leash {
    +pull(force: int)
  }
  class Dog {
    -leash: Leash
    +walk(times: int)
  }

sequenceDiagram
  participant w as Walker
  participant d as Dog
  participant leash as Leash
  w->>d: walk(times: int)
  loop for each t in T (t from 1 to T-1)
    loop for each u in U (u from 1 to U-1)
      d->>leash: pull(times)
    end
  end
"""
        _, f, _ = build(txt)
        self.assertIn("/advance t]", f["Dog.cpp"])
        self.assertIn("/advance u]", f["Dog.cpp"])


# --------------------------------------------------------------------------- bug 3: pointer params

class PointerParameters(Base):
    def test_pointer_parameter_uses_arrow_plain(self):
        _, f, _ = build(PTR_PARAM)
        self.assertIn("l->pull(times);", f["Dog.cpp"])
        self.assertCompiles(f)

    def test_pointer_parameter_uses_arrow_testable(self):
        _, f, _ = build(PTR_PARAM, testable=True)
        self.assertIn("l->pull(times);", f["Dog.cpp"])
        self.assertNotIn("shared_ptr<Leash>", f["Dog.hpp"])      # a parameter is not a member
        self.assertCompiles(f)

    def test_reference_parameter_uses_dot(self):
        _, f, _ = build(PTR_PARAM.replace("Leash*", "Leash"))
        self.assertIn("l.pull(times);", f["Dog.cpp"])
        self.assertIn("Leash& l", f["Dog.cpp"])
        self.assertCompiles(f)

    def test_shared_ptr_parameter_uses_arrow(self):
        _, f, _ = build(PTR_PARAM.replace("Leash*", "std::shared_ptr<Leash>"))
        self.assertIn("l->pull(times);", f["Dog.cpp"])
        self.assertCompiles(f)


# --------------------------------------------------------------------------- bug 4: bindings

class Bindings(Base):
    def test_type_comes_from_callee_class_not_owner(self):
        # Dog has its own pull() returning int; Leash::pull is void -> must NOT bind
        txt = reply_diagram("    +pull(force: int)", owner_extra="    +pull(x: int) int")
        _, f, _ = build(txt)
        cpp = code(f["Dog.cpp"])
        self.assertNotIn("int ok", cpp)
        self.assertNotIn("ok =", cpp)
        self.assertCompiles(f)

    def test_reply_after_void_callee_stays_a_comment(self):
        _, f, w = build(reply_diagram("    +pull(force: int)"))
        cpp = code(f["Dog.cpp"])
        self.assertIn("// leash --> d: ok   (return, not a call)", cpp)
        self.assertNotIn("ok{}", cpp)
        self.assertFalse(w, w)
        self.assertCompiles(f)

    def test_declared_nonvoid_callee_binds_with_declared_type(self):
        _, f, _ = build(reply_diagram("    +pull(force: int) long"))
        cpp = code(f["Dog.cpp"])
        self.assertIn("long ok{};", cpp)
        self.assertIn("ok = leash.pull(times);", cpp)
        self.assertCompiles(f)

    def test_unread_binding_is_maybe_unused(self):
        _, f, _ = build(reply_diagram("    +pull(force: int) long"))
        self.assertIn("[[maybe_unused]] long ok{};", f["Dog.cpp"])
        _, f2, _ = build(reply_diagram("    +pull(force: int) long"), maybe_unused=False)
        self.assertNotIn("[[maybe_unused]]", f2["Dog.cpp"])

    def test_trailing_return_type_wins(self):
        _, f, _ = build(reply_diagram("    +pull(force: int)", call="pull(times) bool"))
        self.assertIn("bool ok{};", f["Dog.cpp"])

    def test_name_type_form_on_reply(self):
        _, f, _ = build(reply_diagram("    +pull(force: int)", reply="ok: double"))
        self.assertIn("double ok{};", f["Dog.cpp"])
        self.assertIn("ok = leash.pull(times);", f["Dog.cpp"])

    def test_unknown_callee_reply_warns_and_stays_comment(self):
        txt = reply_diagram("    +other()", call="mystery(times)")
        _, f, w = build(txt)
        self.assertTrue([x for x in w if "was not bound" in x], w)
        self.assertNotIn("ok =", f["Dog.cpp"])

    def test_explicit_void_binding_is_an_error(self):
        with self.assertRaises(m.DiagramError):
            build(reply_diagram("    +pull(force: int)", call="pull(times) void"))

    def test_reply_to_non_owner_does_not_bind(self):
        _, f, _ = build(reply_diagram("    +pull(force: int) bool", reply="tight", reply_to="w"))
        self.assertNotIn("tight", code(f["Dog.cpp"]).replace("// leash --> w: tight", ""))

    def test_dashed_calls_disables_bindings(self):
        classes, f, w = build(reply_diagram("    +pull(force: int)"), dashed_calls=True)
        self.assertEqual(method(classes, "Dog", "walk").seq.call_bindings, {})
        self.assertNotIn("ok{}", f["Dog.cpp"])

    def test_one_reply_binds_only_once(self):
        txt = reply_diagram("    +pull(force: int) bool") + "  leash-->>d: second\n"
        classes, f, _ = build(txt)
        cpp = code(f["Dog.cpp"])
        self.assertIn("ok = leash.pull(times);", cpp)
        self.assertNotIn("second", cpp.replace("// leash --> d: second", ""))

    def test_binding_name_colliding_with_parameter_is_an_error(self):
        with self.assertRaises(m.DiagramError):
            build(reply_diagram("    +pull(force: int) bool", reply="times"))

    def test_rebinding_with_different_type_is_an_error(self):
        txt = reply_diagram("    +pull(force: int) bool\n    +push(force: int) long") + \
            "  d->>leash: push(times)\n  leash-->>d: ok\n"
        with self.assertRaises(m.DiagramError):
            build(txt)

    def test_rebinding_with_same_type_is_allowed(self):
        txt = reply_diagram("    +pull(force: int) bool") + "  d->>leash: pull(1)\n  leash-->>d: ok\n"
        _, f, _ = build(txt)
        cpp = code(f["Dog.cpp"])
        self.assertEqual(cpp.count("bool ok{};"), 1)
        self.assertIn("ok = leash.pull(1);", cpp)


# --------------------------------------------------------------------------- guards

class Guards(Base):
    allowed = {"times", "tight"}

    def test_classify_guard(self):
        cg = lambda t: m.classify_guard(t, self.allowed)
        self.assertEqual(cg("times > 3"), "times > 3")
        self.assertEqual(cg("tight && times"), "tight && times")
        self.assertIsNone(cg("is angry"))
        self.assertIsNone(cg("5"))
        self.assertIsNone(cg(""))

    def test_dotted_guard_checks_first_component(self):
        cg = lambda t: m.classify_guard(t, self.allowed)
        self.assertIsNone(cg("other.times > 3"))
        self.assertIsNone(cg("stranger.tight"))
        self.assertEqual(cg("times.size"), "times.size")

    def test_free_guard_is_hoisted_and_not_drivable(self):
        classes, f, w = build(SPEC)
        cp = method(classes, "Dog", "walk").controls[-1]
        self.assertEqual((cp.kind, cp.drivable), ("free", False))
        self.assertIn("bool alt2_guard0 = false;", f["Dog.cpp"])
        self.assertEqual(len([x for x in w if "is angry" in x]), 1)

    def test_member_in_guard_allowed_only_for_instance_methods(self):
        txt = """
classDiagram
  class Leash {
    +pull(force: int)
  }
  class Dog {
    -count: int
    +walk(times: int)
  }

sequenceDiagram
  participant w as Walker
  participant d as Dog
  participant leash as Leash
  w->>d: walk(times: int)
  opt count > 2
    d->>leash: pull(times)
  end
"""
        _, f, _ = build(txt)
        self.assertIn("if (count > 2) {", f["Dog.cpp"])
        _, f2, w2 = build(txt.replace("+walk(times: int)", "+walk(times: int)$"))
        self.assertNotIn("if (count > 2) {", f2["Dog.cpp"])
        self.assertTrue([x for x in w2 if "count > 2" in x])


# --------------------------------------------------------------------------- constructors

class Constructors(Base):
    CTOR = SPEC.replace("+walk(times: int) bool", "+Dog()\n    +walk(times: int) bool")

    def test_declared_ctor_gets_injected_params_and_warning(self):
        _, f, w = build(self.CTOR, testable=True)
        self.assertIn("Dog(std::shared_ptr<Leash> leash, std::shared_ptr<Owner> owner);", f["Dog.hpp"])
        self.assertEqual(f["Dog.hpp"].count("Dog("), 1)
        self.assertEqual(len([x for x in w if "is declared in the class diagram" in x]), 1, w)
        self.assertCompiles(f)

    def test_declared_ctor_with_own_params_keeps_them_first(self):
        txt = SPEC.replace("+walk(times: int) bool", "+Dog(name: String)\n    +walk(times: int) bool")
        _, f, _ = build(txt, testable=True)
        self.assertIn("Dog(const std::string& name, std::shared_ptr<Leash> leash, "
                      "std::shared_ptr<Owner> owner);", f["Dog.hpp"])
        self.assertCompiles(f)

    def test_initialiser_order_follows_declaration_order(self):
        # public field declared AFTER a private one: init order must follow the emitted
        # (public-first) declaration order or -Wreorder (an error with -Werror) fires
        txt = """
classDiagram
  class Leash {
    +pull(force: int)
  }
  class Owner {
    +wag(n: int)
  }
  class Dog {
    -a: Leash
    +b: Owner
    +walk(times: int)
  }

sequenceDiagram
  participant w as Walker
  participant d as Dog
  participant a as Leash
  participant b as Owner
  w->>d: walk(times: int)
  d->>a: pull(times)
  d->>b: wag(times)
"""
        _, f, _ = build(txt, testable=True)
        self.assertIn("Dog(std::shared_ptr<Owner> b, std::shared_ptr<Leash> a);", f["Dog.hpp"])
        self.assertIn(": b(b), a(a)", f["Dog.cpp"])
        self.assertCompiles(f)

    def test_class_without_sequence_is_not_injected(self):
        txt = SPEC + """
classDiagram
  class Kennel {
    -leash: Leash
  }
"""
        _, f, _ = build(txt, testable=True)
        self.assertNotIn("shared_ptr", f["Kennel.hpp"])
        self.assertNotIn("friend", f["Kennel.hpp"])


# --------------------------------------------------------------------------- parameter passing (gap 3)

class ParameterPassing(Base):
    def test_pass_param_rules(self):
        classes, _, _ = build(SPEC)
        g = m.Gen(classes, m.Slots({}), [], False, True, lambda s: None)
        self.assertEqual(g.pass_param("int"), "int")
        self.assertEqual(g.pass_param("Leash*"), "Leash*")
        self.assertEqual(g.pass_param("std::shared_ptr<Leash>"), "std::shared_ptr<Leash>")
        self.assertEqual(g.pass_param("Leash"), "Leash&")                # has non-static methods
        self.assertEqual(g.pass_param("std::string"), "const std::string&")
        self.assertEqual(g.pass_param("std::vector<int>"), "const std::vector<int>&")
        self.assertEqual(g.pass_param("void"), "void*")


# --------------------------------------------------------------------------- other modes

class OutputModes(Base):
    def test_data_split_without_data_classes_has_no_dangling_include(self):
        _, f, _ = build(SPEC, testable=True, data_split=True)
        self.assertNotIn("Data.hpp", f)
        for txt in f.values():
            self.assertNotIn('#include "Data.hpp"', txt)
        self.assertCompiles(f)

    def test_data_split_with_data_class_still_includes_it(self):
        txt = SPEC + """
classDiagram
  class Point {
    +x: int
    +y: int
  }
"""
        _, f, _ = build(txt, data_split=True)
        self.assertIn("Data.hpp", f)
        self.assertIn('#include "Data.hpp"', f["Dog.hpp"])
        self.assertCompiles(f)

    def test_external_collaborator_warns_in_testable_mode(self):
        _, f, w = build(SPEC, testable=True, external=["Leash"])
        self.assertTrue([x for x in w if "'Leash' is external" in x], w)
        self.assertNotIn("Leash.hpp", f)


# --------------------------------------------------------------------------- slots & CLI

class SlotsAndCli(Base):
    def test_user_code_survives_regeneration_in_testable_mode(self):
        _, f, _ = build(SPEC, testable=True)
        edited = f["Dog.cpp"].replace(
            "// BEGIN USER CODE [Dog::walk(int)/begin]\n    // END USER CODE [Dog::walk(int)/begin]",
            "// BEGIN USER CODE [Dog::walk(int)/begin]\n    int mine = 42;\n    // END USER CODE [Dog::walk(int)/begin]")
        self.assertIn("int mine = 42;", edited)
        classes = m.build_model(SPEC, [], lambda s: None)
        again = m.generate_files(classes, m.harvest(edited), testable=True, warn=lambda s: None)
        self.assertIn("int mine = 42;", again["Dog.cpp"])

    def test_constructor_slot_id_and_existing_slot_ids_unchanged(self):
        _, f, _ = build(SPEC, testable=True)
        for sid in ("Dog::walk(int)/begin", "Dog::walk(int)/after d>leash.isTight()",
                    "Dog::walk(int)/enter alt(tight)", "Dog::walk(int)/after alt(is angry)",
                    "Dog::walk(int)/end", "Dog.cpp:includes",
                    "Dog::Dog(std::shared_ptr<Leash>,std::shared_ptr<Owner>)"):
            self.assertIn(f"// BEGIN USER CODE [{sid}]", f["Dog.cpp"], sid)

    def _cli(self, *args):
        return subprocess.run([sys.executable, os.path.join(HERE, "mermaid2cpp.py"), *args],
                              capture_output=True, text=True)

    def test_cli_writes_files_and_compiles(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "x.mmd")
            write(src, SPEC)
            r = self._cli(src, "--testable", "-o", os.path.join(d, "out"))
            self.assertEqual(r.returncode, 0, r.stderr)
            names = sorted(os.listdir(os.path.join(d, "out")))
            self.assertEqual(names, ["Dog.cpp", "Dog.hpp", "Leash.cpp", "Leash.hpp",
                                     "Owner.cpp", "Owner.hpp"])
            if HAVE_GXX:
                r = subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-fsyntax-only",
                                    f"-I{d}/out", f"{d}/out/Dog.cpp"], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_cli_split_dirs_and_second_run_is_stable(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "x.mmd")
            write(src, SPEC)
            args = [src, "--testable", "--hpp-dir", f"{d}/inc", "--cpp-dir", f"{d}/src"]
            self.assertEqual(self._cli(*args).returncode, 0)
            first = read(f"{d}/src/Dog.cpp")
            self.assertEqual(self._cli(*args).returncode, 0)
            self.assertEqual(first, read(f"{d}/src/Dog.cpp"))

    def test_cli_error_exit_code(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "bad.mmd")
            write(src, SPEC.replace("alt tight", "alt tight\n  end\n  end\n  alt x"))
            r = self._cli(src)
            self.assertEqual(r.returncode, 1)
            self.assertIn("error:", r.stderr)


if __name__ == "__main__":
    unittest.main()
