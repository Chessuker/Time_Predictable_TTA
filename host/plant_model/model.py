"""DC motor position control: float reference model and fixed-point model.

Timeline follows docs/timing_model.md §5 example 3: the plant steps every
PLANT_STEP cycles, the control task wakes every PERIOD cycles, senses at
anchor + 1 and actuates at anchor + 1 + SENSE_TO_ACT.
"""
import math

import numpy as np
from scipy.linalg import expm

# ---- time base (cycles at 100 MHz) ----
F_CLK = 100_000_000
PLANT_STEP = 10_000          # 100 us
PERIOD = 100_000             # 1 ms
SENSE_TO_ACT = 1_000         # v of t_wait
SENSE_OFF = 1                # D
ACT_OFF = SENSE_OFF + SENSE_TO_ACT
T_PLANT = PLANT_STEP / F_CLK
T_CTRL = PERIOD / F_CLK

# ---- motor: CTMS "DC Motor Position" example parameters ----
J = 3.2284e-6    # kg m^2
B = 3.5077e-6    # N m s
K = 0.0274       # N m/A = V s/rad
R = 4.0          # ohm
L = 2.75e-6      # H. L/R = 0.69 us << T_PLANT, so the current state is dropped

A_C = (B + K * K / R) / J    # 1/s, mechanical pole
BETA = K / (R * J)           # rad/s^2 per V

# ---- interfaces ----
V_MAX = 12.0
U_MAX = 2047                 # pwm_cmd full scale, signed 12 bit
CPR = 4096                   # encoder counts/rev (1024 lines x4)
G = CPR / (2 * math.pi)      # counts/rad
LSB_PER_V = U_MAX / V_MAX
TAU_LSB = 1e-6               # load torque input unit for the fixed plant, N m

# ---- controller design ----
# Continuous PID pole placement: closed loop (s^2 + 2 zeta wn s + wn^2)(s + p3).
# P and I act on the error, D on measurement only (no derivative kick on steps).
# Setpoint weighting on P was tried and dropped: with the integrator clamped at
# +-U_MAX it cannot supply KP (1 - b) ref for large ref, so the loop never settles.
WN = 2 * math.pi * 15.0
ZETA = 0.8
P3 = WN


def design(wn=WN, zeta=ZETA, p3=P3):
    """Return per-sample gains (KP, KD, KI) in pwm LSB and encoder counts."""
    kp_r = (wn ** 2 + 2 * zeta * wn * p3) / BETA        # V/rad
    kd_r = (2 * zeta * wn + p3 - A_C) / BETA            # V s/rad
    ki_r = wn ** 2 * p3 / BETA                          # V/(rad s)
    s = LSB_PER_V / G
    return kp_r * s, kd_r / T_CTRL * s, ki_r * T_CTRL * s


KP, KD, KI = design()

# ---- fixed-point formats ----
SH = 16                      # controller gain fraction bits
E_CLAMP = 1 << 12            # error clamp before multiply (see README)
KPQ = round(KP * (1 << SH))
KDQ = round(KD * (1 << SH))
KIQ = round(KI * (1 << SH))
ACC_MAX = U_MAX << SH

FP = 16                      # plant state fraction bits
FC = 24                      # plant coefficient fraction bits


def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def sbits(x):
    """Bits needed to hold x as a two's complement signed integer."""
    return (x if x >= 0 else -x - 1).bit_length() + 1


class WidthLog:
    """Max signed width seen per named operand/result."""

    def __init__(self):
        self.max = {}

    def see(self, name, x):
        b = sbits(x)
        if b > self.max.get(name, 0):
            self.max[name] = b
        return x

    def mul(self, name, a, b):
        self.see(name + " .a", a)
        self.see(name + " .b", b)
        return self.see(name + " =", a * b)

    def merge(self, other):
        for k, v in other.max.items():
            self.max[k] = max(self.max.get(k, 0), v)


def plant_discrete():
    """ZOH at T_PLANT in scaled units.

    x = [theta (counts), omega (counts per plant step)], inputs [u (LSB), tau (N m)].
    """
    ac = np.array([[0.0, 1.0], [0.0, -A_C]])
    bc = np.array([[0.0, 0.0], [BETA, -1.0 / J]])
    m = np.zeros((4, 4))
    m[:2, :2] = ac
    m[:2, 2:] = bc
    md = expm(m * T_PLANT)
    s = np.diag([G, G * T_PLANT])
    a_s = s @ md[:2, :2] @ np.linalg.inv(s)
    b_s = s @ md[:2, 2:] @ np.diag([1.0 / LSB_PER_V, 1.0])
    return a_s, b_s


# ---------------------------------------------------------------- plants

class FloatPlant:
    def __init__(self, exact_encoder=False):
        self.a, self.b = plant_discrete()
        self.x = np.zeros(2)
        self.exact = exact_encoder

    def step(self, u, tau):
        self.x = self.a @ self.x + self.b @ np.array([u, tau])

    def encoder(self):
        return self.x[0] if self.exact else math.floor(self.x[0])

    def theta(self):
        return self.x[0]


class FixedPlant:
    """States Q.FP, coefficients Q.FC, round-half-up on every update.

    theta' = theta + round((c01 omega + cu0 u + ct0 tau) / 2^FC)   (a00 = 1, a10 = 0)
    omega' =         round((c11 omega + cu1 u + ct1 tau) / 2^FC)
    """

    def __init__(self, log=None):
        a, b = plant_discrete()
        assert a[0, 0] == 1.0 and a[1, 0] == 0.0
        self.c01 = round(a[0, 1] * (1 << FC))
        self.c11 = round(a[1, 1] * (1 << FC))
        self.cu = [round(b[i, 0] * (1 << (FC + FP))) for i in range(2)]
        self.ct = [round(b[i, 1] * TAU_LSB * (1 << (FC + FP))) for i in range(2)]
        self.x = [0, 0]
        self.log = log or WidthLog()

    def step(self, u, tau):
        tau_q = round(tau / TAU_LSB)
        lg = self.log
        th, om = self.x
        half = 1 << (FC - 1)
        acc0 = (lg.mul("plant th<-om", self.c01, om) + lg.mul("plant th<-u", self.cu[0], u)
                + lg.mul("plant th<-tau", self.ct[0], tau_q))
        acc1 = (lg.mul("plant om<-om", self.c11, om) + lg.mul("plant om<-u", self.cu[1], u)
                + lg.mul("plant om<-tau", self.ct[1], tau_q))
        lg.see("plant th acc", acc0)
        lg.see("plant om acc", acc1)
        self.x = [lg.see("plant th", th + ((acc0 + half) >> FC)),
                  lg.see("plant om", (acc1 + half) >> FC)]

    def encoder(self):
        return self.x[0] >> FP

    def theta(self):
        return self.x[0] / (1 << FP)


# ----------------------------------------------------------- controllers

class FloatPID:
    """PID on position, D on measurement, conditional integration + clamp."""

    def __init__(self, saturate=True):
        self.i = 0.0
        self.prev = None
        self.sat_on = saturate

    def step(self, ref, enc):
        if self.prev is None:
            self.prev = enc
        e = ref - enc
        u = KP * e - KD * (enc - self.prev) + self.i
        self.prev = enc
        sat = self.sat_on and abs(u) > U_MAX
        if sat:
            u = math.copysign(U_MAX, u)
        if not (sat and e * u > 0):
            self.i += KI * e
            if self.sat_on:
                self.i = clamp(self.i, -U_MAX, U_MAX)
        return u


class FixedPID:
    """Same structure as FloatPID with integer arithmetic the TTA core will run."""

    def __init__(self, log=None, e_clamp=E_CLAMP):
        self.acc = 0
        self.prev = None
        self.log = log or WidthLog()
        self.e_clamp = e_clamp

    def step(self, ref, enc):
        lg = self.log
        if self.prev is None:
            self.prev = enc
        e = clamp(lg.see("ctrl e (before clamp)", ref - enc), -self.e_clamp, self.e_clamp)
        de = lg.see("ctrl d_enc", enc - self.prev)
        self.prev = enc
        p = lg.mul("ctrl Kp*e", KPQ, e)
        d = lg.mul("ctrl Kd*d_enc", KDQ, de)
        s = lg.see("ctrl sum p-d+acc", p - d + self.acc)
        u = (s + (1 << (SH - 1))) >> SH
        sat = abs(u) > U_MAX
        if sat:
            u = clamp(u, -U_MAX, U_MAX)
        if not (sat and e * u > 0):
            self.acc = clamp(self.acc + lg.mul("ctrl Ki*e", KIQ, e), -ACC_MAX, ACC_MAX)
            lg.see("ctrl acc", self.acc)
        return u


# ------------------------------------------------------------ closed loop

class Loop:
    """Plant + controller on the cycle timeline of timing_model.md §5 ex. 3."""

    def __init__(self, plant, ctrl, phase=0):
        self.plant = plant
        self.ctrl = ctrl
        self.phase = phase
        self.u = 0

    def run_period(self, k, ref, tau):
        anchor = k * PERIOD
        t_sense = anchor + SENSE_OFF
        t_act = anchor + ACT_OFF
        first = anchor + (self.phase - anchor) % PLANT_STEP
        sensed = acted = False
        u_new = enc = None
        for t in range(first, anchor + PERIOD, PLANT_STEP):
            # sense at t_sense sees plant steps at cycles < t_sense
            if not sensed and t >= t_sense:
                enc = self.plant.encoder()
                u_new = self.ctrl.step(ref, enc)
                sensed = True
            # pwm written at t_act is used by plant steps at cycles > t_act
            if sensed and not acted and t > t_act:
                self.u = u_new
                acted = True
            self.plant.step(self.u, tau)
        if not sensed:
            enc = self.plant.encoder()
            u_new = self.ctrl.step(ref, enc)
        if not acted:
            self.u = u_new
        return enc, u_new


def simulate(plant, ctrl, n_periods, ref_fn, tau_fn, phase=0):
    loop = Loop(plant, ctrl, phase)
    t, enc, u, th = [], [], [], []
    for k in range(1, n_periods + 1):
        e, un = loop.run_period(k, ref_fn(k), tau_fn(k))
        t.append(k * T_CTRL)
        enc.append(e)
        u.append(un)
        th.append(plant.theta())
    return np.array(t), np.array(enc, float), np.array(u, float), np.array(th)


def lifted_matrix(phase=0):
    """Linear map of [theta, omega, i, prev, u] over one period, no saturation
    and no quantization. Spectral radius < 1 means the sampled loop is stable."""
    cols = []
    for idx in range(5):
        z = np.zeros(5)
        z[idx] = 1.0
        plant = FloatPlant(exact_encoder=True)
        plant.x = z[:2].copy()
        ctrl = FloatPID(saturate=False)
        ctrl.i, ctrl.prev = z[2], z[3]
        loop = Loop(plant, ctrl, phase)
        loop.u = z[4]
        loop.run_period(1, 0.0, 0.0)
        cols.append([plant.x[0], plant.x[1], ctrl.i, ctrl.prev, loop.u])
    return np.array(cols).T
