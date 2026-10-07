from host.common import gen_sv, spec


def test_committed_package_is_up_to_date():
    assert gen_sv.OUT.exists(), "run: py -m host.common.gen_sv"
    assert gen_sv.OUT.read_text(encoding="utf-8") == gen_sv.generate(), \
        f"{gen_sv.OUT.name} is stale; run: py -m host.common.gen_sv"


def test_generation_is_deterministic():
    assert gen_sv.generate() == gen_sv.generate()


def test_every_port_and_constant_is_present():
    text = gen_sv.generate()
    for p in spec.PORTS:
        assert f"{gen_sv.sv_name(p.name)}" in text and f"8'h{p.id:02X};" in text
    assert "localparam int T_P = 2;" in text
    assert "localparam int LAT_MUL_OUT" in text and "= 2;" in text
    assert "ALU_MAX = 4'd9" in text
    assert f'SPEC_ID = "{spec.SPEC_ID}"' in text


def test_generated_hdl_is_ascii():
    assert gen_sv.generate().isascii()


def test_port_names_are_unique_in_sv():
    names = [gen_sv.sv_name(p.name) for p in spec.PORTS]
    assert len(names) == len(set(names))
