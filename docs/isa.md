# TTA ISA Specification

สถานะ: **FROZEN — Phase 0** (2026-10-02) ผ่าน review แล้ว ใช้เป็นสัญญาของ Phase 1 เป็นต้นไป การแก้หลัง freeze ใช้กติกาเดียวกับ [timing_model.md](timing_model.md) ข้อเสนอระหว่างร่างทุกข้อได้รับการยืนยันแล้ว รายการอยู่ใน §12

เอกสารนี้กำหนดสิ่งที่ software มองเห็น ได้แก่ encoding, port และ semantics ส่วนเวลาของทุกอย่างอยู่ใน [timing_model.md](timing_model.md) ซึ่งถือเป็นสัญญาเดียวกัน ถ้าสองเอกสารขัดกันให้ถือว่าเป็นบั๊กของ spec

spec นี้ไม่ผูกกับ primitive ของ vendor ใด อะไรที่เป็นเรื่องของ FPGA เขียนเป็นหมายเหตุของ implementation เท่านั้น

---

## 1. Instruction Format

ทุก instruction คือ move 1 ตัว กว้าง 32 bit

| Bits | Field | ความหมาย |
|---|---|---|
| 31:24 | `dst` | port ID ปลายทาง |
| 23 | `imm` | 1 = src เป็น immediate |
| 22:0 | `src` / `imm_val` | ถ้า `imm = 0`: port ID ต้นทางอยู่ใน bit 7:0 และ bit 22:8 ต้องเป็น 0 / ถ้า `imm = 1`: immediate 23 bit แบบ sign-extend เป็น 32 bit |

- ช่วงของ immediate คือ −4,194,304 ถึง 4,194,303 ค่าคงที่ที่ใหญ่กว่านี้ใช้สอง move ผ่าน shift ใน ALU หรือโหลดจาก literal pool ใน MEM
- immediate ที่ส่งเข้า tmr port ใช้ได้ 0 ถึง 4,194,303 เท่านั้น (timing_model S2)
- address ของ instruction เป็น **word address** move แรกหลัง reset อยู่ที่ address 0

**ตัวอย่าง encoding** (port ID ตาม §2)

| Assembly | Hex |
|---|---|
| `#1000 -> r1` | `0x118003E8` |
| `#-1 -> r2` | `0x12FFFFFF` |
| `r1 -> alu.a` | `0x20000011` |
| `alu.out -> r3` | `0x13000023` |
| `nop` (= `#0 -> null`) | `0xFF800000` |

---

## 2. Port Map

port ID กว้าง 8 bit ใช้ namespace เดียวกันทั้ง src และ dst แบ่งเป็น block ละ 16 ID โดย **nibble บนคือ FU** และ nibble ล่างคือ port ใน FU นั้น

| Block | FU | หมายเหตุ |
|---|---|---|
| `0x0_` | (reserved) | **`0x00` เป็น illegal เสมอ** word ที่เป็น 0 ทั้งหมดจึง trap ทันที ถ้าโปรแกรมวิ่งเข้า memory ที่ยังไม่ได้เขียน |
| `0x1_` | REG | `r0`–`r15` |
| `0x2_` | ALU | |
| `0x3_` | MUL | |
| `0x4_` | CMP | |
| `0x5_` | PC | |
| `0x6_` | MEM | |
| `0x7_` | TMR slot 0 | |
| `0x8_` | TRAP | |
| `0x9_` | IO | |
| `0xA_` | TELEM | |
| `0xB_` | TMR slot 1+ | กันไว้ตาม A5 ยังไม่ใช้ |
| `0xC_`–`0xE_` | (reserved) | FU ในอนาคต |
| `0xF_` | special | `0xFF` = `null` |

**ตาราง port ทั้งหมด** (R = อ่านเป็น src ได้, W = เขียนเป็น dst ได้, T = trigger)

| ID | Port | ทิศ | ความหมาย |
|---|---|---|---|
| `0x10`–`0x1F` | `r0`–`r15` | R/W | general register ไม่มีตัวไหน hardwire เป็น 0 |
| `0x20` | `alu.a` | W | operand a |
| `0x21` | `alu.op` | W | opcode (§4) |
| `0x22` | `alu.t_b` | W/T | operand b แล้วคำนวณ |
| `0x23` | `alu.out` | R | ผล |
| `0x30` | `mul.a` | W | operand a |
| `0x31` | `mul.t_b` | W/T | operand b แล้วคูณ |
| `0x32` | `mul.out` | R | 32 bit ล่างของผลคูณ signed |
| `0x33` | *(`mul.out_hi`)* | — | กันไว้ ยังไม่มี |
| `0x40` | `cmp.a` | W | operand a |
| `0x41` | `cmp.t_b` | W/T | operand b แล้วเปรียบเทียบ |
| `0x42` | `cmp.eq` | R | 1 ถ้า a = b ไม่งั้น 0 |
| `0x43` | `cmp.lt` | R | 1 ถ้า a < b แบบ signed |
| `0x44` | `cmp.ltu` | R | 1 ถ้า a < b แบบ unsigned |
| `0x50` | `pc.cond` | W | ค่าที่ `t_jz`/`t_jnz` ใช้ตัดสิน |
| `0x51` | `pc.t_jump` | W/T | jump ไป address ที่เขียน |
| `0x52` | `pc.t_jz` | W/T | jump ถ้า `pc.cond == 0` |
| `0x53` | `pc.t_jnz` | W/T | jump ถ้า `pc.cond != 0` |
| `0x54` | `pc.t_call` | W/T | `link ← address ถัดไป` แล้ว jump |
| `0x55` | `pc.link` | R/W | return address |
| `0x60` | `mem.data_in` | W | ข้อมูลสำหรับ store |
| `0x61` | `mem.t_load` | W/T | โหลด word จาก address ที่เขียน |
| `0x62` | `mem.t_store` | W/T | เก็บ `data_in` ลง address ที่เขียน |
| `0x63` | `mem.data_out` | R | ผลของ load |
| `0x70` | `tmr.t_sync` | W/T | `anchor ← now` |
| `0x71` | `tmr.t_advance` | W/T | `anchor += v` แล้ว stall |
| `0x72` | `tmr.t_wait` | W/T | stall จน `anchor + v` |
| `0x73` | `tmr.t_arm` | W/T | `deadline ← anchor + v`, armed |
| `0x74` | `tmr.t_clear` | W/T | disarm |
| `0x75` | `tmr.elapsed` | R | `now − anchor` saturate 32 bit |
| `0x76` | `tmr.flags` | R | §7 |
| `0x77`–`0x7F` | — | — | กันไว้ (เช่น `now_lo`/`now_hi` ในอนาคต) |
| `0x80` | `trap.handler` | R/W | address ของ handler, 0 = ไม่มี handler |
| `0x81` | `trap.cause` | R | §9 |
| `0x82` | `trap.epc` | R | PC ของ move ที่ถูก squash |
| `0x83` | `trap.t_halt` | W/T | หยุด core |
| `0x90` | `io.encoder` | R | encoder count (signed 32 bit) |
| `0x91` | `io.pwm_cmd` | W | คำสั่ง PWM (signed, ±2047 ใช้ได้) |
| `0xA0` | `telem.t_push` | W/T | ใส่ word ลง FIFO ไม่ block |
| `0xA1` | `telem.drops` | R | จำนวน word ที่ถูกทิ้งเพราะ FIFO เต็ม |
| `0xFF` | `null` | W | ค่าที่เขียนถูกทิ้ง ใช้ทำ nop |

ID ที่ไม่อยู่ในตารางเป็น illegal ทั้งหมด

---

## 3. Function Units

หลักที่ใช้ทุก FU
- operand port (ไม่ใช่ trigger) เป็น state ธรรมดา ค่าคงอยู่จนกว่าจะถูกเขียนทับ จึงเขียนครั้งเดียวแล้ว trigger ซ้ำได้หลายครั้ง (เช่นตั้ง `alu.op` นอก loop)
- trigger port ใช้ค่าที่เขียนเป็น operand ตัวสุดท้ายแล้วเริ่มคำนวณ
- output ของ FU คงค่าไว้จนกว่าจะ trigger ครั้งถัดไป (timing_model R2)
- ทุก state ที่ software อ่านได้เป็น 0 หลัง reset (timing_model §1)

**`null`** รับค่าได้ทุกค่าแล้วทิ้ง ไม่มีผลใด ๆ **nop** คือ `#0 -> null` ใช้ 1 cycle เหมือน move ทั่วไป assembler รับ `nop` เป็น mnemonic

**IO** เชื่อมกับ plant ใน HIL demo
- `io.encoder` ให้ค่า encoder ณ ต้น cycle ที่อ่าน plant step ที่ cycle ก่อนหน้าจะเห็นผลแล้ว (ตรงกับ [plant model](../host/plant_model/README.md))
- ค่าที่เขียนลง `io.pwm_cmd` ที่ cycle `c` มีผลกับ plant ตั้งแต่ cycle `c + 1` ถ้าค่าเกิน ±2047 ฝั่ง plant จะ clip ให้ แต่ controller ควร saturate เองอยู่แล้ว

**TELEM**
- `telem.t_push` ใส่ word 32 bit ลง FIFO ที่ส่งออก UART ใช้ 1 cycle เสมอ ไม่ว่า FIFO จะเต็มหรือไม่
- ถ้า FIFO เต็ม word นั้นถูกทิ้งและ `telem.drops` เพิ่ม 1 (saturate ที่ 2^32 − 1) core ไม่เคยรอ UART
- รูปแบบ record ต่อ period กำหนดใน Phase 5

---

## 4. ALU Operations

`alu.out = op(alu.a, b)` เป็นเลข 32 bit แบบ two's complement ผลที่ล้นจะวนรอบ ไม่มี flag overflow

| opcode | Mnemonic | ผล |
|---|---|---|
| 0 | `ADD` | a + b |
| 1 | `SUB` | a − b |
| 2 | `AND` | a & b |
| 3 | `OR` | a \| b |
| 4 | `XOR` | a ^ b |
| 5 | `SHL` | a << b[4:0] |
| 6 | `SHR` | a >> b[4:0] แบบ logical |
| 7 | `SRA` | a >> b[4:0] แบบ arithmetic |
| 8 | `MIN` | min(a, b) แบบ signed |
| 9 | `MAX` | max(a, b) แบบ signed |

- การเขียนค่าอื่นนอกจาก 0–9 ลง `alu.op` เป็น illegal และ trap ที่ move นั้น (cause 3)
- **ทำไมมี `MIN`/`MAX`:** saturation ของ controller เขียนเป็น `clamp(x, lo, hi) = MIN(MAX(x, lo), hi)` ได้โดยไม่มี branch ทำให้ลด branch ที่เงื่อนไขผูกกัน ซึ่ง B4 ระบุว่าเป็นจุดเสี่ยงสูงสุดของ claim WCET tight 0 cycle ต้นทุนในฮาร์ดแวร์คือ comparator กับ mux 32 bit อย่างละตัว

---

## 5. CMP

`cmp.t_b` เปรียบเทียบ `cmp.a` กับ b แล้วเขียนผลลง 3 port พร้อมกัน แต่ละ port เป็น 0 หรือ 1

| Port | ผล |
|---|---|
| `cmp.eq` | a = b |
| `cmp.lt` | a < b แบบ signed |
| `cmp.ltu` | a < b แบบ unsigned |

แยกเป็น port ละค่าแทน bitfield เดียว เพราะ `pc.cond` ทดสอบทั้ง word ถ้าเป็น bitfield ทุก branch ต้องเสีย move เพิ่มเพื่อ mask ด้วย ALU แบบนี้ครอบเงื่อนไขได้ครบ 6 แบบ

| เงื่อนไข | move |
|---|---|
| a = b / a ≠ b | `cmp.eq -> pc.cond` แล้ว `t_jnz` / `t_jz` |
| a < b / a ≥ b | `cmp.lt -> pc.cond` แล้ว `t_jnz` / `t_jz` |
| unsigned | ใช้ `cmp.ltu` แบบเดียวกัน |
| a > b / a ≤ b | สลับ operand |

---

## 6. Control Flow

- ทุก jump ใช้ **absolute word address** จากค่าที่เขียนลง trigger port (immediate จาก label หรือค่าจาก register)
- `pc.t_jz` / `pc.t_jnz` ทดสอบ `pc.cond` ทั้ง 32 bit ใช้นับ loop ด้วย register ได้โดยตรง
- **call/return**
  - `target -> pc.t_call` ตั้ง `pc.link ← address ของ move ถัดจาก call` แล้ว jump
  - return คือ `pc.link -> pc.t_jump`
  - มี link register ตัวเดียว function ที่เรียก function อื่นต้องเก็บ `pc.link` ลง register หรือ MEM ก่อน แล้วเขียนคืนก่อน return (`pc.link` เขียนได้)
- ข้อจำกัดจาก B1 (assembler ตรวจ): jump ที่ target มาจาก register ใช้ได้เฉพาะ `pc.link -> pc.t_jump` (return) และห้าม recursion
- เวลา: taken ใช้ 1 + P cycle, not taken ใช้ 1 cycle, call เท่ากับ jump ที่ taken (timing_model R4)
- target ที่เกินขนาดของ code memory เป็น illegal (cause 5)

---

## 7. Timer

semantics และเวลาของทุก operation อยู่ใน timing_model R5 และ R6 ส่วนนี้กำหนดเฉพาะสิ่งที่ timing_model ยกมาให้ isa.md

- ค่าที่เขียนลง `t_advance`, `t_wait`, `t_arm` ถูกตีความเป็น unsigned 32 bit ส่วนค่าที่เขียนลง `t_sync`, `t_clear` ไม่มีความหมาย (ใช้ `#0`)
- `tmr.elapsed` saturate ที่ `0xFFFF_FFFF` (A6)

**`tmr.flags`**

| Bit | ชื่อ | ความหมาย |
|---|---|---|
| 0 | `late` | sync point ล่าสุดมาช้า (B2) |
| 1 | `armed` | มี deadline ที่ยังไม่ clear |
| 2 | `trapped` | เคยเกิด trap ตั้งแต่ reset |
| 3 | — | กันไว้สำหรับ `late_seen` (B2 อนาคต) |
| 31:4 | — | อ่านได้ 0 เสมอ |

ไม่ทำ bit `elapsed_sat` (ค้างจาก A6) เพราะค่า `0xFFFF_FFFF` ใน `elapsed` บอกอยู่แล้วว่า saturate

---

## 8. Memory

- สถาปัตยกรรมเป็น **Harvard**: code memory กับ data memory แยกกัน MEM เข้าถึง code memory ไม่ได้
- ทั้งสองเป็น on-chip synchronous SRAM (timing_model §2 สมมติฐานเรื่อง memory)
- data memory เข้าถึงทีละ **word 32 bit** ด้วย word address ไม่มี byte/halfword access
- **Load:** `addr -> mem.t_load` แล้วผลอยู่ที่ `mem.data_out` หลัง L = 1
- **Store:** `value -> mem.data_in` แล้ว `addr -> mem.t_store`
- address ส่งผ่าน trigger โดยตรง ไม่มี port `addr` แยกตามแผน §4 เพื่อประหยัด 1 move ต่อการเข้าถึง
- store ที่ cycle `c` มองเห็นได้โดย load ที่ trigger ตั้งแต่ `c + 1`
- address ที่เกินขนาดของ data memory เป็น illegal (cause 4)
- ขนาดของ code และ data memory เป็น parameter ของ implementation (หมายเหตุ implementation: บน Arty ตั้งต้นที่อย่างละ 4,096 word) ค่าเริ่มต้นของ data memory (literal pool) โหลดมาพร้อม image ของโปรแกรม

---

## 9. Trap

**Cause** (`trap.cause`)

| ค่า | สาเหตุ |
|---|---|
| 0 | ไม่มี trap |
| 1 | deadline miss (timing_model R6) |
| 2 | illegal port หรือ illegal encoding (§11) |
| 3 | illegal ALU opcode |
| 4 | data address เกินขนาด |
| 5 | jump target เกินขนาด |

**สิ่งที่เกิดเมื่อ trap** ลำดับการตัดสิน, การ squash, นิยามของ `trap.epc` และเวลา เป็นไปตาม timing_model R6 ทุก cause ส่วนนี้ไม่นิยามซ้ำ
- ถ้า deadline กับ illegal operation เกิดใน cycle เดียวกัน deadline ชนะ (`cause = 1`) และ move นั้นไม่ถูกตรวจ legality
- `trap.cause` ถูกตั้ง, `armed ← 0`, `trapped ← 1`
- ถ้า `trap.handler ≠ 0` move แรกของ handler execute ที่ `d + 1 + H`
- ถ้า `trap.handler = 0` (ค่าหลัง reset) core **halt**

**Phase 1 ไม่มี return จาก trap** handler มีไว้ทำ safe state เช่นเขียน 0 ลง `io.pwm_cmd` ส่ง telemetry แล้วจบด้วย `trap.t_halt`
- เหตุผล: การกลับไปทำงานต่อหลัง deadline miss ทำให้เวลาของ period นั้นเสียไปแล้ว และทำให้ WCET กับการติดตาม armed ใน B1 ต้องครอบ handler ด้วย
- assembler บังคับข้อนี้ด้วย static check S7 (timing_model §4): reject ทุก move ที่ใช้ `trap.epc` เป็น src ของ `pc.t_jump`, `pc.t_jz`, `pc.t_jnz` หรือ `pc.t_call` ส่วนการอ่าน `trap.epc` ไปที่อื่น เช่น telemetry ทำได้ตามปกติ ถ้าไม่ห้ามไว้ return จะได้ผลแค่ครั้งเดียว เพราะ `trapped` ยังเป็น 1 และ trap ครั้งถัดไปจะกลายเป็น double fault
- ถ้าต้องการ return ในอนาคต ต้องเพิ่มทางเคลียร์ `trapped` และเอา S7 ออก โดยใช้ `trap.epc -> pc.t_jump` ได้เลยไม่ต้องเปลี่ยน port map

**Halt:** ไม่มี move execute อีก `now` ยังนับต่อ output ทุกตัว (รวม `io.pwm_cmd`) ค้างค่าสุดท้าย ออกจาก halt ได้ด้วย reset เท่านั้น
- ขณะ halt **ไม่มีการตรวจ trap ใด ๆ** รวมถึง deadline ถ้า `trap.t_halt` ถูกเรียกตอนที่ยัง armed (ทำได้เพราะ `t_halt` ใช้นอก handler ได้) deadline ที่ผ่านไปภายหลังก็ไม่ทำให้ core ออกจาก halt

**Double fault:** ถ้าเกิด trap ขณะ `trapped = 1` core halt ทันทีโดยไม่เข้า handler

---

## 10. Timing Parameters

ค่าทั้งหมดมาจาก timing_model และ ISS กับ RTL ต้องใช้ตารางนี้ร่วมกันจากแหล่งเดียว

| Parameter | ค่า | อ้างอิง |
|---|---|---|
| move | 1 cycle | R1 |
| D (wake delay) | 1 | R5 |
| P (taken jump penalty) | 2 | R4 |
| H (trap entry) | 2 | R6 |
| R (move แรกหลัง reset) | cycle 2 | §1 |

**Latency ของ FU** (ผลอ่านได้ที่ `c + L`)

| FU | L | หมายเหตุ |
|---|---|---|
| REG, operand port, `pc.cond`, `pc.link`, `trap.handler` | — | state ธรรมดา อ่านค่าใหม่ได้ที่ `c + 1` |
| ALU | 1 | |
| MUL | 2 | assembler ตรวจ (S1) |
| CMP | 1 | |
| MEM load | 1 | |
| IO | — | ตาม §3 |
| TELEM | — | `t_push` 1 cycle เสมอ, `drops` อ่านค่าใหม่ได้ที่ `c + 1` |
| PC | — | R4 |
| TMR | — | R5, R6 |

---

## 11. Illegal Operations

ตรวจโดยฮาร์ดแวร์ ทุกข้อทำให้เกิด trap ที่ move นั้น

| เหตุ | cause |
|---|---|
| `dst` เป็น ID ที่ไม่มีในตาราง §2 หรือเป็น port ที่ไม่มี W (รวม `0x00`) | 2 |
| `imm = 0` แล้ว `src` เป็น ID ที่ไม่มี หรือเป็น port ที่ไม่มี R | 2 |
| `imm = 0` แล้ว bit 22:8 ไม่เป็น 0 | 2 |
| เขียนค่านอก 0–9 ลง `alu.op` | 3 |
| address ของ load/store เกินขนาด data memory | 4 |
| target ของ jump/call เกินขนาด code memory | 5 |

ตรวจโดย assembler (reject ก่อนรัน) ตาม timing_model §4 ได้แก่ S1–S7 เช่นอ่าน `mul.out` ก่อนครบ latency, immediate ของ tmr port เกินช่วง, `t_advance` ขณะ armed, indirect jump ที่ไม่ใช่ return, loop ที่ไม่มีขอบเขต และการ jump ไปที่ `trap.epc` ฮาร์ดแวร์ไม่ต้องตรวจกลุ่มนี้ ส่วน ISS ตรวจซ้ำตอนรันและหยุดพร้อม error

---

## 12. ข้อเสนอระหว่างร่าง

ข้อที่ออกแบบเพิ่มระหว่างร่าง isa.md เรียงตามผลกระทบ ยืนยันครบทุกข้อแล้ว (2026-10-02)

1. **[ยืนยัน] Port ID แบ่ง block ละ 16 ตาม FU** และ `0x00` เป็น illegal เพื่อให้ word ที่เป็น 0 ทั้งหมด trap
2. **[ยืนยัน] src port ID อยู่ใน bit 7:0** และ bit 22:8 ต้องเป็น 0
3. **[ยืนยัน] `null` ที่ `0xFF`** และ nop = `#0 -> null`
4. **[ยืนยัน] CMP ให้ผลแยก 3 port** เป็น 0/1 แทน bitfield `flags` ตามแผน
5. **[ยืนยัน] ALU เพิ่ม `MIN`/`MAX`** เพื่อทำ saturation แบบไม่มี branch
6. **[ยืนยัน] `pc.t_call`** กับ link register ตัวเดียวที่เขียนได้
7. **[ยืนยัน] MEM ส่ง address ผ่าน trigger** ไม่มี port `addr` และเข้าถึงทีละ word เท่านั้น
8. **[ยืนยัน] TELEM ใช้ `t_push` ตัวเดียว** กับตัวนับ `drops` ไม่มี port `data` แยก
9. **[ยืนยัน] Layout ของ `tmr.flags`** และไม่ทำ `elapsed_sat`
10. **[ยืนยัน] Trap:** `handler = 0` แปลว่า halt, Phase 1 ไม่มี return, มี `trap.t_halt`, double fault ทำให้ halt
