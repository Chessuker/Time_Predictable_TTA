# Time-Predictable TTA Core + Hard Real-Time HIL Demo — Project Plan

**Board:** Digilent Arty A7-100T (xc7a100tcsg324-1) · **System clock:** 100 MHz
**Toolchain:** Vivado, SystemVerilog, SymbiYosys, Python (assembler / ISS / WCET tool / telemetry)
**สถานะ:** Planning · เริ่ม ~กลางกันยายน 2026

---

## 1. เป้าหมายและกรอบของโปรเจ็กต์

สร้าง processor แบบ **Transport-Triggered Architecture (TTA)** ที่ออกแบบให้ **timing เป็นส่วนหนึ่งของ semantics** กล่าวคือ เวลาที่โปรแกรมใช้ต้องคำนวณได้ล่วงหน้าจาก source code และโปรแกรมสั่งงานตามเวลาจริงได้ในระดับ instruction จากนั้นพิสูจน์ด้วย control loop ที่ควบคุม plant จำลองบน FPGA ตัวเดียวกัน (hardware-in-the-loop)

แนวคิดอ้างอิงจากงานวิจัยสาย **PRET (Precision-Timed) machines** และ time-predictable processor เช่น FlexPRET และ Patmos แต่ใช้ TTA เป็นพื้นฐานแทน RISC

### สิ่งที่โปรเจ็กต์จะพิสูจน์ (claims)

1. **WCET วิเคราะห์ได้แบบ static และ tight:** ค่า WCET ที่ tool คำนวณจาก assembly ต้อง **เท่ากับ** จำนวน cycle ที่วัดได้จริงบนฮาร์ดแวร์เมื่อป้อน input ที่เป็น worst-case path (ไม่ใช่แค่ไม่เกิน)
2. **Sampling/actuation instant ไม่ขึ้นกับ code path:** ด้วย timer instruction จุดเวลาที่อ่าน sensor และสั่ง actuator มี jitter เป็น 0 cycle ไม่ว่า control law จะวิ่ง branch ไหน
3. **Deadline miss ตรวจจับได้แน่นอน:** ถ้างานไม่เสร็จก่อน deadline ฮาร์ดแวร์ trap ที่ cycle ที่กำหนดพอดี ซึ่งพิสูจน์ด้วย formal verification

### สิ่งที่จะไม่อ้าง (non-goals)

- **ไม่อ้างว่าเร็วกว่า** soft-core ทั่วไป จุดขายคือ predictability ไม่ใช่ throughput
- ไม่ทำ RTOS, interrupt แบบ asynchronous, virtual memory หรือ cache
- ไม่อ้างว่าผ่านมาตรฐาน safety certification ใด ๆ
- ไม่อ้างว่า microcontroller แบบ bare-metal ที่ไม่มี cache ทำ deterministic ไม่ได้ ข้อแตกต่างที่อ้างคือ **ความสามารถในการวิเคราะห์และสั่งงานตามเวลาในระดับสถาปัตยกรรม**

---

## 2. ทำไมต้อง TTA

ใน TTA โปรแกรมมีคำสั่งเดียวคือ **move** (ย้ายข้อมูลจาก port หนึ่งไปอีก port หนึ่ง) การคำนวณเกิดเป็นผลข้างเคียงเมื่อเขียนลง **trigger port** ของ function unit (FU) ผลที่ได้สำหรับ hard real-time คือ

- ไม่มี instruction decoder ที่ซับซ้อน ทุก move ใช้ **1 cycle เท่ากัน**
- Latency ของแต่ละ FU เป็นค่าคงที่ที่ประกาศไว้ ทำให้ WCET กลายเป็น **การนับ** ไม่ใช่การประมาณ
- ไม่มี speculation, branch prediction หรือ cache ที่ทำให้เวลาขึ้นกับประวัติการทำงาน
- การเพิ่มความสามารถด้านเวลาทำได้ง่าย เพียงเพิ่ม **Timer FU** ที่มี trigger port

และเป็นก้าวแรกของ CGRA ในอนาคต ที่ใช้ TTA เป็น processing element จนกลายเป็น time-predictable accelerator fabric

---

## 3. สถาปัตยกรรมภาพรวม

```
                         ┌──────────────────────── Arty A7-100T ────────────────────────┐
                         │                                                               │
  Python host            │   ┌──────────┐     transport bus (1 move / cycle)             │
 ┌───────────────┐       │   │ Code ROM │──▶ ┌────────────────────────────────────┐     │
 │ assembler     │ .hex  │   │ (BRAM)   │    │  src mux  ───────────▶  dst decode │     │
 │ ISS (golden)  │──────▶│   └──────────┘    └──┬────┬────┬────┬────┬────┬────┬──┘     │
 │ WCET tool     │       │                      │    │    │    │    │    │    │        │
 │ telemetry/plot│◀──────│ UART 3 Mbaud     ┌───┴┐┌──┴─┐┌─┴──┐┌┴───┐┌┴──┐┌┴───┐┌┴───┐   │
 └───────────────┘       │   ▲              │ALU ││MUL ││CMP ││REG ││PC ││MEM ││TMR │   │
                         │   │              └────┘└────┘└────┘└────┘└───┘└────┘└────┘   │
                         │   │                                    ┌────┐  ┌────┐        │
                         │   └──────────── telemetry FU ◀────────│ IO │  │TRAP│        │
                         │                                        └─┬──┘  └────┘        │
                         │                                          │                   │
                         │                          ┌───────────────┴───────────────┐   │
                         │                          │ Plant model (RTL, fixed-point)│   │
                         │                          │ DC motor + encoder + PWM in   │   │
                         │                          └───────────────────────────────┘   │
                         └───────────────────────────────────────────────────────────────┘
```

### จุดเชื่อมกับงานเดิม

- ใช้ UART host link ที่ 3 Mbaud ซึ่ง stress test แล้วจาก GALS NoC สำหรับ telemetry
- ใช้ประสบการณ์ formal verification แบบ shadow model / peek port กับ timer และ core
- ในอนาคต TTA core นี้เป็น PE ของ CGRA และต่อเข้ากับ GALS interconnect ได้

---

## 4. Instruction set และ timing model

### รูปแบบ instruction (32 bits)

| Bits | Field | ความหมาย |
|---|---|---|
| 31:24 | `dst` | ID ของ destination port |
| 23 | `imm` | 1 = src เป็น immediate |
| 22:0 | `src` / `imm_val` | ID ของ source port หรือ immediate แบบ sign-extended 23 bits |

ค่าคงที่ที่เกิน 23 bits ใช้สอง move ผ่าน shift ใน ALU หรือ literal pool ใน MEM

### Function units (เริ่มต้น)

| FU | Ports | Latency | หมายเหตุ |
|---|---|---|---|
| ALU | `a`, `op`, `t_b` (trigger), `out` | 1 | add/sub/and/or/xor/shl/shr/sra, shift ใช้ barrel shifter เวลาคงที่ |
| MUL | `a`, `t_b`, `out_lo`, `out_hi` | 1 | map ลง DSP48 · **ไม่มี divider** (control law ใช้ fixed-point multiply + shift) |
| CMP | `a`, `t_b`, `flags` | 1 | eq/lt/ltu |
| REG | `r0`–`r15` | 0 | register file อ่าน/เขียนตรง |
| PC | `t_jump`, `cond`, `t_jz`, `t_jnz`, `link` | ดู timing rule | |
| MEM | `addr`, `t_load`, `data_in`, `t_store`, `data_out` | 1 | scratchpad BRAM, synchronous read |
| TMR | `now`, `t_delay_until`, `deadline`, `t_arm`, `t_clear` | ดู timing rule | |
| IO | `encoder`, `pwm_cmd` | 0 | เชื่อมกับ plant |
| TELEM | `data`, `t_push` | 0 | ส่ง record ออก UART ผ่าน FIFO **ไม่ block** (FIFO เต็ม → นับ drop) |
| TRAP | `cause`, `handler_addr` | — | deadline miss, illegal port |

### Timing rules (ต้องเขียนเป็นเอกสาร `docs/timing_model.md` และถือเป็นสัญญา)

1. ทุก move ใช้ **1 cycle พอดี** (1 cycle = 10 ns ที่ 100 MHz)
2. ผลลัพธ์ของ FU ที่มี latency L พร้อมอ่านหลัง trigger **L cycle** และคงค่าไว้จนกว่าจะ trigger ครั้งถัดไป
3. **อ่านผลก่อนครบ latency ถือเป็นข้อผิดพลาดของโปรแกรม** ซึ่ง assembler ต้องตรวจจับแบบ static ไม่ใช่ปล่อยให้ฮาร์ดแวร์ทำงานผิดเงียบ ๆ
4. Jump ที่ taken มี penalty คงที่ **P cycle** (จาก synchronous BRAM fetch) not-taken ไม่มี penalty ค่า P ต้องระบุชัดและ ISS ต้องจำลองตรงกัน
5. `t_delay_until(T)`: PC หยุดนิ่งจนถึง cycle `T` แล้วทำ move ถัดไปที่ cycle `T + D` โดย D เป็นค่าคงที่ที่ระบุไว้ ถ้า `T` ผ่านไปแล้ว ให้ทำต่อทันทีและตั้ง flag `late`
6. `t_arm(deadline)`: ถ้าไม่มี `t_clear` ก่อนถึง cycle `deadline` ฮาร์ดแวร์ trap **ที่ cycle นั้นพอดี**
7. Time base คือ **cycle counter 64 bits** ไม่ใช่ wall clock ภายนอก ทำให้ไม่มี CDC ในเส้นทางเวลา

---

## 5. Toolchain ฝั่ง host

### Assembler (`host/asm/`)

```asm
; ตัวอย่าง syntax
        #1000       -> r1              ; immediate
        r1          -> alu.a
        #ADD        -> alu.op
        r2          -> alu.t_b         ; trigger
        alu.out     -> r3              ; ผลพร้อมหลัง 1 cycle

@loop_bound 16
loop:   ...
        cmp.flags   -> pc.cond
        loop        -> pc.t_jnz

@task control period=100000 deadline=80000
```

- Labels, constants, `@loop_bound`, `@task`
- ตรวจ rule ข้อ 3 (อ่านก่อน latency ครบ) แบบ static
- Output `.hex` สำหรับ `$readmemh` และ symbol table สำหรับ ISS/WCET tool

### Cycle-accurate ISS (`host/iss/`)

- จำลองทุก timing rule ระดับ cycle เป็น **golden model**
- Export trace: `cycle, pc, src, dst, value` ใช้เทียบกับ trace จาก RTL simulation แบบ lockstep

### WCET tool (`host/wcet/`)

- สร้าง control-flow graph จาก assembly
- ใช้ `@loop_bound` แปลง loop ให้ขอบเขตจำกัด แล้วหา longest path (loop ที่ไม่มี annotation → error ไม่ใช่ warning)
- WCET = จำนวน move บน path + jump penalty + latency ที่ต้องรอ
- เริ่มจาก loop แบบมีโครงสร้าง (ไม่มี irreducible CFG) ซึ่ง assembler บังคับได้
- รายงานผล per task และเตือนเมื่อ WCET เกิน `deadline − (overhead ของ timer)`

---

## 6. Hardware-in-the-loop demo

### Plant: DC motor position control

เริ่มที่ **DC motor** เพราะเป็นระบบเชิงเส้น discretize เป็น fixed-point ได้ตรงไปตรงมา ส่วน inverted pendulum มี `sin()` ต้องใช้ CORDIC หรือ linearization จึงเก็บเป็น stretch

- สมการ electrical + mechanical แบบ discrete time ใน RTL, fixed-point Q-format ที่ตรวจความเสถียรกับ Python model ก่อน
- Plant step ทุก N cycle (เช่น 10,000 cycle = 100 µs ของเวลาจำลอง) ผูกกับ cycle counter เดียวกับ core
- Output เป็น encoder count, input เป็น PWM duty / voltage command

### Control task

- PI หรือ PID แบบ fixed-point, period เช่น 1 ms (100,000 cycle)
- โครงสร้างต่อ period: `delay_until(t_k)` → อ่าน encoder → คำนวณ → `delay_until(t_k + Δ)` → เขียน pwm_cmd → ส่ง telemetry
- การใส่ `delay_until` ก่อน actuate ทำให้ **sense-to-actuate latency คงที่เป๊ะ** ไม่ขึ้นกับ branch ใน control law (เช่น anti-windup, saturation)

### Telemetry (ต่อ period)

`period_idx, t_sense, t_actuate, encoder, pwm_cmd, flags(late/trap)` ประมาณ 24 bytes ที่ 1 kHz = 24 kB/s ต่ำกว่าเพดาน UART ราว 300 kB/s มาก

### Baseline สำหรับเทียบ

Soft-core ทั่วไปบนบอร์ดเดียวกัน (เช่น MicroBlaze ที่เปิด cache และใช้ timer interrupt) รัน control law เดียวกันกับ plant เดียวกัน พร้อม **background workload** ที่แย่ง cache/interrupt

ต้องเขียนเงื่อนไขการเทียบให้ชัดและยุติธรรม: baseline ไม่ได้ออกแบบมาเพื่อ real-time และถ้าปิด cache + ไม่มี background load ก็จะ deterministic ขึ้นมาก ผลที่รายงานควรแสดงทั้งสองกรณี

---

## 7. แผนงานรายเฟส

ประมาณการคิดแบบทำควบคู่กับงานอื่น ไม่ใช่ full-time ปรับได้ตามจริง

### Phase 0 — Spec (สัปดาห์ 1)

- [ ] Freeze instruction format, port map, latency ของทุก FU
- [ ] เขียน `docs/timing_model.md` ครบทุก rule พร้อมตัวอย่างนับ cycle ด้วยมือ
- [ ] เลือก Q-format ของ plant และ controller, จำลอง closed loop ใน Python (float แล้วค่อย fixed-point) ให้เสถียรก่อน

**Done เมื่อ:** นับ cycle ของโปรแกรมตัวอย่าง 3 ตัวด้วยมือแล้วได้ตัวเลขชัดเจน และ fixed-point Python model ของ closed loop เสถียร

### Phase 1 — Assembler + ISS (สัปดาห์ 2–3)

- [ ] Assembler: parse, labels, immediates, `.hex`, symbol table
- [ ] Static check ของ latency rule
- [ ] ISS แบบ cycle-accurate + trace export
- [ ] Test programs: Fibonacci, bubble sort ขนาดคงที่, fixed-point multiply, subroutine call/return

**Done เมื่อ:** ทุก test program ให้ผลถูกบน ISS และ assembler ปฏิเสธโปรแกรมที่อ่านผลก่อน latency ครบ

### Phase 2 — RTL core (สัปดาห์ 3–5)

- [ ] Transport bus, src mux, dst decode
- [ ] PC unit + code ROM (BRAM) + jump penalty ตาม spec
- [ ] ALU, MUL (DSP48), CMP, REG, MEM
- [ ] Testbench แบบ **lockstep trace compare** กับ ISS ทุก cycle
- [ ] Bring-up บน Arty: รัน test program แล้วส่งผลออก UART

**Done เมื่อ:** trace RTL ตรงกับ ISS ทุก cycle ในทุก test program และทำงานบนบอร์ดจริงได้ผลตรง, timing closure ที่ 100 MHz

### Phase 3 — Timer, deadline trap, formal (สัปดาห์ 6)

- [ ] TMR FU: `now`, `delay_until`, `arm`, `clear`, flag `late`
- [ ] TRAP FU + handler address
- [ ] Formal properties (SymbiYosys):
  - [ ] PC ไม่ขยับระหว่าง `delay_until` จนถึง `T` และขยับที่ `T + D` พอดี
  - [ ] ถ้า `arm(deadline)` แล้วไม่มี `clear` → trap ที่ cycle `deadline` พอดี ไม่เร็วกว่าและไม่ช้ากว่า
  - [ ] ถ้ามี `clear` ก่อน deadline → ไม่มี trap
  - [ ] ทุก move (ที่ไม่ใช่ delay/jump penalty) ใช้ 1 cycle พอดี
  - [ ] Output ของ FU คงค่าไว้จนกว่าจะ trigger ครั้งถัดไป
  - [ ] Cover: เกิด trap, เกิด `late`, เกิด delay ได้จริง
  - [ ] `fu_tmr` แบบระยะห่าง (`el`, `wrem`, `rem`) เทียบเท่ากับ spec ที่เขียนเป็นเวลา absolute (design_decisions: Timing closure ข้อ ข)
- [ ] Regression ที่ค้างจากรีวิว PR #3 (lockstep หรือ unit test):
  - [ ] Trap epc แบบเจาะจง: legality trap ที่ move N ต้องได้ `trap.epc == N` ส่วน deadline trap ต้องได้ epc เป็น move ถัดไปที่ยังไม่ได้รัน (isa.md §6)
  - [ ] TMR ที่ขอบพอดี: `now` = target − 1, target, target + 1 ของ `t_wait` และ `t_advance` และ `now` = deadline − 1, deadline, deadline + 1 ของ `t_arm`
  - [ ] Telemetry FIFO เต็มแล้ว UART pop กับ `telem.t_push` เกิดใน cycle เดียวกัน (pop ก่อน push, ไม่นับเป็น drop)
  - [ ] Integration `tta_core → FIFO → UART` ผ่าน `arty_tta_top`: word แรก, ระยะ 1320 cycle ต่อ word, word ติดกัน และ drop

**Done เมื่อ:** BMC + k-induction + cover ผ่านทุก property

### Phase 4 — WCET tool และการยืนยัน (สัปดาห์ 7–8)

- [ ] CFG builder + loop bound + longest path
- [ ] Hardware cycle counter วัด start/end ของแต่ละ task ผ่าน telemetry
- [ ] สำหรับทุก test program: หา input ที่ทำให้วิ่ง worst-case path แล้ววัดบนบอร์ด
- [ ] ตาราง WCET (static) เทียบ measured max ของทุก program

**Done เมื่อ:** static WCET **เท่ากับ** measured ที่ worst-case input ในทุก program และ measured ไม่เคยเกิน static ในการรัน input แบบสุ่ม

> **Decision gate ก่อนเดดไลน์ฝึกงานธันวาคม:** Phase 0–4 เพียงพอเป็นงานพอร์ตที่สมบูรณ์ในตัวเอง ("processor ที่ WCET พิสูจน์ได้และตรงกับฮาร์ดแวร์") ถ้าเวลาเริ่มกระทบ GALS NoC ให้หยุดตรงนี้แล้วเขียน README ก่อน HIL demo เป็นส่วนเสริมที่ทำหลังได้

### Phase 5 — HIL plant + control loop (สัปดาห์ 9–10)

- [ ] Plant RTL จาก Python fixed-point model, ตรวจ step response ใน simulation ตรงกับ Python
- [ ] IO FU เชื่อม plant
- [ ] Control task เป็น TTA assembly พร้อม `@task` และ `@loop_bound`
- [ ] TELEM FU + Python receiver + plot (position, command, t_sense/t_actuate)
- [ ] Deadline trap test: จงใจยืด control law ให้เกิน deadline แล้วยืนยันว่า trap เกิดที่ cycle ที่ถูกต้อง

**Done เมื่อ:** closed loop เสถียรบนบอร์ด, step response ตรงกับ Python model, WCET tool ยืนยันว่า control task อยู่ใต้ deadline

### Phase 6 — Baseline และ benchmark (สัปดาห์ 11)

- [ ] Baseline soft-core รัน control law + plant เดียวกัน
- [ ] วัด: period jitter, sense-to-actuate jitter (min / p50 / p99 / max), deadline miss count
- [ ] ทดสอบ baseline ทั้งแบบมีและไม่มี background workload, เปิด/ปิด cache
- [ ] Resource utilization, fmax, power ของ TTA core เทียบ baseline

รายงานตัวเลขอย่างตรงไปตรงมา รวมกรณีที่ baseline ทำได้ดี

### Phase 7 — เอกสาร (สัปดาห์ 12)

- [ ] `README.md`: ปัญหา, ทำไม TTA, claims + non-goals, ผล benchmark, วิธีรัน
- [ ] `docs/timing_model.md`, `docs/wcet_method.md`, `docs/design_decisions.md`
- [ ] Block diagram, timing diagram ของ `delay_until` และ deadline trap
- [ ] กราฟ jitter histogram TTA เทียบ baseline และตาราง WCET static vs measured
- [ ] วิดีโอเดโมสั้น ๆ

---

## 8. Metrics สรุป

| Metric | วิธีวัด | เป้าหมาย |
|---|---|---|
| WCET tightness | static WCET − measured worst-case | 0 cycle |
| Sense-to-actuate jitter | จาก telemetry `t_actuate − t_sense` | 0 cycle |
| Period jitter | ส่วนต่างของ `t_sense` ติดกัน − period | 0 cycle |
| Deadline trap accuracy | formal + test ที่จงใจเกิน | trap ที่ cycle `deadline` พอดี |
| ISS/RTL equivalence | lockstep trace compare | ตรงทุก cycle |
| Timing closure | Vivado routed WNS/WHS | ผ่านที่ 100 MHz |

---

## 9. Stretch goals

- **Inverted pendulum** พร้อม CORDIC FU (fixed iteration → latency คงที่)
- **Time-triggered schedule table** รันหลาย task ด้วย period ต่างกันบน core เดียว พร้อม schedulability check ใน WCET tool
- **Single-path code transformation** ให้ทุก path ใช้เวลาเท่ากัน (ป้องกัน timing side channel ด้วย ซึ่งเชื่อมกับความสนใจด้าน security)
- หลาย transport bus ต่อ cycle พร้อมพิสูจน์ว่าไม่มี bus conflict
- ต่อยอดเป็น PE ของ CGRA

---

## 10. ความเสี่ยงและทางรับมือ

| ความเสี่ยง | ผลกระทบ | ทางรับมือ |
|---|---|---|
| Scope ใหญ่ (assembler + ISS + RTL + WCET + plant) | ไม่เสร็จทันธันวาคม | Decision gate หลัง Phase 4, HIL เป็นส่วนเสริม |
| Timing model ใน spec, ISS และ RTL ไม่ตรงกัน | claim เรื่อง WCET พัง | freeze spec ใน Phase 0 + lockstep trace compare ทุก cycle |
| Synchronous BRAM fetch ทำให้ jump timing ซับซ้อน | นับ cycle ผิด | กำหนด penalty P ชัดตั้งแต่ spec และมี formal property |
| Fixed-point plant ไม่เสถียรหรือ overflow | demo ล้ม | ทดลอง Q-format ใน Python ก่อนเขียน RTL, saturating arithmetic |
| Baseline ไม่ยุติธรรม | ผลเทียบไม่น่าเชื่อถือ | รายงานหลาย configuration และบอกเงื่อนไขชัด |
| Telemetry FIFO เต็ม | ข้อมูลหาย | TELEM ไม่ block core, นับ drop และรายงาน |

---

## 11. โครงสร้าง repository

```
tta-rt/
├── rtl/
│   ├── core/       tta_bus.sv, pc_unit.sv, code_rom.sv, tta_core_top.sv
│   ├── fu/         fu_alu.sv, fu_mul.sv, fu_cmp.sv, fu_reg.sv, fu_mem.sv,
│   │               fu_timer.sv, fu_trap.sv, fu_io.sv, fu_telem.sv
│   ├── plant/      dc_motor_plant.sv
│   ├── common/     uart (จาก GALS NoC), sync, fifo
│   └── top/        arty_tta_top.sv
├── tb/             lockstep testbench + per-FU tests
├── formal/         *.sby + properties
├── constraints/    arty.xdc, timing.xdc
├── host/
│   ├── asm/        assembler
│   ├── iss/        cycle-accurate simulator
│   ├── wcet/       CFG + longest-path analysis
│   ├── plant_model/ Python float/fixed-point model
│   └── telemetry/  receiver + plotting
├── programs/       test programs + control task (.tasm)
├── baseline/       soft-core project + C control law
├── docs/           timing_model.md, wcet_method.md, design_decisions.md, benchmarks.md
└── README.md
```

---

## 12. เรื่องที่เล่าในพอร์ตได้

- ออกแบบ processor ที่ **timing เป็นสัญญาของสถาปัตยกรรม** ไม่ใช่ผลพลอยได้
- สร้าง toolchain ครบวงจร (assembler, cycle-accurate ISS, WCET analyzer) และยืนยันว่าตรงกับ RTL ทุก cycle
- พิสูจน์พฤติกรรมด้านเวลาของ timer และ deadline trap ด้วย formal verification
- เชื่อมงานสถาปัตยกรรมกับปัญหาจริงผ่าน closed-loop control แบบ hardware-in-the-loop และวัดผลเทียบ baseline อย่างซื่อตรง
- เป็นฐานของ time-predictable CGRA ในอนาคต
