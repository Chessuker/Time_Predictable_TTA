# Reading list — Time-Predictable TTA

รวบรวม 2026-09-22 ก่อนเริ่ม Phase 0 จัดตามความสำคัญต่อการออกแบบ timer/ISA

## Tier 1 — อ่านก่อน Phase 0 (กระทบการออกแบบ timer/ISA โดยตรง)

1. **Lickly, Liu, Kim, Patel, Edwards, Lee (2008). Predictable Programming on a Precision Timed Architecture.** CASES 2008.
   - [PDF](https://www.cs.columbia.edu/~sedwards/papers/lickly2008predictable.pdf) · [TR version](https://www.cs.columbia.edu/~sedwards/papers/lickly2008predictable-tr.pdf)
   - ต้นแบบ `deadline` instruction ที่ `t_delay_until` / `t_arm` ของเราเลียนแบบ semantics ของ timer เป็น cycle count ล้วนเหมือนเรา
2. **Ip, Edwards (2006). A Processor Extension for Cycle-Accurate Real-Time Software.**
   - [PDF](https://www.cs.columbia.edu/~sedwards/papers/ip2006processor.pdf)
   - deadline instruction เวอร์ชันแรกสุด บน single-cycle non-pipelined core ใกล้เคียง TTA เราที่สุด (ไม่มี multithreading มาซับซ้อน)
3. **Liu, Isaac (2012). Precision Timed Machines.** PhD thesis, UC Berkeley, UCB/EECS-2012-113.
   - [PDF](https://www2.eecs.berkeley.edu/Pubs/TechRpts/2012/EECS-2012-113.pdf)
   - นิยาม timing instruction ครบชุด (`get_time`, `delay_until`, `exception_on_expire`, `deactivate_exception`) พร้อม semantics ละเอียด map ตรงกับ TMR FU ของเราแทบ 1:1 อ่านบท timing instruction ก่อน
4. **Zimmer, Broman, Shaver, Lee (2014). FlexPRET: A Processor Platform for Mixed-Criticality Systems.** RTAS 2014.
   - [TR](https://www2.eecs.berkeley.edu/Pubs/TechRpts/2013/EECS-2013-172.html) · [GitHub](https://github.com/pretis/flexpret)
   - RISC-V + timing instruction ที่มีโค้ดจริงให้ดู เทียบว่าเขานิยาม trap-on-deadline ยังไง
5. **Bhagyanath, Schneider (2014). TTA as Predictable Architecture for Real-Time Applications.** ICSEMR 2014, TU Kaiserslautern.
   - [PDF](https://es.cs.rptu.de/publications/datarsg/BhSc14.pdf) · [IEEE](https://ieeexplore.ieee.org/document/7043544/)
   - paper เดียวที่เจอที่พูดถึง TTA + WCET ตรงๆ สั้น อ่านเพื่อรู้ว่ามีใครทำก่อนแล้วเราต่างตรงไหน (เขาไม่มี timing instruction)

## Tier 2 — พื้นฐาน TTA และ WCET

6. **Corporaal, H. Transport Triggered Architectures: Principles and Consequences.** TTA seminar slides, 2002.
   - [PDF](http://www.openasip.org/move/doc/TTA_seminar/Corporaal.pdf) · [OpenASIP TTA intro](https://openasip.org/tta.html)
   - ศัพท์มาตรฐาน TTA: trigger port, operand port, socket, bus, bypass ควรใช้ศัพท์ให้ตรงกับ community
7. **Corporaal, H. Computation in the Context of Transport Triggered Architectures.** Int. J. Parallel Programming.
   - [Springer](https://link.springer.com/article/10.1023/A:1007511206083) (paywall)
8. **OpenASIP / TCE toolset.**
   - [GitHub](https://github.com/cpc/openasip) · [openasip.org](https://openasip.org/)
   - ดู ADF (architecture definition) format ว่าเขา encode port/latency ยังไง ช่วยตอน freeze port map
9. **Wilhelm et al. (2008). The Worst-Case Execution-Time Problem: Overview of Methods and Survey of Tools.** ACM TECS 7(3).
   - [PDF](https://www.cs.fsu.edu/~whalley/papers/tecs07.pdf) · [ACM](https://dl.acm.org/doi/10.1145/1347375.1347389)
   - survey มาตรฐาน อ่านส่วน flow analysis + IPET (ILP-based longest path) WCET tool ของเราจะทำแบบเดียวกันแต่ง่ายกว่า
10. **Puschner, P. The Single-Path Approach Towards WCET-Analysable Software.**
    - [PDF](https://www.researchgate.net/profile/Peter-Puschner/publication/4071115_The_single-path_approach_towards_WCET-analysable_software/links/0fcfd50ff154e1bd56000000/The-single-path-approach-towards-WCET-analysable-software.pdf)
    - stretch goal ในแผน แต่ควรรู้ตั้งแต่ตอนออกแบบ CMP/PC ว่าจะรองรับ predicated move ในอนาคตไหม

## Tier 3 — บริบทและ baseline

11. **Edwards, Lee (2007). The Case for the Precision Timed (PRET) Machine.** DAC 2007.
    - [PDF](https://www.cs.columbia.edu/~sedwards/papers/edwards2006case.pdf) · [IEEE](https://ieeexplore.ieee.org/document/4261184)
    - manifesto สั้น เอาไปอ้างใน README
12. **Schoeberl, M. (2009). Time-Predictable Computer Architecture.** EURASIP J. Embedded Systems.
    - [open access](https://jes-eurasipjournals.springeropen.com/articles/10.1155/2009/758480) · [PDF](https://www.jopdesign.com/doc/ca4rts.pdf)
    - เหตุผลว่าทำไม cache/branch prediction ทำลาย WCET
13. **Schoeberl et al. (2018). Patmos: a time-predictable microprocessor.** Real-Time Systems.
    - [PDF](https://backend.orbit.dtu.dk/ws/files/145805464/patmos.pdf) · [T-CREST 2015 JSA](https://www.cs.york.ac.uk/rts/static/papers/Schoeberl2015.pdf) · [patmos.compute.dtu.dk](https://patmos.compute.dtu.dk/)
    - RISC ฝั่ง time-predictable ที่ scale ใหญ่ ดูวิธีเขา validate WCET tool vs hardware
14. **Liu, Reineke, Broman, Zimmer, Lee (2012). A PRET Microarchitecture Implementation with Repeatable Timing and Competitive Performance.** ICCD 2012.
    - [PDF](https://ptolemy.berkeley.edu/projects/chess/pubs/919/ptarm-iccd-2012-accepted-version.pdf)
15. **Mæhlum et al. (2025). Programming Time-Predictable Processors with Lingua Franca.** NG-RES 2025.
    - [PDF](https://drops.dagstuhl.de/storage/01oasics/oasics-vol128-ng-res2025/OASIcs.NG-RES.2025.1/OASIcs.NG-RES.2025.1.pdf)
    - งานล่าสุด ดูว่า deadline semantics ระดับภาษาสูง map ลง ISA ยังไง
16. **Jellum et al. (2023). InterPRET: a Time-predictable Multicore Processor.** CPS-IoT Week 2023.
    - [ACM](https://dl.acm.org/doi/fullHtml/10.1145/3576914.3587497) · [iCyPhy](https://www.icyphy.org/publications/2023_JellumEtAl_InterPRET/)
    - FlexPRET + NoC เกี่ยวกับ future work (CGRA/GALS)
17. **Kopetz, H. (2011). Real-Time Systems: Design Principles for Distributed Embedded Applications.** 2nd ed. (ใช้ 2nd ed. แทน 3rd ed. 2022 ที่หาไม่ได้)
    - [Springer](https://link.springer.com/book/10.1007/978-3-031-11992-7) (หนังสือ)
    - อ่านเฉพาะบท time-triggered + sparse time ถ้าจะทำ schedule table (stretch)

## HIL plant (Phase 5, อ่านทีหลังได้)

18. **Hardware-In-the-Loop simulation of a DC-machine with Intel FPGA boards (2018).**
    - [ResearchGate](https://www.researchgate.net/publication/330489430_Hardware-In-the-Loop_simulation_of_a_DC-machine_with_INTEL_FPGA_boards)
    - เทียบ float vs fixed-point ตรงกับสิ่งที่ Phase 0 ข้อ 4 ต้องตัดสินใจ
19. **FPGA Based Real Time Simulation for Electrical Machines.** IFAC.
    - [ScienceDirect](https://www.sciencedirect.com/science/article/pii/S1474667016377837)
    - state-space + numerical integration แบบ fixed-point

## หมายเหตุจากการค้น

- **ช่องว่างที่ยืนยันได้:** ไม่มี paper ไหนรวม TTA + timing instruction (delay_until / deadline trap) + formal proof ของ trap timing claim ของโปรเจ็กต์นี้ยังใหม่จริง
- ข้อ 1–3 ใช้ชื่อ semantics ต่างกันเล็กน้อย (`deadline` vs `delay_until` + `exception_on_expire`) ตอน Phase 0 ควรเลือกชุดเดียวแล้วอ้างที่มาใน `timing_model.md`
- ยังไม่ได้ค้น: AMD/Xilinx MicroBlaze doc สำหรับ baseline (Phase 6), งาน formal verification ของ timer/deadline (Phase 3)
