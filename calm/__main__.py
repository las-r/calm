import os
import sys
import subprocess
import llvmlite.ir as ir
from . import lexer
from . import parser
from . import nodes

def main():
    # open file
    if len(sys.argv) < 2:
        print("usage: python -m calm <file.cal>")
        return
    file = sys.argv[1]
    with open(file) as f:
        code = f.read()

    # parse code
    tokens = lexer.tokenize(code)
    program = parser.parse(tokens)

    # compile to ir
    module = ir.Module(name=file)
    module.triple = "x86_64-w64-windows-gnu"
    builder = ir.IRBuilder()
    ctx = nodes.Ctx(module, builder)
    program.codegen(ctx)
    base = file.rsplit(".", 1)[0]
    ll = base + ".ll"
    with open(ll, "w") as f:
        f.write(str(module))
        
    # build to exe
    exe = base
    result = subprocess.run(
        ["clang", ll, "-o", exe, "--target=x86_64-w64-windows-gnu"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print("clang build failed:", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)

    # finish up
    os.remove(ll)
    print(f"Output: {exe}")

if __name__ == "__main__":
    main()