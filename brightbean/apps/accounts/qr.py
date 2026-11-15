# JR Content: přidáno 2026-11-14 (blok P2-B17), AGPL-3.0.
"""Minimal QR code encoder (ISO/IEC 18004, byte mode) rendering to SVG.

The image ships no QR library and the block may not install one, so the
two-factor setup page draws its QR code with this module. Only what an
``otpauth://`` URI needs is implemented: byte mode, versions 1-40, the four
error-correction levels and automatic mask selection. The structure follows
the public reference algorithm (function patterns, Reed-Solomon over GF(256)
with polynomial 0x11D, block interleaving, BCH-coded format/version bits).

The output was cross-checked module by module against an independent encoder
(Kazuhiko Arase's QRCode.js bundled with npm) for every version and level;
see the P2-B17 block notes.
"""

from __future__ import annotations

from html import escape

# Index = version (0 unused). Rows: L, M, Q, H.
# fmt: off
_ECC_CODEWORDS_PER_BLOCK = {
    "L": (-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    "M": (-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28),
    "Q": (-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    "H": (-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
}
_NUM_ERROR_CORRECTION_BLOCKS = {
    "L": (-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25),
    "M": (-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49),
    "Q": (-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68),
    "H": (-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81),
}
# fmt: on
# Two format bits per level as the standard encodes them.
_FORMAT_BITS = {"L": 1, "M": 0, "Q": 3, "H": 2}

_MASKS = (
    lambda x, y: (x + y) % 2,
    lambda x, y: y % 2,
    lambda x, y: x % 3,
    lambda x, y: (x + y) % 3,
    lambda x, y: (x // 3 + y // 2) % 2,
    lambda x, y: x * y % 2 + x * y % 3,
    lambda x, y: (x * y % 2 + x * y % 3) % 2,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2,
)


class QrError(ValueError):
    pass


def _num_raw_data_modules(version: int) -> int:
    result = (16 * version + 128) * version + 64
    if version >= 2:
        numalign = version // 7 + 2
        result -= (25 * numalign - 10) * numalign - 55
        if version >= 7:
            result -= 36
    return result


def _num_data_codewords(version: int, ecl: str) -> int:
    return (
        _num_raw_data_modules(version) // 8
        - _ECC_CODEWORDS_PER_BLOCK[ecl][version] * _NUM_ERROR_CORRECTION_BLOCKS[ecl][version]
    )


def _gf_multiply(x: int, y: int) -> int:
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree: int) -> list[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_multiply(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_multiply(root, 0x02)
    return result


def _rs_remainder(data: list[int], divisor: list[int]) -> list[int]:
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_multiply(coef, factor)
    return result


def _encode_data(payload: bytes, version: int, ecl: str) -> list[int] | None:
    """Byte-mode bit stream padded to the version's capacity, or None if it does not fit."""
    count_bits = 8 if version <= 9 else 16
    if len(payload) >= 1 << count_bits:
        return None
    bits: list[int] = []

    def put(value: int, length: int) -> None:
        bits.extend((value >> i) & 1 for i in reversed(range(length)))

    put(0b0100, 4)
    put(len(payload), count_bits)
    for byte in payload:
        put(byte, 8)
    capacity = _num_data_codewords(version, ecl) * 8
    if len(bits) > capacity:
        return None
    put(0, min(4, capacity - len(bits)))
    put(0, (-len(bits)) % 8)
    pad = 0xEC
    while len(bits) < capacity:
        put(pad, 8)
        pad ^= 0xEC ^ 0x11
    return [int("".join(map(str, bits[i : i + 8])), 2) for i in range(0, len(bits), 8)]


class QrCode:
    def __init__(self, payload: bytes, ecl: str = "M", version: int | None = None, mask: int | None = None):
        if ecl not in _FORMAT_BITS:
            raise QrError("Unknown error-correction level.")
        versions = [version] if version else range(1, 41)
        for ver in versions:
            data = _encode_data(payload, ver, ecl)
            if data is not None:
                break
        else:
            raise QrError("Data too long for a QR code.")
        self.version = ver
        self.ecl = ecl
        self.size = ver * 4 + 17
        self.modules = [[False] * self.size for _ in range(self.size)]
        self._function = [[False] * self.size for _ in range(self.size)]
        self._draw_function_patterns()
        self._draw_codewords(self._add_ecc_and_interleave(data))
        if mask is None:
            best = None
            for candidate in range(8):
                self._apply_mask(candidate)
                self._draw_format_bits(candidate)
                penalty = self._penalty()
                if best is None or penalty < best[0]:
                    best = (penalty, candidate)
                self._apply_mask(candidate)  # XOR again undoes the mask
            mask = best[1]
        self.mask = mask
        self._apply_mask(mask)
        self._draw_format_bits(mask)
        del self._function

    # -- function patterns ---------------------------------------------------

    def _set_function(self, x: int, y: int, dark: bool) -> None:
        self.modules[y][x] = dark
        self._function[y][x] = True

    def _draw_function_patterns(self) -> None:
        size = self.size
        for i in range(size):
            self._set_function(6, i, i % 2 == 0)
            self._set_function(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < size and 0 <= y < size:
                        self._set_function(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        positions = self._alignment_positions()
        last = len(positions) - 1
        for i, ay in enumerate(positions):
            for j, ax in enumerate(positions):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self._set_function(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
        self._draw_format_bits(0)  # reserve the area; real bits come after masking
        self._draw_version()

    def _alignment_positions(self) -> list[int]:
        ver = self.version
        if ver == 1:
            return []
        numalign = ver // 7 + 2
        step = (ver * 8 + numalign * 3 + 5) // (numalign * 4 - 4) * 2
        result = [self.size - 7 - i * step for i in range(numalign - 1)] + [6]
        return list(reversed(result))

    def _draw_format_bits(self, mask: int) -> None:
        data = _FORMAT_BITS[self.ecl] << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        size = self.size

        def bit(i: int) -> bool:
            return (bits >> i) & 1 != 0

        for i in range(6):
            self._set_function(8, i, bit(i))
        self._set_function(8, 7, bit(6))
        self._set_function(8, 8, bit(7))
        self._set_function(7, 8, bit(8))
        for i in range(9, 15):
            self._set_function(14 - i, 8, bit(i))
        for i in range(8):
            self._set_function(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self._set_function(8, size - 15 + i, bit(i))
        self._set_function(8, size - 8, True)

    def _draw_version(self) -> None:
        if self.version < 7:
            return
        rem = self.version
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = self.version << 12 | rem
        for i in range(18):
            dark = (bits >> i) & 1 != 0
            a = self.size - 11 + i % 3
            b = i // 3
            self._set_function(a, b, dark)
            self._set_function(b, a, dark)

    # -- data ----------------------------------------------------------------

    def _add_ecc_and_interleave(self, data: list[int]) -> list[int]:
        ver, ecl = self.version, self.ecl
        numblocks = _NUM_ERROR_CORRECTION_BLOCKS[ecl][ver]
        blockecclen = _ECC_CODEWORDS_PER_BLOCK[ecl][ver]
        rawcodewords = _num_raw_data_modules(ver) // 8
        numshortblocks = numblocks - rawcodewords % numblocks
        shortblocklen = rawcodewords // numblocks
        divisor = _rs_divisor(blockecclen)
        blocks = []
        k = 0
        for i in range(numblocks):
            length = shortblocklen - blockecclen + (0 if i < numshortblocks else 1)
            block = data[k : k + length]
            k += length
            ecc = _rs_remainder(block, divisor)
            if i < numshortblocks:
                block = block + [0]
            blocks.append(block + ecc)
        result = []
        for i in range(len(blocks[0])):
            for j, block in enumerate(blocks):
                if i != shortblocklen - blockecclen or j >= numshortblocks:
                    result.append(block[i])
        return result

    def _draw_codewords(self, data: list[int]) -> None:
        size = self.size
        i = 0
        total = len(data) * 8
        right = size - 1
        while right >= 1:
            if right == 6:
                right = 5
            upward = ((right + 1) & 2) == 0
            for vert in range(size):
                y = size - 1 - vert if upward else vert
                for j in range(2):
                    x = right - j
                    if not self._function[y][x] and i < total:
                        self.modules[y][x] = (data[i >> 3] >> (7 - (i & 7))) & 1 != 0
                        i += 1
            right -= 2

    def _apply_mask(self, mask: int) -> None:
        masker = _MASKS[mask]
        for y in range(self.size):
            row, function = self.modules[y], self._function[y]
            for x in range(self.size):
                if not function[x] and masker(x, y) == 0:
                    row[x] = not row[x]

    def _penalty(self) -> int:
        size, modules = self.size, self.modules
        score = 0
        lines = modules + [[modules[y][x] for y in range(size)] for x in range(size)]
        finder = (True, False, True, True, True, False, True)
        light4 = (False,) * 4
        for line in lines:
            run, prev = 0, None
            for cell in line:
                if cell == prev:
                    run += 1
                else:
                    if run >= 5:
                        score += run - 2
                    run, prev = 1, cell
            if run >= 5:
                score += run - 2
            padded = list(light4) + list(line) + list(light4)
            for k in range(len(padded) - 10):
                window = tuple(padded[k : k + 11])
                if window == finder + light4 or window == light4 + finder:
                    score += 40
        for y in range(size - 1):
            for x in range(size - 1):
                c = modules[y][x]
                if c == modules[y][x + 1] == modules[y + 1][x] == modules[y + 1][x + 1]:
                    score += 3
        dark = sum(sum(row) for row in modules)
        total = size * size
        k = (abs(dark * 20 - total * 10) + total - 1) // total - 1
        score += max(k, 0) * 10
        return score

    # -- output --------------------------------------------------------------

    def to_svg(self, border: int = 4, title: str = "") -> str:
        """Scalable SVG; dark modules merged into horizontal runs."""
        parts = []
        for y, row in enumerate(self.modules):
            x = 0
            while x < self.size:
                if row[x]:
                    start = x
                    while x < self.size and row[x]:
                        x += 1
                    parts.append(f"M{start + border},{y + border}h{x - start}v1h-{x - start}z")
                else:
                    x += 1
        dim = self.size + border * 2
        aria = f' aria-label="{escape(title)}"' if title else ""
        label = f"<title>{escape(title)}</title>" if title else ""
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {dim} {dim}" '
            f'shape-rendering="crispEdges" role="img"{aria}>'
            f"{label}"
            f'<rect width="{dim}" height="{dim}" fill="#fff"/>'
            f'<path d="{"".join(parts)}" fill="#000"/></svg>'
        )


def qr_svg(text: str, title: str = "") -> str:
    return QrCode(text.encode("utf-8"), ecl="M").to_svg(title=title)
