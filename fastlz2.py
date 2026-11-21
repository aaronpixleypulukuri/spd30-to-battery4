"""Pure-Python FastLZ codec (level 2 compressor, level 1/2 decompressor).

A faithful port of Ariya Hidayat's reference fastlz.c (v0.5.0), so no native
extension is needed.  `compress()` always emits a level-2 stream, which is what
Native Instruments' NISound container uses for the Battery 4 preset payload.
"""

MAX_COPY = 32
MAX_L2_DISTANCE = 8191
MAX_FARDISTANCE = 65535 + MAX_L2_DISTANCE - 1
HASH_LOG = 14
HASH_MASK = (1 << HASH_LOG) - 1


class FastLZError(ValueError):
    pass


def decompress(data, expected_size=None):
    """Decompress a FastLZ level 1 or level 2 stream."""
    src = memoryview(bytes(data))
    n = len(src)
    if n == 0:
        return b""
    level = (src[0] >> 5) + 1
    if level not in (1, 2):
        raise FastLZError("unknown FastLZ level %d" % level)
    out = bytearray()
    ip = 1
    ctrl = src[0] & 31
    try:
        while True:
            if ctrl >= 32:
                length = (ctrl >> 5) - 1
                ofs = (ctrl & 31) << 8
                if length == 6:
                    if level == 1:
                        length += src[ip]
                        ip += 1
                    else:
                        while True:
                            code = src[ip]
                            ip += 1
                            length += code
                            if code != 255:
                                break
                code = src[ip]
                ip += 1
                length += 3
                dist = ofs + code + 1
                if level == 2 and code == 255 and ofs == (31 << 8):
                    dist = ((src[ip] << 8) | src[ip + 1]) + MAX_L2_DISTANCE + 1
                    ip += 2
                ref = len(out) - dist
                if ref < 0:
                    raise FastLZError("back-reference before start of output")
                if dist >= length:
                    out += out[ref:ref + length]
                else:  # overlapping run: repeat the `dist`-byte pattern
                    pat = bytes(out[ref:])
                    out += (pat * (length // dist + 1))[:length]
            else:
                ctrl += 1
                if ip + ctrl > n:
                    raise FastLZError("literal run past end of input")
                out += src[ip:ip + ctrl]
                ip += ctrl
            if ip >= n:
                break
            ctrl = src[ip]
            ip += 1
    except IndexError:
        raise FastLZError("truncated FastLZ stream") from None
    if expected_size is not None and len(out) != expected_size:
        raise FastLZError("decompressed to %d bytes, expected %d"
                          % (len(out), expected_size))
    return bytes(out)


def _literals(out, src, start, end):
    while end - start >= MAX_COPY:
        out.append(MAX_COPY - 1)
        out += src[start:start + MAX_COPY]
        start += MAX_COPY
    if end > start:
        out.append(end - start - 1)
        out += src[start:end]


def _match(out, length, distance):
    distance -= 1
    if distance < MAX_L2_DISTANCE:
        if length < 7:
            out.append((length << 5) + (distance >> 8))
        else:
            out.append((7 << 5) + (distance >> 8))
            length -= 7
            while length >= 255:
                out.append(255)
                length -= 255
            out.append(length)
        out.append(distance & 255)
    else:
        distance -= MAX_L2_DISTANCE
        if length < 7:
            out.append((length << 5) + 31)
        else:
            out.append((7 << 5) + 31)
            length -= 7
            while length >= 255:
                out.append(255)
                length -= 255
            out.append(length)
        out.append(255)
        out.append(distance >> 8)
        out.append(distance & 255)


def compress(data):
    """Compress `data` as a FastLZ level 2 stream."""
    src = bytes(data)
    length = len(src)
    out = bytearray()
    if length < 16:
        # too short for the matcher (and its 4-byte look-ahead): store as-is
        if length:
            _literals(out, src, 0, length)
            out[0] |= 1 << 5
        return bytes(out)

    ip_bound = length - 4
    ip_limit = length - 12 - 1
    htab = [0] * (1 << HASH_LOG)
    shift = 32 - HASH_LOG
    anchor = 0
    ip = 2

    while ip < ip_limit:
        # find a 3-byte match via the hash table
        while True:
            seq = src[ip] | (src[ip + 1] << 8) | (src[ip + 2] << 16)
            h = ((seq * 2654435769) >> shift) & HASH_MASK
            ref = htab[h]
            htab[h] = ip
            distance = ip - ref
            if distance < MAX_FARDISTANCE:
                cmp = src[ref] | (src[ref + 1] << 8) | (src[ref + 2] << 16)
            else:
                cmp = 0x1000000
            if ip >= ip_limit:
                break
            ip += 1
            if seq == cmp:
                break
        if ip >= ip_limit:
            break
        ip -= 1

        # far matches need at least 5 bytes to pay for the longer opcode
        if distance >= MAX_L2_DISTANCE:
            if src[ref + 3] != src[ip + 3] or src[ref + 4] != src[ip + 4]:
                ip += 1
                continue

        if ip > anchor:
            _literals(out, src, anchor, ip)

        # extend the match (flz_cmp: count includes the mismatching byte)
        p = ref + 3
        q = ip + 3
        while q < ip_bound:
            same = src[p] == src[q]
            p += 1
            q += 1
            if not same:
                break
        mlen = p - (ref + 3)
        _match(out, mlen, distance)

        # update the hash at the match boundary
        ip += mlen
        seq = src[ip] | (src[ip + 1] << 8) | (src[ip + 2] << 16)
        htab[((seq * 2654435769) >> shift) & HASH_MASK] = ip
        ip += 1
        seq = src[ip] | (src[ip + 1] << 8) | (src[ip + 2] << 16)
        htab[((seq * 2654435769) >> shift) & HASH_MASK] = ip
        ip += 1
        anchor = ip

    _literals(out, src, anchor, length)
    out[0] |= 1 << 5  # level 2 marker
    return bytes(out)
