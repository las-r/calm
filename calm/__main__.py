import sys
import shutil
import platform
import subprocess
from pathlib import Path
import llvmlite.ir as ir
from . import lexer
from . import parser
from . import nodes


def find_clang():
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


def default_target_triple():
    machine = platform.machine().lower()
    arch = "x86_64" if machine in ("x86_64", "amd64") else machine
    if sys.platform == "win32":
        return f"{arch}-w64-windows-gnu"
    if sys.platform == "darwin":
        return f"{arch}-apple-darwin"
    return f"{arch}-unknown-linux-gnu"


def main():
    # get file
    if len(sys.argv) < 2:
        print("usage: python -m calm <file.cal>")
        return
    infile = Path(sys.argv[1]).resolve()
    if not infile.is_file():
        print(f"error: no such file: {infile}", file=sys.stderr)
        sys.exit(1)
    code = infile.read_text(encoding="utf-8")

    # parse code
    tokens = lexer.tokenize(code)
    program = parser.parse(tokens)

    # compile to ir
    triple = default_target_triple()
    module = ir.Module(name=str(infile))
    module.triple = triple
    builder = ir.IRBuilder()
    ctx = nodes.Ctx(module, builder)
    program.codegen(ctx)
    base = infile.with_suffix("")
    llfile = base.with_suffix(".ll")
    llfile.write_text(str(module), encoding="utf-8")

    # find clang
    clang = find_clang()
    if clang is None:
        print("error: could not find 'clang'. Install LLVM/Clang and make sure it's on your PATH, or install it to a standard location.", file=sys.stderr)
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

    llfile.unlink()
    print(f"Output: {exe}")


if __name__ == "__main__":
    main()