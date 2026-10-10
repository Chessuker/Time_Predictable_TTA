# Design Decisions

สถานะ: **โน้ตสะสม** เขียนเป็นฉบับเต็มใน Phase 6

เอกสารนี้ตอบคำถามว่า "ทำไมถึงออกแบบแบบนี้" สำหรับข้อจำกัดที่คนอ่านหรือคนฟังน่าจะถาม ส่วน spec อยู่ใน [isa.md](isa.md) และ [timing_model.md](timing_model.md)

---

## ไม่มีคำสั่งหาร *(จดไว้ 2026-10-02)*

**ข้อจำกัด:** ISA ไม่มี divider ในฮาร์ดแวร์ (แผน §4 แถว MUL) control law ใช้การคูณ fixed-point กับ shift

**ทำไม**
1. **เวลาของ divider ทั่วไปไม่คงที่ หรือถ้าคงที่ก็แพง** divider ส่วนใหญ่ทำทีละ bit และหลาย CPU จบก่อนกำหนดถ้าตัวเลขเล็ก latency จึงขึ้นกับข้อมูล ถ้าบังคับให้คงที่จะช้า (ราว 32 cycle) ส่วนแบบ combinational เต็มตัวก็ใหญ่และ path ยาวเกินกว่าจะผ่าน 100 MHz
2. **หารด้วยค่าคงที่ไม่ต้องหารจริง** คำนวณส่วนกลับไว้บน host แล้วตอนรันใช้คูณกับ shift เช่น `e × 2.848` เขียนเป็น `(e × 186_659) >> 16` Python model ใน Phase 0 ยืนยันแล้วว่า controller ทั้งตัวใช้แค่คูณ บวก shift และ clamp
3. **Timing Unit ตัดการหารที่ปกติจำเป็นออก** ระบบทั่วไปต้องหารด้วยช่วงเวลาที่วัดได้ (`v = Δθ / Δt`) เพราะ Δt แกว่ง แต่ในงานนี้ period ตรงทุก cycle (jitter 0) จึงใช้ `Δenc` เป็นความเร็วได้เลย แล้วรวมค่าคงที่ไว้ใน gain ของ D

**งานจริงที่ต้องหารด้วยค่าที่ไม่คงที่** (ไม่อยู่ใน scope ของ demo)
- ชดเชยแรงดัน DC bus: duty = V_ref / V_dc
- วัดความเร็วแบบจับเวลาระหว่าง pulse ตอนหมุนช้า (M/T method): ω = K / Δt
- จำกัดขนาดเวกเตอร์แรงดันใน FOC, Kalman filter, normalize quaternion, adaptive filter (NLMS, AGC), inverse kinematics
- PID ที่ dt ไม่คงที่

**ถ้าต้องหาร ทำได้ทันทีด้วย ISA ปัจจุบันโดยเวลายังคงที่**

| วิธี | เวลา | หมายเหตุ |
|---|---|---|
| shift-subtract วนครบ 32 รอบทุกครั้ง ไม่ออกจาก loop ก่อน | คงที่ ประเมินคร่าว ๆ 300–400 cycle | `@loop_bound 32` และไม่มี branch ที่ขึ้นกับข้อมูล WCET จึง tight |
| Newton–Raphson: ค่าตั้งต้นจากตาราง แล้ววน `x ← x(2 − b·x)` จำนวนรอบตายตัว | คงที่ | ใช้แค่ MUL กับ ALU เข้ากับ ISA นี้ดี |
| หาส่วนกลับครั้งเดียวต่อ period แล้วใช้ซ้ำ | — | เช่นหา `1/V_dc` ครั้งเดียวแล้วคูณกับทุกเฟส |
| เขียนสมการใหม่ให้ไม่ต้องหาร | — | เช่น `a/b < c` เป็น `a < b·c` เมื่อ b > 0 |

ระบบจริงใช้แนวเดียวกัน เช่น DSP สาย motor control อย่าง TI C2000 รุ่นเก่าใช้คำสั่ง conditional subtract วนจำนวนรอบตายตัว ส่วน MCU ที่มี divider เวลาแกว่ง เช่น Cortex-M4 ต้องให้ WCET tool คิดเผื่อค่าสูงสุด ซึ่งปลอดภัยแต่ไม่ tight

**ทางขยายในอนาคต:** เพิ่ม DIV FU หรือ reciprocal unit ใน block `0xC_` ที่กันไว้ (isa.md §2) ได้โดยไม่กระทบ port เดิม เงื่อนไขเดียวคือ **latency ต้องคงที่** ซึ่งเป็นกฎของทุก FU ที่จะเพิ่มในอนาคต

**ข้อควรระวังของการใช้ shift แทนการหาร:** `SRA` ปัดลงหาลบอนันต์ ไม่ได้ปัดเข้าหาศูนย์แบบการหารในภาษา C (`-7 >> 1 = -4` แต่ `-7 / 2 = -3`) model จึงบวกครึ่งหนึ่งก่อน shift (`(x + 2^15) >> 16`) เพื่อปัดไปค่าที่ใกล้ที่สุด ISS และ RTL ต้องได้ผลเดียวกับ Python ทุก bit

---

## Timing closure ที่ 100 MHz และการคงไว้ที่ 100 MHz *(จดไว้ 2026-10-07)*

**ผลสุดท้าย:** Arty A7-100T (`xc7a100tcsg324-1`) ผ่าน timing ที่ 100 MHz ด้วย WNS = +0.185 ns, WHS = +0.034 ns path ที่ช้าที่สุดยาว 9.8 ns Fmax ของ core จึงอยู่ราว 102 MHz

**หลักที่ใช้ทุกรอบ:** แก้แค่**วิธีสร้างวงจร** ห้ามเปลี่ยนพฤติกรรม หลังแก้แต่ละรอบรัน lockstep ทั้ง 41 กรณี (`tests/test_lockstep.py`) และ RTL ต้องตรงกับ ISS ทุก record ทุก cycle ทั้ง 3 รอบไม่มีตัวเลขใน spec เปลี่ยนเลย

ทุก path ที่ยาวเริ่มที่ src mux เหมือนกัน เพราะ X stage ต้องเลือก source → ได้ค่าบน bus → ใช้ค่าในปลายทาง ให้เสร็จใน cycle เดียว (กฎ R2: เขียนที่ `c` อ่านได้ที่ `c + 1`) ทั้งสามรอบต่างกันแค่ว่าค่าบน bus ถูกส่งไปทำอะไรต่อ

| รอบ | WNS | path ที่ช้าที่สุด | ชั้นของ logic |
|---|---|---|---|
| 1 | −4.830 ns | `x_src` → src mux → `bad_alu_op` → `trap` → `exec` → `do_sync` → `anchor_n` → ลบ 64 bit → saturate → `u_tmr/elapsed` | 26 |
| 2 | −0.572 ns | `x_src` → src mux → ALU → `alu_out` และ `x_src` → src mux → jump → `flush` → enable ของ code SRAM | 13–15 |
| 3 | **+0.185 ns** | `x_src` → src mux → ALU → `alu_out` (เหลือเวลาพอ) | 9–10 |

### รอบ 1 → 2: สาเหตุ 2 ข้อ

**(ก) trap ที่ตัดสินจากค่าบน bus ไปหน่วง enable ของทุก FU**
- ต้นเหตุ: trap บางแบบรู้ได้หลังเห็นค่าบน bus แล้วเท่านั้น (เขียนค่าเกิน 9 ลง `alu.op`, address ของ load/store เกินขนาด, target ของ jump ที่ taken เกินขนาด) แต่ทุก FU ใช้ `exec` ตัวเดียวกันที่ต้องรอผลเช็คนี้ Vivado ไม่รู้ว่าการเช็คแต่ละแบบเกิดได้กับ dst ของตัวเองเท่านั้น จึงเอาไปต่อหน้า enable ของทุก FU (fanout 171)
- แก้: เพิ่ม `exec_base = exec_ok && !trap_dl && !x_illegal` ซึ่งไม่รอค่าบน bus ให้ FU ทั่วไปใช้ ส่วน `alu.op`, `mem.t_load`/`t_store` และ jump ยังรอผลเช็คของตัวเองเหมือนเดิม (`tta_core.sv`)

**(ข) Timing Unit คำนวณ 64 bit ต่อกันหลายชั้นใน cycle เดียว**
- ต้นเหตุ: `fu_tmr` เดิมเก็บเวลาแบบ absolute ตาม spec (`anchor`, `deadline`, target) ทำให้ใน cycle เดียวต้องบวก `anchor + v` แบบ 64 bit, ลบ `now + 1 − anchor` แบบ 64 bit แล้ว saturate ทั้งหมดต่อจาก src mux
- แก้: เก็บ**ระยะห่าง**แทน timestamp คือ `el = now − anchor` (64 bit), `wrem = target − now` ระหว่าง stall และ `rem = deadline − now` ขณะ armed แล้วแปลงเงื่อนไขของ spec ให้เป็นการเทียบกับ `el` ครั้งเดียว:
  - stall เมื่อ `n + 1 ≤ anchor + v` เทียบเท่ากับ `el < v`
  - late เมื่อ `n > anchor + v` เทียบเท่ากับ `el > v`
  - deadline trap ที่ `n + 1` เมื่อ `anchor + v ≤ n + 1` เทียบเท่ากับ `v ≤ el + 1`

  ทุกการตัดสินบน path ของค่าบน bus เหลือการเทียบ 32 bit ครั้งเดียว (`fu_tmr.sv`) การแปลงนี้ถูกต้องเพราะ `el ≥ 0` ทุกครั้งที่มี move execute (`el` ติดลบได้เฉพาะระหว่าง stall ของ `t_advance` ซึ่ง B1 กัน trap ไว้แล้ว) และใช้ 64 bit เต็มเพื่อให้เทียบเท่า spec ทุกกรณีภายใต้สมมติฐาน R7
- **ต้องพิสูจน์ใน Phase 3:** formal ต้องแสดงว่าแบบระยะห่างนี้เทียบเท่ากับ spec ที่เขียนเป็นเวลา absolute เช่น trap เกิดที่ `now == deadline` พอดี

### รอบ 2 → 3: สาเหตุ 2 ข้อ

**(ค) ถอดรหัส src ช้าเกินไป**
- ต้นเหตุ: X stage ต้องเอา port ID 8 bit มาถอดรหัสก่อนจึงรู้ว่าจะเลือก source ตัวไหน เสีย LUT ไป 2–3 ชั้นก่อนได้ค่าบน bus
- แก้: ถอดรหัสไว้ล่วงหน้าตั้งแต่ D stage เก็บเป็น one-hot select (`x_sel`) ใน register แล้วใน X เหลือแค่ AND-OR การแก้นี้ทำให้ทุก path ที่ผ่าน src mux เร็วขึ้นพร้อมกัน ไม่ใช่แค่ ALU

**(ง) enable ของ code SRAM รอผลของ jump**
- ต้นเหตุ: เดิมใช้ `re = advance && !flush` ซึ่งต้องรู้ก่อนว่า jump taken หรือไม่ (ต้องดูค่าบน bus)
- แก้: ใช้ `re = advance` อ่านทุกครั้งที่ pipeline เดิน ถ้าเกิด jump word ที่อ่านมาจะถูกทิ้งด้วย `ir_valid` อยู่แล้ว การอ่านเกินมา 1 word จึงไม่มีผลอะไร

### ข้อควรระวังต่อจากนี้
- **เงื่อนไขบังคับ (จดไว้ 2026-10-08 ตามรีวิว PR #3):** ทุก commit ที่แตะ RTL ใต้ `Time_Predictable_TTA.srcs/sources_1/` ต้องได้ **WNS ≥ 0 และ WHS ≥ 0 ที่ 100 MHz** บน `xc7a100tcsg324-1` ตรวจด้วย `tta_timing` ใน Vivado ต้องขึ้น `met` ไม่ผ่านห้าม merge
- **เหลือเวลาแค่ 0.185 ns** ทุกครั้งที่แก้ RTL ต้องรัน `tta_paths` ใน Vivado (`fpga/vivado/tta.tcl`)
- **อย่าแยก `tta_core` เป็นหลาย module เพื่อความสวยงาม** การจัดโครงสร้างใหม่เปลี่ยนผล synthesis ได้ทั้งที่พฤติกรรมเท่าเดิม และ margin ตอนนี้แคบ (ความเห็นจากรีวิว PR #3)
- **ALU ยังเป็น path ที่ช้าที่สุด** ถ้าต้องการเวลาเพิ่ม แยกตัว compare ของ `MIN`/`MAX` ออกจาก adder ได้
- **บทเรียน:** ถ้า signal ควบคุมตัวเดียวคุมหลาย FU ทั้งที่แต่ละ FU ต้องการเงื่อนไขไม่เท่ากัน ให้แยก enable ตามปลายทาง และ logic ที่ไม่ขึ้นกับค่าบน bus ให้ย้ายไปทำล่วงหน้าใน D stage

### ทำไมไม่เพิ่มเป็น 450 MHz

ตัวเลข "เกิน 450 MHz" ของ Arty คือเพดานของ primitive เดี่ยว ๆ ที่มี register ครอบ ตาม DS181 ค่าของ speed grade −1 เป็นดังนี้

| ส่วนของชิป | Fmax (−1) |
|---|---|
| BUFG | 464 MHz |
| DSP48E1 ที่ใส่ register ครบ | 464 MHz |
| DSP48E1 คูณแบบไม่มี MREG | 257 MHz |
| Block RAM / FIFO | 388 MHz |
| MMCM (ตัวสร้าง clock) | 800 MHz |

- ที่ 450 MHz หนึ่ง cycle มี 2.22 ns ทำ logic ได้แค่ 2–3 ชั้น แต่ X stage ตอนนี้มี 9–10 ชั้น ต้องหั่นเป็น 4–5 stage
- การหั่นแบบนั้นขัดกับกฎ R2 ต้องเลือกระหว่างเพิ่ม bypass (ซึ่งกลายเป็น mux ยาวบน critical path) หรือเปลี่ยน ISA ให้ port ส่วนใหญ่มี latency ≥ 2 ทั้งสองทางเปลี่ยน spec ที่ freeze แล้ว และต้องเพิ่ม P, D, H, R และ latency ของ FU ทุกตัว
- ผลกระทบไล่ตามกันไป Phase 0 แก้ spec และตัวอย่าง, Phase 1 ต้องแก้ test ที่ล็อกตัวเลข cycle ไว้ (ส่วน ISS และ assembler ปรับตาม `spec.py` เอง), Phase 2 ต้องออกแบบ pipeline ใหม่ทั้งหมด
- **ตัดสินใจ (2026-10-07): คงไว้ที่ 100 MHz** เป้าหมายของโปรเจกต์คือ predictability ไม่ใช่ throughput (README: non-goals) และ jitter 0 กับ WCET ที่ tight ได้ครบที่ 100 MHz อยู่แล้ว
- **future work:** ถ้าจะทดลองเร่ง clock ให้เล็ง 125–150 MHz ก่อน น่าจะต้องหั่นแค่ X stage ชั้นเดียวพร้อม bypass แต่เป็นการประเมิน ยังไม่ได้วัด ทำหลัง decision gate ของ Phase 4

---

## Reset: core ใช้ synchronous reset ส่วน power-up เป็นเรื่องของบอร์ด *(จดไว้ 2026-10-08)*

**ข้อตกลง:** register ทุกตัวใน `tta_core` และ FU ได้ค่าเริ่มต้นจาก `rst` แบบ synchronous active-high เท่านั้น ไม่พึ่งค่าตอนเปิดเครื่อง จึงใช้ได้ทั้ง FPGA ทุกยี่ห้อและ ASIC

ข้อยกเว้นเดียวคือ**เนื้อหาของ code และ data SRAM** ซึ่ง `tta_sram_1r1w.sv` โหลดจาก image ด้วย `$readmemh` ใน `initial` (FPGA ทำให้ตอน configuration) `rst` ไม่ล้างหน่วยความจำ กด RESET แล้วโปรแกรมจึงเริ่มใหม่บน data ที่ค้างอยู่จากรอบก่อน ไม่ใช่ image เดิม ถ้าทำเป็น ASIC ต้องเปลี่ยนเป็น ROM หรือมีตัวโหลดโปรแกรมแยก

**ส่วนที่เป็นของ Arty เท่านั้น** อยู่ใน `arty_tta_top.sv` ไม่ได้อยู่ใน core:
- register ตัวสร้าง reset (`rst_sync`, `por`, `rst`) และ synchronizer ของสวิตช์ใช้ค่าเริ่มต้นแบบ `logic x = ...` ซึ่ง FPGA ของ Xilinx โหลดให้ตอน configuration (GSR) เพราะก่อนหน้านั้นไม่มี reset ตัวไหนมาจัดการให้
- หลัง configuration จะค้าง `rst` ไว้ 16 cycle แล้วจึงปล่อย และปุ่ม RESET (`ck_rst`, active-low) ผ่าน synchronizer 2 ชั้นก่อนเข้า core

ถ้าย้ายไปบอร์ดอื่นหรือทำ ASIC ให้เขียน top ใหม่ที่สร้าง `rst` ตามเทคโนโลยีนั้น เช่น ใช้วงจร power-on reset ของชิป ส่วน `tta_core` ไม่ต้องแก้

---

## Formal verification ของ core *(จดไว้ 2026-10-08)*

**ผล:** k-induction ผ่าน (`formal/tta_core.sby` task `prove` ใช้เวลาราว 3 นาที) และ cover ผ่านครบทั้ง 6 ข้อ เครื่องมือคือ SymbiYosys กับ yosys-slang ใน oss-cad-suite (WSL Ubuntu-24.04) รันผ่าน `tests/test_formal.py`

**วิธี (ตัดสินร่วมกับผู้ใช้):**
- **instruction stream อิสระ** ตอน formal ใช้ `formal/tta_sram_1r1w_any.sv` แทน SRAM จริงทั้ง code และ data ทุกครั้งที่อ่านจะได้ word อะไรก็ได้ property ที่ผ่านจึงจริงกับทุกโปรแกรม รวมโปรแกรมที่มี move illegal ส่วน RTL ใน `.srcs` ไม่ได้แก้เลย
- **reference model แบบ absolute** อยู่ใน harness เขียนตาม R5/R6 ตรงตัว (`now`, `anchor`, `deadline`, target 64 bit และ "move ถัดไปถึงกำหนดที่ cycle ไหน PC อะไร") model ดูแค่สิ่งที่ core รายงานออกมา (`rv_*`, `tr_*`, `ht_*`) แล้ว assert ว่าทุก cycle ตรงกัน การพิสูจน์นี้จึงเป็นทั้งการตรวจ property ใน plan และการพิสูจน์ว่า `fu_tmr` แบบระยะห่างเทียบเท่ากับ spec (ค้างไว้จากหัวข้อ Timing closure ข้อ ข)
- ไฟล์ formal อยู่ที่ `formal/` ไม่ใช่ `.srcs` เพราะ Vivado ใช้ไม่ได้ และจะมี module `tta_sram_1r1w` ซ้ำกันสองตัว

**Assumption มีแค่กฎที่ assembler บังคับอยู่แล้ว**
- S3: `t_advance` ไม่ execute ขณะ armed
- R7: `now` ไม่วนรอบ (จำกัดไว้ที่ 2^62 ราว 1,461 ปีที่ 100 MHz)

cover ทั้ง 6 ข้อยืนยันว่า assumption ไม่ได้ตัดพฤติกรรมที่ assert พูดถึงทิ้ง

**Invariant ที่ k-induction ต้องการ** (ได้มาจาก counterexample ของ induction ทีละข้อ):
- `el = now − anchor` เสมอ (mod 2^64)
- ระหว่าง stall `wrem = target − now` ขณะ armed และยังไม่ถึง deadline `rem = deadline − now`
- สถานะของ F, D, X สัมพันธ์กับ cycle ที่ move ถัดไปถึงกำหนด (ห่าง 2, 1 หรือ 0 cycle)
- `anchor > now` ได้เฉพาะระหว่าง stall ของ `t_advance` (ซึ่ง `anchor == target`) และตอนนั้นต้องไม่ armed เพราะ S3 ถูกเช็คแค่ตอน `t_advance` execute ส่วน induction เริ่มจากกลาง stall ได้

**Mutation test:** ใส่บั๊กลงในสำเนาของ `fu_tmr.sv` สองแบบ ทั้งสองแบบ proof ล้มภายในไม่กี่ step
- deadline trap เร็วไป 1 cycle (`rem == 2`) ล้มที่ A3
- stall นานไป 1 cycle (`el <= v`) ล้มที่ A2

**ข้อจำกัด:** model ไม่ได้ตรวจค่าที่ FU คำนวณออกมา (ALU, MUL, memory) และไม่ได้ตรวจ cause ของ legality trap ส่วนนี้ยังพึ่ง lockstep กับ ISS ตามเดิม formal ครอบเรื่องเวลา, PC, trap และ halt

**ตรวจซ้ำเรื่องค่าอิสระ (2026-10-10):** ระหว่างเพิ่ม `io.din` พบว่า yosys-slang ไม่สนใจ `(* anyseq *)` บนตัวแปรภายใน
- **กลไก:** ตัวแปรที่ไม่มีตัวขับถูก elaborate เป็นค่าคงที่ `32'x` แยกตามจุดที่ใช้ แล้ว `setundef -undriven -anyseq` ในขั้น prep ของ sby เปลี่ยนแต่ละจุดเป็น `$anyseq` ของตัวเอง ถ้าสัญญาณถูกอ่านสองที่ สองที่จะได้ค่าที่ไม่เกี่ยวกัน ตอนแรก `io_din` ที่ถูกอ่านทั้งใน core และใน assertion จึงได้ counterexample ใน basecase
- **คำอธิบายเดิมผิดเรื่องกลไก:** ข้อความข้างบนที่บอกว่า stub ใช้ `(* anyseq *)` ทำให้ทุกครั้งที่อ่านได้ word อะไรก็ได้ ผลลัพธ์ถูก แต่ที่ทำให้เป็นแบบนั้นจริงคือ setundef ของ sby ไม่ใช่ attribute
- **ผลต่อ Phase 3 ตรวจด้วยสองวิธีบน harness ของ deb6eef (ก่อนเพิ่ม `io.din`):**
  - ดูจาก model ที่ elaborate แล้ว (`design_prep.il`): มี `$anyseq` 32 bit สามตัว ต่อเข้าที่จุดอ่านของ imem, dmem และ `io_encoder` ใน source mux ของ core ตรงตัว และไม่มีสัญญาณใดที่ไม่มีตัวขับแล้วถูกอ่านสองที่
  - ดูจากพฤติกรรม: เพิ่ม cover C8–C10 (ด้านล่าง) เข้าไปใน harness นั้นโดยไม่แก้อย่างอื่น cover ถึงครบ 9/9 แปลว่า word ของ code, ค่าที่ load และ `io.encoder` เปลี่ยนได้ระหว่างการอ่าน
  - สรุป: ไม่พบช่องโหว่ใน A1–A6 ของ Phase 3 สิ่งที่ไม่ได้ทำซ้ำคือ prove และ mutation บน harness เดิม ผลของทั้งสองอย่างยังเป็นตามที่บันทึกไว้ข้างบน ส่วนรอบนี้รันบน harness ใหม่
- **แก้ให้ไม่ต้องพึ่งเงื่อนไขนี้:** `io_encoder` และ `io_din` เป็น input ของ `tta_formal` (smtbmc ให้ค่าอิสระค่าเดียวต่อ step กับทุกจุดที่อ่าน) ส่วน `any_word` ใน stub ของ memory ยังพึ่ง setundef ในขั้น prep ของ sby แต่เขียนกำกับไว้ว่าต้องอ่านจุดเดียว (ไฟล์ stub เปลี่ยนแค่ comment) ถ้าเครื่องมือรุ่นใหม่ทำให้ stub ไม่ได้ค่าใหม่ทุกครั้งที่อ่านอีกต่อไป เช่นได้ค่าคงที่ตลอด run หรือได้ 0 C8 กับ C9 จะไม่ถึงและ `test_formal` จะล้ม
- **cover ใหม่ยืนยันว่าค่าเปลี่ยนได้จริงทุกครั้งที่อ่าน:** C8 word ของ code สองตัวติดกันต่างกัน, C9 load สองครั้งได้ค่าไม่ใช่ 0 ที่ต่างกัน, C10 อ่าน `io.encoder` สองครั้งได้ค่าไม่ใช่ 0 ที่ต่างกัน ถ้าค่าใดกลายเป็นค่าคงที่ตลอด run หรือเป็น 0 cover เหล่านี้จะไม่ถึง
- **ผลหลังแก้:** prove ผ่าน (k-induction, depth 12), cover ถึงครบ 10 ข้อ, mutation 4 แบบล้มทุกแบบ: deadline trap เร็วไป 1 cycle ล้มที่ A3, stall นานไป 1 cycle ล้มที่ A2, ส่ง `io_din` เข้าช่องของ `io.encoder` และกลับกัน ล้มที่ A4 ทั้งสองแบบ

---

## Plant ของ HIL: แบบหลาย cycle แทนแบบ cycle เดียว *(จดไว้ 2026-10-10)*

plant มอเตอร์ DC ของ Phase 5 อยู่ใน top ของบอร์ด ไม่อยู่ใน core ต่อกับ `io.encoder` และ `io.pwm_cmd` ใช้ coefficient จาก `plant_pkg.sv` ซึ่ง generate จาก `host/plant_model` แต่ละ step คำนวณ:
- `θ' = θ + round((C01·ω + CU0·u + CT0·τ) / 2^24)`
- `ω' = round((C11·ω + CU1·u + CT1·τ) / 2^24)`

ผลคูณกว้างถึง 46 bit จึงลองสองแบบ โดยทั้งสองแบบต้องตรงกับ `FixedPlant` ทุก bit (`tests/test_plant.py`)

| แบบ | ไฟล์ | latency | WNS ที่ 100 MHz | LUT | FF | DSP |
|---|---|---|---|---|---|---|
| cycle เดียว (baseline) | `dc_motor_plant.sv` | 1 | **−2.203 ns** (16 level) | 508 | 195 | 18 |
| หลาย cycle | `dc_motor_plant_mc.sv` | 10 | **+3.397 ns** (12 level) | 429 | 377 | 4 |

ตัวเลขมาจาก `tta_plant_compare` ใน `fpga/vivado/tta.tcl` ซึ่ง synthesize แบบ out-of-context แล้ว place & route ที่ 100 MHz

- **แบบ cycle เดียวไม่ผ่าน:** worst path ทำทั้งหมดใน cycle เดียว คือคูณผ่าน DSP 3 ตัวที่ต่อเป็นสาย บวก 64 bit ปัดเศษ แล้วบวกเข้ากับ θ 48 bit ช้ากว่า 10 ns ไป 2.2 ns (ราว 82 MHz) และใช้ DSP 18 ตัว เพราะผลคูณทั้ง 6 ตัวกว้างเกิน DSP เดียว
- **multicycle constraint ช่วยแบบ cycle เดียวตรงๆ ไม่ได้:** `u` มาจาก `pwm_cmd` ที่ core เขียนได้ทุก cycle ต้อง latch input ก่อน ซึ่งก็กลายเป็นแบบหลาย cycle ที่ต้องพึ่ง timing exception
- **แบบหลาย cycle:** ใช้ตัวคูณ 28×40 ตัวเดียวที่มี register คั่น 3 ชั้น ป้อนผลคูณทีละ cycle Vivado ดูด register เข้าไปใน DSP (AREG, BREG, PREG) worst path เหลือแค่บวก 64 bit ข้อแลกเปลี่ยนคือ FF เกือบ 2 เท่า และ encoder เห็นผลช้าลง 9 cycle จาก step 10,000 cycle model ใน Python รับ latency เป็นพารามิเตอร์ จึงยังเทียบได้ตรงทุก cycle

**ตัดสินใจ (2026-10-10):** บอร์ดใช้แบบหลาย cycle ส่วนแบบ cycle เดียวเก็บไว้เป็น baseline ใน repo และเทสต์ยังตรวจว่าตรงกับ Python ทุก bit

**ผลบนบอร์ด (2026-10-11):** design ทั้งตัวที่มี plant ผ่าน timing ด้วย WNS +0.406 ns และ WHS +0.036 ns critical path ไม่ได้อยู่ใน plant แต่อยู่ที่ data memory → ALU ของ core

**Setpoint และโหลด (ตัดสินใจ 2026-10-10):**
- **ตอนนี้:** setpoint มาจากตารางใน `.data` ของโปรแกรม ส่วนแรงบิดโหลดมาจากช่วงเวลาตายตัวที่ `hil_env.sv` นับจาก reset ทั้งคู่กำหนดไว้ใน `host/hil/env.py` การรันทุกครั้งจึงได้ผลเดิมทุก cycle และเทียบกับ model ได้ตรงตัว
- **ส่วนเสริมภายหลัง:** สวิตช์บนบอร์ด ต้องเพิ่ม port `io.sw` ใน ISA ที่ freeze แล้ว

**Deadline trap บนบอร์ด:**
- **วิธีทดสอบ:** ใช้โปรแกรมแยก `control_trap.tta` ให้ period ที่ 50 วนเกิน deadline โดยตั้งใจ
- **ผล:** trap เกิดที่ anchor + 2000 พอดี handler เริ่มที่ anchor + 2003 สั่ง `pwm_cmd = 0` ส่ง record แล้ว halt ตรงกับ ISS ทุกค่า
- **WCET:** tool รายงานโปรแกรมนี้ว่า OVER ซึ่งเป็นสิ่งที่ตั้งใจ ส่วน `control.tta` ได้ W = 90 จาก budget 999

---

## รายการที่ต้องเขียนเพิ่มใน Phase 6

- ประกาศว่า ISA เป็น constant-time โดยตั้งใจ อ้าง Liu §2.3 (มาจาก reading_notes_tier1 ข้อ 10)
- Timing Unit แบบ anchor เทียบกับ timestamp ในแบบของ PTARM และ FlexPRET
- spec ที่ไม่ผูกกับ vendor (MUL 32×32 ให้ 32 bit ล่าง, L = 2)
