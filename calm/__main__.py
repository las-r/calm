import sys
import shutil
import argparse
import platform
import subprocess
from pathlib import Path
import llvmlite.ir as ir
from . import lexer
from . import parser
from . import nodes


def fclang():
    found = shutil.which("clang")
    if found:
        return found
    candidates = []
    if sys.platform == "win32":
        candidates += [
            r"C:\Program Files\LLVM\bin\clang.exe",
            r"C:\Program Files (x86)\LLVM\bin\clang.exe",
        ]
    else:
        candidates += [
            "/usr/bin/clang",
            "/usr/local/bin/clang",
            "/opt/homebrew/opt/llvm/bin/clang",
        ]
    for c in candidates:
        if Path(c).is_file():
            return c
    return None


def dtt():
    machine = platform.machine().lower()
    arch = "x86_64" if machine in ("x86_64", "amd64") else machine
    if sys.platform == "win32":
        return f"{arch}-w64-windows-gnu"
    if sys.platform == "darwin":
        return f"{arch}-apple-darwin"
    return f"{arch}-unknown-linux-gnu"


def compilef(infile: Path, keep_llvmir: bool):
    if not infile.is_file():
        print(f"error: no such file: {infile}", file=sys.stderr)
        sys.exit(1)
    code = infile.read_text(encoding="utf-8")

    # parse code
    tokens = lexer.tokenize(code)
    program = parser.parse(tokens)

    # compile to ir
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
    clang = fclang()
    if clang is None:
        print(
            "error: could not find 'clang'. Install LLVM/Clang and make sure "
            "it's on your PATH, or install it to a standard location.",
            file=sys.stderr,
        )
        sys.exit(1)

    # build to exe
    exe = base.with_suffix(".exe" if sys.platform == "win32" else "")
    result = subprocess.run(
        [clang, str(llfile), "-o", str(exe), f"--target={triple}"],
        capture_output=True, text=True,
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
        command = "build"
        file = args.bare_file
        llvmir = args.bare_llvmir
    else:
        command = args.command
        file = args.file
        llvmir = args.llvmir

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