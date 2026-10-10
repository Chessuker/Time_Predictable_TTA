"""The hardware-in-the-loop environment around the core, as Python.

One source for what the board's top level does besides the core: the plant
(host/plant_model FixedPlant, stepped like dc_motor_plant_mc.sv), the load
torque window, and the setpoint schedule the control program follows. The
same numbers are generated into plant_pkg.sv (host/plant_model/gen_sv.py)
and into programs/control.tta (host/hil/programs.py).

Cycle conventions (t counts cycles from reset, like the core's now):
  - the plant steps at every t with t mod PLANT_STEP == PLANT_PHASE
  - a step at t uses pwm_cmd and tau as they are during cycle t: the last
    io.pwm_cmd write at a cycle w < t, and tau_at(t)
  - its result is visible to io.encoder from cycle t + LATENCY
"""
from host.plant_model import model as m

PLANT_STEP = m.PLANT_STEP
PLANT_PHASE = 0
LATENCY = 10                    # dc_motor_plant_mc.sv (the single-cycle plant would be 1)

# load torque: 0.01 N m during [LOAD_ON, LOAD_OFF) of every LOAD_PERIOD cycles
LOAD_TAU = 10_000               # units of TAU_LSB = 1e-6 N m
LOAD_PERIOD = 100_000_000       # 1 s, the length of the setpoint schedule
LOAD_ON = 40_000_000
LOAD_OFF = 50_000_000

# setpoint schedule of the control program: (periods, ref in encoder counts),
# repeated forever; 1,000 periods of 1 ms = 1 s
SCHEDULE = [(100, 0), (500, 4096), (400, -4096)]


def tau_at(t):
    return LOAD_TAU if LOAD_ON <= t % LOAD_PERIOD < LOAD_OFF else 0


def ref_at(k):
    """Setpoint of period k (k = 0 is the first period that senses)."""
    k %= sum(n for n, _ in SCHEDULE)
    for n, ref in SCHEDULE:
        if k < n:
            return ref
        k -= n
    raise AssertionError


class PlantCosim:
    """FixedPlant driven by a log of io.pwm_cmd writes, advanced lazily.

    encoder(c, pwm_log) returns what io.encoder reads at cycle c. pwm_log is a
    list of (cycle, value) in cycle order that only grows (the ISS's own log,
    or one built by a model); every write at a cycle < c must already be in it.
    """

    def __init__(self, latency=LATENCY, phase=PLANT_PHASE, step=PLANT_STEP, tau=tau_at):
        self.plant = m.FixedPlant()
        self.latency = latency
        self.step = step
        self.tau = tau
        self.next_t = phase
        self._i = 0                 # pwm_log entries already applied
        self._u = 0
        self.steps = 0

    def encoder(self, c, pwm_log):
        while self.next_t + self.latency <= c:
            t = self.next_t
            while self._i < len(pwm_log) and pwm_log[self._i][0] < t:
                self._u = pwm_log[self._i][1]
                self._i += 1
            self.plant.step(self._u, self.tau(t) * m.TAU_LSB)
            self.next_t += self.step
            self.steps += 1
        return self.plant.encoder()
