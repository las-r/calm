# CALM
A minimal, C-like compiled programming language.

## Overview
CALM is a small, statically-typed, compiled language with C-like syntax. It compiles to LLVM IR, which is then handed to `clang` to produce a native executable.

## Getting Started
### Installation
```sh
pip install git+https://github.com/las-r/calm
```

### Usage
```sh
calm run yourfile.cal            # builds exe, runs it, then deletes after
calm build yourfile.cal          # builds exe
calm build yourfile.cal --llvmir # builds exe and saves .ll file
```

## Syntax & Basics
### Comments
```
// This is a comment!
```
Only single-line comments are supported; there is no block-comment syntax.

### Statements & Blocks
Statements are not separated by semicolons or newlines, whitespace is insignificant. Blocks are always delimited with `{` and `}`.

### Variables
Variable declarations require an explicit type, and are optionally initialized:
```
i32 x = 10
f64 pi = 3.14
[i32] nums = ...
```
Reassigning an existing variable omits the type:
```
x = 20
```
Assignment targets can be a plain name, a field (`P.field = X`), an index (`P:0 = X`), or a dereferenced pointer (`#P = X`).

### Operators
* **Arithmetic:** `+`, `-`, `*`, `/`, `%`
* **Bitwise:** `~` (not), `&` (and), `|` (or), `^` (xor), `<<` (shift left), `>>` (shift right)
* **Comparison:** `==`, `!=`, `<`, `<=`, `>`, `>=`
* **Logical:** `!` (not), `&&` (and), `||` (or)

There is no operator precedence; parentheses are the only way to control grouping. `2 + 3 * 4` evaluates to `20`, while `2 + (3 * 4)` evaluates to `14`.

## Control Flow
### Conditionals
```
if x > 0 {
    printf("positive\n")
} else if x == 0 {
    printf("zero\n")
} else {
    printf("negative\n")
}
```
`else if` and `else` are both optional, and any number of `else if` branches may chain.

### Loops
```
i32 i = 0
while i < 5 {
    printf("%d\n", i)
    i = i + 1
}
```
`break` exits the innermost enclosing loop.

## Functions
A function declaration gives its return type first, then its name, then a typed parameter list:
```
def i32 add(i32 a, i32 b) {
    return a + b
}
```

`return` with no following expression returns nothing (only valid when the function's return type is `void`). A `return` is considered bare if the next token is `}` or `else`.

### External (C) Functions
Functions implemented outside CALM are declared with `extc def`, using only a type/name signature. A trailing `...` marks the function as variadic (e.g. for `printf`):
```
extc def i32 printf(i32 f, ...)
```

## Types
| Category | Types |
|---|---|
| Signed integers | `i8`, `i16`, `i32`, `i64` |
| Unsigned integers | `u8`, `u16`, `u32`, `u64` |
| Floats | `f32`, `f64` |
| Slices | `[<t>]`, e.g. `[i32]`, `[[i32]]` |
| Misc. | `void` (only valid as a function's return type) |

There is no dedicated boolean type. Comparisons and logic operators produce an integer (`0` for false, nonzero for true), and `if`/`while` treat any nonzero value as true.

### Chars
Chars can be defined with ``u8 V = `C`` and have type `u8`, e.g.:
```
u8 i = `A
```

### Strings
Strings can be defined with `[u8] V = "..."` and have type `[u8]`, e.g.:
```
[u8] msg = "Hello, world!"
```

### Arrays
Arrays can be defined with `<t>[L] = B`, e.g.:
```
i32[4] nums = {0, 1, 2, 3}
```

### Structs
A struct is declared with typed fields, and constructed with a brace literal listing values positionally, in field-declaration order:
```
struct Point {
    i32 x,
    i32 y
}

Point p = Point{1, 2}
Point p = {3, 4}  // the struct name is optional in declarations if the variable is typed as it
```

Fields are accessed with `.`:
```
p.x = 5
```

## Pointers
* `@X` - address-of: takes a pointer to `X`.
* `#P` - dereference: reads (or, as an assignment target, writes) the value `P` points to.

```
i32 x = 10
[i32] p = @x
#p = 20   // x is now 20
```

## Slices & Indexing
Slices are written `[<t>]` and indexed with `:`:
```
[i32] nums = ...
i32 first = nums:0
nums:1 = 99
```

## Module Imports
CALM has two forms of `;use`, both of which must appear at the very top of the file, before any other code:
```
;use "utils.cal"   // import a local file, path relative to the importing file
;use io            // import a stdlib module (looked up in CALM's std/ directory)
```

Imported code is textually resolved into the importing file before compilation and shares scope with it. A file is only ever imported once.

## Grammar Reference
```
;use "F"                              import local file
;use X                                import stdlib module
<t> V = X                             variable declaration
V = X                                 assignment
P.F = X                               field assignment
A:I = X                               index assignment
#P = X                                pointer-write
if X {...} else if Y {...} else {...} conditional (else/else-if optional)
while X {...}                         loop
break                                 exit innermost loop
def <t> X(<t> Y, ...) {...}           function definition
return X                              return from function (X optional)
struct X {<t> Y, ...}                 struct definition
T{X, Y, ...}                          struct literal
extc def <t> X(<t> Y, ..., ...)       external function declaration (variadic optional)
// COMMENT                            comment

A:I        index into slice A
P.F        access field F of P
@X         address-of X
#P         dereference pointer P
```

## Hello World
```
;use io

def i32 main() {
    printf("Hello, world!\n")

    return 0
}
```