"""host/tools/wcet_board.py: a bad UART stream must never produce a pass (PR #5 review)."""
import importlib.util
import struct
from pathlib import Path

import pytest

from host.wcet import bench

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("wcet_board", ROOT / "host" / "tools" / "wcet_board.py")
wb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wb)


@pytest.fixture(scope="module")
def truth():
    return wb.expected()                                   # (static, iss)


def rec(b, d, cycles):
    return struct.pack("<4I", bench.MAGIC, b, d, cycles + wb.OVERHEAD)


def good_pass(iss):
    return [rec(b, d, iss[(b, d)]) for b, d in wb.ORDER]


def stream(records, lead=b"", split=7):
    """Byte chunks as the serial port would hand them over."""
    data = lead + b"".join(records)
    return [data[i:i + split] for i in range(0, len(data), split)]


def run(records, truth, lead=b""):
    static, iss = truth
    board = wb.parse_pass(stream(records, lead))
    return wb.compare(board, static, iss)[0]


def test_a_clean_pass_passes(truth):
    assert run(good_pass(truth[1]), truth)


def test_starting_mid_record_and_mid_pass_still_reads_one_full_pass(truth):
    recs = good_pass(truth[1])
    tail = b"".join(recs[10:])[5:]                         # half a record, then the rest of a pass
    assert run(recs, truth, lead=tail)


def test_a_missing_record_fails(truth):
    recs = good_pass(truth[1])
    del recs[17]
    with pytest.raises(wb.BoardError, match="expected bench"):
        run(recs, truth)


def test_a_repeated_record_fails(truth):
    recs = good_pass(truth[1])
    recs.insert(9, recs[8])
    with pytest.raises(wb.BoardError, match="repeated, missing or out of order"):
        run(recs, truth)


def test_records_out_of_order_fail(truth):
    recs = good_pass(truth[1])
    recs[3], recs[4] = recs[4], recs[3]
    with pytest.raises(wb.BoardError):
        run(recs, truth)


@pytest.mark.parametrize("b, d", [(len(bench.BENCHES), 0), (0, bench.DATASETS), (0xFFFF_FFFF, 0)])
def test_an_out_of_range_id_fails(truth, b, d):
    recs = good_pass(truth[1])
    recs[5] = rec(b, d, 10)
    with pytest.raises(wb.BoardError, match="out of range"):
        run(recs, truth)


def test_a_stray_byte_breaks_framing_and_fails(truth):
    recs = good_pass(truth[1])
    recs[20] = recs[20] + b"\x00"
    with pytest.raises(wb.BoardError, match="framing"):
        run(recs, truth)


def test_an_impossible_elapsed_fails(truth):
    recs = good_pass(truth[1])
    recs[2] = struct.pack("<4I", bench.MAGIC, 0, 2, wb.OVERHEAD - 1)
    with pytest.raises(wb.BoardError, match="not a possible value"):
        run(recs, truth)


def test_a_truncated_stream_fails(truth):
    recs = good_pass(truth[1])
    with pytest.raises(wb.BoardError, match="ended after 30"):
        run(recs[:30], truth)


def test_a_wrong_cycle_count_is_not_a_pass(truth):
    static, iss = truth
    recs = good_pass(iss)
    recs[1] = rec(0, 1, iss[(0, 1)] + 1)                   # off by one against the ISS
    assert not run(recs, truth)
    recs = good_pass(iss)
    recs[0] = rec(0, 0, static[0] - 1)                     # worst case below static: not tight
    assert not run(recs, truth)


@pytest.mark.parametrize("size", [1, 2, 3, 5, 15, 16, 17, 31, 64, 4096])
def test_any_chunk_size_gives_the_same_pass(truth, size):
    recs = good_pass(truth[1])
    lead = b"".join(good_pass(truth[1])[40:])[3:]          # start mid-record, mid-pass
    data = lead + b"".join(recs)
    chunks = [data[i:i + size] for i in range(0, len(data), size)]
    static, iss = truth
    assert wb.compare(wb.parse_pass(chunks), static, iss)[0]


@pytest.mark.parametrize("seed", range(20))
def test_random_chunk_boundaries_give_the_same_pass(truth, seed):
    import random
    rng = random.Random(seed)
    data = b"".join(good_pass(truth[1]))
    cuts = sorted(rng.sample(range(1, len(data)), rng.randint(1, 80)))
    chunks = [data[a:b] for a, b in zip([0] + cuts, cuts + [len(data)])]
    static, iss = truth
    assert wb.compare(wb.parse_pass(chunks), static, iss)[0]


def test_a_record_cut_short_at_the_end_fails(truth):
    data = b"".join(good_pass(truth[1]))[:-6]              # last record only 10 of 16 bytes
    with pytest.raises(wb.BoardError, match="ended after 47 of 48"):
        wb.parse_pass([data])


def test_a_corrupted_magic_word_mid_stream_fails(truth):
    recs = good_pass(truth[1])
    recs[30] = b"\x00" + recs[30][1:]
    with pytest.raises(wb.BoardError, match="framing"):
        run(recs, truth)


def test_a_duplicated_dataset_inside_a_bench_fails(truth):
    static, iss = truth
    recs = good_pass(iss)
    recs[11] = rec(1, 2, iss[(1, 2)])                      # bench 1 dataset 2 sent twice, 3 missing
    with pytest.raises(wb.BoardError, match="expected bench 1, dataset 3"):
        run(recs, truth)


def test_noise_before_the_first_record_is_skipped_but_not_after(truth):
    recs = good_pass(truth[1])
    assert run(recs, truth, lead=b"\xff\x13\x00garbage")   # before sync: ignored
    with pytest.raises(wb.BoardError, match="framing"):
        run(recs[:5] + [b"garbage" + recs[5]] + recs[6:], truth)


def test_compare_refuses_an_incomplete_record_set(truth):
    static, iss = truth
    board = dict(iss)
    del board[(2, 3)]
    ok, lines = wb.compare(board, static, iss)
    assert not ok and "incomplete" in lines[0]
