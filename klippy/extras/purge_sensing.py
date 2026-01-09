
import logging

class PurgeSensing:
    def __init__(self, config):
        self.endstop = None
        self.wait_time = 5 # seconds
        self.retries = 0

        self.prevSignal = 0
        self.isDetect = False

        self.printer = config.get_printer()

        if config.has_section("purge_sensing"):
            purgeConfig = config.getsection('purge_sensing')
            if purgeConfig.get('endstop', None) is not None:
                # self.endstop = purgeConfig.get('endstop')
                self.endstop = self.printer.load_object(config, 'buttons')  
                self.endstop.register_buttons([purgeConfig.get('endstop')], self._handle_endstop_trigger)
            else:
                raise config.error("purge sensing must have endstop")

            if purgeConfig.get('retries', None) is not None:
                self.retries = purgeConfig.getint('retries')
        
    def _handle_endstop_trigger(self, eventtime, signal):
        # logging.info(f"Purge sensing endstop triggered {eventtime}, {signal}")

        if signal == 1 and self.prevSignal == 0:
            self.isDetect = True
            # logging.info("Purge filament detected!")

        self.prevSignal = signal

    def initState(self):
        self.isDetect = False

    def getParams(self):
        return self.wait_time, self.retries


def load_config(config):
    return PurgeSensing(config)