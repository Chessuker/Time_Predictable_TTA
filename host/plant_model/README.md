# Plant model (Phase 0)

Python model ของ closed loop ระหว่าง DC motor กับ PID controller ทั้งแบบ float และ fixed-point ใช้เลือก Q-format ก่อนเขียน RTL และเป็น reference ที่ step response บนบอร์ดต้องตรงใน Phase 5

```bash
py host/plant_model/run.py
```

ผลลัพธ์พิมพ์ออก console ส่วนกราฟอยู่ที่ `host/plant_model/out/` (gitignored)

## Model

**Plant:** ใช้ค่า parameter จากตัวอย่าง "DC Motor Position" ของ CTMS (J = 3.2284e-6 kg·m², b = 3.5077e-6 N·m·s, K = 0.0274, R = 4 Ω, L = 2.75e-6 H)
- L/R = 0.69 µs สั้นกว่า plant step (100 µs) มาก จึงตัด state ของกระแสออก เหลือ 2 state คือ θ กับ ω
- discretize แบบ ZOH ที่ 100 µs (10,000 cycle) หน่วยของ state คือ θ เป็น encoder count และ ω เป็น count ต่อ plant step
- encoder 4096 count/rev (1024 line ×4), `pwm_cmd` เป็น signed 12 bit (±2047 = ±12 V)
- มี load torque เป็น input สำหรับทดสอบ integrator

**Timeline:** ตรงกับ [timing_model.md](../../docs/timing_model.md) §5 ตัวอย่าง 3 คือ period 1 ms, sense ที่ anchor + 1, actuate ที่ anchor + 1001 ผล sense เห็น plant step ที่ cycle < เวลา sense และ plant step ที่ cycle > เวลา actuate จึงใช้ค่า `pwm_cmd` ใหม่ ทดสอบ 4 phase ระหว่าง plant step กับ anchor แล้ว ผลไม่ต่างกัน

**Controller:** PID วางขั้วแบบ continuous ให้ closed loop เป็น (s² + 2ζωₙs + ωₙ²)(s + p₃) โดย ωₙ = 2π·15 rad/s, ζ = 0.8, p₃ = ωₙ
- P และ I ทำงานบน error ส่วน D ทำงานบน measurement เพื่อไม่ให้ step ทำให้เกิด derivative kick
- anti-windup ใช้ conditional integration (หยุด integrate ถ้า saturate และ error ดันไปทางเดียวกัน) และ clamp integrator ที่ ±2047 ทั้งสองอย่างเป็น branch ที่เงื่อนไขผูกกันตามที่ B4 เตือนไว้
- ลอง setpoint weighting บน P แล้ว แต่ไม่ใช้ เพราะ integrator ที่ถูก clamp จ่ายส่วนที่ขาด KP(1 − b)·ref ไม่พอเมื่อ ref ใหญ่ loop จึงไม่ settle

## Fixed-point formats

| ส่วน | Format | หมายเหตุ |
|---|---|---|
| gain ของ controller | Q.16 (`KPQ` = 186,659, `KDQ` = 1,501,851, `KIQ` = 6,766) | quantization error ≤ 0.003% |
| error ก่อนคูณ | clamp ที่ ±4096 count | ดูหัวข้อถัดไป |
| integrator `acc` | Q.16, clamp ที่ ±2047·2^16 | |
| output | `(p − d + acc + 2^15) >> 16` แล้ว saturate ±2047 | round half up |
| state ของ plant | Q.16 | θ ต้องใช้ 33 bit เมื่อหมุนถึง 10 รอบ |
| coefficient ของ plant | Q.24 | accumulator ต้องกว้าง 46 bit |

**ทำไม clamp error ที่ ±4096 ไม่เปลี่ยนผล:** เมื่อ |P| มากกว่า |I|max + |D|max = 2047 + 22.9 × 280 ≈ 8,464 LSB ผลรวมจะ saturate ไปทางเดียวกับ P อยู่แล้ว ค่า 280 count/ms คือความเร็วสูงสุดของมอเตอร์ที่ 12 V ส่วน P ที่ error 4096 เท่ากับ 2.85 × 4096 ≈ 11,666 LSB ซึ่งเกินค่านี้ `run.py` ยืนยันโดยรันซ้ำแบบไม่ clamp แล้วได้ `pwm_cmd` เหมือนเดิมทุก period ในทุก scenario และทุก phase

## ผล

| scenario | overshoot | settle (±2%) | ss error | สัดส่วนเวลาที่ saturate | fixed ต่างจาก float สูงสุด |
|---|---|---|---|---|---|
| step 100 count | 30% | 71 ms | 0 | 0% | 1 count |
| step 1 รอบ | 11.7% | 80 ms | 0 | 4.5% | 1 count |
| step 10 รอบ | 1.5% | 163 ms | 0 | 19.1% | 1 count |
| step −10 รอบ | 1.5% | 163 ms | 0 | 19.1% | 1 count |
| 1 รอบ + load 0.01 N·m ที่ 400 ms | 11.7% | 80 ms | 1 count | 1.8% | 1 count |

- เสถียร: spectral radius ของ linear loop ที่ยกเป็นช่วง 1 ms คือ 0.934 (< 1) ทุก phase
- fixed-point ต่างจาก float ไม่เกิน 1 count ทุก scenario
- overshoot 30% ของ step เล็กมาจาก zero ของ PI ไม่ใช่ปัญหาของ fixed-point (float เท่ากัน) ถ้าต้องการลด ให้ส่ง ref เป็น ramp หรือ motion profile แทน step

## Bit width ที่ใช้จริงใน controller (ข้อมูลสำหรับเลือก MUL)

| การคูณ | a (coefficient) | b (ตัวแปร) | ผลคูณ |
|---|---|---|---|
| `KPQ × e` | 19 | 14 | 31 |
| `KDQ × Δenc` | 22 | 10 | 30 |
| `KIQ × e` | 14 | 13 | 26 |
| ผลรวม `p − d + acc` | | | 31 |

bit width เป็น signed ที่สังเกตได้จากทุก scenario ส่วน e (ถูก clamp) และ Δenc (จำกัดด้วยความเร็วสูงสุด) มีขอบเขตที่พิสูจน์ได้ด้วย

**ข้อสรุป:** ผลคูณและผลรวมทุกตัวใน controller อยู่ใน 32 bit ผล 32 bit ล่างของการคูณ signed 32 × 32 จึงถูกต้องครบโดยไม่ต้องใช้ 32 bit บน ผลนี้เป็นเหตุผลของ MUL ที่ตัดสินไว้ใน [timing_model.md](../../docs/timing_model.md) §7 (signed 32 × 32 ให้ผล 32 bit ล่าง, L = 2)

state และ accumulator ของ plant กว้างกว่า 32 bit (θ ใช้ 33 bit, accumulator ใช้ 46 bit) แต่เป็นเรื่องของ plant RTL ซึ่งแยกจาก core ไม่ได้ใช้ MUL ของ TTA
