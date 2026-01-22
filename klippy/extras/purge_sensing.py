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
    
    # def cmd_ANALOG_HOMING(self, gcmd):
    #     # config에서 이동축, 거리, 속도, 임계값 등 파라미터를 읽거나, gcode 인자로 받음
    #     axis = gcmd.get('AXIS', 'e').lower()  # 예: X축
    #     distance = float(gcmd.get('DIST', 2250))  # mm
    #     speed = float(gcmd.get('SPEED', 50))     # mm/s
    #     threshold = float(gcmd.get('THRESHOLD', self.threshold))
    #     rising = gcmd.get('RISING', '1') == '1'  # 상승엣지(기본)

    #     self.toolhead = self.printer.lookup_object('toolhead')
    #     start_pos = list(self.toolhead.get_position())
    #     axis_idx = 'xyze'.index(axis)
    #     target_pos = start_pos[:]
    #     target_pos[axis_idx] += distance

    #     # reactor.Completion 객체 생성 (임계값 도달 시 complete)
    #     completion = self.reactor.completion()
    #     def adc_trigger_callback(read_time, value):
    #         if (rising and value > threshold) or (not rising and value < threshold):
    #             completion.complete()
    #     self.mcu_adc.setup_adc_callback(REPORT_TIME, adc_trigger_callback)
    #     self.mcu_adc.setup_adc_sample(SAMPLE_TIME, SAMPLE_COUNT)

    #     self.start_detecting = True
    #     self.isDetect = False
    #     self.gcode.respond_info(f"Analog homing: {axis.upper()}축 {distance}mm, speed={speed}, threshold={threshold}")

    #     # drip_move로 연속 이동, completion이 complete()되면 즉시 정지
    #     self.toolhead.drip_move(target_pos, speed, completion)
    #     self.start_detecting = False
    #     self.gcode.respond_info(f"Analog homing stopped at value={self.mcu_adc.get_last_value()[0]:.2f}")

    #     # 콜백 원복
    #     self.mcu_adc.setup_adc_callback(REPORT_TIME, self.adc_callback)

    def __init__(self, config):
        
        self.last_value = 0.0
        self.max_value = 0.0

        self.endstop = None
        self.wait_time = 5 # seconds
        self.retries = 0
        self.purge_retries = 0

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

            self.finish_purge_sequence = True

        self.gcode.register_command(
            "PURGE_SENSING", self.cmd_PURGE_SENSING,
            desc=self.cmd_PURGE_SENSING_help)
        self.gcode.register_command(
            "PURGE_LOADING", self.cmd_PURGE_LOADING,
            desc=self.cmd_PURGE_LOADING_help)
        self.gcode.register_command(
            "PURGE_HOMING", self.cmd_PURGE_HOMING,
            desc=self.cmd_PURGE_HOMING_help)

        # self.gcode.register_command(
        #     "ANALOG_HOMING", self.cmd_ANALOG_HOMING,
        #     desc=self.cmd_ANALOG_HOMING_help)

        # if config.has_section("purge_sensing"):
        #     purgeConfig = config.getsection('purge_sensing')
        #     if purgeConfig.get('endstop', None) is not None:
        #         # self.endstop = purgeConfig.get('endstop')
        #         self.endstop = self.printer.load_object(config, 'buttons')
        #         self.endstop.register_buttons([purgeConfig.get('endstop')], self._handle_endstop_trigger)
        #     else:
        #         raise config.error("purge sensing must have endstop")

        #     if purgeConfig.get('retries', None) is not None:
        #         self.retries = purgeConfig.getint('retries')
    cmd_PURGE_LOADING_help = "Analog sensor for purge"
    def cmd_PURGE_LOADING(self, gcmd):
        axis = gcmd.get('AXIS', 'e').lower()
        distance = float(gcmd.get('DIST', 2200))    # max 2250, before extrude move to 555 -> 2250 - 555 = 1695
        speed = float(gcmd.get('SPEED', 15))
        self.current_threshold = float(gcmd.get('THRESHOLD', self.threshold))

        self.purgeLoadingDone = False

        self.toolhead = self.printer.lookup_object('toolhead')
        self.toolhead.wait_moves()
        
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
            
            # 확인용 로그
            axis_idx = 'xyze'.index(axis)
            self.gcode.respond_info(f"Actual stop position: {stop_pos[axis_idx]:.3f}")
            self.gcode.respond_info(f"Actual stop position: {stop_pos}")
            
            
    cmd_PURGE_HOMING_help = "Execute a purge homing"
    def cmd_PURGE_HOMING(self, gcmd):
        # NOTE: Get the steppers
        # self.extruder_stepper: ExtruderStepper = self.extruder.extruder_stepper      # ExtruderStepper
        # self.rail: stepper.PrinterRail = self.extruder_stepper.rail # PrinterRail
        # self.stepper: stepper.MCU_stepper = self.extruder_stepper.stepper   # MCU_stepper
        # self.steppers = [self.stepper]                              # [MCU_stepper]

        self.toolhead = self.printer.lookup_object('toolhead')

        # endstops = self.rail.get_endstops()                 # [(mcu_endstop, name)]
        endstops = [(self.purgePin, 'purge_sensing_endstop')]

        phoming: PrinterHoming = self.printer.lookup_object('homing')      # PrinterHoming

        self.th_orig_pos = self.toolhead.get_position()
        logging.info(f"cmd_HOME_EXTRUDER: th_orig_pos={str(self.th_orig_pos)}")
        speed = 200
        movepos = 2250
        pos = self.th_orig_pos[:-1] + [movepos]
        position_min, position_max = (0, 2250)
        e_startpos = position_max + pos[-1] * 0.1
        startpos = self.th_orig_pos[:-1] + [e_startpos]
        self.toolhead.set_position(newpos=startpos, homing_axes="e")

        # NOTE: flag homing start
        self.homing = True

        # NOTE: "manual_home" is defined in the PrinterHoming class (at homing.py).
        #       The method instantiates a "HomingMove" class by passing it the
        #       "endstops" and "toolhead" objects.
        #       The requried "endstop"s are from the extruder's PrinterRail object.
        #       In the "manual_stepper" object, the very "self" object is passed
        #       as a "virtual toolhead" to "manual_home". Here, in contrast, the full
        #       toolhead object is passed because it has been modified to support homing
        #       the extruder axis too.
        # NOTE: "PrinterHoming.manual_home" then calls "HomingMove.homing_move".
        # logging.info(f"cmd_HOME_EXTRUDER: pos={str(pos)}")
        phoming.manual_home(toolhead=self.toolhead, endstops=endstops,
                            pos=pos, speed=speed,
                            # NOTE: argument passed to "mcu_endstop.home_start",
                            #       and used directly in the low-level command.
                            triggered=True,
                            # NOTE: if True, an "error" is recorded when the move
                            #       completes without the endstop triggering.
                            check_triggered=True)


    def adc_callback(self, read_time, read_value):
        value = read_value * self.scale
        for helper in self._limit_helpers:
            helper.callback(read_time, value, self.last_value)
        self.last_value = value
        if self.last_value > self.max_value:
            self.max_value = self.last_value

        if value > self.threshold and self.start_detecting:
            self.isDetect = True 

        if self.is_homing and self.homing_completion:
            # 여기서 직접 로그를 찍어서 값이 들어오는지 확인하세요
            # logging.info(f"Homing ADC Value: {value}") 
            
            if value > self.current_threshold:
                self.is_homing = False # 중복 실행 방지
                self.homing_completion.complete(True)

    # def _handle_endstop_trigger(self, eventtime, signal):
    #     # logging.info(f"Purge sensing endstop triggered {eventtime}, {signal}")

    #     if signal == 1 and self.prevSignal == 0:
    #         self.isDetect = True
    #         # logging.info("Purge filament detected!")

    #     self.prevSignal = signal

    def initState(self):
        self.isDetect = False
        self.purgeLoadingDone = False
        self.start_detecting = True
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
            self.initState()
            self._purge_gcmd = gcmd
            self._purge_retry_count = 0
            self._purge_check_timer = self.reactor.register_timer(self._purge_check_handler, self.reactor.NOW)
        else:
            logging.info("Purge sensing not configured.")

    def _purge_check_handler(self, eventtime):
        if self.isDetect:
            # self._purge_gcmd.respond_info("Purge sensing detected. Proceeding.")
            self.reactor.unregister_timer(self._purge_check_timer)
            self.purge_retries = 0
            if self.virtual_sd is not None:
                self.virtual_sd.finish_purge_sequence = True
            # logging.info("Purge sensing detected. unregistering timer.")
            self.start_detecting = False
            return self.reactor.NEVER
        self._purge_retry_count += 1
        if self._purge_retry_count > self.wait_time*10:  # check for 3 seconds (0.1s * 50)
            self.purge_retries += 1
            if self.purge_retries >= self.retries:
                self.reactor.unregister_timer(self._purge_check_timer)
                self.purge_retries = 0
                self.finish_purge_sequence = True
                if self.virtual_sd is not None:
                    self.virtual_sd.must_pause_work = True
                    self.virtual_sd.do_cancel()
                    self.virtual_sd.finish_purge_sequence = True
                self._purge_gcmd.respond_error("Purge sensing not detected after maximum retries.")
                self.start_detecting = False
                return self.reactor.NEVER
            # self._purge_gcmd.respond_info("Purge sensing not detected! Please check and retry.")
            # logging.info("Purge sensing not detected after retries. unregistering timer.")
            self._purge_retry_count = 0
            self.reactor.unregister_timer(self._purge_check_timer)
            self.gcode.run_script("PURGE_SEQUENCE")
        return eventtime + 0.1
    

    # cmd_PURGE_LOADING_help = "Execute a purge loading"
    # def cmd_PURGE_LOADING(self, gcmd):
    #     if self.config.has_section("purge_setting"):
    #         self.isDetect = False
    #         self.purgeLoadingDone = False
    #         self.toolhead = self.printer.lookup_object('toolhead')
    #         self._purge_gcmd = gcmd
    #         self._purge_retry_count = 0
    #         self.reactor.register_timer(self._wait_for_idle_handler, self.reactor.NOW + 0.1)
    #         # gcmd.respond_info("프린터 동작 완료 대기 중...")
    #         # self._purge_check_timer = self.reactor.register_timer(self._purge_loading_check_handler, self.reactor.NOW)
    #     else:
    #         self._purge_gcmd.respond_info("Purge sensing not configured.")
    #         logging.info("Purge sensing not configured.")

    # def _wait_for_idle_handler(self, eventtime):
    #     logging.info("프린터 동작 중... 완료 대기 중.")
    #     self.toolhead.wait_moves()
    #     self.start_detecting = True

    #     # --- 여기서부터는 프린터가 멈춘 후 실행될 로직 ---
    #     logging.info("프린터 동작 완료 감지. 다음 명령 수행.")
    #     self._purge_check_timer = self.reactor.register_timer(self._purge_loading_check_handler, self.reactor.NOW)
    #     # 더 이상 반복하지 않으려면 NEVER 반환
    #     return self.reactor.NEVER
        

    # def _purge_loading_check_handler(self, eventtime):
    #     if self.isDetect:
    #         # self._purge_gcmd.respond_info("Purge sensing detected. Proceeding.")
    #         logging.info("Purge sensing detected during loading.")
    #         self.gcode.run_script("M82")
    #         self.purgeLoadingDone = True
    #         self.reactor.unregister_timer(self._purge_check_timer)
    #         self.purge_retries = 0
    #         self.start_detecting = False
    #         return self.reactor.NEVER
    #     else:
    #         logging.info("Purge sensing not detected during loading. Extruding more filament.")
    #         self.gcode.run_script("M83")
    #         self.gcode.run_script("G1 E27 F930")
    #         self.toolhead.wait_moves()
    #     return eventtime + 0.1


def load_config(config):
    return PurgeSensing(config)