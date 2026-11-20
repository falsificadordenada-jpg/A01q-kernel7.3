#!/usr/bin/env python3
"""Build the DTB that the stock Samsung A01 bootloader (LK) accepts.

LK drops the phone into Download mode when the DTB does not look like a stock
one. Bisecting on a real A01 (A015M, firmware A015MUBS5CWI3) showed that the
bootloader needs the STOCK versions of a few nodes. Everything else can be ours.

Result = stock DTB (the sdm439 / msm-id 353 one)
         + every top-level node of our DTB replaces the stock node with the same
           base name (cpus, chosen, aliases, memory, reserved-memory, ...), or is added
         + inside /soc: the nodes listed in SOC_REPLACE are replaced by ours,
           i2c@78b6000 stays stock (LK needs it enabled with its wsa881x children),
           pinctrl@1000000 stays stock but gets our properties and our pin states added,
           every other node of our /soc is added.

Pure python, no dependencies. Usage:
  a01q-dtb-merge.py STOCK_BOOT_DTB OURS_DTB OUT_DTB
"""
import struct, sys, copy

class N:
    def __init__(s, name):
        s.name = name; s.props = []; s.kids = []

def load(b, off=0):
    magic, total, offs, offstr, offr, ver, lc, bc, szs, szt = struct.unpack('>10I', b[off:off+40])
    if magic != 0xd00dfeed:
        raise SystemExit('not a DTB at offset %d' % off)
    st = b[off+offs:off+offs+szt]; strs = b[off+offstr:off+offstr+szs]
    i = 0; stack = []; root = None
    while i < len(st):
        t = struct.unpack('>I', st[i:i+4])[0]; i += 4
        if t == 1:
            e = st.index(b'\0', i); n = N(st[i:e].decode()); i = (e + 4) & ~3
            if stack: stack[-1].kids.append(n)
            else: root = n
            stack.append(n)
        elif t == 2: stack.pop()
        elif t == 3:
            ln, no = struct.unpack('>II', st[i:i+8]); i += 8
            v = st[i:i+ln]; i = (i + ln + 3) & ~3
            stack[-1].props.append((strs[no:strs.index(b'\0', no)].decode(), v))
        elif t == 9: break
    return root, total

def dump(root):
    strs = bytearray(); idx = {}
    def sid(n):
        if n not in idx:
            idx[n] = len(strs); strs.extend(n.encode() + b'\0')
        return idx[n]
    out = bytearray()
    pad = lambda x: x + b'\0' * ((-len(x)) % 4)
    def w(n):
        out.extend(struct.pack('>I', 1) + pad(n.name.encode() + b'\0'))
        for k, v in n.props: out.extend(struct.pack('>III', 3, len(v), sid(k)) + pad(v))
        for c in n.kids: w(c)
        out.extend(struct.pack('>I', 2))
    w(root); out.extend(struct.pack('>I', 9))
    rs = b'\0' * 16; o_st = 40 + 16; o_str = o_st + len(out); tot = o_str + len(strs)
    return struct.pack('>10I', 0xd00dfeed, tot, o_st, o_str, 40, 17, 16, 0, len(strs), len(out)) + rs + bytes(out) + bytes(strs)

base = lambda n: n.name.split('@')[0]
def kid(n, name):
    for c in n.kids:
        if c.name == name or base(c) == name: return c

# Nodes of /soc that LK is happy to see in our version (bisected on hardware).
SOC_REPLACE = ['interrupt-controller@b000000', 'timer@b120000', 'restart@4ab000',
               'serial@78b0000', 'usb@78db000', 'i2c@78b7000', 'i2c@78b8000',
               'i2c@7af5000', 'spi@78b7000', 'spi@7af6000']
# Kept as in the stock DTB: 'i2c@78b6000' (LK needs it) and 'pinctrl@1000000' (merged below).
PINCTRL_PROPS = ('gpio-ranges', 'gpio-reserved-ranges', 'interrupts')

def find_stock(b):
    """The stock boot.img DTB file holds several DTBs; pick the sdm439 one (msm-id 353)."""
    off = 0
    while off < len(b) and b[off:off+4] == bytes.fromhex('d00dfeed'):
        r, total = load(b, off)
        mid = dict(r.props).get('qcom,msm-id', b'')
        if mid[:4] == struct.pack('>I', 353): return r
        off += total
    raise SystemExit('no msm-id 353 DTB found in the stock file')

def merge(stock, ours):
    s_soc, o_soc = kid(stock, 'soc'), kid(ours, 'soc')
    for c in ours.kids:
        if base(c) == 'soc': continue
        stock.kids = [x for x in stock.kids if base(x) != base(c)]
        stock.kids.append(copy.deepcopy(c))
    have = {c.name: c for c in s_soc.kids}
    for c in o_soc.kids:
        if c.name in SOC_REPLACE:
            s_soc.kids = [x for x in s_soc.kids if x.name != c.name]
            s_soc.kids.append(copy.deepcopy(c))
        elif c.name == 'pinctrl@1000000':
            sp = have['pinctrl@1000000']; names = {k for k, v in sp.props}
            for k, v in c.props:
                if k in PINCTRL_PROPS and k not in names: sp.props.append((k, v))
            kn = {x.name for x in sp.kids}
            sp.kids += [copy.deepcopy(x) for x in c.kids if x.name not in kn]
        elif c.name not in have:
            s_soc.kids.append(copy.deepcopy(c))
        # same name as a stock node and not listed: the stock node wins
    return stock

if __name__ == '__main__':
    if len(sys.argv) != 4: raise SystemExit(__doc__)
    stock = find_stock(open(sys.argv[1], 'rb').read())
    ours, _ = load(open(sys.argv[2], 'rb').read())
    out = dump(merge(stock, ours))
    open(sys.argv[3], 'wb').write(out)
    print('wrote %s (%d bytes)' % (sys.argv[3], len(out)))
