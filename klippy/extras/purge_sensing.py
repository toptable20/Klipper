# Add text below to printer.cfg to enable purge sensing
#
# [purge_setting]
# sensor_pin: PA4
# retries: 3

SAMPLE_COUNT = 8  # Take 8 subsamples within the MCU
SAMPLE_TIME = 0.010  # with a 0.001s gap between each
REPORT_TIME = 0.100  # and report their average every 1s
THRESHOLD = 20.0  # Threshold value for detection

class LimitHelper:
    def __init__(self, config, idx):
        main_key = "limit%d" % (idx, )
        over_key = "over_%s_gcode" % (main_key, )
        under_key = "under_%s_gcode" % (main_key, )

        printer = config.get_printer()
        self.limit = config.getfloat(main_key)
        self.gcode = printer.lookup_object('gcode')
        gcode_macro = printer.load_object(config, 'gcode_macro')
        self.over_template = gcode_macro.load_template(config, over_key, '')
        self.under_template = gcode_macro.load_template(config, under_key, '')

    def callback(self, read_time, value, last_value):
        if value < self.limit and last_value >= self.limit:
            self.under_template.run_gcode_from_command()
        elif value > self.limit and last_value <= self.limit:
            self.over_template.run_gcode_from_command()


import logging

from kinematics.extruder import PrinterExtruder, ExtruderStepper
from extras.homing import PrinterHoming
import stepper  # , chelper

class PurgeSensing:
    def __init__(self, config):
        
        self.last_value = 0.0
        self.max_value = 0.0

        self.endstop = None
        self.wait_time = 5 # seconds
        self.retries = 0
        self.purge_retries = 0
        self.baseline_value = 0.0

        self.prevSignal = 0
        self.isDetect = False

        self.purgeLoadingDone = False
        self.is_homing = False
        self.homing_completion = None

        self.start_detecting = False
        self.extruder = None

        self.printer = config.get_printer()
        self.config = config
        self.gcode = self.printer.lookup_object('gcode')
        self.virtual_sd = self.printer.lookup_object('virtual_sdcard', None)
        self.toolhead = None
        self.reactor = self.printer.get_reactor()

        self.purgePin = None
    
        if config.has_section("purge_setting"):
            purgeConfig = config.getsection('purge_setting')
            
            self.purgePin = purgeConfig.get('sensor_pin')
            self.scale = purgeConfig.getfloat("scale", 100.0)
            self.threshold = purgeConfig.getfloat("threshold", THRESHOLD)
            self._limit_helpers = []
            for i in range(1, 1000):
                if config.get("limit%d" % (i, ), None) is None:
                    break
                self._limit_helpers.append(LimitHelper(config, i))

            ppins = self.printer.lookup_object("pins")
            self.mcu_adc = ppins.setup_pin("adc", self.purgePin)
            self.mcu_adc.setup_adc_callback(REPORT_TIME, self.adc_callback)
            self.mcu_adc.setup_adc_sample(SAMPLE_TIME, SAMPLE_COUNT)
            query_adc = self.printer.load_object(config, "query_adc")
            query_adc.register_adc("purgeSensor", self.mcu_adc)

            self.retries = purgeConfig.getint('retries')
            logging.info(f"self.retries: {self.retries}")

            self.finish_purge_sequence = True

        # PURGE_SEQUENCE 내부에 사용
        self.gcode.register_command(
            "PURGE_SENSING", self.cmd_PURGE_SENSING,
            desc=self.cmd_PURGE_SENSING_help)
        # 푸드잉크 교체에 사용
        self.gcode.register_command(
            "PURGE_LOADING", self.cmd_PURGE_LOADING,
            desc=self.cmd_PURGE_LOADING_help)
        
    cmd_PURGE_LOADING_help = "Analog sensor for purge"
    def cmd_PURGE_LOADING(self, gcmd):
        axis = gcmd.get('AXIS', 'e').lower()
        distance = float(gcmd.get('DIST', 2200))    # max 2250, before extrude move to 555 -> 2250 - 555 = 1695
        speed = float(gcmd.get('SPEED', 15))

        self.purgeLoadingDone = False

        self.toolhead = self.printer.lookup_object('toolhead')
        self.toolhead.wait_moves()
        
        self.baseline_value = self.last_value
        logging.info(f"self.baseline_value: {self.baseline_value}")
        start_pos = list(self.toolhead.get_position())
        axis_idx = 'xyze'.index(axis)
        target_pos = start_pos[:]
        target_pos[axis_idx] = distance

        # Homing 상태 활성화
        self.homing_completion = self.reactor.completion()
        self.is_homing = True

        self.stepper = self.printer.lookup_object('extruder').extruder_stepper.stepper
        start_mcu_steps = self.stepper.get_mcu_position()
        start_pos_e = self.toolhead.get_position()[3]
        self.start_detecting = True
        
        try:
            self.gcode.respond_info(f"Starting Analog Homing on {axis}...")
            self.toolhead.drip_move(target_pos, speed, self.homing_completion)
        finally:
            self.is_homing = False
            self.homing_completion = None
            self.start_detecting = False
            
            self.toolhead.wait_moves()

            end_mcu_steps = self.stepper.get_mcu_position()

            step_dist = self.stepper.get_step_dist() # 1스텝당 이동 거리
            actual_moved = (end_mcu_steps - start_mcu_steps) * step_dist
            real_stop_pos_e = start_pos_e + actual_moved

            stop_pos = list(self.toolhead.get_position())
            stop_pos[3] = real_stop_pos_e

            self.toolhead.set_position(stop_pos)
            self.stepper.set_position([stop_pos[3], 0, 0])
            logging.info(f"MCU Position after homing: {real_stop_pos_e}")
            self.purgeLoadingDone = True
            
            axis_idx = 'xyze'.index(axis)
            self.gcode.respond_info(f"Actual stop position: {stop_pos[axis_idx]:.3f}")
            self.gcode.respond_info(f"Actual stop position: {stop_pos}")
            
    def adc_callback(self, read_time, read_value):
        value = read_value * self.scale
        for helper in self._limit_helpers:
            helper.callback(read_time, value, self.last_value)
        self.last_value = value
        if self.last_value > self.max_value:
            self.max_value = self.last_value

        if self.start_detecting:
            if value >= (self.baseline_value + self.threshold):
                self.isDetect = True 
                logging.info(f"value: {round(self.last_value, 2)}")

        if self.is_homing and self.homing_completion:
            if value >= (self.baseline_value + self.threshold):
                self.is_homing = False
                self.homing_completion.complete(True)

    def initState(self):
        self.isDetect = False
        self.purgeLoadingDone = False
        self.start_detecting = True
        self.baseline_value = self.last_value
        logging.info(f"self.baseline_value: {self.baseline_value}")
        logging.info("Purge sensing state initialized.")

    def getParams(self):
        return self.wait_time, self.retries
    
    def get_status(self, eventtime=None):
        return {
            'is_detect': bool(self.isDetect),
            'value': round(self.last_value, 2),
            'max_value': round(self.max_value, 2),
            'loading_done': bool(self.purgeLoadingDone),
        }
    
    cmd_PURGE_SENSING_help = "Execute a purge sensing"
    def cmd_PURGE_SENSING(self, gcmd):
        if self.config.has_section("purge_setting"):
            self.virtual_sd = self.printer.lookup_object('virtual_sdcard', None)
            self.finish_purge_sequence = False
            if self.virtual_sd is not None:
                self.virtual_sd.finish_purge_sequence = False
                logging.info("Set virtual_sd.finish_purge_sequence to False")

            self.toolhead = self.printer.lookup_object('toolhead')
            self.toolhead.wait_moves()
            if self.purge_retries == 0:
                self.initState()
            self._purge_gcmd = gcmd
            self._purge_retry_count = 0
            logging.info("start purge sensing")
            self._purge_check_timer = self.reactor.register_timer(self._purge_check_handler, self.reactor.NOW)
        else:
            logging.info("Purge sensing not configured.")

    def _purge_check_handler(self, eventtime):
        if self.isDetect:
            self._purge_gcmd.respond_info("Purge sensing detected. Proceeding.")
            self.reactor.unregister_timer(self._purge_check_timer)
            self.purge_retries = 0
            self.start_detecting = False
            if self.virtual_sd is not None:
                self.virtual_sd.finish_purge_sequence = True
            return self.reactor.NEVER

        self._purge_retry_count += 1
        if self._purge_retry_count > self.wait_time * 10:
            self.purge_retries += 1
            if self.purge_retries >= self.retries:
                self.reactor.unregister_timer(self._purge_check_timer)
                self.purge_retries = 0
                self.finish_purge_sequence = True
                self.start_detecting = False
                if self.virtual_sd is not None:
                    self.virtual_sd.must_pause_work = True
                    self.virtual_sd.do_cancel()
                    self.virtual_sd.finish_purge_sequence = True
                self._purge_gcmd.respond_error("Maximum retries reached. Purge sensing failed.")
                return self.reactor.NEVER
            self._purge_retry_count = 0
            self.reactor.unregister_timer(self._purge_check_timer)
            self.gcode.run_script("PURGE_SEQUENCE")
        return eventtime + 0.1
    
def load_config(config):
    return PurgeSensing(config)