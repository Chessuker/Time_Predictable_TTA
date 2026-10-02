# Reading notes — Tier 1 (5 lectures)

สรุปจากการอ่าน paper ข้อ 1–5 ใน [reading_list.md](reading_list.md) เขียนเป็น lecture เรียงตามลำดับเวลาของแนวคิด (2006 → 2014) ท้ายไฟล์มีรายการ decision ที่กระทบ Phase 0 โดยตรง

---

## Lecture 1 — Ip & Edwards 2006: กำเนิด `deadline` instruction

**โจทย์:** จะเขียน software ที่ทำงานตามจังหวะเวลาระดับ cycle ได้ยังไง ทางเลือกเดิมมีสองทาง ทางแรกคือนับ cycle เองแล้วยัด NOP ซึ่งต้องรู้เวลาทุก instruction และพังทันทีเมื่อมี loop ที่จำนวนรอบไม่คงที่ ทางที่สองคือ timer interrupt + scheduler ซึ่งได้ความละเอียดแค่ระดับ ms

**ไอเดีย:** เพิ่ม instruction เดียวเข้า ISA

```
dead  T, Rs      ; T = timer $t0..$t3, ค่าจาก register
deadi T, imm16   ; ค่าจาก immediate
```

- timer แต่ละตัวเป็น counter 16 bit นับลง 1 ต่อ cycle หยุดที่ 0
- เมื่อ execute `deadline` จะ**หยุดรอ**จน timer เป็น 0 → cycle ถัดไป reload ค่าใหม่แล้วรัน instruction ถัดไปทันที
- ถ้า timer เป็น 0 อยู่แล้ว (deadline ผ่านไปแล้ว) → reload แล้วไปต่อเลย **ไม่มี flag ไม่มี error**

**semantics สำคัญ (Fig. 2):** วาง `deadline` สองตัวคร่อม block → block นั้นใช้เวลา**อย่างน้อย** N cycle พอดี ถ้าโค้ดใช้น้อยกว่านั้น ตัวอย่างในเปเปอร์ `deadi $t0,8 / add / deadi $t0,10 / add` ทำให้ add สองตัวห่างกัน 8 cycle เป๊ะ โดย deadline ตัวที่สองรอ 7 cycle ชดเชย add 1 cycle เอง

สองสิ่งที่เปเปอร์นี้ชี้ให้เห็นแล้วยังใช้ได้ถึงวันนี้

1. **โปรแกรมไม่ต้องรู้ execution time ของตัวเองเพื่อให้ timing ถูก** ต่างจาก NOP padding ที่ต้องรู้ WCET ก่อนถึงจะเขียนได้ WCET analysis ยังต้องทำอยู่ แต่ทำเพื่อ**พิสูจน์**ว่าทัน deadline ไม่ใช่เพื่อ**สร้าง** timing
2. **temporal binary compatibility:** โปรแกรมที่ทัน deadline ทุกจุด ย้ายไป CPU เร็วกว่าแล้วยังได้ timing เดิม แค่ปรับตัวเลข deadline ตาม clock

**ฮาร์ดแวร์:** MIPS-like 16 bit, single-cycle ไม่มี pipeline, 25 MHz บน Spartan-3 XC3S200 นี่คือสถาปัตยกรรมที่**ใกล้ TTA ของเราที่สุด**ในบรรดา 5 เปเปอร์ เพราะไม่มี multithreading มาซับซ้อน

**Demo:** VGA text controller 78 บรรทัด asm เทียบ VHDL 450 บรรทัด และ UART receiver พร้อม auto-baud 35 บรรทัด ใช้ timer สองตัวแยกกัน ($t1 คุม line, $t0 คุม character) ทำให้เปลี่ยนจำนวน column ได้โดยไม่กระทบ timing ของ line

**สอง idiom ที่เปเปอร์สอน:**
- `deadline` ต้น block = *ตั้ง* timer, `deadline` ตัวถัดไป = *รอ* จริง
- `deadline` ตัวเดียวใน loop = บังคับ period ของ loop

**จุดอ่อน (เปเปอร์ไม่ได้พูดเอง แต่ Liu ชี้ทีหลัง):** timer เป็น countdown แบบ relative ถ้าพลาด deadline หนึ่งครั้ง จุดอ้างอิงเวลาจะเลื่อนไปทั้งหมด (drift สะสม) และไม่มีทางตรวจจับว่าพลาด

---

## Lecture 2 — Lickly et al. 2008: ปรัชญา PRET + deadline บน pipeline จริง

**ปรัชญา PRET:** สถาปัตยกรรมสมัยใหม่ตกหลุม unpredictability เพราะไล่ average-case อย่างเดียว ต้องคิดใหม่เหมือน RISC revolution เปเปอร์วางหลัก 5 ข้อ: scratchpad แทน cache, thread-interleaved pipeline ไม่มี bypass, timing control ที่ระดับ ISA, time-triggered communication, และภาษาที่มี time ใน semantics

**สถาปัตยกรรม:** SPARC v8 + 6-stage pipeline + 6 hardware thread round-robin แต่ละ stage มี thread คนละตัวเสมอ → ไม่มี data hazard ไม่ต้อง forwarding เมื่อ thread ต้องรอ (memory, deadline) ใช้ **replay** คือ thread นั้น re-execute instruction เดิมซ้ำจนเสร็จ thread อื่นไม่กระทบ

**Memory wheel:** main memory แชร์ผ่านตารางเวลาคงที่ แต่ละ thread ได้ window 13 cycle วนรอบทุก 78 cycle ถ้าขอถูกจังหวะได้ 13 cycle ถ้าพลาดจังหวะพอดีรอได้ถึง 90 cycle จุดสำคัญคือ **latency ขึ้นกับ cycle ที่ขอเท่านั้น ไม่ขึ้นกับ thread อื่น**

**Deadline instruction:** syntax เดียวกับ Ip & Edwards มี 12 register ต่อ thread (8 ตัวนับ instruction cycle = ทุก 6 clock, 4 ตัวขับด้วย PLL) immediate 13 bit block ผ่าน replay ใน regacc stage ถ้าหมดอายุแล้วก็แค่ reload ต่อ ระบุไว้ว่า *"later, we plan to allow the architecture to throw an exception when a deadline is missed"* (Liu ทำจริงใน Lecture 3)

**Producer/consumer ไม่ใช้ lock:** ใช้เวลาแทน mutex ตั้ง offset เริ่มต้นต่างกัน (28 vs 41 → 168 vs 246 cycle ห่างกัน 78 = 1 รอบ wheel) แล้วให้ทุก thread วน loop ด้วย deadline 26 = 156 cycle = 2 รอบ wheel พอดี ทำให้ producer เขียนรอบแรก consumer/observer อ่านรอบสอง เสมอ

**Section 6.4 คือส่วนที่มีค่าที่สุดสำหรับเรา** ผู้เขียนยอมรับตรงๆ ว่า

> การคำนวณ timing constraint ทำด้วยมือ ผิดพลาดง่าย ต้องคำนวณใหม่ทุกครั้งที่แก้โค้ดหรือ optimize และไม่มีทางมั่นใจว่าผลถูก

และตั้งเป้าว่า *timing error ควรตรวจจับได้ง่ายเท่า syntax error* นี่คือเหตุผลตรงตัวว่าทำไมแผนของเราถึงต้องมี assembler static check + ISS + WCET tool ตั้งแต่ Phase 1 ไม่ใช่ทำทีหลัง

**Benchmark:** ช้ากว่า LEON3 เฉลี่ย 3.54 เท่า เพราะ 5 ใน 6 thread ว่าง ผู้เขียนบอกเองว่าการเทียบนี้ไม่ได้วัด predictability ซึ่งเป็นจุดแข็งจริง เราควรเขียน non-goal แบบเดียวกันใน README

---

## Lecture 3 — Liu 2012 (PhD thesis, PTARM): ชุด timing instruction ฉบับเต็ม

อ่านเฉพาะ §1.1.1, §2.3, §3.4.4, §3.6 (บทอื่นเป็น pipeline/DRAM controller ไม่เกี่ยวกับเรา)

### สองทางในการใส่เวลาเข้า ISA (§2.3)

Liu แยกชัดว่ามีสองแนว

| แนว | ความหมาย | ข้อดี | ข้อเสีย |
|---|---|---|---|
| **Constant-time ISA** | ทุก instruction มี execution time คงที่เป็นส่วนหนึ่งของ ISA | reasoning ง่าย ผูก timing กับ program ไม่ใช่ platform | ล็อก microarchitecture ห้ามเร็วขึ้น |
| **Timing instructions** | เพิ่ม instruction ที่*ควบคุม*เวลา ส่วน instruction อื่นเร็วเท่าไหร่ก็ได้ | microarch พัฒนาต่อได้ | ต้องพึ่ง WCET analysis แยกอยู่ดี |

Liu เลือกแนวที่สอง **TTA ของเราเลือกแนวแรกโดยเจตนา** (ทุก move = 1 cycle, FU latency คงที่) เพราะเป้าคือ WCET = การนับ ไม่ใช่การประมาณ และเราไม่สนใจว่า microarch รุ่นหน้าจะเร็วขึ้นไหม ควรเขียนเหตุผลนี้ลง `design_decisions.md` โดยอ้าง §2.3 ของ Liu ตรงๆ

### Platform clock

64-bit **nanosecond** counter บวก 10 ทุก cycle ที่ 100 MHz reset เป็น 0 เหตุผลที่เลือก ns แทน format แบบ IEEE 1588 (sec + ns) คือให้ programmer บวกลบ timestamp ด้วย add/adc ธรรมดาได้โดยไม่ต้องจัดการ overflow ข้าม field 64 bit ns ครอบคลุม 584 ปี timestamp ถูก latch ตอน **fetch** ของแต่ละ instruction

### สี่ instruction (Table 2.2 / 3.2)

| Instruction | ทำอะไร | cycle |
|---|---|---|
| `get_time` | โหลด 64-bit timestamp ลง rd, rd+1 | 2 (register file มี write port เดียว) |
| `delay_until(ts)` | หยุด PC จน now ≥ ts ถ้า now ผ่าน ts แล้ว = NOP | ≥1 |
| `exception_on_expire(ts)` | ลงทะเบียน ts ใน deadline slot ฮาร์ดแวร์เทียบทุก thread cycle เมื่อ now ≥ ts → trap แล้ว slot ปิดตัวเอง | 1 |
| `deactivate_exception` | ปิด slot ถ้าไม่มี slot เปิดอยู่ = ไม่ทำอะไร | 1 |

มี **deadline slot เดียวต่อ thread** หลาย deadline จัดการใน software ประโยคสำคัญของ Liu: *ไม่มี instruction ไหน enforce execution time ทั้งหมดเป็นแค่กลไกให้ monitor/detect/react* สถาปัตยกรรมข้างล่างต้อง predictable เองถึงจะได้ WCET แน่น

### สี่ scenario ของ deadline (Fig. 2.14)

- **A** ทำ task ให้จบก่อน แล้วค่อยเช็คว่าเลย deadline ไหม (late detection ด้วย `get_time` + เทียบใน software) ใช้เมื่อ task แตะ I/O ที่ปล่อยค้างไม่ได้
- **B** พลาดปุ๊บ trap ทันที ใช้ `exception_on_expire` + `deactivate_exception`
- **C** แค่กันไม่ให้เกิน ถ้าเสร็จก่อนก็ไปต่อเลย (โค้ดเดียวกับ B)
- **D** กันไม่ให้เกิน **และ** ไม่ให้ task ถัดไปเริ่มก่อนเวลา = B + `delay_until` ต่อท้าย

**กฎลำดับที่ต้องจำ:** ใน scenario D ต้อง `deactivate_exception` **ก่อน** `delay_until` เสมอ ถ้าสลับ `delay_until` จะพาเวลาเลย deadline ไปทั้งที่ยังไม่ deactivate → trap ทุกครั้งแม้ task ไม่ได้พลาด

### Timed loop สามแบบ (Fig. 2.15) — เรื่องที่กระทบ control task ของเราตรงที่สุด

1. `get_time` **ในลูป** ทุกรอบแล้วบวก period → ถ้ารอบใดพลาด deadline รอบถัดไปทั้งหมดเลื่อนตาม (drift สะสม ไม่ฟื้น)
2. `get_time` **ครั้งเดียวนอกลูป** แล้วบวก period สะสมในลูป → รอบที่พลาดถูกรอบถัดไปที่เสร็จเร็วชดเชยเอง period กลับมาตรง
3. **Self-compensating:** ต้นลูปเช็คว่ารอบก่อนพลาดไหม ถ้าพลาดรัน task เวอร์ชันสั้น แต่ต้องลบ offset ค่าคงที่ (80 ns ในระบบเขา) ชดเชย loop overhead ไม่งั้นจะตรวจเจอว่าพลาดทุกรอบเพราะ `delay_until` เองทำให้เวลาเลย deadline เสมอ

สำหรับเรา: control task ต้องใช้แบบ 2 คือ `t_k = t_0 + k × period` คำนวณจาก t_0 ที่อ่านครั้งเดียว ห้ามอ่าน `now` มาเป็นฐาน deadline ใหม่ทุกรอบ

### Jitter analysis (§3.6.2) — ตัวเลขที่ต้องมีคู่กันใน spec เรา

- thread เห็นเวลาเปลี่ยนทุก 4 processor cycle = 40 ns → `delay_until` มี jitter ได้ถึง 39 ns
- instruction หลัง `delay_until` เริ่มที่ **≥ ts + 1 thread cycle** (Fig. 3.18: offset 161 ns → รันจริงที่ t+240)
- exception ก็สังเกตที่ granularity เดียวกัน (deadline t+161 → trap ที่ t+200)
- **response time ของ timer exception = 8 thread cycle คงที่** (1 cycle ตอน throw + 7 cycle vector/setup code) โดยห้ามแตะ DRAM ใน 3 cycle แรกหลัง trap

Liu ทำได้แค่ "ไม่เกิน 39 ns" เพราะ thread interleaving ของเราเป็น single-thread 1 move/cycle จึง**ทำให้ jitter เป็น 0 cycle ได้จริง** และค่า D ของเราคือสิ่งเดียวกับ "+1 thread cycle" ของ Liu แต่นิยามเป๊ะได้

### Exception semantics (§3.6.4, Table 3.6)

ตอน trap ที่ cycle t+360 instruction ที่ address นั้น**ไม่ complete** address ถูกเก็บลง link register แล้วมา re-execute หลัง handler return นี่คือคำถามที่ spec เราต้องตอบ: ที่ cycle == deadline พอดี move ที่กำลังจะรันถูก squash หรือถูก commit ก่อน

---

## Lecture 4 — Zimmer et al. 2014 (FlexPRET): timing instruction บน RISC-V + mixed criticality

**โจทย์ต่าง:** mixed-criticality คือ task หลายระดับความสำคัญบน core เดียว ต้องการ isolation ให้ hard real-time แต่ไม่อยากทิ้ง cycle เปล่า

**สถาปัตยกรรม:** RISC-V 5-stage เขียนด้วย Chisel รองรับ 1–8 hardware thread สลับ thread ได้**ทุกรูปแบบ** (ต่างจาก PTARM ที่บังคับ round-robin) มี forwarding/stall/flush แต่เช็ค thread ID ด้วย แบ่ง thread เป็น

- **HRTT** (hard) ถูก schedule ที่ slot คงที่เท่านั้น → scheduling frequency คงที่ → thread cycle cost ต่อ instruction คงที่ (branch taken = 3/2/1 thread cycle ที่ frequency 1, 1/2, ≤1/3)
- **SRTT** (soft) ใช้ cycle ที่เหลือแบบ round-robin รวมถึง cycle ที่ HRTT หลับหรือรอ `delay_until`

**Timing instructions (§III-D):** clock 64-bit ns เหมือน PTARM

| RISC-V ext | ความหมาย |
|---|---|
| `GTL r2` / `GTH r1` | อ่าน low แล้ว **hardware latch high ไว้** อ่าน high ทีหลังได้ค่าจากจุดเวลาเดียวกัน = atomic โดยไม่ต้องมี 64-bit path |
| `DU r1,r2` | delay until (replay จน now ≥) cycle ที่รอถูกยกให้ SRTT ไม่ทิ้งเปล่า |
| `EE r1,r2` | exception on expire handler address ตั้งผ่าน control register (`MTPCR`) 1 slot ต่อ thread |
| `DE` | deactivate |
| `TS` | thread sleep ใช้คู่ `EE` เป็น timer wakeup |

**ตัวอย่าง control loop ในเปเปอร์** (idiom เดียวกับ Liu แบบ 2):

```c
get_time(h,l);
while(1){ add_ms(h,l,10); exception_on_expire(h,l,handler); compute_task(); delay_until(h,l); }
```

สังเกตว่า `EE` มาก่อน task และ `DU` ปิดท้าย ลำดับตรงกับ scenario D ของ Liu

**WCET:** HRTT วิเคราะห์ static ได้ตรงไปตรงมา SRTT ต้องใช้ measurement เพราะ cycle stealing วัดง่ายเพราะ timing instruction ให้ timestamp แม่นในตัว

**FPGA:** Virtex-5, 80 MHz, 16 kB I-SPM + D-SPM ทดสอบด้วย Mälardalen benchmark ทั้ง simulator (Chisel C++) และบอร์ดจริงเทียบผลกัน ซึ่งเป็น flow เดียวกับ lockstep ISS/RTL ของเรา

**Open source:** [github.com/pretis/flexpret](https://github.com/pretis/flexpret) ดู timer unit ใน Chisel ได้ตอน Phase 3

---

## Lecture 5 — Bhagyanath & Schneider 2014: TTA ในฐานะ PRET machine

**Thesis ของเปเปอร์:** TTA schedule ทุกอย่างแบบ static ที่ระดับ data transport → **ไม่ต้อง model pipeline ใน WCET analysis เลย** และยัง performance เท่า pipelined processor

**เหตุผลหลัก:** RAW hazard ที่ pipeline แก้ด้วย forwarding TTA แก้ด้วยการ move ผลจาก output port ของ FU ตรงเข้า input port ของ FU ถัดไป (`Sub.o -> And.i`) ซึ่ง compiler เห็นและกำหนดเองที่ compile time ผลคือ**เวลาของ basic block บวกกันตรงๆ ได้** ไม่มี pairwise/long timing effect ข้าม block ตัวอย่างในเปเปอร์: pipeline ใช้ 15 cycle แต่ถ้าบวก block แยก (15+4+6=25) ต้องลบ overlap AB=-4, BC=-3, ABC=-3 ถึงจะได้ 15 ส่วน TTA หลัง code motion ได้ block 4+1+10 = 15 บวกตรงๆ

**Branch:** เงื่อนไข evaluate ใน cycle เดียวกับที่ PC update ผ่าน **guarded execution** (Abacus ใช้ 2 cycle)

**Experiment:** TTA fully connected เขียนใน Quartz (synchronous language) register 8×8 bit, ALU/MUL/DIV/LSU(2 cycle)/GCU เทียบ "Abacus" MIPS 5-stage สองรุ่น (stall / forward) ไม่มี cache ทั้งคู่ benchmark จาก Mälardalen ในภาษา assembly รันจำลอง ~10k รอบทั่ว input space

- TTA เร็วกว่า Abacus-forwarded **19.66%** (avg), **16.34%** (worst)
- **WCET tool ของเขา = นับจำนวน instruction ต่อ basic block + longest path ใน CFG** เหมือนแผนเราเป๊ะ
- error ระหว่าง predicted vs measured WCET: **TTA 2.94%** vs Abacus 12.14% ส่วนที่คลาดของ TTA มาจาก `bs` (binary search) เพราะ tool **ไม่มี infeasible path detection**

**ข้อจำกัดที่ต้องรู้:** ไม่มี timing instruction, ไม่มี cache, benchmark เล็กมาก (sort 3 element, fibonacci ≤ 13), simulation เท่านั้นยังไม่ลง FPGA, และ future work ของเขาคือ*เพิ่ม* cache/dynamic scheduling ซึ่งเดินสวนทางกับเรา

**นัยต่อเรา:**
- ยืนยัน thesis "WCET = counting" และโครง WCET tool ของเรา
- ช่องว่างที่เราเติม: timing instruction + deadline trap + formal proof + FPGA จริง ซึ่งเปเปอร์นี้ไม่มีเลย
- **2.94% ของเขาเป็นคำเตือน:** claim "static WCET เท่า measured 0 cycle" ของเราจะพังทันทีถ้า test program มี infeasible path ต้องเลือก program ที่ทุก path feasible หรือให้ tool รับ path annotation จาก user

---

## Decision ที่กระทบ Phase 0 (ฉบับแก้ 2026-09-25 หลังเสนอ Timing Unit)

### แนวคิด Timing Unit (ตกลงแล้ว)

TMR เป็นยูนิตที่เป็นเจ้าของเลขคณิตของเวลาทั้งหมด state ภายในเป็น 64 bit (`now`, `anchor`, `deadline`) และ comparator อยู่ในยูนิต ส่วนค่าที่วิ่งบน transport bus เป็น 32 bit ทุกตัว ต่างจาก PTARM และ FlexPRET ที่โหลด timestamp ลง general register แล้วให้โปรแกรมบวกเอง

ผลที่ตามมา
- loop overhead compensation ของ Liu §3.6.3 (offset 80 ns) หายไป เพราะการบวก period ในฮาร์ดแวร์ใช้ 0 cycle
- timed loop แบบที่ drift (Liu Fig. 2.15 แบบ 1) เขียนไม่ได้ เพราะโปรแกรมทำได้แค่สั่งให้ anchor บวก period ทับตัวเอง
- `late` นิยามได้ในฮาร์ดแวร์ คือ ณ ตอนที่ `t_advance` ถ้า `now > anchor` อยู่แล้วแปลว่ารอบก่อนเกิน period
- formal property เป็น local ต่อ TMR บวกสัญญาณ gate ไปที่ PC และเนื่องจาก counter เพิ่มทีละ 1 เงื่อนไข `now >= deadline` จะเป็นจริงครั้งแรกตอน `now == deadline` พอดี
- TELEM snapshot counter เองได้โดยไม่ต้องผ่าน bus
- มีทางออกสำรองคือ `tmr.now_lo` / `tmr.now_hi` ใช้ 2 move และ latch ค่าไว้ตอนอ่าน lo แบบเดียวกับ GTL/GTH ของ FlexPRET มีไว้สำหรับ debug และ time sync ในอนาคต

Port sketch (operation ตาม A1 แล้ว แต่เลข port ID ยังไม่กำหนด)

| Port | ทิศ | ทำอะไร |
|---|---|---|
| `tmr.t_sync` | dst/trigger | `anchor = now` ใช้ตอน init |
| `tmr.t_advance` | dst/trigger | `anchor += v` แล้ว stall PC จน `now >= anchor` |
| `tmr.t_wait` | dst/trigger | stall จน `now >= anchor + v` anchor ไม่ขยับ |
| `tmr.t_arm` | dst/trigger | `deadline = anchor + v`, `armed = 1` |
| `tmr.t_clear` | dst/trigger | `armed = 0` |
| `tmr.elapsed` | src | `now - anchor` (32 bit) |
| `tmr.flags` | src | `{late, armed, trapped}` |

### ปิดแล้ว

| ข้อเดิม | เหตุผล |
|---|---|
| 1 time base | cycle counter 64 bit อยู่ภายในยูนิตเท่านั้น ไม่ต้องตัดสินเรื่อง ns กับ cycle |
| 2 bus width | ค่าบน bus เป็น 32 bit ทุกตัว |
| 3 absolute vs countdown | ใช้ anchor ในฮาร์ดแวร์ ไม่มี drift โดยโครงสร้าง |
| 4 idiom `t_k = t_0 + kP` | ฮาร์ดแวร์ทำให้ เหลือแค่เป็น test program |
| 10 ประกาศ constant-time ISA | เป็นงานเขียนเอกสาร ใส่ใน `design_decisions.md` อ้าง Liu §2.3 |
| 12 อ้าง Lickly §6.4 | เป็นงานเขียนเอกสาร ใส่ใน README |
| 13 non-goal เรื่อง throughput | เป็นงานเขียนเอกสาร ใส่ใน README ตามแนว Lickly §7 |

### กลุ่ม A: ต้องตอบก่อน freeze port map และ `timing_model.md`

- [x] **A1. ชุด operation** *(ตัดสิน 2026-09-26)* ใช้ 5 operation นี้

  | Operation | ความหมาย |
  |---|---|
  | `t_sync` | `anchor ← now` |
  | `t_advance(v)` | `anchor ← anchor + v` แล้ว stall จนถึง anchor |
  | `t_wait(v)` | stall จน `now >= anchor + v` โดย**ไม่เปลี่ยน anchor** |
  | `t_arm(v)` | `deadline ← anchor + v`, `armed ← 1` |
  | `t_clear` | `armed ← 0` |

- [x] **A2. encode `v`** *(ตัดสิน 2026-09-26)* TMR ตีความค่า 32 bit ที่มาจาก bus เป็น **unsigned** ถ้า `v` มาจาก register ใช้ได้ถึง 2^32 − 1 ≈ 42.9 s
  - ผลที่ตามมาจาก instruction format: immediate ถูก sign-extend แบบเดียวกันทุก move (§4 ของแผน) ทำให้ช่วงที่ใช้ได้กับ tmr port คือ **0 ถึง 4,194,303** (2^22 − 1 ≈ 41.9 ms) ถ้า imm มี bit 22 เป็น 1 ค่าบน bus จะกลายเป็น 0xFFC0_0000 ขึ้นไป ซึ่งพอตีความแบบ unsigned แล้วเป็นการรอราว 43 วินาที
  - assembler ต้องปฏิเสธ immediate ที่ติดลบหรือเกิน 4,194,303 เมื่อ dst เป็น tmr port ค่าที่ใหญ่กว่านี้ให้ผ่าน register
- [x] **A3. D = 1** *(ตัดสิน 2026-09-26 ตามความรู้ปัจจุบัน)* move ถัดไปรันที่ cycle `target + 1` และถ้าตอนสั่งก็เลยเวลาแล้ว timing move นั้นใช้ 1 cycle เท่ากับ move ปกติ
- [x] **A4. Trap semantics** *(ตัดสิน 2026-09-26 ตามข้อแนะนำ)* move ใน cycle `deadline` ถูก squash และ PC ของมันเก็บลง `trap.epc`, trap แทรก stall ของ `t_wait`/`t_advance` ได้, และ trap entry เป็น forced taken jump ทำให้ **H = P**
- [x] **A5. จำนวน slot** *(ตัดสิน 2026-09-26)* ตอนนี้ทำแค่ anchor 1 กับ deadline 1 แต่**กันเลข port ID ไว้**สำหรับขยาย slot ในอนาคต
- [x] **A6. Overflow** *(ตัดสิน 2026-09-26: `elapsed` **saturate ที่ 0xFFFF_FFFF** ส่วนจะมี bit `elapsed_sat` ใน `tmr.flags` หรือไม่ ให้ตัดสินตอนกำหนด layout ของ flags ส่วนกรณี `now < anchor` ปิดไปแล้วเพราะ B1 ห้าม `t_advance` ระหว่าง armed)*
  - **ส่วนที่ไม่ต้องตัดสินใจ:** immediate ที่เกิน range ถูก assembler ปฏิเสธตาม A2 ส่วน `v` ที่คำนวณด้วย ALU แล้ววน 32 bit ถือเป็นบั๊กของโปรแกรม TMR ได้ค่าที่ถูกรูปแบบ ผลคือรอสั้นลงและ `late` ติด
  - **สมมติฐานที่ต้องเขียนลง `timing_model.md`:** `now`, `anchor` และ `deadline` เป็น 64 bit ไม่วนรอบภายใน 2^64 cycle ≈ 5,845 ปีที่ 100 MHz
  - **ต้องตัดสิน:** `elapsed = now − anchor` ถูกตัดเหลือ 32 bit จะวนเมื่อห่างจาก anchor เกิน 2^32 cycle ≈ 42.9 s เช่นตอนยังไม่ได้ `t_sync` (anchor = 0 จาก reset) หรือตอนโปรแกรมค้างนานโดยไม่มี `t_advance` ข้อแนะนำคือ **saturate ที่ 0xFFFF_FFFF** เพราะค่าที่ติดเพดานเห็นชัดว่าผิด ส่วนค่าที่วนแล้วดูเหมือนถูกต้อง ต้นทุนแค่ OR-reduce 32 bit บนของผลลบ 64 bit และอาจเพิ่ม bit `elapsed_sat` ใน `tmr.flags` ด้วย
  - **ขึ้นกับ B1:** ถ้า trap แทรกตอน stall ของ `t_advance` จะเกิด `now < anchor` ผลลบติดลบแล้ววนเป็นค่าใหญ่ ถ้า B1 ห้าม `t_advance` ระหว่าง armed กรณีนี้เกิดไม่ได้ ถ้าไม่ห้าม ต้องนิยาม `elapsed` เมื่อ `now < anchor` เพิ่ม (เช่น saturate ที่ 0)
  - **หมายเหตุสำหรับ Phase 3:** k-induction ไม่รู้ว่าตัวนับ 64 bit ไม่วนในทางปฏิบัติ อาจเริ่มพิสูจน์จาก state ที่ `now` ใกล้ 2^64 − 1 แล้วได้ counterexample ปลอมตอนวนกลับ 0 ต้องใส่ assumption ว่า `now` ไม่วน หรือ invariant ที่ผูก `now` กับจำนวน cycle นับจาก reset

### กลุ่ม B: ต้องตอบก่อน Phase 1 (assembler, WCET tool)

- [x] **B1. `t_advance` ขณะ `armed`** *(ตัดสิน 2026-09-26)*
  - assembler **reject แบบ static** ถ้ามี path ใดใน CFG ที่มาถึง `t_advance` ในขณะที่ยัง armed (dataflow ติดตาม bit เดียว ไม่ดูเงื่อนไข branch ถ้า reject โปรแกรมที่ถูก ให้ย้าย `t_clear` ไปจุดที่ path มาบรรจบ)
  - Phase 1: `t_arm` และ `t_clear` ต้องอยู่ใน function เดียวกัน (ไม่ติดตาม armed ข้าม call/return)
  - ขอบเขตโปรแกรม: **bounded + structured** รองรับ data-dependent branch และ loop ที่มี `@loop_bound` ไม่รองรับ recursion, indirect jump ที่ไม่ใช่ return และ loop ไม่มีขอบเขต (เขียนเป็น non-goal ใน README)
  - ผลต่อ A6: เมื่อห้าม `t_advance` ระหว่าง armed กรณี `now < anchor` เกิดไม่ได้ ไม่ต้องนิยามเพิ่ม
- [x] **B2. วงจรชีวิตของ `late`** *(ตัดสิน 2026-09-26, แก้ความหมาย)* `late` = **sync point ล่าสุดมาถึงช้า** เป็น non-sticky
  - ทั้ง `t_advance` และ `t_wait` เขียนทับ: `late ← (now > target)`
  - ไม่ใช้ OR เพราะจะทำให้ `late` กลายเป็น sticky ภายใน period
  - ข้อควรรู้: ใน control loop ที่ telemetry อ่าน flags หลัง `t_wait` จะเห็นแค่สถานะของ `t_wait` ถ้า `t_advance` มาช้าแต่ `t_wait` ตรงเวลา ค่า late ของ `t_advance` จะหายไป ข้อมูล period overrun ยังดูได้จาก `t_sense` ใน telemetry หรือให้อ่าน flags ทันทีหลัง `t_advance`
  - อนาคต (ไม่ทำใน Phase 1): เพิ่ม `late_seen` = เคยมี sync point สายตั้งแต่ clear/reset หรือไม่
- [x] **B3. WCET ข้าม timing move** *(ตัดสิน 2026-09-26)* แยก computation budget ออกจาก timing enforcement
  - `t_advance` และ `t_wait` เป็น sync point เวลาที่ stall ไม่นับเป็น WCET ของ computation segment
  - WCET tool แบ่งโปรแกรมเป็น segment ระหว่าง sync point แล้วตรวจว่าแต่ละ segment ไม่เกิน budget
  - segment หลัง sync point ที่มี target `T` เริ่มที่ `T + D` เช่น T = 1000, D = 1 → เริ่มที่ 1001
  - task-level WCET ใน Phase 1 วัดจาก wake ถึง `t_clear`
  - ผลพลอยได้: loop หลักของ task ที่ไม่สิ้นสุดถูกตัดที่ sync point จึงไม่ต้องมี `@loop_bound` กฎของ tool คือ cycle ใน CFG ทุกวงต้องมี `@loop_bound` หรือผ่าน sync point อย่างน้อยหนึ่งจุด
  - [ ] **ยังต้องยืนยัน: สูตร budget แบบ off-by-one** (ดู B3a)
- [x] **B3a. นิยาม budget** *(ตัดสิน 2026-09-26)*
  - Δ คือ **budget** ของ segment ส่วนค่าที่ส่งให้ timing move คือ `v = Δ + D` เงื่อนไขที่ตรวจจึงเป็น `W ≤ Δ`
  - W นับเฉพาะ computation ไม่รวม timing move ที่ปิด segment **และต้องรวม cycle ของ jump penalty P ด้วย** เพราะเป็นเวลาที่ computation ใช้จริง (ถ้านับแค่จำนวน move ผลตรวจจะผิดทันทีที่ segment มี jump ที่ taken)
  - ตรวจความถูก: wake = anchor + D, `t_wait` รันที่ anchor + D + W, target = anchor + Δ + D → ไม่ late เมื่อ W ≤ Δ
  - `v` ของ `t_wait` นับจาก anchor ไม่ใช่จาก sync point ก่อนหน้า ถ้ามีหลาย `t_wait` ใน period เดียว: `v_k = v_{k−1} + Δ_k + D` (โดย v_0 = 0 ที่ `t_advance`) และ segment สุดท้ายก่อน `t_advance` รอบถัดไปมี budget `period − v_last − D`
  - deadline: ตาม A4 move ใน cycle `deadline` ถูก squash ดังนั้น `t_clear` ต้องรันที่ cycle ≤ deadline − 1
- [x] **B4. Infeasible path** *(ตัดสิน 2026-09-26 ตามข้อแนะนำ)*
  - WCET tool ส่ง **witness path** (ลำดับ basic block ของ longest path) ออกมาคู่กับตัวเลข
  - หา input ที่พาไป witness path บน ISS: โปรแกรมเล็กใช้ exhaustive search, control law ให้คนตั้ง input เอง
  - หาเจอ = path feasible → วัดบนบอร์ดได้ tightness 0 cycle โดยมี input นั้นเป็นหลักฐาน (ตรงกับ Done criteria ของ Phase 4)
  - หาไม่เจอ = infeasible path → ปรับโครงโปรแกรม (เช่นซ้อน branch ที่เงื่อนไขผูกกัน) หรือรายงานตามจริงว่าไม่ tight
  - **ไม่ทำ path annotation ใน Phase 1** เพราะ annotation ที่ผิดทำให้ WCET ต่ำกว่าจริง (unsafe) ซึ่งแย่กว่าสูงเกิน เพิ่มเมื่อเจอโปรแกรมที่ต้องใช้จริง
  - จุดเสี่ยงสูงสุด: control law ที่มี saturation กับ anti-windup เป็น branch ที่เงื่อนไขผูกกัน

### ค่าคงที่ของ pipeline

- [x] **P = 2** *(ตัดสิน 2026-09-26 ตามข้อแนะนำ)*
  - fetch pipeline 3 stage: fetch (sync BRAM) → decode (register คั่น) → execute move
  - jump ที่ taken ทำให้ 2 move ที่ fetch เข้ามาแล้วถูกทิ้ง not-taken ไม่เสีย cycle
  - เหตุผล: ถ้าไม่มี register คั่น การ decode, ผ่าน src mux จากทุก FU และเขียนเข้า FU ปลายทางต้องเสร็จใน 10 ns เดียว ซึ่งเสี่ยงไม่ผ่าน timing ที่ 100 MHz ถ้าต้องกลับมาแก้ spec ทีหลังจะแพงกว่าการเสีย 1 cycle ต่อ jump
  - ผลตาม A4: **H = P = 2**
  - P เป็นสัญญาที่ RTL ใน Phase 2 ต้องทำให้ได้ ห้ามนำไปวัดจาก RTL แล้วค่อยปรับ spec ตาม

### เลื่อนไปทีหลัง

- TELEM snapshot counter ที่ cycle ไหน เก็บ 64 หรือ 32 bit ตัดสินที่ Phase 5 ได้ ไม่กระทบ port map ของ core
