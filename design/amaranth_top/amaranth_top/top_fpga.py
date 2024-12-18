from amaranth import *
from amaranth.lib import io
from amaranth.lib.wiring import Component, In, Out, connect
from amaranth.lib.cdc import ResetSynchronizer
from amaranth.build import Resource, Pins, Attrs

from amaranth_boards.de10_nano import DE10NanoPlatform

from .top import Top
from .audio_constants import MIC_FREQ_HZ, NUM_MICS
from .audio_mic import MicCapture
from .audio_convolve import Convolver
from .hps_pll_cyclone_v import IntelPLL
from .hps_axi3_csr import AXI3CSRBridge
from .hps_cyclone_v import CycloneVHPS
from .hps_bus import AudioRAMBus
from .hps_axi3 import AXI3Signature

class AudioAdapter(Component):
    audio: In(AudioRAMBus())
    axi: Out(AXI3Signature(addr_width=32, data_width=32, id_width=8,
        user_width={"aw": 5, "ar": 5}))

    def elaborate(self, platform):
        m = Module()

        axi = self.axi
        audio = self.audio

        m.d.comb += [
            axi.aw.id.eq(0), # always write with id 0
            axi.aw.len.eq(audio.length),
            axi.aw.size.eq(0b001), # two bytes at a time
            axi.aw.burst.eq(0b01), # burst mode: increment
            # heard vague rumors that these should just all be 1 to activate
            # caching as expected...
            axi.aw.cache.eq(0b1111),
            # and 5 1 bits for the user data too (though that is from the
            # handbook)...
            axi.aw.user.eq(0b11111),
            axi.aw.valid.eq(audio.addr_valid),
            audio.addr_ready.eq(axi.aw.ready),

            axi.w.data.eq( # route 16 bit data to both 32 bit halves
                Cat(audio.data, audio.data)),
            axi.w.valid.eq(audio.data_valid),
            axi.w.last.eq(audio.data_last),
            audio.data_ready.eq(axi.w.ready),

            axi.b.ready.eq(axi.b.valid),
            audio.txn_done.eq(axi.b.valid),
        ]

        # transform 16 bit audio bus into 32 bit AXI bus
        # remove bottom two address bits to stay 32 bit aligned
        m.d.comb += axi.aw.addr.eq(audio.addr & 0xFFFFFFFC)
        curr_half = Signal() # 16 bit half of the 32 bit word we're writing
        with m.If(axi.aw.valid & axi.aw.ready):
            # latch which half we are writing initially
            m.d.sync += curr_half.eq(audio.addr[1])
        with m.If(axi.w.valid & axi.w.ready):
            # swap halves after every write
            m.d.sync += curr_half.eq(~curr_half)
        # set strobes to enable low or high bytes according to current half
        m.d.comb += axi.w.strb.eq(Mux(curr_half, 0b1100, 0b0011))

        # plug off AXI port address write and read data ports
        m.d.comb += [
            axi.ar.valid.eq(0),
            axi.r.ready.eq(axi.r.valid),
        ]

        return m

class FPGATop(Elaboratable):
    def elaborate(self, platform):
        m = Module()

        # set up basic resources
        clk50 = platform.request("clk50", 0).i
        status = Cat([platform.request("led", n).o for n in range(8)])
        button = platform.request("button", 1).i
        button_off = platform.request("button", 0).i
        switches = Cat([platform.request("switch", n).i for n in range(4)])

        # add each GPIO as its own resource
        def add_resources():
            power_pins = [11, 12, 29, 30]
            for pin in range(1, 41):
                if pin in power_pins: continue
                for conn in range(2):
                    platform.add_resources([Resource(
                        f"gpio{conn}",
                        pin,
                        Pins(f"{pin}", dir="io", conn=("gpio", conn)),
                        Attrs(IO_STANDARD="3.3-V LVTTL"),
                    )])
        add_resources()

        # -------------------------------------------------------------
        # GPIO PIN ASSIGNMENTS
        # -------------------------------------------------------------
        # tuple of (GPIO connector (0 or 1), Pin Number (1-40))
        sck_pins = [
            (0, 17),    # A1
            (0, 3),     # A2
            (0, 10),    # A3
            (0, 24),    # A4
            (0, 38),    # A5
            (0, 31)     # A6
        ]

        ws_pins = [
            (0, 9),     # A1
            (0, 4),     # A2
            (0, 18),    # A3
            (0, 32),    # A4
            (0, 37),    # A5
            (0, 23)     # A6
        ]

        mic_pins = [
            (0, 13),    # Ant 1 Mic 1 R/L
            (0, 15),    # Ant 1 Mic 2 R/L
            (0, 7),     # Ant 1 Mic 3 R/L
            (0, 19),    # Ant 1 Mic 4 R/L

            (0, 2),     # Ant 2 Mic 1 R/L
            (0, 1),     # Ant 2 Mic 2 R/L
            (0, 6),     # Ant 2 Mic 3 R/L
            (0, 5),     # Ant 2 Mic 4 R/L

            (0, 16),    # Ant 3 Mic 1 R/L
            (0, 14),    # Ant 3 Mic 2 R/L
            (0, 20),    # Ant 3 Mic 3 R/L
            (0, 8),     # Ant 3 Mic 4 R/L

            (0, 28),    # Ant 4 Mic 1 R/L
            (0, 26),    # Ant 4 Mic 2 R/L
            (0, 34),    # Ant 4 Mic 3 R/L
            (0, 22),    # Ant 4 Mic 4 R/L

            (0, 39),    # Ant 5 Mic 1 R/L
            (0, 40),    # Ant 5 Mic 2 R/L
            (0, 35),    # Ant 5 Mic 3 R/L
            (0, 36),    # Ant 5 Mic 4 R/L

            (0, 25),    # Ant 6 Mic 1 R/L
            (0, 27),    # Ant 6 Mic 2 R/L
            (0, 21),    # Ant 6 Mic 3 R/L
            (0, 33),    # Ant 6 Mic 4 R/L
        ]
        # -------------------------------------------------------------
        # -------------------------------------------------------------
        # -------------------------------------------------------------

        # set up HPS
        hps = CycloneVHPS()

        # wire up main clock domain and PLL. note that all PLL outputs are
        # marked as asynchronous w.r.t. its inputs and each other in the .sdc
        m.domains.sync = sync = ClockDomain()
        m.d.comb += sync.clk.eq(clk50)
        main_pll = IntelPLL("50 MHz")

        # hook up another PLL for the convolver so the ratios work out
        conv_pll = IntelPLL("50 MHz")

        # hold whole design in reset until PLL is locked
        reset = Signal()
        m.d.comb += reset.eq(
            hps.h2f_rst & main_pll.o_locked & conv_pll.o_locked)
        m.submodules += ResetSynchronizer(reset)

        # set up mic capture domain
        mic_capture_freq = MIC_FREQ_HZ * MicCapture.REL_FREQ
        m.domains.mic_capture = mic_capture = ClockDomain()
        m.d.comb += mic_capture.clk.eq(
            main_pll.add_output(f"{mic_capture_freq} Hz"))
        m.submodules += ResetSynchronizer(reset, domain="mic_capture")

        # set up the convolver domain
        convolver_freq = MIC_FREQ_HZ * Convolver.REL_FREQ
        # round up to the next multiple of 1MHz so the PLL ratios will be
        # realizable and Quartus won't explode
        convolver_freq = ((convolver_freq//1e6)+1)*1e6
        m.domains.convolver = convolver = ClockDomain()
        m.d.comb += convolver.clk.eq(
            conv_pll.add_output(f"{convolver_freq} Hz"))
        m.submodules += ResetSynchronizer(reset, domain="convolver")

        # wire up top module
        m.submodules.top = top = Top()
        m.d.comb += [
            top.button_raw.eq(button),
            top.button_off_raw.eq(button_off),
            top.switches_raw.eq(switches),
            status.eq(top.status_leds),
        ]

        # wire up microphone data bus
        for (conn, pin) in sck_pins:
            buf = io.Buffer("o", platform.request(f"gpio{conn}", pin, dir="-"))
            m.submodules += buf

            m.d.comb += buf.o.eq(top.mic_sck)

        for (conn, pin) in ws_pins:
            buf = io.Buffer("o", platform.request(f"gpio{conn}", pin, dir="-"))
            m.submodules += buf

            m.d.comb += buf.o.eq(top.mic_ws)

        for ci, (conn, pin) in enumerate(mic_pins):
            buf = io.Buffer("i", platform.request(f"gpio{conn}", pin, dir="-"))
            m.submodules += buf

            m.d.comb += top.mic_data_raw[ci].eq(buf.i)

        # hook up audio RAM bus to AXI port
        m.submodules.f2h = f2h = hps.request_fpga2hps_port(data_width=32)
        m.submodules.audio_adapter = audio_adapter = AudioAdapter()
        connect(m, top.audio_ram, audio_adapter.audio)
        connect(m, audio_adapter.axi, f2h)

        # hook up AXI -> CSR bridge
        m.submodules.csr_bridge = csr_bridge = AXI3CSRBridge()
        m.submodules.h2f_lw = h2f_lw = hps.request_hps2fpga_lw_port()
        connect(m, h2f_lw, csr_bridge.axi_bus)
        connect(m, csr_bridge.csr_bus, top.csr_bus)

        # submodules we don't want Amaranth to elaborate until now
        m.submodules.hps = hps
        m.submodules.main_pll = main_pll
        m.submodules.conv_pll = conv_pll

        return m

def gen_build():
    import sys
    from pathlib import Path

    # constraints go in the .sdc file
    constraints = [
        # PLL clock stock constraints
        "derive_pll_clocks",
        "derive_clock_uncertainty",

        # eliminate false paths to async clear inputs of synchronizers
        # (as created by Amaranth's ResetSynchronizer)
        """
        foreach path [get_entity_instances altera_std_synchronizer] {
            set path_pins [get_pins -nowarn $path|dreg[*]|clrn];
            if {[get_collection_size $path_pins] > 0} {
                set_false_path -to $path_pins;
            }
        }""",
    ]

    # settings go in the .qsf file
    settings = [
        # see comment in the HPS file
        "set_instance_assignment -name hps_partition on -entity "
            "hps_secret_dummy_partition_module",
    ]

    plan = DE10NanoPlatform().build(FPGATop(),
        add_constraints="\n".join(constraints),
        add_settings="\n".join(settings),
        do_build=False,
        # prevent source paths from being written into the design, in particular
        # absolute paths!
        strip_internal_attrs=True)

    plan.extract(Path(sys.argv[1]))

if __name__ == "__main__":
    gen_build()
