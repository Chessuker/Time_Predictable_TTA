# Timing Model

สถานะ: **FROZEN — Phase 0** (2026-10-02) ผ่าน review แล้ว ใช้เป็นสัญญาของ Phase 1 เป็นต้นไป

**การแก้หลัง freeze:** ห้ามแก้เงียบ ๆ ทุกการเปลี่ยนต้องแก้ timing_model.md กับ isa.md ให้ตรงกันใน commit เดียว ระบุเหตุผลและวันที่ไว้ที่จุดที่แก้ และ ISS, assembler, WCET tool และ RTL ต้องอัปเดตตาม

**บันทึกการแก้หลัง freeze**
- 2026-10-02: ตัวอย่างโค้ดใช้ `#label` สำหรับ label ที่ใช้เป็นค่า ตาม [asm_syntax.md](asm_syntax.md) แก้เฉพาะ syntax ของตัวอย่าง ไม่เปลี่ยน semantics หรือตัวเลข cycle
- 2026-10-05: §1 เรื่อง trace เพิ่มว่า trap และ halt มี record ของตัวเอง และย้ายรูปแบบเต็มไปไว้ที่ toolchain_formats.md §6 ไม่เปลี่ยน semantics

เอกสารนี้คือสัญญาด้านเวลาที่ assembler, ISS, WCET tool และ RTL ต้องทำตามตรงกันทุก cycle ถ้า RTL ทำตามไม่ได้ ให้กลับมาแก้เอกสารนี้ก่อน ห้ามแก้ ISS ให้ตรงกับ RTL เงียบ ๆ

ที่มาของ decision แต่ละข้อ (A1–A6, B1–B4, P) อยู่ใน [reading_notes_tier1.md](reading_notes_tier1.md) ส่วน "Decision ที่กระทบ Phase 0"

เอกสารนี้ใช้แทน rule ข้อ 5 และแถว TMR ในตาราง FU ของ §4 ในแผน (`t_delay_until` แบบ absolute) ซึ่งถูกแทนด้วย Timing Unit แบบ anchor

**ไม่อยู่ในเอกสารนี้** (ดู [isa.md](isa.md)): เลข port ID, encoding, bit layout ของ `tmr.flags` และตาราง latency ของ FU ฉบับเต็ม ค่า latency ที่ตัดสินแล้ว (L = 1 ยกเว้น MUL = 2) สรุปไว้ใน §7

spec ในเอกสารนี้ต้องไม่ผูกกับ primitive ของ vendor ใด ถ้าจะพูดถึง FPGA ให้เขียนเป็นหมายเหตุของ implementation

---

## 1. นิยามพื้นฐาน

### Cycle และ `now`

- 1 cycle = 10 ns ที่ 100 MHz
- `now` คือ cycle counter 64 bit ภายใน TMR มีค่า 0 ตอน reset และเพิ่มทีละ 1 ทุก cycle โดยไม่มีข้อยกเว้น (ไม่หยุดตอน stall หรือ trap)
- ในเอกสารนี้ "cycle `c`" หมายถึง cycle ที่ `now == c`

### Move execute ที่ cycle ไหน

- "move `m` execute ที่ cycle `c`" หมายถึงค่าของ src ถูกอ่านและเขียนลง dst ใน cycle `c`
- move อ่านค่าของ state ณ ต้น cycle `c` และผลที่เขียนมองเห็นได้ตั้งแต่ cycle `c + 1`
- แต่ละ cycle มี move execute ได้มากที่สุด 1 ตัว cycle ที่ไม่มี move execute เรียกว่า bubble (มาจาก jump penalty, stall ของ timing move หรือ trap entry)
- trace ของ ISS และ RTL มี 1 record ต่อ move ที่ execute จริง bubble และ move ที่ถูก squash ไม่มี record ของ move แต่ trap และ halt มี record ของตัวเอง รูปแบบเต็มอยู่ใน [toolchain_formats.md](toolchain_formats.md) §6

### ค่าคงที่

| สัญลักษณ์ | ค่า | ความหมาย |
|---|---|---|
| D | 1 | move ถัดจาก timing move รันที่ `max(c, T) + D` (R5) |
| P | 2 | bubble ต่อ control transfer ที่ taken 1 ครั้ง (R4) |
| H | 2 (= P) | bubble ระหว่าง cycle ที่ trap ถึง move แรกของ handler (R6) |
| R | 2 (= P) | move แรกหลังปล่อย reset execute ที่ cycle R มอง reset เป็น jump ไป address 0 ที่ cycle −1 จึงใช้กลไกเดียวกับ R4 ไม่มี reset path พิเศษ *(freeze 2026-09-29)* |

P = 2 มาจาก fetch pipeline 3 stage (fetch จาก on-chip synchronous SRAM, decode, execute) RTL จะ implement อย่างไรก็ได้ ขอแค่ได้ตัวเลขตามตารางนี้

### ค่าหลัง reset *(freeze 2026-09-29)*

state ทุกตัวที่ software อ่านได้มีค่า 0 หลัง reset ได้แก่ `r0`–`r15`, output และ operand port ของทุก FU, `pc.cond`, และ state ของ TMR (`now`, `anchor`, `deadline`, `armed`, `late`, `trapped`) เพื่อให้ ISS กับ RTL ตรงกันตั้งแต่ cycle แรก

---

## 2. Timing rules

### R1. Move ละ 1 cycle

ใน straight-line code ถ้า move `m` execute ที่ `c` move ถัดไปจะ execute ที่ `c + 1` ข้อยกเว้นมีแค่ R4, R5, R6

### R2. Latency ของ FU

- ถ้า move ที่ cycle `c` เขียน trigger port ของ FU ที่มี latency L ผลจะอ่านได้โดย move ที่ cycle `≥ c + L` และคงค่าไว้จนกว่าจะ trigger ครั้งถัดไป
- port ที่ไม่ใช่ trigger (operand port เช่น `alu.a`, `alu.op` และ register `r0`–`r15`) เป็น state ธรรมดา เขียนที่ `c` อ่านค่าใหม่ได้ที่ `c + 1`
- ดังนั้น FU ที่ L = 1 อ่านผลได้ที่ move ถัดไปทันที ไม่มี hazard ใน machine ที่ทำ 1 move ต่อ cycle
- **L เป็นระยะห่างขั้นต่ำเป็นจำนวน cycle** จาก move ที่ trigger ถึง move ที่อ่านผล ไม่ใช่เวลาที่ฮาร์ดแวร์ stall รอ core **ไม่มี interlock และไม่ stall เพราะ latency เลย** โปรแกรมต้องวาง move อื่นคั่นอย่างน้อย L − 1 ตัวเอง (เช่น MUL ต้องมี 1 move คั่น) และ assembler ตรวจตาม R3
- ข้อสังเกต: แผน §4 ให้ REG, IO, TELEM มี latency 0 และ ALU มี latency 1 ภายใต้นิยามนี้ทั้งสองแบบอ่านได้ที่ `c + 1` เหมือนกันและไม่มีความต่างที่สังเกตได้ เอกสารนี้และ isa.md §10 จึงไม่ใช้ L = 0 port ที่เป็น state ธรรมดาเขียนเป็น "—" แทน

### R3. อ่านก่อนครบ latency = โปรแกรมผิด

- การอ่าน output ของ FU ที่ cycle `r` ถูกต้องก็ต่อเมื่อ trigger ครั้งล่าสุดก่อน `r` เกิดที่ `c` โดย `r ≥ c + L`
- assembler ตรวจแบบ static บนทุก path ของ CFG และ **reject** ไม่ใช่ warning
- ISS ตรวจซ้ำตอนรัน ถ้าเจอให้หยุดพร้อม error ฮาร์ดแวร์ไม่ต้องตรวจ
- MUL มี L = 2 (§7) กฎนี้จึงมีผลตั้งแต่ Phase 1 FU อื่นที่ L = 1 ไม่มีทางละเมิดกฎนี้ใน machine ที่ทำ 1 move ต่อ cycle

### R4. Control transfer

- ถ้า jump ที่ **taken** execute ที่ `c` move แรกของ target execute ที่ **`c + 1 + P`** (bubble ที่ `c + 1` และ `c + 2`)
- ถ้า conditional jump ที่ **not taken** execute ที่ `c` move ถัดไป execute ที่ `c + 1` เหมือน move ปกติ
- `pc.t_jump` taken เสมอ, `pc.t_jz` taken เมื่อ `pc.cond == 0`, `pc.t_jnz` taken เมื่อ `pc.cond != 0` โดยใช้ค่าของ `pc.cond` ณ ต้น cycle `c` (move ก่อนหน้าเขียนได้ทันตาม R2)
- ค่า P ไม่ขึ้นกับ target, ทิศทาง หรือประวัติการ jump (ไม่มี branch prediction)
- call และ return มีเวลาเท่ากับ jump ที่ taken รายละเอียดของ `pc.link` อยู่ใน isa.md

### R5. Timing Unit (TMR)

**State ภายใน:** `now`, `anchor`, `deadline` (64 bit ทั้งหมด), `armed`, `late`, `trapped` (1 bit)

**ค่า `v` บน bus** เป็น 32 bit แบบ unsigned แล้ว zero-extend เป็น 64 bit ก่อนบวก ค่า `v` ใน immediate ใช้ได้ 0 ถึง 4,194,303 (assembler reject นอกช่วงนี้เมื่อ dst เป็น tmr port) ค่าที่ใหญ่กว่านี้ให้ส่งผ่าน register

**Operation** (move ที่ trigger execute ที่ cycle `c`)

| Port | ผลต่อ state (มองเห็นที่ `c + 1`) | target `T` | move ถัดไป execute ที่ |
|---|---|---|---|
| `tmr.t_sync` | `anchor ← c` | ไม่มี | `c + 1` |
| `tmr.t_advance` ← v | `anchor ← anchor + v`, `late ← (c > T)` | `anchor + v` (anchor เดิม) | `max(c, T) + D` |
| `tmr.t_wait` ← v | `late ← (c > T)` anchor ไม่เปลี่ยน | `anchor + v` | `max(c, T) + D` |
| `tmr.t_arm` ← v | `deadline ← anchor + v`, `armed ← 1` | ไม่มี | `c + 1` |
| `tmr.t_clear` | `armed ← 0` | ไม่มี | `c + 1` |

ค่าที่เขียนเข้า `t_sync` และ `t_clear` ไม่มีความหมาย (ใช้ `#0` ตามธรรมเนียม)

**ข้อสรุปจากตาราง**

- `t_advance` กับ `t_wait` เป็น **sync point** ถ้ามาทัน (`c ≤ T`) move ถัดไปรันที่ `T + 1` พอดีไม่ว่า `c` จะเป็นเท่าไร ถ้ามาสาย (`c > T`) ใช้ 1 cycle เท่า move ปกติ และ `late = 1`
- cycle `c + 1` ถึง `T` เป็น bubble (stall) ไม่มี move execute
- `late` หมายถึง sync point ล่าสุดมาถึงช้า เป็น non-sticky ทั้งสอง operation เขียนทับ
- `anchor` ขยับด้วย `t_advance` เท่านั้น (บวก `v` ทับตัวเอง) จึงไม่ drift แม้รอบก่อนจะสาย รอบที่สายไม่ทำให้ grid ของเวลาเลื่อน
- ถ้าสายเกิน 1 period target ของ `t_advance` รอบถัดไปก็ผ่านไปแล้วเช่นกัน โปรแกรมจะรันหลายรอบติดกันโดยไม่ stall และ `late = 1` ทุกรอบจนตามทัน

**Source port**

| Port | ค่าที่อ่านได้ที่ cycle `c` |
|---|---|
| `tmr.elapsed` | `min(c − anchor, 2^32 − 1)` เป็น 32 bit (saturate) |
| `tmr.flags` | `{late, armed, trapped}` layout ใน isa.md |

ตัวอย่าง: อ่าน `tmr.elapsed` เป็น move แรกหลังตื่นจาก `t_advance` ที่มาทัน ได้ค่า D = 1

### R6. Deadline และ trap

**Deadline**
- **เงื่อนไข:** ณ ต้น cycle `d` ถ้า `armed == 1` และ `d ≥ deadline` เกิด trap ที่ cycle `d` โดย `d` คือ cycle แรกที่เงื่อนไขเป็นจริง
- เพราะ `now` เพิ่มทีละ 1 และ `armed` ถูกตั้งก่อนถึง deadline ในกรณีปกติ trap จะเกิดที่ `d == deadline` พอดี ถ้า `t_arm` ตั้ง deadline ที่ผ่านไปแล้ว trap เกิดที่ `c + 1` ทันทีแทนที่จะไม่ trap เลย *(freeze 2026-09-29)*

**Illegal operation** (isa.md §11) เกิด trap ที่ cycle `d` ซึ่งเป็น cycle ที่ move ที่ illegal จะ execute

**ลำดับการตัดสินในแต่ละ cycle `d`** ISS และ RTL ต้องใช้ลำดับนี้เหมือนกัน
1. ถ้า core อยู่ในสถานะ halt ไม่ตรวจอะไรและไม่มี trap ใด ๆ (isa.md §9)
2. ตรวจ deadline ด้วย state ณ ต้น cycle ถ้าเป็นจริง เกิด trap cause 1 และ move ใน cycle นั้น (ถ้ามี) ถูก squash โดย**ไม่ถูก execute และไม่ถูกตรวจ legality** deadline จึงชนะเสมอเมื่อเกิดพร้อม illegal operation
3. ถ้าไม่เกิด deadline trap และมี move ใน cycle นั้น ตรวจ legality ถ้า illegal เกิด trap ตาม cause ใน isa.md §9 และ move นั้นถูก squash
4. ถ้าไม่เกิด trap move execute ตามปกติ

**สิ่งที่เกิดเมื่อ trap ที่ cycle `d`** (ทุก cause)
- move ใน cycle `d` (ถ้ามี) ถูก squash ไม่มีผลใด ๆ ต่อ state
- `trap.epc ← PC ของ move ถัดไปในลำดับโปรแกรมที่ยังไม่ได้ execute` นิยามนี้เป็นนิยามเดียวของ `trap.epc` ครอบทุกกรณี
  - cycle `d` มี move อยู่ (รวมกรณี illegal) → move นั้น เพราะถูก squash จึงนับว่ายังไม่ได้ execute
  - อยู่ใน stall ของ `t_wait` → move หลัง `t_wait`
  - อยู่ใน bubble ของ jump → move แรกของ target
- `trap.cause` ถูกตั้ง, `armed ← 0`, `trapped ← 1`
- move แรกของ handler execute ที่ **`d + 1 + H`** ยกเว้นกรณี halt ตาม isa.md §9 (handler = 0 หรือ double fault)
- trap แทรก stall ของ `t_wait` ได้ ส่วน stall ของ `t_advance` ไม่มีทางถูกแทรกเพราะ assembler ห้าม `t_advance` ขณะ armed (§4)
- ผลที่ตามมา: **`t_clear` ต้อง execute ที่ cycle ≤ deadline − 1** ถ้า execute ที่ deadline พอดีจะถูก squash และเกิด trap

### R7. Time base

- time base คือ `now` ตัวเดียว ไม่มี wall clock ภายนอก จึงไม่มี CDC ในเส้นทางเวลา
- **สมมติฐาน:** `now`, `anchor`, `deadline` ไม่วนรอบภายใน 2^64 cycle (≈ 5,845 ปีที่ 100 MHz) ISS และ RTL ไม่ต้องจัดการกรณีวน
- `elapsed` ถูกตัดเหลือ 32 bit จะถึงเพดานเมื่อห่างจาก anchor เกิน 2^32 − 1 cycle (≈ 42.9 s) เช่นก่อน `t_sync` ครั้งแรก ค่าที่ติดเพดานบอกชัดว่าผิด ต่างจากค่าที่วนแล้วซึ่งดูเหมือนถูก
- กรณี `now < anchor` ตอนอ่าน `elapsed` เกิดได้ทางเดียวคือ trap แทรก stall ของ `t_advance` ซึ่ง §4 ห้ามไว้แล้ว ถ้า ISS เจอกรณีนี้ให้หยุดพร้อม error

### สมมติฐานเรื่อง memory

- memory ทุกตัวที่ core เข้าถึง (code ROM และ MEM scratchpad) เป็น **on-chip synchronous SRAM** latency คงที่ตาม R2 ไม่ขึ้นกับ address, ประวัติการเข้าถึง หรือ state ใด ๆ (implementation บน Arty ใช้ BRAM ส่วน ASIC ใช้ SRAM macro)
- ไม่มี cache, DRAM, flash หรือ storage นอกชิปอยู่ใน path ของโปรแกรม rule ทุกข้อและ claim ว่า WCET tight 0 cycle ตั้งอยู่บนสมมติฐานนี้
- อุปกรณ์ที่ช้าหรือ latency ไม่คงที่ต่อผ่าน FIFO แบบไม่ block เหมือน TELEM กับ UART core ไม่ต้องรออุปกรณ์เหล่านี้

> **อาจเพิ่มในอนาคต:** ถ้าต้องใช้ DDR3 บนบอร์ด (256 MB) หรือ storage นอกชิป ทางเลือกคือ (1) มองเป็น I/O แบบไม่ block ผ่าน FIFO, (2) ใช้ worst-case latency ที่พิสูจน์ได้ใน W แล้วให้ sync point ดูดซับ jitter ซึ่งทำให้ segment นั้นไม่ tight 0 cycle อีกต่อไป, หรือ (3) ใช้ controller ที่ออกแบบให้ predictable เช่น PRET DRAM controller (Liu thesis) หรือ memory tree ของ T-CREST/Patmos (reading list ข้อ 13, 13b) เมื่อเพิ่มต้องแก้สมมติฐานข้อนี้และระบุ segment ที่ได้รับผลกระทบ

---

## 3. WCET model

สัญญาระหว่าง WCET tool กับ timing rule ข้างบน

### Segment และ budget

- sync point (`t_advance`, `t_wait`) ตัดโปรแกรมเป็น **segment** WCET tool ตรวจทีละ segment
- segment เริ่มที่ move แรกหลัง sync point และจบที่ sync point ถัดไป (ไม่รวมตัวมันเอง)
- **W** ของ segment = จำนวน cycle บน path ที่ยาวที่สุด นับทุก move ที่ execute (รวม `t_arm`, `t_clear` และ move ของ jump) บวก P ต่อ jump ที่ taken บน path นั้น **ไม่รวม** sync point ที่ปิด segment และไม่รวมเวลา stall
- **Δ** คือ budget ของ segment ค่าที่เขียนลง timing move คือ `v = Δ + D` แล้วตรวจว่า **`W ≤ Δ`**
- ทำไมถึงถูก: segment ที่เริ่มหลัง sync point ซึ่ง target คือ `anchor + v_prev` จะเริ่มที่ `anchor + v_prev + 1` sync point ปิด segment จึง execute ที่ `anchor + v_prev + 1 + W` และไม่สายเมื่อ `≤ anchor + v_prev + Δ + 1` คือ `W ≤ Δ`

### หลาย sync point ใน 1 period

`v` ของ `t_wait` นับจาก anchor เสมอ ไม่ได้นับจาก sync point ก่อนหน้า

- `v_0 = 0` (ที่ `t_advance`)
- `v_k = v_{k−1} + Δ_k + D`
- segment สุดท้ายก่อน `t_advance` ของรอบถัดไปมี budget `period − v_last − D`

### กฎของ CFG

- task-level WCET ใน Phase 1 วัดจาก wake ถึง `t_clear`
- cycle ใน CFG ทุกวงต้องมี `@loop_bound` หรือผ่าน sync point อย่างน้อย 1 จุด ถ้าไม่มีทั้งสองอย่างให้ error
- WCET tool ส่ง **witness path** (ลำดับ basic block ของ path ที่ยาวที่สุด) ออกมาพร้อมตัวเลข เพื่อให้หา input ที่พาไป path นั้นบน ISS ได้ ไม่มี path annotation ใน Phase 1

---

## 4. Static check ที่ assembler ต้องทำ

| # | ตรวจอะไร | มาจาก |
|---|---|---|
| S1 | อ่าน output ของ FU ก่อนครบ latency บน path ใดก็ตาม | R3 |
| S2 | immediate ที่ส่งเข้า tmr port ต้องอยู่ใน 0..4,194,303 | R5, A2 |
| S3 | ไม่มี path ใดมาถึง `t_advance` ขณะที่ยัง armed (dataflow 1 bit ไม่ดูเงื่อนไข branch) | B1 |
| S4 | `t_arm` และ `t_clear` อยู่ใน function เดียวกัน | B1 (Phase 1) |
| S5 | CFG เป็น structured ไม่มี recursion และไม่มี indirect jump นอกจาก return | B1 |
| S6 | cycle ใน CFG ทุกวงมี `@loop_bound` หรือผ่าน sync point | B3 |
| S7 | ไม่มี move ที่ใช้ `trap.epc` เป็น src ของ jump หรือ call (Phase 1 ไม่มี return จาก trap) | isa.md §9 |

S1 ถึง S5 และ S7 เป็นงานของ assembler ส่วน S6 และการตรวจ `W ≤ Δ` เป็นงานของ WCET tool

---

## 5. ตัวอย่างนับ cycle ด้วยมือ

ใช้ latency ALU, CMP, MEM = 1 และ MUL = 2 (§7) กับ syntax `src -> dst` ตามแผน §5 cycle ในตัวอย่างนับจาก move แรกของโปรแกรมเป็น 0

### ตัวอย่าง 1: straight-line กับ latency

คำนวณ `r3 = (5 + 7) × 3` และ `r4 = 10`

| cycle | move | หมายเหตุ |
|---|---|---|
| 0 | `#5 -> r1` | |
| 1 | `#7 -> r2` | |
| 2 | `#ADD -> alu.op` | |
| 3 | `r1 -> alu.a` | |
| 4 | `r2 -> alu.t_b` | trigger ALU (L = 1) |
| 5 | `alu.out -> mul.a` | อ่านได้ตั้งแต่ 4 + 1 = 5 ✓ |
| 6 | `#3 -> mul.t_b` | trigger MUL (L = 2) |
| 7 | `#10 -> r4` | move ที่ไม่ขึ้นกับผลคูณ ใส่ในช่วงรอ latency |
| 8 | `mul.out -> r3` | อ่านได้ตั้งแต่ 6 + 2 = 8 ✓ |

**รวม 9 cycle** move ถัดไปของโปรแกรมรันที่ cycle 9, `r3 = 36` และ `r4 = 10`

- ถ้าย้าย `mul.out -> r3` ขึ้นมาที่ cycle 7 จะอ่านก่อน 6 + 2 = 8 assembler ต้อง reject (S1)
- ถ้าไม่มีงานอื่นที่ไม่ขึ้นกับผลคูณ ต้องใส่ move ที่ไม่มีผล (nop) ที่ cycle 7 เวลารวมยังเป็น 9 cycle เท่าเดิม latency ที่เพิ่มจึงเสียเวลาไม่เกิน 1 cycle ต่อการคูณ และเป็นค่าคงที่ที่ WCET tool นับได้

### ตัวอย่าง 2: loop และ branch

**2a. Loop นับถอยหลัง N รอบ**

```asm
        #N          -> r1            ; 0
        #SUB        -> alu.op        ; 1  ตั้ง op ครั้งเดียว ค่าค้างอยู่ใน operand port
@loop_bound N
loop:   r1          -> alu.a         ; a
        #1          -> alu.t_b       ; a+1  trigger
        alu.out     -> r1            ; a+2
        r1          -> pc.cond       ; a+3
        #loop       -> pc.t_jnz      ; a+4  taken ถ้า r1 != 0
after:  ...
```

- body 1 รอบ = 5 move
- N − 1 รอบแรก jump taken: 5 + P = 7 cycle ต่อรอบ
- รอบสุดท้าย not taken: 5 cycle
- `after` execute ที่ **2 + 7(N − 1) + 5 = 7N**

ตรวจด้วยมือที่ N = 2:

| cycle | เหตุการณ์ |
|---|---|
| 0–1 | setup |
| 2–6 | รอบที่ 1, `t_jnz` ที่ 6 taken (r1 = 1) |
| 7–8 | bubble (P = 2) |
| 9–13 | รอบที่ 2, `t_jnz` ที่ 13 not taken (r1 = 0) |
| 14 | `after` ✓ (7 × 2 = 14) |

**2b. if/else ที่ขึ้นกับข้อมูล**

```asm
        r1          -> pc.cond       ; 0
        #else       -> pc.t_jz       ; 1  taken ถ้า r1 == 0
        #10         -> r2            ; then
        #join       -> pc.t_jump     ; taken เสมอ
else:   #20         -> r2
join:   ...
```

| path | cycle ที่ move execute | `join` execute ที่ |
|---|---|---|
| r1 ≠ 0 (then) | 0, 1 (not taken), 2, 3 (taken), bubble 4–5 | **6** |
| r1 = 0 (else) | 0, 1 (taken), bubble 2–3, 4 | **5** |

WCET จนถึง `join` = 6 cycle witness path คือ then (input ใดก็ได้ที่ r1 ≠ 0) ทั้งสอง path ต่างกัน 1 cycle ซึ่งเป็น jitter ที่ตัวอย่าง 3 ใช้ `t_wait` กำจัด

### ตัวอย่าง 3: timed control loop

โครงตามแผน §6 ค่าที่ใช้: period = 100,000, sense-to-actuate = 1,000, deadline = 2,000 cycle หลัง anchor, control law มี WCET K = 623 cycle (รวม P ของ jump ภายในแล้ว), telemetry 6 move

```asm
        #0          -> tmr.t_sync            ; anchor ← now
loop:   #100000     -> tmr.t_advance         ; anchor += period, stall
        io.encoder  -> r1                    ; sense
        #2000       -> tmr.t_arm             ; deadline = anchor + 2000
        ...                                  ; control law, K cycle
        #1000       -> tmr.t_wait            ; stall จน anchor + 1000
        r5          -> io.pwm_cmd            ; actuate
        #0          -> tmr.t_clear
        ...                                  ; telemetry 6 move
        #loop       -> pc.t_jump
```

**Budget**

| segment | จาก → ถึง | W | v | Δ = v − D | ผ่าน? |
|---|---|---|---|---|---|
| 1 | หลัง `t_advance` → `t_wait` | 1 + 1 + 623 = 625 | 1,000 | 999 | ✓ เหลือ 374 |
| 2 | หลัง `t_wait` → `t_advance` | 1 + 1 + 6 + 1 + P = 11 | (100,000) | 100,000 − 1,000 − 1 = 98,999 | ✓ |

**Timeline 1 period** (A = anchor หลัง `t_advance` ของรอบนั้น, worst case K = 623)

| cycle | เหตุการณ์ |
|---|---|
| A + 1 | ตื่น, `io.encoder -> r1` (sense) |
| A + 2 | `t_arm` → deadline = A + 2000 |
| A + 3 … A + 625 | control law |
| A + 626 | `t_wait` target A + 1000 → มาทัน, `late ← 0` |
| A + 627 … A + 1000 | stall |
| A + 1001 | `r5 -> io.pwm_cmd` (actuate) |
| A + 1002 | `t_clear` (≤ A + 1999 ✓) |
| A + 1003 … A + 1008 | telemetry |
| A + 1009 | `#loop -> pc.t_jump` |
| A + 1010 … A + 1011 | bubble |
| A + 1012 | `t_advance` target A + 100000 → มาทัน, stall ถึง A + 100000 |
| A + 100001 | ตื่นรอบถัดไป, sense |

ผลที่ต้องได้:

- sense อยู่ที่ A + 1 และ actuate อยู่ที่ A + 1001 ทุกรอบ ไม่ว่า control law จะวิ่ง path ไหน ตราบที่ K ≤ 997 (W ≤ 999) **sense-to-actuate = v ของ `t_wait` = 1,000 cycle พอดี jitter 0**
- ระยะ sense ถึง sense ของสองรอบติดกัน = period = 100,000 cycle พอดี
- `t_advance` ไม่เจอ armed เพราะ `t_clear` อยู่ก่อน jump บนทุก path (S3 ผ่าน)
- loop หลักไม่มี `@loop_bound` แต่ผ่าน sync point (S6 ผ่าน)
- รอบแรก: `t_sync` ที่ c0 ทำให้ anchor = c0, `t_advance` ที่ c0 + 1 stall ถึง c0 + 100000 แล้วตื่นที่ c0 + 100001

**กรณีสาย (K = 1,200 จากบั๊ก)**

- W ของ segment 1 = 1,202 > 999 WCET tool ต้องเตือนก่อนรันจริง
- ถ้ายังรัน: `t_wait` execute ที่ A + 1203 > A + 1000 → `late ← 1`, ไม่ stall
- actuate ที่ A + 1204 (สาย 203 cycle), `t_clear` ที่ A + 1205 ยังไม่ถึง deadline จึงไม่ trap
- anchor ของรอบถัดไปยังเป็น A + 100000 ตามเดิม `t_advance` มาทันและ `late ← 0` รอบถัดไปกลับมาตรงเวลาเอง

**กรณี trap (K = 2,100)**

- control law ยังรันอยู่ที่ cycle A + 2000 (move ของ control law อยู่ที่ A + 3 … A + 2102)
- cycle A + 2000: trap, move ที่ cycle นั้นถูก squash, `trap.epc` = PC ของ move นั้น, `armed ← 0`, `trapped ← 1`
- move แรกของ handler execute ที่ A + 2000 + 1 + H = **A + 2003**

---

## 6. หมายเหตุสำหรับ formal verification (Phase 3)

- property ของ TMR เป็น local ต่อยูนิต บวกสัญญาณ stall และ trap ที่ส่งไป PC
- k-induction ไม่รู้ว่าตัวนับ 64 bit ไม่วนในทางปฏิบัติ อาจเริ่มพิสูจน์จาก state ที่ `now` ใกล้ 2^64 − 1 แล้วได้ counterexample ปลอมตอนวนกลับ 0 ต้องใส่ assumption ว่า `now` ไม่วน หรือ invariant ที่ผูก `now` กับจำนวน cycle นับจาก reset
- property หลักที่ต้องพิสูจน์: trap เกิดที่ `now == deadline` พอดีเมื่อ armed, move หลัง sync point ที่มาทันรันที่ `T + 1` พอดี, ไม่มี move ใด execute ระหว่าง stall

---

## 7. ประเด็นที่ยังเปิด

### เพิ่มในเอกสารนี้

freeze ครบทั้ง 5 ข้อแล้ว (2026-09-29)

ข้อเหล่านี้ไม่ได้อยู่ใน decision A/B แต่ต้องกำหนดเพื่อให้ spec ครบ

1. **[freeze] R = 2** R = 1 ทำได้แต่ต้อง fetch ระหว่าง reset และมี reset path พิเศษ แลกกับ 10 ns ครั้งเดียวตอนเปิดเครื่อง ซึ่งไม่กระทบ period, jitter หรือ WCET
2. **[freeze] Trap ใช้ `≥` ไม่ใช่ `==`** `t_arm` ที่ตั้ง deadline ในอดีตจะ trap ที่ `c + 1` ทันทีแทนที่จะไม่ trap เลย
3. **[freeze] นิยาม `trap.epc`** แบบรวมทุกกรณีตาม R6 (move ถัดไปที่ยังไม่ได้ execute)
4. **[freeze] ค่าหลัง reset** ทุก state ที่ software อ่านได้เป็น 0 (ดู §1)
5. **[freeze] นิยาม latency ใน R2** (อ่านได้ที่ `c + L`) ทำให้ latency 0 กับ 1 ในแผน §4 มีผลเท่ากัน ค่า L ของทุก FU เป็น 1 ยกเว้น MUL = 2 (ดูหัวข้อ MUL ด้านล่าง)

### MUL *(ตัดสิน 2026-09-30, รอลง isa.md)*

- **Semantics:** signed 32 × 32 และ `mul.out` ให้ผล 32 bit ล่าง แบบเดียวกับ `mul` ของ RISC-V ส่วน 32 bit บนยังไม่มี port แต่กันเลข port ID ไว้สำหรับ `mul.out_hi` แบบเดียวกับ A5
- **L = 2**
- **เหตุผลจาก software:** [plant model](../host/plant_model/README.md) แสดงว่าผลคูณและผลรวมทุกตัวใน controller อยู่ใน 32 bit ผล 32 bit ล่างจึงเป็นค่าที่ถูกต้องครบ
- **เหตุผลที่เลือก L = 2:** ความกว้างและ latency ต้องไม่ผูกกับ primitive ของ vendor ใด ถ้า L = 1 ทุก implementation ต้องคูณ 32 × 32 ให้เสร็จใน cycle เดียวรวมกับ src mux ซึ่งเสี่ยงบน FPGA ที่ 100 MHz และยากขึ้นอีกบน ASIC ที่ clock สูงกว่า L = 2 ยอมให้มี pipeline register กลางตัวคูณ ส่วนต้นทุนไม่เกิน 1 cycle ต่อการคูณ (ตัวอย่าง 1) และยังเป็นค่าคงที่ จึงไม่ลด predictability
- ตาราง FU ในแผน §4 (`out_lo`, `out_hi`, latency 1) ถูกแทนด้วยหัวข้อนี้

### ตัดสินใน isa.md แล้ว (2026-10-02)

- เลข port ID ของทุก FU และ block `0xB_` ที่กันไว้สำหรับ slot ของ TMR (A5) → isa.md §2
- layout ของ `tmr.flags` และไม่ทำ `elapsed_sat` (A6) → isa.md §7
- ตาราง latency ของทุก FU → isa.md §10
- nop = `#0 -> null` → isa.md §3
- `pc.t_call` และ `pc.link` → isa.md §6
- Phase 1 ไม่มี return จาก trap (fail-stop) และ static check S7 → isa.md §9

### เลื่อนไปทีหลัง

- `late_seen` (B2) ไม่ทำใน Phase 1
- `tmr.now_lo` / `tmr.now_hi` สำหรับ debug และ time sync ยังไม่อยู่ใน port map
- TELEM snapshot counter ตัดสินที่ Phase 5
- memory นอกชิป (DDR3, flash, storage) ดูหมายเหตุใน §2 สมมติฐานเรื่อง memory
