# Add text below to printer.cfg to enable purge sensing
#
# [purge_sensing]
# sensor_pin: PA4
# retries: 3

SAMPLE_COUNT = 8  # Take 8 subsamples within the MCU
SAMPLE_TIME = 0.001  # with a 0.001s gap between each
REPORT_TIME = 0.010  # and report their average every 1s
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

        self.prevSignal = 0
        self.isDetect = False

        self.printer = config.get_printer()
    
        if config.has_section("purge_sensing"):
            purgeConfig = config.getsection('purge_sensing')
            
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

        if value > self.threshold:
            self.isDetect = True

    # def _handle_endstop_trigger(self, eventtime, signal):
    #     # logging.info(f"Purge sensing endstop triggered {eventtime}, {signal}")

    #     if signal == 1 and self.prevSignal == 0:
    #         self.isDetect = True
    #         # logging.info("Purge filament detected!")

    #     self.prevSignal = signal

    def initState(self):
        self.isDetect = False

    def getParams(self):
        return self.wait_time, self.retries
    
    def get_status(self, eventtime):
        return {
            "is_detect": self.isDetect,
            "value": round(self.last_value, 2),
            "max_value": round(self.max_value, 2)
        }   


def load_config(config):
    return PurgeSensing(config)