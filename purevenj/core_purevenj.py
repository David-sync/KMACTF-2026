import struct, mmap, ctypes
from pathlib import Path

MASK = (1 << 64) - 1
MUL  = 0x2545F4914F6CDD1D

ADJ = {
    0:  [1, 2, 3, 4, 5],
    1:  [0, 5, 11, 7, 2],
    2:  [0, 1, 7, 8, 3],
    3:  [0, 2, 8, 9, 4],
    4:  [0, 3, 9, 10, 5],
    5:  [0, 4, 10, 11, 1],
    6:  [7, 8, 9, 10, 11],
    7:  [1, 2, 8, 6, 11],
    8:  [2, 3, 9, 6, 7],
    9:  [3, 4, 10, 6, 8],
    10: [4, 5, 11, 6, 9],
    11: [5, 1, 7, 6, 10],
}

def xs(x):
    x ^= x >> 12
    x ^= (x << 25) & MASK
    x ^= x >> 27
    return x & MASK

def rnd(x):
    return ((x * MUL) & MASK) >> 32

def stage_params(seed):
    x = xs(seed); node = rnd(x) % 12
    x = xs(x);    slot = rnd(x) % 5

    nodes = list(range(1, 12))
    x = xs(x);    t1 = nodes.pop(rnd(x) % 11)
    x = xs(x);    t2 = nodes[rnd(x) % 10]
    return x, node, slot, t1, t2

def put3(buf, off, tri):
    struct.pack_into("<QQQ", buf, off, *tri)

def get3(buf, off):
    return struct.unpack_from("<QQQ", buf, off)

def same_cell(a, b):
    if a[0] != b[0]:
        return False
    return a[1] == b[1] if a[0] == 0 else a[1:] == b[1:]

class GameCore:
    def __init__(self, chal_path):
        raw = Path(chal_path).read_bytes()
        size = ((len(raw) + 0xfff) // 0x1000) * 0x1000

        self.mem = mmap.mmap(-1, size, prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC)
        self.mem.write(raw)
        self.mem.write(b"\0" * (size - len(raw)))
        self.base = ctypes.addressof(ctypes.c_char.from_buffer(self.mem))

        libc = ctypes.CDLL(None)
        malloc  = ctypes.cast(libc.malloc,  ctypes.c_void_p).value
        free    = ctypes.cast(libc.free,    ctypes.c_void_p).value
        memmove = ctypes.cast(libc.memmove, ctypes.c_void_p).value
        self.free = ctypes.CFUNCTYPE(None, ctypes.c_void_p)(free)

        for off in range(0x67450, min(size, 0x69b00), 8):
            val = struct.unpack_from("<Q", self.mem, off)[0]
            if 0x3000 <= val < len(raw):
                struct.pack_into("<Q", self.mem, off, self.base + val)

        for off, val in [(0x68f08, malloc), (0x68ff8, free), (0x68ea8, memmove)]:
            struct.pack_into("<Q", self.mem, off, val)

        self.F8    = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint64, ctypes.c_uint64)(self.base + 0x8dd0)
        self.Fmove = ctypes.CFUNCTYPE(ctypes.c_ubyte, ctypes.c_void_p, ctypes.c_uint32)(self.base + 0x92e0)
        self.Fupd  = ctypes.CFUNCTYPE(None, ctypes.c_void_p)(self.base + 0x9200)
        self.Facts = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(self.base + 0x87f0)
        self.Fmem  = ctypes.CFUNCTYPE(ctypes.c_ubyte, ctypes.c_void_p, ctypes.c_void_p)(self.base + 0x9080)

    def init_stage(self, stage, node, slot, t1, t2):
        st = bytearray(0xc8)

        put3(st, 0x00, (0, 0, 0))   # current object
        put3(st, 0x18, (0, 0, 0))   # return C(0)
        put3(st, 0x30, (0, t1, 0))  # target 1
        put3(st, 0x48, (0, t2, 0))  # target 2

        if stage == 0:
            self.F8((ctypes.c_char * 72).from_buffer(st, 0x60), node, slot)
        else:
            nb = ADJ[node][slot]
            rev = ADJ[nb].index(node)
            tag = 3 if stage == 1 else 4
            for i, x in enumerate([tag, 1, node, slot, 1, nb, rev, 0, 0]):
                struct.pack_into("<Q", st, 0x60 + 8 * i, x)

        struct.pack_into("<Q", st, 0xa8, 0)              # focus
        struct.pack_into("<Q", st, 0xb0, 0)              # moves
        struct.pack_into("<Q", st, 0xb8, 13 + 3 * stage) # max moves
        return st

    def cur(self, st):      return get3(st, 0x00)
    def ret(self, st):      return get3(st, 0x18)
    def t1(self, st):       return get3(st, 0x30)
    def t2(self, st):       return get3(st, 0x48)
    def focus(self, st):    return struct.unpack_from("<Q", st, 0xa8)[0]
    def moves(self, st):    return struct.unpack_from("<Q", st, 0xb0)[0]
    def maxmoves(self, st): return struct.unpack_from("<Q", st, 0xb8)[0]
    def flags(self, st):    return tuple(st[0xc0:0xc3])

    def name(self, tri):
        typ, v, idx = tri
        if typ == 0:
            return f"C({v})"
        if typ == 1:
            return f"E({v},{idx})"
        if typ == 2:
            return f"K({v},{idx})"
        return str(tri)

    def cell_name(self, tri):
        return self.name(tri)

    def is_member(self, st, tri):
        tmp = bytearray(24)
        put3(tmp, 0, tri)
        return bool(self.Fmem((ctypes.c_char * 72).from_buffer(st, 0x60),
                              (ctypes.c_char * 24).from_buffer(tmp)))

    def update(self, st):
        self.Fupd((ctypes.c_char * len(st)).from_buffer(st))

    def rotate(self, st, mode):
        return bool(self.Fmove((ctypes.c_char * len(st)).from_buffer(st), mode))

    def actions(self, st):
        out = bytearray(24)
        self.Facts((ctypes.c_char * 24).from_buffer(out),
                   (ctypes.c_char * 72).from_buffer(st, 0x60),
                   (ctypes.c_char * 24).from_buffer(st, 0x00))

        cap, ptr, length = struct.unpack_from("<QQQ", out)
        res = [struct.unpack("<QQQ", ctypes.string_at(ptr + 24 * i, 24)) for i in range(length)]

        if cap and ptr:
            self.free(ptr)
        return res

    def legal(self, st):
        out = []

        for v in range(12):
            if v != self.focus(st):
                ns = bytearray(st)
                struct.pack_into("<Q", ns, 0xa8, v)
                out.append((f"f{v}", ns))

        for cmd, mode in [("rrw", 0), ("rw", 1)]:
            ns = bytearray(st)
            if self.rotate(ns, mode):
                out.append((cmd, ns))

        typ, v, idx = self.cur(st)
        if typ == 0:
            cand = [(1, v, i) for i in range(5)] + [(2, v, i) for i in range(5)]
        elif typ == 1:
            cand = [(0, v, 0), (2, v, idx), (2, v, (idx + 1) % 5)]
        elif typ == 2:
            cand = [(0, v, 0), (1, v, (idx + 4) % 5), (1, v, idx)]
        else:
            cand = []

        targets = [self.ret(st), self.t1(st), self.t2(st)]
        for tri in dict.fromkeys(cand):
            if self.is_member(st, tri) or any(same_cell(tri, t) for t in targets):
                ns = bytearray(st)
                put3(ns, 0, tri)
                self.update(ns)
                if tri[0] == 0:
                    cmd = "w c"
                elif tri[0] == 1:
                    cmd = f"w e {tri[2]}"
                else:
                    cmd = f"w k {tri[2]}"
                out.append((cmd, ns))

        if self.is_member(st, self.cur(st)):
            entries = self.actions(st)
            if entries:
                ns = bytearray(st)
                put3(ns, 0, entries[0])
                self.update(ns)
                out.append(("c", ns))

                seen = set()
                for tri in entries:
                    node = tri[1]
                    if node not in seen:
                        seen.add(node)
                        ns = bytearray(st)
                        put3(ns, 0, tri)
                        self.update(ns)
                        out.append((f"c {node}", ns))

        return out
