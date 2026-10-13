"""host/baseline/board.py and analysis.py without the board: the bootloader
dialogue against a fake serial port that answers like the NEORV32 bootloader
(sw/bootloader main.c, hal/system.c), the NRV1 stream parser, and the
statistics. The replay against the RTL plant is in test_baseline_ctrl.py."""
import struct

import pytest

from host.baseline import analysis as A, board as BD, bustrace as bt

SIG = 0x214F454E                # BIN_SIGNATURE of the bootloader


def exe_image(words, base=0):
    """neorv32_exe.bin: signature, base, size, checksum, then the words; the
    bootloader requires sum(words) + checksum + 1 == 0 (mod 2^32)"""
    chk = (-sum(words) - 1) & 0xFFFF_FFFF
    return struct.pack(f"<4I{len(words)}I", SIG, base, 4 * len(words), chk, *words)


class FakeBootloader:
    """Serial port with the bootloader behind it."""

    def __init__(self, soc=0x0000_0060, banner=b"\x00\xff\n\nNEORV32 Bootloader\nbuild: x\n\n"):
        self.soc, self.banner = soc, banner
        self.out = bytearray()
        self.state = "off"
        self.rx = bytearray()
        self.started = False
        self.loaded = None
        self.baudrate = BD.BOOT_BAUD
        self.breaks = 0
        self.overruns = 0

    # pyserial-like
    @property
    def in_waiting(self):
        return len(self.out)

    def read(self, n):
        n = min(n, 5)                    # a few bytes at a time, as a real port delivers them
        b = bytes(self.out[:n])
        del self.out[:n]
        return b

    def reset_input_buffer(self):
        self.out.clear()

    def flush(self):
        pass

    def send_break(self, duration):
        assert duration >= 0.010
        self.breaks += 1
        self.state = "cmd"
        self.started = False
        self.out += self.banner + b"Auto-boot\nLoading from SPI flash @0x00400000... \aERROR_DEVICE\n" \
            b"Type 'h' for help.\nCMD:> "

    def write(self, data):
        assert self.baudrate == BD.BOOT_BAUD
        for c in data:
            if self.state == "cmd":
                self.out += bytes([c]) + b"\n"
                if c == ord("i"):
                    self.out += f"HWV:  0x01130600\nCLK:  0x05F5E100\nSOC:  0x{self.soc:08X}\n".encode() + b"CMD:> "
                elif c == ord("u"):
                    self.out += b"Awaiting neorv32_exe.bin... "
                    self.state, self.rx = "upload", bytearray()
                elif c == ord("e"):
                    self.out += b"Booting (@0x00000000)...\n\n"
                    self.started = True
                    self.state = "app"
                else:
                    self.out += b"CMD:> "
            elif self.state == "upload":
                # UART0 has a 1-byte RX FIFO and the bootloader reads only after
                # its prompt is out: a byte arriving while it is still printing
                # (prompt not yet all delivered to the host) is lost
                if self.out:
                    self.overruns += 1
                    continue
                self.rx.append(c)
                if len(self.rx) >= 16:
                    sig, base, size, chk = struct.unpack_from("<4I", self.rx)
                    if sig != SIG:
                        self.out += b"\aERROR_SIGNATURE\n"
                        self.state = "dead"
                    elif len(self.rx) == 16 + size:
                        words = struct.unpack_from(f"<{size // 4}I", self.rx, 16)
                        if (sum(words) + chk + 1) & 0xFFFF_FFFF:
                            self.out += b"\aERROR_CHECKSUM\n"
                            self.state = "dead"
                        else:
                            self.loaded = (base, words)
                            self.out += b"OK\nCMD:> "
                            self.state = "cmd"


def test_configurations():
    c1, c0 = BD.configs(1), BD.configs(0)
    assert len(c1) == 8 and len(c0) == 6                       # L1di only with the caches
    assert set(c0) == {n for n in c1 if "L1di" not in n}
    assert c1["irq_L2_wait"] == {"TRIG": 0, "LOAD": 3, "ACT": 1}
    assert not [n for n in c1 if n.startswith("poll")]         # polling: its own commit


def test_upload_and_start():
    ser = FakeBootloader()
    link = BD.Link(ser)
    img = exe_image([1, 2, 0xFFFF_FFFF, 0x1234_5678])
    BD.reset_to_bootloader(link)
    BD.check_bitstream(link, 1)
    BD.upload_and_start(link, img)
    assert ser.breaks == 1 and ser.started and ser.loaded == (0, (1, 2, 0xFFFF_FFFF, 0x1234_5678))
    assert ser.overruns == 0


def test_sending_before_the_whole_prompt_overruns():
    # what the first board run did: start the image as soon as "Awaiting" was
    # in, while the bootloader was still printing " neorv32_exe.bin... ". On
    # the board the first bytes were lost and the rest misread as the header
    # (ERROR_SIGNATURE); the fake has no time, so it loses them all
    ser = FakeBootloader()
    link = BD.Link(ser)
    BD.reset_to_bootloader(link)
    ser.write(b"u")
    link.wait_for(b"Awaiting", 5)
    ser.write(exe_image([1, 2, 3]))
    assert ser.overruns > 0 and ser.loaded is None


@pytest.mark.parametrize("soc, cache", [(0x60, 0), (0x00, 1), (0x20, 1), (0x40, 0)])
def test_wrong_bitstream_is_refused(soc, cache):
    link = BD.Link(FakeBootloader(soc=soc))
    BD.reset_to_bootloader(link)
    with pytest.raises(BD.BoardError, match="wrong bitstream"):
        BD.check_bitstream(link, cache)


def test_bootloader_errors_are_reported():
    link = BD.Link(FakeBootloader())
    BD.reset_to_bootloader(link)
    bad = bytearray(exe_image([5, 6, 7]))
    bad[-1] ^= 1                                               # checksum no longer matches
    with pytest.raises(BD.BoardError, match="ERROR_CHECKSUM"):
        BD.upload_and_start(link, bytes(bad))
    link = BD.Link(FakeBootloader())
    BD.reset_to_bootloader(link)
    with pytest.raises(BD.BoardError, match="ERROR_SIGNATURE"):
        BD.upload_and_start(link, b"\0" * 32)


def test_the_banner_device_error_is_not_fatal():
    # the bootloader tries the (absent) SPI flash after reset and prints
    # ERROR_DEVICE before the prompt: reset_to_bootloader must get past it
    link = BD.Link(FakeBootloader())
    BD.reset_to_bootloader(link)
    assert link.buf == bytearray()


# ---------------------------------------------------------------- NRV1 stream
def rec_words(k, rel=100_000, start=50, sense=200, act=600, enc=-3, u=17, flags=0b111):
    return [bt.MAGIC, k, rel & 0xFFFF_FFFF, rel >> 32, start, sense, act, enc & 0xFFFF_FFFF, u & 0xFFFF_FFFF, flags]


def stream(*records, pre=b""):
    return pre + b"".join(struct.pack("<10I", *w) for w in records)


def chunks(b, size=7):
    return [b[i:i + size] for i in range(0, len(b), size)]


def test_stream_starts_at_record_zero():
    junk = b"Booting from 0x00000000...\n" + struct.pack("<I", bt.MAGIC) + b"\x01\x00"
    old = rec_words(5)                                          # an earlier run's record: skipped
    s = stream(old, rec_words(0), rec_words(1, rel=200_000), rec_words(2, rel=300_000), pre=junk)
    recs = BD.parse_stream(chunks(s), 3)
    assert [r.k for r in recs] == [0, 1, 2] and recs[0].enc == -3 and recs[1].rel == 200_000


def test_stream_is_strict_after_record_zero():
    with pytest.raises(BD.BoardError, match="expected record 1, got 2"):
        BD.parse_stream(chunks(stream(rec_words(0), rec_words(2))), 3)
    with pytest.raises(BD.BoardError, match="lost framing"):
        BD.parse_stream(chunks(stream(rec_words(0)) + b"\x00" * 40), 3)
    with pytest.raises(BD.BoardError, match="ended after 1"):
        BD.parse_stream(chunks(stream(rec_words(0))), 3)


# ---------------------------------------------------------------- statistics
def R(k, rel, start, sense, act, flags=0b111, bg=0):
    return bt.Record(k, rel, start, sense, act, 0, 0, flags | bg)


def test_nearest_rank_percentiles():
    d = A.dist(list(range(1, 101)))
    assert (d["min"], d["p50"], d["p99"], d["max"], d["spread"]) == (1, 50, 99, 100, 99)
    d = A.dist([7])
    assert d["p50"] == d["p99"] == 7


def test_summary_quantities():
    P = A.PERIOD
    recs = [R(0, 0, 30, 170, 330), R(1, P, 40, 175, 3000), R(2, 2 * P, 35, 160, 1999),
            R(3, 3 * P, None, None, None, flags=0)]
    s = A.summary(recs)
    assert s["release_start"]["max"] == 40 and s["sense_act"]["max"] == 3000 - 175
    # consecutive SENSE events minus the period: sense 170 -> 175 -> 160, releases exact
    assert (s["sense_interval"]["min"], s["sense_interval"]["max"]) == (-15, 5)
    assert s["release_interval"]["min"] == s["release_interval"]["max"] == 0
    assert s["deadline_misses"] == 2                           # act 3000, and the record with no act
    # a release one cycle late shows in the release interval, and moves the sense interval with it
    late = A.summary([R(0, 0, 30, 170, 330), R(1, P + 1, 30, 170, 330)])
    assert late["release_interval"]["max"] == 1 and late["sense_interval"]["max"] == 1
    bg = A.summary([R(0, 0, 30, 170, 330, bg=(502 << 16) | (7 << 8))])
    assert bg["bg_per_period"]["max"] == 7 and bg["bg_cycles_per_period"]["max"] == 502


def test_all_periods_and_steady_state():
    P = A.PERIOD
    recs = [R(0, 0, 90, 340, 570), R(1, P, 30, 175, 340), R(2, 2 * P, 32, 176, 2100)]
    s = A.summaries(recs)
    assert s["all"]["periods"] == 3 and s["steady"]["periods"] == 2
    assert s["all"]["release_start"]["max"] == 90 and s["steady"]["release_start"]["max"] == 32
    assert s["all"]["sense_interval"]["n"] == 2 and s["steady"]["sense_interval"]["n"] == 1
    assert s["all"]["deadline_misses"] == s["steady"]["deadline_misses"] == 1


def test_records_csv_round_trip(tmp_path, monkeypatch):
    P = A.PERIOD
    recs = [bt.Record(0, 98_000, 31, 176, 339, -5, 17, 0b111 | (502 << 16) | (7 << 8)),
            bt.Record(1, 98_000 + P, None, 176, None, 0, 0, 0b010)]
    monkeypatch.setattr(BD, "git_describe", lambda: "test")
    s = BD.save(tmp_path, "irq_L2_asap", 1, recs)
    assert BD.load_records(tmp_path / "records.csv") == recs
    assert not s["integrity_ok"]                               # events missing in record 1
    assert "INTEGRITY PROBLEMS" in BD.verdict(s) and "DEADLINE MISSES 1 OF 2" in BD.verdict(s)


def test_verdict_keeps_misses_apart_from_integrity():
    # "integrity OK" is about the measurement; misses are printed with it, never hidden
    P = A.PERIOD
    recs = [R(0, 0, 30, 170, 330), R(1, P, 30, 170, 2500)]
    s = {"integrity_ok": True, "n_sequence_problems": 0, "n_replay_mismatches": 0, **A.summaries(recs)}
    v = BD.verdict(s)
    assert v.startswith("integrity OK") and "DEADLINE MISSES 1 OF 2" in v


def test_sequence_checks():
    P = A.PERIOD
    ok = [R(0, 0, 1, 2, 3), R(1, P, 1, 2, 3)]
    assert A.check_sequence(ok) == []
    bad = [R(0, 0, 1, 2, 3), R(2, P + 1, 1, 2, 3, flags=0b1_1011)]
    probs = A.check_sequence(bad)
    assert any("k = 2" in p for p in probs) and any("100001 cycles" in p for p in probs)
    assert any("0b011" in p for p in probs) and any("repeated" in p for p in probs)
    assert any("dropped" in p for p in probs)


# ---------------------------------------------------------------- provenance (audit A5-3)
REAL_GIT_STATE = BD.git_state                 # before any fixture replaces it
GIT_BUILD = {"commit": "a" * 40, "dirty": True,
             "changed": [" M Time_Predictable_TTA.xpr", "?? baseline/sw/ctrl/extra.h"]}


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A repository root with baseline/sw sources and a built executable."""
    sw = tmp_path / "baseline" / "sw" / "ctrl"
    sw.mkdir(parents=True)
    (sw / "main.c").write_bytes(b"int main(void) { return 0; }\n")
    (sw / "extra.h").write_bytes(b"#define X 1\n")                  # untracked in GIT_BUILD
    out = tmp_path / "build" / "baseline_exe" / "irq_L0_asap"
    out.mkdir(parents=True)
    (out / "neorv32_exe.bin").write_bytes(b"\x13\x00\x00\x00" * 64)
    monkeypatch.setattr(BD, "ROOT", tmp_path)
    monkeypatch.setattr(BD, "SW_SOURCES", tmp_path / "baseline" / "sw")
    monkeypatch.setattr(BD, "EXE_DIR", tmp_path / "build" / "baseline_exe")
    monkeypatch.setattr(BD, "OUT_DIR", tmp_path / "build" / "baseline_board")
    monkeypatch.setattr(BD, "bitstream_path", lambda cache: tmp_path / "arty_neorv32_top.bit")
    monkeypatch.setattr(BD, "git_state", lambda repo=None: GIT_BUILD if repo is None else
                        {"commit": "e" * 40, "dirty": False, "changed": []})
    return tmp_path, out


def fake_board(monkeypatch, seen=None):
    """run_one without a board: every serial step is a stand-in."""
    for f in ("reset_to_bootloader", "check_bitstream", "upload_and_start"):
        monkeypatch.setattr(BD, f, lambda *a: None)
    monkeypatch.setattr(BD, "serial_chunks", lambda *a: iter(()))
    monkeypatch.setattr(BD, "parse_stream", lambda chunks, n: [bt.Record(0, 98_000, 31, 176, 339, 0, 0, 0b111)])

    class Ser:
        baudrate = 0

        def reset_input_buffer(self):
            pass
    return Ser()


def test_the_build_manifest_is_bound_to_the_executable(tree):
    import hashlib
    root, out = tree
    m = BD.write_manifest("irq_L0_asap", out, {"TRIG": 0}, "riscv-none-elf-gcc 15.2.0")
    exe = (out / "neorv32_exe.bin").read_bytes()
    assert m["exe"] == {"bytes": 256, "sha256": hashlib.sha256(exe).hexdigest()}
    assert m["git"] == {**GIT_BUILD, "changed_sha256": {
        "Time_Predictable_TTA.xpr": None,                                   # not in this tree: as deleted
        "baseline/sw/ctrl/extra.h": hashlib.sha256(b"#define X 1\n").hexdigest()}}
    assert m["neorv32"] == {"commit": "e" * 40, "dirty": False, "changed": [], "changed_sha256": {}}
    assert m["toolchain"].startswith("riscv")
    assert m["sources"] == {"baseline/sw/ctrl/extra.h": hashlib.sha256(b"#define X 1\n").hexdigest(),
                            "baseline/sw/ctrl/main.c": hashlib.sha256(b"int main(void) { return 0; }\n").hexdigest()}
    assert __import__("json").loads((out / "manifest.json").read_text()) == m


def test_local_changes_inside_the_neorv32_submodule_are_hashed(tree, monkeypatch):
    # the submodule commit names the committed sources only; crt0, the linker
    # script and the libraries go into every executable, so a local edit or a
    # new file there must be identified by its content
    import hashlib
    root, out = tree
    nrv = root / "baseline" / "neorv32"
    (nrv / "sw" / "common").mkdir(parents=True)
    (nrv / "sw" / "lib").mkdir(parents=True)
    (nrv / "sw" / "common" / "crt0.S").write_bytes(b"_start: j main\n")
    (nrv / "sw" / "lib" / "extra.c").write_bytes(b"int extra;\n")
    sub_state = {"commit": "e" * 40, "dirty": True,
                 "changed": [" M sw/common/crt0.S", "?? sw/lib/extra.c", " D sw/common/gone.c"]}
    monkeypatch.setattr(BD, "git_state", lambda repo=None: GIT_BUILD if repo is None else sub_state)
    m = BD.write_manifest("irq_L0_asap", out, {}, "gcc")
    assert m["neorv32"]["changed_sha256"] == {
        "sw/common/crt0.S": hashlib.sha256(b"_start: j main\n").hexdigest(),
        "sw/lib/extra.c": hashlib.sha256(b"int extra;\n").hexdigest(),
        "sw/common/gone.c": None}
    (nrv / "sw" / "common" / "crt0.S").write_bytes(b"_start: j other\n")                 # edited after the build
    assert BD.load_manifest("irq_L0_asap", (out / "neorv32_exe.bin").read_bytes()) == m
    line = BD.provenance_line(BD.provenance(1, (out / "neorv32_exe.bin").read_bytes(), m))
    assert "NEORV32 changes: sw/common/crt0.S, sw/lib/extra.c, sw/common/gone.c" in line


def test_a_renamed_file_is_hashed_under_its_new_name(tmp_path):
    (tmp_path / "new.c").write_bytes(b"x")
    state = {"changed": ["R  old.c -> new.c"]}
    assert BD.changed_hashes(tmp_path, state) == {"new.c": __import__("hashlib").sha256(b"x").hexdigest()}


def test_a_source_changed_after_the_build_does_not_change_the_recorded_build(tree, monkeypatch):
    root, out = tree
    built = BD.write_manifest("irq_L0_asap", out, {}, "gcc")
    (root / "baseline" / "sw" / "ctrl" / "main.c").write_bytes(b"int main(void) { return 1; }\n")  # edited after
    monkeypatch.setattr(BD, "git_state", lambda repo=None: {"commit": "c" * 40, "dirty": False, "changed": []})
    s = BD.run_one(fake_board(monkeypatch), "irq_L0_asap", 1, 1)
    prov = s["provenance"]
    assert prov["build"] == built                                 # the state the executable came from
    assert prov["build"]["git"]["commit"] == "a" * 40 and prov["run"]["git"]["commit"] == "c" * 40
    assert prov["sources_changed_since_build"] == ["baseline/sw/ctrl/main.c"]
    line = BD.provenance_line(prov)
    assert "from commit aaaaaaaaaa + changes: Time_Predictable_TTA.xpr, baseline/sw/ctrl/extra.h" in line
    assert "(sources changed since the build: baseline/sw/ctrl/main.c)" in line


def test_an_executable_its_manifest_does_not_describe_is_refused(tree, monkeypatch):
    root, out = tree
    BD.write_manifest("irq_L0_asap", out, {}, "gcc")
    (out / "neorv32_exe.bin").write_bytes(b"rebuilt without a manifest")
    with pytest.raises(BD.BoardError, match="not the executable its manifest describes"):
        BD.run_one(fake_board(monkeypatch), "irq_L0_asap", 1, 1)
    (out / "manifest.json").unlink()
    with pytest.raises(BD.BoardError, match="no build manifest"):
        BD.run_one(fake_board(monkeypatch), "irq_L0_asap", 1, 1)


def test_git_state_keeps_the_first_status_line_whole(monkeypatch):
    # 'git status --porcelain' starts with a space for a file changed in the work tree;
    # stripping the output used to cut the first character of that file name
    class R:
        def __init__(self, out):
            self.stdout = out
    outs = iter([R("f" * 40 + "\n"), R(" M Time_Predictable_TTA.xpr\n?? notes.txt\n")])
    monkeypatch.setattr(BD.subprocess, "run", lambda *a, **k: next(outs))
    g = BD.git_state()
    assert g == {"commit": "f" * 40, "dirty": True, "changed": [" M Time_Predictable_TTA.xpr", "?? notes.txt"]}
    assert BD._files(g["changed"]) == "Time_Predictable_TTA.xpr, notes.txt"


def test_git_state_reads_the_real_repository():
    g = BD.git_state()
    assert len(g["commit"]) == 40 and int(g["commit"], 16) >= 0
    assert isinstance(g["dirty"], bool) and (g["dirty"] == bool(g["changed"]))


def test_no_bitstream_file_old_runs_and_first_version_runs_say_so(tree):
    assert BD.provenance(0, b"x")["bitstream_file"] is None
    assert BD.provenance_line(None) == "provenance not recorded (run made before audit A5-3)"
    first = {"exe": {"bytes": 1, "sha256": "d" * 64}, "bitstream_file": None,
             "git": {"commit": "b" * 40, "dirty": True, "changed": ["M Time_Predictable_TTA.xpr"]}}
    assert "no build manifest; at run time commit bbbbbbbbbb" in BD.provenance_line(first)


def test_report_lists_the_provenance_of_every_run(tree, monkeypatch, capsys):
    root, out = tree
    BD.write_manifest("irq_L0_asap", out, {}, "gcc")
    recs = [bt.Record(k, 98_000 + k * A.PERIOD, 31, 176, 339, 0, 0, 0b111) for k in range(3)]
    exe = (out / "neorv32_exe.bin").read_bytes()
    BD.save(BD.OUT_DIR / "c1" / "irq_L0_asap", "irq_L0_asap", 1, recs, exe, BD.load_manifest("irq_L0_asap", exe))
    BD.save(BD.OUT_DIR / "c1" / "irq_L0_wait", "irq_L0_wait", 1, recs)
    (BD.OUT_DIR / "c1" / "irq_L0_wait" / "summary.json").write_text("{}")        # an old run
    import sys
    BD.report(sys.stdout)                                      # its default is bound at import
    out_text = capsys.readouterr().out
    assert "- c1 irq_L0_asap: program sha256" in out_text and "from commit aaaaaaaaaa" in out_text
    assert "- c1 irq_L0_wait: provenance not recorded" in out_text


def test_every_changed_file_is_hashed_however_many(tree, monkeypatch):
    # git_state used to keep only the first 200 status lines, so a larger dirty
    # tree lost the hashes of the rest while still saying it was dirty
    import hashlib
    root, out = tree
    many = root / "many"
    many.mkdir()
    for i in range(250):
        (many / f"f{i:03}.txt").write_bytes(f"{i}\n".encode())

    class R:
        def __init__(self, out):
            self.stdout = out
    status = "".join(f"?? many/f{i:03}.txt\n" for i in range(250))
    outs = iter([R("a" * 40 + "\n"), R(status), R("e" * 40 + "\n"), R("")])   # repository, then submodule
    monkeypatch.setattr(BD, "git_state", REAL_GIT_STATE)          # the real one, over a fake git
    monkeypatch.setattr(BD.subprocess, "run", lambda *a, **k: next(outs))
    m = BD.write_manifest("irq_L0_asap", out, {}, "gcc")
    assert len(m["git"]["changed"]) == 250
    assert m["git"]["changed_sha256"] == {f"many/f{i:03}.txt": hashlib.sha256(f"{i}\n".encode()).hexdigest()
                                          for i in range(250)}
    line = BD._files(m["git"]["changed"])                # the display limit is only here
    assert line.startswith("many/f000.txt, ") and line.endswith(" and 240 more")


def test_status_paths_of_every_stored_form():
    assert [BD._path(x) for x in (" M a.txt", "?? b c.txt", "MM d.txt", "R  old.c -> new.c", ' M "q r.txt"')] == \
        ["a.txt", "b c.txt", "d.txt", "new.c", "q r.txt"]
    # the 2026-10-13 runs were saved by the first A5-3 version, which stripped the
    # leading space of the first line: 'M Time_...' must still read as the full name
    assert BD._path("M Time_Predictable_TTA.xpr") == "Time_Predictable_TTA.xpr"
    first = {"exe": {"bytes": 1, "sha256": "d" * 64}, "bitstream_file": None,
             "git": {"commit": "b" * 40, "dirty": True, "changed": ["M Time_Predictable_TTA.xpr"]}}
    assert "changes: Time_Predictable_TTA.xpr" in BD.provenance_line(first)
