# Toolchain File Formats

สถานะ: **Phase 1** (ตัดสิน 2026-10-02)

เอกสารนี้กำหนดรูปแบบไฟล์ที่ tool ส่งต่อให้กัน ได้แก่ assembler, disassembler, ISS, WCET tool และ RTL testbench syntax ของ source อยู่ใน [asm_syntax.md](asm_syntax.md)

---

## 1. ภาพรวม

source `prog.tta` 1 ไฟล์ assemble ได้ 4 ไฟล์

| ไฟล์ | ใครใช้ | อ้างอิงจาก |
|---|---|---|
| `prog.code.hex` | RTL (simulation และ init memory), ISS | `$readmemh` ของ Verilog (IEEE 1364) |
| `prog.data.hex` | RTL, ISS | `$readmemh` |
| `prog.json` | ISS, WCET tool, ตัวอ่าน telemetry, disassembler | ของโปรเจกต์เอง ได้แนวคิดจาก symbol table ของ ELF และตารางบรรทัดของ DWARF |
| `prog.lst` | คน | listing ของ GNU as (`as -a`) |

**กฎที่ใช้กับทุกไฟล์**
- **Deterministic:** input เดียวกันต้องได้ output ที่เหมือนกันทุก byte ไม่มี timestamp, path แบบ absolute หรือลำดับที่ขึ้นกับ hash ของ dict ทำให้ diff และ reproduce ผลได้
- encoding เป็น UTF-8 และขึ้นบรรทัดด้วย LF
- ถ้ามี error แม้แต่ตัวเดียว assembler ไม่สร้างไฟล์ใดเลย จะไม่มีกรณีที่ได้ไฟล์ครึ่งเดียว
- ไฟล์ทั้งหมดเป็นของที่ generate ได้ ไม่ต้อง commit ลง git

**นามสกุลของ source** คือ `.tta` แยกจาก `.s` ของ GNU as เพื่อไม่ให้สับสนกับ assembly ของ ISA อื่น และทำ syntax highlighting เฉพาะได้ภายหลัง

---

## 2. `prog.code.hex` และ `prog.data.hex`

```
118003e8
20000011
13000023
00000000
...
```

- 1 บรรทัดต่อ 1 word, เลขฐานสิบหกตัวพิมพ์เล็ก 8 หลัก ไม่มี `0x` ไม่มี comment ไม่มี `@address`
- บรรทัดที่ n (นับจาก 0) คือ word ที่ address n
- **จำนวนบรรทัดเท่ากับขนาด memory พอดี** (`imem_words`, `dmem_words` ใน `prog.json`) ส่วนที่โปรแกรมไม่ได้ใช้เติมด้วย `00000000`
  - เหตุผล: word ที่เป็น 0 ใน code memory คือ illegal ที่ตั้งใจออกแบบไว้ (isa.md §2) ถ้าไม่เติม simulator จะได้ `X` และ FPGA ได้ค่าที่ขึ้นกับเครื่องมือ การวิ่งเลยท้ายโปรแกรมจะได้ผลไม่ตรงกันระหว่าง ISS, RTL และบอร์ด
  - ใน data memory ค่า 0 ตรงกับกฎว่า state ทุกตัวเป็น 0 หลัง reset และตรงกับ `.space`
- ค่าใน data เป็น 32 bit แบบ two's complement (`.word -1` → `ffffffff`)
- ขนาด memory ตั้งต้นที่อย่างละ 4,096 word ปรับผ่าน option ของ assembler ได้ และค่าที่ใช้จริงถูกบันทึกใน `prog.json`

**ทำไมไม่ใช้รูปแบบอื่น:** `.coe` เป็นของ Xilinx ขัดกับหลักไม่ผูก vendor ส่วน Intel HEX มี address และ checksum สำหรับแฟลชผ่าน programmer ซึ่งงานนี้ไม่ต้องใช้ `$readmemh` อ่านได้ทุก simulator, ใช้ init memory ตอน synthesize ได้ และอ่านใน Python ได้ในบรรทัดเดียว

---

## 3. `prog.json`

```json
{
  "format": 1,
  "spec": "phase0-frozen-2026-10-02",
  "source": "ctrl.tta",
  "imem_words": 4096,
  "dmem_words": 4096,
  "code_len": 42,
  "data_len": 3,
  "symbols": {
    "PERIOD":  {"kind": "equ",   "value": 100000},
    "gains":   {"kind": "label", "section": "data", "value": 0},
    "loop":    {"kind": "label", "section": "text", "value": 2},
    "on_trap": {"kind": "func",  "section": "text", "value": 37}
  },
  "functions": [
    {"name": "main",    "start": 0,  "end": 36},
    {"name": "on_trap", "start": 37, "end": 41}
  ],
  "annotations": [
    {"kind": "task",       "name": "control", "addr": 2},
    {"kind": "loop_bound", "addr": 10, "n": 16}
  ],
  "source_map": [
    {"addr": 0, "file": "ctrl.tta", "line": 9},
    {"addr": 1, "file": "ctrl.tta", "line": 10}
  ]
}
```

| Field | ความหมาย |
|---|---|
| `format` | รุ่นของรูปแบบไฟล์นี้ เพิ่มเมื่อโครงสร้างของ JSON เปลี่ยน |
| `spec` | รุ่นของ spec (isa.md และ timing_model.md) ที่ใช้ assemble ดู §3.1 |
| `source` | **ชื่อไฟล์ (basename)** ของ source เท่านั้น เช่น `ctrl.tta` ไม่ว่าจะส่งให้ assembler เป็น `ctrl.tta`, `./ctrl.tta` หรือ path แบบ absolute ก็ได้ค่าเดียวกัน |
| `imem_words`, `dmem_words` | ขนาด memory ที่ใช้ เท่ากับจำนวนบรรทัดของ `.hex` |
| `code_len`, `data_len` | **จำนวน word ที่ section สร้างขึ้นก่อนเติม 0 ให้เต็ม memory** นับทุก word ไม่ว่าค่าจะเป็นอะไร (`.word 1, 0, 2` นับ 3 และ `.space n` นับ n) |
| `symbols` | label, function และ `.equ` ที่ผู้ใช้ประกาศ ไม่รวม opcode ที่กำหนดไว้แล้ว `kind` เป็น `label`, `func` หรือ `equ` ส่วน `section` มีเฉพาะ label และ func |
| `functions` | ขอบเขตของทุก function รวม `main` โดย `start` และ `end` เป็น address แบบ**นับรวมทั้งสองฝั่ง (inclusive)** ถ้า `main` ไม่มี move อยู่เลยจะไม่ปรากฏ |
| `annotations` | `task` ระบุ address ของ label ที่ตามหลัง ส่วน `loop_bound` ระบุ address ของ header ของ loop และค่า `n` |
| `source_map` | 1 รายการต่อ 1 word ใน code เรียงตาม address (`code_len` รายการ) `file` เป็น basename เหมือน `source` |

key ใน object เรียงตามตัวอักษร และ list เรียงตาม address หรือลำดับใน source เพื่อให้ได้ไฟล์ที่ deterministic

**Invariant** ที่ assembler รับประกันและ tool ที่อ่านไฟล์ตรวจได้
- `0 ≤ code_len ≤ imem_words` และ `0 ≤ data_len ≤ dmem_words`
- ทุก function: `0 ≤ start ≤ end < code_len`, function ไม่ซ้อนทับกัน และ list เรียงตาม `start`
- word ใน code ที่ address `≥ code_len` และ word ใน data ที่ address `≥ data_len` เป็น 0 ทั้งหมด
- word ใน code ที่ address `< code_len` ไม่เป็น 0 เพราะ `00000000` เป็น illegal และ assembler สร้างได้ทางเดียวคือผ่าน `.illegal` ซึ่งไม่รับ word ที่เป็น 0 (asm_syntax.md §5)

**path ในอนาคต:** Phase 1 ไม่มี include จึงมี source ไฟล์เดียว ถ้าเพิ่ม include ให้เปลี่ยน `file` เป็น path แบบ relative ต่อโฟลเดอร์ของไฟล์หลัก ใช้ `/` เป็นตัวคั่นและ normalize แล้ว พร้อมเพิ่ม `format`

### 3.1 `spec` และการตรวจรุ่น

- ค่า `spec` มาจากค่าคงที่ตัวเดียวในโมดูล spec กลาง (`host/common/`) ซึ่ง assembler และ ISS import ร่วมกัน
- **เปลี่ยนค่า** เมื่อมีการแก้ที่กระทบ encoding, semantics หรือเวลา ตามกติกาการแก้หลัง freeze
- **ไม่เปลี่ยน** เมื่อแก้แค่ syntax ของ source หรือตัวอย่างในเอกสาร เช่นการเปลี่ยนเป็น `#label` เมื่อ 2026-10-02
- ISS **ปฏิเสธไม่รัน** ถ้า `format` หรือ `spec` ไม่ตรงกับของตัวเอง หรือจำนวนบรรทัดของ `.hex` ไม่ตรงกับ `imem_words`/`dmem_words` เพื่อกันไม่ให้ image เก่ารันบน ISS ที่ใช้กฎใหม่โดยไม่รู้ตัว
- ISS ตรวจ invariant ทุกข้อใน §3 ซ้ำตอนโหลดด้วย แม้ assembler จะรับประกันไว้แล้ว เพราะ image เป็นสิ่งที่ส่งต่อระหว่าง tool ไฟล์ที่ถูกแก้ด้วยมือหรือเสียระหว่างทางจึงต้องถูกปฏิเสธที่ขอบ ไม่ใช่ไปเจอทีหลังตอนรัน

**ทำไมไม่ใช้ ELF:** ELF เป็นมาตรฐานจริง แต่ binutils ไม่รู้จัก ISA นี้ จึงไม่ได้ประโยชน์จากเครื่องมือที่มีอยู่ ต้องใส่ annotation และ section แบบ Harvard เองอยู่ดี และอ่านใน Python ยากกว่า JSON มาก

---

## 4. `prog.lst`

```
addr   word      line  source
                    1  .equ PERIOD, 100_000
                    5          .text
0000   80800025     6          #on_trap    -> trap.handler   ; = 37
0001   70800000     7          #0          -> tmr.t_sync
0002   718186a0     9  loop:   #PERIOD     -> tmr.t_advance  ; = 100000
...
D0000  0002d923     3  gains:  .word 186_659, 1_501_851, 6_766
D0001  0016ea9b
D0002  00001a6e
```

- ทุกบรรทัดของ source ปรากฏตามลำดับ บรรทัดที่ไม่สร้าง word (comment, directive, label ที่อยู่บรรทัดเดียว) มีช่อง address และ word ว่าง
- address ของ code เป็นฐานสิบหก 4 หลัก ส่วน address ของ data นำหน้าด้วย `D`
- immediate ที่เป็น symbol หรือนิพจน์มีค่าจริงต่อท้ายเป็น `; = <ค่า>`
- `.word` ที่มีหลายค่าแสดง word ละบรรทัด
- listing มีไว้ให้คนอ่านเท่านั้น tool ห้าม parse ไฟล์นี้ และรูปแบบเปลี่ยนได้โดยไม่ต้องเพิ่ม `format`

---

## 5. Disassembler และการทดสอบแบบ round-trip

disassembler อ่าน `prog.code.hex` และ `prog.data.hex` แล้วสร้าง source ที่ normalize แล้ว

**รูปแบบที่สร้างออกมา (ทั้งสองโหมด)**
- 1 move ต่อบรรทัด ในรูป `src -> dst` และ `#0 -> null` แสดงเป็น `nop`
- word ใน code ที่เขียนด้วย syntax ปกติไม่ได้ แสดงเป็น `.illegal 0x<word>` โดยใช้กฎเดียวกับที่ assembler ใช้ตัดสินว่าจะรับ `.illegal` หรือไม่ (asm_syntax.md §5) word หนึ่งจึงมีวิธีเขียนได้วิธีเดียว disassembler ไม่หยุดเมื่อเจอ word แบบนี้
- data แสดงเป็น `.word` ทีละ word

**มี 2 โหมด**

| | มี `prog.json` | ไม่มี `prog.json` |
|---|---|---|
| ความยาวของ code และ data | ใช้ `code_len` และ `data_len` แล้วตรวจ invariant ใน §3 ถ้า word ที่เกินความยาวไม่เป็น 0 ให้เตือนว่า image เสีย | ตัด word ที่เป็น 0 ที่ต่อท้ายออกทั้งหมด สำหรับ code ได้ `code_len` เดิมพอดีเสมอ เพราะ word ใน `code_len` ไม่เป็น 0 (§3) ส่วน data ที่ลงท้ายด้วย 0 จะสั้นลง แต่ตอน assemble กลับก็เติม 0 คืนเหมือนเดิม |
| target ของ jump, call, `trap.handler` | แสดงเป็นชื่อ label และใส่ label ที่ address ของมัน | แสดงเป็นตัวเลข |
| ขอบของ function | สร้าง `.func` / `.endfunc` ตาม `functions` | ไม่มี |
| `.equ` | ไม่สร้าง immediate อื่นนอกจาก target ข้างบนแสดงเป็นตัวเลข | ไม่มี |
| annotation | สร้างกลับจาก `annotations` | ไม่มี |
| round-trip | **รับประกัน** test ต้องใช้โหมดนี้ | **ไม่รับประกัน** มีไว้ให้คนอ่าน เพราะ assembler ต้องการ label เป็น target ของ jump และต้องรู้ขอบของ function |

**นิยามของ round-trip test** (โหมดที่มี `prog.json`): assemble(`src`) ได้ `code.hex` และ `data.hex` จากนั้น disassemble ได้ `src'` แล้ว assemble(`src'`) ด้วยขนาด memory เดิมต้องได้ `.hex` ทั้งสองไฟล์ที่เหมือนเดิมทุก byte ทดสอบด้วยการเทียบ binary ไม่ได้เทียบข้อความ เพราะ source ต้นฉบับมี comment, `.equ` และการจัดรูปแบบที่ disassembler สร้างคืนไม่ได้ ส่วน `prog.json` ไม่ต้องเหมือนเดิม เพราะ symbol ของ `.equ` หายไป
- listing (§4) แสดง source ต้นฉบับพร้อมค่าที่ assembler คำนวณได้ ไม่ได้ใช้ disassembler เพราะ source มีข้อมูลครบกว่า (ชื่อ `.equ`, comment)

---

## 6. Trace ของ ISS และ RTL *(ตัดสิน 2026-10-05)*

trace มีไว้ทำ **lockstep compare** ระหว่าง ISS (Python) กับ RTL testbench (SystemVerilog) ทั้งสองฝั่งเขียนไฟล์ `prog.trace` รูปแบบเดียวกันแล้วเทียบทีละบรรทัด

**อ้างอิงจาก:** commit log ของ RISC-V ได้แก่ RVFI (RISC-V Formal Interface ของ riscv-formal) ที่กำหนดให้มี record ต่อ instruction ที่ retire และ commit log ของ Spike ที่ Ibex กับ riscv-dv ใช้เทียบกับ RTL ต่างกันตรงที่หน่วยของการ retire คือ move และสิ่งที่บันทึกคือการย้ายค่าบน bus (src → dst) พร้อม record ของ trap และ halt

### 6.1 รูปแบบ

- ข้อความ 1 record ต่อบรรทัด ตัวอักษรแรกบอกชนิด
- field คั่นด้วย**ช่องว่าง 1 ตัวพอดี** ไม่มีช่องว่างนำหน้าหรือต่อท้าย ไม่จัดคอลัมน์ ฝั่ง RTL เขียนได้ด้วย `$fwrite` บรรทัดเดียว และเทียบแบบตัวอักษรต่อตัวอักษรได้
- hex เป็นตัวพิมพ์เล็กไม่มี `0x` ส่วนฐานสิบไม่มีศูนย์นำหน้า
- trace เป็นของให้เครื่องเทียบเท่านั้น การแสดงชื่อ port หรือ disassembly ให้ tool แยกแปลงจาก trace กับ `prog.json`

```
M 2 00000000 118003e8 imm 11 000003e8
M 3 00000001 20000011 11 20 000003e8
M 4 00000002 13000023 23 13 0000000c
T 2000 00000025 1
H 2000 nohandler
```

### 6.2 Record

**`M` move ที่ execute จริง** `M <cycle> <pc> <word> <src> <dst> <value>`

| Field | รูปแบบ | ความหมาย |
|---|---|---|
| cycle | ฐานสิบ | ค่า `now` ตอน execute บรรทัดแรกจึงตรวจ R = 2 ได้ stall และ bubble เห็นจากช่องว่างของ cycle ระหว่างบรรทัด |
| pc | hex 8 หลัก | word address ของ move |
| word | hex 8 หลัก | instruction word ดิบ ถ้า fetch ผิดจะเห็นตรงนี้ |
| src | `imm` หรือ hex 2 หลัก | port ID ต้นทาง ถ้า word ตรงแต่ src/dst ไม่ตรง แปลว่าบั๊กอยู่ที่ decoder |
| dst | hex 2 หลัก | port ID ปลายทาง |
| value | hex 8 หลัก | ค่าบน bus (immediate ที่ sign-extend แล้ว หรือค่าที่อ่านจาก port) |

ไม่มี record ของผล FU ตอน trigger ถ้า FU คำนวณผิด จะเห็นที่ move แรกที่อ่าน output ของ FU นั้น ซึ่งช้าไปไม่กี่ cycle แต่ยังเจอแน่นอน

**`T` trap** `T <d> <epc> <cause>` โดย d เป็นฐานสิบ, epc เป็น hex 8 หลักตามนิยามใน timing_model R6 และ cause เป็นฐานสิบตาม isa.md §9 move ที่ถูก squash ไม่มีบรรทัด `M`

**`H` halt** `H <cycle> <reason>`

| reason | เมื่อไร | record ก่อนหน้าใน cycle เดียวกัน |
|---|---|---|
| `halt` | move ที่เขียน `trap.t_halt` execute | `M` ของ move นั้น |
| `nohandler` | trap ขณะ `trap.handler = 0` | `T` |
| `double` | trap ขณะ `trapped = 1` | `T` |

**`E` จบเพราะครบจำนวน cycle** `E <n> maxcycles` เมื่อ simulate cycle 0 ถึง n − 1 ครบแล้วยังไม่ halt

ถ้ามีหลาย record ใน cycle เดียวกัน เรียงตามตารางข้างบน (`M` หรือ `T` มาก่อน `H`) trace จบด้วย `H` หรือ `E` เสมอ บรรทัดเดียว

### 6.3 กฎการเทียบ

- เทียบทีละบรรทัดตั้งแต่บรรทัดแรก ทุกบรรทัดต้องเหมือนกันทุกตัวอักษร
- หยุดที่บรรทัดแรกที่ไม่ตรง แล้วรายงานสองบรรทัดนั้น บรรทัดของ source (จาก `source_map`) และ 10 บรรทัดก่อนหน้า
- ถ้าฝั่งหนึ่งจบก่อน ถือว่าไม่ตรงที่บรรทัดแรกที่อีกฝั่งยังมี
- ทั้งสองฝั่งต้องรันด้วยไฟล์ `.hex` ชุดเดียวกัน, stimulus เดียวกัน (§6.4) และจำนวน cycle สูงสุดเท่ากัน

### 6.4 Stimulus ของ IO (`prog.stim`)

input จาก IO ต้องเหมือนกันทุก cycle ทั้งสองฝั่ง ไม่งั้น trace ต่างกันทันที

```
0 io.encoder 0
100000 io.encoder 12
200000 io.encoder -3
```

- บรรทัดละ `<cycle> <port> <value>` cycle และ value เป็นฐานสิบ value มีเครื่องหมายได้ ฝั่ง SystemVerilog อ่านด้วย `$fscanf` ได้ตรง ๆ
- ค่าใช้ตั้งแต่ต้น cycle นั้นเป็นต้นไป move ที่อ่าน port นั้นที่ cycle `c` ได้ค่าของบรรทัดล่าสุดที่ cycle ≤ `c`
- cycle ของ port เดียวกันต้อง**เพิ่มขึ้นอย่างเดียวตามลำดับในไฟล์** (ห้ามซ้ำ ห้ามถอยหลัง) และห้ามติดลบ ไฟล์ที่ผิดกฎนี้ถูก**ปฏิเสธพร้อมบอกบรรทัด** tool ไม่เรียงให้ใหม่เอง เพราะ stimulus เป็นสัญญาระหว่าง ISS กับ RTL ถ้าฝั่งหนึ่งซ่อมให้แต่อีกฝั่งไม่ซ่อม trace จะต่างกันโดยไม่มีใครรู้สาเหตุ
- ถ้าไม่มีไฟล์ หรือยังไม่ถึงบรรทัดแรก ค่าเป็น 0 ตามกฎค่าหลัง reset
- ตอนนี้ใช้กับ `io.encoder` อย่างเดียว ใส่ชื่อ port ไว้ในแต่ละบรรทัดเพื่อรองรับ port input ใหม่ในอนาคต ส่วนการต่อกับ plant model จริงเป็นงานของ Phase 5

### 6.5 Parameter ที่ต้องใช้ร่วมกันเพื่อให้ trace ตรงกัน

ค่าของ `telem.drops` ขึ้นกับความจุของ telemetry FIFO และความเร็วที่ UART ดึงข้อมูลออก ถ้าโปรแกรมอ่าน `drops` แล้ว ISS กับ RTL ใช้ค่าพวกนี้ต่างกัน trace จะต่างกัน จึงต้องกำหนดเป็น parameter ในโมดูล spec กลาง (`host/common/`) แล้วใช้ร่วมกันทั้ง ISS และ RTL

| Parameter | หน่วย | ค่า |
|---|---|---|
| ความจุของ FIFO | word | กำหนดตอน implement |
| เวลาที่ UART ใช้ส่ง 1 word | cycle (จำนวนเต็ม) | กำหนดตอน implement จาก baud divider ที่เป็นจำนวนเต็ม |

ISS ต้องจำลอง FIFO ตามสองค่านี้ทุก cycle ไม่ใช่แค่นับ word ที่ push

**พฤติกรรมของ FIFO ที่ ISS ใช้และ RTL ต้องทำให้ตรง** *(กำหนดตอนเขียน ISS 2026-10-05)*
- ในแต่ละ cycle ฝั่ง UART ดึงก่อน แล้ว push ของ move ใน cycle นั้นจึงลงทีหลัง
- word ที่ push ที่ cycle `p` เริ่มส่งได้เร็วที่สุดที่ `p + 1` และ**ออกจาก FIFO ตอนเริ่มส่ง** (ย้ายไปอยู่ใน shift register ของ UART) ไม่ใช่ตอนส่งเสร็จ
- UART ส่ง word ละ `TELEM_CYCLES_PER_WORD` cycle ต่อกันโดยไม่มีช่องว่าง word ถัดไปเริ่มที่ `max(เวลาที่ word ก่อนหน้าส่งเสร็จ, เวลาที่ push + 1)`
- push ที่เจอ FIFO มี word อยู่ครบ `TELEM_FIFO_WORDS` แล้วจะถูกทิ้ง และ `drops` เพิ่ม 1
- ผลคือถ้า push ติดกันทุก cycle จะรับได้ `TELEM_FIFO_WORDS + 1` word ก่อนเริ่มทิ้ง (1 word อยู่ใน UART แล้ว)

---

## 7. ยังไม่กำหนดในเอกสารนี้

- option ของ command line ของแต่ละ tool กำหนดตอน implement
