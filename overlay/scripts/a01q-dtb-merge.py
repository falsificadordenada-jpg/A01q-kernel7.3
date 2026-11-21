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

Phandles: a pstore log from a real boot showed that our phandle numbers collide with the stock
ones (e.g. a "gpios" reference of ours resolved to /soc/ad-hoc-bus/fab-bimc, the timer lost its
interrupt parent, smem lost its memory-region). The script therefore renumbers every phandle of
our nodes (the reference cells are found from the binding schema, see find_refs()): a node that replaces/merges into a stock node takes that stock node's phandle (LK
needs it for the DTBO overlay targets, and the stock root `interrupt-parent` keeps working),
every other node of ours gets a fresh number above the stock ones. The stock root
`interrupt-parent` (the downstream MPM "wake-gic", no mainline driver) is replaced by ours (the GIC).

Pure python, no dependencies. Usage:
  a01q-dtb-merge.py STOCK_BOOT_DTB OURS_DTB OUT_DTB
"""
import os, struct, sys, copy

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
# Android's first-stage init looks for the boot device by the name on the kernel command line
# (androidboot.bootdevice=7824900.sdhci) and the stock fstab uses /dev/block/platform/soc/7824900.sdhci.
# Mainline calls the node mmc@..., which would make the device 7824900.mmc, so the nodes are renamed
# and then replace the (disabled) stock nodes of the same name.
RENAME = {'mmc@7824900': 'sdhci@7824900', 'mmc@7864900': 'sdhci@7864900'}
SOC_REPLACE += list(RENAME.values())
# Kept as in the stock DTB: 'i2c@78b6000' (LK needs it) and 'pinctrl@1000000' (merged below).
PINCTRL_PROPS = ('gpio-ranges', 'gpio-reserved-ranges', 'interrupts')
# The stock pinctrl node routes its summary IRQ through the MPM "wake-gic" (interrupts-extended and
# its own interrupt-parent). Mainline has no driver for that controller, so the pinctrl driver
# would defer forever and take every consumer (eMMC, I2C, keys, ...) with it. Use ours instead.
PINCTRL_DROP = ('interrupts-extended', 'interrupt-parent')

def find_stock(b):
    """The stock boot.img DTB file holds several DTBs; pick the sdm439 one (msm-id 353)."""
    off = 0
    while off < len(b) and b[off:off+4] == bytes.fromhex('d00dfeed'):
        r, total = load(b, off)
        mid = dict(r.props).get('qcom,msm-id', b'')
        if mid[:4] == struct.pack('>I', 353): return r
        off += total
    raise SystemExit('no msm-id 353 DTB found in the stock file')

def pname(n):
    return 'soc' if base(n) == 'soc' else n.name

def phandle_of(n):
    for k, v in n.props:
        if k in ('phandle', 'linux,phandle') and len(v) == 4:
            return struct.unpack('>I', v)[0]
    return None

def walk(n, path=()):
    p = path + (pname(n),)
    yield p, n
    for c in n.kids:
        yield from walk(c, p)

def plan(stock, ours):
    """Decide which of our nodes end up in the result. Returns (top_replace, soc_actions)."""
    s_soc, o_soc = kid(stock, 'soc'), kid(ours, 'soc')
    have = {c.name: c for c in s_soc.kids}
    top = [c for c in ours.kids if base(c) != 'soc']
    acts = []  # (our node, 'replace'|'merge'|'add'|'drop')
    for c in o_soc.kids:
        if c.name in SOC_REPLACE: acts.append((c, 'replace'))
        elif c.name == 'pinctrl@1000000': acts.append((c, 'merge'))
        elif c.name not in have: acts.append((c, 'add'))
        else: acts.append((c, 'drop'))
    return top, acts

def stock_counterpart(stock, path):
    """Stock node at the same (normalised) path, or None. path[0] is the root name."""
    n = stock
    for name in path[1:]:
        n = next((c for c in n.kids if pname(c) == name or base(c) == name), None)
        if n is None: return None
    return n

# --- phandle reference discovery -------------------------------------------------------------
# dtc only emits __local_fixups__ for /plugin/ files, so a normal DTB built with -@ carries no
# list of the cells that hold phandles. We rebuild that list from the binding schema of the
# properties our DTS uses ("phandle + N argument cells", where N comes from the provider node).
CELLS_PROP = {
    'clocks': '#clock-cells', 'assigned-clocks': '#clock-cells', 'assigned-clock-parents': '#clock-cells',
    'resets': '#reset-cells', 'power-domains': '#power-domain-cells', 'iommus': '#iommu-cells',
    'dmas': '#dma-cells', 'phys': '#phy-cells', 'hwlocks': '#hwlock-cells', 'mboxes': '#mbox-cells',
    'io-channels': '#io-channel-cells', 'interconnects': '#interconnect-cells', 'pwms': '#pwm-cells',
    'interrupts-extended': '#interrupt-cells', 'qcom,smem-states': '#qcom,smem-state-cells',
    'thermal-sensors': '#thermal-sensor-cells', 'gpios': '#gpio-cells', 'gpio': '#gpio-cells',
}
FIXED_CELLS = {'qcom,dload-mode': 1, 'cooling-device': 2, 'qcom,ipc': 2, 'qcom,ipc-1': 2, 'qcom,ipc-2': 2, 'gpio-ranges': 3,
               'qcom,rpm-msg-ram': 0}
SINGLE = {'interrupt-parent', 'memory-region', 'next-level-cache', 'phy-handle', 'remote-endpoint',
          'operating-points-v2', 'required-opps', 'cpu', 'sram', 'syscon', 'trip', 'port', 'sleep-state',
          'nvmem-cells', 'cpu-idle-states', 'domain-idle-states', 'msi-parent', 'qcom,rpm-msg-ram',
          'power-domain', 'qcom,smd-edge', 'vdd-supply', 'phandle-prop', 'nvmem', 'qcom,pbs', 'qcom,mmio'}
# properties that never hold phandles (used only to keep the audit below quiet)
NOT_PH = {'reg', 'ranges', 'dma-ranges', 'interrupts', 'compatible', 'status', 'phandle', 'linux,phandle',
          'device_type', 'model', 'label', 'bootargs', 'stdout-path', 'name', 'regulator-name',
          'gpio-reserved-ranges', 'interrupt-map', 'interrupt-map-mask', 'opp-hz', 'opp-microvolt',
          'opp-level', 'opp-peak-kBps', 'qcom,smem', 'qcom,local-pid', 'qcom,remote-pid', 'no-map',
          'size', 'alignment', 'alloc-ranges', 'qcom,client-id', 'cpu-release-addr', 'enable-method',
          'bits', 'drive-strength', 'cache-level', 'qcom,pmic-id', 'bias-disable', 'bias-pull-up',
          'bias-pull-down', 'output-high', 'output-low', 'qcom,ipc-id', 'qcom,smd-channels', 'qcom,remote-pid'}

def ref_kind(name):
    if name in CELLS_PROP: return ('cells', CELLS_PROP[name])
    if name.endswith('-gpios') or name.endswith('-gpio'): return ('cells', '#gpio-cells')
    if name in FIXED_CELLS: return ('fixed', FIXED_CELLS[name])
    if name in SINGLE or name.endswith('-supply') or (name.startswith('pinctrl-') and name[8:].isdigit()):
        return ('single', 0)
    return None

def find_refs(ours):
    """Return ({id(node): {prop: [cell offsets holding a phandle]}}, {phandle: node}, [audit lines])."""
    byph = {}
    for _, n in walk(ours):
        ph = phandle_of(n)
        if ph is not None: byph[ph] = n
    refs, audit = {}, []
    for path, n in walk(ours):
        for k, v in n.props:
            kind = ref_kind(k)
            if kind is None:
                if k not in NOT_PH and not k.endswith('-names') and not k.startswith('#') and len(v) % 4 == 0 and v:
                    cells = [struct.unpack('>I', v[i:i+4])[0] for i in range(0, len(v), 4)]
                    if any(c in byph for c in cells) and len(v) <= 64 and not (v.endswith(b'\0') and v[:1].isalpha()):
                        audit.append('/'.join(path) + ':' + k)
                continue
            if len(v) % 4: raise SystemExit('bad length of %s:%s' % ('/'.join(path), k))
            cells = [struct.unpack('>I', v[i:i+4])[0] for i in range(0, len(v), 4)]
            i, offs = 0, []
            while i < len(cells):
                h = cells[i]
                if kind[0] == 'single': argc = 0
                elif kind[0] == 'fixed': argc = kind[1]
                elif h == 0: argc = 0  # empty entry
                else:
                    if h not in byph:
                        raise SystemExit('%s:%s references phandle %d that is not in our DTB' % ('/'.join(path), k, h))
                    cp = dict(byph[h].props).get(kind[1])
                    if cp is None: raise SystemExit('%s:%s: provider %s has no %s' % ('/'.join(path), k, byph[h].name, kind[1]))
                    argc = struct.unpack('>I', cp)[0]
                if h != 0:
                    if h not in byph: raise SystemExit('%s:%s references unknown phandle %d' % ('/'.join(path), k, h))
                    offs.append(i * 4)
                i += 1 + argc
            if i != len(cells):
                raise SystemExit('%s:%s: cell count does not match the provider #cells' % ('/'.join(path), k))
            if offs: refs.setdefault(id(n), {})[k] = offs
    return refs, byph, audit

def remap_phandles(stock, ours, top, acts):
    refs, byph, audit = find_refs(ours)
    for a in (audit if os.environ.get('A01Q_AUDIT') else []):
        sys.stderr.write('audit: %s has numbers equal to a phandle (not treated as a reference)\n' % a)
    # stock phandle space
    used = [phandle_of(n) for _, n in walk(stock) if phandle_of(n)]
    nxt = [max(used) + 1]
    keep = {}  # id(node) -> stock counterpart or None, for every node of ours that survives
    def mark(n, path, counterpart_ok):
        keep[id(n)] = stock_counterpart(stock, path) if counterpart_ok else None
        for c in n.kids: mark(c, path + (pname(c),), counterpart_ok)
    root = (pname(stock),)
    for c in top: mark(c, root + (pname(c),), True)
    s_name = pname(kid(stock, 'soc'))
    for c, how in acts:
        if how != 'drop': mark(c, root + (s_name, pname(c)), how != 'add')
    mp, dead = {}, {}
    for ph, n in byph.items():
        if id(n) in keep:
            cp = keep[id(n)]
            sp = phandle_of(cp) if cp is not None else None
            if sp is not None: mp[ph] = sp
            else:
                mp[ph] = nxt[0]; nxt[0] += 1
        else:
            dead[ph] = 0xffff0000 + ph  # unresolvable on purpose, never collides with a real node
    mp.update(dead)
    def fix(n):
        offs = refs.get(id(n), {})
        new = []
        for k, v in n.props:
            if k in ('phandle', 'linux,phandle') and len(v) == 4:
                old = struct.unpack('>I', v)[0]; v = struct.pack('>I', mp.get(old, old))
            elif k in offs:
                b = bytearray(v)
                for off in offs[k]:
                    old = struct.unpack('>I', b[off:off+4])[0]
                    if old in dead and id(n) in keep:
                        sys.stderr.write('warning: %s:%s references a node that is not part of the result\n' % (n.name, k))
                    b[off:off+4] = struct.pack('>I', mp.get(old, old))
                v = bytes(b)
            new.append((k, v))
        n.props = new
        for c in n.kids: fix(c)
    fix(ours)
    ours.kids = [c for c in ours.kids if c.name not in ('__local_fixups__', '__symbols__', '__fixups__')]
    return len(mp) - len(dead), len(dead)

def first_compat(n):
    v = dict(n.props).get('compatible')
    return v.split(b'\0')[0] if v else None

def merge(stock, ours):
    o_soc = kid(ours, 'soc')
    for c in o_soc.kids:
        c.name = RENAME.get(c.name, c.name)
    top, acts = plan(stock, ours)
    n_map, n_dead = remap_phandles(stock, ours, top, acts)
    sys.stderr.write('phandles: %d remapped, %d dropped\n' % (n_map, n_dead))
    s_soc = kid(stock, 'soc')
    have = {c.name: c for c in s_soc.kids}
    # The stock root `interrupt-parent` is the downstream MPM "wake-gic", which mainline has no
    # driver for: top-level nodes without their own parent (the arch timer!) would get no IRQs.
    o_ip = next((v for k, v in ours.props if k == 'interrupt-parent'), None)
    if o_ip is not None:
        stock.props = [(k, o_ip if k == 'interrupt-parent' else v) for k, v in stock.props]
    for c in top:
        stock.kids = [x for x in stock.kids if base(x) != base(c)]
        stock.kids.append(copy.deepcopy(c))
    for c, how in acts:
        if how == 'replace':
            s_soc.kids = [x for x in s_soc.kids if x.name != c.name]
            s_soc.kids.append(copy.deepcopy(c))
        elif how == 'merge':
            sp = have['pinctrl@1000000']
            sp.props = [(k, v) for k, v in sp.props if k not in PINCTRL_DROP]
            names = {k for k, v in sp.props}
            for k, v in c.props:
                if k in PINCTRL_PROPS and k not in names: sp.props.append((k, v))
            kn = {x.name for x in sp.kids}
            sp.kids += [copy.deepcopy(x) for x in c.kids if x.name not in kn]
        elif how == 'add':
            s_soc.kids.append(copy.deepcopy(c))
        # 'drop': same name as a stock node and not listed -> the stock node wins
    # Stock (downstream) nodes that a mainline driver would also bind, and that duplicate a node of
    # ours (same compatible), are switched off: they steal the memory region / IRQ of our node or
    # fail to probe (smem without memory-region, SPMI, SMP2P, TSENS, the second arch timer).
    # They stay in the tree because the stock DTBO and the bootloader expect them.
    ours_compat = {first_compat(n) for _, n in walk(ours)} - {None, b'regulator-fixed'}
    installed = {c.name for c, how in acts if how in ('replace', 'add')}
    off = []
    for c in s_soc.kids:
        if c.name in installed or c.name == 'pinctrl@1000000': continue
        if first_compat(c) in ours_compat:
            c.props = [(k, v) for k, v in c.props if k != 'status'] + [('status', b'disabled\0')]
            off.append(c.name)
    sys.stderr.write('stock nodes disabled (duplicates of ours): %s\n' % ', '.join(off))
    return stock

if __name__ == '__main__':
    if len(sys.argv) != 4: raise SystemExit(__doc__)
    stock = find_stock(open(sys.argv[1], 'rb').read())
    ours, _ = load(open(sys.argv[2], 'rb').read())
    out = dump(merge(stock, ours))
    open(sys.argv[3], 'wb').write(out)
    print('wrote %s (%d bytes)' % (sys.argv[3], len(out)))
