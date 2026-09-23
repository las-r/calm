import argparse
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
import llvmlite.ir as ir
from . import lexer
from . import parser
from . import nodes

# stdlib path and regex
STDDIR = Path(__file__).parent / "std"
USESTRRE = re.compile(r';use\s+"([^"]+)"\s*$')
USEIDRE = re.compile(r';use\s+(\w+)\s*$')

# stdlib finder
def findstd(name):
    path = STDDIR / f"{name}.cal"
    if not path.is_file():
        print(f"error: no such stdlib module: {name}", file=sys.stderr)
        sys.exit(1)
    return path

# import resolver
def resolveuse(infile, code, seen: set | None = None):
    if seen is None:
        seen = set()
    infile = infile.resolve()
    if infile in seen:
        return ""
    seen.add(infile)
    lines = code.splitlines()
    resolved = []
    i = 0
    nlines = len(lines)
    while i < nlines:
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            resolved.append(line)
            i += 1
            continue
        mstr = USESTRRE.match(stripped)
        if mstr:
            incpath = (infile.parent / mstr.group(1)).resolve()
            if not incpath.is_file():
                print(f"error: no such file: {incpath}", file=sys.stderr)
                sys.exit(1)
            resolved.append(resolveuse(incpath, incpath.read_text(encoding="utf-8"), seen))
            i += 1
            continue
        mid = USEIDRE.match(stripped)
        if mid:
            incpath = findstd(mid.group(1))
            resolved.append(resolveuse(incpath, incpath.read_text(encoding="utf-8"), seen))
            i += 1
            continue
        if stripped.startswith(";use"):
            print(f"error: malformed ;use directive: {stripped!r}", file=sys.stderr)
            sys.exit(1)
        break
    for n, line in enumerate(lines[i:], start=i + 1):
        if line.strip().startswith(";use"):
            print(
                f"error: {infile}:{n}: ';use' must appear before any other code",
                file=sys.stderr,
            )
            sys.exit(1)
    resolved.append("\n".join(lines[i:]))
    return "\n".join(resolved)

# clang finder
def findclang():
    found = shutil.which("clang")
    if found:
        return found
    candidates = (
        (r"C:\Program Files\LLVM\bin\clang.exe", r"C:\Program Files (x86)\LLVM\bin\clang.exe")
        if sys.platform == "win32"
        else ("/usr/bin/clang", "/usr/local/bin/clang", "/opt/homebrew/opt/llvm/bin/clang")
    )
    for c in candidates:
        if Path(c).is_file():
            return c
    return None

# target triple detector
def dtt():
    machine = platform.machine().lower()
    arch = "x86_64" if machine in ("x86_64", "amd64") else machine
    if sys.platform == "win32":
        return f"{arch}-w64-mingw32"
    if sys.platform == "darwin":
        return f"{arch}-apple-darwin"
    return f"{arch}-unknown-linux-gnu"

# compiler
def compilef(infile: Path, keep_llvmir: bool) -> Path:
    if not infile.is_file():
        print(f"error: no such file: {infile}", file=sys.stderr)
        sys.exit(1)
    raw = infile.read_text(encoding="utf-8")
    code = resolveuse(infile, raw)

    # parse code
    tokens = lexer.tokenize(code)
    program = parser.parse(tokens)

    # compile to IR
    triple = dtt()
    module = ir.Module(name=str(infile))
    module.triple = triple
    builder = ir.IRBuilder()
    ctx = nodes.Ctx(module, builder)
    program.codegen(ctx)
    base = infile.with_suffix("")
    llfile = base.with_suffix(".ll")
    llfile.write_text(str(module), encoding="utf-8")

    # find clang
    clang = findclang()
    if clang is None:
        print(
            "error: could not find 'clang'. Install LLVM/Clang and make sure "
            "it's on your PATH, or install it to a standard location.",
            file=sys.stderr,
        )
        sys.exit(1)

    # build executable
    exe = base.with_suffix(".exe" if sys.platform == "win32" else "")
    result = subprocess.run(
        [clang, str(llfile), "-o", str(exe), f"--target={triple}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("clang build failed:", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
    if keep_llvmir:
        print(f"LLVM IR: {llfile}")
    else:
        llfile.unlink()
    return exe

# main
def main():
    argparser = argparse.ArgumentParser(prog="calm", description="The calm compiler")
    subparsers = argparser.add_subparsers(dest="command")
    buildparser = subparsers.add_parser("build", help="compile a .cal file to an executable")
    buildparser.add_argument("file", help="path to the .cal source file")
    buildparser.add_argument("--llvmir", action="store_true", help="keep the generated .ll file")
    runparser = subparsers.add_parser("run", help="compile a .cal file, run it, then delete the executable")
    runparser.add_argument("file", help="path to the .cal source file")
    runparser.add_argument("--llvmir", action="store_true", help="keep the generated .ll file")
    argparser.add_argument("bare_file", nargs="?", default=None, help=argparse.SUPPRESS)
    argparser.add_argument("--llvmir", dest="bare_llvmir", action="store_true", help=argparse.SUPPRESS)
    args = argparser.parse_args()

    if args.command is None:
        if args.bare_file is None:
            argparser.print_usage(sys.stderr)
            sys.exit(1)
        command, file, llvmir = "build", args.bare_file, args.bare_llvmir
    else:
        command, file, llvmir = args.command, args.file, args.llvmir

    infile = Path(file).resolve()
    exe = compilef(infile, keep_llvmir=llvmir)
    if command == "build":
        print(f"Output: {exe}")
    elif command == "run":
        result = subprocess.run([str(exe)])
        exe.unlink()
        sys.exit(result.returncode)

if __name__ == "__main__":
    main()