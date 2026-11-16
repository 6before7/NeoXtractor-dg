"""Python 2.7 instruction set facts shared by the opcode recovery and the rewriter."""

# CPython 2.7 opcode numbers (opcode.opmap of 2.7.18)
CPY27 = {"BINARY_ADD": 23, "BINARY_AND": 64, "BINARY_DIVIDE": 21, "BINARY_FLOOR_DIVIDE": 26, "BINARY_LSHIFT": 62, "BINARY_MODULO": 22, "BINARY_MULTIPLY": 20, "BINARY_OR": 66, "BINARY_POWER": 19, "BINARY_RSHIFT": 63, "BINARY_SUBSCR": 25, "BINARY_SUBTRACT": 24, "BINARY_TRUE_DIVIDE": 27, "BINARY_XOR": 65, "BREAK_LOOP": 80, "BUILD_CLASS": 89, "BUILD_LIST": 103, "BUILD_MAP": 105, "BUILD_SET": 104, "BUILD_SLICE": 133, "BUILD_TUPLE": 102, "CALL_FUNCTION": 131, "CALL_FUNCTION_KW": 141, "CALL_FUNCTION_VAR": 140, "CALL_FUNCTION_VAR_KW": 142, "COMPARE_OP": 107, "CONTINUE_LOOP": 119, "DELETE_ATTR": 96, "DELETE_FAST": 126, "DELETE_GLOBAL": 98, "DELETE_NAME": 91, "DELETE_SLICE+0": 50, "DELETE_SLICE+1": 51, "DELETE_SLICE+2": 52, "DELETE_SLICE+3": 53, "DELETE_SUBSCR": 61, "DUP_TOP": 4, "DUP_TOPX": 99, "END_FINALLY": 88, "EXEC_STMT": 85, "EXTENDED_ARG": 145, "FOR_ITER": 93, "GET_ITER": 68, "IMPORT_FROM": 109, "IMPORT_NAME": 108, "IMPORT_STAR": 84, "INPLACE_ADD": 55, "INPLACE_AND": 77, "INPLACE_DIVIDE": 58, "INPLACE_FLOOR_DIVIDE": 28, "INPLACE_LSHIFT": 75, "INPLACE_MODULO": 59, "INPLACE_MULTIPLY": 57, "INPLACE_OR": 79, "INPLACE_POWER": 67, "INPLACE_RSHIFT": 76, "INPLACE_SUBTRACT": 56, "INPLACE_TRUE_DIVIDE": 29, "INPLACE_XOR": 78, "JUMP_ABSOLUTE": 113, "JUMP_FORWARD": 110, "JUMP_IF_FALSE_OR_POP": 111, "JUMP_IF_TRUE_OR_POP": 112, "LIST_APPEND": 94, "LOAD_ATTR": 106, "LOAD_CLOSURE": 135, "LOAD_CONST": 100, "LOAD_DEREF": 136, "LOAD_FAST": 124, "LOAD_GLOBAL": 116, "LOAD_LOCALS": 82, "LOAD_NAME": 101, "MAKE_CLOSURE": 134, "MAKE_FUNCTION": 132, "MAP_ADD": 147, "NOP": 9, "POP_BLOCK": 87, "POP_JUMP_IF_FALSE": 114, "POP_JUMP_IF_TRUE": 115, "POP_TOP": 1, "PRINT_EXPR": 70, "PRINT_ITEM": 71, "PRINT_ITEM_TO": 73, "PRINT_NEWLINE": 72, "PRINT_NEWLINE_TO": 74, "RAISE_VARARGS": 130, "RETURN_VALUE": 83, "ROT_FOUR": 5, "ROT_THREE": 3, "ROT_TWO": 2, "SETUP_EXCEPT": 121, "SETUP_FINALLY": 122, "SETUP_LOOP": 120, "SETUP_WITH": 143, "SET_ADD": 146, "SLICE+0": 30, "SLICE+1": 31, "SLICE+2": 32, "SLICE+3": 33, "STOP_CODE": 0, "STORE_ATTR": 95, "STORE_DEREF": 137, "STORE_FAST": 125, "STORE_GLOBAL": 97, "STORE_MAP": 54, "STORE_NAME": 90, "STORE_SLICE+0": 40, "STORE_SLICE+1": 41, "STORE_SLICE+2": 42, "STORE_SLICE+3": 43, "STORE_SUBSCR": 60, "UNARY_CONVERT": 13, "UNARY_INVERT": 15, "UNARY_NEGATIVE": 11, "UNARY_NOT": 12, "UNARY_POSITIVE": 10, "UNPACK_SEQUENCE": 92, "WITH_CLEANUP": 81, "YIELD_VALUE": 86}
NAME = {v: k for k, v in CPY27.items()}
HAVE_ARGUMENT = 90
# jump instructions: relative targets are counted from the next instruction, absolute ones from the code start
JREL = {'FOR_ITER', 'JUMP_FORWARD', 'SETUP_LOOP', 'SETUP_EXCEPT', 'SETUP_FINALLY', 'SETUP_WITH'}
JABS = {'JUMP_IF_FALSE_OR_POP', 'JUMP_IF_TRUE_OR_POP', 'JUMP_ABSOLUTE', 'POP_JUMP_IF_FALSE', 'POP_JUMP_IF_TRUE',
        'CONTINUE_LOOP'}


def instructions(code):
    """(offset, opcode, arg or None). The NeoX permutation keeps Python 2's rule: opcodes >= 90 take a 2-byte arg."""
    i = 0
    while i < len(code):
        op = code[i]
        if op >= HAVE_ARGUMENT:
            yield i, op, code[i + 1] | code[i + 2] << 8
            i += 3
        else:
            yield i, op, None
            i += 1


class Fused(tuple):
    """One opcode standing for two instructions: (first, second, 'hi/lo' or 'lo/hi' packing of the two arguments)."""
    def __new__(cls, a, b, split):
        return tuple.__new__(cls, (a, b, split))

    def expand(self, arg):
        hi, lo = arg >> 8, arg & 0xff
        return [(self[0], hi), (self[1], lo)] if self[2] == 'hi/lo' else [(self[0], lo), (self[1], hi)]


class Paired(tuple):
    """An opcode that appears twice in a row and stands for two different instructions: (first, second)."""
    def __new__(cls, a, b):
        return tuple.__new__(cls, (a, b))
