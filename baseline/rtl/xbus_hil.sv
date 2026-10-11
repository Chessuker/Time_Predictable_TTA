`timescale 1ns / 1ps
// Plant I/O, machine timer and timing monitor of the Phase 6 baseline, on the
// NEORV32 XBUS at 0xF000_0000 (uncached).
//
// One counter, mtime, counts every cycle from reset, so it is the same time
// base as the plant (hil_env) and as the TTA core's now. Every event below is
// stamped with mtime in hardware; software only causes the events.
//
//   word  register      access  event
//   0     MTIME_LO      R
//   1     MTIME_HI      R
//   2     MTIMECMP_LO   R/W     writing it commits {MTIMECMP_HI shadow, value}
//   3     MTIMECMP_HI   R/W     shadow; write it first
//   4     ENCODER       R       SENSE: the value of the cycle of the strobe
//   5     PWM_CMD       R/W     ACT: written in the cycle of the strobe, the plant
//                               sees it from the next cycle (as io.pwm_cmd)
//   6     DIN           R
//   7     START         W       START: software writes it as its first store
//   8     MON_CTRL      R/W     bit 0: monitor on (records + UART TX to the monitor)
//   9     MON_DROPS     R       telemetry words dropped (FIFO full)
//   10    MARK_IN       W       BG_IN: a background interrupt handler starts its body
//   11    MARK_OUT      W       BG_OUT: ... and ends it
//
// Timer: MTIP = mtime >= mtimecmp, registered (irq_mti one cycle later), as the
// RISC-V CLINT; mtimecmp resets to all ones. RELEASE is the first cycle in
// which mtime >= mtimecmp holds.
//
// Monitor: after it is switched on, each RELEASE opens a record and closes the
// previous one, which goes out on the UART as 10 words:
//   MAGIC, k, release[31:0], release[63:32], start - release, sense - release,
//   act - release, encoder read, pwm_cmd written, flags
// Only the first START/SENSE/ACT after a release is kept (an event in the
// release cycle itself belongs to the new record); a missing one reads
// 0xFFFF_FFFF. flags: bit 0..2 start/sense/act seen, bit 3 a second event of a
// kind arrived, bit 4 words were dropped before this record, bits 15:8 the
// number of BG_IN events, bits 31:16 the cycles spent in background bodies
// (a cycle c counts if BG_IN at i <= c < BG_OUT at o, so one body adds o - i),
// both over the record's cycles [release, next release) and saturating.
// Bus responses: every access is acknowledged one cycle after its strobe.
module xbus_hil #(
  parameter int CYCLES_PER_WORD = 1320   // UART: 40 bits x 33 clocks (as the TTA telemetry)
) (
  input  logic        clk,
  input  logic        rst,
  input  logic [3:0]  adr,               // word index
  input  logic [31:0] dat_i,
  input  logic        we,
  input  logic        stb,               // already qualified by the address decoder
  input  logic        cyc,
  output logic [31:0] dat_o,
  output logic        ack,
  output logic        irq_mti,
  input  logic [31:0] encoder,
  input  logic [31:0] din,
  output logic [31:0] pwm_cmd,
  output logic        mon_en,
  output logic        mon_txd
);
  localparam logic [31:0] MAGIC = 32'h4E52_5631;          // "NRV1"
  localparam logic [31:0] NONE  = 32'hFFFF_FFFF;

  // ---------------------------------------------------------------- timer
  logic [63:0] mtime, mtimecmp;
  logic [31:0] cmp_hi_shadow;
  logic        due, due_q;
  assign due = mtime >= mtimecmp;

  always_ff @(posedge clk) begin
    if (rst) begin
      mtime <= '0; due_q <= 1'b0; irq_mti <= 1'b0;
    end else begin
      mtime   <= mtime + 64'd1;
      due_q   <= due;
      irq_mti <= due;
    end
  end

  logic release_now;                     // first cycle of mtime >= mtimecmp
  assign release_now = due && !due_q;

  // ---------------------------------------------------------------- bus
  logic        req;
  logic        sense_now, act_now, start_now;
  assign req       = stb && cyc;
  assign sense_now = req && !we && adr == 4'd4;
  assign act_now   = req && we && adr == 4'd5;
  assign start_now = req && we && adr == 4'd7;
  logic        bg_in_now, bg_out_now;
  assign bg_in_now  = req && we && adr == 4'd10;
  assign bg_out_now = req && we && adr == 4'd11;

  logic [31:0] drops;
  always_ff @(posedge clk) begin
    if (rst) begin
      ack <= 1'b0; dat_o <= '0; mtimecmp <= '1; cmp_hi_shadow <= '1;
      pwm_cmd <= '0; mon_en <= 1'b0;
    end else begin
      ack <= req;
      if (req && we) begin
        unique case (adr)
          4'd2: mtimecmp <= {cmp_hi_shadow, dat_i};
          4'd3: cmp_hi_shadow <= dat_i;
          4'd5: pwm_cmd <= dat_i;
          4'd8: mon_en <= dat_i[0];
          default: ;
        endcase
      end
      if (req && !we) begin
        unique case (adr)
          4'd0: dat_o <= mtime[31:0];
          4'd1: dat_o <= mtime[63:32];
          4'd2: dat_o <= mtimecmp[31:0];
          4'd3: dat_o <= cmp_hi_shadow;
          4'd4: dat_o <= encoder;
          4'd5: dat_o <= pwm_cmd;
          4'd6: dat_o <= din;
          4'd8: dat_o <= {31'b0, mon_en};
          4'd9: dat_o <= drops;
          default: dat_o <= '0;
        endcase
      end
    end
  end

  // ---------------------------------------------------------------- monitor
  logic        open_r;                   // a record is open (a release happened while on)
  logic [31:0] k;
  logic [63:0] rel;
  logic [31:0] t_start, t_sense, t_act, v_enc, v_u;
  logic [2:0]  seen;
  logic        extra, dropped;
  logic        in_bg;                    // between a BG_IN and its BG_OUT
  logic        bg_busy_now;              // this cycle counts as background
  logic [7:0]  bg_n;                     // BG_IN events in the record, saturating
  logic [15:0] bg_cyc;                   // background cycles in the record, saturating
  assign bg_busy_now = bg_in_now || (in_bg && !bg_out_now);

  // snapshot of the record being sent, one word per cycle into the FIFO
  logic [31:0] out [10];
  logic [3:0]  out_n;                    // words left to push
  logic        push;
  logic [31:0] push_data;
  assign push      = out_n != '0;
  assign push_data = out[4'd10 - out_n];

  // offset of an event in this cycle from its release (0 in the release cycle)
  logic [31:0] rel_off;
  logic        live;                     // events in this cycle go into a record
  logic [2:0]  seen_now;                 // what the record already has
  assign rel_off  = release_now ? 32'd0 : mtime[31:0] - rel[31:0];
  assign live     = open_r || release_now;
  assign seen_now = release_now ? 3'b000 : seen;

  always_ff @(posedge clk) begin
    if (rst || !mon_en) begin
      open_r <= 1'b0; k <= '0; out_n <= '0; seen <= '0; extra <= 1'b0;
      in_bg <= 1'b0; bg_n <= '0; bg_cyc <= '0;
    end else begin
      if (bg_in_now) in_bg <= 1'b1;
      else if (bg_out_now) in_bg <= 1'b0;
      if (push) out_n <= out_n - 1'b1;
      if (release_now) begin
        if (open_r) begin
          out[0] <= MAGIC;           out[1] <= k;
          out[2] <= rel[31:0];       out[3] <= rel[63:32];
          out[4] <= seen[0] ? t_start : NONE;
          out[5] <= seen[1] ? t_sense : NONE;
          out[6] <= seen[2] ? t_act   : NONE;
          out[7] <= v_enc;           out[8] <= v_u;
          out[9] <= {bg_cyc, bg_n, 3'b0, dropped, extra, seen};
          out_n  <= 4'd10;
          k      <= k + 1'b1;
        end
        open_r <= 1'b1;
        rel    <= mtime;
        seen   <= '0;
        extra  <= 1'b0;
        v_enc  <= '0;
        v_u    <= '0;
        bg_n   <= {7'b0, bg_in_now};       // the release cycle is the new record's first
        bg_cyc <= {15'b0, bg_busy_now};
      end else if (open_r) begin
        if (bg_in_now && bg_n != '1) bg_n <= bg_n + 1'b1;
        if (bg_busy_now && bg_cyc != '1) bg_cyc <= bg_cyc + 1'b1;
      end
      // events after the release block, so in the release cycle they land in the new record
      if (live && start_now) begin
        if (!seen_now[0]) begin t_start <= rel_off; seen[0] <= 1'b1; end else extra <= 1'b1;
      end
      if (live && sense_now) begin
        if (!seen_now[1]) begin t_sense <= rel_off; v_enc <= encoder; seen[1] <= 1'b1; end
        else extra <= 1'b1;
      end
      if (live && act_now) begin
        if (!seen_now[2]) begin t_act <= rel_off; v_u <= dat_i; seen[2] <= 1'b1; end
        else extra <= 1'b1;
      end
    end
  end

  // ---------------------------------------------------------------- UART
  logic        tx_start;
  logic [31:0] tx_data;
  logic [31:0] drops_prev;
  fu_telem #(.DEPTH(32), .CYCLES_PER_WORD(CYCLES_PER_WORD)) u_fifo (
    .clk, .rst, .push, .data(push_data), .drops, .tx_start, .tx_data
  );
  always_ff @(posedge clk) begin
    if (rst) begin dropped <= 1'b0; drops_prev <= '0; end
    else begin
      drops_prev <= drops;
      if (drops != drops_prev) dropped <= 1'b1;
      else if (release_now && open_r) dropped <= 1'b0;
    end
  end
  /* verilator lint_off PINCONNECTEMPTY */
  uart_tx_word #(.CLKS_PER_BIT(CYCLES_PER_WORD / 40)) u_uart (
    .clk, .rst, .start(tx_start), .word(tx_data), .txd(mon_txd), .busy(), .ready()
  );
  /* verilator lint_on PINCONNECTEMPTY */
endmodule
