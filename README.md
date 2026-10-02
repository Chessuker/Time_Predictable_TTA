# Time_Predictable_TTA

## Non-goals

- **ไม่มี DRAM หรือ storage นอกชิปใน Phase 0–4** memory ทุกตัวที่ core เข้าถึงเป็น on-chip synchronous SRAM ที่ latency คงที่ ส่วน DDR3, flash, SSD และ HDD มี latency ที่ขึ้นกับ state ของอุปกรณ์ (row buffer, refresh, garbage collection, seek) ซึ่งขัดกับเป้าหมาย WCET tight 0 cycle รายละเอียดอยู่ใน [docs/timing_model.md](docs/timing_model.md) §2
  - *อาจเพิ่มในอนาคต* ผ่าน FIFO แบบไม่ block หรือ predictable memory controller (PRET DRAM controller, T-CREST/Patmos)
- **ไม่แข่งเรื่อง throughput หรือ average-case performance** core ทำ 1 move ต่อ cycle ไม่มี cache, speculation หรือ branch prediction และทุก instruction ใช้เวลาคงที่โดยตั้งใจ จึงคาดว่าจะช้ากว่า soft-core ทั่วไปอย่าง MicroBlaze เมื่อวัดเวลาเฉลี่ย สิ่งที่โปรเจกต์นี้วัดคือ predictability ได้แก่ jitter ของจุด sense/actuate, ความ tight ของ WCET และ trap ที่ตรง cycle ไม่ใช่ความเร็ว แนวเดียวกับ PTARM ซึ่งช้ากว่า LEON3 เฉลี่ย 3.54 เท่า และผู้เขียนระบุเองว่าการเทียบแบบนั้นไม่ได้วัดจุดแข็งของมัน (Lickly et al. 2008 §7)
- **รองรับเฉพาะโปรแกรมที่ bounded และ structured** ใช้ branch ที่ขึ้นกับข้อมูลได้ และใช้ loop ได้ถ้ามี `@loop_bound` หรือ loop นั้นผ่าน sync point (`t_advance`/`t_wait`) ส่วนที่ไม่รองรับและ assembler/WCET tool จะ reject คือ
  - recursion
  - indirect jump ที่ไม่ใช่ return
  - loop ที่ไม่มีขอบเขต
  
  เหตุผลคือ WCET ต้องคำนวณได้แบบ static และตรงกับที่วัดได้บนบอร์ด โปรแกรมที่อยู่นอกกรอบนี้ (เช่น dynamic programming ที่ขนาดขึ้นกับ input โดยไม่มีขอบเขตบน) ไม่มี WCET ที่พิสูจน์ได้ รายละเอียดอยู่ใน [docs/timing_model.md](docs/timing_model.md) §3–§4
