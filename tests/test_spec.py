from host.common import spec

BLOCK_FU = {0x1: None, 0x2: "alu", 0x3: "mul", 0x4: "cmp", 0x5: "pc", 0x6: "mem",
            0x7: "tmr", 0x8: "trap", 0x9: "io", 0xA: "telem", 0xF: None}


def test_ids_and_names_unique():
    assert len({p.id for p in spec.PORTS}) == len(spec.PORTS)
    assert len({p.name for p in spec.PORTS}) == len(spec.PORTS)


def test_port_block_matches_fu():
    for p in spec.PORTS:
        assert p.id >> 4 in BLOCK_FU, p.name
        assert p.fu == BLOCK_FU[p.id >> 4], p.name


def test_zero_id_is_not_a_port():
    assert 0x00 not in spec.PORT_BY_ID


def test_every_port_has_a_direction():
    for p in spec.PORTS:
        assert p.readable or p.writable, p.name
        assert not p.trigger or p.writable, p.name


def test_fu_outputs_point_at_a_trigger_of_the_same_fu():
    for p in spec.PORTS:
        if p.produced_by is None:
            assert p.latency is None, p.name
            continue
        trig = spec.PORT_BY_NAME[p.produced_by]
        assert trig.trigger and trig.fu == p.fu, p.name
        assert p.latency >= 1, p.name


def test_port_groups_exist():
    for name in spec.CONTROL_PORTS + spec.TMR_VALUE_PORTS + spec.SYNC_POINT_PORTS:
        assert spec.PORT_BY_NAME[name].trigger


def test_timing_constants():
    assert (spec.D, spec.P, spec.H, spec.R) == (1, 2, 2, 2)
    assert spec.H == spec.P and spec.R == spec.P


def test_reserved_names_cover_registers_and_opcodes():
    assert {"r0", "r15", "null", "nop", "main", "alu", "ADD", "MAX"} <= spec.RESERVED_NAMES
