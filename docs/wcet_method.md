# WCET method

สถานะ: Phase 4 · tool อยู่ที่ `host/wcet/` ใช้ตาม [timing_model.md](timing_model.md) §3 ซึ่งเป็นสัญญาระหว่าง tool กับฮาร์ดแวร์

```bash
py -m host.wcet programs/fib.tta
```

## 1. หน่วยที่วิเคราะห์

**Segment** คือช่วงของโปรแกรมระหว่างจุดตัดสองจุดใน function เดียว

- **จุดเริ่ม:** entry ของ function, move ถัดจาก sync point (`t_advance`, `t_wait`) หรือ move ถัดจาก `t_sync`
- **จุดจบ:** sync point, `t_sync`, return (`pc.link -> pc.t_jump`), `trap.t_halt` หรือ word ที่ trap เสมอ (`.illegal`)

**W** ของ segment คือจำนวน cycle ตั้งแต่ move แรกถึงจุดจบ ถ้าจุดจบเป็นจุดตัด (sync point หรือ `t_sync`) ไม่นับ cycle ของตัวมันเองและไม่นับ stall ตาม §3 ถ้าจุดจบเป็น return, halt หรือ trap นับ move นั้นด้วย

`t_sync` ไม่ใช่ sync point เพราะไม่ stall แต่ย้าย anchor ถ้าปล่อยไว้กลาง segment budget จะผิด tool จึงใช้เป็นจุดตัดด้วย ส่วนนี้ tool เพิ่มขึ้นเอง ไม่ได้อยู่ใน spec ที่ freeze แล้ว และไม่เปลี่ยนตัวเลขใดใน spec

## 2. Cost model

| อะไร | cycle | กฎ |
|---|---|---|
| move ที่ execute | 1 | R1 |
| jump หรือ call ที่ taken, return | + P | R4 |
| call ไป `f` | + P + W(f) + P | W(f) = entry ถึง return รวม move ของ return |
| stall ของ sync point | ไม่นับ | §3 |

edge cost ใน `host/common/cfg.py` เป็นค่าขั้นต่ำที่สร้างไว้สำหรับ S1 tool นี้ใช้แค่โครงสร้างของ CFG และคิด cost เองตามตารางข้างบน

## 3. หา path ที่ยาวที่สุด: IPET

ใช้ Implicit Path Enumeration (Li & Malik 1995) แบบเดียวกับ aiT, OTAWA และ platin ของ Patmos ตัวแปร `x(e)` คือจำนวนครั้งที่ edge `e` ถูกใช้

```
maximize    Σ x(move) · 1  +  Σ x(e) · extra(e)
subject to  x(source) = 1, x(sink) = 1
            flow in = flow out ที่ทุก move
            x(header) ≤ N · x(edge ที่เข้า loop จากข้างนอก)      สำหรับ @loop_bound N
```

แก้ด้วย `scipy.optimize.milp` (HiGHS) ทำ ILP แยกทุกคู่ของจุดเริ่มกับจุดจบ ผลที่ได้เป็นจำนวนครั้งต่อ edge

**Witness path:** tool เรียงจำนวนครั้งต่อ edge กลับเป็น path จริงหนึ่งเส้น เพื่อให้รู้ว่าต้องป้อน input แบบไหนจึงจะวิ่งไปตาม path ที่ยาวที่สุด เก็บเป็นลำดับของ edge ไม่ใช่ address เพราะ jump ที่มีเงื่อนไขและ target เป็นคำสั่งถัดไป จะมี edge สองเส้นที่ cost ต่างกันระหว่างคู่ address เดียวกัน

constraint ของ loop ใน ILP เป็นแบบรวม การเดินแบบ Hierholzer ธรรมดาจึงอาจให้ entry หนึ่งของ loop ในวิ่งเกิน N รอบ แล้วอีก entry วิ่งน้อยกว่า ได้จำนวนครั้งและ cost ถูก แต่เป็น path ที่เกิดจริงไม่ได้ tool จึงแบ่งรอบให้แต่ละ entry อย่างสมดุล คือ `ceil(H / E)` จากจำนวน header ที่เหลือ H และ entry ที่เหลือ E ค่านี้ไม่เกิน N เพราะ ILP บังคับ `H ≤ N·E` ไว้แล้ว

หลังสร้างเสร็จ tool ตรวจ witness ทุกเส้นซ้ำ ถ้าข้อใดไม่ผ่านเป็น internal error ไม่มีทางกลายเป็นผลที่ผิดแบบเงียบๆ:
- เริ่มที่ source และจบที่ sink
- ทุก edge ต่อกันและมีอยู่จริงใน CFG
- จำนวนครั้งต่อ edge ตรงกับคำตอบของ ILP
- cost เท่ากับค่า objective
- ไม่มี loop ไหนวิ่งเกิน bound ใน entry ใด entry หนึ่ง

`tests/test_wcet_review.py` ยืนยันด้วยว่า witness ของโปรแกรมทดสอบทุกตัว (รวม bubble sort และ loop ซ้อน) ตรงกับลำดับ PC ที่ ISS รันจริงเมื่อใช้ worst-case input ทีละตำแหน่ง

**Loop:** หา natural loop จาก back edge (edge ที่ปลายทาง dominate ต้นทาง)
- header ของ loop ต้องมี `@loop_bound` (S6) ถ้าไม่มีจะเป็น error
- loop ที่ผ่าน sync point ถูกตัดเป็นหลาย segment อยู่แล้ว จึงไม่ต้องมี bound
- retreating edge ที่ไม่ใช่ back edge แปลว่า loop เป็นแบบ irreducible tool จะปฏิเสธ

**Call:** วิเคราะห์ callee ก่อน caller ซึ่งทำได้เพราะ S5 ห้าม recursion ไว้แล้ว

## 4. Budget

segment ที่จบที่ sync point ซึ่ง v เป็น immediate มี budget ดังนี้

```
Δ = v − offset − D        offset = 0 หลัง t_advance หรือ t_sync,  v_prev หลัง t_wait
```

ผ่านเมื่อ `W ≤ Δ` กรณีที่ไม่มี budget:
- segment ที่เริ่มที่ entry เพราะไม่รู้ anchor
- segment ที่จบที่ `t_sync`
- timing move ที่รับ v จาก register ซึ่ง tool จะเตือน (asm_syntax.md §6)

ตัวอย่าง 3 ของ timing_model §5 ได้ W = 625 และ Δ = 999 ตรงกับที่นับด้วยมือ

## 5. ความหมายของตัวเลขและข้อจำกัด

**W เป็นขอบบนที่ปลอดภัย (safe upper bound) เสมอ** ภายใต้เงื่อนไขที่ tool พิมพ์ไว้ในบรรทัด `valid if` ของแต่ละ segment ได้แก่
- loop ทุกตัวที่ segment อาจวิ่งผ่าน รวม loop ใน callee ต้องวิ่ง header ไม่เกิน N ครั้งต่อการเข้า loop หนึ่งครั้ง
- loop ที่ไม่อยู่บน worst path ก็นับเป็นเงื่อนไขด้วย เพราะถ้ามันวิ่งเกิน bound path ที่ผ่านมันอาจยาวกว่า W
- ไม่มี trap เกิดใน segment และทุก move ถูกกติกา (ผ่าน S1–S7)

**W เป็นค่า tight** คือมี input ที่ใช้เวลา W พอดี ก็ต่อเมื่อ witness path เกิดได้จริง tool ไม่ได้ตรวจ feasibility ของ path ถ้ามี branch ที่เงื่อนไขขัดกันเอง W จะสูงกว่าความจริงได้แต่ไม่มีทางต่ำกว่า วิธียืนยันว่า tight คือรัน worst-case input บน ISS หรือบอร์ด ซึ่ง benchmark ทุกตัวในตาราง §6 ยืนยันแล้ว

- **loop bound เป็นเงื่อนไขของ input:** ถ้าข้อมูลทำให้ loop วิ่งเกิน `@loop_bound` เวลาที่วัดได้จะเกิน WCET เช่น `fib` ใน benchmark รับ n ได้แค่ 1–20 เทสต์ `test_a_bound_below_the_real_count_breaks_the_precondition` แสดงกรณีนี้ไว้
- **infeasible path:** ถ้า path ที่ยาวที่สุดใน CFG เกิดจริงไม่ได้ เช่น branch สองจุดที่เงื่อนไขขัดกันเอง WCET ยังปลอดภัยแต่จะไม่ tight ทางแก้คือเขียนโค้ดให้เลี่ยงกรณีนี้ (B4) เช่นใช้ MIN/MAX แทน if ซึ่ง `sort8` และ clamp ใน `pid` ทำอยู่
- **ยังไม่รองรับ** call ไป function ที่มี sync point, indirect call และ path ที่จบด้วย halt ภายใน callee

## 6. การยืนยัน

`programs/wcet_bench.tta` สร้างจาก `py -m host.wcet.bench` มี benchmark 6 ตัว
- ตัวที่เวลาขึ้นกับข้อมูล: `fib(n)`, `search`, `cpos`, `pid`
- ตัวที่เวลาคงที่: `sort8`, `spo`

harness วัดเวลาแต่ละครั้งด้วย `t_sync → call → tmr.elapsed` ได้ `elapsed = W + 2 + 2P` ทุกตัวมี dataset 8 ชุด ชุดที่ 0 คือ worst case ที่เลือกจาก witness path

| ชั้น | ตรวจอะไร | ที่ไหน |
|---|---|---|
| ISS | dataset 0 วัดได้ **เท่ากับ** W ทุกตัว และ input สุ่ม 40 seed × 7 ชุดไม่เกิน W | `tests/test_wcet.py` |
| RTL | trace ของ `wcet_bench` ตรงกับ ISS ทุก cycle | `tests/test_lockstep.py` (`prog_wcet_bench`) |
| บอร์ด | ทั้ง 48 run ตรงกับ ISS, dataset 0 เท่ากับ W และไม่มีชุดไหนเกิน W (ผ่าน) | `host/tools/wcet_board.py` |

ตัวอ่านผลจากบอร์ดเข้มงวดพอที่ stream ผิดรูปแบบจะไม่มีทางผ่าน:
- หลัง magic word ตัวแรก ทุก 16 byte ต้องเป็น record หนึ่งตัว
- id ต้องอยู่ในช่วง และ elapsed ต้องเป็นค่าที่เกิดได้
- ตั้งแต่ (0, 0) record ต้องมาตามลำดับของ harness พอดีจนครบ 48 คู่

record ที่ซ้ำ หาย สลับลำดับ อยู่นอกช่วง มี byte แปลกปน หรือ stream ที่จบก่อนครบ จะล้มทันที ทุกกรณีมีเทสต์อยู่ใน `tests/test_wcet_board.py`

ผล (seed 2026) หน่วยเป็น cycle ของ function บอร์ด Arty A7-100T ได้ตัวเลข**เท่ากับ ISS ทุกช่อง** (วัด 2026-10-10)

| bench | W (static) | บอร์ดและ ISS ชุด 0–7 |
|---|---|---|
| fib | 224 | 224 48 125 191 191 48 92 224 |
| search | 229 | 229 139 229 111 55 97 153 229 |
| cpos | 276 | 276 268 268 270 270 265 268 270 |
| sort8 | 1275 | 1275 ทุกชุด |
| pid | 76 | 76 63 63 76 63 63 63 63 |
| spo | 19 | 19 ทุกชุด |

**บนบอร์ด** ใน Vivado Tcl Console:

```
tta_asm wcet_bench
```

จากนั้นกด Generate Bitstream แล้ว Program Device แล้วรัน:

```bash
py host/tools/wcet_board.py COM3
```
