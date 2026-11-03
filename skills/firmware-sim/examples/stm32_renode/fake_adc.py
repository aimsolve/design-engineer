# Renode Python peripheral (IronPython 2.7, no imports needed): a synthetic 24-bit ADC data register at offset 0.
# Every read returns base + step (after sample STEP_AT: "motor on") + approx. Gaussian noise (sum of 12 uniforms).
# Change BASE/STEP/SIGMA to replay the client's measured numbers; any register layout can be faked the same way.
if request.IsInit:
    BASE = 100000
    STEP = 2000
    STEP_AT = 20
    SIGMA = 100
    n = 0
    seed = 12345
elif request.IsRead:
    s = 0
    for i in range(12):
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        s += (seed >> 16) & 0xFF
    noise = (s - 12 * 128) * SIGMA / 256
    v = BASE + (STEP if n >= STEP_AT else 0) + noise
    n += 1
    request.Value = v & 0xFFFFFF
elif request.IsWrite:
    pass
