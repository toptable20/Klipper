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

        self.target_height = 0
        self.target_radius = 0
        self.target_number = 0
        self.print_sequence = 0
        self.detect_type = 0

        self.gcode.register_command(
            "SET_BED_CENTER_CALIBRATION", self.cmd_SET_BED_CENTER_CALIBRATION,
            desc=self.cmd_SET_BED_CENTER_CALIBRATION_help)
        self.gcode.register_command(
            "SET_DETECT_TYPE", self.cmd_SET_DETECT_TYPE,
            desc=self.cmd_SET_DETECT_TYPE_help)
        self.gcode.register_command(
            "SET_TARGET_HEIGHT", self.cmd_SET_TARGET_HEIGHT,
            desc=self.cmd_SET_TARGET_HEIGHT_help)
        self.gcode.register_command(
            "SET_TARGET_RADIUS", self.cmd_SET_TARGET_RADIUS,
            desc=self.cmd_SET_TARGET_RADIUS_help)
        
        self.gcode.register_command(
            "SET_TARGET_NUMBER", self.cmd_SET_TARGET_NUMBER,
            desc=self.cmd_SET_TARGET_NUMBER_help)
        self.gcode.register_command(
            "SET_PRINT_SEQUENCE", self.cmd_SET_PRINT_SEQUENCE,
            desc=self.cmd_SET_PRINT_SEQUENCE_help)
        
        self.gcode.register_command(
            "SET_PURGE_ON_PRINT_START", self.cmd_SET_PURGE_ON_PRINT_START,
            desc=self.cmd_SET_PURGE_ON_PRINT_START_help)
        self.gcode.register_command(
            "SET_BED_MESH_ON_PRINT_START", self.cmd_SET_BED_MESH_ON_PRINT_START,
            desc=self.cmd_SET_BED_MESH_ON_PRINT_START_help)      
        self.gcode.register_command(
            'SET_MESH_POINT', self.cmd_SET_MESH_POINT,
            desc=self.cmd_SET_MESH_POINT_help)
        
        self.gcode.register_command(
            "CAMCALIB_WAIT_DONE", self.cmd_CAMCALIB_WAIT_DONE,
            desc=self.cmd_CAMCALIB_WAIT_DONE_help)
        
        self.bed_center_calibration = self.printer.load_object(config, 'bed_center_calibration')
        self.temp_humi_sensing = self.printer.load_object(config, 'temp_humi_sensing')
        
        self.need_tool_head = True
        self.cap = [50, 37.5, 25, 12.5, 0]
        self.cap_cuts = [830, 1155, 1485, 1805, 2200]

        self.custom_points = -1
        self.calib_wait_flag = False

        self.available_bed_mesh = config.has_section("bed_mesh")
        self.available_input_shaper = config.has_section("input_shaper")
        self.available_z_calibration = config.has_section("probe")
        self.available_purge_sensing = config.has_section("purge_setting")
        

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
    def note_camcalib_start(self):
        self.state = "camcalib"
    def note_camcalib_complete(self):
        self.state = "standby"
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
    cmd_SET_DETECT_TYPE_help = "Set bed center calibration detect type (circle or unstructured)"
    cmd_SET_TARGET_HEIGHT_help = "Set target height for bed center calibration"
    cmd_SET_TARGET_RADIUS_help = "Set target radius for bed center calibration"

    cmd_SET_PURGE_ON_PRINT_START_help = "Enable or disable purge on " \
                                            "print start"
    cmd_SET_BED_MESH_ON_PRINT_START_help = "Enable or disable bed mesh " \
                                            "requirement for print start"
    cmd_SET_MESH_POINT_help = "Set Bed Mesh Point"
    cmd_SET_TARGET_NUMBER_help = "Set target number for bed center calibration"
    cmd_SET_PRINT_SEQUENCE_help = "Set print sequence for bed center calibration"

    cmd_CAMCALIB_WAIT_DONE_help = "Wait for bed center calibration to complete before starting print"

    def cmd_SET_BED_CENTER_CALIBRATION(self, gcmd):
        self.need_bed_center_calibration = gcmd.get_int("ENABLE", self.need_bed_center_calibration, minval = 0)
        logging.info("Set bed center calibration to %d", self.need_bed_center_calibration)
    def cmd_SET_DETECT_TYPE(self, gcmd):
        detect_type = gcmd.get_int("VALUE", self.detect_type)
        self.detect_type = detect_type
        logging.info("Set bed center calibration detect type to %s (0: circle, 1: unstructured)", self.detect_type)
        self.bed_center_calibration.set_detect_type(detect_type)
    def cmd_SET_PURGE_ON_PRINT_START(self, gcmd):
        self.need_purge_on_print_start = gcmd.get_int("ENABLE", self.need_purge_on_print_start, minval = 0)
        logging.info("Set purge on print start to %d", self.need_purge_on_print_start)
    def cmd_SET_BED_MESH_ON_PRINT_START(self, gcmd):
        self.need_bed_mesh_on_print_start = gcmd.get_int("ENABLE", self.need_bed_mesh_on_print_start, minval = 0)
        logging.info("Set bed mesh on print start to %d", self.need_bed_mesh_on_print_start)
    def cmd_SET_MESH_POINT(self, gcmd):
        custom_points = gcmd.get_int('VALUE', self.custom_points, minval = 3)
        self.custom_points = custom_points
        logging.info(f"Get bed mesh custom point: {self.custom_points}")
    def cmd_SET_TARGET_HEIGHT(self, gcmd):
        target_height = gcmd.get_int('VALUE', self.target_height, minval = 0)
        self.target_height = target_height
        gcmd.respond_info("target_height: Set target height set to %d mm" % (self.target_height,))
        self.bed_center_calibration.set_height(self.target_height)
        if self.available_bed_mesh:
            bed_mesh = self.printer.lookup_object('bed_mesh')
            bed_mesh.set_target_height(target_height)
            logging.info(f"set horizontal z to {target_height}+10mm")
    def cmd_SET_TARGET_RADIUS(self, gcmd):
        target_radius = gcmd.get_int('VALUE', self.target_radius, minval = 0)
        self.target_radius = target_radius
        gcmd.respond_info("target_radius: Set target radius set to %d mm" % (self.target_radius,))
        self.bed_center_calibration.set_radius(self.target_radius)
    def cmd_SET_TARGET_NUMBER(self, gcmd):
        target_number = gcmd.get_int('VALUE', self.target_number, minval = 1)
        self.target_number = target_number
        gcmd.respond_info("target_number: Set target number set to %d" % (self.target_number,))
        self.bed_center_calibration.set_target_number(target_number)
    def cmd_SET_PRINT_SEQUENCE(self, gcmd):
        print_sequence = gcmd.get_int('ENABLE', self.print_sequence, minval = 0)
        self.print_sequence = print_sequence
        gcmd.respond_info("print_sequence: Set print sequence set to %d (0: One at a Time, 1: All at Once)" % (self.print_sequence,))
        self.bed_center_calibration.set_print_sequence(print_sequence)
    def cmd_CAMCALIB_WAIT_DONE(self, gcmd):
        sdcard = self.printer.lookup_object('virtual_sdcard', None)
        calib_wait_flag = gcmd.get("VALUE", self.calib_wait_flag)
        sdcard.calib_wait_flag = bool(calib_wait_flag)

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
            'available_input_shaper': self.available_input_shaper,
            'available_z_calibration': self.available_z_calibration,
            'available_purge_sensing': self.available_purge_sensing,
        }
    def get_calc_print_time(self):
        self.total_time = self.calc_print_time.calc_time(build_layers = True)

def load_config(config):
    return PrintStats(config)
