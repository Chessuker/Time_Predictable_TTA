# Assembly Syntax

สถานะ: **Phase 1** (ตัดสิน 2026-10-02)

เอกสารนี้กำหนด syntax ของ assembly ที่ assembler (`host/asm/`) รับ ส่วน encoding และ semantics ของแต่ละ port อยู่ใน [isa.md](isa.md) และเวลาอยู่ใน [timing_model.md](timing_model.md)

**หลักการเดียวที่คุมทั้งหมด: 1 บรรทัดที่เป็น move = 1 move = 1 cycle** (ยกเว้น jump และ stall ซึ่ง timing_model บอกเวลาไว้แล้ว) assembler ไม่มี pseudo-instruction หรือ macro ที่ขยายเป็นหลาย move เพื่อให้นับ cycle จาก source ได้ตรง ๆ

---

## 1. ที่มาของ syntax

| ส่วน | อ้างอิงจาก |
|---|---|
| `src -> dst` | MOVE project ของ Corporaal และ TCE (ต้นตระกูลของ TTA) ต่างกันตรงที่บรรทัดหนึ่งมี 1 move เพราะ core ทำได้ 1 move ต่อ cycle |
| ชื่อ port แบบ `fu.port` | รูปแบบมาจาก TCE ส่วนชื่อจริงมาจาก isa.md §2 ไม่ใส่ opcode ในชื่อ trigger port แบบ TCE เพราะ ISA นี้มี opcode เป็น port แยก (`alu.op`) |
| `#` นำหน้า immediate | ARM และ 68k |
| `_` คั่นหลักตัวเลข | Verilog, Python, Rust |
| `.equ`, `.text`, `.data`, `.word`, `.space`, `.func`/`.endfunc`, นิพจน์ตอน assemble | GNU as |
| `;` comment | NASM, AVR, 6502 |
| `@loop_bound`, `@task` | ของโปรเจกต์นี้เอง ได้แนวคิดจาก flow fact ของ WCET tool เช่น AIS ของ aiT และ pragma `loopbound` ของ Patmos |
| มีแค่ `nop` เป็น pseudo-instruction | ของโปรเจกต์นี้เอง ตามหลัก 1 บรรทัด = 1 cycle |
| `.illegal` | ของโปรเจกต์นี้เอง เทียบได้กับ `.word` หรือ `.inst` ที่ GNU as ใช้ใส่ word ดิบใน code แต่จำกัดให้ใช้ได้เฉพาะ word ที่เขียนแบบปกติไม่ได้ |

---

## 2. โครงสร้างของบรรทัด

```
[label:] [statement] [; comment]
```

- บรรทัดว่างและบรรทัดที่มีแต่ comment ไม่มีผล
- statement คือ move, `nop`, directive หรือ annotation อย่างใดอย่างหนึ่ง
- label ที่อยู่บรรทัดเดียวโดด ๆ ชี้ไปที่ item ถัดไปใน section เดียวกัน หลาย label ชี้ address เดียวกันได้
- ตัวอักษรใหญ่เล็กมีผล (`loop` กับ `Loop` คนละชื่อ)

---

## 3. Move

```asm
        src -> dst
```

- **dst** ต้องเป็น port ที่เขียนได้ (W ใน isa.md §2)
- **src** เป็นอย่างใดอย่างหนึ่ง
  - **port** ที่อ่านได้ (R) เช่น `r3`, `alu.out`, `tmr.elapsed`
  - **immediate** คือ `#` ตามด้วยนิพจน์ เช่น `#1000`, `#loop`, `#PERIOD + 1`
- **กฎ: ไม่มี `#` = port, มี `#` = ค่าที่ฝังใน instruction** label และค่าคงที่ที่ใช้เป็นค่าต้องมี `#` เสมอ ถ้าเขียน `loop -> pc.t_jump` assembler จะ error และบอกให้ใส่ `#`
- ชื่อ port: `r0`–`r15`, `null` และ `fu.port` ตาม isa.md §2 (ตัวพิมพ์เล็กทั้งหมด)

**`nop`** คือ `#0 -> null` ใช้ 1 cycle เป็น pseudo-instruction ตัวเดียว

---

## 4. Immediate และนิพจน์

**ตัวเลข**
- ฐานสิบ `1000`, ฐานสิบหก `0x3E8`, ฐานสอง `0b1010`
- ใส่ `_` คั่นระหว่างหลักได้ `100_000`, `0xFFFF_FFFF`
- เลขลบใช้ unary minus: `#-1`

**Symbol ที่ใช้ในนิพจน์ได้**
- label ใน `.text` มีค่าเป็น code word address ส่วน label ใน `.data` มีค่าเป็น data word address
- ค่าคงที่จาก `.equ`
- opcode ของ ALU ที่กำหนดไว้แล้ว: `ADD`=0, `SUB`=1, `AND`=2, `OR`=3, `XOR`=4, `SHL`=5, `SHR`=6, `SRA`=7, `MIN`=8, `MAX`=9

**Operator** (ลำดับความสำคัญจากสูงไปต่ำ เหมือนภาษา C)

| ระดับ | Operator |
|---|---|
| 1 | `-x`, `~x` (unary) |
| 2 | `*` |
| 3 | `+`, `-` |
| 4 | `<<`, `>>` (arithmetic) |
| 5 | `&` |
| 6 | `^` |
| 7 | `\|` |

ใช้วงเล็บได้ ไม่มีการหารเพราะไม่มีงานที่ต้องใช้และเลี่ยงคำถามเรื่องการปัดเศษ นิพจน์คำนวณด้วยจำนวนเต็มไม่จำกัดขนาดแล้วจึงตรวจช่วงตาม §8 อ้าง label ที่ประกาศทีหลังได้ (assembler ทำสอง pass)

---

## 5. Directive

| Directive | ใช้ใน | ความหมาย |
|---|---|---|
| `.text` | | เริ่มหรือกลับไปที่ section code (ค่าตั้งต้นตอนเริ่มไฟล์) address เริ่มที่ 0 ซึ่งเป็น move แรกหลัง reset |
| `.data` | | เริ่มหรือกลับไปที่ section data address เริ่มที่ 0 (Harvard ตาม isa.md §8) |
| `.word e1, e2, …` | `.data` | ใส่ word 32 bit เรียงกัน ค่าต้องอยู่ใน −2^31 ถึง 2^32 − 1 แล้วเก็บแบบ two's complement |
| `.space n` | `.data` | จองที่ n word ที่มีค่าเป็น 0 (n ≥ 1) |
| `.equ NAME, expr` | ที่ใดก็ได้ | ประกาศค่าคงที่ บรรทัดนี้ห้ามมี label |
| `.func name` | `.text` | เริ่ม function `name` และนิยาม label `name` ที่ address ปัจจุบัน |
| `.endfunc` | `.text` | จบ function ที่เปิดอยู่ |
| `.illegal expr` | `.text` | ใส่ word ดิบ 1 word ที่ assembler ไม่ยอมรับในรูปปกติ ใช้ทำ test ของ trap (§5.1) |

- move อยู่ได้เฉพาะใน `.text` ส่วน `.word` และ `.space` อยู่ได้เฉพาะใน `.data` เพราะ code memory มีไว้ execute อย่างเดียว
- `.func` ซ้อนกันไม่ได้ และทุก `.func` ต้องมี `.endfunc` ปิด
- code ที่ไม่ได้อยู่ใน `.func` ใดถือเป็น function `main` โดยปริยาย และต้องอยู่**ก่อน** `.func` ตัวแรกทั้งหมด หลังจาก `.func` ตัวแรกแล้ว move ทุกตัวต้องอยู่ใน `.func` เพื่อให้ `main` เป็นช่วง address ต่อเนื่องเดียว (`functions` ใน toolchain_formats.md §3 ต้องไม่ซ้อนทับกัน)

### 5.1 `.illegal`

**มีไว้ทำไม:** ISS ต้องมี test program ที่ทำให้เกิด trap cause 2, 3 และ 5 จริง แต่ syntax ปกติไม่มีทางสร้าง word แบบนั้น เช่น `#12 -> alu.op` encode ได้แต่ค่าเกิน 0–9 และ jump ไป address ที่เกินขนาด memory เขียนไม่ได้เพราะ target ต้องเป็น label `.illegal` ยังทำให้ disassembler แสดง word แบบนี้ในรูปที่ assemble กลับได้ ([toolchain_formats.md](toolchain_formats.md) §5)

**กฎ**
- `expr` ต้องมีค่าใน 1 ถึง `0xFFFF_FFFF` ใช้ 1 word = 1 cycle เหมือน move ทั่วไป
- **assembler รับเฉพาะ word ที่เขียนด้วย syntax ปกติไม่ได้ ณ ตำแหน่งนั้นของโปรแกรมนั้น** คือ decode ไม่ได้ตาม isa.md §11 หรือ decode ได้แต่ไม่ผ่านการตรวจราย move ใน §8 (port, ทิศ, ช่วงของ immediate, target ของ control flow) ถ้า word นั้นเขียนด้วย syntax ปกติได้ assembler error และแสดงรูปปกติให้ ผลคือ word หนึ่งมีวิธีเขียนได้วิธีเดียว
- **word `0x0000_0000` ใช้ไม่ได้** แม้จะเป็น illegal เพราะเป็นค่าที่ใช้เติมท้าย memory ถ้ายอมให้อยู่กลาง code disassembler จะหาความยาวของโปรแกรมจาก `.hex` อย่างเดียวไม่ได้ ถ้าต้องการทดสอบ dst ที่ illegal ใช้ ID ที่ไม่มีอยู่จริงตัวอื่น เช่น `.illegal 0x0F80_0000`
- "illegal" ในที่นี้หมายถึง**อยู่นอกภาษาที่ assembler ยอมรับ** ซึ่งกว้างกว่า illegal ของ ISA บาง word ที่ใส่ด้วย `.illegal` CPU execute ได้ตามปกติ เช่น `t_call` ไปยัง address ที่ไม่ใช่ต้น function
- `.illegal` นับเป็นจุดจบของ control flow เหมือน `trap.t_halt` ทั้งในกฎห้ามไหลข้ามขอบ (§8) และตอนสร้าง CFG
- ใช้ได้กับ test program เท่านั้น โปรแกรมจริงไม่มีเหตุผลต้องใช้

```asm
        .text
        #on_trap    -> trap.handler
        .illegal 0x2180_000C          ; = #12 -> alu.op → trap cause 3

.func on_trap
        trap.cause  -> telem.t_push
        #0          -> trap.t_halt
.endfunc
```

---

## 6. Annotation

annotation ขึ้นต้นด้วย `@` และ**ไม่เปลี่ยน binary แม้แต่ bit เดียว** มีไว้สำหรับการวิเคราะห์ (static check และ WCET tool) เท่านั้น annotation ต้องอยู่บรรทัดของตัวเอง และบรรทัดที่มีเนื้อหาถัดไปต้องเป็นบรรทัดที่มี label ใน `.text` หรือบรรทัด `.func NAME` (ซึ่งนิยาม label `NAME`)

**`@loop_bound N`**
- N คือ**จำนวนครั้งสูงสุดที่ header ของ loop ทำงานต่อการเข้า loop 1 ครั้ง** (N ≥ 1 และต้องเป็นค่าคงที่)
- วางไว้บรรทัดก่อน label ของ header ทันที

```asm
@loop_bound N
loop:   r1          -> alu.a
        ...
        #loop       -> pc.t_jnz
```

**`@task name`**
- ตั้งชื่อ task ให้ label ถัดไป ใช้สำหรับรายงาน WCET
- period และ deadline **ไม่ได้อยู่ใน annotation** WCET tool อ่านจาก immediate ของ `t_advance`, `t_wait` และ `t_arm` ในโค้ดเอง เพื่อให้ข้อมูลมีแหล่งเดียว ใน task ที่ต้องวิเคราะห์ ค่า v ของ timing move จึงต้องเป็น immediate (ตรวจใน Phase 4)

---

## 7. ชื่อที่สงวนไว้

ใช้เป็น label, `.equ` หรือชื่อ function ไม่ได้
- `r0`–`r15`, `null`, `nop`
- ชื่อ FU: `alu`, `mul`, `cmp`, `pc`, `mem`, `tmr`, `trap`, `io`, `telem`
- opcode ของ ALU: `ADD`, `SUB`, `AND`, `OR`, `XOR`, `SHL`, `SHR`, `SRA`, `MIN`, `MAX`
- `main`

label, `.equ` และชื่อ function ใช้ namespace เดียวกัน ห้ามซ้ำกัน

---

## 8. ข้อผิดพลาดที่ assembler ตรวจ

error รายงานเป็น `file:line:col: error: message` และ assembler ไม่สร้าง output ถ้ามี error แม้แต่ตัวเดียว

**Syntax และ symbol**
- port ไม่มีอยู่จริง, ใช้ port ผิดทิศ (อ่าน port ที่ไม่มี R หรือเขียน port ที่ไม่มี W)
- src เป็นชื่อที่ไม่ใช่ port และไม่มี `#`
- symbol ที่ไม่ได้ประกาศ, ประกาศซ้ำ, ใช้ชื่อที่สงวนไว้ หรือ `.equ` ที่อ้างกันเป็นวง
- directive อยู่ผิด section, `.func` ซ้อนกันหรือไม่ได้ปิด
- annotation ที่บรรทัดถัดไปไม่มี label, `@loop_bound` ที่ N ไม่ใช่ค่าคงที่ ≥ 1

**ช่วงของ immediate** (ตามปลายทาง)

| dst | ช่วงที่ยอมรับ | ที่มา |
|---|---|---|
| ทั่วไป | −4,194,304 ถึง 4,194,303 | isa.md §1 |
| `tmr.t_advance`, `tmr.t_wait`, `tmr.t_arm` | 0 ถึง 4,194,303 | S2 |
| `alu.op` | 0 ถึง 9 | isa.md §4 |
| `mem.t_load`, `mem.t_store` | 0 ถึงขนาด data memory − 1 | isa.md §8 |

**Target ของ control flow** (ทำให้สร้าง CFG ได้แน่นอนตอน assemble)
- immediate ที่ส่งเข้า `pc.t_jump`, `pc.t_jz`, `pc.t_jnz` ต้องเป็น label ใน `.text` ตัวเดียว ห้ามมีนิพจน์อื่นปน และ label นั้นต้องชี้ไปที่ word ที่มีอยู่จริง (address < `code_len`) ไม่ใช่ label ที่อยู่ท้าย `.text` โดยไม่มีอะไรตามหลัง
- immediate ที่ส่งเข้า `pc.t_call` และ `trap.handler` ต้องเป็นชื่อ `.func`
- src ที่เป็น port ส่งเข้า jump ได้แค่ `pc.link -> pc.t_jump` (return) และต้องอยู่ใน `.func` (S5) ส่วน `trap.epc` ห้ามเป็น src ของ jump หรือ call ทุกตัว (S7)

**ห้ามไหลข้ามขอบ**
- move สุดท้ายก่อน `.func`, move สุดท้ายก่อน `.endfunc` และ move สุดท้ายของ `.text` ต้องเป็น `pc.t_jump`, `trap.t_halt` หรือ `.illegal`
- เหตุผล: ถ้าไม่บังคับ โค้ดจะไหลเข้า function ถัดไปหรือวิ่งเลยท้ายโปรแกรมเงียบ ๆ (ถ้าวิ่งเลยท้ายจะเจอ word ที่เป็น 0 แล้ว trap แต่ควรจับได้ตั้งแต่ตอน assemble)

**Static check ตาม timing_model §4** assembler รัน S1–S5 และ S7 หลัง assemble สำเร็จ ส่วน S6 เป็นงานของ WCET tool

---

## 9. Grammar (EBNF)

```ebnf
program     = { line } ;
line        = [ label_def ] [ statement ] [ comment ] NEWLINE
            | annotation [ comment ] NEWLINE ;
label_def   = IDENT ":" ;
statement   = move | "nop" | directive ;
move        = source "->" port ;
source      = port | "#" expr ;
port        = REG | "null" | IDENT "." IDENT ;
REG         = "r" DIGITS ;                        (* 0..15 *)

directive   = ".text" | ".data"
            | ".word" expr { "," expr }
            | ".space" expr
            | ".equ" IDENT "," expr
            | ".func" IDENT | ".endfunc"
            | ".illegal" expr ;
annotation  = "@loop_bound" expr | "@task" IDENT ;

expr        = or_e ;
or_e        = xor_e { "|" xor_e } ;
xor_e       = and_e { "^" and_e } ;
and_e       = shift_e { "&" shift_e } ;
shift_e     = add_e { ( "<<" | ">>" ) add_e } ;
add_e       = mul_e { ( "+" | "-" ) mul_e } ;
mul_e       = unary { "*" unary } ;
unary       = ( "-" | "~" ) unary | primary ;
primary     = NUMBER | IDENT | "(" expr ")" ;

IDENT       = ( LETTER | "_" ) { LETTER | DIGIT | "_" } ;
NUMBER      = DIGITS_ | "0x" HEXDIGITS_ | "0b" BINDIGITS_ ;   (* "_" คั่นระหว่างหลักได้ *)
comment     = ";" { ANY_CHAR } ;
```

กฎที่ grammar ไม่ได้บังคับ (ชื่อ port ที่ถูกต้อง, ช่วงของค่า, section, ขอบของ function) อยู่ใน §5–§8

---

## 10. ตัวอย่างเต็ม

control loop ตามตัวอย่าง 3 ใน timing_model

```asm
.equ PERIOD, 100_000
.equ V_ACT,  1_000             ; sense-to-actuate (v ของ t_wait)
.equ DL,     2_000             ; deadline หลัง anchor

        .data
gains:  .word 186_659, 1_501_851, 6_766      ; KPQ, KDQ, KIQ (Q.16)

        .text
        #on_trap    -> trap.handler
        #0          -> tmr.t_sync
@task control
loop:   #PERIOD     -> tmr.t_advance         ; stall จนถึง anchor ใหม่
        io.encoder  -> r1                    ; sense ที่ anchor + 1
        #DL         -> tmr.t_arm
        #gains      -> mem.t_load
        mem.data_out -> r2                   ; KPQ
        ...                                  ; control law
        #V_ACT      -> tmr.t_wait
        r5          -> io.pwm_cmd            ; actuate ที่ anchor + 1001
        #0          -> tmr.t_clear
        #loop       -> pc.t_jump

.func on_trap
        #0          -> io.pwm_cmd            ; safe state
        trap.cause  -> telem.t_push
        trap.epc    -> telem.t_push
        #0          -> trap.t_halt
.endfunc
```

---

## 11. ยังไม่กำหนดในเอกสารนี้

- รูปแบบไฟล์ output (`.hex` ของ code และ data, symbol table, listing) กำหนดแยกก่อนเริ่มเขียน assembler
- การ include ไฟล์อื่น ยังไม่มีใน Phase 1
- macro ยังไม่มีใน Phase 1 ถ้าจะเพิ่ม listing ต้องแสดงจำนวน move ที่ macro ขยายออกมา
