# Virtual SDCard print stat tracking
#
# Copyright (C) 2020  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import logging
import numpy as np

class PrintStats:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode_move = self.printer.load_object(config, 'gcode_move')
        self.reactor = self.printer.get_reactor()
        self.reset()
        # Register commands
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode.register_command(
            "SET_PRINT_STATS_INFO", self.cmd_SET_PRINT_STATS_INFO,
            desc=self.cmd_SET_PRINT_STATS_INFO_help)
        self.printer.register_event_handler("extruder:activate_extruder",
                                       self._handle_activate_extruder)
        self.calc_print_time = self.printer.load_object(config, 'print_time_calc')
        self.total_time = 0.0

        self.need_bed_center_calibration = 0
        self.need_purge_on_print_start = 0
        self.need_bed_mesh_on_print_start = 0
        self.gcode.register_command(
            "SET_BED_CENTER_CALIBRATION", self.cmd_SET_BED_CENTER_CALIBRATION,
            desc=self.cmd_SET_BED_CENTER_CALIBRATION_help)
        self.gcode.register_command(
            "SET_PURGE_ON_PRINT_START", self.cmd_SET_PURGE_ON_PRINT_START,
            desc=self.cmd_SET_PURGE_ON_PRINT_START_help)
        self.gcode.register_command(
            "SET_BED_MESH_ON_PRINT_START", self.cmd_SET_BED_MESH_ON_PRINT_START,
            desc=self.cmd_SET_BED_MESH_ON_PRINT_START_help)      
        self.gcode.register_command(
            'SET_MESH_POINT', self.cmd_SET_MESH_POINT,
            desc=self.cmd_SET_MESH_POINT_help)
        
        self.bed_center_calibration = self.printer.load_object(config, 'bed_center_calibration')
        self.temp_humi_sensing = self.printer.load_object(config, 'temp_humi_sensing')
        
        self.need_tool_head = True
        self.cap = [50, 37.5, 25, 12.5, 0]
        self.cap_cuts = [830, 1155, 1485, 1805, 2200]

        self.custom_points = -1

        self.available_bed_mesh = config.has_section("bed_mesh")
        

    def _handle_activate_extruder(self):
        gc_status = self.gcode_move.get_status()
        self.last_epos = gc_status['position'].e
    def _update_filament_usage(self, eventtime):
        gc_status = self.gcode_move.get_status(eventtime)
        cur_epos = gc_status['position'].e
        self.filament_used += (cur_epos - self.last_epos) \
            / gc_status['extrude_factor']
        self.last_epos = cur_epos
    def set_current_file(self, filename):
        self.reset()
        self.filename = filename
    def note_start(self):
        curtime = self.reactor.monotonic()
        if self.print_start_time is None:
            self.print_start_time = curtime
        elif self.last_pause_time is not None:
            # Update pause time duration
            pause_duration = curtime - self.last_pause_time
            self.prev_pause_duration += pause_duration
            self.last_pause_time = None
        # Reset last e-position
        gc_status = self.gcode_move.get_status(curtime)
        self.last_epos = gc_status['position'].e
        self.state = "printing"
        self.get_calc_print_time()
        self.error_message = ""
    def note_pause(self):
        if self.last_pause_time is None:
            curtime = self.reactor.monotonic()
            self.last_pause_time = curtime
            # update filament usage
            self._update_filament_usage(curtime)
        if self.state != "error":
            self.state = "paused"
    def note_complete(self):
        self._note_finish("complete")
    def note_error(self, message):
        self._note_finish("error", message)
    def note_cancel(self):
        self._note_finish("cancelled")
    def note_set_temperature(self):
        self.state = "heating"
    def note_printing(self):
        if self.state == "heating":
            self.state = "printing"
    def _note_finish(self, state, error_message = ""):
        if self.print_start_time is None:
            return
        self.state = state
        self.error_message = error_message
        eventtime = self.reactor.monotonic()
        self.total_duration = eventtime - self.print_start_time
        if self.filament_used < 0.0000001:
            # No positive extusion detected during print
            self.init_duration = self.total_duration - \
                self.prev_pause_duration
        self.print_start_time = None
    cmd_SET_PRINT_STATS_INFO_help = "Pass slicer info like layer act and " \
                                    "total to klipper"
    def cmd_SET_PRINT_STATS_INFO(self, gcmd):
        total_layer = gcmd.get_int("TOTAL_LAYER", self.info_total_layer, \
                                   minval=0)
        current_layer = gcmd.get_int("CURRENT_LAYER", self.info_current_layer, \
                                     minval=0)
        if total_layer == 0:
            self.info_total_layer = None
            self.info_current_layer = None
        elif total_layer != self.info_total_layer:
            self.info_total_layer = total_layer
            self.info_current_layer = 0

        if self.info_total_layer is not None and \
                current_layer is not None and \
                current_layer != self.info_current_layer:
            self.info_current_layer = min(current_layer, self.info_total_layer)

    cmd_SET_BED_CENTER_CALIBRATION_help = "Enable or disable bed center " \
                                         "calibration requirement for print start"
    cmd_SET_PURGE_ON_PRINT_START_help = "Enable or disable purge on " \
                                            "print start"
    cmd_SET_BED_MESH_ON_PRINT_START_help = "Enable or disable bed mesh " \
                                            "requirement for print start"
    cmd_SET_MESH_POINT_help = "Set Bed Mesh Point"
    def cmd_SET_BED_CENTER_CALIBRATION(self, gcmd):
        self.need_bed_center_calibration = gcmd.get_int("ENABLE", self.need_bed_center_calibration, minval = 0)
        logging.info("Set bed center calibration to %d", self.need_bed_center_calibration)
    def cmd_SET_PURGE_ON_PRINT_START(self, gcmd):
        self.need_purge_on_print_start = gcmd.get_int("ENABLE", self.need_purge_on_print_start, minval = 0)
        logging.info("Set purge on print start to %d", self.need_purge_on_print_start)
    def cmd_SET_BED_MESH_ON_PRINT_START(self, gcmd):
        self.need_bed_mesh_on_print_start = gcmd.get_int("ENABLE", self.need_bed_mesh_on_print_start, minval = 0)
        logging.info("Set bed mesh on print start to %d", self.need_bed_mesh_on_print_start)
    def cmd_SET_MESH_POINT(self, gcmd):
        custom_points = gcmd.get_int('VALUE', self.custom_points, minval = 0)
        self.custom_points = custom_points
        logging.info(f"Get bed mesh custom point: {self.custom_points}")
        

    def get_bed_center_calibration(self):
        return self.need_bed_center_calibration
    def get_purge_on_print_start(self):
        return self.need_purge_on_print_start
    def get_bed_mesh_on_print_start(self):
        return self.need_bed_mesh_on_print_start
    def get_bed_mesh_custom_points(self):
        return self.custom_points
    
    def reset_filament_remaining(self):
        self.filament_remaining = 0.
        self.last_remaining = 0

    def reset(self):
        self.filename = self.error_message = ""
        self.state = "standby"
        self.prev_pause_duration = self.last_epos = 0.
        self.filament_used = self.total_duration = 0.
        self.filament_remaining = 0.
        self.current_temperature = 0.
        self.current_humidity = 0.
        self.last_remaining = 0
        self.print_start_time = self.last_pause_time = None
        self.init_duration = 0.
        self.info_total_layer = None
        self.info_current_layer = None
    def get_status(self, eventtime):
        if self.need_tool_head:
            self.toolhead = self.printer.lookup_object('toolhead')
            self.need_tool_head = False
        
        time_paused = self.prev_pause_duration
        if self.print_start_time is not None:
            if self.last_pause_time is not None:
                # Calculate the total time spent paused during the print
                time_paused += eventtime - self.last_pause_time
            else:
                # Accumulate filament if not paused
                self._update_filament_usage(eventtime)
            self.total_duration = eventtime - self.print_start_time
            if self.filament_used < 0.0000001:
                # Track duration prior to extrusion
                self.init_duration = self.total_duration - time_paused
        cur_extruder_pos = self.toolhead.get_position()[-1]
        if cur_extruder_pos > self.last_remaining and cur_extruder_pos >= self.cap_cuts[0]:   # E 830 = 50ml
            self.filament_remaining = "{:.2f}".format(np.interp(self.toolhead.get_position()[-1], self.cap_cuts, self.cap))
            self.last_remaining = cur_extruder_pos
        print_duration = self.total_duration - self.init_duration - time_paused
        return {
            'filename': self.filename,
            'total_duration': self.total_duration,
            'print_duration': print_duration,
            'total_time': self.total_time,            
            'filament_used': self.filament_used,
            'filament_remaining': self.filament_remaining,
            'temp': self.temp_humi_sensing.get_status()['temp'],
            'humi': self.temp_humi_sensing.get_status()['humi'],
            'state': self.state,
            'message': self.error_message,
            'bed_center_calibration_active': self.need_bed_center_calibration,
            'info': {'total_layer': self.info_total_layer,
                     'current_layer': self.info_current_layer},
            'available_camera': self.bed_center_calibration.get_is_available_camera(),
            'available_bed_mesh': self.available_bed_mesh,
        }
    def get_calc_print_time(self):
        self.total_time = self.calc_print_time.calc_time(build_layers = True)

def load_config(config):
    return PrintStats(config)
