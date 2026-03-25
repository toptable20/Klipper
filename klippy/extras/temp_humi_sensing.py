# Add text below to printer.cfg to enable purge sensing
#
# [temp_humi_setting]
# temp_pin: 
# humi_pin: 

SAMPLE_COUNT = 3  # Take 8 subsamples within the MCU
SAMPLE_TIME = 0.2  # with a 0.001s gap between each
REPORT_TIME = 1.0  # and report their average every 1s
MOVING_AVG_SIZE = 10  # Window size for moving average filter

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
from collections import deque

class TempHumiSensing:
    def __init__(self, config):        
        self.printer = config.get_printer()
        self.config = config
        self.gcode = self.printer.lookup_object('gcode')
        self.virtual_sd = self.printer.lookup_object('virtual_sdcard', None)
        self.reactor = self.printer.get_reactor()

        self.temp = 0
        self.humi = 0
        
        self.temp_buffer = deque(maxlen=MOVING_AVG_SIZE)
        self.humi_buffer = deque(maxlen=MOVING_AVG_SIZE)

        if config.has_section("temp_humi_setting"):
            purgeConfig = config.getsection('temp_humi_setting')
            
            self.tempPin = purgeConfig.get('temp_pin')
            self.humiPin = purgeConfig.get('humi_pin')

            self._limit_helpers = []
            for i in range(1, 1000):
                if config.get("limit%d" % (i, ), None) is None:
                    break
                self._limit_helpers.append(LimitHelper(config, i))

            ppins = self.printer.lookup_object("pins")
            self.mcu_adc_temp = ppins.setup_pin("adc", self.tempPin)
            self.mcu_adc_temp.setup_adc_callback(REPORT_TIME, self.adc_temp_callback)
            self.mcu_adc_temp.setup_adc_sample(SAMPLE_TIME, SAMPLE_COUNT)
            query_adc = self.printer.load_object(config, "query_adc")
            query_adc.register_adc("tempSensor", self.mcu_adc_temp)

            self.mcu_adc_humi = ppins.setup_pin("adc", self.humiPin)
            self.mcu_adc_humi.setup_adc_callback(REPORT_TIME, self.adc_humi_callback)
            self.mcu_adc_humi.setup_adc_sample(SAMPLE_TIME, SAMPLE_COUNT)
            query_adc = self.printer.load_object(config, "query_adc")
            query_adc.register_adc("humiSensor", self.mcu_adc_humi)

            self.finish_purge_sequence = True
        
    def adc_temp_callback(self, read_time, read_value):
        raw_temp = -66.875 + 218.75*(read_value*3.3/5.0)
        self.temp_buffer.append(raw_temp)
        self.temp = sum(self.temp_buffer) / len(self.temp_buffer)

    def adc_humi_callback(self, read_time, read_value):
        raw_humi = -12.5+125*(read_value*3.3/5.0)
        self.humi_buffer.append(raw_humi)
        self.humi = sum(self.humi_buffer) / len(self.humi_buffer)
            
    def get_status(self, eventtime=None):
        return {
            'temp': round(self.temp, 1),
            'humi': round(self.humi)
        }
    
def load_config(config):
    return TempHumiSensing(config)
