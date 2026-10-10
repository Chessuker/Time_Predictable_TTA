# External input interface (`io.din`)

เอกสารนี้กำหนดเส้นทางของ input ภายนอกตั้งแต่ pin บนบอร์ดจนถึง actuator และบอกว่าอะไรรับประกันได้ อะไรรับประกันไม่ได้ ใช้กับ switch mode ของ HIL และเป็นแบบให้ input ของ controller ในอนาคต

แบ่งเป็นสองชั้นที่ไม่ปนกัน
- **ISA** ([isa.md](isa.md) §3): `io.din` ที่ `0x92` อ่านที่ cycle `r` ได้ค่าบน input ของ core ณ ต้น cycle `r` core ไม่ sample หรือกรองเอง
- **Board interface** (`board_din.sv`, `din_debounce.sv`): ทำให้ pin ดิบปลอดภัยพอจะส่งให้ core ทุกอย่างที่เป็นเรื่องของบอร์ดอยู่ที่นี่ ไม่อยู่ใน spec

cycle ในเอกสารนี้นับแบบเดียวกับ `now` ของ core (cycle 0 คือ cycle แรกหลังปล่อย reset) และ `x(t)` คือค่าระหว่าง cycle `t`

## 1. Board interface บน Arty A7

| กลุ่ม | pin | ไปที่ | การเปลี่ยน |
|---|---|---|---|
| setpoint | `sw[1:0]` | `din[1:0]` | เฉพาะตอน commit |
| load | `sw[2]` | `din[2]` และโหลดของ `hil_env` | สด |
| commit | `btn0` (BTN0) | ขอบขาขึ้นหลัง debounce สั่ง commit | |
| สงวนไว้ | `sw[3]` | ไม่ใช้ | |

`din[31:3] = 0` และโปรแกรมต้อง mask ใช้เฉพาะ bit ที่นิยามแล้ว

**Debounce** ทำทีละกลุ่ม (`din_debounce.sv`) กลุ่มละหนึ่งชุด ทุก bit ของกลุ่มเปลี่ยนพร้อมกันใน cycle เดียว
- synchronizer 2 FF ต่อ bit: `s2` คือ pin ที่ช้าไป 2 cycle
- `s2(t) ≠ cand(t)`: `cand ← s2`, `cnt ← 0`
- ไม่อย่างนั้นถ้า `cnt = N_DB − 1`: `out ← cand`
- ไม่อย่างนั้น `cnt ← cnt + 1`

**Commit:** `din[1:0](t+1) = sw_db[1:0](t)` เมื่อ `btn_db(t) = 1` และ `btn_db(t−1) = 0` กดค้างไม่ commit ซ้ำ

`N_DB = 500 000` (5 ms) เป็น parameter ของ `arty_tta_top` simulation ใช้ค่าเล็กกว่านี้ได้

โมเดลที่ตรงทุก cycle อยู่ที่ `host/hil/board_din.py` และ `tests/test_board_din.py` เทียบ RTL กับโมเดลทีละ cycle

## 2. เวลา

| ช่วง | cycle |
|---|---|
| pin `sw[2]` นิ่งที่ `p` → `din[2]` ที่ core | `p + N_DB + 3` |
| pin `btn0` นิ่งที่ `p` → `din[1:0]` ที่ core | `p + N_DB + 4` (ค่าที่ copy คือ `sw_db` ของ cycle ก่อนหน้า) |
| ค่าใหม่ถึง core ที่ `c` → move ที่เขียน `io.pwm_cmd` | `[ACT + 1 − s, ACT + 1 − s + PERIOD − 1]` |
| move เขียน `io.pwm_cmd` ที่ `w` → plant เห็นค่า | `w + 1` |

- ขอบ async ที่ pin อาจ resolve ช้าไป 1 cycle (metastability) ช่วงจาก pin จึงเป็น +3 หรือ +4 และ +4 หรือ +5 ตามลำดับ simulation ไม่แสดงกรณีนี้
- `s` คือ offset ที่โปรแกรมอ่าน `io.din` หลัง anchor ช่วงที่สามใช้ได้เมื่อ s คงที่ทุก period ไม่มี period ที่สาย (flags = 0) และยังไม่ halt
- ถ้า `c = anchor_k + s` ค่าใหม่ทัน period k ถ้า `c = anchor_k + s + 1` ต้องรอ period k+1
- `control_sw` ใช้ `s = 2`, `ACT = 1000`, `PERIOD = 100 000` จึงได้ 999 ถึง 100 998 cycle และจาก pin ถึงเขียน pwm ราว 5.01 ถึง 6.01 ms

## 3. รับประกันอะไร

เงื่อนไขพื้นฐานของทุกข้อด้านล่าง: โปรแกรมอ่าน `io.din` ที่ `anchor_k + s` ทุก period และ sample ห่างกัน PERIOD พอดี ฝั่ง host พิสูจน์เงื่อนไขนี้ได้จาก record ที่ k ต่อเนื่อง, elapsed หลังอ่าน input คงที่ และ flags = 0

| ระดับ | นิยาม | รับประกัน | ไม่รับประกัน |
|---|---|---|---|
| accepted | ค่าปรากฏที่ input ของ core | pin ที่นิ่งอย่างน้อย `N_DB + 1` cycle (นับที่ `s2`) ถูกรับ สั้นกว่านั้นถูกทิ้ง ค่าที่รับแล้วค้างอย่างน้อย `N_DB + 1` cycle (`din[1:0]` อย่างน้อย `2(N_DB + 1)` เพราะต้องปล่อยปุ่มก่อนกดใหม่) | ไม่มีบันทึกการเด้งของ pin |
| sampled | โปรแกรมอ่านที่ `anchor_k + s` และส่งค่าใน telemetry | ทุกค่าที่ accepted ของแต่ละกลุ่มอยู่ในอย่างน้อยหนึ่ง record เมื่อ `N_DB + 1 ≥ PERIOD` (`din[2]`) และ `2(N_DB + 1) ≥ PERIOD` (`din[1:0]`) | ค่าหลัง reset ของ `din[1:0]` ซึ่งค้างแค่ถึง commit แรก, ค่าผสมของทั้ง word ถ้าสองกลุ่มเปลี่ยนห่างกันน้อยกว่า PERIOD, ค่าก่อน sample แรก, หลัง record สุดท้าย และหลัง halt |
| command actuated | write ที่ `anchor_k + ACT + 1` คำนวณจากค่าที่ sample ใน period เดียวกัน | latency ตามตารางในข้อ 2 | ไม่รับประกันว่า pwm จะเปลี่ยนค่าที่สังเกตได้ เช่นตอน saturate |
| physical load observed | `hil_env` ใช้ `din[2]` ที่ accepted ตรงตั้งแต่ cycle นั้น plant ใส่แรงบิดตั้งแต่ step แรกที่ไม่ก่อน cycle นั้น encoder เห็นผลหลัง LATENCY ของ plant | telemetry บอกสถานะโหลด ณ sample ของแต่ละ period | cycle ที่โหลดเปลี่ยนจริงรู้แค่ว่าอยู่ใน period ไหน จึง replay ผลบนบอร์ดแบบทุก bit ไม่ได้เมื่อโหลดสลับ ใน simulation replay ได้เพราะรู้ trace ของ input |

ค่าที่ใช้บนบอร์ด (`N_DB = 500 000`, `PERIOD = 100 000`) เผื่อไว้ 5 เท่า (`din[2]`) และ 10 เท่า (`din[1:0]`)

**Commit กับการบิดสวิตช์:** สองทางมี latency เท่ากัน commit จึงได้ตำแหน่งล่าสุดของสวิตช์เมื่อสวิตช์นิ่งไม่ช้ากว่าปุ่ม (cycle เดียวกันก็ได้) ถ้านับกรณีขอบ async ต้องให้สวิตช์นิ่งก่อนอย่างน้อย 1 cycle ถ้ากดปุ่มก่อนแล้วค่อยบิดสวิตช์ จะได้ค่าที่ debounce ไว้ก่อนหน้า ไม่มีทางได้ค่าที่ยังไม่นิ่ง ทั้งสามกรณีอยู่ใน `test_commit_takes_the_new_value_only_if_the_switch_settles_no_later` และใน test ที่เทียบกับ RTL

## 4. การตรวจ

- `tests/test_board_din.py`:
  - โมเดลแบบ register กับแบบ event ตรงกัน
  - ขอบ `N_DB` และ `N_DB + 1`
  - ระยะค้างค่าต่ำสุดและการกด commit
  - ขอบ PERIOD ของการ sample
  - RTL เท่ากับโมเดลทุก cycle (N_DB = 1, 4, 9 รวมสคริปต์ที่มีการเด้ง)
- `tests/test_iss.py`, `tests/test_lockstep.py`:
  - ขอบ `anchor + 2` และ `+ 3`
  - ค่าที่ค้าง PERIOD พอดีถูกใช้ period เดียว ส่วน PERIOD − 1 หลุดได้
  - ISS และ RTL ตรงกัน
- `tests/test_rtl_units.py::test_switch_pins_to_pwm_cmd_end_to_end`: บอร์ดทั้งตัวใน simulation ตั้งแต่ pin ถึง `pwm_cmd` เทียบทีละชั้น:
  - `din` ที่ core เทียบกับโมเดล
  - trace และ telemetry ทาง UART เทียบกับ ISS
  - cycle ที่ `pwm_cmd` เปลี่ยนเทียบกับสูตรในข้อ 2

## 5. ต่อยอด (ยังไม่ทำ)

- **Input capture:** board interface บันทึก `(now, ค่าใหม่)` ทุกครั้งที่กลุ่มใดเปลี่ยน แล้วส่งออกทางช่องของตัวเอง
- **Deterministic replay:** แปลง capture เป็น `prog.stim` ([toolchain_formats.md](toolchain_formats.md) §6.4) แล้วรัน ISS, RTL และ plant model ซ้ำ ทำให้ switch mode เทียบกับ model ได้ทุก bit รวมช่วงที่โหลดสลับ
- **Fault handling ระดับ application:** เช่นรหัส din ที่ไม่ถูกต้องให้ถือค่าที่ valid ล่าสุด, ตรวจ input ที่สั่นถี่ผิดปกติ, ตรวจ input ที่ค้างนานผิดปกติ
