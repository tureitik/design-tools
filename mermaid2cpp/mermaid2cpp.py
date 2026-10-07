#!/usr/bin/env python3
"""
mermaid2cpp - generate C++ (.hpp + .cpp) from Mermaid class diagrams and sequence diagrams.

CLASS DIAGRAMS
  Inheritance   Base <|-- Derived     Derived --|> Base     (also <|.. and ..|>)
  Composition   Whole *-- Part        Part --* Whole        (a "many" cardinality such as
                "0..*", "*", "n" or "many" gives std::vector<Part>; a ": name" label names the field)
  Members       class Dog { +bark(times: int) bool  -int age }      Dog : +name: String
                suffix $ = static, * = abstract;  <<interface>> / <<abstract>> stereotypes
  Types         T[] -> std::vector<T>, List~T~, Map~K,V~, Set~T~, String/string -> std::string
  Every class in a class diagram is generated (use --external to skip types that already exist).

SEQUENCE DIAGRAMS = CLASS METHOD BODIES
  A sequence diagram implements ONE method. Its first message is the entry point:
        Walker->>Dog: walk(times: int) bool        implements Dog::walk(int) -> bool
  The receiver (Dog) is the owner: the body is generated into Dog::walk in Dog.cpp. If the class
  diagram already declares walk, that declaration is used (arguments are matched by count);
  otherwise the method is added to the class from the entry message (arguments must be typed).
  Only messages SENT BY THE OWNER become code. A message between two other lifelines happens
  inside their own methods; it is kept as a comment and a warning is printed.

  Lifelines are object instances. Declare the type with
      participant d as Dog            (instance "d", type Dog)
      participant d as d:Dog          (same)
      participant D as diariser (Diariser)   (instance "diariser", type Diariser; the id D is
                                      only the short name used in messages)
      participant Dog                 (type Dog, instance "dog")
  A collaborator lifeline is resolved, in order, to: a method parameter or a member of the owner
  with the same name as the instance; the single member/parameter of the lifeline's type; or,
  failing both, a default-constructed local (with a warning) that you set up in a slot
  (with --testable, a new std::shared_ptr<T> member is added instead - see below).
  Self-messages call the method directly.
  Messages      A->>B: name(args)     args are expressions (42, x + 1) or typed declarations
                                      (items: Line[]) which become locals at the top of the method
                A-->>B: text          dashed arrows are returns and become comments
                                      (use --dashed-calls to treat them as calls)
  Bindings      A dashed return arrow from a collaborator BACK TO THE OWNER whose text is a
                single identifier (optionally 'name: Type') BINDS the result of the owner's
                most recent unbound call to that collaborator:
                'Leash-->>Dog: tight'  ->  'tight = leash.isTight();'
                with 'bool tight{};' hoisted to the top of the method. The type comes
                from the call's trailing return type, then 'name: Type', then the return
                type declared for the method in the CALLEE's class. An explicit void
                binding is an error. A reply that cannot bind (the callee is declared void,
                or no type can be found - the latter with a warning) and every other dashed
                arrow stays a comment. --dashed-calls disables bindings.
  Blocks        alt / else / else ... / end      -> if / else if / else
                opt ... end                      -> if
                loop N times ... end             -> for;   loop <text> ... end -> while (guard)
  Guards        A guard is classified at emission time:
                - a guard that is a C++ expression over parameters, bound variables
                  and (non-static) members is emitted verbatim:  'alt tight' -> 'if (tight)',
                  'opt times > 3' -> 'if (times > 3)' (for 'a.b' the first part must be known);
                - anything else (e.g. "is angry") keeps the old behaviour:
                  'bool alt1_guard0 = false;' at the top of the method, and a warning
                  that the path cannot be selected by a test. Assign it in the user
                  slot just before the block.
                - a 'while'-style loop whose guard is a bound variable bound by the
                  FIRST message in the loop body is emitted as
                      while (true) { more = cursor.hasNext(); if (!more) break; ... }
                  (the break comes right after the binding call).
  Control points  Every alt/opt/loop branch is recorded on the method as
                  Method.controls (a list of ControlPoint: block id, branch, kind
                  binding/expr/else/free/count, condition text, bound variable + type,
                  the (collaborator, method, args) source of the binding, and for 'expr'
                  the bound variables it references). Method.bindings maps every bound
                  variable to (type, source). The ordered
                  owner-sent calls of any branch are available as branch_calls(seq, br)
                  - the input a later test generator turns into EXPECT_CALLs.

TESTABLE MODE (--testable)
  - every non-static method of a collaborator class (any class that receives a call
    from the owner in a sequence) is virtual, and such classes get 'virtual ~Class()';
    external collaborators cannot be changed, so one warning is printed per class
    saying its methods must be virtual;
  - every collaborator-typed by-value field / non-many composition part of a
    sequence-owning class becomes std::shared_ptr<T>; a lifeline that would otherwise
    fall back to a default-constructed local instead becomes a new shared_ptr member
    named after the instance (with a warning);
  - such a class gets a constructor taking one std::shared_ptr<T> per injected member
    (in declaration order, initialised in the member-initialiser list); constructors
    declared in the diagram get the parameters appended (with a warning); no default
    constructor is generated for a class with injected members. Only classes that own a
    sequence method are changed, and generation never modifies the parsed model;
  - every sequence-owning class gets 'friend struct <Class>_TestAccess;' as the first
    line of its class body (the struct itself is defined by the test code);
  - pointer-like types (T*, std::shared_ptr<T>, std::unique_ptr<T>) are accessed
    with ->, always (not only in --testable mode) - members and parameters alike.

PARAMETER PASSING
  Generated methods are never const, so parameters are passed: primitives and
  pointer/reference types by value; std::shared_ptr/std::unique_ptr by value (sink
  parameters); diagram classes with at least one non-static method by T& (so methods
  can be called on the parameter); data classes and every other type (std::string,
  std::vector<...>, ...) by const T&.

OUTPUT
  -o DIR           one Class.hpp + Class.cpp per class (declarations in .hpp, all method
                   definitions in .cpp; dependencies become #includes or forward declarations)
  -o DIR --single NAME     NAME.hpp + NAME.cpp holding every class
  --hpp-dir DIR --cpp-dir DIR   put headers and sources in different directories (each one
                   overrides -o for its file type; give both, or -o for the other one).
                   --include-prefix P makes the .cpp files write #include "P<Class>.hpp".
  (no -o)          print all files to stdout (no merging)

WARNINGS
  Parameters of method definitions are marked [[maybe_unused]] (C++17) so -Wextra stays quiet;
  --no-maybe-unused turns this off.

USER CODE SLOTS
  // BEGIN USER CODE [id]   ...   // END USER CODE [id]
  Slots sit in every method body (the whole body, or - for sequence methods - at the start, after
  every call, on entry to every block branch and after every block) and in the include list of
  every file. When an output file already exists, the code inside each slot is carried over by id.
  Slots whose id disappeared from the diagrams are kept at the end of the class's .cpp as
  commented-out ORPHAN blocks (and restored automatically if the slot comes back).
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field


class DiagramError(Exception):
    pass


# --------------------------------------------------------------------------- helpers

CPP_KEYWORDS = {"new", "delete", "class", "default", "this", "template", "operator",
                "register", "union", "friend", "namespace", "switch", "case", "do", "if"}

PRIM_RE = re.compile(r"^(u?int\d+_t|size_t|bool|char|short|int|long|float|double|"
                     r"unsigned( int| long)?|void)$")


def decap(s: str) -> str:
    return s[:1].lower() + s[1:]


def comment_text(s: str) -> str:
    """Make text safe for a // comment (a trailing backslash would splice the next line)."""
    return s.replace("\n", " ").rstrip("\\ ").strip()


def split_top(s: str) -> list[str]:
    """Split on commas that are not nested in brackets, generics, ~generics~ or quotes."""
    parts, cur, depth, tilde, quote = [], [], 0, False, None
    for ch in s:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "([{<":
            depth += 1
        elif ch in ")]}>":
            depth -= 1
        elif ch == "~":
            tilde = not tilde
        elif ch == "," and depth <= 0 and not tilde:
            parts.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    parts.append("".join(cur))
    return parts


def cpp_type(t: str) -> str:
    t = t.strip()
    # ~...~ -> <...>: a trailing ~~ is >>, a trailing lone ~ is >, every other ~ is <
    if t.endswith("~~"):
        t = t[:-2].replace("~", "<") + ">>"
    elif t.endswith("~"):
        t = t[:-1].replace("~", "<") + ">"
    else:
        t = t.replace("~", "<")
    prev = None
    while prev != t:                       # T[] -> std::vector<T>
        prev = t
        t = re.sub(r"([\w:]+(?:<[^<>]*>)?)\[\]", r"std::vector<\1>", t)
    t = re.sub(r"(?<![\w:])(?:String|string)\b", "std::string", t)
    t = re.sub(r"(?<![\w:])List(?=<)", "std::vector", t)
    t = re.sub(r"(?<![\w:])Map(?=<)", "std::map", t)
    t = re.sub(r"(?<![\w:])Set(?=<)", "std::set", t)
    return re.sub(r",\s*", ", ", t)


def is_pointer_like(t: str) -> bool:
    """True for T*, std::shared_ptr<...> and std::unique_ptr<...> (accessed with ->)."""
    t = t.strip()
    return t.endswith("*") or bool(re.fullmatch(r"std::(?:shared|unique)_ptr<.+>", t))


def is_shared_ptr(t: str) -> bool:
    return bool(re.fullmatch(r"std::shared_ptr<.+>", t.strip()))


def bare_type(t: str) -> str:
    """Type with const, &, * and smart-pointer wrappers removed (for matching lifelines)."""
    t = re.sub(r"^const\s+", "", t.strip())
    t = re.sub(r"[&*\s]+$", "", t)
    m = re.fullmatch(r"std::(?:shared|unique)_ptr<(.+)>", t)
    return m[1].strip() if m else t


# --------------------------------------------------------------------------- slots

SLOT_RE = re.compile(
    r"^([ \t]*)// BEGIN USER CODE \[([^\]\n]+)\][ \t]*\r?\n(.*?)^[ \t]*// END USER CODE \[\2\][ \t]*$",
    re.M | re.S)
ORPHAN_RE = re.compile(
    r"^// ORPHAN BEGIN \[([^\]\n]+)\][ \t]*\r?\n(.*?)^// ORPHAN END \[\1\][ \t]*$", re.M | re.S)


def harvest(text: str) -> dict[str, str]:
    """Collect user code from an existing generated file, keyed by slot id."""
    found: dict[str, str] = {}
    for m in SLOT_RE.finditer(text):
        indent, sid, body = m.group(1), m.group(2), m.group(3)
        lines = [ln[len(indent):] if ln.startswith(indent) else ln.lstrip()
                 for ln in body.splitlines()]
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        found[sid] = "\n".join(lines)
    for m in ORPHAN_RE.finditer(text):          # earlier orphans: kept unless the slot is live
        lines = []
        for ln in m.group(2).splitlines():
            ln = ln[4:] if ln.startswith("// |") else ln
            lines.append(ln[1:] if ln.startswith(" ") else ln)
        found.setdefault(m.group(1), "\n".join(lines))
    return found


class Slots:
    def __init__(self, preserved: dict[str, str]):
        self.preserved = preserved
        self.used: set[str] = set()

    def emit(self, out: list[str], indent: int, sid: str, default: str = "") -> None:
        sid = sid.replace("[", "{").replace("]", "}")
        sid = re.sub(r"[\r\n]", " ", sid).strip()
        base, n = sid, 1
        while sid in self.used:                 # identical calls get #2, #3, ...
            n += 1
            sid = f"{base}#{n}"
        self.used.add(sid)
        pad = " " * indent
        body = self.preserved.get(sid)
        if body is None or not body.strip():
            body = default
        out.append(f"{pad}// BEGIN USER CODE [{sid}]")
        for ln in body.splitlines():
            out.append(f"{pad}{ln}" if ln.strip() else "")
        out.append(f"{pad}// END USER CODE [{sid}]")

    def orphans(self) -> list[tuple[str, str]]:
        return [(k, v) for k, v in self.preserved.items()
                if k not in self.used and v.strip()]


# --------------------------------------------------------------------------- class model

VIS = {"+": "public", "-": "private", "#": "protected", "~": "public"}


@dataclass
class Param:
    name: str
    type: str


@dataclass
class Method:
    name: str
    params: list[Param]
    ret: str
    vis: str
    static: bool = False
    abstract: bool = False
    seq: "SeqDiagram | None" = None
    controls: list["ControlPoint"] = field(default_factory=list)
    synth: bool = False      # constructor synthesised for --testable injection (not in the diagram)
    bindings: dict = field(default_factory=dict)   # name -> (type, (instance, method, [args])); set by SeqEmitter.run


@dataclass
class ControlPoint:
    """One selectable branch of an alt/opt/loop block, for the test generator.

    kind: "binding" (guard is a variable bound to a call result), "expr" (guard is a
    C++ expression over parameters/bound variables/members, emitted verbatim; `vars` lists
    the bound variables it references, see Method.bindings), "else" (an unguarded else
    branch: taken when the earlier branches of the block are not), "free" (natural-language
    guard, emitted as a user-assigned bool and not test-drivable), "count" (loop N times /
    for each ...).
    """
    block_id: str            # "alt1", "opt2", "loop1"
    branch: int              # branch index within the block
    kind: str
    expr: str | None = None  # condition text ("expr"), e.g. "times > 3"
    var: str | None = None   # bound variable name ("binding")
    type: str | None = None  # C++ type of the bound variable ("binding")
    source: tuple | None = None  # (collaborator_instance, method, [arg exprs]) of the call
    drivable: bool = True    # False for "free"
    vars: list = field(default_factory=list)   # bound variables referenced by an "expr" condition


@dataclass
class CallRec:
    """One owner-sent call, in order, as recorded while emitting a branch path."""
    instance: str
    method: str
    args: list[str]
    binding: str | None = None


@dataclass
class Field:
    name: str
    type: str
    vis: str
    static: bool = False


@dataclass
class Part:
    type: str
    name: str
    many: bool


@dataclass
class ClassInfo:
    name: str
    bases: list[str] = field(default_factory=list)
    fields: list[Field] = field(default_factory=list)
    methods: list[Method] = field(default_factory=list)
    parts: list[Part] = field(default_factory=list)
    stereotype: str | None = None


REL_RE = re.compile(
    r'^(\w+)\s*(?:"([^"]*)"\s*)?(<\|--|<\|\.\.|--\|>|\.\.\|>|\*--|--\*)\s*(?:"([^"]*)"\s*)?'
    r"(\w+)\s*(?::\s*(.*))?$")
METHOD_RE = re.compile(
    r"^(?:(?P<pre>[\w:<>~\[\], ]+?)\s+)?(?P<name>[A-Za-z_]\w*)\s*\((?P<args>.*)\)\s*(?P<m1>[*$]*)\s*"
    r"(?::?\s*(?P<ret>[\w:<>~\[\], ]+?))?\s*(?P<m2>[*$]*)$")
ATTR_NAMED_RE = re.compile(r"^(?P<name>[A-Za-z_]\w*)\s*:(?!:)\s*(?P<type>.+?)\s*(?P<m>\$?)$")
ATTR_TYPED_RE = re.compile(r"^(?P<type>.+?)\s+(?P<name>[A-Za-z_]\w*)\s*(?P<m>\$?)$")
TYPED_ARG_RE = re.compile(r"^([A-Za-z_]\w*)\s*:(?!:)\s*(.+)$")


def parse_params(s: str) -> list[Param]:
    out = []
    for i, p in enumerate(split_top(s)):
        p = p.strip()
        if not p:
            continue
        if m := TYPED_ARG_RE.match(p):
            out.append(Param(m[1], m[2].strip()))
        elif m := re.match(r"^(.+?)\s+([A-Za-z_]\w*)$", p):
            out.append(Param(m[2], m[1].strip()))
        else:
            out.append(Param(f"arg{i}", p))
    return out


def parse_member(c: ClassInfo, text: str, where: str) -> None:
    text = text.strip().rstrip(";")
    vis = "public"
    if text and text[0] in VIS:
        vis, text = VIS[text[0]], text[1:].strip()
    if "(" in text:
        m = METHOD_RE.match(text)
        if not m:
            raise DiagramError(f"{where}: cannot parse method '{text}'")
        mods = (m["m1"] or "") + (m["m2"] or "")
        ret = (m["ret"] or m["pre"] or "void").strip()
        c.methods.append(Method(m["name"], parse_params(m["args"]), ret, vis,
                                static="$" in mods, abstract="*" in mods))
        return
    m = ATTR_NAMED_RE.match(text) or ATTR_TYPED_RE.match(text)
    if not m:
        raise DiagramError(f"{where}: cannot parse attribute '{text}' "
                           "(write 'name: Type' or 'Type name')")
    c.fields.append(Field(m["name"], m["type"].strip(), vis, static=m["m"] == "$"))


def parse_class_diagram(lines, classes: dict[str, ClassInfo], warn) -> None:
    def get(n: str) -> ClassInfo:
        return classes.setdefault(n, ClassInfo(n))

    current: ClassInfo | None = None
    for no, raw in lines:
        line = raw.strip()
        where = f"line {no}"
        if not line or line.startswith("%%"):
            continue
        if current is not None:
            if line == "}":
                current = None
            elif m := re.match(r"^<<\s*(\w+)\s*>>$", line):
                current.stereotype = m[1].lower()
            else:
                parse_member(current, line, where)
            continue
        if m := re.match(r"^class\s+(\w+)\s*(\{)?\s*$", line):
            c = get(m[1])
            current = c if m[2] else None
        elif m := re.match(r"^<<\s*(\w+)\s*>>\s*(\w+)$", line):
            get(m[2]).stereotype = m[1].lower()
        elif m := REL_RE.match(line):
            left, lcard, arrow, rcard, right, label = m.groups()
            if arrow in ("<|--", "<|.."):
                get(right).bases.append(left)
                get(left)
            elif arrow in ("--|>", "..|>"):
                get(left).bases.append(right)
                get(right)
            else:
                whole, part, card = (left, right, rcard) if arrow == "*--" else (right, left, lcard)
                many = (card or "").strip().lower().endswith(("*", "n", "many"))
                label = (label or "").strip()
                name = label if re.fullmatch(r"[A-Za-z_]\w*", label) else \
                    decap(part) + ("s" if many else "")
                get(whole).parts.append(Part(part, name, many))
                get(part)
        elif m := re.match(r"^(\w+)\s*:\s*(.+)$", line):
            parse_member(get(m[1]), m[2], where)
        else:
            warn(f"{where}: class diagram line not understood, skipped: {line}")


# --------------------------------------------------------------------------- sequence model

@dataclass
class Lifeline:
    id: str
    alias: str | None = None
    type: str = ""
    instance: str = ""


@dataclass(eq=False)
class Call:
    sender: str
    receiver: str
    text: str
    dashed: bool
    line: int


@dataclass
class Branch:
    guard: str | None
    items: list = field(default_factory=list)


@dataclass
class Block:
    kind: str
    branches: list[Branch]
    line: int


@dataclass
class SeqDiagram:
    name: str | None
    lifelines: dict[str, Lifeline]
    root: Branch
    owner: str = ""            # lifeline id of the object whose method this sequence implements
    entry: "Call | None" = None
    # filled by attach_sequences (GAP 1a): Call -> bound variable name / its C++ type
    call_bindings: dict["Call", str] | None = None
    call_bind_types: dict["Call", str] | None = None


def branch_calls(seq: "SeqDiagram", branch: Branch) -> list[CallRec]:
    """The owner-sent calls of one branch, in order - what the test generator turns
    into EXPECT_CALLs. Dashed return arrows (without a binding) and messages sent by
    other lifelines are skipped: they are comments in the generated code."""
    out: list[CallRec] = []
    bindings = seq.call_bindings or {}

    def walk(items) -> None:
        for it in items:
            if isinstance(it, Call):
                if it.sender != seq.owner:
                    continue
                m = CALL_RE.match(it.text)
                if not m:
                    continue
                if it.dashed and not bindings.get(it):
                    continue
                exprs = [a.strip() for a in split_top(m[2] or "") if a.strip()]
                out.append(CallRec(it.receiver, m[1], exprs, bindings.get(it)))
            else:
                for br in it.branches:
                    walk(br.items)

    walk(branch.items)
    return out


PART_RE = re.compile(r"^(?:participant|actor)\s+(\w+)(?:\s+as\s+(.+))?$")
MSG_RE = re.compile(r"^(\w+)\s*(-{1,2})(>>|>|x|\))\s*[+-]?\s*(\w+)\s*:\s*(.*)$")
# A call may carry a trailing return type: "check(x) bool", "check(x): bool", "check(x) -> bool".
# The return type is documentation only (the callee's own declaration decides the C++ type).
CALL_RE = re.compile(
    r"^(\w+)\s*(?:\((.*)\))?\s*(?::?\s*(?:->\s*)?(?P<ret>[\w:<>~\[\], ]+?))?\s*$")


def parse_sequence(lines, warn) -> SeqDiagram:
    lifelines: dict[str, Lifeline] = {}
    root = Branch(None)
    stack: list[tuple[Block | None, Branch]] = [(None, root)]
    name = None

    def ll(i: str) -> Lifeline:
        return lifelines.setdefault(i, Lifeline(i))

    for no, raw in lines:
        line = raw.strip()
        if not line or line.startswith("%%"):
            continue
        if m := PART_RE.match(line):
            ll(m[1]).alias = m[2].strip() if m[2] else None
        elif m := MSG_RE.match(line):
            ll(m[1]); ll(m[4])
            stack[-1][1].items.append(Call(m[1], m[4], m[5].strip(), m[2] == "--", no))
        elif m := re.match(r"^(alt|opt|loop)\b\s*(.*)$", line):
            blk = Block(m[1], [Branch(m[2].strip())], no)
            stack[-1][1].items.append(blk)
            stack.append((blk, blk.branches[0]))
        elif m := re.match(r"^else\b\s*(.*)$", line):
            blk = stack[-1][0]
            if blk is None or blk.kind != "alt":
                raise DiagramError(f"line {no}: 'else' outside an alt block")
            br = Branch(m[1].strip())
            blk.branches.append(br)
            stack[-1] = (blk, br)
        elif line == "end":
            if len(stack) == 1:
                raise DiagramError(f"line {no}: 'end' without an open block")
            stack.pop()
        elif m := re.match(r"^(par|and|critical|option|break|rect|box)\b", line):
            raise DiagramError(f"line {no}: '{m[1]}' blocks are not supported "
                               "(supported: alt/else, opt, loop)")
        elif m := re.match(r"^title\b[: ]*(.*)$", line, re.I):
            name = m[1].strip() or name
        elif re.match(r"^(activate|deactivate|autonumber|note|link|links|create|destroy)\b",
                      line, re.I):
            pass
        else:
            warn(f"line {no}: sequence diagram line not understood, skipped: {line}")
    if len(stack) > 1:
        raise DiagramError(f"line {stack[-1][0].line}: block was never closed with 'end'")
    return SeqDiagram(name, lifelines, root)


TYPE_RE = r"[A-Za-z_][\w:]*(?:<[^<>]*>)?"
NAME_RE = r"[A-Za-z_]\w*"


def parse_alias(alias: str, known: set[str]) -> tuple[str | None, str | None]:
    """Read the text after 'participant X as'. Returns (instance, type); either may be None.

    Accepted: 'diariser (Diariser)', 'Diariser (diariser)', 'd:Diariser', 'Diariser'.
    With parentheses, the part that names a known class is the type; otherwise the
    parenthesised part is the type.
    """
    t = alias.strip().strip("\"'").strip()
    if m := re.fullmatch(rf"({TYPE_RE})\s*\(\s*({TYPE_RE})\s*\)", t):
        first, inner = m[1], m[2]
        if first in known and inner not in known and re.fullmatch(NAME_RE, inner):
            return inner, first
        return (first if re.fullmatch(NAME_RE, first) else None), inner
    parts = re.split(r"(?<!:):(?!:)", t)
    cand = parts[-1].strip()
    if re.fullmatch(TYPE_RE, cand) and (len(parts) > 1 or cand[:1].isupper() or cand in known):
        inst = parts[0].strip() if len(parts) > 1 else None
        return (inst if inst and re.fullmatch(NAME_RE, inst) else None), cand
    return None, None


def resolve_lifelines(seq: SeqDiagram, known: set[str] = frozenset(), warn=lambda m: None) -> None:
    used = set()
    for l in seq.lifelines.values():
        inst, typ = parse_alias(l.alias, set(known)) if l.alias else (None, None)
        if typ is None:
            if l.alias:
                warn(f"lifeline '{l.id}': cannot read the type from 'as {l.alias}'; "
                     f"use e.g. 'as {decap(l.id)} (TypeName)'")
            if l.id[:1].isupper():
                typ = l.id
            else:
                raise DiagramError(
                    f"cannot infer the type of lifeline '{l.id}': write "
                    f"'participant {l.id} as <TypeName>' or 'as {l.id} (TypeName)'")
        if inst is None or inst == typ:
            inst = l.id if not l.id[:1].isupper() else decap(l.id)
            if inst == typ:
                inst = decap(typ)
        if inst in CPP_KEYWORDS:
            inst += "_"
        if inst in used:
            raise DiagramError(f"two lifelines map to the same instance name '{inst}'")
        used.add(inst)
        l.type, l.instance = typ, inst


# --------------------------------------------------------------------------- linking

ENTRY_RE = re.compile(
    r"^(?P<name>\w+)\s*(?:\((?P<args>.*)\))?\s*(?::?\s*(?:->\s*)?(?P<ret>[\w:<>~\[\], ]+))?$")


def finalize_parts(classes: dict[str, ClassInfo]) -> None:
    """Give composition fields unique names within their class."""
    for c in classes.values():
        used = {f.name for f in c.fields}
        for p in c.parts:
            n, i = p.name, 2
            while n in used:
                n, i = f"{p.name}{i}", i + 1
            p.name = n
            used.add(n)


GUARD_CHAR_RE = re.compile(r"[\w.\s,!?\-+*/%<>=!&|^~()\[\]\"':.]+")


def classify_guard(text: str, allowed: set[str]) -> str | None:
    """A guard that is a pure C++ expression (every identifier is a parameter, bound
    variable, member, literal or true/false/nullptr) -> its text, else None. For a dotted
    name ('a.b') the FIRST component must be allowed."""
    text = text.strip()
    if not text or not GUARD_CHAR_RE.fullmatch(text):
        return None
    idents = set(re.findall(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", text))
    if not idents:
        return None                     # a bare number is not a usable condition
    for i in idents:
        if i in ("true", "false", "nullptr") or i.split(".")[0] in allowed:
            continue
        return None
    return text


def attach_sequences(classes, seqs, external: set[str], warn=lambda m: None,
                     dashed_calls: bool = False) -> None:
    """Bind every sequence diagram to the method it implements, and (GAP 1a) record
    which dashed return arrows bind a call result to a local variable."""
    for s in seqs:
        label = f"sequence '{s.name}'" if s.name else "sequence diagram"
        resolve_lifelines(s, set(classes), warn)
        first = s.root.items[0] if s.root.items else None
        if not isinstance(first, Call) or first.dashed:
            raise DiagramError(
                f"{label}: it must start with the message that calls the method being "
                "implemented, e.g. 'Caller->>Owner: name(arg: Type) ReturnType'")
        s.owner, s.entry = first.receiver, first
        owner = s.lifelines[first.receiver]
        c = classes.get(owner.type)
        if c is None or c.name in external:
            raise DiagramError(
                f"{label}: lifeline '{owner.id}' has type {owner.type}, which is not a class "
                "generated from the class diagrams - a sequence implements a method of a "
                "generated class")
        em = ENTRY_RE.match(first.text)
        if not em:
            raise DiagramError(f"line {first.line}: cannot read the entry message '{first.text}'")
        args = []
        for a in split_top(em["args"] or ""):
            a = a.strip()
            if not a:
                continue
            t = TYPED_ARG_RE.match(a)
            if not t:
                raise DiagramError(
                    f"line {first.line}: argument '{a}' of the entry message must be typed "
                    "(name: Type) because it declares the method's parameters")
            args.append(Param(t[1], t[2].strip()))
        cands = [m for m in c.methods if m.name == em["name"]]
        if em["args"] is not None:
            cands = [m for m in cands if len(m.params) == len(args)]
            if len(cands) > 1:   # same arity: narrow by parameter TYPE (overload resolution)
                atypes = [cpp_type(a.type) for a in args]
                typed = [m for m in cands if [cpp_type(p.type) for p in m.params] == atypes]
                if len(typed) == 1:
                    cands = typed
        if len(cands) > 1:
            raise DiagramError(f"{label}: {c.name}::{em['name']} is ambiguous "
                               "(several overloads with that argument count)")
        if cands:
            m = cands[0]
        else:
            m = Method(em["name"], args, (em["ret"] or "void").strip(), "public")
            c.methods.append(m)
        if m.abstract or c.stereotype == "interface":
            raise DiagramError(f"{label}: {c.name}::{m.name} is abstract/interface and "
                               "cannot have a sequence body")
        # static methods may have a sequence body, but their diagram may only reference
        # parameters and locals (no instance members); resolve() enforces that.
        if m.seq is not None:
            raise DiagramError(f"{c.name}::{m.name} is implemented by two sequence diagrams")
        m.seq = s
        if dashed_calls:                 # dashed arrows are calls: nothing is a return binding
            s.call_bindings, s.call_bind_types = {}, {}
        else:
            s.call_bindings, s.call_bind_types = _find_bindings(s, classes, warn)


BIND_RE = re.compile(r"([A-Za-z_]\w*)(?:\s*:\s*(.+))?")


def _callee_return(s: SeqDiagram, classes, call: Call) -> str | None:
    """Declared return type of the method a call invokes, looked up in the CALLEE's class
    (None if the class or method is unknown)."""
    cm = CALL_RE.match(call.text)
    cc = classes.get(s.lifelines[call.receiver].type)
    if not cm or cc is None:
        return None
    nargs = len([a for a in split_top(cm[2] or "") if a.strip()])
    named = [mm for mm in cc.methods if mm.name == cm[1]]
    ms = [mm for mm in named if len(mm.params) == nargs] or named
    return cpp_type(ms[0].ret) if ms else None


def _find_bindings(s: SeqDiagram, classes, warn) -> tuple[dict, dict]:
    """GAP 1a: a dashed message from a collaborator back to the OWNER whose text is a single
    identifier (optionally 'name: Type') binds the result of the owner's most recent unbound
    call to that collaborator. Type: the call's trailing return type, then 'name: Type', then
    the callee's declared return type. A reply that cannot bind (the callee is declared void,
    or no type can be found) stays a comment, so existing diagrams keep working; only an
    EXPLICIT void binding is an error. Returns ({call: name}, {call: C++ type})."""
    bindings: dict[Call, str] = {}
    types: dict[Call, str] = {}
    pending: dict[str, Call] = {}

    def walk(items) -> None:
        for it in items:
            if not isinstance(it, Call):
                for br in it.branches:
                    walk(br.items)
                continue
            if it.dashed:
                m = BIND_RE.fullmatch(it.text.strip())
                if not (m and it.receiver == s.owner and it.sender in pending):
                    continue
                call = pending.pop(it.sender)
                explicit = m[2].strip() if m[2] else None
                cm = CALL_RE.match(call.text)
                if explicit is None and cm and cm["ret"]:
                    explicit = cm["ret"].strip()
                if explicit is not None:
                    ty = cpp_type(explicit)
                    if ty == "void":
                        raise DiagramError(f"line {it.line}: '{m[1]}' binds a void result - give the "
                                           "call a non-void trailing return type or the return "
                                           "message a 'name: Type' form")
                else:
                    ty = _callee_return(s, classes, call)
                    if ty is None:
                        warn(f"line {it.line}: reply '{it.text}' was not bound to a variable: the "
                             "type is unknown (declare the method in the class diagram, give the "
                             "call a trailing return type or write 'name: Type'); kept as a comment")
                        continue
                    if ty == "void":
                        continue          # the callee returns nothing: a plain return comment
                bindings[call] = m[1]
                types[call] = ty
            elif it.sender == s.owner and it.receiver != s.owner and CALL_RE.match(it.text):
                pending[it.receiver] = it

    walk(s.root.items)
    return bindings, types


# --------------------------------------------------------------------------- generation

STD_HEADERS = (("map", "std::map"), ("set", "std::set"), ("string", "std::string"),
               ("vector", "std::vector"), ("memory", "std::unique_ptr"),
               ("memory", "std::shared_ptr"), ("memory", "std::make_unique"),
               ("memory", "std::make_shared"), ("cstddef", "size_t"),
               ("cstdint", "uint8_t"), ("cstdint", "int64_t"), ("cstdint", "uint64_t"),
               ("cstdint", "int32_t"), ("cstdint", "uint32_t"))


def std_includes(text: str) -> list[str]:
    seen = []
    for h, pat in STD_HEADERS:
        if pat in text and h not in seen:
            seen.append(h)
    if "nlohmann::json" in text:
        seen.append("nlohmann/json.hpp")
    return [f"#include <{h}>" if h != "nlohmann/json.hpp" else '#include "nlohmann/json.hpp"'
            for h in seen]


def sig_types(m: Method) -> str:
    return ",".join(cpp_type(p.type) for p in m.params)


class Gen:
    def __init__(self, classes, slots: Slots, external, dashed_calls, maybe_unused, warn,
             inline_bodies: bool = False, testable: bool = False):
        self.classes, self.slots, self.warn = classes, slots, warn
        self.dashed_calls = dashed_calls
        self.inline_bodies = inline_bodies
        self.testable = testable
        self.attr = "[[maybe_unused]] " if maybe_unused else ""
        self.external = set(external)
        self.gen = [n for n in classes if n not in self.external]
        self.genset = set(self.gen)
        self.base_names = {b for c in classes.values() for b in c.bases}
        self.cpp_refs: dict[str, set[str]] = {}
        # GAP 2: a collaborator is any (generated or external) class that receives a
        # call from the owner in at least one sequence diagram.
        self.collab_names: set[str] = set()
        self._inj_cache: dict[str, list[tuple[str, str]]] = {}
        self._warned: set[str] = set()
        if testable:
            for c in classes.values():
                for m in c.methods:
                    if m.seq is None:
                        continue
                    self.collab_names |= {cpp_type(m.seq.lifelines[lid].type)
                                          for lid in SeqEmitter.collab_lifelines(m.seq, dashed_calls)}
            for n in self.collab_names:
                if n in self.external or n not in self.classes:
                    self.warn(f"--testable: collaborator class '{n}' is external; its methods "
                              "must be virtual so generated code can call them polymorphically")

    # ---- parameter passing (GAP 3)
    def pass_param(self, t: str) -> str:
        """How a parameter of C++ type t is passed: primitives and pointer/reference
        types by value; smart pointers by value (sink parameters); diagram classes
        that have at least one non-static method by T& (the generated method is not
        const, so a const T& could not be called); data classes and everything else
        (std::string, std::vector<...>, ...) by const T&."""
        t = t.strip()
        if re.fullmatch(r"void", t):
            return "void*"        # bare void is not a usable C++ parameter type
        if PRIM_RE.match(t) or t.endswith(("&", "*")):
            return t
        if is_shared_ptr(t):
            return t
        if t in self.classes:
            c = self.classes[t]
            if any(m.name != c.name and not m.static for m in c.methods):
                return f"{t}&"
            return f"const {t}&"
        return f"const {t}&"

    # ---- type bookkeeping
    def idents(self, t: str) -> set[str]:
        return {w for w in re.findall(r"[A-Za-z_]\w*", cpp_type(t)) if w in self.genset}

    def exact(self, t: str) -> bool:
        return cpp_type(t).strip() in self.genset

    def needs_default(self, t: str) -> bool:
        # True if a by-value member of type t can be default-initialized (i.e. t is a
        # class that has a no-arg ctor, or t is not a generated class).
        nt = cpp_type(t).strip()
        if nt not in self.classes:
            return True
        c = self.classes[nt]
        ctors = [m for m in self.methods_of(c) if m.name == c.name]
        if not ctors:
            return True
        return any(len(self.ctor_params(c, m)) == 0 for m in ctors)

    def refs(self, c: ClassInfo) -> set[str]:
        r = {b for b in c.bases if b in self.genset}
        for f in c.fields:
            r |= self.idents(f.type)
        for p in c.parts:
            r |= {p.type} & self.genset
        for m in c.methods:
            r |= self.idents(m.ret)
            for p in m.params:
                r |= self.idents(p.type)
        r |= {t for _, t in self.injected_members(c)} & self.genset
        r.discard(c.name)
        return r

    def hdr_includes(self, c: ClassInfo) -> list[str]:
        need = [b for b in c.bases if b in self.genset]
        need += [p.type for p in c.parts if not p.many and p.type in self.genset]
        need += [cpp_type(f.type).strip() for f in c.fields if self.exact(f.type)]
        return sorted({n for n in need if n != c.name})

    def incomplete_members(self, c: ClassInfo) -> bool:
        return (any(p.many and p.type in self.genset for p in c.parts)
                or any(self.idents(f.type) and not self.exact(f.type) for f in c.fields))

    def warn_once(self, msg: str) -> None:
        if msg not in self._warned:
            self._warned.add(msg)
            self.warn(msg)

    @staticmethod
    def lifeline_provided(c: ClassInfo, m: Method, ll: Lifeline) -> bool:
        """True if method m of class c already has a parameter / member that resolve() would
        pick for lifeline ll (same name as the instance, or the same type)."""
        cands = [(p.name, cpp_type(p.type)) for p in m.params]
        cands += [(f.name, cpp_type(f.type)) for f in c.fields if not f.static]
        cands += [(p.name, f"std::vector<{p.type}>" if p.many else p.type) for p in c.parts]
        if any(n == ll.instance for n, _ in cands):
            return True
        return any(bare_type(t) == cpp_type(ll.type) for _, t in cands)

    def extra_members(self, c: ClassInfo) -> list[tuple[str, str]]:
        """GAP 2b: (name, type) of collaborator members that nothing in the class provides:
        a lifeline of an instance method that would otherwise fall back to a default-
        constructed local becomes a new std::shared_ptr<T> member named after the instance."""
        out: list[tuple[str, str]] = []
        seen: dict[str, str] = {}
        for m in c.methods:
            if m.seq is None or m.static:
                continue
            for lid in SeqEmitter.collab_lifelines(m.seq, self.dashed_calls):
                ll = m.seq.lifelines[lid]
                t = cpp_type(ll.type)
                if t not in self.collab_names or self.lifeline_provided(c, m, ll):
                    continue
                if seen.get(ll.instance, t) != t:
                    raise DiagramError(f"{c.name}: lifeline instance '{ll.instance}' has type "
                                       f"{seen[ll.instance]} in one method and {t} in another; "
                                       "rename one of the lifelines")
                if ll.instance not in seen:
                    seen[ll.instance] = t
                    out.append((ll.instance, t))
        return out

    def injected_members(self, c: ClassInfo) -> list[tuple[str, str]]:
        """GAP 2b: (name, type) of every collaborator member of a sequence-owning class that
        becomes a std::shared_ptr<T> (testable mode only): collaborator-typed by-value fields,
        non-many composition parts, and members added for lifelines nothing provides. Listed in
        DECLARATION order (public/protected/private fields, then parts, then added members),
        which is also the constructor parameter and member-initialiser order."""
        if not self.testable or not any(m.seq is not None for m in c.methods):
            return []
        if c.name in self._inj_cache:
            return self._inj_cache[c.name]
        found: dict[str, str] = {}
        for f in c.fields:
            t = cpp_type(f.type).strip()
            if not f.static and t in self.collab_names and not is_pointer_like(t):
                found[f.name] = t
        for p in c.parts:
            if not p.many and p.type in self.collab_names and not is_pointer_like(p.type):
                found[p.name] = p.type
        added = self.extra_members(c)
        for n, t in added:
            if n in found and found[n] != t:
                raise DiagramError(f"{c.name}: lifeline instance '{n}' has type {t} but the member "
                                   f"'{n}' has type {found[n]}")
            found.setdefault(n, t)
        order: list[str] = []
        for vis in ("public", "protected", "private"):
            order += [f.name for f in c.fields if not f.static and f.vis == vis and f.name in found]
        order += [p.name for p in c.parts if p.name in found and p.name not in order]
        order += [n for n in found if n not in order]
        self._inj_cache[c.name] = [(n, found[n]) for n in order]
        return self._inj_cache[c.name]

    def methods_of(self, c: ClassInfo) -> list[Method]:
        """The methods to emit: c.methods plus (testable mode) a synthesised injection
        constructor when the class has injected members but declares no constructor. The model
        itself is never modified, so generating twice gives identical output."""
        ms = list(c.methods)
        if self.testable and self.injected_members(c) and not any(m.name == c.name for m in ms):
            ms.insert(0, Method(c.name, [], "void", "public", synth=True))
        return ms

    def ctor_params(self, c: ClassInfo, m: Method) -> list[Param]:
        """Parameters of a method as emitted: constructors get one std::shared_ptr<T> per
        injected member appended (GAP 2c)."""
        if m.name != c.name:
            return list(m.params)
        return list(m.params) + [Param(n, f"std::shared_ptr<{t}>") for n, t in self.injected_members(c)]

    def data_class(self, c: ClassInfo) -> bool:
        # A data class holds fields and has no sequence-diagram method, so its whole
        # definition (fields + accessors) is inlined into the shared Data.hpp header
        # and no .cpp file is emitted for it. RAII/service stereotypes (XgbModel, etc.)
        # are excluded: they own resources and need out-of-line method bodies.
        return (bool(c.fields)
                and c.stereotype not in ("raii", "service", "policy", "orchestrator")
                and not any(m.seq is not None for m in c.methods))

    def is_virtual(self, c: ClassInfo) -> bool:
        return (c.name in self.base_names or c.stereotype in ("interface", "abstract")
                or any(m.abstract for m in c.methods)
                or self.testable and c.name in self.collab_names)

    def ancestor_has(self, c: ClassInfo, method: str) -> bool:
        seen, todo = set(), list(c.bases)
        while todo:
            b = todo.pop()
            if b in seen or b not in self.classes:
                continue
            seen.add(b)
            if any(m.name == method and not m.static for m in self.classes[b].methods):
                return True
            todo.extend(self.classes[b].bases)
        return False

    def class_order(self) -> list[ClassInfo]:
        state: dict[str, int] = {}
        out: list[ClassInfo] = []

        def visit(n: str, path: list[str]) -> None:
            if n not in self.genset or state.get(n) == 2:
                return
            if state.get(n) == 1:
                raise DiagramError("cyclic inheritance/by-value composition: "
                                   + " -> ".join(path + [n]))
            state[n] = 1
            c = self.classes[n]
            for d in self.hdr_includes(c):
                visit(d, path + [n])
            state[n] = 2
            out.append(c)

        for n in self.gen:
            visit(n, [])
        return out

    # ---- header side
    def method_decl(self, c: ClassInfo, m: Method) -> tuple[str, bool]:
        iface = c.stereotype == "interface"
        pure = (m.abstract or iface) and not m.static
        override = not m.static and self.ancestor_has(c, m.name)
        virt = not m.static and not override and (
            pure or c.name in self.base_names or (self.testable and c.name in self.collab_names))
        params = ", ".join(f"{self.pass_param(cpp_type(p.type))} {p.name}" for p in m.params)
        ret = "" if m.name == c.name else cpp_type(m.ret) + " "   # constructors have no return type
        decl = (("static " if m.static else "") + ("virtual " if virt else "")
                + f"{ret}{m.name}({params})"
                + (" override" if override else "") + (" = 0" if pure else "") + ";")
        return decl, pure

    def class_decl(self, c: ClassInfo) -> list[str]:
        head = f"class {c.name}"
        if c.bases:
            head += " : " + ", ".join(f"public {b}" for b in c.bases)
        sec: dict[str, list[str]] = {"public": [], "protected": [], "private": []}
        virt, ool = self.is_virtual(c), self.incomplete_members(c)
        inlined = self.inline_bodies and self.data_class(c)
        injected = self.injected_members(c)
        ms = self.methods_of(c)
        # GAP 2e: tests need to reach private members of sequence-owning classes
        if self.testable and not inlined and any(m.seq is not None for m in c.methods):
            sec["public"].append(f"    friend struct {c.name}_TestAccess;")
        has_ctor = any(m.name == c.name for m in ms)
        if injected:
            # GAP 2c: no default constructor (the injection constructor is declared with the
            # methods below); the destructor is out-of-line only if member types are incomplete
            if ool:
                sec["public"].append(f"    {'virtual ' if virt else ''}~{c.name}();")
            elif virt:
                sec["public"].append(f"    virtual ~{c.name}() = default;")
        elif has_ctor:
            pass                          # ctor is declared (inlined or out-of-line); no default
        elif ool:                         # defaulted in the .cpp, where member types are complete
            sec["public"].append(f"    {c.name}();")
            sec["public"].append(f"    {'virtual ' if virt else ''}~{c.name}();")
        else:
            sec["public"].append(f"    {c.name}() = default;")
            if virt:
                sec["public"].append(f"    virtual ~{c.name}() = default;")
        inj_names = {n for n, _ in injected}
        for f in c.fields:
            if f.static:
                sec[f.vis].append(f"    inline static {cpp_type(f.type)} {f.name}{{}};")
                continue
            if f.name in inj_names:       # GAP 2b: collaborator field -> shared_ptr (forward declaration is enough)
                sec[f.vis].append(f"    std::shared_ptr<{cpp_type(f.type)}> {f.name};")
            else:
                ft = cpp_type(f.type)
                init = "" if (self.exact(ft) and not self.needs_default(ft)) else "{}"
                sec[f.vis].append(f"    {ft} {f.name}{init};")
        for p in c.parts:
            if p.many:
                sec["private"].append(f"    std::vector<{p.type}> {p.name};")
            elif p.name in inj_names:     # GAP 2b: non-many composition part -> shared_ptr
                sec["private"].append(f"    std::shared_ptr<{p.type}> {p.name};")
            elif self.exact(p.type) and not self.needs_default(p.type):
                sec["private"].append(f"    {p.type} {p.name};")
            else:
                sec["private"].append(f"    {p.type} {p.name}{{}};")
        declared = {f.name for f in c.fields} | {p.name for p in c.parts}
        for n, t in injected:             # GAP 2b: members added for lifelines nothing provides
            if n not in declared:
                sec["private"].append(f"    std::shared_ptr<{t}> {n};")
        for m in ms:
            if inlined:
                params = ", ".join(f"{self.pass_param(cpp_type(p.type))} {p.name}" for p in m.params)
                if m.name == c.name:                 # constructor: inline, no return type
                    sec[m.vis].append(f"    {m.name}({params}) {{}};")
                    continue
                if m.abstract or c.stereotype == "interface":
                    sec[m.vis].append("    " + self.method_decl(c, m)[0])
                else:
                    ret = cpp_type(m.ret)
                    body = "" if ret == "void" else "return {};"
                    # data-class accessors are const so they can be called through const& refs
                    cc = "" if m.static else " const"
                    sec[m.vis].append(f"    {ret} {m.name}({params}){cc} {{ {body} }};")
            elif m.name == c.name and injected:
                # GAP 2c: the injected parameters are appended to the (declared or synthesised) ctor
                ps = ", ".join(f"{self.pass_param(cpp_type(p.type))} {p.name}"
                               for p in self.ctor_params(c, m))
                sec[m.vis].append(f"    {m.name}({ps});")
            else:
                sec[m.vis].append("    " + self.method_decl(c, m)[0])
        out = [head + " {"]
        for vis in ("public", "protected", "private"):
            if sec[vis]:
                out.append(f"{vis}:")
                out.extend(sec[vis])
        out.append("};")
        return out

    # ---- source side
    def class_source(self, c: ClassInfo) -> list[str]:
        out: list[str] = []
        self.cpp_refs[c.name] = set(self.refs(c))
        if self.inline_bodies and self.data_class(c):
            return []                      # fully inlined into Data.hpp; no .cpp
        injected = self.injected_members(c)
        self.cpp_refs[c.name] |= {t for _, t in injected}
        ms = self.methods_of(c)
        has_ctor = any(m.name == c.name for m in ms)
        if injected:
            # GAP 2c: no default constructor; an out-of-line destructor only when member
            # types are incomplete in the header
            if self.incomplete_members(c):
                out += [f"{c.name}::~{c.name}() = default;", ""]
        elif self.incomplete_members(c) and not has_ctor:
            out += [f"{c.name}::{c.name}() = default;", "",
                    f"{c.name}::~{c.name}() = default;", ""]
        for m in ms:
            is_ctor = m.name == c.name
            if is_ctor and injected and not m.synth:
                self.warn(f"{c.name}::{c.name} is declared in the class diagram; the injected "
                          "collaborator parameters were appended to it")
            decl, pure = self.method_decl(c, m)
            if pure:
                continue
            mparams = self.ctor_params(c, m)
            params = ", ".join(f"{self.attr}{self.pass_param(cpp_type(p.type))} {p.name}"
                               for p in mparams)
            ret = "" if is_ctor else cpp_type(m.ret) + " "          # ctor: no return type
            init = ""
            if is_ctor and injected:
                # members are initialised in declaration order from the same-named parameter
                init = " : " + ", ".join(f"{n}({n})" for n, _ in injected)
            out.append(f"{ret}{c.name}::{m.name}({params}){init} {{")
            sid = f"{c.name}::{m.name}({','.join(cpp_type(p.type) for p in mparams)})"
            if m.seq is None:
                self.slots.emit(out, 4, sid, "" if (is_ctor or cpp_type(m.ret) == "void") else "return {};")
            else:
                out += SeqEmitter(self, c, m, sid).run()
            out += ["}", ""]
        return out


class SeqEmitter:
    """Turns one sequence diagram into the body of the method it implements."""

    def __init__(self, g: Gen, c: ClassInfo, m: Method, sid: str):
        self.g, self.c, self.m, self.sid, self.seq = g, c, m, sid, m.seq
        self.owner = self.seq.owner
        self.out: list[str] = []
        self.decls: dict[str, str] = {}
        self.locals: dict[str, str] = {}
        self.guards: dict[str, str] = {}
        self.counts = {"alt": 0, "opt": 0, "loop": 0}
        self.access: dict[str, tuple[str, str]] = {}
        self._while_advance: dict[int, str] = {}           # block level -> loop variable to advance
        # GAP 1: bindings and control points
        self.bindings: dict[str, tuple[str, tuple]] = {}   # name -> (type, (instance, method, [args]))
        self.while_break: dict[int, str] = {}              # body level -> var whose binding call ends the loop
        self.controls: list[ControlPoint] = []

    # -- collaborators
    @staticmethod
    def collab_lifelines(seq: SeqDiagram, dashed_calls: bool = False) -> list[str]:
        """Lifeline ids that receive an owner-sent call (used to compute Gen.collab_names)."""
        found: list[str] = []

        def walk(items) -> None:
            for it in items:
                if isinstance(it, Call):
                    if (it.sender == seq.owner and it.receiver != seq.owner
                            and (not it.dashed or dashed_calls) and it.receiver not in found):
                        found.append(it.receiver)
                else:
                    for br in it.branches:
                        walk(br.items)

        walk(seq.root.items[1:])
        return found

    def receivers(self, items) -> list[str]:
        found: list[str] = []
        for it in items:
            if isinstance(it, Call):
                if (it.sender == self.owner and it.receiver != self.owner
                        and (not it.dashed or self.g.dashed_calls) and it.receiver not in found):
                    found.append(it.receiver)
            else:
                for br in it.branches:
                    found += [r for r in self.receivers(br.items) if r not in found]
        return found

    def resolve(self, ids: list[str]) -> None:
        injected = dict(self.g.injected_members(self.c))      # name -> collaborator type
        extra = {n for n, _ in self.g.extra_members(self.c)}
        cands = [(p.name, cpp_type(p.type), True) for p in self.m.params]
        if not self.m.static:   # static methods cannot access instance members
            declared = {f.name for f in self.c.fields} | {p.name for p in self.c.parts}
            for f in self.c.fields:
                if not f.static:
                    t = f"std::shared_ptr<{injected[f.name]}>" if f.name in injected else cpp_type(f.type)
                    cands.append((f.name, t, False))
            for p in self.c.parts:
                t = (f"std::shared_ptr<{injected[p.name]}>" if p.name in injected
                     else f"std::vector<{p.type}>" if p.many else p.type)
                cands.append((p.name, t, False))
            for n, t in injected.items():       # members added for lifelines nothing provided
                if n not in declared:
                    cands.append((n, f"std::shared_ptr<{t}>", False))
        for lid in ids:
            ll = self.seq.lifelines[lid]
            hit = [c for c in cands if c[0] == ll.instance]
            if not hit:
                hit = [c for c in cands if bare_type(c[1]) == cpp_type(ll.type)]
                if len(hit) > 1:
                    raise DiagramError(
                        f"{self.sid}: lifeline '{lid}' ({ll.type}) matches several members/"
                        f"parameters ({', '.join(h[0] for h in hit)}); name the lifeline "
                        "after the one you mean")
            if hit:
                name, t, _ = hit[0]
                # GAP 2d: pointers and smart pointers (members AND parameters) use ->
                self.access[lid] = (name, "->" if is_pointer_like(t) else ".")
                if name in extra:
                    self.g.warn_once(f"{self.c.name}: no member or parameter for lifeline '{lid}' "
                                     f"({cpp_type(ll.type)}); added a new member "
                                     f"'std::shared_ptr<{cpp_type(ll.type)}> {name}' "
                                     "(inject it through the constructor)")
            else:
                t = cpp_type(ll.type)
                self.locals[ll.instance] = t
                self.access[lid] = (ll.instance, ".")
                self.g.warn(f"{self.sid}: {self.c.name} has no member or parameter for lifeline "
                            f"'{lid}' ({t}); using a default-constructed local '{ll.instance}'"
                            " - set it up in a user slot")
            self.g.cpp_refs[self.c.name] |= self.g.idents(ll.type)

    # -- emission
    def slot(self, lvl: int, sid: str) -> None:
        self.g.slots.emit(self.out, lvl * 4, f"{self.sid}/{sid}")

    def run(self) -> list[str]:
        items = self.seq.root.items[1:]
        self.resolve(self.receivers(items))
        # GAP 1a: bindings found at attach time become locals hoisted with the others
        reserved = {p.name for p in self.m.params}
        reserved |= {f.name for f in self.c.fields if not f.static} | {p.name for p in self.c.parts}
        reserved |= {n for n, _ in self.g.injected_members(self.c)}
        for call, name in (self.seq.call_bindings or {}).items():
            ty = (self.seq.call_bind_types or {})[call]
            if name in reserved:
                raise DiagramError(f"line {call.line}: the binding name '{name}' collides with a "
                                   f"parameter or member of {self.c.name}")
            if name in self.bindings and self.bindings[name][0] != ty:
                raise DiagramError(f"line {call.line}: '{name}' is already bound with type "
                                   f"{self.bindings[name][0]} and cannot be rebound as {ty}")
            self.bindings[name] = (ty, (self.access[call.receiver][0], CALL_RE.match(call.text)[1], []))
        self.slot(1, "begin")
        self.items(items, 1)
        ret = cpp_type(self.m.ret)
        if ret != "void":
            self.g.slots.emit(self.out, 4, f"{self.sid}/end", "return {};")
        res: list[str] = []
        for n, t in self.locals.items():
            res.append(f"    {t} {n}{{}};  // no member or parameter named '{n}': set it up in a slot")
        for n, t in self.decls.items():
            res.append(f"    {t} {n}{{}};")
        for n, (t, src) in self.bindings.items():
            res.append(f"    {self.g.attr}{t} {n}{{}};  // bound by {src[0]}.{src[1]}()")
        for n, text in self.guards.items():
            res.append(f"    bool {n} = false;  // guard: {comment_text(text) or '(none)'}")
        if res:
            res.append("")
        self.m.controls = self.controls
        self.m.bindings = dict(self.bindings)
        return res + self.out

    def items(self, items, lvl: int) -> None:
        for it in items:
            self.call(it, lvl) if isinstance(it, Call) else self.block(it, lvl)

    def call(self, c: Call, lvl: int) -> None:
        pad = "    " * lvl
        s, r, owner = c.sender, c.receiver, self.owner
        if c.dashed and not self.g.dashed_calls:
            self.out.append(f"{pad}// {s} --> {r}: {comment_text(c.text)}   (return, not a call)")
            return
        if s != owner:
            self.out.append(f"{pad}// {s} -> {r}: {comment_text(c.text)}   "
                            f"(not sent by {owner}: belongs in the method of {s})")
            self.g.warn(f"{self.sid}: line {c.line}: '{s}->>{r}: {c.text}' is not sent by the "
                        f"owner '{owner}', so no code was generated for it")
            return
        m = CALL_RE.match(c.text)
        if not m:
            raise DiagramError(f"line {c.line}: message '{c.text}' is not a call like name(args)")
        pnames = {p.name: cpp_type(p.type) for p in self.m.params}
        exprs = []
        for a in split_top(m[2] or ""):
            a = a.strip()
            if not a:
                continue
            if t := TYPED_ARG_RE.match(a):
                ty = cpp_type(t[2])
                if t[1] in pnames:
                    if pnames[t[1]] != ty:
                        raise DiagramError(f"line {c.line}: '{t[1]}' is a parameter of type "
                                           f"{pnames[t[1]]} but is declared as {ty} here")
                elif self.decls.get(t[1], ty) != ty:
                    raise DiagramError(f"line {c.line}: argument '{t[1]}' is declared with two "
                                       f"different types ({self.decls[t[1]]} and {ty})")
                else:
                    self.decls[t[1]] = ty
                exprs.append(t[1])
            else:
                exprs.append(a)
        if r == owner:
            target = ""
        else:
            name, arrow = self.access[r]
            target = name + arrow
        self.out.append(f"{pad}// {s} -> {r}: {comment_text(c.text)}")
        # GAP 1a: assign the call result into the bound variable at the call site
        bind = (self.seq.call_bindings or {}).get(c)
        if bind:
            ty, _ = self.bindings[bind]
            self.out.append(f"{pad}{bind} = {target}{m[1]}({', '.join(exprs)});")
            src = (self.access[r][0], m[1], exprs)
            self.bindings[bind] = (ty, src)
            for cp in self.controls:     # a control recorded before its call was emitted gets the real args
                if cp.var == bind and cp.source and cp.source[:2] == src[:2] and not cp.source[2]:
                    cp.source = src
            if self.while_break.get(lvl) == bind:      # while (true) { x = ...; if (!x) break; ...
                self.out.append(f"{pad}if (!{bind}) break;")
                del self.while_break[lvl]
        else:
            self.out.append(f"{pad}{target}{m[1]}({', '.join(exprs)});")
        # slot ids ignore a trailing return type so adding/removing "bool" keeps user code attached
        key = c.text[:m.start("ret")].rstrip(" :->") if m["ret"] else c.text
        self.slot(lvl, f"after {s}>{r}.{key}")

    def guard_allowed(self) -> set[str]:
        """Identifiers a guard expression may use: parameters, bound variables and
        (non-static methods only) instance fields and parts."""
        allowed = {p.name for p in self.m.params} | set(self.bindings)
        if not self.m.static:
            allowed |= {f.name for f in self.c.fields if not f.static}
            allowed |= {p.name for p in self.c.parts}
        return allowed

    def _vars(self, expr: str) -> list[str]:
        """Bound variables referenced by a guard expression."""
        return [v for v in self.bindings if re.search(rf"(?<![\w.]){re.escape(v)}\b", expr)]

    def block(self, b: Block, lvl: int) -> None:
        pad = "    " * lvl
        self.counts[b.kind] += 1
        k = self.counts[b.kind]
        bid = f"{b.kind}{k}"
        first = b.branches[0].guard or ""
        allowed = self.guard_allowed()
        for i, br in enumerate(b.branches):
            g = br.guard or ""
            note = f"  // {comment_text(g)}" if g else ""
            if b.kind == "loop":
                if m := re.fullmatch(r"(\d+)\s+times?", g, re.I):
                    head = f"for (int loop{k}_i = 0; loop{k}_i < {m[1]}; ++loop{k}_i) {{"
                    self.controls.append(ControlPoint(bid, 0, "count", None, None, None, None, True))
                elif m := re.fullmatch(rf"for\s+each\s+({NAME_RE})\s+in\s+(\S+)", g, re.I):
                    # "loop for each i in K" -> for (int i = 0; i < K; ++i) so the
                    # loop variable is in scope for the generated calls in the body.
                    head = f"for (int {m[1]} = 0; {m[1]} < {m[2]}; ++{m[1]}) {{"
                    self.controls.append(ControlPoint(bid, 0, "count", None, None, None, None, True))
                elif m := re.fullmatch(rf"for\s+each\s+({NAME_RE})\s+(\S+)", g, re.I):
                    # "loop for each i labels" -> for (auto i : labels) so the loop
                    # variable is in scope for the generated calls in the body.
                    head = f"for (auto {m[1]} : {m[2]}) {{"
                    self.controls.append(ControlPoint(bid, 0, "count", None, None, None, None, True))
                elif m := re.fullmatch(rf"for\s+each\s+({NAME_RE})\s+in\s+({NAME_RE})\s+\(.+\)", g, re.I):
                    # "loop for each t in T (t from 1 to T-1)" -> a while loop whose
                    # variable is declared in the loop-body entry slot (the user
                    # writes 'int t = 1;') and advanced by the user in the slot
                    # after the last call in the body.
                    var, bound = m[1], m[2]
                    self.locals[var] = "int"
                    gn = f"loop{k}_cond"
                    self.guards[gn] = f"{var} < {bound}"
                    self._while_advance[lvl] = var
                    head = f"while ({gn}) {{"
                    self.controls.append(ControlPoint(bid, 0, "expr", f"{var} < {bound}", None, None, None, True))
                else:
                    # GAP 1b: a while-style loop whose guard is a bound variable that
                    # is bound by the FIRST message inside the loop body becomes
                    # while (true) { more = ...; if (!more) break; ... }
                    if g and g in self.bindings:
                        fc = br.items[0] if br.items and isinstance(br.items[0], Call) else None
                        if fc is not None and (self.seq.call_bindings or {}).get(fc) == g:
                            head = "while (true) {"
                            self.while_break[lvl + 1] = g
                            self.controls.append(ControlPoint(bid, 0, "binding", None, g,
                                                              self.bindings[g][0], self.bindings[g][1], True))
                            self.out.append(f"{pad}{head}{note}")
                            self.slot(lvl + 1, f"enter {b.kind}({g})")
                            self.items(br.items, lvl + 1)
                            self.while_break.pop(lvl + 1, None)
                            continue
                    expr = classify_guard(g, allowed)
                    if expr:
                        head = f"while ({expr}) {{"
                        self.controls.append(ControlPoint(bid, 0, "expr", expr, None, None, None, True,
                                                          self._vars(expr)))
                    else:
                        gn = f"loop{k}_cond"
                        self.guards[gn] = g
                        head = f"while ({gn}) {{"
                        if g:
                            self.g.warn(f"line {b.line}: loop guard '{g}' is not a C++ expression "
                                        f"over parameters/bound variables/members; the path cannot be "
                                        "selected by a test (assign the guard in the user slot)")
                        if g:
                            self.controls.append(ControlPoint(bid, 0, "free", None, None, None, None, False))
                        else:
                            self.controls.append(ControlPoint(bid, 0, "count", None, None, None, None, True))
                self.out.append(f"{pad}{head}{note}")
            elif i > 0 and not g:
                self.out.append(f"{pad}}} else {{")
                self.controls.append(ControlPoint(bid, i, "else"))
            else:
                # GAP 1b: a bound variable alone is a test-drivable condition
                if g and g in self.bindings:
                    head = f"if ({g}) {{" if i == 0 else f"}} else if ({g}) {{"
                    ty, src = self.bindings[g]
                    self.controls.append(ControlPoint(bid, i, "binding", None, g, ty, src, True))
                    self.out.append(f"{pad}{head}{note}")
                else:
                    expr = classify_guard(g, allowed)
                    if expr:
                        kw = "if" if i == 0 else "} else if"
                        self.out.append(f"{pad}{kw} ({expr}) {{{note}")
                        self.controls.append(ControlPoint(bid, i, "expr", expr, None, None, None, True,
                                                          self._vars(expr)))
                    else:
                        gn = f"{b.kind}{k}_guard{i}"
                        self.guards[gn] = g
                        kw = "if" if i == 0 else "} else if"
                        self.out.append(f"{pad}{kw} ({gn}) {{{note}")
                        if g:
                            self.g.warn(f"line {b.line}: guard '{g}' is not a C++ expression over "
                                        f"parameters/bound variables/members; the path cannot be selected "
                                        "by a test (assign the guard in the user slot)")
                        self.controls.append(ControlPoint(bid, i, "free", None, None, None, None, False))
            label = b.kind if i == 0 else "else"
            self.slot(lvl + 1, f"enter {label}({g})")
            self.items(br.items, lvl + 1)
        if b.kind == "loop" and lvl in self._while_advance:
            var = self._while_advance.pop(lvl)
            self.g.slots.emit(self.out, lvl * 4, f"{self.sid}/advance {var}", f"{var}++;")
        self.out.append(f"{pad}}}")
        self.slot(lvl, f"after {b.kind}({first})")


# --------------------------------------------------------------------------- driver

def split_diagrams(text: str):
    diagrams, cur, in_front, title = [], None, False, None
    for no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if line.startswith("```"):
            continue
        if line == "---":
            in_front = not in_front
            continue
        if in_front:
            if m := re.match(r"title:\s*(.+)", line):
                title = m[1].strip().strip("\"'")
            continue
        if m := re.match(r"^(classDiagram(?:-v2)?|sequenceDiagram)\b", line):
            cur = ["class" if m[1].startswith("class") else "sequence", title, []]
            diagrams.append(cur)
            title = None
        elif cur is not None:
            cur[2].append((no, raw))
    return diagrams


def build_model(text: str, external, warn, dashed_calls: bool = False):
    diagrams = split_diagrams(text)
    if not diagrams:
        raise DiagramError("no classDiagram or sequenceDiagram found")
    classes: dict[str, ClassInfo] = {}
    seqs: list[SeqDiagram] = []
    for kind, title, lines in diagrams:
        if kind == "class":
            parse_class_diagram(lines, classes, warn)
        else:
            s = parse_sequence(lines, warn)
            s.name = s.name or title
            seqs.append(s)
    finalize_parts(classes)
    attach_sequences(classes, seqs, set(external), warn, dashed_calls)
    return classes


def file_names(classes, external, single: str | None, data_split: bool = False) -> list[str]:
    if single:
        return [f"{single}.hpp", f"{single}.cpp"]
    if data_split:
        out = ["Data.hpp"]
        for n in classes:
            if n in set(external):
                continue
            c = classes[n]
            if bool(c.fields) and not any(m.seq is not None for m in c.methods):
                continue
            out.append(f"{n}.hpp")
            out.append(f"{n}.cpp")
        return out
    return [f"{n}{e}" for n in classes if n not in set(external) for e in (".hpp", ".cpp")]


def orphan_block(items: list[tuple[str, str]]) -> list[str]:
    if not items:
        return []
    out = ["// ===== ORPHANED USER CODE: these slots no longer exist in the diagrams =====",
           "// Move the code somewhere useful and delete the block. If the slot id returns",
           "// to the diagram, the code is restored automatically."]
    for sid, code in items:
        out.append(f"// ORPHAN BEGIN [{sid}]")
        out += [f"// | {ln}".rstrip() for ln in code.splitlines()]
        out.append(f"// ORPHAN END [{sid}]")
    return out


BANNER = ["// Generated by mermaid2cpp. Edit ONLY inside USER CODE slots;",
          "// everything else is overwritten on regeneration."]


def generate_files(classes, preserved, includes=(), external=(), dashed_calls=False,
                   warn=print, maybe_unused=True, single: str | None = None,
                   inc_prefix: str = "", data_split: bool = False,
                   testable: bool = False) -> dict[str, str]:
    slots = Slots(preserved)
    g = Gen(classes, slots, external, dashed_calls, maybe_unused, warn,
            inline_bodies=data_split, testable=testable)
    order = g.class_order()
    files: dict[str, str] = {}
    user_inc = [f'#include "{h}"' for h in includes]

    # generate in a stable order so slot-id de-duplication (#2, #3) is deterministic
    decls = {c.name: g.class_decl(c) for c in order}
    srcs: dict[str, list[str]] = {}
    for c in order:
        srcs[c.name] = g.class_source(c)

    def orphans_for(names: set[str] | None) -> list[str]:
        items = [(k, v) for k, v in slots.orphans()
                 if names is None or k.split("::", 1)[0] in names]
        return orphan_block(items)

    def assemble(name: str, includes_: list[str], body: list[str], extra: list[str] = ()):
        txt = "\n".join(body)
        head = BANNER + (["#pragma once"] if name.endswith(".hpp") else [])
        head += std_includes(txt) + includes_
        slots.emit(head, 0, f"{name}:includes")
        files[name] = "\n".join(head) + "\n\n" + txt.rstrip() + "\n" + (
            ("\n" + "\n".join(extra) + "\n") if extra else "")

    if single:
        body = [f"class {c.name};" for c in order] + [""]
        for c in order:
            body += decls[c.name] + [""]
        assemble(f"{single}.hpp", user_inc, body)
        body = []
        for c in order:
            body += srcs[c.name]
        assemble(f"{single}.cpp", [f'#include "{inc_prefix}{single}.hpp"'], body, orphans_for(None))
    elif g.inline_bodies:
        # Data classes (fields, no sequence method) are inlined into one Data.hpp;
        # every other class gets its own .hpp + .cpp. Data.hpp must be included by
        # the non-data headers so their references to data classes are complete.
        data = [c for c in order if g.data_class(c)]
        rest = [c for c in order if not g.data_class(c)]
        data_names = {d.name for d in data}
        if data:
            fwd = sorted({n for d in data for n in g.hdr_includes(d) if n not in data_names})
            body = [f"class {n};" for n in fwd] + ([""] if fwd else [])
            for c in data:
                body += decls[c.name] + [""]
            assemble("Data.hpp", user_inc, body)
        for c in rest:
            hi = [n for n in g.hdr_includes(c) if n not in data_names]
            inc = [f'#include "{n}.hpp"' for n in hi] + (['#include "Data.hpp"'] if data else [])
            fwd = sorted((g.refs(c) | {p.type for p in c.parts if p.type in g.genset})
                         - set(hi) - {c.name} - data_names)
            body = [f"class {n};" for n in fwd] + ([""] if fwd else []) + decls[c.name]
            assemble(f"{c.name}.hpp", inc + user_inc, body)
        for c in rest:
            refs = sorted(g.cpp_refs.get(c.name, set()) - {c.name} - data_names)
            inc = ([f'#include "{inc_prefix}{c.name}.hpp"'] if inc_prefix else [f'#include "{c.name}.hpp"']) \
                  + [f'#include "{inc_prefix}{n}.hpp"' for n in refs]
            if data_names:
                inc.append(f'#include "{inc_prefix}Data.hpp"')
            assemble(f"{c.name}.cpp", inc, srcs[c.name], orphans_for({c.name}))
    else:
        for c in order:
            inc = [f'#include "{n}.hpp"' for n in g.hdr_includes(c)]
            fwd = sorted((g.refs(c) | {p.type for p in c.parts if p.type in g.genset})
                         - set(g.hdr_includes(c)) - {c.name})
            body = [f"class {n};" for n in fwd] + ([""] if fwd else []) + decls[c.name]
            assemble(f"{c.name}.hpp", inc + user_inc, body)
        for c in order:
            refs = sorted(g.cpp_refs.get(c.name, set()) - {c.name})
            inc = [f'#include "{inc_prefix}{c.name}.hpp"'] + [f'#include "{inc_prefix}{n}.hpp"' for n in refs]
            assemble(f"{c.name}.cpp", inc, srcs[c.name], orphans_for({c.name}))
    return files


def main() -> int:
    ap = argparse.ArgumentParser(description="Mermaid class/sequence diagrams -> C++ (.hpp/.cpp)")
    ap.add_argument("input", help=".mmd or .md file containing mermaid diagrams")
    ap.add_argument("-o", "--out-dir", metavar="DIR",
                    help="write files here (created if needed); existing files have their "
                         "user code slots merged. Without -o all files are printed.")
    ap.add_argument("--hpp-dir", metavar="DIR",
                    help="write the .hpp files here instead of -o DIR (e.g. include/)")
    ap.add_argument("--cpp-dir", metavar="DIR",
                    help="write the .cpp files here instead of -o DIR (e.g. src/)")
    ap.add_argument("--include-prefix", metavar="PREFIX", default="",
                    help='prefix for the generated headers in the .cpp files\' #include lines, '
                         'e.g. "../include/" or "mylib/" (default: none, i.e. the header '
                         'directory is on the compiler include path)')
    ap.add_argument("--single", metavar="NAME",
                    help="write one NAME.hpp + NAME.cpp holding every class instead of one pair per class")
    ap.add_argument("--data-split", action="store_true",
                    help="put every data class (fields, no sequence method) into one Data.hpp "
                         "with no .cpp file; other classes keep their own .hpp + .cpp")
    ap.add_argument("--include", action="append", default=[], metavar="HEADER",
                    help='add #include "HEADER" to every generated header (repeatable)')
    ap.add_argument("--external", action="append", default=[], metavar="TYPE",
                    help="class that already exists: do not generate it (repeatable)")
    ap.add_argument("--dashed-calls", action="store_true",
                    help="treat dashed arrows (-->>) as calls instead of returns")
    ap.add_argument("--testable", action="store_true",
                    help="generate mock-injectable code: collaborators get virtual methods/"
                         "destructors, collaborator-typed members become std::shared_ptr<T> "
                         "injected through the constructor, and sequence-owning classes get "
                         "'friend struct <Class>_TestAccess;'")
    ap.add_argument("--no-maybe-unused", action="store_true",
                    help="do not add [[maybe_unused]] to generated parameters (pre-C++17 targets)")
    a = ap.parse_args()

    warn = lambda m: print(f"warning: {m}", file=sys.stderr)
    import os
    hdir = a.hpp_dir or a.out_dir
    cdir = a.cpp_dir or a.out_dir
    if (a.hpp_dir or a.cpp_dir) and not (hdir and cdir):
        print("error: give both --hpp-dir and --cpp-dir (or -o DIR for the one you leave out)",
              file=sys.stderr)
        return 1
    to_disk = bool(hdir and cdir)

    def dest(fn: str) -> str:
        return os.path.join(hdir if fn.endswith(".hpp") else cdir, fn)

    try:
        classes = build_model(open(a.input, encoding="utf-8").read(), a.external, warn,
                              a.dashed_calls)
        preserved: dict[str, str] = {}
        if to_disk:
            for fn in file_names(classes, a.external, a.single, a.data_split):
                path = dest(fn)
                if os.path.exists(path):
                    preserved.update(harvest(open(path, encoding="utf-8").read()))
        files = generate_files(classes, preserved, a.include, a.external, a.dashed_calls,
                               warn, not a.no_maybe_unused, a.single, a.include_prefix,
                               data_split=a.data_split, testable=a.testable)
    except DiagramError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if to_disk:
        for d in {hdir, cdir}:
            os.makedirs(d, exist_ok=True)
        for fn, txt in files.items():
            with open(dest(fn), "w", encoding="utf-8") as f:
                f.write(txt)
        where = hdir if hdir == cdir else f"{hdir} (.hpp) and {cdir} (.cpp)"
        print(f"wrote {len(files)} files to {where}", file=sys.stderr)
    else:
        for fn, txt in files.items():
            sys.stdout.write(f"// ===== FILE: {fn} =====\n{txt}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
