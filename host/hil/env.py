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


# switch mode (docs/io_interface.md): io.din from the board input interface
N_DB = 500_000                  # board_din.sv debounce: stable for N_DB + 1 cycles (5 ms)
DIN_SP = 0b011                  # io.din[1:0]: setpoint code, committed with BTN0
DIN_LOAD = 0b100                # io.din[2]: load switch, live; hil_env adds it to the load window
SP_TABLE = (0, 4096, -4096, 2048)   # control_sw.tta: setpoint by din[1:0], encoder counts


def tau_at(t):
    return LOAD_TAU if LOAD_ON <= t % LOAD_PERIOD < LOAD_OFF else 0


def value_at(changes, t):
    """Value at cycle t of a signal given as [(cycle, value)] in cycle order (0 before)."""
    v = 0
    for c, x in changes:
        if c > t:
            break
        v = x
    return v


def tau_with_din(din, window=tau_at):
    """hil_env's load: on in the window or while io.din[2] (the load switch) is 1.
    din is io.din at the core input as [(cycle, value)], e.g. from board_din.interface."""
    import bisect
    cycles = [c for c, _ in din]

    def tau(t):
        i = bisect.bisect_right(cycles, t) - 1
        on = i >= 0 and din[i][1] & DIN_LOAD
        return LOAD_TAU if on or window(t) else 0
    return tau


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
