from .nodes import *

# calm parser
# by las-r

# operator and type lists
UNOPS = ["-", "~", "!"]
BINOPS = ["+", "-", "*", "/", "%", "&", "|", "^", "<<", ">>",
          "==", "!=", "<=", ">=", "<", ">", "&&", "||"]
BUILTINTYPES = ["i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64",
                "f32", "f64", "void"]

# dynamic types set
knowntypes = set(BUILTINTYPES)

# type parser
def parsetype(tokens):
    slicedepth = 0
    while tokens.peek() == "[":
        tokens.eat()
        slicedepth += 1
    name = tokens.eat()
    for i in range(slicedepth):
        if tokens.peek() == "]":
            tokens.eat()
        else:
            raise SyntaxError("Expected closing ']' in type")
    return TypeNode(name, slicedepth)

def istype(tokens):
    tok = tokens.peek()
    return tok == "[" or tok in knowntypes

# atom parser
def parseatom(tokens):
    # addr of
    if tokens.peek() == "@":
        tokens.eat()
        a = parseatom(tokens)
        return AddrOfNode(a)
    
    # deref
    if tokens.peek() == "#":
        tokens.eat()
        a = parseatom(tokens)
        return DerefNode(a)

    # unary ops
    if tokens.peek() in UNOPS:
        op = tokens.eat()
        a = parseatom(tokens)
        return UnaryOpNode(op, a)

    # parentheses
    if tokens.peek() == "(":
        tokens.eat()
        expr = parseexpr(tokens)
        if tokens.peek() == ")":
            tokens.eat()
        else:
            raise SyntaxError("Expected closing ')'")
        node = expr
        while tokens.peek() in (".", ":"):
            tokens.eat()
            iexpr = parseatom(tokens)
            node = IndexNode(node, iexpr)
        return node

    # literal, variable, and function call
    tok = tokens.eat()
    if tok in knowntypes and tokens.peek() == "{":
        tokens.eat()
        values = []
        if tokens.peek() != "}":
            values.append(parseexpr(tokens))
            while tokens.peek() == ",":
                tokens.eat()
                values.append(parseexpr(tokens))
        if tokens.peek() == "}":
            tokens.eat()
        else:
            raise SyntaxError("Expected closing '}' in struct literal")
        node = StructLiteralNode(tok, values)
    elif tok.startswith('"') and tok.endswith('"'):
        node = StrLiteralNode(tok[1:-1])
    else:
        try:
            node = IntLiteralNode(int(tok))
        except ValueError:
            try:
                node = FloatLiteralNode(float(tok))
            except ValueError:
                if tokens.peek() == "(":
                    tokens.eat()
                    args = []
                    if tokens.peek() != ")":
                        args.append(parseexpr(tokens))
                        while tokens.peek() == ",":
                            tokens.eat()
                            args.append(parseexpr(tokens))
                    if tokens.peek() == ")":
                        tokens.eat()
                    else:
                        raise SyntaxError(f"Expected closing ')' in call '{tok}'")
                    node = CallNode(tok, args)
                else:
                    node = VarRefNode(tok)

    # field access and indexing
    while tokens.peek() in (".", ":"):
        if tokens.peek() == ".":
            tokens.eat()
            field = tokens.eat()
            node = FieldAccessNode(node, field)
        else:
            tokens.eat()
            iexpr = parseatom(tokens)
            node = IndexNode(node, iexpr)

    return node

# expression parser
def parseexpr(tokens):
    a = parseatom(tokens)
    while tokens.can_eat() and tokens.peek() in BINOPS:
        op = tokens.eat()
        b = parseatom(tokens)
        a = BinaryOpNode(a, op, b)
    return a

# block parser
def parseblock(tokens):
    body = []
    if tokens.peek() == "{":
        tokens.eat()
    else:
        raise SyntaxError("Expected opening '{'")
    while tokens.can_eat() and tokens.peek() != "}":
        body.append(parsestmt(tokens))
    if tokens.peek() == "}":
        tokens.eat()
    else:
        raise SyntaxError("Expected closing '}'")
    return body

# statement parser
def parsestmt(tokens):
    # if statement
    if tokens.peek() == "if":
        tokens.eat()
        cond = parseexpr(tokens)
        body = parseblock(tokens)
        ebody = None
        if tokens.peek() == "else":
            tokens.eat()
            if tokens.peek() == "if":
                ebody = [parsestmt(tokens)]
            else:
                ebody = parseblock(tokens)
        return IfNode(cond, body, ebody)

    # while statement
    if tokens.peek() == "while":
        tokens.eat()
        cond = parseexpr(tokens)
        body = parseblock(tokens)
        return WhileNode(cond, body)

    # break statement
    if tokens.peek() == "break":
        tokens.eat()
        return BreakNode()

    # function definition statement
    if tokens.peek() == "def":
        tokens.eat()
        rettype = parsetype(tokens)
        name = tokens.eat()
        params = parseparams(tokens)
        body = parseblock(tokens)
        return FunctionNode(rettype, name, params, body)

    # extern function statement
    if tokens.peek() == "extc":
        tokens.eat()
        if tokens.peek() == "def":
            tokens.eat()
        else:
            raise SyntaxError("Expected 'def' after 'extc'")
        rettype = parsetype(tokens)
        name = tokens.eat()
        params, variadic = parseexternparams(tokens)
        return ExternFunctionNode(rettype, name, params, variadic)

    # struct definition statement
    if tokens.peek() == "struct":
        tokens.eat()
        name = tokens.eat()
        fields = parsefields(tokens)
        knowntypes.add(name)
        return StructNode(name, fields)

    # return statement
    if tokens.peek() == "return":
        tokens.eat()
        if tokens.can_eat() and tokens.peek() not in ("}", "else"):
            expr = parseexpr(tokens)
        else:
            expr = None
        return ReturnNode(expr)

    # declaration
    if istype(tokens):
        vartype = parsetype(tokens)
        name = tokens.eat()
        value = None
        if tokens.peek() == "=":
            tokens.eat()
            if tokens.peek() == "{":
                value = parsestructliteral(tokens, vartype.name)
            else:
                value = parseexpr(tokens)
        return VarDeclNode(vartype, name, value)

    # assignment or fallback expression
    lhs = parseexpr(tokens)
    if tokens.peek() == "=":
        tokens.eat()
        rhs = parseexpr(tokens)
        if isinstance(lhs, VarRefNode):
            return AssignNode(rhs, name=lhs.name)
        if isinstance(lhs, (IndexNode, FieldAccessNode)):
            return AssignNode(rhs, target=lhs)
        raise SyntaxError("Invalid assignment target")

    return lhs

# struct literal parser
def parsestructliteral(tokens, structname):
    tokens.eat()
    values = []
    if tokens.peek() != "}":
        values.append(parseexpr(tokens))
        while tokens.peek() == ",":
            tokens.eat()
            values.append(parseexpr(tokens))
    if tokens.peek() == "}":
        tokens.eat()
    else:
        raise SyntaxError("Expected closing '}' in struct literal")
    return StructLiteralNode(structname, values)

# param list parser
def parseparams(tokens):
    params = []
    if tokens.peek() == "(":
        tokens.eat()
        if tokens.peek() != ")":
            params.append(parseparam(tokens))
            while tokens.peek() == ",":
                tokens.eat()
                params.append(parseparam(tokens))
        if tokens.peek() == ")":
            tokens.eat()
        else:
            raise SyntaxError("Expected closing ')' in parameter list")
    return params

# param parser
def parseparam(tokens):
    ptype = parsetype(tokens)
    name = tokens.eat()
    return ParamNode(ptype, name)

# extern param list parser
def parseexternparams(tokens):
    params = []
    variadic = False
    if tokens.peek() == "(":
        tokens.eat()
        if tokens.peek() != ")":
            if tokens.peek() == "...":
                tokens.eat()
                variadic = True
            else:
                params.append(parseparam(tokens))
                while tokens.peek() == ",":
                    tokens.eat()
                    if tokens.peek() == "...":
                        tokens.eat()
                        variadic = True
                        break
                    params.append(parseparam(tokens))
        if tokens.peek() == ")":
            tokens.eat()
        else:
            raise SyntaxError("Expected closing ')' in extern parameter list")
    return params, variadic

# struct field list parser
def parsefields(tokens):
    fields = []
    if tokens.peek() == "{":
        tokens.eat()
    else:
        raise SyntaxError("Expected opening '{' in struct definition")
    if tokens.peek() != "}":
        fields.append(parsefield(tokens))
        while tokens.peek() == ",":
            tokens.eat()
            fields.append(parsefield(tokens))
    if tokens.peek() == "}":
        tokens.eat()
    else:
        raise SyntaxError("Expected closing '}' in struct definition")
    return fields

# field parser
def parsefield(tokens):
    ftype = parsetype(tokens)
    name = tokens.eat()
    return FieldNode(ftype, name)

# parser
def parse(tokens):
    decls = []
    while tokens.can_eat():
        decls.append(parsestmt(tokens))
    return ProgramNode(decls)