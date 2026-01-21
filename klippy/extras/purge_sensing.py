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

class PurgeSensing:
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

        self.start_detecting = False

        self.printer = config.get_printer()
        self.config = config
        self.gcode = self.printer.lookup_object('gcode')
        self.virtual_sd = self.printer.lookup_object('virtual_sdcard', None)
        self.toolhead = None
        self.reactor = self.printer.get_reactor()
    
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

    def adc_callback(self, read_time, read_value):
        value = read_value * self.scale
        for helper in self._limit_helpers:
            helper.callback(read_time, value, self.last_value)
        self.last_value = value
        if self.last_value > self.max_value:
            self.max_value = self.last_value

        if value > self.threshold and self.start_detecting:
            self.isDetect = True 

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
    

    cmd_PURGE_LOADING_help = "Execute a purge loading"
    def cmd_PURGE_LOADING(self, gcmd):
        if self.config.has_section("purge_setting"):
            self.isDetect = False
            self.purgeLoadingDone = False
            self.toolhead = self.printer.lookup_object('toolhead')
            self._purge_gcmd = gcmd
            self._purge_retry_count = 0
            self.reactor.register_timer(self._wait_for_idle_handler, self.reactor.NOW + 0.1)
            # gcmd.respond_info("프린터 동작 완료 대기 중...")
            # self._purge_check_timer = self.reactor.register_timer(self._purge_loading_check_handler, self.reactor.NOW)
        else:
            self._purge_gcmd.respond_info("Purge sensing not configured.")
            logging.info("Purge sensing not configured.")

    def _wait_for_idle_handler(self, eventtime):
        logging.info("프린터 동작 중... 완료 대기 중.")
        self.toolhead.wait_moves()
        self.start_detecting = True

        # --- 여기서부터는 프린터가 멈춘 후 실행될 로직 ---
        logging.info("프린터 동작 완료 감지. 다음 명령 수행.")
        self._purge_check_timer = self.reactor.register_timer(self._purge_loading_check_handler, self.reactor.NOW)
        # 더 이상 반복하지 않으려면 NEVER 반환
        return self.reactor.NEVER
        

    def _purge_loading_check_handler(self, eventtime):
        if self.isDetect:
            # self._purge_gcmd.respond_info("Purge sensing detected. Proceeding.")
            logging.info("Purge sensing detected during loading.")
            self.gcode.run_script("M82")
            self.purgeLoadingDone = True
            self.reactor.unregister_timer(self._purge_check_timer)
            self.purge_retries = 0
            self.start_detecting = False
            return self.reactor.NEVER
        else:
            logging.info("Purge sensing not detected during loading. Extruding more filament.")
            self.gcode.run_script("M83")
            self.gcode.run_script("G1 E27 F930")
            self.toolhead.wait_moves()
        return eventtime + 0.1


def load_config(config):
    return PurgeSensing(config)