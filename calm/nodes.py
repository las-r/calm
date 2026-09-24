import llvmlite.ir as ir

# calm nodes
# by las-r

INTTYPES = {"i8": 8, "i16": 16, "i32": 32, "i64": 64, "u8": 8, "u16": 16, "u32": 32, "u64": 64}
UNSIGNED = {"u8", "u16", "u32", "u64"}
ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", "\"": "\"", "'": "'"}

# helpers
def unescape(s):
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt not in ESCAPES:
                raise Exception(f"Unknown escape sequence: \\{nxt}")
            out.append(ESCAPES[nxt]); i += 2; continue
        out.append(c); i += 1
    return "".join(out)

def coerce(ctx, val, targetty, unsigned=False):
    if val.type == targetty:
        return val
    b = ctx.builder
    sf, df = isinstance(val.type, (ir.FloatType, ir.DoubleType)), isinstance(targetty, (ir.FloatType, ir.DoubleType))
    si, di = isinstance(val.type, ir.IntType), isinstance(targetty, ir.IntType)
    if df and si: return b.uitofp(val, targetty) if unsigned else b.sitofp(val, targetty)
    if di and sf: return b.fptoui(val, targetty) if unsigned else b.fptosi(val, targetty)
    if df and sf: return b.fpext(val, targetty) if isinstance(targetty, ir.DoubleType) else b.fptrunc(val, targetty)
    if di and si:
        if targetty.width > val.type.width: return b.zext(val, targetty) if unsigned else b.sext(val, targetty)
        if targetty.width < val.type.width: return b.trunc(val, targetty)
        return val
    raise Exception(f"Cannot coerce {val.type} to {targetty}")

def widertype(a, b):
    def rank(ty) -> tuple:
        if isinstance(ty, ir.DoubleType): return (2, 0)
        if isinstance(ty, ir.FloatType): return (1, 0)
        if isinstance(ty, ir.IntType): return (0, ty.width)
        return (-1, 0)
    return a if rank(a) >= rank(b) else b


# base classes
class Ctx:
    def __init__(self, module, builder):
        self.module, self.builder = module, builder
        self.symtable, self.types, self.funcs = {}, {}, {}
        self.loopstack, self.structfields = [], {}
        
class Node:
    def codegen(self, ctx): raise Exception("codegen not implemented")
    def codegenptr(self, ctx): raise Exception(f"{type(self).__name__} has no address")

class TypeNode(Node):
    def __init__(self, name, slicedepth=0, arraylen=None):
        self.name, self.slicedepth, self.arraylen = name, slicedepth, arraylen

    def resolve(self, ctx):
        base = self.resolvebase(ctx)
        for _ in range(self.slicedepth):
            base = ir.PointerType(base)
        if self.arraylen is not None:
            base = ir.ArrayType(base, self.arraylen)
        return base

    def resolvebase(self, ctx):
        if self.name in INTTYPES: return ir.IntType(INTTYPES[self.name])
        if self.name == "f32": return ir.FloatType()
        if self.name == "f64": return ir.DoubleType()
        if self.name == "void": return ir.VoidType()
        if self.name in ctx.types: return ctx.types[self.name]
        raise Exception(f"Unknown type: {self.name}")

    def isunsigned(self): return self.name in UNSIGNED
    def isfloat(self): return self.name in ("f32", "f64")

# literal nodes
class IntLiteralNode(Node):
    def __init__(self, value, bits=32): self.value, self.bits = value, bits
    def codegen(self, ctx): return ir.Constant(ir.IntType(self.bits), self.value)
    
class CharLiteralNode(Node):
    def __init__(self, char): self.char = char
    def codegen(self, ctx): return IntLiteralNode(ord(self.char)).codegen(ctx)

class FloatLiteralNode(Node):
    def __init__(self, value, bits=64): self.value, self.bits = value, bits
    def codegen(self, ctx):
        return ir.Constant(ir.DoubleType() if self.bits == 64 else ir.FloatType(), self.value)

class BoolLiteralNode(Node):
    def __init__(self, value): self.value = value
    def codegen(self, ctx): return ir.Constant(ir.IntType(1), int(self.value))

def _globarray(ctx, prefix, data, elemty=None):
    """Shared helper: build a global constant array and return a pointer to its first element."""
    glob = ir.GlobalVariable(ctx.module, data.type, ctx.module.get_unique_name(prefix))
    glob.initializer = data
    glob.linkage = "internal"
    glob.global_constant = True
    zero = ir.Constant(ir.IntType(32), 0)
    return ctx.builder.gep(glob, [zero, zero], inbounds=True)

class StrLiteralNode(Node):
    def __init__(self, value): self.value = value
    def codegen(self, ctx):
        cstr = bytearray(unescape(self.value).encode("utf-8") + b'\x00')
        const = ir.Constant(ir.ArrayType(ir.IntType(8), len(cstr)), cstr)
        return _globarray(ctx, "strlit", const)

class SliceLiteralNode(Node):
    def __init__(self, items, elemtype): self.items, self.elemtype = items, elemtype
    def codegen(self, ctx):
        elemty = self.elemtype.resolve(ctx)
        vals = [i.codegen(ctx) for i in self.items]
        arrty = ir.ArrayType(elemty, len(vals))
        allconst = all(isinstance(v, ir.Constant) for v in vals)
        const = ir.Constant(arrty, vals if allconst else ir.Undefined)
        return _globarray(ctx, "slicelit", const)

# variable nodes
class VarRefNode(Node):
    def __init__(self, name): self.name = name
    def codegen(self, ctx): return ctx.builder.load(ctx.symtable[self.name], name=self.name)
    def codegenptr(self, ctx): return ctx.symtable[self.name]

class VarDeclNode(Node):
    def __init__(self, vartype, name, value=None):
        self.vartype, self.name, self.value = vartype, name, value

    def codegen(self, ctx):
        ty = self.vartype.resolve(ctx)
        ptr = ctx.builder.alloca(ty, name=self.name)
        ctx.symtable[self.name] = ptr
        if self.value is not None:
            ctx.builder.store(coerce(ctx, self.value.codegen(ctx), ty), ptr)
        return ptr

class AssignNode(Node):
    def __init__(self, expr, name=None, target=None):
        self.expr, self.name, self.target = expr, name, target

    def codegen(self, ctx):
        val = self.expr.codegen(ctx)
        if self.target is not None: ptr = self.target.codegenptr(ctx)
        elif self.name is not None: ptr = ctx.symtable[self.name]
        else: raise Exception("AssignNode needs a name or a target")
        val = coerce(ctx, val, ptr.type.pointee)
        ctx.builder.store(val, ptr)
        return val

# operation nodes
class UnaryOpNode(Node):
    def __init__(self, op, a, isfloat=False):
        self.op, self.a, self.isfloat = op, a, isfloat

    def codegen(self, ctx):
        a, b = self.a.codegen(ctx), ctx.builder
        if self.op == "!":
            if isinstance(a.type, ir.IntType) and a.type.width == 1: return b.not_(a)
            return b.icmp_unsigned("==", a, ir.Constant(a.type, 0))
        if self.op == "~": return b.not_(a)
        if self.op == "-": return b.fneg(a) if self.isfloat else b.neg(a)
        raise Exception(f"Unknown unary operator: {self.op}")

class BinaryOpNode(Node):
    _FOPS = {"+": "fadd", "-": "fsub", "*": "fmul"}
    _IOPS = {"+": "add", "-": "sub", "*": "mul", "&": "and_", "|": "or_", "^": "xor",
             "<<": "shl", "&&": "and_", "||": "or_"}
    _CMPS = {"==", "!=", "<", ">", "<=", ">="}

    def __init__(self, a, op, b, isfloat=False, isunsigned=False):
        self.a, self.op, self.b = a, op, b
        self.isfloat, self.isunsigned = isfloat, isunsigned

    def codegen(self, ctx):
        a, b = self.a.codegen(ctx), self.b.codegen(ctx)
        if a.type != b.type:
            target = widertype(a.type, b.type)
            a = coerce(ctx, a, target, unsigned=self.isunsigned)
            b = coerce(ctx, b, target, unsigned=self.isunsigned)
        bld, op, u, f = ctx.builder, self.op, self.isunsigned, self.isfloat
        if f and op in self._FOPS: return getattr(bld, self._FOPS[op])(a, b)
        if op in self._IOPS: return getattr(bld, self._IOPS[op])(a, b)
        if op == "/": return bld.fdiv(a, b) if f else (bld.udiv(a, b) if u else bld.sdiv(a, b))
        if op == "%": return bld.frem(a, b) if f else (bld.urem(a, b) if u else bld.srem(a, b))
        if op == ">>": return bld.lshr(a, b) if u else bld.ashr(a, b)
        if op in self._CMPS:
            return bld.fcmp_ordered(op, a, b) if f else (bld.icmp_unsigned(op, a, b) if u else bld.icmp_signed(op, a, b))
        raise Exception(f"Unknown binary operator: {op}")

# array/slice nodes
class IndexNode(Node):
    def __init__(self, arr, idx): self.arr, self.idx = arr, idx
    def codegen(self, ctx): return ctx.builder.load(self.codegenptr(ctx))

    def codegenptr(self, ctx):
        ptr = self.arr.codegenptr(ctx)
        idx = self.idx.codegen(ctx)
        pointee = ptr.type.pointee
        if isinstance(pointee, ir.ArrayType):
            zero = ir.Constant(ir.IntType(32), 0)
            return ctx.builder.gep(ptr, [zero, idx], inbounds=True)
        else:
            sptr = ctx.builder.load(ptr)
            return ctx.builder.gep(sptr, [idx], inbounds=True)

# control flow nodes
class IfNode(Node):
    def __init__(self, cond, body, ebody=None):
        self.cond, self.body, self.ebody = cond, body, ebody

    def codegen(self, ctx):
        cond = self.cond.codegen(ctx)
        if not (isinstance(cond.type, ir.IntType) and cond.type.width == 1):
            if isinstance(cond.type, (ir.FloatType, ir.DoubleType)):
                cond = ctx.builder.fcmp_ordered("!=", cond, ir.Constant(cond.type, 0.0))
            elif isinstance(cond.type, ir.PointerType):
                cond = ctx.builder.icmp_unsigned("!=", cond, ir.Constant(cond.type, None))
            elif isinstance(cond.type, ir.IntType):
                cond = ctx.builder.icmp_unsigned("!=", cond, ir.Constant(cond.type, 0))
            else:
                raise Exception(f"Cannot convert {cond.type} to a boolean condition")
        func = ctx.builder.function
        thenbb = func.append_basic_block("if.then")
        elsebb = func.append_basic_block("if.else") if self.ebody else None
        endbb = func.append_basic_block("if.end")
        ctx.builder.cbranch(cond, thenbb, elsebb if elsebb else endbb)

        ctx.builder.position_at_end(thenbb)
        for node in self.body: node.codegen(ctx)
        if not (ctx.builder.block and ctx.builder.block.is_terminated): ctx.builder.branch(endbb)

        if self.ebody:
            ctx.builder.position_at_end(elsebb)
            for node in self.ebody: node.codegen(ctx)
            if not (ctx.builder.block and ctx.builder.block.is_terminated): ctx.builder.branch(endbb)

        ctx.builder.position_at_end(endbb)

class WhileNode(Node):
    def __init__(self, cond, body): self.cond, self.body = cond, body

    def codegen(self, ctx):
        func = ctx.builder.function
        condbb = func.append_basic_block("while.cond")
        bodybb = func.append_basic_block("while.body")
        endbb = func.append_basic_block("while.end")
        ctx.builder.branch(condbb)
        ctx.builder.position_at_end(condbb)
        ctx.builder.cbranch(self.cond.codegen(ctx), bodybb, endbb)
        ctx.builder.position_at_end(bodybb)
        ctx.loopstack.append(endbb)
        for node in self.body: node.codegen(ctx)
        ctx.loopstack.pop()
        if not (ctx.builder.block and ctx.builder.block.is_terminated): ctx.builder.branch(condbb)
        ctx.builder.position_at_end(endbb)

class BreakNode(Node):
    def codegen(self, ctx):
        if not ctx.loopstack: raise Exception("'break' outside of loop")
        ctx.builder.branch(ctx.loopstack[-1])

# function nodes
class FunctionNode(Node):
    def __init__(self, rettype, name, params, body):
        self.rettype, self.name, self.params, self.body = rettype, name, params, body

    def codegen(self, ctx):
        paramtypes = [p.paramtype.resolve(ctx) for p in self.params]
        fnty = ir.FunctionType(self.rettype.resolve(ctx), paramtypes)
        func = ir.Function(ctx.module, fnty, name=self.name)
        ctx.funcs[self.name] = func

        saved = (ctx.builder, ctx.symtable, getattr(ctx, "currentrettype", None), getattr(ctx, "currentrettypenode", None))
        ctx.builder = ir.IRBuilder(func.append_basic_block("entry"))
        ctx.symtable = {}
        ctx.currentrettype = fnty.return_type
        ctx.currentrettypenode = self.rettype

        for arg, p in zip(func.args, self.params):
            arg.name = p.name
            ptr = ctx.builder.alloca(arg.type, name=p.name)
            ctx.builder.store(arg, ptr)
            ctx.symtable[p.name] = ptr
        for node in self.body: node.codegen(ctx)
        if not (ctx.builder.block and ctx.builder.block.is_terminated):
            ctx.builder.ret_void() if isinstance(fnty.return_type, ir.VoidType) else ctx.builder.ret(ir.Constant(fnty.return_type, 0))

        ctx.builder, ctx.symtable, ctx.currentrettype, ctx.currentrettypenode = saved
        return func

class ParamNode:
    def __init__(self, paramtype, name): self.paramtype, self.name = paramtype, name

class ExternFunctionNode(Node):
    def __init__(self, rettype, name, params, variadic=False):
        self.rettype, self.name, self.params, self.variadic = rettype, name, params, variadic

    def codegen(self, ctx):
        paramtypes = [p.paramtype.resolve(ctx) for p in self.params]
        fnty = ir.FunctionType(self.rettype.resolve(ctx), paramtypes, var_arg=self.variadic)
        func = ir.Function(ctx.module, fnty, name=self.name)
        ctx.funcs[self.name] = func
        return func

class CallNode(Node):
    def __init__(self, name, args, argtypes=None):
        self.name, self.args, self.argtypes = name, args, argtypes

    def codegen(self, ctx):
        if self.name not in ctx.funcs: raise Exception(f"Undefined function: {self.name}")
        func = ctx.funcs[self.name]
        fnty = func.function_type
        numfixed = len(fnty.args)
        args = []
        for i, a in enumerate(self.args):
            val = a.codegen(ctx)
            if fnty.var_arg and i >= numfixed:
                argtype = self.argtypes[i] if self.argtypes and i < len(self.argtypes) else None
                if isinstance(val.type, ir.FloatType):
                    val = ctx.builder.fpext(val, ir.DoubleType())
                elif isinstance(val.type, ir.IntType) and val.type.width < 32:
                    unsigned = argtype is not None and argtype.isunsigned()
                    val = ctx.builder.zext(val, ir.IntType(32)) if unsigned else ctx.builder.sext(val, ir.IntType(32))
            args.append(val)
        return ctx.builder.call(func, args)

class ReturnNode(Node):
    def __init__(self, expr=None): self.expr = expr

    def codegen(self, ctx):
        if self.expr is None:
            ctx.builder.ret_void()
            return
        val = self.expr.codegen(ctx)
        rettype = getattr(ctx, "currentrettype", None)
        if rettype is not None and val.type != rettype:
            rtn = getattr(ctx, "currentrettypenode", None)
            val = coerce(ctx, val, rettype, unsigned=rtn.isunsigned() if rtn is not None else False)
        ctx.builder.ret(val)

# struct nodes
class FieldNode:
    def __init__(self, fieldtype, name): self.fieldtype, self.name = fieldtype, name

class StructNode(Node):
    def __init__(self, name, fields): self.name, self.fields = name, fields

    def codegen(self, ctx):
        structty = ctx.module.context.get_identified_type(self.name)
        structty.set_body(*(f.fieldtype.resolve(ctx) for f in self.fields))
        ctx.types[self.name] = structty
        ctx.structfields[self.name] = [f.name for f in self.fields]
        return structty

class StructLiteralNode(Node):
    def __init__(self, structname, values): self.structname, self.values = structname, values

    def codegen(self, ctx):
        structty = ctx.types[self.structname]
        tmp = ctx.builder.alloca(structty, name=f"{self.structname}.lit")
        zero = ir.Constant(ir.IntType(32), 0)
        for i, valnode in enumerate(self.values):
            fieldptr = ctx.builder.gep(tmp, [zero, ir.Constant(ir.IntType(32), i)], inbounds=True)
            ctx.builder.store(valnode.codegen(ctx), fieldptr)
        return ctx.builder.load(tmp)

class FieldAccessNode(Node):
    def __init__(self, obj, field): self.obj, self.field = obj, field
    def fieldindex(self, ctx, structname): return ctx.structfields[structname].index(self.field)
    def codegen(self, ctx): return ctx.builder.load(self.codegenptr(ctx))
    def codegenptr(self, ctx):
        objptr = self.obj.codegenptr(ctx)
        idx = self.fieldindex(ctx, objptr.type.pointee.name)
        zero = ir.Constant(ir.IntType(32), 0)
        return ctx.builder.gep(objptr, [zero, ir.Constant(ir.IntType(32), idx)], inbounds=True)
    
# array nodes
class ArrayLiteralNode(Node):
    def __init__(self, elemtype, values): self.elemtype, self.values = elemtype, values

    def codegen(self, ctx):
        elemty = self.elemtype.resolve(ctx)
        arrty = ir.ArrayType(elemty, len(self.values))
        tmp = ctx.builder.alloca(arrty, name="arr.lit")
        zero = ir.Constant(ir.IntType(32), 0)
        for i, valnode in enumerate(self.values):
            ptr = ctx.builder.gep(tmp, [zero, ir.Constant(ir.IntType(32), i)], inbounds=True)
            ctx.builder.store(coerce(ctx, valnode.codegen(ctx), elemty), ptr)
        return ctx.builder.load(tmp)

# pointer nodes
class AddrOfNode(Node):
    def __init__(self, target): self.target = target
    def codegen(self, ctx): return self.target.codegenptr(ctx)

class DerefNode(Node):
    def __init__(self, target): self.target = target
    def codegen(self, ctx): return ctx.builder.load(self.target.codegen(ctx))
    def codegenptr(self, ctx): return self.target.codegen(ctx)

# program root
class ProgramNode(Node):
    def __init__(self, decls): self.decls = decls

    def codegen(self, ctx):
        for decl in self.decls:
            if isinstance(decl, (StructNode, ExternFunctionNode)): decl.codegen(ctx)
        for decl in self.decls:
            if isinstance(decl, FunctionNode): decl.codegen(ctx)
        return ctx.module