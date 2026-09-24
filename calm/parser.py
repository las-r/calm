from .nodes import *

# calm parser
# by las-r

UNOPS = ["-", "~", "!"]
BINOPS = ["+", "-", "*", "/", "%", "&", "|", "^", "<<", ">>",
          "==", "!=", "<=", ">=", "<", ">", "&&", "||"]
BUILTINTYPES = ["i8", "i16", "i32", "i64", "u8", "u16", "u32", "u64",
                "f32", "f64", "void"]

knowntypes = set(BUILTINTYPES)

# small helpers
def expect(tokens, tok, errmsg):
    if tokens.peek() == tok:
        tokens.eat()
    else:
        raise SyntaxError(errmsg)
    
def istype(tokens):
    tok = tokens.peek()
    return tok == "[" or tok in knowntypes

# list parser
def parselist(tokens, closer, parseitem):
    items = []
    if tokens.peek() != closer:
        items.append(parseitem(tokens))
        while tokens.peek() == ",":
            tokens.eat()
            items.append(parseitem(tokens))
    return items

# braced parser
def parsebraced(tokens, opener, closer, errname, parseitem):
    expect(tokens, opener, f"Expected opening '{opener}' in {errname}")
    items = parselist(tokens, closer, parseitem)
    expect(tokens, closer, f"Expected closing '{closer}' in {errname}")
    return items

# type parser
def parsetype(tokens):
    slicedepth = 0
    while tokens.peek() == "[":
        tokens.eat()
        slicedepth += 1
    name = tokens.eat()
    for _ in range(slicedepth):
        expect(tokens, "]", "Expected closing ']' in type")
    return TypeNode(name, slicedepth)

# struct literal parser
def parsestructliteral(tokens, structname):
    values = parsebraced(tokens, "{", "}", "struct literal", parseexpr)
    return StructLiteralNode(structname, values)

# postfix parser
def parsepostfix(tokens, node):
    while tokens.peek() in (".", ":"):
        if tokens.peek() == ".":
            tokens.eat()
            node = FieldAccessNode(node, tokens.eat())
        else:
            tokens.eat()
            node = IndexNode(node, parseatom(tokens))
    return node

# atom parser
def parseatom(tokens):
    if tokens.peek() == "@":
        tokens.eat()
        return AddrOfNode(parseatom(tokens))
    if tokens.peek() == "#":
        tokens.eat()
        return DerefNode(parseatom(tokens))
    if tokens.peek() in UNOPS:
        op = tokens.eat()
        return UnaryOpNode(op, parseatom(tokens))
    if tokens.peek() == "(":
        tokens.eat()
        expr = parseexpr(tokens)
        expect(tokens, ")", "Expected closing ')'")
        return parsepostfix(tokens, expr)

    # literal, variable, and function call
    tok = tokens.eat()
    if tok in knowntypes and tokens.peek() == "{":
        node = parsestructliteral(tokens, tok)
    elif tok.startswith("`"):
        node = CharLiteralNode(tok[1])
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
                    args = parselist(tokens, ")", parseexpr)
                    expect(tokens, ")", f"Expected closing ')' in call '{tok}'")
                    node = CallNode(tok, args)
                else:
                    node = VarRefNode(tok)

    return parsepostfix(tokens, node)

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
    expect(tokens, "{", "Expected opening '{'")
    while tokens.can_eat() and tokens.peek() != "}":
        body.append(parsestmt(tokens))
    expect(tokens, "}", "Expected closing '}'")
    return body

# statement parser
def parsestmt(tokens):
    kw = tokens.peek()

    if kw == "if":
        tokens.eat()
        cond = parseexpr(tokens)
        body = parseblock(tokens)
        ebody = None
        if tokens.peek() == "else":
            tokens.eat()
            ebody = [parsestmt(tokens)] if tokens.peek() == "if" else parseblock(tokens)
        return IfNode(cond, body, ebody)
    if kw == "while":
        tokens.eat()
        cond = parseexpr(tokens)
        return WhileNode(cond, parseblock(tokens))
    if kw == "break":
        tokens.eat()
        return BreakNode()
    if kw == "def":
        tokens.eat()
        rettype = parsetype(tokens)
        name = tokens.eat()
        params = parseparams(tokens)
        return FunctionNode(rettype, name, params, parseblock(tokens))
    if kw == "extc":
        tokens.eat()
        expect(tokens, "def", "Expected 'def' after 'extc'")
        rettype = parsetype(tokens)
        name = tokens.eat()
        params, variadic = parseexternparams(tokens)
        return ExternFunctionNode(rettype, name, params, variadic)
    if kw == "struct":
        tokens.eat()
        name = tokens.eat()
        fields = parsefields(tokens)
        knowntypes.add(name)
        return StructNode(name, fields)
    if kw == "return":
        tokens.eat()
        expr = parseexpr(tokens) if tokens.can_eat() and tokens.peek() not in ("}", "else") else None
        return ReturnNode(expr)

    # declaration
    if istype(tokens):
        vartype = parsetype(tokens)
        name = tokens.eat()
        value = None
        if tokens.peek() == "=":
            tokens.eat()
            value = parsestructliteral(tokens, vartype.name) if tokens.peek() == "{" else parseexpr(tokens)
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

# param parsers
def parseparam(tokens):
    ptype = parsetype(tokens)
    return ParamNode(ptype, tokens.eat())

def parseparams(tokens):
    if tokens.peek() != "(":
        return []
    return parsebraced(tokens, "(", ")", "parameter list", parseparam)

def parseexternparams(tokens):
    params, variadic = [], False
    if tokens.peek() != "(":
        return params, variadic
    tokens.eat()
    if tokens.peek() != ")":
        while True:
            if tokens.peek() == "...":
                tokens.eat()
                variadic = True
                break
            params.append(parseparam(tokens))
            if tokens.peek() != ",":
                break
            tokens.eat()
    expect(tokens, ")", "Expected closing ')' in extern parameter list")
    return params, variadic

# struct field parsers
def parsefield(tokens):
    ftype = parsetype(tokens)
    return FieldNode(ftype, tokens.eat())

def parsefields(tokens):
    return parsebraced(tokens, "{", "}", "struct definition", parsefield)

# main parser
def parse(tokens):
    decls = []
    while tokens.can_eat():
        decls.append(parsestmt(tokens))
    return ProgramNode(decls)