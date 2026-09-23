import llvmlite.ir as ir

# calm nodes
# by las-r

# types and escape characters
INTTYPES = {"i8": 8, "i16": 16, "i32": 32, "i64": 64, "u8": 8, "u16": 16, "u32": 32, "u64": 64}
UNSIGNED = {"u8", "u16", "u32", "u64"}
ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", "\"": "\"", "'": "'"}

# escape helper
def unescape(s):
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt in ESCAPES:
                out.append(ESCAPES[nxt])
                i += 2
                continue
            else:
                raise Exception(f"Unknown escape sequence: \\{nxt}")
        out.append(c)
        i += 1
    return "".join(out)

# coercion helper
def coerce(ctx, val, targetty):
    if val.type == targetty:
        return val
    if isinstance(targetty, (ir.FloatType, ir.DoubleType)) and isinstance(val.type, ir.IntType):
        return ctx.builder.sitofp(val, targetty)
    if isinstance(val.type, (ir.FloatType, ir.DoubleType)) and isinstance(targetty, ir.IntType):
        return ctx.builder.fptosi(val, targetty)
    if isinstance(targetty, (ir.FloatType, ir.DoubleType)) and isinstance(val.type, (ir.FloatType, ir.DoubleType)):
        if isinstance(targetty, ir.DoubleType):
            return ctx.builder.fpext(val, targetty)
        return ctx.builder.fptrunc(val, targetty)
    if isinstance(targetty, ir.IntType) and isinstance(val.type, ir.IntType):
        if targetty.width > val.type.width:
            return ctx.builder.sext(val, targetty)
        elif targetty.width < val.type.width:
            return ctx.builder.trunc(val, targetty)
        return val
    raise Exception(f"Cannot coerce {val.type} to {targetty}")

# codegen context
class Ctx:
    def __init__(self, module, builder):
        self.module = module
        self.builder = builder
        self.symtable = {}
        self.types = {}
        self.funcs = {}
        self.loopstack = []
        self.structfields = {}

# base node
class Node:
    def codegen(self, ctx):
        raise Exception("codegen not implemented")

    def codegenptr(self, ctx):
        raise Exception(f"{type(self).__name__} has no address")

class TypeNode(Node):
    def __init__(self, name, slicedepth=0):
        self.name = name
        self.slicedepth = slicedepth

    def resolve(self, ctx):
        base = self.resolvebase(ctx)
        for _ in range(self.slicedepth):
            base = ir.PointerType(base)
        return base

    def resolvebase(self, ctx):
        if self.name in INTTYPES:
            return ir.IntType(INTTYPES[self.name])
        if self.name == "f32":
            return ir.FloatType()
        if self.name == "f64":
            return ir.DoubleType()
        if self.name == "void":
            return ir.VoidType()
        if self.name in ctx.types:
            return ctx.types[self.name]
        raise Exception(f"Unknown type: {self.name}")

    def isunsigned(self):
        return self.name in UNSIGNED

    def isfloat(self):
        return self.name in ("f32", "f64")

# literal nodes
class IntLiteralNode(Node):
    def __init__(self, value, bits=32):
        self.value = value
        self.bits = bits

    def codegen(self, ctx):
        return ir.Constant(ir.IntType(self.bits), self.value)

class FloatLiteralNode(Node):
    def __init__(self, value, bits=64):
        self.value = value
        self.bits = bits

    def codegen(self, ctx):
        ty = ir.DoubleType() if self.bits == 64 else ir.FloatType()
        return ir.Constant(ty, self.value)

class BoolLiteralNode(Node):
    def __init__(self, value):
        self.value = value

    def codegen(self, ctx):
        return ir.Constant(ir.IntType(1), int(self.value))

class StrLiteralNode(Node):
    def __init__(self, value):
        self.value = value

    def codegen(self, ctx):
        decoded = unescape(self.value)
        cstr = bytearray(decoded.encode("utf-8") + b'\x00')
        const = ir.Constant(ir.ArrayType(ir.IntType(8), len(cstr)), cstr)
        glob = ir.GlobalVariable(ctx.module, const.type, ctx.module.get_unique_name("strlit"))
        glob.initializer = const # type: ignore[assignment]
        glob.linkage = "internal"
        glob.global_constant = True
        zero = ir.Constant(ir.IntType(32), 0)
        return ctx.builder.gep(glob, [zero, zero], inbounds=True)

class SliceLiteralNode(Node):
    def __init__(self, items, elemtype):
        self.items = items
        self.elemtype = elemtype

    def codegen(self, ctx):
        elemty = self.elemtype.resolve(ctx)
        vals = [i.codegen(ctx) for i in self.items]
        arrty = ir.ArrayType(elemty, len(vals))
        glob = ir.GlobalVariable(ctx.module, arrty, ctx.module.get_unique_name("slicelit"))
        if all(isinstance(v, ir.Constant) for v in vals):
            glob.initializer = ir.Constant(arrty, vals) # type: ignore[assignment]
        else:
            glob.initializer = ir.Constant(arrty, ir.Undefined) # type: ignore[assignment]
        glob.linkage = "internal"
        glob.global_constant = True
        zero = ir.Constant(ir.IntType(32), 0)
        return ctx.builder.gep(glob, [zero, zero], inbounds=True)

# variable nodes
class VarRefNode(Node):
    def __init__(self, name):
        self.name = name

    def codegen(self, ctx):
        ptr = ctx.symtable[self.name]
        return ctx.builder.load(ptr, name=self.name)

    def codegenptr(self, ctx):
        return ctx.symtable[self.name]

class VarDeclNode(Node):
    def __init__(self, vartype, name, value=None):
        self.vartype = vartype
        self.name = name
        self.value = value

    def codegen(self, ctx):
        ty = self.vartype.resolve(ctx)
        ptr = ctx.builder.alloca(ty, name=self.name)
        ctx.symtable[self.name] = ptr
        if self.value is not None:
            val = self.value.codegen(ctx)
            val = coerce(ctx, val, ty)
            ctx.builder.store(val, ptr)
        return ptr

class AssignNode(Node):
    def __init__(self, expr, name=None, target=None):
        self.expr = expr
        self.name = name
        self.target = target

    def codegen(self, ctx):
        val = self.expr.codegen(ctx)
        if self.target is not None:
            ptr = self.target.codegenptr(ctx)
        elif self.name is not None:
            ptr = ctx.symtable[self.name]
        else:
            raise Exception("AssignNode needs a name or a target")
        targetty = ptr.type.pointee
        val = coerce(ctx, val, targetty)
        ctx.builder.store(val, ptr)
        return val

# operation nodes
class UnaryOpNode(Node):
    def __init__(self, op, a, isfloat=False):
        self.op = op
        self.a = a
        self.isfloat = isfloat

    def codegen(self, ctx):
        a = self.a.codegen(ctx)
        b = ctx.builder
        if self.op == "!":
            if isinstance(a.type, ir.IntType) and a.type.width == 1:
                return b.not_(a)
            return b.icmp_unsigned("==", a, ir.Constant(a.type, 0))
        if self.op == "~":
            return b.not_(a)
        if self.op == "-":
            return b.fneg(a) if self.isfloat else b.neg(a)
        raise Exception(f"Unknown unary operator: {self.op}")

class BinaryOpNode(Node):
    def __init__(self, a, op, b, isfloat=False, isunsigned=False):
        self.a = a
        self.op = op
        self.b = b
        self.isfloat = isfloat
        self.isunsigned = isunsigned

    def codegen(self, ctx):
        a = self.a.codegen(ctx)
        b = self.b.codegen(ctx)
        bld = ctx.builder
        if self.op == "+": return bld.fadd(a, b) if self.isfloat else bld.add(a, b)
        if self.op == "-": return bld.fsub(a, b) if self.isfloat else bld.sub(a, b)
        if self.op == "*": return bld.fmul(a, b) if self.isfloat else bld.mul(a, b)
        if self.op == "/":
            if self.isfloat: return bld.fdiv(a, b)
            return bld.udiv(a, b) if self.isunsigned else bld.sdiv(a, b)
        if self.op == "%":
            if self.isfloat: return bld.frem(a, b)
            return bld.urem(a, b) if self.isunsigned else bld.srem(a, b)
        if self.op == "&": return bld.and_(a, b)
        if self.op == "|": return bld.or_(a, b)
        if self.op == "^": return bld.xor(a, b)
        if self.op == "<<": return bld.shl(a, b)
        if self.op == ">>": return bld.lshr(a, b) if self.isunsigned else bld.ashr(a, b)
        if self.op in ("==", "!=", "<", ">", "<=", ">="):
            if self.isfloat:
                return bld.fcmp_ordered(self.op, a, b)
            return bld.icmp_unsigned(self.op, a, b) if self.isunsigned else bld.icmp_signed(self.op, a, b)
        if self.op == "&&": return bld.and_(a, b)
        if self.op == "||": return bld.or_(a, b)
        raise Exception(f"Unknown binary operator: {self.op}")

# array/slice nodes
class IndexNode(Node):
    def __init__(self, arr, idx):
        self.arr = arr
        self.idx = idx

    def codegen(self, ctx):
        return ctx.builder.load(self.codegenptr(ctx))

    def codegenptr(self, ctx):
        arr = self.arr.codegen(ctx)
        idx = self.idx.codegen(ctx)
        return ctx.builder.gep(arr, [idx], inbounds=True)

# control flow nodes
class IfNode(Node):
    def __init__(self, cond, body, ebody=None):
        self.cond = cond
        self.body = body
        self.ebody = ebody

    def codegen(self, ctx):
        cond = self.cond.codegen(ctx)
        if not (isinstance(cond.type, ir.IntType) and cond.type.width == 1):
            if isinstance(cond.type, (ir.FloatType, ir.DoubleType)):
                cond = ctx.builder.fcmp_ordered("!=", cond, ir.Constant(cond.type, 0.0))
            elif isinstance(cond.type, ir.PointerType):
                nullptr = ir.Constant(cond.type, None)
                cond = ctx.builder.icmp_unsigned("!=", cond, nullptr)
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
        for node in self.body:
            node.codegen(ctx)
        blk = ctx.builder.block
        if blk is None or not blk.is_terminated:
            ctx.builder.branch(endbb)
        if self.ebody:
            ctx.builder.position_at_end(elsebb)
            for node in self.ebody:
                node.codegen(ctx)
            blk = ctx.builder.block
            if blk is None or not blk.is_terminated:
                ctx.builder.branch(endbb)
        ctx.builder.position_at_end(endbb)

class WhileNode(Node):
    def __init__(self, cond, body):
        self.cond = cond
        self.body = body

    def codegen(self, ctx):
        func = ctx.builder.function
        condbb = func.append_basic_block("while.cond")
        bodybb = func.append_basic_block("while.body")
        endbb = func.append_basic_block("while.end")
        ctx.builder.branch(condbb)
        ctx.builder.position_at_end(condbb)
        cond = self.cond.codegen(ctx)
        ctx.builder.cbranch(cond, bodybb, endbb)
        ctx.builder.position_at_end(bodybb)
        ctx.loopstack.append(endbb)
        for node in self.body:
            node.codegen(ctx)
        ctx.loopstack.pop()
        blk = ctx.builder.block
        if blk is None or not blk.is_terminated:
            ctx.builder.branch(condbb)
        ctx.builder.position_at_end(endbb)

class BreakNode(Node):
    def __init__(self):
        pass

    def codegen(self, ctx):
        if not ctx.loopstack:
            raise Exception("'break' outside of loop")
        ctx.builder.branch(ctx.loopstack[-1])

# function nodes
class FunctionNode(Node):
    def __init__(self, rettype, name, params, body):
        self.rettype = rettype
        self.name = name
        self.params = params
        self.body = body

    def codegen(self, ctx):
        paramtypes = [p.paramtype.resolve(ctx) for p in self.params]
        fnty = ir.FunctionType(self.rettype.resolve(ctx), paramtypes)
        func = ir.Function(ctx.module, fnty, name=self.name)
        ctx.funcs[self.name] = func
        entry = func.append_basic_block("entry")
        savedbuilder = ctx.builder
        savedsymtable = ctx.symtable
        ctx.builder = ir.IRBuilder(entry)
        ctx.symtable = {}
        for arg, p in zip(func.args, self.params):
            arg.name = p.name
            ptr = ctx.builder.alloca(arg.type, name=p.name)
            ctx.builder.store(arg, ptr)
            ctx.symtable[p.name] = ptr
        for node in self.body:
            node.codegen(ctx)
        blk = ctx.builder.block
        if blk is None or not blk.is_terminated:
            if isinstance(fnty.return_type, ir.VoidType):
                ctx.builder.ret_void()
            else:
                ctx.builder.ret(ir.Constant(fnty.return_type, 0))
        ctx.builder = savedbuilder
        ctx.symtable = savedsymtable
        return func

class ParamNode:
    def __init__(self, paramtype, name):
        self.paramtype = paramtype
        self.name = name

class ExternFunctionNode(Node):
    def __init__(self, rettype, name, params, variadic=False):
        self.rettype = rettype
        self.name = name
        self.params = params
        self.variadic = variadic

    def codegen(self, ctx):
        paramtypes = [p.paramtype.resolve(ctx) for p in self.params]
        fnty = ir.FunctionType(self.rettype.resolve(ctx), paramtypes, var_arg=self.variadic)
        func = ir.Function(ctx.module, fnty, name=self.name)
        ctx.funcs[self.name] = func
        return func

class CallNode(Node):
    def __init__(self, name, args):
        self.name = name
        self.args = args

    def codegen(self, ctx):
        if self.name not in ctx.funcs:
            raise Exception(f"Undefined function: {self.name}")
        func = ctx.funcs[self.name]
        fnty = func.function_type
        numfixed = len(fnty.args)
        args = []
        for i, a in enumerate(self.args):
            val = a.codegen(ctx)
            if fnty.var_arg and i >= numfixed:
                if isinstance(val.type, ir.FloatType):
                    val = ctx.builder.fpext(val, ir.DoubleType())
                elif isinstance(val.type, ir.IntType) and val.type.width < 32:
                    val = ctx.builder.sext(val, ir.IntType(32))
            args.append(val)
        return ctx.builder.call(func, args)

class ReturnNode(Node):
    def __init__(self, expr=None):
        self.expr = expr

    def codegen(self, ctx):
        if self.expr is not None:
            ctx.builder.ret(self.expr.codegen(ctx))
        else:
            ctx.builder.ret_void()

# struct nodes
class FieldNode:
    def __init__(self, fieldtype, name):
        self.fieldtype = fieldtype
        self.name = name

class StructNode(Node):
    def __init__(self, name, fields):
        self.name = name
        self.fields = fields

    def codegen(self, ctx):
        structty = ctx.module.context.get_identified_type(self.name)
        fieldtypes = [f.fieldtype.resolve(ctx) for f in self.fields]
        structty.set_body(*fieldtypes)
        ctx.types[self.name] = structty
        ctx.structfields[self.name] = [f.name for f in self.fields]
        return structty
    
class StructLiteralNode(Node):
    def __init__(self, structname, values):
        self.structname = structname
        self.values = values

    def codegen(self, ctx):
        structty = ctx.types[self.structname]
        tmp = ctx.builder.alloca(structty, name=f"{self.structname}.lit")
        zero = ir.Constant(ir.IntType(32), 0)
        for i, valnode in enumerate(self.values):
            val = valnode.codegen(ctx)
            fieldidx = ir.Constant(ir.IntType(32), i)
            fieldptr = ctx.builder.gep(tmp, [zero, fieldidx], inbounds=True)
            ctx.builder.store(val, fieldptr)
        return ctx.builder.load(tmp)

class FieldAccessNode(Node):
    def __init__(self, obj, field):
        self.obj = obj
        self.field = field

    def fieldindex(self, ctx, structname):
        names = ctx.structfields[structname]
        return names.index(self.field)

    def codegen(self, ctx):
        return ctx.builder.load(self.codegenptr(ctx))

    def codegenptr(self, ctx):
        objptr = self.obj.codegenptr(ctx)
        structty = objptr.type.pointee
        idx = self.fieldindex(ctx, structty.name)
        zero = ir.Constant(ir.IntType(32), 0)
        fieldidx = ir.Constant(ir.IntType(32), idx)
        return ctx.builder.gep(objptr, [zero, fieldidx], inbounds=True)

# program root
class ProgramNode(Node):
    def __init__(self, decls):
        self.decls = decls

    def codegen(self, ctx):
        for decl in self.decls:
            if isinstance(decl, (StructNode, ExternFunctionNode)):
                decl.codegen(ctx)
        for decl in self.decls:
            if isinstance(decl, FunctionNode):
                decl.codegen(ctx)
        return ctx.module